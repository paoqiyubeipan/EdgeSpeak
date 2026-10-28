"""日语查词：分词（MeCab + UniDic）、还原原型、查 JMdict / 维基词典释义和 Kanjium 音高。
英文取词：找出光标处的英文单词，查 ECDICT 英汉词典。"""
import json
import pathlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field

import fugashi

import dict_store
import uk_english

MAX_SPAN = 6  # 最长匹配最多跨几个词
INFLECTABLE = {"動詞", "形容詞", "形状詞"}
SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")


def open_db(path):
    """只读打开，后台更新词典时不会互相锁住。"""
    return sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)


# 语体 / 用法标记：JMdict 的 misc 代码和维基词典的 tags 都转成中文，显示在释义前面
REGISTER_LABELS = {
    "net-sl": "网络用语", "Internet": "网络用语", "sl": "俚语", "slang": "俚语", "neologism": "新词",
    "col": "口语", "colloquial": "口语", "informal": "非正式", "arch": "古语", "archaic": "古语",
    "obs": "旧词", "obsolete": "旧词", "dated": "旧", "hon": "尊敬语", "honorific": "尊敬语",
    "hum": "谦让语", "humble": "谦让语", "pol": "礼貌语", "polite": "礼貌语", "vulg": "粗俗", "vulgar": "粗俗",
    "derog": "贬义", "derogatory": "贬义", "abbr": "缩略", "abbreviation": "缩略", "joc": "玩笑", "humorous": "玩笑",
    "fam": "亲昵", "rare": "罕用", "poet": "诗歌", "male": "男性用语", "fem": "女性用语", "chn": "儿语",
    "form": "书面语", "formal": "书面语", "figuratively": "比喻", "euph": "委婉", "euphemistic": "委婉",
    "on-mim": "拟声拟态", "onomatopoeia": "拟声", "id": "惯用语", "idiomatic": "惯用语", "yoji": "四字熟语",
    "proverb": "谚语", "sens": "敏感", "Kansai": "关西方言", "ksb": "关西方言", "dialectal": "方言",
}
SLANG_TAGS = {"Internet", "slang", "neologism", "net-sl", "sl"}
NET_TAGS = {"Internet", "neologism", "net-sl"}
MAX_ZH = 5


def _gloss_phrases(glosses):
    """释义里的每个短语，去掉开头的 to 和括号注释，用来判断两本词典是不是说的同一个意思。"""
    phrases = set()
    for gloss in glosses:
        for part in re.split(r"[;,]", re.sub(r"\([^)]*\)", "", gloss)):
            part = re.sub(r"^\s*(to|a|an|the)\s+", "", part.strip().lower()).strip()
            if part:
                phrases.add(part)
    return phrases


def extra_slang_senses(senses, wikt_rows):
    """JMdict 有这个词时，从维基词典补上它还没收的网络用语 / 俚语义项（意思重复的不补）。"""
    tags = {t for _, _, misc in senses for t in misc}
    wanted = NET_TAGS if tags & {"sl"} and not tags & {"net-sl"} else SLANG_TAGS
    if tags & {"net-sl"}:
        return []
    known = _gloss_phrases(g for _, glosses, _ in senses for g in glosses)
    extra = []
    for _, pos, rows in wikt_rows:
        for gloss, gloss_tags in rows:
            if wanted & set(gloss_tags) and not _gloss_phrases([gloss]) & known:
                extra.append(([pos], [gloss], gloss_tags))
                known |= _gloss_phrases([gloss])
    return extra


def register_labels(tags):
    labels = []
    for tag in tags or []:
        label = REGISTER_LABELS.get(tag)
        if label and label not in labels:
            labels.append(label)
    return labels


def to_hiragana(text):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def is_kana(c):
    return "぀" <= c <= "ヿ"


def has_kanji(text):
    return any("㐀" <= c <= "鿿" or "豈" <= c <= "﫿" or c == "々" for c in text)


def split_mora(reading):
    """かな → 拍。小写的ゃゅょ等并入前一拍，っ、ー、ん各算一拍。"""
    morae = []
    for c in reading:
        if c in SMALL_KANA and morae:
            morae[-1] += c
        else:
            morae.append(c)
    return morae


def pitch_pattern(n_morae, accent):
    """音调型（核的位置）→ 每拍高低。0=平板，1=头高，n=第 n 拍后下降。"""
    if accent == 0:
        return [i > 0 for i in range(n_morae)]
    if accent == 1:
        return [i == 0 for i in range(n_morae)]
    return [0 < i < accent for i in range(n_morae)]


def ruby_segments(surface, reading):
    """把读音对齐到每段汉字上：取り扱っ / とりあつかっ → [(取, と), (り, None), (扱, あつか), (っ, None)]。"""
    if not reading or not has_kanji(surface):
        return [(surface, None)]
    runs = []  # [(文字, 是否汉字)]
    for c in surface:
        kanji = not is_kana(c)
        if runs and runs[-1][1] == kanji:
            runs[-1] = (runs[-1][0] + c, kanji)
        else:
            runs.append((c, kanji))
    pattern = "".join("(.+?)" if kanji else re.escape(to_hiragana(text)) for text, kanji in runs)
    match = re.fullmatch(pattern, reading)
    if not match:
        return [(surface, reading)]
    groups = iter(match.groups())
    return [(text, next(groups)) if kanji else (text, None) for text, kanji in runs]


# 变形拆解：词尾成分的作用。助动词按词元查（读んだ 的 だ 词元是 た），其余按原型查
PART_ROLES = {
    "ない": "否定", "ぬ": "否定", "ん": "否定", "まい": "否定意志", "た": "过去 / 完成", "だ": "断定",
    "ます": "礼貌", "です": "礼貌", "たい": "愿望", "たがる": "愿望，第三人称",
    "れる": "被动 / 可能 / 尊敬", "られる": "被动 / 可能 / 尊敬", "せる": "使役", "させる": "使役",
    "う": "意志 / 推量", "よう": "意志 / 推量", "らしい": "推测", "そう": "样态", "べし": "应当",
    "て": "连接", "で": "连接", "ば": "假定", "ちゃ": "连接，口语", "じゃ": "连接，口语",
    "いる": "持续 / 状态，ている", "ある": "状态，てある", "しまう": "完成 / 遗憾，てしまう",
    "おく": "预先，ておく", "いく": "趋向，ていく", "くる": "趋向，てくる", "みる": "尝试，てみる",
    "くださる": "请求，てください", "もらう": "受益，てもらう", "くれる": "受益，てくれる",
    "あげる": "给予，てあげる", "いける": "可能，能…下去", "なる": "变化", "ほしい": "希望，てほしい",
}


def breakdown(word):
    """“食べる + ない（否定）+ た（过去 / 完成）”。原型后面没有变形词尾时返回空串。"""
    tail = list(word.tail)
    if tail and tail[0][1] == "する" and word.base.endswith("する"):
        tail.pop(0)  # 連絡 + する 已经合成原型“連絡する”
    if not tail:
        return ""
    items = [word.base]
    for _, base, pos, lemma in tail:
        role = PART_ROLES.get(lemma if pos == "助動詞" else base) or PART_ROLES.get(base)
        items.append(f"{base}（{role}）" if role else base)
    return " + ".join(items)


SENTENCE_END = "。！？!?\n"
MAX_SENTENCE = 200


def sentence_around(text, start, end):
    """（text[start:end] 所在的那一句，目标词在这句里的起点）。句子太长时以目标词为中心截取。"""
    s = max(text.rfind(c, 0, start) for c in SENTENCE_END) + 1
    ends = [i for i in (text.find(c, end) for c in SENTENCE_END) if i >= 0]
    e = min(ends) + 1 if ends else len(text)
    return _clip_sentence(text, s, e, start, end)


# 英文的句号后面要跟空白才算句末（3.5 里的点不算）；中日文的句号、换行照旧
EN_SENTENCE_END = re.compile(r"[.!?]+[\"'”’)\]]*(?=\s|$)|[。！？\n]")
# 这些缩写和单个大写字母（人名缩写 J. K.）后面的点不是句末
EN_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "u.s", "jr", "sr", "prof", "no", "fig",
                    "approx", "dept", "inc", "ltd", "co", "mt", "jan", "feb", "aug", "sept", "oct", "nov", "dec"}


def _sentence_ends(text, pos, endpos):
    for m in EN_SENTENCE_END.finditer(text, pos, endpos):
        before = re.search(r"(\S+)$", text[max(0, m.start() - 12):m.start()])
        word = before.group(1) if before else ""
        if m.group().startswith(".") and (word.lower().rstrip(".") in EN_ABBREVIATIONS or re.fullmatch(r"[A-Z]", word)):
            continue
        yield m


def english_sentence_around(text, start, end):
    """和 sentence_around 一样，按英文的断句规则。"""
    s = 0
    for m in _sentence_ends(text, 0, start):
        s = m.end()
    m = next(_sentence_ends(text, end, len(text)), None)
    return _clip_sentence(text, s, m.end() if m else len(text), start, end)


def _clip_sentence(text, s, e, start, end):
    if e - s > MAX_SENTENCE:
        half = (MAX_SENTENCE - (end - start)) // 2
        s, e = max(s, start - half), min(e, end + half)
    segment = text[s:e]
    lead = len(segment) - len(segment.lstrip())
    return segment.strip(), start - s - lead


def is_japanese(text):
    return has_kanji(text) or any(is_kana(c) for c in text)


@dataclass
class EnglishWord:
    word: str
    phonetic: str  # 已转成国际音标写法，可能为空
    lines: list  # 中文释义，每个词性一行，如 "n. 薪水"
    tags: list  # 考试标签：四级、六级……
    forms: list  # [(类型, 词形)]，如 [("过去式", "ate")]
    via: str = ""  # 从变形跳到原词时的说明，如 "eating 是 eat 的现在分词"
    uk_notes: list = field(default_factory=list)  # 英美差异：["美式拼写 color"]、["英式说 lift"]


EN_TAGS = {"zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级", "ky": "考研", "toefl": "托福", "ielts": "雅思",
           "gre": "GRE"}
EN_FORMS = {"p": "过去式", "d": "过去分词", "i": "现在分词", "3": "三单", "s": "复数", "r": "比较级", "t": "最高级"}
# 词性标记后面跟空白，或者连着另一个标记（vi.vt.、vt.,vi.）
_POS_END = r"\.(?:\s|,|(?=[a-z]+\.))"
EN_POS = re.compile(r"(a|adj|adv|n|v|vt|vi|prep|conj|pron|int)" + _POS_END)  # 不含 pl.：复数的意思和原词一样
# 释义里说明“是某词的变形”的写法：( interest的名词复数 )、（service复数形式）、（open过去分词形式）、（第三人称单数）、
# go的ing形式、单三形式……
EN_FORM_OF = re.compile(r"名词复数|动词复数|复数形式|复数[）)]|的复数|第三人称|三单|的单数|过去式|过去时|过去分词|现在分词|"
                        r"比较级|最高级|的\w*形式|ing形式|单三形式|过去形式|动名词|的变形")
EN_POINTER = re.compile(r"=|等于|同“|同\"|<主英>|<英>|<美>|〈美〉|〈英〉|英国英语|美国英语")
EN_ANY_POS = re.compile(r"(a|adj|adv|n|v|vt|vi|prep|conj|pron|int|interj|num|art|aux|pl)" + _POS_END)
EN_NOUN = re.compile(r"(n|pl)" + _POS_END)
EN_VERB = re.compile(r"(v|vt|vi)" + _POS_END)
# 专名注释：比尔斯（姓氏）、因祸得福（电影名）、火箭（篮球队名）……ECDICT 把它们和普通释义写在一起
EN_NAME = re.compile(r"[（(][^）)]*(人名|姓氏|姓|男子名|女子名|地名|电影名|歌曲名|杂志名|书名|游戏名|公司名|队名|电视剧名|乐队名|品牌|"
                     r"郡名|州|城市|海峡|旧约|新约|圣经|剧集|剧名|连续剧|片名)[^）)]*[）)]")
# 复数另有常用意思的词（glasses 眼镜、spirits 情绪）：ECDICT 里没有考试标签，靠这张表认
OWN_PLURALS = {"glasses", "spectacles", "colours", "colors", "spirits", "manners", "customs", "damages", "minutes",
               "works", "papers", "forces", "quarters", "grounds", "troops", "savings", "surroundings", "belongings",
               "earnings", "outskirts", "remains", "contents", "odds", "premises", "proceeds", "letters", "arms",
               "goods", "looks", "sands", "waters", "heavens", "times", "pains", "regards", "terms", "relations"}
CJK = re.compile(r"[一-鿿]")


def parse_exchange(text):
    """ECDICT 的词形变化字段 "p:ate/d:eaten/0:eat/1:p" → {类型: 词形}。"""
    return dict(p.split(":", 1) for p in (text or "").split("/") if ":" in p)


# “colour 的变形”“（变形）”这种只说明是别的词的变体的行；“变形金刚”“变形的”不算
EN_VARIANT = re.compile(r"的变形|变形\s*[)）]|变形形式")


def sense_lines(row):
    """有中文释义的行：只写“<主英>=…”、变形说明、只有词性或音标的行不算。"""
    return [l for l in row[2].splitlines() if CJK.search(l) and not EN_POINTER.search(l)
            and not EN_VARIANT.search(l)]


def richness(row):
    """比较英美两个拼写的词条用：先看有没有带词性的释义，再看不算“[网络]”的义项数，最后看总义项数。"""
    lines = sense_lines(row)
    count = lambda ls: sum(len(re.split(r"[,;，；]", l)) for l in ls)  # noqa: E731
    return (any(EN_ANY_POS.match(l.strip()) for l in lines), count([l for l in lines if "[网络]" not in l]),
            count(lines))


def drop_names(line):
    """去掉一行里的专名部分（比尔斯（姓氏）；奥尼恩斯（英国语言学家））。整行都是专名时返回空串。"""
    m = EN_ANY_POS.match(line)
    head, body = (line[:m.end()], line[m.end():]) if m else ("", line)
    parts = [p.strip() for p in re.split(r"[;；]", body) if p.strip()]
    names = {re.split(r"[（(]", p)[0] for p in parts if EN_NAME.search(p) or "人名" in p}
    kept = [p for p in parts if not (EN_NAME.search(p) or "人名" in p or re.split(r"[（(]", p)[0] in names)]
    return head + "；".join(kept) if kept else ""


def own_senses(row, pos=EN_ANY_POS):
    """变形词自己的意思（不是“某词的过去式”这种说明，去掉姓氏、电影名之类的专名）。
    只有最后一段是变形说明的保留前面几段：n. 眼镜；双筒望远镜；玻璃（glass的复数形式）→ n. 眼镜；双筒望远镜"""
    lines = []
    for l in sense_lines(row):
        l = l.strip()
        if not pos.match(l):
            continue
        parts = re.split(r"[;；]", l)
        if EN_FORM_OF.search(parts[-1]) and len(parts) > 1 and not EN_FORM_OF.search("".join(parts[:-1])):
            l = "；".join(p.strip() for p in parts[:-1])
        if not EN_FORM_OF.search(l):
            lines.append(drop_names(l))
    return [l for l in lines if l]


def mostly_in(line, text):
    """line 的中文字六成以上也出现在 text 里：变形词照抄了原词的释义（孩子们 / 孩子）。"""
    chars = set(CJK.findall(line))
    return bool(chars) and len(chars & set(CJK.findall(text))) >= 0.6 * len(chars)


def pos_class(line):
    """释义行的词性大类：n / v / a / 其他。复数 pl. 算名词，vt. vi. 算动词，adj. adv. 算形容词类。"""
    m = EN_ANY_POS.match(line.strip())
    tag = m.group(1) if m else ""
    return {"n": "n", "pl": "n", "v": "v", "vt": "v", "vi": "v", "a": "a", "adj": "a", "adv": "a"}.get(tag, tag)


def bigrams(text):
    """相邻两个汉字组成的词：比较两段释义说的是不是一回事（委员会；街道办事处 / 委员会）。"""
    return {a + b for a, b in zip(text, text[1:]) if CJK.match(a) and CJK.match(b)}


def respell(word, src, dst):
    """按 src → dst 的拼写差异改 word：disorganized → disorganised 的差异用在 disorganize 上得 disorganise。
    差异超过两个字母、或 word 里没有对应的地方时返回 None。"""
    i = 0
    while i < min(len(src), len(dst)) and src[i] == dst[i]:
        i += 1
    a, b = src[i:], dst[i:]
    while a and b and a[-1] == b[-1]:
        a, b = a[:-1], b[:-1]
    if len(a) > 2 or len(b) > 2 or not word.startswith(src[:i]) or not word[i:].startswith(a):
        return None
    return word[:i] + b + word[i + len(a):]


class EnglishDictionary:
    """ECDICT 英汉词典（离线）。不是线程安全的，只在创建它的线程里用。"""

    def __init__(self):
        self.generation = dict_store.generation
        path = dict_store.path_of("ecdict")
        self.db = open_db(path) if path else None  # 还没下载完时为 None，查什么都查不到

    @property
    def ready(self):
        return self.db is not None

    @property
    def stale(self):
        return self.generation != dict_store.generation

    def _row(self, key):
        return self.db.execute("SELECT word, phonetic, translation, tag, exchange FROM en WHERE key = ?",
                               (key,)).fetchone()

    def lookup(self, word):
        if self.db is None:
            return None
        key = word.lower()
        row = self._row(key)
        lemma_row, kinds = self._lemma_row(key, row)
        other_spelling = uk_english.SPELLING.get(key) or uk_english.AMERICAN.get(key)
        if not kinds and other_spelling:
            # 另一种拼写那边找得到原词时用它的：labeled → labelled → label、colored → coloured → colour
            alt_lemma, alt_kinds = self._lemma_row(other_spelling, self._row(other_spelling))
            if alt_kinds and self._classes(alt_lemma) - {""}:
                lemma_row, kinds = alt_lemma, alt_kinds
        if lemma_row and not kinds and row and not own_senses(row):
            kinds = ["变形"]  # 自己只写着“get的过去分词”：gotten → get
        # 变形词跳到原词，并说明是哪种变形。原词要列着这个变形才跳（ECDICT 有些假原词：colored → colore）；
        # 只是“复数”而自己另有意思的不跳：they 不是 it 的复数；pediatrics（儿科学）不是形容词 pediatric 的复数
        lemma_classes = self._classes(lemma_row) if lemma_row else set()
        if "复数" in kinds and "n" not in lemma_classes and (
                lemma_classes - {"", "num", "interj"} or not self._trusted_plural(key, row, lemma_row)):
            lemma_3 = parse_exchange(lemma_row[4]).get("3", "").lower()
            own_noun = row and any(pos_class(l) == "n" for l in own_senses(row))
            if "v" in lemma_classes and (lemma_3 in (key, other_spelling) or not lemma_3 and not own_noun):
                # 原词只是动词：ECDICT 把三单记成了“复数”（economises → economise）
                kinds = list(dict.fromkeys("三单" if k == "复数" else k for k in kinds))
            elif kinds == ["复数"]:
                # 原词没有名词意思，不是它的复数：they 不是 it 的，pediatrics 不是形容词 pediatric 的，
                # headquarters、tweezers 不是动词 headquarter、tweezer 的三单
                lemma_row = None
            else:
                kinds = [k for k in kinds if k != "复数"]
        jump = lemma_row and (kinds or not row)
        # 英式名词是美式原词的“复数”时不跳：maths 不是 math 的复数，而是英式说法
        if jump and uk_english.VOCAB.get(lemma_row[0].lower(), "").endswith(f"说 {key}"):
            jump, lemma_row = False, None
        own = []
        if jump:
            # 本身也是个词的把它自己的意思也带上，按句子判断是哪个：词性和原词不同的（tired 疲倦的、left 左边的），
            # 它自己就有考试标签、是个常用词的（pants 裤子、found 建立），或者是复数另有意思的常用词（glasses 眼镜）。
            # 照抄原词释义的后面再去掉
            # 原词卡片上没有的词性也算（bandage 只有名词，bandaged 带上“vt. 用绷带包扎”）
            lemma_words = bigrams("".join(sense_lines(lemma_row)))
            own = [l for l in (own_senses(row, EN_POS) if row else [])
                   if pos_class(l) not in lemma_classes or row[3].strip()
                   or key in OWN_PLURALS and pos_class(l) == "n" and not bigrams(l) & lemma_words]
            row = lemma_row
        if not row:
            return None
        # 以英式为准：标题用英式拼写。英美两种拼写的词条哪个释义好用哪个（ECDICT 里有的英式词条只写“<主英>=…”，
        # 有的美式词条反过来只写“<美>=…”或“[网络]…”）；词形变化优先用英式词条自己的（neighboured，而不是 neighbored）
        title, uk_notes, card_rows = row[0], [], [row]
        # 不是英美成对的词：变形只改双写字母（traveled → travelled），别的不动（checkered 不能变成 chequered）
        parts = {}
        for k, v in parse_exchange(row[4]).items():
            b = uk_english.british(v)
            parts[k] = b if re.sub(r"(.)\1", r"\1", b.lower()) == re.sub(r"(.)\1", r"\1", v.lower()) else v
        # 只有动词英美写法不同的（practice → practise）：动词变形用英式的，三单照现在分词的差异改
        if "作动词" in uk_english.PARTIAL.get(row[0].lower(), ""):
            ex = parse_exchange(row[4])
            for k in "pdi":
                if ex.get(k):
                    parts[k] = uk_english.british(ex[k])
            if ex.get("i") and ex.get("3") and parts["i"] != ex["i"]:
                parts["3"] = respell(ex["3"], ex["i"], parts["i"]) or ex["3"]
        lower = row[0].lower()
        # 名词复数跳到原词时不按光标处的词推：analyses 和动词的 analyzes 拼写不同，和 analysis 无关
        us, uk = self._pair(lower, lower if kinds == ["复数"] else key)
        if us:
            us_row, uk_row = self._row(us), self._row(uk)
            card_rows += [r for r in (us_row, uk_row) if r]
            title = uk.capitalize() if row[0][:1].isupper() else uk
            uk_parts = parse_exchange(uk_row[4]) if uk_row else {}
            other = parse_exchange((us_row or row)[4])
            parts = {}
            for k in EN_FORMS:
                # 比较级、最高级英美一样（phonier），不改
                form = uk_parts.get(k) or (other.get(k) and (other[k] if k in "rt" else self._to_uk(other[k], us, uk)))
                if form:
                    parts[k] = form
            row = max((r for r in (uk_row, us_row) if r), key=richness)  # 一样好时用英式的
            # 另一个词条有、选中的词条没有的词性补上（sulphur 补上美式词条的 vt. 用硫磺处理）
            alt = us_row if row is uk_row else uk_row
            have = {pos_class(l) for l in sense_lines(row)}
            # 没标词性的、只标了领域的（[化] …）意思不一样的也补上
            extra = [l for l in (sense_lines(alt) if alt else []) if l not in row[2] and (
                pos_class(l) not in have if EN_ANY_POS.match(l.strip())
                else "[网络]" not in l and not mostly_in(l, row[2]))]
            # 考试标签两个词条的合起来（centre 的在美式词条 center 上）
            tags = " ".join(dict.fromkeys(" ".join(r[3] for r in (uk_row, us_row) if r).split()))
            row = (row[0], row[1], "\n".join([row[2]] + extra), tags, row[4])
            uk_notes.append(f"美式拼写 {us_row[0] if us_row else us}")
        elif key in uk_english.SPELLING:
            uk_notes.append(f"{word} 是美式拼写，英式写作 {uk_english.british(word)}")  # labeled → label
        via = ""
        if jump:
            via = f"{word} 是 {title} 的{'、'.join(kinds) or '变形'}"
            # 照抄原词释义的不算（和卡片上可能用到的每个词条都比）
            card_text = "\n".join(r[2] for r in card_rows)
            own = [l for l in own if not mostly_in(l, card_text)]
            if own:
                via += f"；本身也是 {own[0] if len(own[0]) <= 24 else own[0][:23] + '…'}"
        # 说明以光标处的词为准（tyres、lifts），它没有时再用原词的。动词变形（fell、tired、stored）只留讲动词的说明，
        # “指秋天时说 autumn”“指轮胎时写 tyre”这种名词的说明对它不成立
        lemma_notes = uk_english.notes(lower)
        if jump and "复数" not in kinds:
            lemma_notes = [n for n in lemma_notes if "动词" in n]
        uk_notes = list(dict.fromkeys(uk_notes + (uk_english.notes(key) or lemma_notes)))
        _, phonetic, translation, tag, _ = row
        plural = parts.get("s", "")
        if plural.lower() == title.lower():
            del parts["s"]  # tweezers 的“复数”是它自己
        elif plural.lower() == title.lower() + "s" and plural.lower() != key and (
                title.endswith("s") or "s" in parse_exchange(row[4]).get("1", "")):
            del parts["s"]  # ECDICT 给本来就是复数的词又加了个复数（faecess、childrens）
        if parts.get("s") and not any(EN_NOUN.match(l.strip()) for l in translation.splitlines()):
            # 没有名词意思的词也列了个“复数”（americanises）：动词的话其实是三单，别的不要
            plural = parts.pop("s")
            # 只有动词原词才挪成三单；approaching 这种分词词条的“复数” approachings 不要
            if "0" not in parse_exchange(row[4]) and any(EN_VERB.match(l.strip()) for l in translation.splitlines()):
                parts.setdefault("3", plural)
        t = title.lower()
        if parts.get("i", "").lower().endswith("eing") and t.endswith("e") and len(t) > 2 and t[-2] not in "aeiouyg":
            parts["i"] = parts["i"][:-4] + "ing"  # ECDICT 的 axeing → axing（being、ageing、singeing、cueing 不动）
        forms = [(label, parts[k]) for k, label in EN_FORMS.items() if parts.get(k)]
        return EnglishWord(title, ipa(phonetic), [l.strip() for l in translation.splitlines() if l.strip()],
                           [EN_TAGS[t] for t in tag.split() if t in EN_TAGS], forms, via, uk_notes)

    @staticmethod
    def _trusted_plural(key, row, lemma_row):
        """原词的释义都没标词性（laptop 只写着“[计] 膝上型电脑”）时，不知道它是不是名词：
        复数词条自己没有别的意思、没说是别的词的复数（aches 写着“ache的名词复数”，不是 ACH 的），
        原词也不只是人名、网络释义（cance、crossbone）时才跳。"""
        if not row:
            return True
        # corps 军队、belongings 财产、faeces 粪便：自己有意思的不跳。没标词性的行去掉括号里的说明再看，
        # 和原词说的不是一回事才算（discographies 唱片目录 / discography 椎间盘造影术、orthodontics 正牙学 / 牙正常的）
        lemma_words = bigrams("".join(sense_lines(lemma_row)))
        plain = [drop_names(re.sub(r"[(（][^()（）]*[)）]", "", l)) for l in sense_lines(row)
                 if not EN_ANY_POS.match(l.strip()) and "[网络]" not in l]
        if own_senses(row) or any(CJK.search(l) and not bigrams(l) & lemma_words for l in plain):
            return False
        named = {w.lower() for w in re.findall(r"([A-Za-z]+)\s*的[^)）\n]{0,4}复数", row[2])}
        if named - {lemma_row[0].lower()}:
            return False
        return any("[网络]" not in l and drop_names(l) for l in sense_lines(lemma_row))

    def _classes(self, row):
        """词条释义的词性大类。英式词条只写“<主英>=economize”时看另一种拼写的。"""
        classes = {pos_class(l) for l in sense_lines(row)}
        other = uk_english.AMERICAN.get(row[0].lower()) or uk_english.SPELLING.get(row[0].lower())
        if not classes and other and self._row(other):
            classes = {pos_class(l) for l in sense_lines(self._row(other))}
        if not classes - {""}:
            # 释义都没标词性时，“vt. [主英国英语] =criminalize”这种指向别的词的行也能说明词性
            rows = [row] + [r for r in [other and self._row(other)] if r]
            classes |= {pos_class(l) for r in rows for l in r[2].splitlines() if EN_ANY_POS.match(l.strip())}
        return classes

    def _pair(self, lower, key):
        """原词的（美式拼写，英式拼写）；不是英美成对的词时为 (None, None)。"""
        if lower in uk_english.AMERICAN:
            return uk_english.AMERICAN[lower], lower
        if lower in uk_english.SPELLING:
            return lower, uk_english.SPELLING[lower]
        if lower in uk_english.PARTIAL:
            return None, None  # practice、license：只有部分义项不同，由说明交代
        # 原词不在对照表里、光标处的词在（disorganised → disorganize）：照它的拼写差异推出原词的另一种写法，词典里有才算
        if key in uk_english.SPELLING:
            key_us, key_uk = key, uk_english.SPELLING[key]
        elif key in uk_english.AMERICAN:
            key_us, key_uk = uk_english.AMERICAN[key], key
        else:
            return None, None
        if re.sub(r"(.)\1", r"\1", key_us) == re.sub(r"(.)\1", r"\1", key_uk):
            return None, None  # 只差双写字母的（labeled / labelled）：原词 label 英美一样
        simple = sum(a != b for a, b in zip(key_us, key_uk)) <= 1 and abs(len(key_us) - len(key_uk)) <= 1
        lemma_text = "".join(sense_lines(self._row(lower)) if self._row(lower) else [])
        for src, dst, is_us in ((key_us, key_uk, True), (key_uk, key_us, False)):
            other = respell(lower, src, dst)
            if other in uk_english.PARTIAL:
                continue  # practise 不和 practice 配对：名词 practice 英式也这么写
            other_row = self._row(other) if other and other != lower else None
            if not other_row:
                continue
            # 推出来的词要和原词说的是一回事（aging/ageing 推出的 agee 是“半开地”，不是 age）；
            # 只差一个字母的（z → s、补 u）词条残缺也行，别的要是个有词性的正经词条
            same = bigrams("".join(sense_lines(other_row))) & bigrams(lemma_text) or lower in other_row[2].lower()
            if same and (simple or any(EN_ANY_POS.match(l.strip()) for l in sense_lines(other_row))):
                return (lower, other) if is_us else (other, lower)
        return None, None

    def _to_uk(self, form, us, uk):
        """美式词条列的变形转成英式：查对照表，没有的照原词的拼写差异改（aggrandized → aggrandised）。
        改不了的不要（宁可少一个变形，也不在英式卡片上写美式拼写）。"""
        if uk_english.british(form) != form:
            return uk_english.british(form)
        stem = uk.lower()[:-1] if uk.lower().endswith("e") else uk.lower()  # organise → organis（organising）
        if form.lower().startswith(stem) or us == uk:
            return form
        rest = form[len(us):]
        if us.lower().endswith("er") and uk.lower().endswith("re") and form.lower().startswith(us.lower()) \
                and rest[:1].lower() in ("e", "i"):
            return uk[:-1] + rest  # sabered → sabred、sabering → sabring
        converted = respell(form.lower(), us.lower(), uk.lower())
        if converted and form[:1].isupper():
            converted = converted[:1].upper() + converted[1:]
        # 改出来的要以英式原词开头（aggrandised）或者词典里有（sabred）；phoney 改不出 phoneier 这种
        return converted if converted and (converted.lower().startswith(stem) or self._row(converted.lower())) else None

    def _lemma_row(self, key, row):
        """key 的原词词条和 key 是它的哪几种变形：(词条, ["过去式", ...])。不是变形词时为 (None, [])。"""
        exchange = parse_exchange(row[4]) if row else {}
        lemma = exchange.get("0") or (None if row else self._lemma_of(key))
        lemma_row = self._row(lemma) if lemma and lemma != key else None
        if not lemma_row:
            return None, []
        spellings = {key, uk_english.SPELLING.get(key), uk_english.AMERICAN.get(key)}
        kinds = [EN_FORMS[k] for k, v in parse_exchange(lemma_row[4]).items()
                 if v.lower() in spellings and k in EN_FORMS]
        paired = lemma.lower() in uk_english.SPELLING or lemma.lower() in uk_english.AMERICAN
        # 原词是个正经词条、这个词也确实是它加词尾变来的（trialled ← trial）
        regular = len(lemma) > 2 and key.startswith(lemma.lower()[:-1]) \
            and any(EN_ANY_POS.match(l.strip()) for l in sense_lines(lemma_row))
        if not kinds and (paired or regular):
            # ECDICT 的原词常常没列变形（privatise、trial），按这个词自己标的变形类型（1:dp）
            kinds = [EN_FORMS[c] for c in exchange.get("1", "") if c in EN_FORMS]
        return lemma_row, kinds

    def _lemma_of(self, key):
        row = self.db.execute("SELECT lemma FROM en_forms WHERE form = ?", (key,)).fetchone()
        return row[0] if row else None


def ipa(phonetic):
    """ECDICT 的音标用了几个替代字符，换成标准的国际音标。"""
    return phonetic.replace("ә", "ə").replace(":", "ː").replace("'", "ˈ").replace("ɒː", "ɔː")


# ---------------------------------------------------------------- 英文取词（按住 Shift 停在英文单词上）

EN_JOINERS = set(".\\@:#=")  # 夹在两个字母数字之间时，说明是网址、路径、邮箱、代码的一部分
# 整段（空白之间）像网址、路径时也不查：https://、C:\、/usr/bin、a/b/c；单个斜杠的 and/or、his/her 照查
EN_PATH = re.compile(r"://|^[A-Za-z]:[\\/]|\\|^[/~]|/.*/|^www\.", re.I)


def halfwidth(text):
    """全角英数（ＡＢＣ）转成半角。逐字替换，长度不变，位置都对得上。"""
    return "".join(chr(ord(c) - 0xFEE0) if "！" <= c <= "～" else c for c in text)


def is_latin(c):
    """英文单词里的字母：ASCII 字母和带重音的拉丁字母（café、naïve、résumé）。"""
    return c.isalpha() and (c.isascii() or "À" <= c <= "ɏ")


def in_word(c):
    return is_latin(c) or c.isdigit() or c in "_'’-"


def strip_accents(word):
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


@dataclass
class EnglishHit:
    """光标处的英文单词。context / span 和 WordInfo 的同名字段一样：给 AI 当上下文，也用来判断是不是同一个词。"""
    surface: str  # 原文里的写法：Running、don't、well-known
    entry: object  # EnglishWord；词典里没有时为 None
    context: str
    span: tuple  # surface 在 context 里的位置
    dictionary_ready: bool = True  # 英汉词典是否已下载（精简版第一次运行时还没有）

    @property
    def base(self):
        return self.entry.word if self.entry else self.surface


def english_word_at(text, offset):
    """text[offset] 处英文单词的 (起点, 终点)。不在字母上，或是代码标识符、网址、路径里的一段时返回 None。"""
    text = halfwidth(text)
    if not 0 <= offset < len(text) or not is_latin(text[offset]):
        return None
    s, e = offset, offset + 1
    while s > 0 and in_word(text[s - 1]):
        s -= 1
    while e < len(text) and in_word(text[e]):
        e += 1
    # 两头的撇号、连字符不算（'quoted'、破折号 --），光标在字母上所以不会越过 offset
    while text[s] in "'’-":
        s += 1
    while text[e - 1] in "'’-":
        e -= 1
    if any(c.isdigit() or c == "_" for c in text[s:e]):
        return None  # mp3、v2、my_var
    if s >= 2 and text[s - 1] in EN_JOINERS and text[s - 2].isalnum():
        return None  # example.com、C:\Users、user@mail
    if e + 1 < len(text) and text[e] in EN_JOINERS and text[e + 1].isalnum():
        return None
    ts, te = s, e
    while ts > 0 and not text[ts - 1].isspace():
        ts -= 1
    while te < len(text) and not text[te].isspace():
        te += 1
    if EN_PATH.search(text[ts:te]):
        return None
    return s, e


def lookup_english(en_dict, text, offset):
    """text[offset] 处的英文单词 → EnglishHit。不在英文单词上时返回 None。
    先查整体（well-known、don't），查不到再查光标所在的那一段（state-of-the-art 的 art）和去掉 's 的形式。"""
    span = english_word_at(text, offset)
    if span is None:
        return None
    s, e = span
    plain = halfwidth(text)
    run = plain[s:e]
    candidates = [(s, e)]
    if "-" in run:
        part_s = max(s, plain.rfind("-", s, offset) + 1)
        part_e = plain.find("-", offset, e)
        candidates.append((part_s, e if part_e < 0 else part_e))
    if run[-2:].lower() in ("'s", "’s"):
        candidates.append((s, e - 2))  # John's、today's
    for cs, ce in dict.fromkeys(candidates):
        form = plain[cs:ce].replace("’", "'")
        entry = en_dict.lookup(form) or (en_dict.lookup(strip_accents(form)) if not form.isascii() else None)
        if entry:
            return EnglishHit(text[cs:ce], entry, text, (cs, ce), en_dict.ready)
    # 词典里没有：普通单词照样弹出卡片（专有名词、新词可以让 AI 解释）；驼峰式的多半是代码，不弹
    if len(run) < 2 or re.search(r"[a-z][A-Z]", run):
        return None
    return EnglishHit(text[s:e], None, text, (s, e), en_dict.ready)


@dataclass
class Token:
    surface: str
    start: int
    end: int
    pos1: str
    pos2: str
    base: str  # 原型（书写形式）
    kana: str  # 本处读音（平假名）
    kana_base: str  # 原型读音（平假名）
    a_type: str
    lemma: str = ""  # 词元：读んだ 的 だ 是 た（过去），静かな 的 な 是 だ（断定）

    @property
    def is_symbol(self):
        return self.pos1 in ("補助記号", "空白", "記号") or not self.surface.strip()


@dataclass
class WordInfo:
    surface: str  # 光标处的整个词，含变形：食べなかった
    ruby: list  # [(文字, 读音或 None)]，用于标注假名
    base: str  # 原型：食べる
    base_reading: str  # 原型读音：たべる
    accents: list  # 音调型，如 [2]
    senses: list  # [(词性列表, 释义列表, 附注列表)]：JMdict 的；JMdict 没有这个词时用英文维基词典的
    context: str  # 取到的上下文，给 AI 用
    span: tuple  # surface 在 context 里的位置
    pos: list = field(default_factory=list)
    tail: list = field(default_factory=list)  # 原型后面的变形词尾 [(表层, 原型, 词性, 词元)]，用来拆解
    zh: list = field(default_factory=list)  # 中文维基词典的释义 [(释义, 标记列表)]


class JapaneseDictionary:
    """不是线程安全的：SQLite 连接和分词器只在创建它的线程里用。"""

    def __init__(self):
        self.generation = dict_store.generation
        paths = dict_store.current()
        if "unidic" in paths:
            d = paths["unidic"]["path"].replace("\\", "/")
            self.tagger = fugashi.Tagger(f'-r "{d}/mecabrc" -d "{d}"')
        else:
            self.tagger = fugashi.Tagger()  # 没有完整版 UniDic 时用随程序带的精简版
        self.db = open_db(dict_store.require("jmdict"))  # 没下载时抛出 dict_store.Missing
        self.wikt = open_db(paths["wiktionary"]["path"]) if "wiktionary" in paths else None

    @property
    def stale(self):
        """后台装好了新词典，需要重新打开。"""
        return self.generation != dict_store.generation

    def tokenize(self, text):
        tokens, pos = [], 0
        for w in self.tagger(text):
            start = text.find(w.surface, pos)
            if start < 0:
                start = pos
            end = start + len(w.surface)
            pos = end
            f = w.feature
            tokens.append(Token(w.surface, start, end, f.pos1 or "", f.pos2 or "",
                                f.orthBase if f.orthBase and f.orthBase != "*" else w.surface,
                                to_hiragana(f.kana or "") if f.kana and f.kana != "*" else "",
                                to_hiragana(f.kanaBase or "") if f.kanaBase and f.kanaBase != "*" else "",
                                f.aType or "*", f.lemma or ""))
        return tokens

    def entries(self, form):
        rows = self.db.execute(
            "SELECT e.common, e.data FROM forms f JOIN entries e ON e.id = f.id WHERE f.form = ? ORDER BY e.common DESC",
            (form,)).fetchall()
        return [json.loads(data) for _, data in rows]

    def wiktionary(self, form, lang, reading=""):
        """维基词典里 form 的词条 [(读音, 词性, [[释义, 标记], ...])]。给了读音时只要读音对得上的和没标读音的（都对不上就全要）。"""
        if self.wikt is None:
            return []
        rows = [(r, pos, json.loads(senses)) for r, pos, senses in
                self.wikt.execute("SELECT reading, pos, senses FROM wikt WHERE word = ? AND lang = ?", (form, lang))]
        if reading:
            same = [row for row in rows if to_hiragana(row[0]) == to_hiragana(reading)]
            if same:
                rows = same + [row for row in rows if not row[0]]
        return rows

    def known(self, form):
        if self.entries(form):
            return True
        return self.wikt is not None and self.wikt.execute(
            "SELECT 1 FROM wikt WHERE word = ? LIMIT 1", (form,)).fetchone() is not None

    def accents(self, word, reading):
        rows = self.db.execute("SELECT reading, accent FROM accents WHERE word = ?", (word,)).fetchall()
        for r, accent in rows:
            if r == reading:
                return [int(a) for a in accent.split(",") if a.isdigit()]
        return []

    def lookup(self, text, offset):
        """text[offset] 处的词。不是日语词时返回 None。"""
        tokens = self.tokenize(text)
        i = next((k for k, t in enumerate(tokens) if t.start <= offset < t.end), None)
        if i is None or tokens[i].is_symbol or not is_japanese(tokens[i].surface):
            return None
        i = self._head_of(tokens, i)

        # 从光标所在的词开始找词典里最长的匹配；最后一个词按原型匹配（取り扱っ → 取り扱う）
        best = None
        for k in range(min(MAX_SPAN, len(tokens) - i), 0, -1):
            span = tokens[i:i + k]
            if any(t.is_symbol for t in span):
                continue
            forms = ["".join(t.surface for t in span)]
            if span[-1].pos1 in INFLECTABLE or span[-1].pos1 == "助動詞":
                # 先按原型查：飲みたい 要查到 飲む，而不是名词 飲み
                forms.insert(0, "".join(t.surface for t in span[:-1]) + span[-1].base)
            for form in dict.fromkeys(forms):
                if self.known(form) and self._plausible(form, span):  # JMdict 或维基词典里有（网络用语常常只有维基词典收了）
                    best = (k, form, self.entries(form))
                    break
            if best:
                break

        if best:
            k, form, entries = best
        else:
            k, form, entries = 1, tokens[i].base, []
        span = tokens[i:i + k]
        last = span[-1]

        # 把后面的变形词尾并进来：食べ + なかっ + た、連絡 + し + て + ください
        j, suru, after_te = i + k, False, False
        if last.pos1 == "名詞" and j < len(tokens) and tokens[j].pos1 == "動詞" and tokens[j].base == "する":
            suru, j = True, j + 1
        if last.pos1 in INFLECTABLE or suru:
            while j < len(tokens):
                t = tokens[j]
                if t.pos1 == "助動詞":
                    after_te = False
                elif t.pos1 == "助詞" and t.pos2 == "接続助詞" and t.surface in ("て", "で", "ば", "ちゃ", "じゃ"):
                    after_te = True
                elif after_te and t.pos1 in ("動詞", "形容詞") and t.pos2 == "非自立可能":
                    after_te = False
                else:
                    break
                j += 1

        entry, reading = self._pick_entry(entries, form, span)
        wikt_en = self.wiktionary(form, "en", reading)
        if not entry and wikt_en and wikt_en[0][0] and (not reading or reading == form or len(span) > 1):
            reading = to_hiragana(wikt_en[0][0])  # 分词器不认识的词，读音以维基词典为准
        base = form + ("する" if suru else "")
        base_reading = reading + ("する" if suru else "")
        surface = text[tokens[i].start:tokens[j - 1].end]

        accents = self.accents(form, reading)
        if not accents and len(span) == 1:
            accents = [int(a) for a in last.a_type.split(",") if a.isdigit()][:1]

        if surface == form and entry:
            ruby = ruby_segments(surface, reading)
        else:
            ruby = []
            for t in tokens[i:j]:
                ruby.extend(ruby_segments(t.surface, t.kana))

        senses = self._senses(entry, form, reading) if entry else []
        if senses:
            senses += extra_slang_senses(senses, wikt_en)
        else:  # JMdict 没有的词全用维基词典的
            senses = [([pos], [gloss], tags) for _, pos, rows in wikt_en for gloss, tags in rows]
        zh = []
        for _, _, rows in self.wiktionary(form, "zh", reading):
            for gloss, tags in rows:
                if all(gloss != z for z, _ in zh):
                    zh.append((gloss, tags))
        # 太多时只留前几条，但网络用语 / 俚语义项总要留着
        zh = [z for k, z in enumerate(zh) if k < MAX_ZH or SLANG_TAGS & set(z[1])]
        return WordInfo(surface, ruby, base, base_reading, accents, senses, text,
                        (tokens[i].start, tokens[j - 1].end), senses[0][0] if senses else [],
                        [(t.surface, t.base, t.pos1, t.lemma) for t in tokens[i + k:j]], zh)

    def _plausible(self, form, span):
        """以助词结尾的多词匹配要读音对得上：「今日は暑い」不能当成寒暄语「今日は（こんにちは）」。"""
        if len(span) == 1 or span[-1].pos1 != "助詞":
            return True
        reading = "".join(t.kana for t in span)
        entries = self.entries(form)
        if entries:
            return any(kana == reading for e in entries for kana, _ in e["r"])
        return any(to_hiragana(r) == reading for r, _, _ in self.wiktionary(form, "en") + self.wiktionary(form, "zh"))

    @staticmethod
    def _head_of(tokens, i):
        """指在词尾（なかっ、た、て、する……）上时，退回到它所属的词。"""
        while i > 0:
            t, prev = tokens[i], tokens[i - 1]
            tail = (t.pos1 == "助動詞"
                    or (t.pos1 == "助詞" and t.pos2 == "接続助詞" and t.surface in ("て", "で", "ば"))
                    or (t.pos1 in ("動詞", "形容詞") and t.pos2 == "非自立可能"
                        and (prev.surface in ("て", "で", "ば") or (t.base == "する" and prev.pos1 == "名詞"))))
            if not tail or prev.is_symbol or prev.pos1 in ("助詞",) and prev.surface not in ("て", "で", "ば"):
                break
            i -= 1
        return i

    def _pick_entry(self, entries, form, span):
        """同形词（生物：せいぶつ / なまもの 是两个词条）里挑一个：优先挑分词器读音所在的词条；
        词条内部用词典排在前面的常用读音（日本語 读 にほんご 而不是 にっぽんご）。"""
        mecab_reading = "".join(t.kana_base if t is span[-1] else t.kana for t in span)

        def readings(e):
            if form in (kana for kana, _ in e["r"]):
                return [form]  # 查到的本身就是假名写法
            return [kana for kana, applies in e["r"] if not applies or form in applies]

        def rank(e):
            """按假名查到好几个词条时，这个读音排在前面的优先：この 是「此の」的第一个读音，只是「九」的第四个。"""
            kana = [k for k, _ in e["r"]]
            return kana.index(form) if form in kana else 0

        candidates = [(e, readings(e)) for e in entries]
        candidates = sorted(((e, r) for e, r in candidates if r), key=lambda c: rank(c[0]))
        for e, r in candidates:
            if mecab_reading in r:
                return e, r[0]
        if candidates:
            return candidates[0][0], candidates[0][1][0]
        return None, mecab_reading or form

    @staticmethod
    def _senses(entry, form, reading):
        result = []
        for pos, glosses, misc, only_kanji, only_kana in entry["s"]:
            if only_kanji and form in entry["k"] and form not in only_kanji:
                continue
            if only_kana and reading not in only_kana:
                continue
            result.append((pos, glosses, misc))
        return result
