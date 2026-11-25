"""PEB / RTL_USER_PROCESS_PARAMETERS rewrite.

What userland actually sees about a process:
  * PEB->ProcessParameters->ImagePathName   (GetModuleFileNameW(NULL, ...) is the
    real loader path, but a lot of tooling reads this string instead)
  * PEB->ProcessParameters->CommandLine     (GetCommandLineW, WMI Win32_Process,
    NtQueryInformationProcess(ProcessCommandLineInformation=60))
  * PEB->ProcessParameters->CurrentDirectory.DosPath
  * PEB->ProcessParameters->Environment     (GetEnvironmentStringsW)
  * PEB->ProcessParameters->WindowTitle / DesktopInfo

All of them are plain UNICODE_STRINGs inside the PEB, so they can be repointed
at our own heap buffers after start-up.  The kernel-side image name
(NtQueryInformationProcess(ProcessImageFileName=27)) is NOT reachable from
user mode - that is what the decoy image path in `start_rebrand.py` is for.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Dict, Optional

_ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Explicit prototypes: the default c_int restype truncates 64-bit pointers.
_kernel32.GetProcessHeap.argtypes = []
_kernel32.GetProcessHeap.restype = wintypes.HANDLE
_ntdll.RtlAllocateHeap.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_size_t]
_ntdll.RtlAllocateHeap.restype = ctypes.c_void_p
_ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p
]
_ntdll.NtQueryInformationProcess.restype = ctypes.c_long

# x64 RTL_USER_PROCESS_PARAMETERS field offsets (Win10/Win11 stable layout).
OFF_CURRENT_DIR = 0x38
OFF_IMAGE_PATH = 0x60
OFF_COMMAND_LINE = 0x70
OFF_ENVIRONMENT = 0x80
OFF_WINDOW_TITLE = 0xB0
OFF_DESKTOP_INFO = 0xC0

# PEB->ProcessParameters
PEB_OFF_PROCESS_PARAMETERS = 0x20


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2_0", ctypes.c_void_p),
        ("Reserved2_1", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


def peb_address() -> int:
    pbi = _PROCESS_BASIC_INFORMATION()
    status = _ntdll.NtQueryInformationProcess(
        ctypes.c_void_p(-1),  # NtCurrentProcess
        0,  # ProcessBasicInformation
        ctypes.byref(pbi),
        ctypes.sizeof(pbi),
        None,
    )
    if status != 0 or not pbi.PebBaseAddress:
        raise OSError(f"NtQueryInformationProcess failed status=0x{status & 0xFFFFFFFF:08X}")
    return int(pbi.PebBaseAddress)


def process_parameters() -> int:
    peb = peb_address()
    return int(ctypes.c_void_p.from_address(peb + PEB_OFF_PROCESS_PARAMETERS).value or 0)


def _ustr_at(params: int, offset: int) -> UNICODE_STRING:
    return UNICODE_STRING.from_address(params + offset)


def read_ustr(params: int, offset: int) -> str:
    u = _ustr_at(params, offset)
    if not u.Buffer or not u.Length:
        return ""
    return ctypes.wstring_at(u.Buffer, u.Length // 2)


def _alloc(text: str) -> int:
    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    addr = _ntdll.RtlAllocateHeap(ctypes.c_void_p(_kernel32.GetProcessHeap()), 0, size)
    if not addr:
        raise MemoryError("RtlAllocateHeap failed")
    ctypes.memmove(addr, ctypes.addressof(buf), size)
    return int(addr)


def write_ustr(params: int, offset: int, text: str) -> None:
    """Repoint one UNICODE_STRING at a fresh heap copy of `text`."""
    u = _ustr_at(params, offset)
    addr = _alloc(text)
    u.Length = len(text) * 2
    u.MaximumLength = (len(text) + 1) * 2
    u.Buffer = ctypes.c_void_p(addr)


def write_ustr_inplace(params: int, offset: int, text: str) -> bool:
    """Overwrite the existing buffer in place.

    Required for CommandLine: GetCommandLineW() hands back a cached pointer to
    the original buffer, so repointing the UNICODE_STRING changes what the PEB
    says but not what the process itself is told.  Overwriting the bytes keeps
    both views identical.  Returns False when the new text does not fit.
    """
    u = _ustr_at(params, offset)
    if not u.Buffer:
        return False
    capacity = (u.MaximumLength or u.Length) // 2
    if capacity and len(text) >= capacity:
        return False
    buf = ctypes.create_unicode_buffer(text)
    ctypes.memmove(u.Buffer, ctypes.addressof(buf), (len(text) + 1) * 2)
    u.Length = len(text) * 2
    return True


def write_field(params: int, offset: int, text: str, prefer_inplace: bool = False) -> str:
    if prefer_inplace and write_ustr_inplace(params, offset, text):
        return "inplace"
    write_ustr(params, offset, text)
    return "repoint"


def scrub_original(offset: int, text: str = "") -> None:
    """Overwrite the pre-patch bytes of a field (defeats pointer-chasing scans)."""
    u = _ustr_at(process_parameters(), offset)
    if u.Buffer and u.Length:
        fill = ctypes.create_unicode_buffer(text or "\x00" * (u.Length // 2))
        ctypes.memmove(u.Buffer, ctypes.addressof(fill), u.Length)


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD),
    ]


_kernel32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
_kernel32.VirtualQuery.restype = ctypes.c_size_t


def _readable_span(addr: int, limit: int) -> int:
    """Bytes we may read from `addr` before leaving the committed region."""
    mbi = _MBI()
    if not _kernel32.VirtualQuery(ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        return 0
    end = int(mbi.BaseAddress or 0) + int(mbi.RegionSize or 0)
    return max(0, min(limit, end - addr))


def _find_terminator(data: bytes) -> int:
    idx = data.find(b"\x00\x00\x00\x00")
    while idx != -1:
        if idx % 2 == 0:
            return idx
        idx = data.find(b"\x00\x00\x00\x00", idx + 1)
    return -1


def _read_env_block(ptr: int, limit: int = 1 << 20) -> str:
    """Read a double-NUL terminated UTF-16 environment block.

    The block is a single heap allocation, so reading past its committed region
    faults; the span is derived from VirtualQuery instead of guessing a size.
    """
    data = b""
    try:
        while len(data) < limit:
            span = _readable_span(ptr + len(data), min(4096, limit - len(data)))
            if span <= 0:
                break
            data += ctypes.string_at(ptr + len(data), span)
            end = _find_terminator(data)
            if end != -1:
                return data[:end].decode("utf-16-le", "replace")
    except OSError:
        pass
    return data.decode("utf-16-le", "replace")


def read_environment() -> Dict[str, str]:
    params = process_parameters()
    ptr = ctypes.c_void_p.from_address(params + OFF_ENVIRONMENT).value
    out: Dict[str, str] = {}
    if not ptr:
        return out
    for entry in _read_env_block(ptr).split("\x00"):
        if not entry:
            continue
        k, _, v = entry.partition("=")
        out[k] = v
    return out


def write_environment(env: Dict[str, str]) -> None:
    """Replace PEB->ProcessParameters->Environment with a rebuilt block."""
    params = process_parameters()
    items = [f"{k}={v}" for k, v in sorted(env.items()) if k]
    block = "\x00".join(items) + "\x00\x00"
    buf = ctypes.create_unicode_buffer(block, len(block) + 1)
    size = ctypes.sizeof(buf)
    addr = _ntdll.RtlAllocateHeap(ctypes.c_void_p(_kernel32.GetProcessHeap()), 0, size)
    if not addr:
        raise MemoryError("RtlAllocateHeap failed (environment)")
    ctypes.memmove(addr, ctypes.addressof(buf), size)
    ctypes.c_void_p.from_address(params + OFF_ENVIRONMENT).value = addr
    ctypes.c_uint64.from_address(params + 0x3F0).value = len(block) * 2  # EnvironmentSize (best effort)


def snapshot() -> Dict[str, str]:
    params = process_parameters()
    return {
        "peb": f"0x{peb_address():X}",
        "params": f"0x{params:X}",
        "image_path": read_ustr(params, OFF_IMAGE_PATH),
        "command_line": read_ustr(params, OFF_COMMAND_LINE),
        "current_dir": read_ustr(params, OFF_CURRENT_DIR),
        "window_title": read_ustr(params, OFF_WINDOW_TITLE),
        "desktop_info": read_ustr(params, OFF_DESKTOP_INFO),
        "env_count": len(read_environment()),
    }


def apply(
    command_line: Optional[str] = None,
    image_path: Optional[str] = None,
    current_dir: Optional[str] = None,
    window_title: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    sync_python: bool = True,
    sync_argv: bool = True,
    sync_executable: bool = True,
    rewrite_argv0: bool = True,
) -> Dict[str, str]:
    """Rewrite the requested PEB fields and keep the Python view consistent."""
    params = process_parameters()
    modes: Dict[str, str] = {}

    # Python first: SetEnvironmentVariableW (used by os.environ) rebuilds the
    # PEB environment block, so our block has to be installed *after* it.
    if sync_python:
        if image_path and sync_executable:
            sys.executable = image_path
            sys._base_executable = image_path  # type: ignore[attr-defined]
        if command_line and sync_argv:
            argv = _argv_from_command_line(command_line)
            if argv:
                sys.argv = argv
                if hasattr(sys, "orig_argv"):
                    sys.orig_argv = argv
        elif rewrite_argv0 and image_path and sys.argv:
            sys.argv[0] = image_path
            if hasattr(sys, "orig_argv") and sys.orig_argv:
                sys.orig_argv[0] = image_path
        if environment is not None:
            os.environ.clear()
            os.environ.update(environment)

    # CommandLine / ImagePathName are also cached by the process itself, so try
    # in-place overwrite first and only fall back to repointing.
    if image_path:
        modes["image_path"] = write_field(params, OFF_IMAGE_PATH, image_path, prefer_inplace=True)
    if command_line:
        modes["command_line"] = write_field(params, OFF_COMMAND_LINE, command_line, prefer_inplace=True)
    if current_dir:
        modes["current_dir"] = write_field(params, OFF_CURRENT_DIR, current_dir, prefer_inplace=True)
    if window_title is not None:
        modes["window_title"] = write_field(params, OFF_WINDOW_TITLE, window_title, prefer_inplace=True)
    if environment is not None:
        write_environment(environment)
        modes["environment"] = "repoint"

    return snapshot() | {"modes": modes}


def _argv_from_command_line(command_line: str):
    import shlex

    try:
        return shlex.split(command_line, posix=False)
    except ValueError:
        return None


def self_test() -> int:
    """Patch our own PEB and verify through the public Win32 APIs."""
    before = snapshot()
    decoy_cl = r'"C:\ProgramData\KL\runtime\holder.exe" --serve --profile default'
    decoy_img = r"C:\ProgramData\KL\runtime\holder.exe"
    decoy_env = dict(os.environ)
    decoy_env.pop("PYAPPIFY" + "_APP_VERSION", None)
    decoy_env["KL_PROFILE"] = "default"

    apply(command_line=decoy_cl, image_path=decoy_img, environment=decoy_env)
    after = snapshot()

    api_cmd = ctypes.windll.kernel32.GetCommandLineW
    api_cmd.restype = ctypes.c_wchar_p
    api_line = api_cmd()

    api_env = ctypes.windll.kernel32.GetEnvironmentStringsW
    api_env.restype = ctypes.c_void_p
    env_ptr = api_env()
    env_dict = {}
    for entry in _read_env_block(env_ptr).split("\x00"):
        if entry:
            k, _, v = entry.partition("=")
            env_dict[k] = v
    ctypes.windll.kernel32.FreeEnvironmentStringsW(ctypes.c_void_p(env_ptr))
    env_ok = env_dict.get("KL_PROFILE") == "default" and ("PYAPPIFY" + "_APP_VERSION") not in env_dict
    peb_env_ok = read_environment().get("KL_PROFILE") == "default"

    ok = (
        after["command_line"] == decoy_cl
        and after["image_path"] == decoy_img
        and api_line == decoy_cl
        and env_ok
        and peb_env_ok
    )
    print("before   :", before["command_line"])
    print("after    :", after["command_line"])
    print("api cmd  :", api_line)
    print("api env  :", env_ok, "peb env:", peb_env_ok, "entries:", len(env_dict))
    print("RESULT   :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(self_test())
