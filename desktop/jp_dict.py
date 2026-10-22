"""日语查词：分词（MeCab + UniDic）、还原原型、查 JMdict / 维基词典释义和 Kanjium 音高。"""
import json
import pathlib
import re
import sqlite3
from dataclasses import dataclass, field

import fugashi

import dict_store

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


EN_TAGS = {"zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级", "ky": "考研", "toefl": "托福", "ielts": "雅思",
           "gre": "GRE"}
EN_FORMS = {"p": "过去式", "d": "过去分词", "i": "现在分词", "3": "三单", "s": "复数", "r": "比较级", "t": "最高级"}


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
        key, via = word.lower(), ""
        row = self._row(key)
        exchange = dict(p.split(":", 1) for p in row[4].split("/") if ":" in p) if row else {}
        lemma = exchange.get("0") or (None if row else self._lemma_of(key))
        if lemma and lemma != key and self._row(lemma):
            # 变形词跳到原词，并说明是哪种变形
            lemma_row = self._row(lemma)
            kinds = [EN_FORMS[k] for k, v in (p.split(":", 1) for p in lemma_row[4].split("/") if ":" in p)
                     if v.lower() == key and k in EN_FORMS]
            via = f"{word} 是 {lemma_row[0]} 的{'、'.join(kinds) or '变形'}"
            row = lemma_row
        if not row:
            return None
        text, phonetic, translation, tag, exchange = row
        parts = dict(p.split(":", 1) for p in exchange.split("/") if ":" in p)
        forms = [(label, parts[k]) for k, label in EN_FORMS.items() if parts.get(k)]
        return EnglishWord(text, ipa(phonetic), [l.strip() for l in translation.splitlines() if l.strip()],
                           [EN_TAGS[t] for t in tag.split() if t in EN_TAGS], forms, via)

    def _lemma_of(self, key):
        row = self.db.execute("SELECT lemma FROM en_forms WHERE form = ?", (key,)).fetchone()
        return row[0] if row else None


def ipa(phonetic):
    """ECDICT 的音标用了几个替代字符，换成标准的国际音标。"""
    return phonetic.replace("ә", "ə").replace(":", "ː").replace("'", "ˈ").replace("ɒː", "ɔː")


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

        candidates = [(e, readings(e)) for e in entries]
        candidates = [(e, r) for e, r in candidates if r]
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
