"""英式英语：英文卡片以英式为准。数据在 uk_english.json：
- spelling：美式拼写 → 英式拼写（color → colour）。来自 tysto.com 的英美拼写对照表，
  经 hyperreality/American-British-English-Translator 整理（MIT），去掉了几处错误。
- partial：只对部分义项成立的拼写差异（tire 指轮胎时写 tyre），只加说明，不改标题。
- vocab：常见的英美用词差异（elevator → 英式说 lift）。
"""
import json
import os
import sys

_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # 打包版在 _internal 里
with open(os.path.join(_HERE, "uk_english.json"), encoding="utf-8") as _f:
    _DATA = json.load(_f)

SPELLING = _DATA["spelling"]
PARTIAL = _DATA["partial"]
VOCAB = _DATA["vocab"]
AMERICAN = {uk: us for us, uk in SPELLING.items()}  # 英式拼写 → 美式拼写


def british(word):
    """美式拼写的英式写法，没有差异时原样返回（保留首字母大写）。"""
    uk = SPELLING.get(word.lower())
    if not uk:
        return word
    return uk.capitalize() if word[:1].isupper() else uk


def notes(word):
    """这个词（原形，小写）的英美差异说明：部分义项的拼写差异、用词差异。"""
    return [n for n in (PARTIAL.get(word), VOCAB.get(word)) if n]
