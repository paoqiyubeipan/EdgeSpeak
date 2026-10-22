"""把电脑版生成好的词典整理成安卓版的 assets：

  python make_assets.py <电脑版的 dict 文件夹> <Sudachi system_core.dic>

输出到 app/src/main/assets/dict/：
  jmdict.sqlite   JMdict 日英 + Kanjium 音高（原样复制）
  wikt.sqlite     维基词典英文版 + 中文版的日语词条（原样复制）
  ecdict.sqlite   ECDICT 英汉：只留日语释义里会出现的英文单词（完整版 300 多 MB，手机上用不着）
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
    rows = [r for r in db.execute("SELECT key, word, phonetic, translation, tag, exchange FROM en") if r[0] in keep]
    out.executemany("INSERT INTO en VALUES (?, ?, ?, ?, ?, ?)", rows)
    kept = {r[0] for r in rows}
    out.executemany("INSERT INTO en_forms VALUES (?, ?)",
                    [(f, l) for f, l in db.execute("SELECT form, lemma FROM en_forms") if f in vocab and l in kept])
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
