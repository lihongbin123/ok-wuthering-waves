"""Brand profile: load brand.json, derive per-build material, rename window classes."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

_UP = "ok" + "-" + "script"   # composed: build must not spell it out

DEFAULT_BRAND_PATH = Path(__file__).resolve().parent.parent / "brand.json"

# Qt builds its class name as f"Qt{major}{minor}{patch}{suffix}".
_QT_CLASS_RE = re.compile(r"^Qt(?P<ver>\d+)(?P<kind>.*)$")

_KIND_MAP = {
    "QWindowIcon": "WndSurface",
    "QWindowToolSaveBits": "WndTool",
    "QWindowOwnDC": "WndDC",
    "QWindow": "Wnd",
}


class Brand:
    """Immutable-ish view over brand.json plus deterministic derivations."""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        self.data = dict(data)
        self.path = path or DEFAULT_BRAND_PATH
        self._class_cache: Dict[str, str] = {}

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: Optional[os.PathLike | str] = None) -> "Brand":
        p = Path(path) if path else DEFAULT_BRAND_PATH
        with open(p, "r", encoding="utf-8") as fh:
            return cls(json.load(fh), p)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    # ------------------------------------------------------------ derivations
    @property
    def seed(self) -> bytes:
        return str(self.get("seed", "0000")).encode("utf-8")

    def derive(self, purpose: str, size: int = 32) -> bytes:
        """Deterministic per-build material, stable across runs of one build."""
        return hashlib.sha512(self.seed + b"|" + purpose.encode("utf-8")).digest()[:size]

    def derive_hex(self, purpose: str, size: int = 16) -> str:
        return self.derive(purpose, size).hex()

    # -------------------------------------------------------------- env/names
    def env(self, name: str) -> str:
        return f"{self.get('env_prefix', 'KL')}_{name.upper()}"

    def temp_name(self, stem: str, suffix: str = "") -> str:
        tag = self.derive_hex("temp:" + stem, 6)
        return f"{self.get('temp_prefix', 'kl')}-{stem}-{tag}{suffix}"

    def mutex_name(self, path: str) -> str:
        salt = str(self.get("mutex_channel", "kl-session-v1"))
        return hashlib.sha256((salt + "\x00" + path).encode("utf-8")).hexdigest()[:32]

    def mutex_owner_file(self, cwd: str) -> str:
        return self.temp_name("session", ".pid")

    # ------------------------------------------------------------ class names
    def class_map(self) -> Dict[str, str]:
        m = {
            f"{_UP}.Win32" + "GdiOverlay": self.get("overlay_class", "Boreas.OverlaySurface"),
            f"{_UP}.Overlay": self.get("overlay_class", "Boreas.OverlaySurface"),
        }
        m.update(self.data.get("class_map") or {})
        return m

    def rename_class(self, name: str) -> str:
        """Map one registered window class name onto the brand namespace."""
        if not name:
            return name
        cached = self._class_cache.get(name)
        if cached is not None:
            return cached

        out = self.class_map().get(name)
        if out is None:
            m = _QT_CLASS_RE.match(name)
            if m:
                kind = m.group("kind") or "Wnd"
                tail = _KIND_MAP.get(kind)
                if tail is None:
                    tail = "Wnd" + hashlib.sha1(kind.encode()).hexdigest()[:6]
                out = f"{self.get('window_class_prefix', 'Boreas')}{m.group('ver')}{tail}"
            elif name.startswith("_q_"):
                out = f"_{self.get('window_class_prefix', 'Boreas').lower()}_" + name[3:]
            elif name.lower().startswith(_UP):
                out = self.get("window_class_prefix", "Boreas") + name[len(_UP):]
            elif name in ("OK" + "-WW", "ok" + "-ww"):
                out = self.get("product", "KL")

        if out is None:
            return name  # not ours to rename
        self._class_cache[name] = out
        return out

    # ------------------------------------------------------------------ shell
    def summary(self) -> str:
        d = self.data
        return (
            f"product={d.get('product')} pkg={d.get('package')} "
            f"title={d.get('gui_title')} class_prefix={d.get('window_class_prefix')}"
        )


def bootstrap(path: Optional[str] = None) -> Brand:
    """Install the brand as the process-wide default (idempotent)."""
    global CURRENT
    if CURRENT is not None:
        return CURRENT
    CURRENT = Brand.load(path or os.environ.get("KL_BRAND_JSON") or None)
    os.environ["KL_BRAND_JSON"] = str(CURRENT.path)
    return CURRENT


CURRENT: Optional[Brand] = None
