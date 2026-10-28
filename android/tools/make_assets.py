"""把电脑版生成好的词典整理成安卓版的 assets：

  python make_assets.py <电脑版的 dict 文件夹> <Sudachi system_core.dic>

输出到 app/src/main/assets/dict/：
  jmdict.sqlite   JMdict 日英 + Kanjium 音高（原样复制）
  wikt.sqlite     维基词典英文版 + 中文版的日语词条（原样复制）
  ecdict.sqlite   ECDICT 英汉（完整版 300 多 MB，手机上用不着全部）：留下日语释义里会出现的英文单词和它们的原词，
                  再加上普通英文单词（全小写字母、可带撇号和连字符，且有考试标签、变形或音标的，约 47 万条）
                  和带撇号的词（don't、o'clock），供英文取词用。合计约 53 万条、64 MB
  system_core.dic Sudachi 分词词典
  manifest.json   每个文件的大小，首次启动时解压用来显示进度
"""
import json
import os
import re
import shutil
import sqlite3
import sys

WORD = re.compile(r"[A-Za-z][A-Za-z'-]*[A-Za-z]|[A-Za-z]")
ENGLISH_KEY = re.compile(r"^[a-z][a-z'\-]*$")  # 普通英文单词（不要词组、专有名词、带数字的）


def newest(folder, prefix):
    manifest = json.load(open(os.path.join(folder, "manifest.json"), encoding="utf-8"))
    return os.path.join(folder, manifest[prefix]["path"])


def english_vocabulary(jmdict, wikt):
    vocab = set()
    for (data,) in sqlite3.connect(jmdict).execute("SELECT data FROM entries"):
        for sense in json.loads(data)["s"]:
            for gloss in sense[1]:
                vocab.update(w.lower() for w in WORD.findall(gloss))
    for (senses,) in sqlite3.connect(wikt).execute("SELECT senses FROM wikt WHERE lang = 'en'"):
        for gloss, _ in json.loads(senses):
            vocab.update(w.lower() for w in WORD.findall(gloss))
    return vocab


def build_ecdict(src, dst, vocab):
    if os.path.exists(dst):
        os.remove(dst)
    out = sqlite3.connect(dst)
    out.executescript("""
        CREATE TABLE en (key TEXT PRIMARY KEY, word TEXT, phonetic TEXT, translation TEXT, tag TEXT, exchange TEXT);
        CREATE TABLE en_forms (form TEXT PRIMARY KEY, lemma TEXT);
    """)
    db = sqlite3.connect(src)
    # 释义里出现的可能是变形（eating），把它的原词（eat）也留下
    lemmas = {lemma for form, lemma in db.execute("SELECT form, lemma FROM en_forms") if form in vocab}
    keep = vocab | lemmas

    def wanted(key, phonetic, tag, exchange):
        """英文取词用：普通英文单词里有考试标签、变形或音标的（没有这些的多半是生僻词、缩写），以及带撇号的词。"""
        return bool(ENGLISH_KEY.match(key) and (tag or exchange or phonetic)) or "'" in key

    rows = [r for r in db.execute("SELECT key, word, phonetic, translation, tag, exchange FROM en")
            if r[0] and (r[0] in keep or wanted(r[0], r[2], r[4], r[5]))]
    out.executemany("INSERT INTO en VALUES (?, ?, ?, ?, ?, ?)", rows)
    kept = {r[0] for r in rows}
    # 变形 → 原词：变形本身是要留的词（释义里出现的，或像普通英文单词的），原词也留下了
    out.executemany("INSERT INTO en_forms VALUES (?, ?)",
                    [(f, l) for f, l in db.execute("SELECT form, lemma FROM en_forms")
                     if (f in vocab or ENGLISH_KEY.match(f) or "'" in f) and l in kept])
    out.commit()
    out.execute("VACUUM")
    out.close()
    return len(rows)


def main(dict_dir, sudachi_dic):
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "src", "main", "assets", "dict")
    os.makedirs(out, exist_ok=True)
    jmdict, wikt, ecdict = (newest(dict_dir, n) for n in ("jmdict", "wiktionary", "ecdict"))
    shutil.copyfile(jmdict, os.path.join(out, "jmdict.sqlite"))
    shutil.copyfile(wikt, os.path.join(out, "wikt.sqlite"))
    shutil.copyfile(sudachi_dic, os.path.join(out, "system_core.dic"))
    n = build_ecdict(ecdict, os.path.join(out, "ecdict.sqlite"), english_vocabulary(jmdict, wikt))
    files = ["system_core.dic", "jmdict.sqlite", "wikt.sqlite", "ecdict.sqlite"]
    manifest = {"files": {f: os.path.getsize(os.path.join(out, f)) for f in files}}
    json.dump(manifest, open(os.path.join(out, "manifest.json"), "w"), indent=1)
    for f, size in manifest["files"].items():
        print(f"{f:16} {size / 1e6:8.1f} MB")
    print(f"英汉词条 {n}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
