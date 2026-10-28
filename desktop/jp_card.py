"""把查词结果画成一张卡片（PIL RGBA 图片，带透明阴影边）。"""
import os
import re
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from jp_dict import SLANG_TAGS, pitch_pattern, register_labels, split_mora, to_hiragana

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
WIDTH = 380
PAD = 18
SHADOW = 16  # 卡片四周留给阴影的透明边
RADIUS = 14
MAX_SENSES = 5  # 英文释义最多显示几条（AI 选中的那条在后面也会补上）
MAX_ZH_LINES = 6

BG = (255, 255, 255, 255)
BORDER = (226, 232, 240, 255)
TEXT = (17, 24, 39, 255)
MUTED = (107, 114, 128, 255)
FAINT = (156, 163, 175, 255)
ACCENT = (0, 120, 212, 255)
PITCH = (234, 88, 12, 255)
CHIP_BG = (238, 242, 255, 255)
CHIP_TEXT = (55, 48, 163, 255)
AI_BG = (245, 243, 255, 255)
AI_ACCENT = (109, 40, 217, 255)
HIGHLIGHT = (254, 243, 199, 255)
REGISTER = (190, 18, 60, 255)  # 网络用语、俚语等标记
ZH_BG = (240, 253, 244, 255)
ZH_ACCENT = (21, 128, 61, 255)
ERROR = (185, 28, 28, 255)

POS_ZH = {
    "n": "名词", "pn": "代词", "v1": "一段动词", "v1-s": "一段动词", "vk": "カ变动词", "vs": "可加する",
    "vs-i": "サ变动词", "vs-s": "サ变动词", "vz": "サ变动词", "vt": "他动词", "vi": "自动词",
    "adj-i": "い形容词", "adj-ix": "い形容词", "adj-na": "な形容词", "adj-no": "可加の", "adj-t": "タル形容词",
    "adj-pn": "连体词", "adj-f": "连体词", "adv": "副词", "adv-to": "副词", "aux": "助动词", "aux-v": "助动词",
    "aux-adj": "助形容词", "conj": "连词", "cop": "系词", "ctr": "量词", "exp": "惯用语", "int": "感叹词",
    "num": "数词", "pref": "前缀", "suf": "后缀", "n-suf": "名词后缀", "n-pref": "名词前缀", "prt": "助词",
    # 维基词典的词性
    "noun": "名词", "verb": "动词", "adj": "形容词", "adj_noun": "な形容词", "adv": "副词", "intj": "感叹词",
    "name": "专有名词", "phrase": "短语", "proverb": "谚语", "suffix": "后缀", "prefix": "前缀", "pron": "代词",
    "particle": "助词", "postp": "助词", "conj": "连词", "counter": "量词", "classifier": "量词", "det": "连体词",
}


@lru_cache(maxsize=None)
def font(name, size, index=0):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size, index=index)


def jp(size, bold=False):
    return font("YuGothB.ttc" if bold else "YuGothM.ttc", size)


def zh(size, bold=False):
    return font("msyhbd.ttc" if bold else "msyh.ttc", size)


def en(size):
    return font("segoeui.ttf", size)


def pos_labels(tags):
    labels = []
    for tag in tags:
        label = "五段动词" if tag.startswith("v5") else POS_ZH.get(tag)
        if label and label not in labels:
            labels.append(label)
    return labels


def text_width(f, text):
    return f.getlength(text)


def wrap(f, text, width):
    """按宽度折行（英文按空格断，太长的词硬断）。"""
    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}".strip()
        if text_width(f, candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        while text_width(f, word) > width:
            cut = len(word)
            while cut > 1 and text_width(f, word[:cut]) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    if line:
        lines.append(line)
    return lines


NO_LINE_START = set("。、，．,.！？!?）)」』】〕”’ー々ゃゅょっャュョッ：；:;")
NO_LINE_END = set("（(「『【〔“‘")


def wrap_chars(f, text, width):
    """按字折行（中日文没有空格可断）。句号、右括号等不放在行首（宁可让上一行稍微超出一点），左括号不放在行尾。"""
    lines, line = [], ""
    for c in text:
        if text_width(f, line + c) > width and line and c not in NO_LINE_START:
            carry = ""
            while line and line[-1] in NO_LINE_END:
                carry, line = line[-1] + carry, line[:-1]
            if line:
                lines.append(line)
            line = (carry + c).lstrip()
        else:
            line += c
    if line:
        lines.append(line)
    return lines


class Card:
    """render() 返回图片。点击区域（图片坐标，已乘 scale）：header_bottom 之上读原文；ai_region 内请求 AI 解释；
    其余读原型。ai 是 {"state": None/loading/streaming/done/error, "text": ...}。"""

    def __init__(self, word, scale=1.0, ai=None):
        self.word = word
        self.s = scale
        self.ai = ai or {"state": None, "text": ""}
        self.image = None
        self.header_bottom = 0
        self.ai_region = None  # (上, 下)
        self.links = []  # 英文释义里每个单词的位置：(左, 上, 右, 下, 单词)
        self.margin = round(SHADOW * scale)

    def link_at(self, x, y):
        """点击位置上的英文单词（图片坐标）。"""
        for l, t, r, b, word in self.links:
            if l <= x <= r and t <= y <= b:
                return word
        return None

    def px(self, v):
        return round(v * self.s)

    def render(self):
        s, px = self.s, self.px
        w = self.word
        ops = []  # (y 偏移, 绘制函数)；先排版算出总高度，再一次性画
        y = PAD

        # 标题：带假名注音的原文
        big, small = jp(px(28), bold=True), jp(px(12))
        ruby_h = px(16) if any(r for _, r in w.ruby) else 0
        # 注音可以伸出汉字两侧；只有相邻两段注音会重叠时才把后面的字往右挪
        x = PAD * s
        ruby_right = -1e9
        segments = []
        for text, reading in w.ruby:
            tw = text_width(big, text)
            rw = text_width(small, reading) if reading else 0
            if reading:
                x += max(0, ruby_right + px(2) - (x + (tw - rw) / 2))
                x = max(x, PAD * s + max(0, (rw - tw) / 2))
                ruby_right = x + (tw + rw) / 2
            segments.append((x, text, reading, tw, rw, tw))
            x += tw
        title_y = y
        y += (ruby_h + px(36)) / s
        self.header_bottom = px(y + 6)

        def draw_title(d, oy):
            for sx, text, reading, tw, rw, seg_w in segments:
                if reading:
                    d.text((sx + (seg_w - rw) / 2, oy + px(title_y)), reading, font=small, fill=MUTED)
                d.text((sx + (seg_w - tw) / 2, oy + px(title_y) + ruby_h), text, font=big, fill=TEXT)
            self._speaker_icon(d, px(WIDTH - PAD - 18), oy + px(title_y) + ruby_h + px(10))
        ops.append(draw_title)

        y += 8
        sep1 = y

        # 原型 / 读音 + 音高
        y += 12
        label = "原型" if w.surface != w.base else "读音"
        base_y = y
        morae = split_mora(w.base_reading)
        y += 34

        def draw_base(d, oy):
            d.line([(px(PAD), oy + px(sep1)), (px(WIDTH - PAD), oy + px(sep1))], fill=BORDER, width=max(1, px(1)))
            cx = self._pill(d, px(PAD), oy + px(base_y + 3), label, ACCENT, (255, 255, 255, 255))
            cx += px(10)
            base_font = jp(px(20), bold=True)
            if w.base != w.base_reading:
                d.text((cx, oy + px(base_y)), w.base, font=base_font, fill=TEXT)
                cx += text_width(base_font, w.base) + px(12)
            cx = self._pitch(d, cx, oy + px(base_y), morae, w.accents[0] if w.accents else None)
            for a in w.accents:
                cx = self._badge(d, cx + px(6), oy + px(base_y + 5), str(a))
        ops.append(draw_base)

        # 词性
        labels = pos_labels(w.pos)
        if labels:
            chips_y = y

            def draw_chips(d, oy):
                cx = px(PAD)
                for lab in labels:
                    cx = self._pill(d, cx, oy + px(chips_y), lab, CHIP_BG, CHIP_TEXT) + px(6)
            ops.append(draw_chips)
            y += 30

        # 释义（AI 选中的义项高亮）
        from jp_ai import chosen_sense, parse  # 延迟导入：jp_ai 依赖 AI 服务的库，单独画卡片时不必加载
        ai_fields = parse(self.ai["text"]) if self.ai["state"] in ("streaming", "done") else {}
        chosen = chosen_sense(ai_fields) if self.ai["state"] == "done" else None
        gloss_font = en(px(14))
        num_font = en(px(14))
        label_font = zh(px(12))
        lines = []  # (显示的编号或 None, 义项编号, 文字, 语体标记)
        shown = list(range(1, min(len(w.senses), MAX_SENSES) + 1))
        if chosen and MAX_SENSES < chosen <= len(w.senses):
            shown.append(chosen)
        for n in shown:
            _, glosses, misc = w.senses[n - 1]
            reg = "·".join(register_labels(misc))
            reg = f"〔{reg}〕" if reg else ""
            reg_w = text_width(label_font, reg)
            for k, line in enumerate(wrap(gloss_font, "; ".join(glosses), px(WIDTH - PAD * 2 - 22) - reg_w)):
                lines.append((n if k == 0 else None, n, line, reg if k == 0 else ""))
        more = len(w.senses) - len(shown)
        if more > 0:
            lines.append((None, None, f"…… 还有 {more} 条英文释义", ""))
        if not w.senses:
            lines.append((None, None, "（中文释义见下方）" if w.zh else "（词典里没有这个词）", ""))
        gloss_y = y + 2

        def draw_glosses(d, oy):
            ly = oy + px(gloss_y)
            for n, sense, line, reg in lines:
                if chosen and sense == chosen:
                    d.rectangle([px(PAD - 6), ly - px(1), px(WIDTH - PAD + 6), ly + px(20)], fill=HIGHLIGHT)
                if n:
                    d.text((px(PAD), ly), f"{n}.", font=num_font, fill=FAINT)
                x0 = px(PAD + 22)
                if reg:
                    d.text((x0, ly + px(2)), reg, font=label_font, fill=REGISTER)
                    x0 += text_width(label_font, reg)
                if sense is None:  # 提示行
                    d.text((x0 if w.senses else px(PAD), ly + px(1)), line, font=zh(px(13)), fill=FAINT)
                    ly += px(21)
                    continue
                d.text((x0, ly), line, font=gloss_font, fill=TEXT)
                # 记下每个英文单词的位置和所在义项，点了打开它的释义页
                for m in re.finditer(r"[A-Za-z][A-Za-z'-]*[A-Za-z]|[A-Za-z]", line):
                    left = x0 + gloss_font.getlength(line[:m.start()])
                    self.links.append((left, ly, left + gloss_font.getlength(m.group()), ly + px(20),
                                       (m.group(), sense)))
                ly += px(21)
        ops.append(draw_glosses)
        y += 2 + 21 * len(lines) + 8

        # 中文释义（中文维基词典）
        if w.zh:
            y = self._layout_zh(ops, y)

        # AI 区
        y = self._layout_ai(ops, y, ai_fields)

        # 提示
        hint_y = y

        def draw_hint(d, oy):
            d.text((px(PAD), oy + px(hint_y)), "点标题读原文 · 点英文单词查它的释义 · Esc 或移开鼠标关闭",
                   font=zh(px(11)), fill=FAINT)
        ops.append(draw_hint)
        y += 18 + PAD

        # 画：阴影 → 卡片 → 内容
        m = self.margin
        card_w, card_h = px(WIDTH), px(y)
        img = Image.new("RGBA", (card_w + 2 * m, card_h + 2 * m), (0, 0, 0, 0))
        shadow = Image.new("L", img.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle([m, m + px(4), m + card_w, m + card_h + px(4)],
                                                 radius=px(RADIUS), fill=70)
        shadow = shadow.filter(ImageFilter.GaussianBlur(px(8)))
        img.paste((15, 23, 42, 255), (0, 0), shadow)
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=px(RADIUS), fill=BG, outline=BORDER,
                             width=max(1, px(1)))
        for op in ops:
            op(cd, 0)
        img.alpha_composite(card, (m, m))
        self.header_bottom += m
        if self.ai_region:
            self.ai_region = (self.ai_region[0] + m, self.ai_region[1] + m)
        self.links = [(l + m, t + m, r + m, b + m, word) for l, t, r, b, word in self.links]
        self.image = img
        return img

    def _layout_zh(self, ops, y):
        px = self.px
        body = zh(px(13))
        rows = []  # (是否某条的第一行, 语体标记, 文字)
        for gloss, tags in self.word.zh:
            label = "·".join(register_labels(tags))
            label = f"〔{label}〕" if label else ""
            for k, line in enumerate(wrap_chars(body, label + gloss, px(WIDTH - PAD * 2 - 24 - 54))):
                rows.append((k == 0, label if k == 0 else "", line[len(label):] if k == 0 else line))
        if len(rows) > MAX_ZH_LINES:
            rows = rows[:MAX_ZH_LINES - 1] + [(True, "", "……")]
        top = y
        height = 12 + 21 * len(rows)

        def draw_zh(d, oy):
            d.rounded_rectangle([px(PAD), oy + px(top), px(WIDTH - PAD), oy + px(top + height)], radius=px(10),
                                fill=ZH_BG)
            self._pill(d, px(PAD + 10), oy + px(top + 6), "中文", ZH_ACCENT, (255, 255, 255, 255))
            ly = oy + px(top + 6)
            for first, label, line in rows:
                x = px(PAD + 12 + 54)
                if first and line != "……":
                    d.text((x - px(10), ly), "·", font=body, fill=FAINT)
                if label:
                    d.text((x, ly + px(1)), label, font=zh(px(12)), fill=REGISTER)
                    x += text_width(zh(px(12)), label)
                d.text((x, ly), line, font=body, fill=TEXT)
                ly += px(21)
        ops.append(draw_zh)
        return top + height + 12

    def _layout_ai(self, ops, y, fields, names=("释义", "读音", "义项", "拆解", "翻译"),
                   button="结合上下文解释（中文）"):
        """排版 AI 区，返回下一块的 y（未乘 scale）。names 是要显示的字段，button 是还没请求时按钮上的字。"""
        px = self.px
        state, text = self.ai["state"], self.ai["text"]
        if state == "off":
            return y
        inner = WIDTH - PAD * 2 - 24
        body = zh(px(13))
        top = y
        if state in (None, "error"):
            # 还没请求 / 出错：一个可点的按钮（出错时点了重试）。还没请求时 text 可以替换按钮文字
            lines = wrap_chars(body, text, px(inner)) if state == "error" else []
            height = 34 + 19 * len(lines)

            def draw_button(d, oy):
                d.rounded_rectangle([px(PAD), oy + px(top), px(WIDTH - PAD), oy + px(top + height)], radius=px(10),
                                    fill=AI_BG)
                cx = self._pill(d, px(PAD + 10), oy + px(top + 7), "AI", AI_ACCENT, (255, 255, 255, 255))
                label = "点击重试" if state == "error" else (text or button)
                d.text((cx + px(8), oy + px(top + 7)), label, font=zh(px(13), bold=True), fill=AI_ACCENT)
                ly = oy + px(top + 31)
                for line in lines:
                    d.text((px(PAD + 12), ly), line, font=body, fill=ERROR)
                    ly += px(19)
            ops.append(draw_button)
            self.ai_region = (px(top), px(top + height))
            return top + height + 12

        # 加载中 / 生成中 / 完成
        rows = []  # (标签或 None, 行文字, 颜色)；标签为 "" 表示整行从左边开始写，不缩进
        if fields:
            # 拆解由本地分词结果生成（确定且准确），不让 AI 猜
            if "拆解" in names:
                from jp_dict import breakdown
                fields = dict(fields, 拆解=breakdown(self.word))
            for name in names:
                value, label, color = fields.get(name), name, TEXT
                if not value:
                    continue
                if name == "读音":
                    # 只在 AI 认为这里的读法和词典标的不一样时提醒（如「今日」读 こんにち）
                    if state != "done":
                        continue
                    local = to_hiragana("".join(r or t for t, r in self.word.ruby))
                    ai_reading = to_hiragana(re.sub(r"[\s（(].*$", "", value.strip()))
                    if not ai_reading or ai_reading == local or not re.fullmatch(r"[\u3041-\u309fー]+", ai_reading):
                        continue
                    value, color = f"这里读「{ai_reading}」（上面标的是「{local}」）", ERROR
                if name == "义项":
                    # 写成一句话：对应上面第几条英文释义（那一条同时被高亮）
                    if not self.word.senses:
                        continue
                    from jp_ai import chosen_sense
                    n = chosen_sense({"义项": value})
                    if n and n <= len(self.word.senses):
                        value = f"对应上面第 {n} 条英文释义"
                    elif "无" in value or n or state == "done":
                        value = "词典释义都不贴切"
                    else:
                        continue  # 还在输出中，等数字出来
                    label, color = "", AI_ACCENT
                width = inner if label == "" else inner - 40
                for k, line in enumerate(wrap_chars(body, value, px(width))):
                    rows.append((label if k == 0 else (None if label else ""), line, color))
        elif text.strip():
            rows = [(None, line, TEXT) for line in wrap_chars(body, text.strip().replace("\n", " "), px(inner))]
        if state != "done":
            rows.append((None, "AI 正在思考…" if state == "thinking" else "AI 正在分析…", FAINT))
        height = 14 + 21 * len(rows)

        def draw_ai(d, oy):
            d.rounded_rectangle([px(PAD), oy + px(top), px(WIDTH - PAD), oy + px(top + height)], radius=px(10),
                                fill=AI_BG)
            ly = oy + px(top + 7)
            for label, line, color in rows:
                x = px(PAD + 12)
                if label:
                    self._pill(d, x, ly, label, AI_ACCENT, (255, 255, 255, 255))
                if fields and label != "":
                    x += px(44)
                d.text((x, ly), line, font=body, fill=color)
                ly += px(21)
        ops.append(draw_ai)
        return top + height + 12

    def _pill(self, d, x, y, text, bg, fg):
        f = zh(self.px(12))
        w = text_width(f, text) + self.px(14)
        d.rounded_rectangle([x, y, x + w, y + self.px(20)], radius=self.px(10), fill=bg)
        d.text((x + self.px(7), y + self.px(1)), text, font=f, fill=fg)
        return x + w

    def _badge(self, d, x, y, text):
        f = en(self.px(11))
        r = self.px(9)
        d.ellipse([x, y, x + 2 * r, y + 2 * r], outline=PITCH, width=max(1, self.px(1.5)))
        d.text((x + r, y + r), text, font=f, fill=PITCH, anchor="mm")
        return x + 2 * r

    def _pitch(self, d, x, y, morae, accent):
        """假名上方画音高线：高的拍上面有横线，下降处画一条竖线。"""
        f = jp(self.px(18))
        top = y + self.px(1)
        lw = max(1, self.px(2))
        highs = pitch_pattern(len(morae), accent) if accent is not None else [False] * len(morae)
        for i, (mora, high) in enumerate(zip(morae, highs)):
            mw = text_width(f, mora)
            d.text((x, y + self.px(4)), mora, font=f, fill=TEXT)
            if accent is not None:
                if high:
                    d.line([(x, top), (x + mw, top)], fill=PITCH, width=lw)
                    nxt = highs[i + 1] if i + 1 < len(highs) else accent == 0
                    if not nxt:  # 下降核
                        d.line([(x + mw, top), (x + mw, top + self.px(9))], fill=PITCH, width=lw)
                elif i > 0 and highs[i - 1] is False and False:
                    pass
            x += mw
        return x

    def _finish(self, ops, height):
        """画阴影、卡片底和内容（height 未乘 scale），返回图片。"""
        px, m = self.px, self.margin
        card_w, card_h = px(WIDTH), px(height)
        img = Image.new("RGBA", (card_w + 2 * m, card_h + 2 * m), (0, 0, 0, 0))
        shadow = Image.new("L", img.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle([m, m + px(4), m + card_w, m + card_h + px(4)],
                                                 radius=px(RADIUS), fill=70)
        shadow = shadow.filter(ImageFilter.GaussianBlur(px(8)))
        img.paste((15, 23, 42, 255), (0, 0), shadow)
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=px(RADIUS), fill=BG, outline=BORDER,
                             width=max(1, px(1)))
        for op in ops:
            op(cd, 0)
        img.alpha_composite(card, (m, m))
        return img

    def _speaker_icon(self, d, x, y):
        p = self.px
        d.rectangle([x, y + p(5), x + p(4), y + p(11)], fill=ACCENT)
        d.polygon([(x + p(4), y + p(5)), (x + p(9), y + p(1)), (x + p(9), y + p(15)), (x + p(4), y + p(11))], fill=ACCENT)
        for r in (5, 9):
            d.arc([x + p(9) - p(r), y + p(8) - p(r), x + p(9) + p(r), y + p(8) + p(r)], -50, 50, fill=ACCENT,
                  width=max(1, p(1.5)))


def en_bold(size):
    return font("segoeuib.ttf", size)


class EnglishCard(Card):
    """英文卡片：点日语卡片里的英文单词后弹出，或按住 Shift 直接停在英文单词上（in_context）。
    entry 是 jp_dict.EnglishWord，查不到时为 None。header_bottom 之上点击读这个英文单词。"""

    def __init__(self, entry, clicked_word, scale=1.0, ai=None, dictionary_ready=True, in_context=False):
        super().__init__(entry, scale, ai or {"state": "off", "text": ""})
        self.word_text = entry.word if entry else clicked_word
        self.dictionary_ready = dictionary_ready
        self.in_context = in_context  # AI 结合原文句子解释，而不是结合日语释义

    def render(self):
        px, e = self.px, self.word
        ops = []
        y = PAD

        title_font = en_bold(px(26))
        phonetic = f"/{e.phonetic}/" if e and e.phonetic else ""
        title_y = y

        def draw_title(d, oy):
            d.text((px(PAD), oy + px(title_y)), self.word_text, font=title_font, fill=TEXT)
            if phonetic:
                x = px(PAD) + text_width(title_font, self.word_text) + px(10)
                d.text((x, oy + px(title_y + 9)), phonetic, font=en(px(15)), fill=MUTED)
            self._speaker_icon(d, px(WIDTH - PAD - 18), oy + px(title_y + 10))
        ops.append(draw_title)
        y += 40
        self.header_bottom = px(y)

        if e and e.via:
            via_y = y
            via_lines = wrap_chars(zh(px(12)), e.via, px(WIDTH - PAD * 2))

            def draw_via(d, oy):
                for k, line in enumerate(via_lines):
                    d.text((px(PAD), oy + px(via_y + 18 * k)), line, font=zh(px(12)), fill=MUTED)
            ops.append(draw_via)
            y += 18 * len(via_lines) + 2

        # 英美差异（以英式为准）：美式拼写 color、英式说 lift……
        uk_lines = [line for note in (e.uk_notes if e else [])
                    for line in wrap_chars(zh(px(12)), note, px(WIDTH - PAD * 2))]
        if uk_lines:
            uk_y = y

            def draw_uk(d, oy):
                for k, line in enumerate(uk_lines):
                    d.text((px(PAD), oy + px(uk_y + 18 * k)), line, font=zh(px(12)), fill=ZH_ACCENT)
            ops.append(draw_uk)
            y += 18 * len(uk_lines) + 2

        y += 6
        sep_y = y
        ops.append(lambda d, oy: d.line([(px(PAD), oy + px(sep_y)), (px(WIDTH - PAD), oy + px(sep_y))],
                                        fill=BORDER, width=max(1, px(1))))
        y += 10

        # 中文释义：词性 + 意思，每个词性一行（长的折行）
        body = zh(px(14))
        rows = []  # (词性或 None, 文字)
        for line in (e.lines if e else []):
            m = re.match(r"^([a-z]+\.|\[[^\]]+\])\s*(.*)$", line)
            pos, meaning = (m.group(1), m.group(2)) if m else ("", line)
            for k, part in enumerate(wrap_chars(body, meaning, px(WIDTH - PAD * 2 - 48))):
                rows.append((pos if k == 0 else None, part))
        if not rows:
            rows.append((None, "离线词典里没有这个词。" if self.dictionary_ready else "英汉词典还在下载，稍后再试。"))
        rows_y = y

        def draw_rows(d, oy):
            ly = oy + px(rows_y)
            for pos, text in rows:
                if pos:  # 用中文字体：领域标记里有汉字，如 [计]
                    d.text((px(PAD), ly + px(1)), pos, font=zh(px(13)), fill=FAINT)
                d.text((px(PAD + 48) if e else px(PAD), ly), text, font=body, fill=TEXT)
                ly += px(23)
        ops.append(draw_rows)
        y += 23 * len(rows) + 6

        if e and e.tags:
            tags_y = y

            def draw_tags(d, oy):
                cx = px(PAD)
                for tag in e.tags:
                    cx = self._pill(d, cx, oy + px(tags_y), tag, CHIP_BG, CHIP_TEXT) + px(6)
            ops.append(draw_tags)
            y += 28

        if e and e.forms:
            small = zh(px(12))
            form_lines = [""]  # 按整项折行，不把“三单 eats”拆到两行
            for item in (f"{label} {form}" for label, form in e.forms):
                candidate = f"{form_lines[-1]} · {item}" if form_lines[-1] else item
                if form_lines[-1] and text_width(small, candidate) > px(WIDTH - PAD * 2):
                    form_lines.append(item)
                else:
                    form_lines[-1] = candidate
            forms_y = y

            def draw_forms(d, oy):
                ly = oy + px(forms_y)
                for line in form_lines:
                    d.text((px(PAD), ly), line, font=small, fill=MUTED)
                    ly += px(18)
            ops.append(draw_forms)
            y += 18 * len(form_lines) + 6

        # AI：这个英文单词在原文句子里的意思；从日语卡片点进来时是在那条日语释义里的意思（手动点了才请求）
        from jp_ai import parse
        fields = parse(self.ai["text"]) if self.ai["state"] in ("streaming", "done") else {}
        if self.in_context:
            y = self._layout_ai(ops, y + 4, fields, ("意思", "翻译"))
        else:
            y = self._layout_ai(ops, y + 4, fields, ("意思", "整条"), "它在这条释义里是什么意思")

        hint_y = y
        ops.append(lambda d, oy: d.text((px(PAD), oy + px(hint_y)), "点单词读发音 · Esc 或移开鼠标关闭",
                                        font=zh(px(11)), fill=FAINT))
        y = hint_y + 18 + PAD

        img = self._finish(ops, y)
        self.header_bottom += self.margin
        if self.ai_region:
            self.ai_region = (self.ai_region[0] + self.margin, self.ai_region[1] + self.margin)
        self.image = img
        return img
