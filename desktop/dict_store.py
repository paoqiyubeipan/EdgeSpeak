"""词典数据：放在哪、怎么生成、怎么自动更新。

四份数据各自检查、各自更新，生成到“词典文件夹”里，manifest.json 记录每份的版本、日期和文件名：
  unidic      分词词典 UniDic 現代書き言葉（国立国语研究所，BSD）       unidic-<版本>\\
  jmdict      JMdict 日英（EDRDG）+ Kanjium 音高，都是 CC BY-SA 4.0     jmdict-<时间>.sqlite
  wiktionary  维基词典里的日语词：英文版 + 中文版（CC BY-SA 4.0）       wikt-<时间>.sqlite
  ecdict      ECDICT 英汉完整版（MIT）                                  ecdict-<时间>.sqlite
程序自带一份（程序文件夹\\dict），自动更新下载到 %LOCALAPPDATA%\\EdgeSpeak\\dict；两边都有时用数据日期更新的那份。
"""
import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.utils import parsedate_to_datetime

log = logging.getLogger("EdgeSpeak")

COMPONENTS = ("jmdict", "ecdict", "wiktionary", "unidic")  # 缺词典时也按这个顺序下载：先下小的，JMdict 一到就能查词
LABELS = {"unidic": "UniDic 分词词典", "jmdict": "JMdict 日英词典", "wiktionary": "维基词典", "ecdict": "ECDICT 英汉词典"}

UNIDIC_INDEX = "https://clrd.ninjal.ac.jp/unidic/back_number.html"
JMDICT_RELEASES = "https://api.github.com/repos/scriptin/jmdict-simplified/releases/latest"
KANJIUM_PATH = "data/source_files/raw/accents.txt"
KANJIUM_COMMITS = f"https://api.github.com/repos/mifunetoshiro/kanjium/commits?path={KANJIUM_PATH}&per_page=1"
KANJIUM_RAW = "https://raw.githubusercontent.com/mifunetoshiro/kanjium/{sha}/" + KANJIUM_PATH
WIKT_EN = "https://kaikki.org/dictionary/Japanese/kaikki.org-dictionary-Japanese.jsonl"
WIKT_EN_ALL = "https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz"  # 上面的单语言文件下线后改用全量数据，只取日语
WIKT_ZH = "https://kaikki.org/dictionary/downloads/zh/zh-extract.jsonl.gz"
ECDICT_RELEASES = "https://api.github.com/repos/skywind3000/ECDICT/releases/latest"

USER_AGENT = "EdgeSpeak-dictionary-updater"
CHUNK = 1 << 20


def app_dir():
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


BUNDLED_ROOT = os.path.join(app_dir(), "dict")
USER_ROOT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "EdgeSpeak", "dict")

generation = 0  # 每装好一份新数据加一；查词线程看到变化就重新打开词典
_install_lock = threading.Lock()


# ---------------------------------------------------------------- 当前用哪份

def read_manifest(root):
    try:
        with open(os.path.join(root, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_manifest(root, manifest):
    tmp = os.path.join(root, "manifest.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(root, "manifest.json"))


def current():
    """{名字: {"version", "date", "path"(绝对路径)}}。两边都有时取数据日期新的。"""
    result = {}
    for root in (BUNDLED_ROOT, USER_ROOT):
        for name, entry in read_manifest(root).items():
            path = os.path.join(root, entry["path"])
            if os.path.exists(path) and (name not in result or entry["date"] >= result[name]["date"]):
                result[name] = dict(entry, path=path)
    return result


def path_of(name):
    entry = current().get(name)
    return entry["path"] if entry else None


class Missing(Exception):
    """词典还没下载（精简版安装包第一次运行时）。"""


def require(name):
    path = path_of(name)
    if not path:
        raise Missing(f"{LABELS[name]}还没下载")
    return path


def missing():
    have = current()
    return [name for name in COMPONENTS if name not in have]


# ---------------------------------------------------------------- 网络

def _open(url, method="GET"):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=60)


def _json(url):
    with _open(url) as r:
        return json.load(r)


def _last_modified(url):
    with _open(url, "HEAD") as r:
        return _iso(parsedate_to_datetime(r.headers["Last-Modified"]).timestamp())


def _iso(timestamp):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _download(url, dst, cancel=None, progress=None):
    """progress(已下载字节, 总字节) 每下载约 1% 调用一次。"""
    with _open(url) as r, open(dst, "wb") as f:
        total, done, reported, shown = int(r.headers.get("Content-Length") or 0), 0, 0, 0
        while chunk := r.read(CHUNK):
            if cancel and cancel.is_set():
                raise InterruptedError("已取消")
            f.write(chunk)
            done += len(chunk)
            if done - reported >= 100 * CHUNK:
                reported = done
                log.info("  下载中 %s：%d / %d MB", os.path.basename(dst), done >> 20, total >> 20)
            if progress and (not total or done - shown >= total // 100):
                shown = done
                progress(done, total)
    return dst


# ---------------------------------------------------------------- 上游的最新版本

def latest_unidic():
    with _open(UNIDIC_INDEX) as r:
        html = r.read().decode("utf-8", "replace")
    found = re.findall(r'href="([^"]*?unidic-cwj-(\d{6})\.zip)"', html)  # 不带 _full 的轻量版就够 MeCab 用
    href, version = max(found, key=lambda m: m[1])
    return {"version": version, "date": f"{version[:4]}-{version[4:]}-01T00:00:00Z",
            "urls": [urllib.parse.urljoin(UNIDIC_INDEX, href)]}


def latest_jmdict():
    release = _json(JMDICT_RELEASES)
    asset = next(a for a in release["assets"] if re.fullmatch(r"jmdict-eng-[\d.]+\+\d+\.json\.zip", a["name"]))
    commit = _json(KANJIUM_COMMITS)[0]
    return {"version": f"{release['tag_name']} / kanjium {commit['sha'][:7]}",
            "date": max(release["published_at"], commit["commit"]["committer"]["date"]),
            "urls": [asset["browser_download_url"], KANJIUM_RAW.format(sha=commit["sha"])]}


def latest_wiktionary():
    try:
        en_url, en_date = WIKT_EN, _last_modified(WIKT_EN)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        en_url, en_date = WIKT_EN_ALL, _last_modified(WIKT_EN_ALL)
    zh_date = _last_modified(WIKT_ZH)
    return {"version": f"en {en_date[:10]} / zh {zh_date[:10]}", "date": max(en_date, zh_date),
            "urls": [en_url, WIKT_ZH]}


def latest_ecdict():
    release = _json(ECDICT_RELEASES)
    asset = next(a for a in release["assets"] if re.fullmatch(r"ecdict-sqlite-\d+\.zip", a["name"]))
    return {"version": release["tag_name"], "date": release["published_at"], "urls": [asset["browser_download_url"]]}


LATEST = {"unidic": latest_unidic, "jmdict": latest_jmdict, "wiktionary": latest_wiktionary, "ecdict": latest_ecdict}


# ---------------------------------------------------------------- 生成

def build_unidic(sources, out_dir):
    with zipfile.ZipFile(sources[0]) as z:
        z.extractall(out_dir)
    open(os.path.join(out_dir, "mecabrc"), "w").close()  # MeCab 需要一个配置文件，内容可以为空


def _restriction(values):
    return None if not values or values == ["*"] else values


def build_jmdict(sources, out_path):
    jmdict_zip, accents_txt = sources
    with zipfile.ZipFile(jmdict_zip) as z:
        name = next(n for n in z.namelist() if n.endswith(".json"))
        words = json.loads(z.read(name))["words"]
    db = sqlite3.connect(out_path)
    db.executescript("""
        CREATE TABLE entries (id INTEGER PRIMARY KEY, common INTEGER, data TEXT);
        CREATE TABLE forms (form TEXT, id INTEGER);
        CREATE TABLE accents (word TEXT, reading TEXT, accent TEXT);
    """)
    for w in words:
        kanji = [k["text"] for k in w["kanji"]]
        kana = [[r["text"], _restriction(r.get("appliesToKanji"))] for r in w["kana"]]
        senses, pos = [], []
        for s in w["sense"]:
            pos = s["partOfSpeech"] or pos  # 空的沿用上一个义项
            glosses = [g["text"] for g in s["gloss"] if g.get("lang", "eng") == "eng"]
            if glosses:
                senses.append([pos, glosses, s.get("misc", []),
                               _restriction(s.get("appliesToKanji")), _restriction(s.get("appliesToKana"))])
        if not senses:
            continue
        common = any(k.get("common") for k in w["kanji"]) or any(r.get("common") for r in w["kana"])
        data = {"k": kanji, "r": kana, "s": senses}
        db.execute("INSERT INTO entries VALUES (?, ?, ?)",
                   (int(w["id"]), int(common), json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
        db.executemany("INSERT INTO forms VALUES (?, ?)", [(f, int(w["id"])) for f in set(kanji) | {r[0] for r in kana}])
    with open(accents_txt, encoding="utf-8") as f:
        rows = []
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                word, reading, accent = parts
                rows.append((word, reading or word, accent))
    db.executemany("INSERT INTO accents VALUES (?, ?, ?)", rows)
    db.executescript("CREATE INDEX forms_form ON forms(form); CREATE INDEX accents_word ON accents(word); VACUUM;")
    db.close()


SKIP_POS = {"character", "romanization", "soft-redirect", "syllable"}
KANA = re.compile(r"^[ぁ-ゟ゠-ヿー]+$")


def _reading(entry):
    """维基词典词条的读音（平假名或片假名），拿不到时为空串。"""
    word = entry["word"]
    if KANA.match(word):
        return word
    for form in entry.get("forms", []):
        if "canonical" in form.get("tags", []) and form.get("ruby"):
            # ruby 只标了汉字部分：把原词里的汉字段按顺序换成读音
            reading, rest = "", form["form"].split(" ")[0]
            for base, ruby in form["ruby"]:
                k = rest.find(base)
                if k < 0:
                    break
                reading, rest = reading + rest[:k] + ruby, rest[k + len(base):]
            else:
                reading += rest
                if KANA.match(reading):
                    return reading
    for sound in entry.get("sounds", []):
        other = sound.get("other", "")
        if KANA.match(other) and "ー" not in other:  # 发音写法里长音写成ー，和词典读音对不上
            return other
    return ""


def _wikt_rows(lines, lang, to_simplified):
    for line in lines:
        if '"lang_code": "ja"' not in line:  # 先粗筛，省得每行都解析 JSON
            continue
        entry = json.loads(line)
        if entry.get("lang_code") != "ja" or entry.get("pos") in SKIP_POS:
            continue
        word, reading, senses = entry["word"], _reading(entry), []
        for sense in entry.get("senses", []):
            glosses = sense.get("glosses")
            if not glosses or "no-gloss" in sense.get("tags", []):
                continue
            text = glosses[-1].strip()  # 子义项的 glosses 前面带着上级义项
            m = re.match(r"^(.+?)【(.+?)】\s*\n?(.*)$", text, re.S)  # 中文版批量导入的词条：発注【はっちゅう】\n订购……
            if m and m.group(1) == word:
                reading = reading or m.group(2).split("・")[0]
                text = m.group(3)
            text = re.sub(r"\s+", " ", text).strip()
            if lang == "zh":
                text = to_simplified(text)
            if text and not re.fullmatch(r"同上[。.]?", text):  # “同上”指的是网页上相邻的词条，单独看没有意义
                senses.append([text[:400], sense.get("tags", [])])
        if senses:
            yield lang, word, reading, entry.get("pos", ""), json.dumps(senses, ensure_ascii=False, separators=(",", ":"))


def build_wiktionary(sources, out_path):
    en_src, zh_src = sources
    import opencc
    to_simplified = opencc.OpenCC("t2s").convert
    db = sqlite3.connect(out_path)
    db.executescript("CREATE TABLE wikt (lang TEXT, word TEXT, reading TEXT, pos TEXT, senses TEXT);")
    for lang, src in (("en", en_src), ("zh", zh_src)):
        opener = gzip.open if src.endswith(".gz") else open
        with opener(src, "rt", encoding="utf-8") as f:
            db.executemany("INSERT INTO wikt VALUES (?, ?, ?, ?, ?)", _wikt_rows(f, lang, to_simplified))
    db.executescript("CREATE INDEX wikt_word ON wikt(word); VACUUM;")
    log.info("维基词典：%s", dict(db.execute("SELECT lang, count(*) FROM wikt GROUP BY lang").fetchall()))
    db.close()


def build_ecdict(sources, out_path):
    """ECDICT 完整版（约 340 万条）转成查词用的两张表；再从词形变化生成“变形 → 原词”表，点 eating 也能查到 eat。"""
    tmp = os.path.dirname(out_path)
    with zipfile.ZipFile(sources[0]) as z:
        name = next(n for n in z.namelist() if n.endswith(".db"))
        src_path = z.extract(name, tmp)
    src = sqlite3.connect(src_path)
    db = sqlite3.connect(out_path)
    db.executescript("""
        CREATE TABLE en (key TEXT PRIMARY KEY, word TEXT, phonetic TEXT, translation TEXT, tag TEXT, exchange TEXT);
        CREATE TABLE en_forms (form TEXT PRIMARY KEY, lemma TEXT);
    """)
    keys, forms = set(), {}

    def rows():
        for word, phonetic, translation, tag, exchange in src.execute(
                "SELECT word, phonetic, translation, tag, exchange FROM stardict "
                "WHERE translation IS NOT NULL AND translation != '' ORDER BY collins DESC, bnc IS NULL, bnc"):
            key = word.strip().lower()
            if key in keys:
                continue
            keys.add(key)
            exchange = exchange or ""
            for part in exchange.split("/"):
                kind, _, form = part.partition(":")
                if kind in "pdi3srt" and form:
                    forms.setdefault(form.lower(), key)
            yield key, word.strip(), phonetic or "", translation.replace("\\n", "\n"), tag or "", exchange

    db.executemany("INSERT INTO en VALUES (?, ?, ?, ?, ?, ?)", rows())
    db.executemany("INSERT INTO en_forms VALUES (?, ?)", [(f, l) for f, l in forms.items() if f not in keys])
    db.commit()
    db.execute("VACUUM")
    db.close()
    src.close()
    os.remove(src_path)


BUILDERS = {"unidic": build_unidic, "jmdict": build_jmdict, "wiktionary": build_wiktionary, "ecdict": build_ecdict}
OUTPUT = {"unidic": "unidic-{version}", "jmdict": "jmdict-{stamp}.sqlite", "wiktionary": "wikt-{stamp}.sqlite",
          "ecdict": "ecdict-{stamp}.sqlite"}


def install(name, info, root, sources=None, cancel=None, on_status=None):
    """下载（或用给定的 sources）并生成一份数据，装进 root，更新 manifest。返回新路径。
    on_status(说明) 报告进度，如“维基词典：下载 35%”。"""
    status = on_status or (lambda text: None)
    os.makedirs(root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tmp-", dir=root) as tmp:
        if sources is None:
            sources, urls = [], info["urls"]
            for k, url in enumerate(urls):
                filename = f"{k}-" + os.path.basename(urllib.parse.urlparse(url).path)
                log.info("下载 %s", url)
                part = f"（{k + 1}/{len(urls)}）" if len(urls) > 1 else ""

                def progress(done, total, part=part):
                    amount = f"{done * 100 // total}%" if total else f"{done >> 20} MB"
                    status(f"{LABELS[name]}：下载{part} {amount}")
                sources.append(_download(url, os.path.join(tmp, filename), cancel, progress))
        status(f"{LABELS[name]}：生成中…")
        stamp = time.strftime("%Y%m%d%H%M%S")
        out_name = OUTPUT[name].format(version=re.sub(r"[^\w.-]", "_", info["version"]), stamp=stamp)
        out_tmp = os.path.join(tmp, "out-" + out_name)
        log.info("生成 %s", out_name)
        if name == "unidic":
            os.makedirs(out_tmp)
        BUILDERS[name](sources, out_tmp)
        final = os.path.join(root, out_name)
        if os.path.exists(final):
            shutil.rmtree(final) if os.path.isdir(final) else os.remove(final)
        os.replace(out_tmp, final)
    with _install_lock:
        manifest = read_manifest(root)
        manifest[name] = {"version": info["version"], "date": info["date"], "path": out_name}
        write_manifest(root, manifest)
    global generation
    generation += 1
    cleanup(root)
    return final


def cleanup(root=USER_ROOT):
    """删掉不再使用的旧版本。正被打开的文件删不掉，下次启动再删。"""
    manifest = read_manifest(root)
    if root == USER_ROOT:
        # 程序自带的数据不比用户文件夹里的旧时（比如装了新版程序），用户文件夹里那份就没用了
        bundled = read_manifest(BUNDLED_ROOT)
        stale = [n for n, e in manifest.items() if n in bundled and bundled[n]["date"] >= e["date"]
                 and os.path.exists(os.path.join(BUNDLED_ROOT, bundled[n]["path"]))]
        if stale:
            for n in stale:
                del manifest[n]
            write_manifest(root, manifest)
    used = {e["path"] for e in manifest.values()}
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        if name in used or not re.match(r"(unidic-|jmdict-|wikt-|ecdict-|tmp-)", name):
            continue
        path = os.path.join(root, name)
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- 检查更新

def check_updates(cancel=None, only=None, on_status=None, on_installed=None):
    """检查每份数据有没有新版本（还没有的就下载），装进用户文件夹。
    返回 (装好的 [(名字, 版本)], 出错的 [(名字, 说明)])。"""
    installed, failed = [], []
    have = current()
    for name in only or COMPONENTS:
        if cancel and cancel.is_set():
            break
        try:
            info = LATEST[name]()
            mine = have.get(name)
            if mine and info["date"] <= mine["date"]:  # 只装比现在用的更新的数据
                continue
            log.info("%s 有新版本：%s → %s", LABELS[name], mine["version"] if mine else "（无）", info["version"])
            install(name, info, USER_ROOT, cancel=cancel, on_status=on_status)
            installed.append((name, info["version"]))
            if on_installed:
                on_installed(name)
        except InterruptedError:
            break
        except Exception as e:
            log.warning("%s 更新失败：%r", LABELS[name], e)
            failed.append((name, f"{e.__class__.__name__}: {e}"))
    return installed, failed


class Updater:
    """后台定时检查词典更新：启动后等一会儿查一次，之后每天一次。check_now() 立即查（托盘菜单用）。
    缺词典（精简版安装包第一次运行）时立即下载，不管自动更新开没开；失败了过一会儿再试。"""

    FIRST_DELAY = 120
    INTERVAL = 24 * 3600
    RETRY = 600  # 缺词典且下载失败时，多久再试一次

    def __init__(self, is_enabled, on_done, on_status=None, on_installed=None):
        self.is_enabled = is_enabled
        self.on_done = on_done  # on_done(装好的, 出错的, 是否手动, 是否还缺词典)
        self.on_status = on_status or (lambda text: None)  # 进度说明；空串表示结束
        self.on_installed = on_installed  # on_installed(名字)：每装好一份调用一次
        self.wake = threading.Event()
        self.manual = False
        self.cancel = threading.Event()
        self.busy = False
        self.status = ""
        cleanup()
        threading.Thread(target=self._loop, daemon=True).start()

    def _set_status(self, text):
        if text != self.status:
            self.status = text
            self.on_status(text)

    def check_now(self):
        if self.busy:
            return False
        self.manual = True
        self.wake.set()
        return True

    def stop(self):
        self.cancel.set()
        self.wake.set()

    def _loop(self):
        if not missing():
            self.wake.wait(self.FIRST_DELAY)
        while not self.cancel.is_set():
            manual, self.manual = self.manual, False
            self.wake.clear()
            lacking = missing()
            if manual or lacking or self.is_enabled():
                self.busy = True
                self._set_status("正在下载词典…" if lacking else "正在检查词典更新…")
                try:
                    # 缺词典又没开自动更新时只下缺的，已有的不动
                    only = None if manual or self.is_enabled() else lacking
                    installed, failed = check_updates(self.cancel, only, self._set_status, self.on_installed)
                finally:
                    self.busy = False
                    self._set_status("")
                if not self.cancel.is_set():
                    self.on_done(installed, failed, manual, bool(missing()))
            self.wake.wait(self.RETRY if missing() else self.INTERVAL)
