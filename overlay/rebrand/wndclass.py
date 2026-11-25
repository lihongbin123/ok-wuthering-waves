"""Window class name remapping via a native USER32 IAT detour.

The rename rules live in `wndclass_native.c` and run without the Python GIL.
Python only locates the IAT slots and points them at the DLL exports, so USER32
calls made by Qt threads never re-enter the interpreter.

The ctypes-callback version this replaced deadlocked the Qt GUI thread inside
QWidget paint code ("Python 未响应"), because every window creation from a Qt
thread had to acquire the GIL.

Set `KL_WNDCLASS_MODE=off` to skip the detour entirely (fallback switch).
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Dict, Iterable, Optional

from .brand import Brand

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
_kernel32.LoadLibraryW.restype = wintypes.HMODULE
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
_kernel32.VirtualProtect.restype = wintypes.BOOL

PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04

_UP = "ok" + "-" + "script"

_targets = ("registerclassexw", "registerclassw", "createwindowexw", "createwindowexa", "unregisterclassw")

_EXPORTS = {
    "registerclassexw": "kl_RegisterClassExW",
    "registerclassw": "kl_RegisterClassW",
    "unregisterclassw": "kl_UnregisterClassW",
    "createwindowexw": "kl_CreateWindowExW",
    "createwindowexa": "kl_CreateWindowExA",
}

_ORIGINAL_ORDER = ("registerclassexw", "registerclassw", "unregisterclassw",
                   "createwindowexw", "createwindowexa")

_OFF_VALUES = ("off", "0", "false", "skip", "none")

_state: Dict[str, object] = {
    "installed": False,
    "patched": 0,
    "originals": {},
    "slots": [],
    "dll": None,
}


# --------------------------------------------------------------- PE plumbing
def _u16(base: int, off: int) -> int:
    return ctypes.c_uint16.from_address(base + off).value


def _u32(base: int, off: int) -> int:
    return ctypes.c_uint32.from_address(base + off).value


def _u64(base: int, off: int) -> int:
    return ctypes.c_uint64.from_address(base + off).value


def _cstr(base: int, rva: int) -> str:
    return ctypes.string_at(base + rva).decode("ascii", "replace")


def _import_slots(base: int):
    """Yield (dll_name, function_name, iat_entry_address) for a mapped module."""
    e_lfanew = _u32(base, 0x3C)
    opt = base + e_lfanew + 24
    magic = _u16(opt, 0)
    dd = opt + (112 if magic == 0x20B else 96)
    imp_rva = _u32(dd, 8)
    if not imp_rva:
        return
    desc = base + imp_rva
    while True:
        oft = _u32(desc, 0)
        name_rva = _u32(desc, 12)
        ft = _u32(desc, 16)
        if not oft and not name_rva and not ft:
            break
        dll = _cstr(base, name_rva).lower()
        thunk_rva = oft or ft
        idx = 0
        while thunk_rva:
            val = _u64(base, thunk_rva + idx * 8)
            if val == 0:
                break
            if not (val & 0x8000000000000000):
                hint_name = base + (val & 0x7FFFFFFF)
                fn = ctypes.string_at(hint_name + 2).decode("ascii", "replace")
                yield dll, fn.lower(), base + ft + idx * 8
            idx += 1
        desc += 20


def _write_slot(slot: int, value: int) -> None:
    old = wintypes.DWORD()
    if not _kernel32.VirtualProtect(ctypes.c_void_p(slot), 8, PAGE_READWRITE, ctypes.byref(old)):
        raise ctypes.WinError()
    try:
        ctypes.c_uint64.from_address(slot).value = value
    finally:
        restored = wintypes.DWORD()
        _kernel32.VirtualProtect(ctypes.c_void_p(slot), 8, old.value or PAGE_READONLY, ctypes.byref(restored))


# ------------------------------------------------------------------- helpers
def find_qwindows() -> Optional[str]:
    try:
        import PySide6
    except Exception:
        return None
    root = os.path.dirname(PySide6.__file__)
    candidate = os.path.join(root, "plugins", "platforms", "qwindows.dll")
    return candidate if os.path.isfile(candidate) else None


def find_qt_core() -> Optional[str]:
    """Qt6Core registers the tray-icon / observer helper windows, not Qt6Gui."""
    try:
        import PySide6
    except Exception:
        return None
    candidate = os.path.join(os.path.dirname(PySide6.__file__), "Qt6Core.dll")
    return candidate if os.path.isfile(candidate) else None


def native_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "wndclass_native.dll")


def declared_mode() -> str:
    return os.environ.get("KL_WNDCLASS_MODE", "native").strip().lower()


def _load_native(brand: Brand, path: Optional[str] = None):
    dll_path = path or native_path()
    dll = ctypes.WinDLL(dll_path)
    dll.kl_wnd_init.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR]
    dll.kl_wnd_init.restype = None
    dll.kl_wnd_init(str(brand.get("window_class_prefix", "Boreas")),
                     str(brand.get("product", "KL")),
                     str(brand.get("overlay_class", "Boreas.OverlaySurface")))
    dll.kl_wnd_set_originals.argtypes = [ctypes.c_void_p] * 5
    dll.kl_wnd_set_originals.restype = None
    return dll


def _hook_pointer(dll, name: str) -> int:
    return int(ctypes.cast(getattr(dll, name), ctypes.c_void_p).value or 0)


def _module_bases(path: Optional[str], extra: Iterable[str] = ()) -> list[int]:
    handle = _kernel32.GetModuleHandleW(path) or _kernel32.LoadLibraryW(path)
    if not handle:
        raise ctypes.WinError()
    bases = [int(handle)]
    for item in [find_qt_core(), *extra]:
        try:
            h = _kernel32.GetModuleHandleW(item) if item else _kernel32.GetModuleHandleW(None)
            if not h and item:
                h = _kernel32.LoadLibraryW(item)
            if h and int(h) not in bases:
                bases.append(int(h))
        except Exception:
            pass
    host = _kernel32.GetModuleHandleW(None)
    if host and int(host) not in bases:
        bases.append(int(host))
    return bases


def _collect_originals(base: int, originals: Dict[str, int]) -> None:
    for dll, fn, slot in _import_slots(base):
        if dll.split(".")[0] != "user32" or fn not in _targets:
            continue
        addr = _u64(slot, 0)
        if addr:
            originals.setdefault(fn, addr)


def _apply_module(base: int, hook_ptrs: Dict[str, int], originals: Dict[str, int]) -> int:
    patched = 0
    slots = _state.setdefault("slots", [])
    for dll, fn, slot in _import_slots(base):
        if dll.split(".")[0] != "user32" or fn not in _targets:
            continue
        original_addr = _u64(slot, 0)
        if not original_addr:
            continue
        originals.setdefault(fn, original_addr)
        hook = hook_ptrs.get(fn)
        if not hook:
            continue
        _write_slot(slot, hook)
        slots.append((slot, original_addr))
        patched += 1
    return patched


def uninstall() -> int:
    """Restore every patched IAT slot."""
    restored = 0
    for slot, original_addr in _state.get("slots", []):
        try:
            _write_slot(slot, original_addr)
            restored += 1
        except Exception:  # pragma: no cover
            pass
    _state["slots"] = []
    _state["installed"] = False
    return restored


def install(brand: Optional[Brand] = None, module: Optional[str] = None,
            extra: tuple = ()) -> int:
    """Detour USER32 class APIs through the native DLL.

    `extra` accepts module paths; the host interpreter image is patched too, so
    classes registered from Python (ctypes) are renamed as well.
    """
    if _state["installed"]:
        return int(_state["patched"])

    if declared_mode() in _OFF_VALUES:
        _state["installed"] = True
        _state["patched"] = 0
        return 0

    if brand is None:
        from .brand import bootstrap

        brand = bootstrap()

    path = module or find_qwindows()
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("qwindows.dll not found (PySide6 platform plugin missing)")

    # Pull in Qt6Gui first so the platform plugin's dependencies resolve, then
    # load the plugin ourselves - it must be loaded before QApplication exists.
    try:
        import PySide6.QtGui  # noqa: F401
    except Exception:
        pass

    dll = _load_native(brand)
    bases = _module_bases(path, extra)

    originals: Dict[str, int] = {}
    for base in bases:
        _collect_originals(base, originals)
    dll.kl_wnd_set_originals(*[originals.get(fn, 0) for fn in _ORIGINAL_ORDER])

    hook_ptrs = {fn: _hook_pointer(dll, _EXPORTS[fn]) for fn in _ORIGINAL_ORDER}
    patched = 0
    for base in bases:
        patched += _apply_module(base, hook_ptrs, originals)

    _state["originals"] = originals
    _state["dll"] = dll
    _state["patched"] = patched
    _state["installed"] = True
    import atexit

    atexit.register(uninstall)
    return patched


def self_test(brand: Optional[Brand] = None) -> int:
    """Create a real Qt window and read back its kernel class name."""
    from .brand import bootstrap

    brand = brand or bootstrap()
    n = install(brand)
    print(f"native hooks: {n} iat slots -> {native_path()}")

    from PySide6.QtWidgets import QApplication, QWidget, QMainWindow

    app = QApplication.instance() or QApplication([])
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]

    def klass(widget):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(int(widget.winId())), buf, 256)
        return buf.value

    w = QWidget()
    w.setWindowTitle("probe")
    w.show()
    app.processEvents()
    m = QMainWindow()
    m.show()
    app.processEvents()

    got = {"QWidget": klass(w), "QMainWindow": klass(m)}
    for k, v in got.items():
        print(f"{k:16s}: {v}")
    leaked = [v for v in got.values() if v.startswith("Qt") or _UP in v]
    print("RESULT           :", "PASS" if not leaked else f"FAIL {leaked}")
    for x in (w, m):
        x.hide()
    return 0 if not leaked else 1


if __name__ == "__main__":
    raise SystemExit(self_test())
