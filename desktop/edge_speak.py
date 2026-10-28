"""EdgeSpeak：选中文字后按快捷键，用微软 Edge TTS 朗读；Edge 不可用时自动改用系统语音。"""
import asyncio
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import queue
import re
import ssl
import sys
import threading
import time
import winreg
from array import array
from collections import OrderedDict

# edge_tts 导入时会用 certifi 的证书包建两次 SSL 上下文，OpenSSL 3 加载这个大文件有时每次要好几秒。
# 导入期间改用 Windows 自带的证书库（约 0.1 秒），连接微软的服务器一样能验证。
_create_default_context = ssl.create_default_context
ssl.create_default_context = lambda *args, cafile=None, **kwargs: _create_default_context(*args, **kwargs)
try:
    import edge_tts
finally:
    ssl.create_default_context = _create_default_context
import miniaudio
import opencc
import pystray
import pythoncom
import win32com.client
from PIL import Image, ImageDraw

APP_NAME = "EdgeSpeak"
CONFIG_DIR = os.path.join(os.environ["APPDATA"], APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_PATH = os.path.join(CONFIG_DIR, "log.txt")

DEFAULT_CONFIG = {
    "hotkey": "ctrl+alt+q",
    "zh_voice": "zh-CN-XiaoxiaoNeural",
    "en_voice": "en-GB-SoniaNeural",  # 英文以英式为准
    "ja_voice": "ja-JP-NanamiNeural",
    "kanji_lang": "ja",  # 只有汉字没有假名时按哪种语言读：ja / zh
    "hover_lookup": True,  # 按住 Shift 悬停查日语词
    "hover_english": True,  # 悬停在英文单词上时也查（ECDICT 英汉词典）
    "ai_provider": "cohere",  # AI 解释用哪个服务：cohere / mistral / openrouter
    "ai_model": "",  # Cohere 模型名，留空用默认聊天模型
    "mistral_model": "",  # Mistral 模型名，留空用 mistral-small-latest
    "openrouter_model": "",  # OpenRouter 模型名，留空用免费路由 openrouter/free
    "ai_fallbacks": [],  # 备用模型 [{"provider": ..., "model": ...}]，主模型出错时按顺序换
    "ai_auto": True,  # 卡片停留片刻后自动请求 AI 解释
    "cohere_api_key": "",  # 各服务的密钥，用 Windows DPAPI 加密后存放，在设置窗口里填
    "mistral_api_key": "",
    "openrouter_api_key": "",
    "dict_auto_update": True,  # 每天在后台检查一次词典更新
    "rate": 0,  # 语速百分比，-40 ~ +40
    "engine": "auto",  # auto：Edge 优先，失败用系统语音；sapi：只用系统语音
}
ZH_VOICES = [
    ("晓晓（女）", "zh-CN-XiaoxiaoNeural"),
    ("晓伊（女）", "zh-CN-XiaoyiNeural"),
    ("云希（男）", "zh-CN-YunxiNeural"),
    ("云健（男）", "zh-CN-YunjianNeural"),
]
EN_VOICES = [
    ("Sonia（英式女）", "en-GB-SoniaNeural"),
    ("Ryan（英式男）", "en-GB-RyanNeural"),
    ("Aria（美式女）", "en-US-AriaNeural"),
    ("Jenny（美式女）", "en-US-JennyNeural"),
    ("Guy（美式男）", "en-US-GuyNeural"),
    ("Andrew（美式男）", "en-US-AndrewNeural"),
]
JA_VOICES = [
    ("七海 Nanami（女）", "ja-JP-NanamiNeural"),
    ("圭太 Keita（男）", "ja-JP-KeitaNeural"),
]
RATES = [("很慢", -40), ("慢", -20), ("正常", 0), ("快", 20), ("很快", 40)]
ENGINES = [("Edge 优先（断网时用系统语音）", "auto"), ("只用系统语音", "sapi")]
KANJI_LANGS = [("日语优先", "ja"), ("中文优先", "zh")]
# 日语里基本不会出现的简体字和中文虚词：纯汉字文本里出现这些就按中文读
CHINESE_ONLY = set("这们说么个时对发话为还进过让给样从问题经现开关门见长东车书买卖实头间边动电网络软"
                   "吗呢吧啊啦呀嘛她你怎谁哪啥听读讲谢请认识语词汉简级产业务员应该觉处组织资计报纸录"
                   "我是很也把吃喝這們說麼讓嗎")

MAX_CHARS = 3000
EDGE_TIMEOUT = 10  # 等第一个（以及之后每一个）音频块的最长时间
CACHE_SIZE = 50
START_BUFFER = 6000  # 边下边播：攒够 0.25 秒音频再开始播，避免开头卡顿
PARTIAL_MARGIN = 1152  # 解码未下载完的 mp3 时，丢掉末尾可能不完整的一两帧

log = logging.getLogger(APP_NAME)


# ---------------------------------------------------------------- 配置

def load_config():
    first_run = not os.path.exists(CONFIG_PATH)
    cfg = dict(DEFAULT_CONFIG)
    if not first_run:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            log.exception("读取配置失败，使用默认配置")
    return cfg, first_run


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- Win32

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wt.HWND]
user32.CloseClipboard.argtypes = []
user32.EmptyClipboard.argtypes = []
user32.EnumClipboardFormats.argtypes = [wt.UINT]
user32.EnumClipboardFormats.restype = wt.UINT
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
HWND_MESSAGE = wt.HWND(-3)
ERROR_ALREADY_EXISTS = 183
# 这些格式存的是 GDI 句柄而不是内存块，无法按字节备份
GDI_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
MAX_FORMAT_BYTES = 20 * 1024 * 1024


class Clipboard:
    """在后台线程里操作剪贴板。需要一个自己的窗口，否则 EmptyClipboard 后 SetClipboardData 可能失败。"""

    def __init__(self):
        self.hwnd = user32.CreateWindowExW(0, "STATIC", APP_NAME, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)

    def _open(self):
        for _ in range(25):
            if user32.OpenClipboard(self.hwnd):
                return True
            time.sleep(0.02)
        return False

    def backup(self):
        saved = []
        if not self._open():
            return None
        try:
            fmt = 0
            while True:
                fmt = user32.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                if fmt in GDI_FORMATS:
                    continue
                handle = user32.GetClipboardData(fmt)
                if not handle:
                    continue
                size = kernel32.GlobalSize(handle)
                if not size or size > MAX_FORMAT_BYTES:
                    continue
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    continue
                try:
                    saved.append((fmt, ctypes.string_at(ptr, size)))
                finally:
                    kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
        return saved

    def restore(self, saved):
        if saved is None or not self._open():
            return
        try:
            user32.EmptyClipboard()
            for fmt, data in saved:
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not handle:
                    continue
                ptr = kernel32.GlobalLock(handle)
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(fmt, handle):
                    kernel32.GlobalFree(handle)
        finally:
            user32.CloseClipboard()

    def read_text(self):
        if not self._open():
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def capture_selection(self, hotkey):
        """模拟 Ctrl+C 取选中文字，取完把剪贴板还原。没选中任何东西时返回空串。"""
        released = wait_keys_released(parse_hotkey(hotkey)[1])
        app = foreground_app()
        saved = self.backup()
        seq = user32.GetClipboardSequenceNumber()
        press_keys(VK_CONTROL, ord("C"))
        # Chromium 内核的程序（Edge、Chrome、VS Code、Claude……）在我们还原剪贴板之后，
        # 对同一段选区的下一次 Ctrl+C 不会写剪贴板，再按一次才写。
        if not wait_clipboard_change(seq, 0.25):
            press_keys(VK_CONTROL, ord("C"))
            if not wait_clipboard_change(seq, 1.0):
                log.info("剪贴板没有变化，可能没有选中文字（前台程序 %s，快捷键%s松开）", app, "已" if released else "未")
                return ""
        time.sleep(0.05)  # 有的程序会分几次写入不同格式
        text = self.read_text()
        self.restore(saved)
        log.info("取词成功（前台程序 %s）", app)
        return text


# ---------------------------------------------------------------- 快捷键和按键

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
KEYEVENTF_KEYUP = 0x2
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
VK_MASK = 0xE8  # 系统未使用的键码，用来打断“单独按了 Alt/Win”
VK_NAMES = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "pause": 0x13,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}


def parse_hotkey(hotkey):
    """"ctrl+alt+q" → (修饰键位掩码, 主键键码)。"""
    mods, vk = 0, None
    for part in (p.strip().lower() for p in hotkey.split("+")):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "windows"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk = 0x70 + int(part[1:]) - 1
        elif part in VK_NAMES:
            vk = VK_NAMES[part]
        else:
            raise ValueError(f"不认识的按键“{part}”")
    if vk is None:
        raise ValueError("缺少主键，例如 ctrl+alt+q 里的 q")
    return mods, vk


def press_keys(*vks):
    """依次按下，再倒序松开。"""
    for vk in vks:
        user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), KEYEVENTF_KEYUP, 0)


def wait_clipboard_change(seq, timeout):
    deadline = time.monotonic() + timeout
    while user32.GetClipboardSequenceNumber() == seq:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def wait_keys_released(main_vk, timeout=1.5):
    """等快捷键松开，否则按住的 Ctrl/Alt 会和模拟的 Ctrl+C 混在一起。"""
    keys = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, main_vk)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in keys):
            return True
        time.sleep(0.02)
    return False


def foreground_app():
    """前台窗口所属的程序名，写日志排查取词问题用。"""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return "?"
    finally:
        kernel32.CloseHandle(handle)


def listen_hotkey(hotkey, callback, on_error):
    """用系统的 RegisterHotKey 注册全局快捷键（不装键盘钩子），在本线程的消息循环里接收。"""
    try:
        mods, vk = parse_hotkey(hotkey)
    except ValueError as e:
        on_error(f"快捷键 {hotkey} 无效：{e}。请在配置文件里修改后重启。")
        return
    if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
        on_error(f"快捷键 {hotkey_label(hotkey)} 已被其他程序占用，请在配置文件里换一个后重启。")
        return
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            if mods & (MOD_ALT | MOD_WIN):
                press_keys(VK_MASK)  # 防止松开 Alt/Win 时激活窗口菜单栏或开始菜单
            callback()


class AudioOutput:
    """打开音频设备可能要一秒多（蓝牙耳机、音效增强驱动等），所以设备用完不马上关，
    空闲一段时间后再关；按下快捷键时就提前打开，和联网合成同时进行。"""

    SAMPLE_RATE = 24000
    IDLE_CLOSE_SECONDS = 300

    def __init__(self):
        self.lock = threading.Lock()
        self.open_lock = threading.Lock()
        self.device = None
        self.samples = None  # 正在播放的 PCM，None 表示静音
        self.pos = 0
        self.complete = False  # 边下边播时：音频是否已经全部到齐
        self.last_used = 0.0

    def warm(self):
        threading.Thread(target=self._open_quietly, daemon=True).start()

    def _open_quietly(self):
        try:
            self.open()
        except Exception:
            log.exception("打开音频设备失败")

    def open(self):
        with self.open_lock:
            if self.device is None:
                device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                                  sample_rate=self.SAMPLE_RATE)
                stream = self._stream()
                next(stream)
                device.start(stream)
                self.device = device
            self.last_used = time.monotonic()

    def close_if_idle(self):
        if time.monotonic() - self.last_used > self.IDLE_CLOSE_SECONDS:
            self.close()

    def close(self):
        with self.open_lock:
            if self.device is not None:
                self.device.close()
                self.device = None

    def _stream(self):
        frames = yield b""
        while True:
            with self.lock:
                chunk = b""
                if self.samples is not None and self.pos < len(self.samples):
                    chunk = self.samples[self.pos:self.pos + frames].tobytes()
                    self.pos += frames
            frames = yield chunk + bytes(frames * 2 - len(chunk))  # 不够的部分补静音

    def begin(self):
        """开始一段新的播放，之后用 append 追加音频，finish 表示追加完毕，wait 等它放完。"""
        self.open()
        with self.lock:
            self.samples, self.pos, self.complete = array("h"), 0, False

    def append(self, samples):
        with self.lock:
            if self.samples is not None:
                self.samples.extend(samples)

    def finish(self):
        with self.lock:
            self.complete = True

    def wait(self, stop_event):
        """等播放结束；stop_event 被设置时立即停止。"""
        try:
            while not stop_event.wait(0.02):
                with self.lock:
                    if self.samples is None or (self.complete and self.pos >= len(self.samples)):
                        break
        finally:
            with self.lock:
                self.samples = None
            self.last_used = time.monotonic()

    def play(self, data, stop_event):
        """播放一段完整的 mp3。"""
        samples = decode_mp3(data)
        self.begin()
        self.append(samples)
        self.finish()
        self.wait(stop_event)


def decode_mp3(data, partial=False):
    """解码成 24kHz 单声道 PCM。partial=True 表示数据还没下载完。"""
    try:
        samples = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                   sample_rate=AudioOutput.SAMPLE_RATE).samples
    except miniaudio.DecodeError:
        if partial:
            return array("h")
        raise
    if partial:
        del samples[max(0, len(samples) - PARTIAL_MARGIN):]
    return samples


# ---------------------------------------------------------------- 朗读

class SapiVoice:
    """Windows 自带语音，离线兜底用。"""

    def __init__(self):
        self.voice = win32com.client.Dispatch("SAPI.SpVoice")
        self.tokens = {}
        for token in self.voice.GetVoices():
            lang = token.GetAttribute("Language").lower()
            if "804" in lang:
                self.tokens.setdefault("zh", token)
            elif "411" in lang:
                self.tokens.setdefault("ja", token)
            elif "809" in lang:
                self.tokens["en"] = token  # 英文以英式为准：有英式语音就用英式的
            elif "409" in lang:
                self.tokens.setdefault("en", token)

    def speak(self, text, lang, rate, stop_event):
        token = self.tokens.get(lang)
        if token is not None:
            self.voice.Voice = token
        self.voice.Rate = round(rate / 12)
        self.voice.Speak(text, 1)  # 1 = 异步
        while not self.voice.WaitUntilDone(50):
            if stop_event.is_set():
                self.voice.Speak("", 3)  # 异步 + 清空队列 = 立即停止
                break


_to_shinjitai = None


def to_shinjitai(text):
    """繁体字 / 旧字体 → 日语新字体（發注 → 発注）。日语音色不认识旧字体组成的词，会逐字乱读。"""
    global _to_shinjitai
    if _to_shinjitai is None:
        _to_shinjitai = opencc.OpenCC("t2jp")
    return _to_shinjitai.convert(text)


def detect_language(text, kanji_lang="ja"):
    """有假名就是日语；没有汉字按英文读；只有汉字时，出现中文特有的字按中文读，否则按 kanji_lang。"""
    if re.search(r"[぀-ヿㇰ-ㇿｦ-ﾟ]", text):
        return "ja"
    if not re.search(r"[㐀-鿿豈-﫿]", text):
        return "en"
    if kanji_lang == "zh" or any(c in CHINESE_ONLY for c in text):
        return "zh"
    return "ja"


class Speaker:
    """所有剪贴板操作、合成和播放都在同一个后台线程里做，快捷键回调只负责发信号。"""

    def __init__(self, cfg, notify):
        self.cfg = cfg
        self.notify = notify
        self.jobs = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.cache = OrderedDict()
        self.warned_fallback = False
        self.audio = AudioOutput()
        self.thread = threading.Thread(target=self._run_logged, daemon=True)
        self.thread.start()

    def _run_logged(self):
        try:
            self._run()
        except Exception:
            log.exception("朗读线程意外退出")

    def toggle(self):
        """快捷键：没在读就读选中的文字，正在读就停下。"""
        if self.busy:
            self.stop_event.set()
        else:
            self.jobs.put(None)

    def say(self, text, lang=None):
        """朗读指定文字；lang 为 None 时自动判断语言。"""
        self.stop_event.set()
        self.jobs.put((text, lang))

    def _run(self):
        pythoncom.CoInitialize()
        self.clipboard = Clipboard()
        self.sapi = None
        while True:
            try:
                job = self.jobs.get(timeout=30)
            except queue.Empty:
                self.audio.close_if_idle()
                continue
            while not self.jobs.empty():
                job = self.jobs.get_nowait()
            self.stop_event.clear()
            self.busy = True
            if self.cfg["engine"] == "auto":
                self.audio.warm()
            try:
                if job is None:
                    self._speak(self.clipboard.capture_selection(self.cfg["hotkey"]))
                else:
                    self._speak(*job)
            except Exception:
                log.exception("朗读失败")
            finally:
                self.busy = False

    def _speak(self, text, lang=None):
        text = re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]
        if not text:
            return
        lang = lang or detect_language(text, self.cfg["kanji_lang"])
        if lang == "ja":
            text = to_shinjitai(text)
        rate = int(self.cfg["rate"])
        log.info("朗读 %d 字（%s）：%s", len(text), lang, text[:40])

        if self.cfg["engine"] == "auto":
            key = (text, self.cfg[f"{lang}_voice"], rate)
            try:
                if key in self.cache:
                    self.cache.move_to_end(key)
                    self.audio.play(self.cache[key], self.stop_event)
                    return
                mp3 = self._edge_stream(*key)
                if mp3:
                    self.cache[key] = mp3
                    if len(self.cache) > CACHE_SIZE:
                        self.cache.popitem(last=False)
                return
            except Exception as e:
                log.warning("Edge TTS 失败，改用系统语音：%r", e)
                if not self.warned_fallback:
                    self.warned_fallback = True
                    self.notify("Edge TTS 暂时不可用（可能断网了），已改用系统语音。")

        if self.stop_event.is_set():
            return
        if self.sapi is None:
            self.sapi = SapiVoice()
        self.sapi.speak(text, lang, rate, self.stop_event)

    def _edge_stream(self, text, voice, rate):
        """边下载边播放。完整下载并播完时返回 mp3 供缓存，被停止或中途出错返回 None。
        还没出声就失败会抛出异常，由调用方改用系统语音。"""
        chunks = queue.Queue()

        def download():
            async def run():
                async for chunk in edge_tts.Communicate(text, voice, rate=f"{rate:+d}%").stream():
                    if chunk["type"] == "audio":
                        chunks.put(chunk["data"])
            try:
                asyncio.run(run())
                chunks.put(None)
            except Exception as e:
                chunks.put(e)

        # 下载放在单独线程里：网络可能卡在系统调用里（比如安全软件拦截联网），
        # 这时 asyncio 的超时不起作用，只能丢下这个线程，改用系统语音。
        threading.Thread(target=download, daemon=True).start()
        started = time.monotonic()
        deadline = started + EDGE_TIMEOUT
        mp3 = bytearray()
        queued = 0  # 已经交给声卡的采样数
        playing = complete = False
        last_decode = 0.0
        try:
            while not self.stop_event.is_set():
                try:
                    item = chunks.get(timeout=0.05)
                except queue.Empty:
                    if time.monotonic() < deadline:
                        continue
                    item = TimeoutError("Edge TTS 无响应")
                if isinstance(item, Exception):
                    if not playing and not mp3:
                        raise item
                    log.warning("Edge TTS 中途出错：%r", item)
                    break
                if item is not None:
                    mp3 += item
                    deadline = time.monotonic() + EDGE_TIMEOUT
                    if time.monotonic() - last_decode < 0.25:
                        continue
                last_decode = time.monotonic()
                samples = decode_mp3(bytes(mp3), partial=item is not None)
                if not playing and (len(samples) >= START_BUFFER or item is None):
                    self.audio.begin()
                    playing = True
                    log.info("Edge 出声用时 %.2fs", time.monotonic() - started)
                if playing and len(samples) > queued:
                    self.audio.append(samples[queued:])
                    queued = len(samples)
                if item is None:
                    complete = True
                    break
        finally:
            if playing:
                self.audio.finish()
                self.audio.wait(self.stop_event)
        return bytes(mp3) if complete and not self.stop_event.is_set() else None


# ---------------------------------------------------------------- 开机自启

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pythonw}" "{os.path.abspath(__file__)}"'


def is_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass


# ---------------------------------------------------------------- 托盘

def make_icon_image(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=14 * s, fill=(0, 120, 212, 255))
    white = (255, 255, 255, 255)
    d.rectangle([12 * s, 25 * s, 21 * s, 39 * s], fill=white)
    d.polygon([(21 * s, 25 * s), (32 * s, 15 * s), (32 * s, 49 * s), (21 * s, 39 * s)], fill=white)
    for r in (9, 17):
        box = [32 * s - r * s, 32 * s - r * s, 32 * s + r * s + 6 * s, 32 * s + r * s]
        d.arc(box, start=-45, end=45, fill=white, width=max(1, round(4 * s)))
    return img


def hotkey_label(hotkey):
    return "+".join(k.strip().capitalize() for k in hotkey.split("+"))


def run_tray(cfg, first_run):
    icon = pystray.Icon(APP_NAME, make_icon_image(), APP_NAME)
    if is_autostart():
        set_autostart(True)  # 程序换了位置（比如改用安装版）后，开机自启要指向现在运行的这一份

    def notify(message):
        try:
            icon.notify(message, APP_NAME)
        except Exception:
            log.exception("通知失败")

    def refresh_menu():
        try:
            icon.update_menu()
        except Exception:
            pass  # 托盘还没跑起来

    # 日语 OCR：没装就提醒一次，菜单里给出“安装日语 OCR”
    ocr = {"ready": None, "installing": False, "notice_pending": False}
    ocr_missing = "没有找到 Windows 日语 OCR，VS Code 编辑区、图片里的文字查不了词。右键托盘图标 →「安装日语 OCR」即可安装。"

    def on_ocr_status(ready):
        first = ocr["ready"] is None
        ocr["ready"] = ready
        refresh_menu()
        if first and not ready:
            if icon.visible:
                notify(ocr_missing)
            else:
                ocr["notice_pending"] = True

    def install_ocr(icon, item):
        if ocr["installing"]:
            return
        ocr["installing"] = True
        refresh_menu()

        def run():
            notify("请在弹出的管理员确认窗口中点“是”。安装需要联网，可能要一两分钟。")
            ok, message = install_japanese_ocr()
            log.info("安装日语 OCR：%s", message)
            if ok:
                if popup.reload_ocr():
                    message = "日语 OCR 已安装，VS Code 编辑区、图片里的文字也能查词了。"
                else:
                    message = "日语 OCR 已安装，重启 EdgeSpeak 后生效。"
            ocr["installing"] = False
            refresh_menu()
            notify(message)
        threading.Thread(target=run, daemon=True).start()

    speaker = Speaker(cfg, notify)
    # 加载分词词典要一点时间，放到托盘创建前做
    from jp_ai import Explainer
    from jp_popup import LookupPopup, install_japanese_ocr
    from settings_window import open_settings as show_settings

    def open_settings():
        show_settings(cfg, save_config, make_icon_image(64))

    popup = LookupPopup(lambda text, lang: speaker.say(text, lang=lang), lambda: cfg["hover_lookup"], on_ocr_status,
                        explainer=Explainer(cfg), open_settings=open_settings,
                        english_enabled=lambda: cfg["hover_english"])

    # 词典：精简版安装包第一次运行时在后台下载；之后自动更新。装好后查词线程下次查词时自动换用新词典
    import dict_store
    first_download = bool(dict_store.missing())
    dict_notice = ("首次运行，正在后台下载词典（共约 1.4 GB，视网速要几分钟到半小时）。日英词典下载完就能查词，"
                   "朗读不受影响；进度可以把鼠标停在托盘图标上查看。")

    def on_dict_status(text):
        icon.title = f"{APP_NAME}：{text}" if text else APP_NAME
        refresh_menu()

    def on_dict_installed(name):
        if first_download and name == "jmdict":
            notify("日英词典已就绪，可以按住 Shift 查词了。其余词典继续在后台下载。")

    def on_dict_update(installed, failed, manual, still_missing):
        refresh_menu()
        names = "、".join(f"{dict_store.LABELS[n]}（{v}）" for n, v in installed)
        if still_missing and failed:
            notify("部分词典下载失败（" + "、".join(dict_store.LABELS[n] for n, _ in failed) + "），"
                   "请检查网络，或在安全软件里允许 EdgeSpeak 联网。10 分钟后自动重试，"
                   "也可以右键托盘图标 →「词典」→「立即检查更新」。")
        elif first_download and installed and not still_missing:
            notify("词典全部下载完成。")
        elif installed:
            notify(f"词典已更新：{names}")
        elif manual:
            notify("检查更新失败，详情见日志：" + "；".join(dict_store.LABELS[n] for n, _ in failed) if failed
                   else "词典都已是最新版本。")

    updater = dict_store.Updater(lambda: cfg["dict_auto_update"], on_dict_update, on_dict_status, on_dict_installed)

    def check_dict_updates(icon, item):
        if updater.check_now():
            notify("正在检查词典更新，有新版本会在后台下载（分词词典约 700 MB），装好后会通知。")
        else:
            notify("后台正在下载或检查词典，完成后会通知。")

    def toggle_dict_auto_update(icon, item):
        cfg["dict_auto_update"] = not cfg["dict_auto_update"]
        save_config(cfg)

    def choice(key, value, preview=None):
        def action(icon, item):
            cfg[key] = value
            save_config(cfg)
            if preview:
                speaker.say(preview)

        def checked(item):
            return cfg[key] == value

        return action, checked

    def submenu(key, options, preview=None):
        items = []
        for label, value in options:
            action, checked = choice(key, value, preview)
            items.append(pystray.MenuItem(label, action, checked=checked, radio=True))
        return pystray.Menu(*items)

    def toggle_autostart(icon, item):
        set_autostart(not is_autostart())

    def toggle_hover_lookup(icon, item):
        cfg["hover_lookup"] = not cfg["hover_lookup"]
        save_config(cfg)

    def toggle_hover_english(icon, item):
        cfg["hover_english"] = not cfg["hover_english"]
        save_config(cfg)

    def open_config_dir(icon, item):
        os.startfile(CONFIG_DIR)

    def settings_action(icon, item):
        open_settings()

    def quit_app(icon, item):
        updater.stop()
        speaker.stop_event.set()
        speaker.audio.close()
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem(f"选中文字后按 {hotkey_label(cfg['hotkey'])} 朗读 / 停止", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("中文音色", submenu("zh_voice", ZH_VOICES, "你好，这是现在的中文音色。")),
        pystray.MenuItem("英文音色", submenu("en_voice", EN_VOICES, "Hello, this is the current English voice.")),
        pystray.MenuItem("日语音色", submenu("ja_voice", JA_VOICES, "こんにちは、これは今の日本語の音声です。")),
        pystray.MenuItem("只有汉字时", submenu("kanji_lang", KANJI_LANGS)),
        pystray.MenuItem("按住 Shift 悬停查词", toggle_hover_lookup, checked=lambda item: cfg["hover_lookup"]),
        pystray.MenuItem("    英文单词也查", toggle_hover_english, checked=lambda item: cfg["hover_english"],
                         enabled=lambda item: cfg["hover_lookup"]),
        pystray.MenuItem(lambda item: "正在安装日语 OCR…" if ocr["installing"] else "安装日语 OCR（需要管理员确认）",
                         install_ocr, enabled=lambda item: not ocr["installing"],
                         visible=lambda item: ocr["ready"] is False),
        pystray.MenuItem("语速", submenu("rate", RATES, "语速已调整。")),
        pystray.MenuItem("朗读引擎", submenu("engine", ENGINES)),
        pystray.MenuItem("词典", pystray.Menu(
            pystray.MenuItem(lambda item: updater.status or "立即检查更新", check_dict_updates,
                             enabled=lambda item: not updater.busy),
            pystray.MenuItem("每天自动更新", toggle_dict_auto_update, checked=lambda item: cfg["dict_auto_update"]),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("设置…（AI 解释的密钥和模型）", settings_action, default=True),
        pystray.MenuItem("开机自动启动", toggle_autostart, checked=lambda item: is_autostart()),
        pystray.MenuItem("打开配置文件夹（改快捷键后需重启）", open_config_dir),
        pystray.MenuItem("退出", quit_app),
    )

    def on_ready(icon):
        icon.visible = True

        def on_error(message):
            log.error(message)
            notify(message)

        threading.Thread(target=listen_hotkey, args=(cfg["hotkey"], speaker.toggle, on_error), daemon=True).start()
        if first_run:
            notify(f"已在后台运行。选中文字后按 {hotkey_label(cfg['hotkey'])} 朗读，再按一次停止。")
        if ocr["notice_pending"]:
            notify(ocr_missing)
        if first_download:
            notify(dict_notice)
        refresh_menu()

    icon.run(setup=on_ready)


def main():
    # 按显示器各自的缩放比例工作：取词坐标和弹窗位置都是物理像素，卡片也能画得清晰
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    os.makedirs(CONFIG_DIR, exist_ok=True)
    logging.basicConfig(
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    if len(sys.argv) == 3 and sys.argv[1] == "--say":
        # 自测用：EdgeSpeak.exe --say "要读的文字"，读完就退出
        speaker = Speaker(load_config()[0], log.warning)
        speaker.say(sys.argv[2])
        time.sleep(0.1)
        while speaker.thread.is_alive() and (speaker.busy or not speaker.jobs.empty()):
            time.sleep(0.05)
        speaker.audio.close()  # 不关设备的话，退出时音频回调线程可能卡住进程
        log.info("--say 完成")
        return
    mutex = kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        user32.MessageBoxW(None, f"{APP_NAME} 已经在运行了，请看任务栏右下角的托盘图标。", APP_NAME, 0x40)
        return
    cfg, first_run = load_config()
    if first_run:
        save_config(cfg)
    log.info("启动，快捷键 %s", cfg["hotkey"])
    try:
        run_tray(cfg, first_run)
    finally:
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
