"""设备层：截图与输入。只用 ctypes + 标准库，不依赖旧框架。

- BitBltCapture: GDI 全屏截图（原型够用；HDR/独占全屏场景后续换 WGC）
- FakeCapture:  测试用纯色帧
- Input:        SendInput；dry_run 时只记录事件
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def enable_dpi_awareness() -> str:
    """声明 DPI 感知。否则 150% 缩放时 GetSystemMetrics/BitBlt 只有左上角 2560x1440。"""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:  # noqa: BLE001
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        if shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:  # noqa: BLE001
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:  # noqa: BLE001
        pass
    return "none"


DPI_MODE = enable_dpi_awareness()

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

VK = {
    "a": 0x41, "d": 0x44, "s": 0x53, "w": 0x57,
    "e": 0x45, "q": 0x51, "r": 0x52, "f": 0x46, "t": 0x54, "b": 0x42,
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "escape": 0x1B,
    "shift": 0x10, "lshift": 0xA0, "ctrl": 0x11, "lctrl": 0xA2,
    "alt": 0x12, "lalt": 0xA4,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
}


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


@dataclass
class Frame:
    width: int
    height: int
    bgra: bytes
    ts: float

    def size(self) -> tuple[int, int]:
        return self.width, self.height


class FakeCapture:
    def __init__(self, width: int = 1920, height: int = 1080, fill: int = 0):
        self.width = width
        self.height = height
        self.fill = fill

    def grab(self) -> Frame:
        pixel = bytes((self.fill, self.fill, self.fill, 255))
        return Frame(self.width, self.height, pixel * (self.width * self.height), time.time())


class BitBltCapture:
    def __init__(self):
        self._screen = user32.GetDC(0)

    def grab(self) -> Frame:
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        mem = gdi32.CreateCompatibleDC(self._screen)
        bmp = gdi32.CreateCompatibleBitmap(self._screen, width, height)
        gdi32.SelectObject(mem, bmp)
        gdi32.BitBlt(mem, 0, 0, width, height, self._screen, 0, 0, SRCCOPY)

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height          # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(mem, bmp, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        return Frame(width, height, buffer.raw, time.time())

    def close(self) -> None:
        if self._screen:
            user32.ReleaseDC(0, self._screen)
            self._screen = None


class WindowCapture:
    """窗口级抓取（PrintWindow）。用于 BitBlt 抓不到 HUD 图层时的对照。"""
    PW_RENDERFULLCONTENT = 0x2

    def __init__(self, title: str | None = None, pid: int | None = None,
                 pw_flag: int = 0x2):
        self.hwnd = find_window(title_contains=title, pid=pid)
        if not self.hwnd:
            raise RuntimeError(f"window not found: title={title} pid={pid}")
        self.pw_flag = pw_flag

    def grab(self) -> Frame:
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        width, height = int(rect.right), int(rect.bottom)
        if width <= 0 or height <= 0:
            raise RuntimeError("window has no client area")
        hdc = user32.GetDC(self.hwnd)
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
        gdi32.SelectObject(mem, bmp)
        user32.PrintWindow(self.hwnd, mem, self.pw_flag)
        frame = _dib_frame(mem, bmp, width, height)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(self.hwnd, hdc)
        return frame

    def close(self) -> None:
        return None


def activate_window(hwnd: int) -> bool:
    """强制把窗口带到前台（全屏游戏也能生效），返回是否成功。"""
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)
    current = user32.GetForegroundWindow()
    tid_cur = user32.GetWindowThreadProcessId(current, None)
    tid_tgt = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(tid_cur, tid_tgt, True)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    user32.AttachThreadInput(tid_cur, tid_tgt, False)
    for _ in range(10):
        time.sleep(0.15)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


def ensure_foreground(hwnd: int, retries: int = 12, delay: float = 0.4) -> bool:
    """严格前台校验：连续尝试激活，全部失败则返回 False（调用方必须放弃发键）。"""
    for _ in range(retries):
        if hwnd and activate_window(hwnd):
            return True
        time.sleep(delay)
    return False


def find_window(title_contains: str | None = None, pid: int | None = None) -> int:
    """按进程号或标题关键字找顶层可见窗口。"""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if pid is not None and owner_pid.value != pid:
            return True
        if title_contains:
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buffer, 256)
            if title_contains not in buffer.value:
                return True
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        if rect.right > 320 and rect.bottom > 240:
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def _dib_frame(mem, bmp, width: int, height: int) -> Frame:
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = BI_RGB
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bmp, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS)
    return Frame(width, height, buffer.raw, time.time())


class Input:
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.events: list[dict] = []

    def key(self, name: str, hold_ms: int = 0) -> None:
        vk = VK.get(name.lower(), None)
        if vk is None:
            raise ValueError(f"unknown key: {name}")
        self.events.append({"t": time.time(), "type": "key", "key": name})
        if self.dry_run:
            return
        _send_vk(vk, up=False)
        if hold_ms:
            time.sleep(hold_ms / 1000)
            _send_vk(vk, up=True)

    def click(self, x: int | None = None, y: int | None = None) -> None:
        """默认在当前鼠标位置点击（战斗里的单击就是这样），给坐标才移动。"""
        self.events.append({"t": time.time(), "type": "click", "x": x, "y": y})
        if self.dry_run:
            return
        if x is not None and y is not None:
            user32.SetCursorPos(int(x), int(y))
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)

    def mouse_down(self) -> None:
        self.events.append({"t": time.time(), "type": "mouse_down"})
        if not self.dry_run:
            user32.mouse_event(0x0002, 0, 0, 0, 0)

    def mouse_up(self) -> None:
        self.events.append({"t": time.time(), "type": "mouse_up"})
        if not self.dry_run:
            user32.mouse_event(0x0004, 0, 0, 0, 0)

    def macro_spam(self, times: int = 3, interval_ms: int = 30) -> None:
        """连点：按固定间隔连发左键（角色代码里的连击/普攻补点）。"""
        for _ in range(max(1, int(times))):
            self.click()
            time.sleep(max(0, interval_ms) / 1000)
        self.events.append({"t": time.time(), "type": "macro_spam",
                            "times": int(times), "interval_ms": int(interval_ms)})


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT),
                    ("hi", _HARDWAREINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


_INPUT_EXPECTED_SIZE = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
if ctypes.sizeof(_INPUT) != _INPUT_EXPECTED_SIZE:
    # INPUT 必须包含 MOUSEINPUT/HARDWAREINPUT 联合体，否则 SendInput 返回 0 (err 87)
    raise RuntimeError(
        f"INPUT struct size {ctypes.sizeof(_INPUT)} != {_INPUT_EXPECTED_SIZE}")


def _send_vk(vk: int, up: bool) -> None:
    """按扫描码发送（DirectInput/游戏 RawInput 才认），失败再退回 VK。"""
    scan = user32.MapVirtualKeyW(vk, 0)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp.ki = _KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags,
                         time=0, dwExtraInfo=0)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if not sent:
        inp = _INPUT(type=INPUT_KEYBOARD)
        inp.ki = _KEYBDINPUT(wVk=vk, wScan=0,
                             dwFlags=KEYEVENTF_KEYUP if up else 0,
                             time=0, dwExtraInfo=0)
        sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if not sent:
        raise RuntimeError(
            f"SendInput failed vk={vk} up={up} err={ctypes.windll.kernel32.GetLastError()}")
