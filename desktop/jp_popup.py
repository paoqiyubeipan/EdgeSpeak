"""按住 Shift 把鼠标停在日语（或英文单词）上：用 UI Automation 取光标下的文字（取不到时用 Windows 自带的 OCR），
查词后在旁边弹出卡片。"""
import asyncio
import base64
import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
import tempfile
import threading
import time
from dataclasses import dataclass

import comtypes
import comtypes.client
from PIL import Image, ImageChops

import dict_store
from jp_ai import en_context_request, en_request, has_key, jp_request, provider_of
from jp_card import Card, EnglishCard
from jp_dict import EnglishDictionary, EnglishHit, JapaneseDictionary, lookup_english

log = logging.getLogger("EdgeSpeak")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
shcore = ctypes.WinDLL("shcore")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_byte)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, wt.LPVOID]
user32.SetTimer.restype = ctypes.c_size_t
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPVOID]
user32.LoadCursorW.restype = wt.HANDLE
user32.SetCursor.argtypes = [wt.HANDLE]
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = wt.HMONITOR
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
shcore.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT)]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int,
                         ctypes.c_int, wt.DWORD]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER),
                            wt.UINT]

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x8, 0x80, 0x08000000
WM_TIMER, WM_LBUTTONUP, WM_MOUSEACTIVATE, WM_SETCURSOR = 0x0113, 0x0202, 0x0021, 0x0020
WM_APP_RESULT = 0x8000 + 1
WM_APP_AI = 0x8000 + 2
MA_NOACTIVATE = 3
SW_HIDE = 0
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
ULW_ALPHA = 2
SRCCOPY = 0x00CC0020
IDC_ARROW, IDC_HAND = 32512, 32649
AI_DWELL = 0.6  # 卡片在同一个词上停留这么久才自动请求 AI 解释
VK_LBUTTON, VK_RBUTTON, VK_SHIFT, VK_ESCAPE = 0x01, 0x02, 0x10, 0x1B
MOUSE_AND_SHIFT_KEYS = {0x01, 0x02, 0x04, 0x05, 0x06, 0x10, 0xA0, 0xA1, 0xE8}

TICK_MS = 30
SHIFT_DELAY = 0.12  # 单独按住 Shift 这么久才开始查，避免打字时按 Shift 输入大写字母误触发
CONTEXT_CHARS = 120  # 光标前后各取多少字（分词用，也给 AI 当上下文）


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def in_rect(x, y, rect, pad=0):
    l, t, r, b = rect
    return l - pad <= x <= r + pad and t - pad <= y <= b + pad


def monitor_at(x, y):
    """（缩放比例，工作区矩形）"""
    mon = user32.MonitorFromPoint(wt.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
    dpi_x, dpi_y = wt.UINT(96), wt.UINT(96)
    shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    w = info.rcWork
    return dpi_x.value / 96, (w.left, w.top, w.right, w.bottom)


# ---------------------------------------------------------------- 取光标下的文字

@dataclass
class TextHit:
    text: str  # 光标前后的一段文字
    offset: int  # 光标所在的字在 text 里的位置
    char_rect: tuple  # 那个字的屏幕矩形 (l, t, r, b)，可能为 None
    source: str = "uia"  # uia / ocr


class TextAtPoint:
    """用 UI Automation 的 TextPattern 取屏幕坐标处的文字。要在已经初始化 COM 的线程里创建和使用。"""

    UIA_TextPatternId = 10014
    TextUnit_Character = 0
    Start, End = 0, 1

    def __init__(self):
        try:  # 打包版里是打包时预先生成好的
            from comtypes.gen import UIAutomationClient as uia
        except ImportError:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia
        self.uia_mod = uia
        self.uia = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        self.walker = self.uia.RawViewWalker

    def _text_patterns(self, element):
        """从光标处的元素往上，依次给出带 TextPattern 的祖先。
        Chromium / Electron 里文字到“文档”元素之间可能隔几十层（Claude 的聊天正文就很深）。"""
        for _ in range(64):
            if not element:
                return
            try:
                unknown = element.GetCurrentPattern(self.UIA_TextPatternId)
                if unknown:
                    yield unknown.QueryInterface(self.uia_mod.IUIAutomationTextPattern)
            except comtypes.COMError:
                pass
            try:
                element = self.walker.GetParentElement(element)
            except comtypes.COMError:
                return

    def _char_at(self, pt, x, y):
        """（光标下那个字的文字区间，它的矩形）。有的文字元素自己的 TextPattern 给不出坐标，
        这时换上一级（通常是整个文档）再试。"""
        for n, pattern in enumerate(self._text_patterns(self.uia.ElementFromPoint(pt))):
            if n >= 3:
                break
            char = pattern.RangeFromPoint(pt)
            char.ExpandToEnclosingUnit(self.TextUnit_Character)
            boxes = char.GetBoundingRectangles() or ()
            if len(boxes) >= 4:
                l, t, w, h = boxes[:4]
                rect = (l, t, l + w, t + h)
                if in_rect(x, y, rect, pad=3):
                    return char, rect
                # 字的矩形不在光标处：光标在行尾空白处，RangeFromPoint 给的是最近的字
                return None, None
        return None, None

    def get(self, x, y):
        pt = self.uia_mod.tagPOINT(x, y)
        try:
            char, rect = self._char_at(pt, x, y)
            if char is None:
                return None
            ctx = char.Clone()
            ctx.MoveEndpointByUnit(self.Start, self.TextUnit_Character, -CONTEXT_CHARS)
            prefix = ctx.Clone()
            prefix.MoveEndpointByRange(self.End, char, self.Start)
            offset = len(prefix.GetText(-1))
            ctx.MoveEndpointByUnit(self.End, self.TextUnit_Character, CONTEXT_CHARS)
            text = ctx.GetText(-1)
            ch = char.GetText(-1)
        except comtypes.COMError as e:
            log.debug("UI Automation 取词失败：%r", e)
            return None
        if not text or not ch.strip() or ch == "￼":
            # ￼ 是嵌入对象（canvas、图片……）的占位符，里面的字辅助接口看不到，交给 OCR
            return None
        if text[offset:offset + len(ch)] != ch:
            # 换行等字符可能让长度对不上，就在附近找这个字
            found = text.find(ch, max(0, offset - 3))
            if found < 0:
                return None
            offset = found
        return TextHit(text, offset, rect)


def grab_screen(left, top, width, height):
    """截屏幕的一块（物理像素）。不带 CAPTUREBLT，分层窗口不会被截进来，正好避开我们自己的卡片。"""
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(mem, bitmap)
    gdi32.BitBlt(mem, 0, 0, width, height, screen, left, top, SRCCOPY)
    header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height, biPlanes=1,
                              biBitCount=32, biCompression=0)
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(header), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    return Image.frombuffer("RGB", (width, height), buf.raw, "raw", "BGRX", 0, 1)


def is_ascii_word(text):
    return text.isascii() and any(c.isalnum() for c in text)


def mostly_latin(text):
    """去掉空白后六成以上是半角字符：这段主要是英文（日语 OCR 把几个字母认成汉字时也算）。"""
    chars = [c for c in text if not c.isspace()]
    return bool(chars) and sum(c.isascii() for c in chars) >= 0.6 * len(chars)


class ScreenOcr:
    """UI Automation 取不到字时（VS Code 编辑区、PDF、图片……）的后备：截取光标所在的一条屏幕，
    用 Windows 自带的日语 OCR 识别，再找出光标下的字。光标下是英文时可以换英文 OCR 再认一次（english=True）。
    要在 MTA 线程里创建和使用。"""

    HALF_WIDTH = 500  # 截取范围（按 100% 缩放计），左右各 500、上下各 60 像素：光标所在行加上下几行当上下文
    HALF_HEIGHT = 60

    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.security.cryptography import CryptographicBuffer
        self.SoftwareBitmap, self.BGRA8, self.Buffer = SoftwareBitmap, BitmapPixelFormat.BGRA8, CryptographicBuffer
        self.engine = OcrEngine.try_create_from_language(Language("ja"))
        if self.engine is None:
            raise RuntimeError("没有安装 Windows 日语 OCR（设置 → 时间和语言 → 语言 → 日语 → 光学字符识别）")
        # 日语 OCR 认英文常常漏掉单词间的空格、认错字母；装了英文 OCR（英文系统一般自带）就用它认英文
        self.en_engine = next((OcrEngine.try_create_from_language(lang)
                               for lang in OcrEngine.available_recognizer_languages
                               if lang.language_tag.lower().startswith("en")), None)

    def _recognize(self, img, engine):
        r, g, b = img.split()
        a = Image.new("L", img.size, 255)
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()
        bitmap = self.SoftwareBitmap.create_copy_from_buffer(self.Buffer.create_from_byte_array(data), self.BGRA8,
                                                             img.width, img.height)

        async def run():
            return await engine.recognize_async(bitmap)
        return asyncio.run(run())

    def get(self, x, y, scale, english=False):
        engine = self.en_engine if english else self.engine
        if engine is None:
            return None
        hw, hh = round(self.HALF_WIDTH * scale), round(self.HALF_HEIGHT * scale)
        left, top = x - hw, y - hh
        img = grab_screen(left, top, 2 * hw, 2 * hh)
        zoom = 2 if scale < 1.5 else 1  # 字太小时放大再识别，准确率高不少
        if zoom > 1:
            img = img.resize((img.width * zoom, img.height * zoom), Image.LANCZOS)
        cx, cy = (x - left) * zoom, (y - top) * zoom
        lines = []  # [(文字, 上, 下, 光标所在字的位置或 None, 那个字的矩形)]
        for line in self._recognize(img, engine).lines:
            words = list(line.words)
            if not words:
                continue
            line_top = min(w.bounding_rect.y for w in words)
            line_bottom = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
            on_line = line_top - 2 <= cy <= line_bottom + 2
            text, offset, rect, prev = "", None, None, None
            for w in words:
                if prev and (english or is_ascii_word(prev) and is_ascii_word(w.text)):
                    text += " "  # 日语 OCR 逐字返回，只在英文单词之间补空格；英文 OCR 按单词返回
                start = len(text)
                text += w.text
                r = w.bounding_rect
                if on_line and offset is None and r.x - 2 <= cx <= r.x + r.width + 2:
                    n = len(w.text)
                    k = min(n - 1, max(0, int((cx - r.x) / max(r.width, 1) * n)))
                    offset = start + k
                    cl = left + (r.x + r.width * k / n) / zoom
                    rect = (cl, top + r.y / zoom, cl + r.width / n / zoom, top + (r.y + r.height) / zoom)
                prev = w.text
            lines.append((text, line_top, line_bottom, offset, rect))
        lines.sort(key=lambda item: item[1])
        hit = next((i for i, item in enumerate(lines) if item[3] is not None), None)
        if hit is None:
            return None
        # 把紧挨着的上一行、下一行也带上，作为上下文（日语折行时句子常跨行）
        text, line_top, line_bottom, offset, rect = lines[hit]
        height = line_bottom - line_top
        # 英文折行处是单词间的空格；日语折行处直接相连，行首行尾是英文时用换行隔开
        if hit > 0 and line_top - lines[hit - 1][2] < height:
            before = lines[hit - 1][0]
            joiner = " " if english else "" if before and not is_ascii_word(before[-1]) else "\n"
            text, offset = before + joiner + text, offset + len(before) + len(joiner)
        if hit + 1 < len(lines) and lines[hit + 1][1] - line_bottom < height:
            after = lines[hit + 1][0]
            joiner = " " if english else "" if after and not is_ascii_word(after[0]) else "\n"
            text += joiner + after
        return TextHit(text, offset, rect, "ocr")


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("fMask", wt.ULONG), ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
                ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR), ("lpDirectory", wt.LPCWSTR),
                ("nShow", ctypes.c_int), ("hInstApp", wt.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY), ("dwHotKey", wt.DWORD),
                ("hIconOrMonitor", wt.HANDLE), ("hProcess", wt.HANDLE)]


shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

ERROR_CANCELLED = 1223
INSTALL_TIMEOUT_MS = 20 * 60 * 1000

# 先按通配符查出组件的确切名称（版本号部分以后可能变），已安装就直接成功
OCR_INSTALL_SCRIPT = """
$ErrorActionPreference = 'Stop'
try {
    $cap = Get-WindowsCapability -Online -Name 'Language.OCR~~~ja-JP~*' | Select-Object -First 1
    if (-not $cap) { throw 'Windows Update 里找不到日语 OCR 组件（Language.OCR~~~ja-JP）' }
    if ($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
    exit 0
} catch {
    $_.Exception.Message | Out-File -Encoding utf8 -FilePath '%s'
    exit 1
}
"""


def install_japanese_ocr(verb="runas"):
    """弹出管理员确认，安装 Windows 日语 OCR，一直等到装完。返回（是否成功，说明）。"""
    error_file = os.path.join(tempfile.gettempdir(), "edgespeak_ocr_install.txt")
    try:
        os.remove(error_file)
    except OSError:
        pass
    script = OCR_INSTALL_SCRIPT % error_file.replace("'", "''")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                              "powershell.exe")
    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x40 | 0x100,  # NOCLOSEPROCESS | NOASYNC
                             lpVerb=verb, lpFile=powershell, nShow=0,
                             lpParameters=f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}")
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            return False, "已取消安装。"
        return False, f"无法启动安装（错误 {error}）。"
    try:
        if kernel32.WaitForSingleObject(info.hProcess, INSTALL_TIMEOUT_MS) != 0:
            return False, "安装超时，请稍后在“设置 → 时间和语言 → 语言和区域”里手动安装。"
        code = wt.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    finally:
        kernel32.CloseHandle(info.hProcess)
    if code.value == 0:
        return True, "日语 OCR 已安装。"
    try:
        with open(error_file, encoding="utf-8-sig") as f:
            detail = f.read().strip()
    except OSError:
        detail = f"退出码 {code.value}"
    return False, f"安装失败：{detail}"


# ---------------------------------------------------------------- 弹窗

def premultiplied_bgra(img):
    r, g, b, a = img.split()
    return Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(r, a), a)).tobytes()


class LookupPopup:
    """界面线程：定时检查 Shift 和鼠标，显示 / 隐藏卡片。工作线程：取词、查词。"""

    def __init__(self, speak, is_enabled, on_ocr_status=None, explainer=None, open_settings=None,
                 english_enabled=lambda: True):
        self.speak = speak  # speak(text, lang)：lang 为 "ja" 或 "en"
        self.is_enabled = is_enabled
        self.english_enabled = english_enabled  # 光标下不是日语时，是否查英文单词
        self.en_hwnd = None  # 英文单词卡片的窗口
        self.en_card = None
        self.en_rect = None  # 英文卡片显示时的屏幕矩形
        self.en_dict = None  # 在界面线程里用，第一次点英文单词时才打开
        self.en_view = None  # 英文卡片的内容：{"entry", "word", "request", "ai"}
        self.on_ocr_status = on_ocr_status  # on_ocr_status(是否可用)，在工作线程里调用
        self.explainer = explainer  # jp_ai.Explainer；None 时卡片不显示 AI 区
        self.open_settings = open_settings
        self.ai = {"state": "off", "text": ""}
        self.ai_due = None  # 到这个时间就自动请求 AI 解释
        self.ai_updates = queue.Queue()
        self.view = None  # 当前卡片的位置信息
        self.ocr = None
        self._reload_ocr = threading.Event()
        self._reload_done = threading.Event()
        self.requests = queue.Queue()
        self.results = queue.Queue()
        self.hwnd = None
        self.seq = 0
        self.shift_since = None
        self.typed = False
        self.last_request = None
        self.visible = False
        self.card = None
        self.card_rect = (0, 0, 0, 0)  # 卡片本体（不含阴影）的屏幕矩形
        self.anchor_rect = (0, 0, 0, 0)
        self.shown_key = None
        self.ready = threading.Event()
        threading.Thread(target=self._ui_loop, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- 工作线程

    def _worker(self):
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            reader = TextAtPoint()
        except Exception:
            log.exception("查词功能初始化失败")
            return
        self._load_ocr()
        try:
            dictionary = self._open_dictionary()
        except Exception:
            log.exception("打开词典失败")
            return
        en_dict = None  # 英文取词用的英汉词典（这个线程自己的连接，第一次遇到英文时打开）
        self.ready.wait()
        log.info("查词功能就绪")
        while True:
            req = self.requests.get()
            while not self.requests.empty():
                req = self.requests.get_nowait()
            if self._reload_ocr.is_set():
                self._reload_ocr.clear()
                self._load_ocr()
                self._reload_done.set()
            if req is None:  # 只是用来唤醒线程
                continue
            if dictionary.stale:
                try:
                    dictionary = JapaneseDictionary()
                    log.info("已换用更新后的词典")
                except Exception:
                    log.exception("打开更新后的词典失败，继续用旧的")
                    dictionary.generation = dict_store.generation
            ocr = self.ocr
            seq, x, y = req
            started = time.monotonic()
            word = None
            try:
                hit = reader.get(x, y)
                if hit is None and ocr is not None:
                    hit = ocr.get(x, y, monitor_at(x, y)[0])
                if hit:
                    word = dictionary.lookup(hit.text, hit.offset)
                if word is None and hit and self.english_enabled():
                    if en_dict is None or en_dict.stale:
                        en_dict = EnglishDictionary()
                    if hit.source == "ocr":
                        word, hit = self._english_from_ocr(ocr, en_dict, hit, x, y)
                    else:
                        word = lookup_english(en_dict, hit.text, hit.offset)
            except Exception:
                log.exception("查词失败")
                hit = None
            if word:
                log.info("查词 %s → %s（%s，%.0fms）", word.surface, word.base, hit.source,
                         (time.monotonic() - started) * 1000)
            self.results.put((seq, x, y, word, hit))
            user32.PostMessageW(self.hwnd, WM_APP_RESULT, 0, 0)

    @staticmethod
    def _english_from_ocr(ocr, en_dict, hit, x, y):
        """OCR 取到的字查不到日语词时找英文单词，返回（EnglishHit 或 None，用的那次识别结果）。
        日语 OCR 认英文常漏空格、把字母认成数字或汉字；英文 OCR 认英文准，但会把旁边的日文认成乱码粘到单词上。
        所以两个都认：一致时用英文 OCR 的；不一致时看光标这一段主要是不是英文。"""
        ja_word = lookup_english(en_dict, hit.text, hit.offset)
        en_hit = ocr.get(x, y, monitor_at(x, y)[0], english=True)
        en_word = lookup_english(en_dict, en_hit.text, en_hit.offset) if en_hit else None
        if en_word and ja_word:
            a, b = en_word.surface.lower(), ja_word.surface.lower()
            if a in b or b in a or mostly_latin(hit.text):
                return en_word, en_hit
            return ja_word, hit
        if en_word and mostly_latin(hit.text):
            return en_word, en_hit
        return ja_word, hit

    @staticmethod
    def _open_dictionary():
        """词典还没下载完（精简版安装包第一次运行）时一直等，JMdict 下载好就能查词。"""
        waiting = False
        while True:
            try:
                return JapaneseDictionary()
            except dict_store.Missing as e:
                if not waiting:
                    log.info("等词典下载完再开始查词：%s", e)
                    waiting = True
                time.sleep(3)

    def _load_ocr(self):
        try:
            self.ocr = ScreenOcr()
        except Exception as e:
            log.warning("OCR 不可用，只能在支持辅助接口的程序里查词：%s", e)
            self.ocr = None
        if self.on_ocr_status:
            self.on_ocr_status(self.ocr is not None)

    def reload_ocr(self, timeout=15):
        """装好日语 OCR 后重新加载（在工作线程里做）。返回现在是否可用。"""
        self._reload_done.clear()
        self._reload_ocr.set()
        self.requests.put(None)
        self._reload_done.wait(timeout)
        return self.ocr is not None

    # ---- 界面线程

    def _ui_loop(self):
        self._wndproc = WNDPROC(self._on_message)  # 保持引用，避免被回收
        cls = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc,
                          lpszClassName="EdgeSpeakLookupCard")  # 光标在 WM_SETCURSOR 里按位置设置
        user32.RegisterClassExW(ctypes.byref(cls))
        self.hwnd, self.en_hwnd = (
            user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                                   "EdgeSpeakLookupCard", "EdgeSpeak", WS_POPUP, 0, 0, 0, 0, None, None, None, None)
            for _ in range(2))
        user32.SetTimer(self.hwnd, 1, TICK_MS, None)
        self.ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TIMER:
                self._tick()
                return 0
            if msg == WM_APP_RESULT:
                self._on_results()
                return 0
            if msg == WM_APP_AI:
                self._on_ai_updates()
                return 0
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if msg == WM_SETCURSOR:
                # 只有能点的地方（标题、AI 按钮、英文单词）显示小手
                pt = wt.POINT(*cursor_pos())
                user32.ScreenToClient(hwnd, ctypes.byref(pt))
                if hwnd == self.en_hwnd:
                    clickable = self.en_card is not None and self._english_target(pt.x, pt.y) is not None
                else:
                    clickable = self.card is not None and self._click_target(pt.x, pt.y) is not None
                user32.SetCursor(user32.LoadCursorW(None, IDC_HAND if clickable else IDC_ARROW))
                return 1
            if msg == WM_LBUTTONUP:
                x = ctypes.c_short(lparam & 0xFFFF).value
                y = ctypes.c_short(lparam >> 16 & 0xFFFF).value
                if hwnd == self.en_hwnd:
                    target = self._english_target(x, y) if self.en_card else None
                    if target == "title":
                        self.speak(self.en_card.word_text, "en")
                    elif target == "ai":
                        self._request_english_ai()
                    return 0
                target = self._click_target(x, y) if self.card else None
                if target == "ai":
                    self._request_ai(self.view["word"])  # 英文卡片的 card.word 是词典条目，不是取到的词
                elif target == "title" and isinstance(self.card, EnglishCard):
                    self.speak(self.card.word_text, "en")
                elif target == "title":
                    log.info("点击卡片，读：%s", self.card.word.surface)
                    self.speak(self.card.word.surface, "ja")
                elif target:
                    self._show_english(target[1])
                return 0
        except Exception:
            log.exception("弹窗处理消息出错")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _click_target(self, x, y):
        """卡片上 (x, y)（窗口坐标）处能点的东西："title"、"ai"、("word", 英文单词) 或 None。"""
        region = self.card.ai_region
        if region and region[0] <= y <= region[1]:
            return "ai"
        if y < self.card.header_bottom:
            return "title"
        link = self.card.link_at(x, y)
        return ("word", link) if link else None

    def _english_target(self, x, y):
        region = self.en_card.ai_region
        if region and region[0] <= y <= region[1]:
            return "ai"
        return "title" if y < self.en_card.header_bottom else None

    def _in_cards(self, x, y, pad=0):
        return in_rect(x, y, self.card_rect, pad) or (self.en_rect is not None and in_rect(x, y, self.en_rect, pad))

    def _show_english(self, link):
        """在日语卡片旁边弹出英文单词卡片（右边放不下就放左边）。再点别的单词就换内容。
        link 是（英文单词，它所在的义项编号）：AI 解释时要用那条释义做上下文。"""
        word, sense = link
        if self.en_dict is None or self.en_dict.stale:
            self.en_dict = EnglishDictionary()
        entry = self.en_dict.lookup(word)
        log.info("查英文：%s → %s", word, entry.word if entry else "（没有）")
        jp_word = self.view["word"]
        request = en_request(word, "; ".join(jp_word.senses[sense - 1][1]), jp_word)
        self.en_view = {"entry": entry, "word": word, "request": request, "ai": self._initial_ai(request)}
        self._render_english()

    def _render_english(self):
        ev = self.en_view
        scale, work = self.view["scale"], self.view["work"]
        card = EnglishCard(ev["entry"], ev["word"], scale, ev["ai"], dictionary_ready=self.en_dict.ready)
        img = card.render()
        m, gap = card.margin, round(10 * scale)
        jl, jt, jr, jb = self.card_rect
        wx = jr + gap - m
        if wx + img.width - m > work[2]:
            wx = jl - gap - (img.width - m)
        wy = max(work[1] - m, min(jt - m, work[3] - img.height + m))
        self._update_layered(img, wx, wy, self.en_hwnd)
        self.en_card = card
        self.en_rect = (wx + m, wy + m, wx + img.width - m, wy + img.height - m)

    def _hide_english(self):
        if self.en_rect is not None:
            user32.ShowWindow(self.en_hwnd, SW_HIDE)
            self.en_rect = None
            self.en_card = None
            self.en_view = None

    def _other_keys_down(self):
        return any(key_down(vk) for vk in range(0x01, 0xFF) if vk not in MOUSE_AND_SHIFT_KEYS)

    def _tick(self):
        if not self.is_enabled():
            self.hide()
            return
        now = time.monotonic()
        x, y = cursor_pos()
        if self.visible and key_down(VK_ESCAPE):
            self.hide()
        if self.visible and self.ai_due and now >= self.ai_due:
            # 卡片在同一个词上停够了才自动请求，免得 Shift 滑过一串词时每个都消耗额度
            self.ai_due = None
            if self.ai["state"] is None:
                self._request_ai(self.view["word"], manual=False)
        if key_down(VK_SHIFT):
            if self.shift_since is None:
                self.shift_since, self.typed, self.last_request = now, False, None
            if self._other_keys_down():
                self.typed = True  # Shift 是拿来打字 / 组合键的，不查
            if self.typed or now - self.shift_since < SHIFT_DELAY:
                return
            if self.visible and self._in_cards(x, y):
                return
            if self.last_request is None or abs(x - self.last_request[0]) + abs(y - self.last_request[1]) > 2:
                self.seq += 1
                self.last_request = (x, y)
                self.requests.put((self.seq, x, y))
            return
        self.shift_since = None
        if not self.visible:
            return
        if (key_down(VK_LBUTTON) or key_down(VK_RBUTTON)) and not self._in_cards(x, y):
            self.hide()
            return
        scale, _ = monitor_at(x, y)
        rects = [self.card_rect, self.anchor_rect] + ([self.en_rect] if self.en_rect else [])
        union = (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects),
                 max(r[3] for r in rects))
        if not in_rect(x, y, union, pad=24 * scale):
            self.hide()

    def _on_results(self):
        latest = None
        while not self.results.empty():
            latest = self.results.get_nowait()
        if latest is None or latest[0] != self.seq:
            return  # 鼠标已经移到别处，等更新的结果
        _, x, y, word, hit = latest
        if word is None:
            if key_down(VK_SHIFT):
                self.hide()
            return
        key = (word.context, word.span)
        if self.visible and key == self.shown_key:
            return
        self.show(word, x, y, hit.char_rect)
        self.shown_key = key

    def show(self, word, x, y, char_rect):
        scale, work = monitor_at(x, y)
        anchor = tuple(round(v) for v in char_rect) if char_rect else (x - 1, y - 1, x + 1, y + 1)
        self.view = {"word": word, "x": x, "scale": scale, "work": work, "anchor": anchor, "placement": None}
        self._hide_english()
        self.ai = self._initial_ai(self._request_for(word))
        self.ai_due = None
        if self.ai["state"] is None and self.explainer.cfg.get("ai_auto", True):
            if has_key(self.explainer.cfg):
                self.ai_due = time.monotonic() + AI_DWELL
            else:
                self.ai["text"] = f"填好 {provider_of(self.explainer.cfg).label} 密钥后可自动解释（点这里设置）"
        self._render()
        self.visible = True

    def _render(self):
        """按当前状态画卡片并摆好位置。高度会随 AI 输出变化：放在单词下方就往下长，放在上方就往上长。"""
        v, word = self.view, self.view["word"]
        if isinstance(word, EnglishHit):
            card = EnglishCard(word.entry, word.surface, v["scale"], self.ai, word.dictionary_ready, in_context=True)
        else:
            card = Card(word, v["scale"], self.ai)
        img = card.render()
        m, s, anchor, work = card.margin, v["scale"], v["anchor"], v["work"]
        below = anchor[3] + round(8 * s) - m
        if v["placement"] is None:  # 下面放不下就放到上面
            v["placement"] = "below" if below + img.height - m <= work[3] else "above"
        wy = below if v["placement"] == "below" else anchor[1] - round(8 * s) - img.height + m
        wx = v["x"] - m - round(28 * s)
        wx = max(work[0] - m, min(wx, work[2] - img.width + m))
        wy = max(work[1] - m, wy)
        self._update_layered(img, wx, wy)
        self.card = card
        self.card_rect = (wx + m, wy + m, wx + img.width - m, wy + img.height - m)
        self.anchor_rect = anchor

    # ---- AI 解释

    @staticmethod
    def _request_for(word):
        """卡片上的词（WordInfo 或 EnglishHit）结合上下文解释的请求。"""
        return en_context_request(word) if isinstance(word, EnglishHit) else jp_request(word)

    def _initial_ai(self, request):
        if self.explainer is None:
            return {"state": "off", "text": ""}
        cached = self.explainer.cached(request)
        if cached is not None:
            return {"state": "done", "text": cached}
        if self.explainer.is_running(request):
            return {"state": "loading", "text": ""}
        return {"state": None, "text": ""}

    def _no_key(self, manual):
        """没填密钥时手动点了就打开设置窗口。返回是否没有密钥。"""
        if has_key(self.explainer.cfg):
            return False
        if manual and self.open_settings:
            self.open_settings()
        return True

    def _ai_callback(self, target, key):
        def on_update(state, text):  # 在 AI 线程里调用，转给界面线程
            self.ai_updates.put((target, key, state, text))
            user32.PostMessageW(self.hwnd, WM_APP_AI, 0, 0)
        return on_update

    def _request_ai(self, word, manual=True):
        if self._no_key(manual):
            if manual:
                self.ai = {"state": "error", "text": f"还没有设置 {provider_of(self.explainer.cfg).label} API 密钥，"
                                                     "已打开设置窗口。填好后再点这里。"}
                self._render()
            return
        # 同一个词可能被重新查过（新对象），按上下文和位置匹配
        on_update = self._ai_callback("jp", (word.context, word.span))
        if self.explainer.explain(self._request_for(word), on_update, manual):
            self.ai = {"state": "loading", "text": ""}
        else:
            self.ai = {"state": None, "text": "自动解释太频繁，已暂停一会儿（点这里手动解释）"}
        self._render()

    def _request_english_ai(self):
        ev = self.en_view
        if self._no_key(True):
            ev["ai"] = {"state": "error",
                        "text": f"还没有设置 {provider_of(self.explainer.cfg).label} API 密钥，已打开设置窗口。"}
        else:
            self.explainer.explain(ev["request"], self._ai_callback("en", ev["request"]))
            ev["ai"] = {"state": "loading", "text": ""}
        self._render_english()

    def _on_ai_updates(self):
        jp_changed = en_changed = False
        while not self.ai_updates.empty():
            target, key, state, text = self.ai_updates.get_nowait()
            if not self.visible:
                continue
            if target == "jp":
                shown = self.view["word"] if self.view else None
                if shown and (shown.context, shown.span) == key:
                    self.ai = {"state": state, "text": text}
                    jp_changed = True
            elif self.en_view and self.en_view["request"] == key:
                self.en_view["ai"] = {"state": state, "text": text}
                en_changed = True
        if jp_changed:
            self._render()
        if en_changed:
            self._render_english()

    def hide(self):
        self.ai_due = None
        self._hide_english()
        if self.visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.visible = False
            self.shown_key = None

    def _update_layered(self, img, x, y, hwnd=None):
        hwnd = hwnd or self.hwnd
        w, h = img.size
        data = premultiplied_bgra(img)
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1,
                                  biBitCount=32, biCompression=0)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data, len(data))
        old = gdi32.SelectObject(mem, bitmap)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, 按像素透明
        user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(w, h)),
                                   mem, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
        # 每次都提到置顶窗口的最上层，否则会被之后才置顶的窗口盖住
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
