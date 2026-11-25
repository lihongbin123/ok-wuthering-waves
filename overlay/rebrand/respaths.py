"""Resource-root and runtime-name patch layer.

Everything the app derives from its own name at runtime:
  * Config.config_folder (configs/, profiles/)
  * overlay window class constant
  * single-instance mutex + %TEMP% pid file
  * logger / log file stem
  * template and task folders

Source rewriting already changes the literals.  This module is the belt-and-
braces layer for anything still computed at runtime, and the place where the
mutex hash gets its build salt.
"""

from __future__ import annotations

import hashlib
import sys
from typing import Any, Dict, List, Optional

from .brand import Brand


class _SaltedHash:
    """Drop-in stand-in for the hashlib module that salts md5/sha1 inputs."""

    def __init__(self, salt: bytes):
        self._salt = salt

    def _wrap(self, fn):
        def inner(data: Any = b"", *args, **kwargs):
            if isinstance(data, str):
                data = data.encode("utf-8")
            return fn(self._salt + bytes(data), *args, **kwargs)

        return inner

    def __getattr__(self, name):
        real = getattr(hashlib, name)
        if callable(real) and name in {"md5", "sha1", "sha256", "sha512"}:
            return self._wrap(real)
        return real


def patch_config_folder(brand: Brand) -> Optional[str]:
    try:
        from kl.util.config import Config  # type: ignore
    except Exception:
        return None
    folder = str(brand.get("config_folder", "profiles"))
    Config.config_folder = folder
    return folder


def patch_overlay_class(brand: Brand) -> List[str]:
    patched = []
    name = str(brand.get("overlay_class", "Boreas.OverlaySurface"))
    for mod_name, mod in list(sys.modules.items()):
        if mod is None or not mod_name.startswith("kl."):
            continue
        if hasattr(mod, "_window_class_name"):
            setattr(mod, "_window_class_name", name)
            patched.append(mod_name)
    return patched


def patch_mutex_salt(brand: Brand) -> bool:
    """Salt the md5 used for the instance mutex / temp pid file."""
    for mod_name in ("kl.util.process",):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        if not hasattr(mod, "hashlib"):
            continue
        setattr(mod, "hashlib", _SaltedHash(brand.derive("mutex-salt", 16)))
        return True
    return False


def patch_logger_name(brand: Brand) -> bool:
    """Rename the file-logger stem when the app already created its logger."""
    for mod_name in ("kl.util.logger",):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        default = getattr(mod, "config_logger", None)
        if default is None:
            continue
        want = str(brand.get("log_name", "kl"))

        def config_logger(config=None, name=want, _orig=default):
            return _orig(config, name=want)

        mod.config_logger = config_logger
        return True
    return False


def expected_names(brand: Brand) -> Dict[str, str]:
    return {
        "config_folder": str(brand.get("config_folder")),
        "templates_folder": str(brand.get("templates_folder")),
        "tasks_folder": str(brand.get("tasks_folder")),
        "screenshots_folder": str(brand.get("screenshots_folder")),
        "log_name": str(brand.get("log_name")),
        "overlay_class": str(brand.get("overlay_class")),
        "temp_prefix": str(brand.get("temp_prefix")),
    }


def install(brand: Brand) -> Dict[str, Any]:
    return {
        "config_folder": patch_config_folder(brand),
        "overlay_modules": patch_overlay_class(brand),
        "mutex_salted": patch_mutex_salt(brand),
        "logger_patched": patch_logger_name(brand),
        "expected": expected_names(brand),
    }
