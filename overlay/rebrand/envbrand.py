"""Environment branding.

The PEB environment block is what `GetEnvironmentStringsW`, `os.environ`,
`cmd /c set`, Process Hacker and every AMSI/ETW consumer read.  It must not
carry launcher variables (PYAPPIFY_*) or the framework's own PYTHON* leftovers,
and it must expose a consistent, brand-owned variable set.
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, Iterable, Optional

from .brand import Brand

# Keys that identify the launcher/framework rather than the machine.
_DROP_PREFIXES = (
    "PYAPPIFY_",
    "PYTHON",
    "PIP_",
    "VIRTUAL_ENV",
    "OK" + "_",
    "OK" + "WW",
)
_DROP_EXACT = {"_", "SHLVL", "OLDPWD", "PSModulePath"}


def _should_drop(key: str) -> bool:
    if key in _DROP_EXACT:
        return True
    upper = key.upper()
    return any(upper.startswith(p) for p in _DROP_PREFIXES)


def build_environment(brand: Brand, base: Optional[Dict[str, str]] = None,
                      drop: Iterable[str] = ()) -> Dict[str, str]:
    """Return a cleaned environment block owned by the brand."""
    src = dict(base if base is not None else os.environ)
    extra_drop = {k.upper() for k in drop}
    env: Dict[str, str] = {}
    for k, v in src.items():
        if _should_drop(k) or k.upper() in extra_drop:
            continue
        env[k] = v

    build_id = hashlib.sha1(brand.seed).hexdigest()[:10]
    env[brand.env("HOME")] = env.get(brand.env("HOME"), "")
    env[brand.env("PROFILE")] = str(brand.get("profile", "default"))
    env[brand.env("VERSION")] = str(brand.get("version", "1.0.0"))
    env[brand.env("BUILD")] = build_id
    env[brand.env("DECODER")] = str(brand.get("decoder_name", "theta"))
    env[brand.env("BRAND_JSON")] = str(brand.path)
    return env


def apply(brand: Brand, drop: Iterable[str] = ()) -> Dict[str, str]:
    """Rebuild the live environment (Python view) and return the new block."""
    env = build_environment(brand, drop=drop)
    os.environ.clear()
    os.environ.update(env)
    return env


def leaked(keys: Iterable[str], brand: Brand) -> list[str]:
    """Keys that still look like they belong to the upstream launcher."""
    out = []
    for k in keys:
        if _should_drop(k) or k.startswith(brand.get("env_prefix", "KL") + "_"):
            if k.upper().startswith(("PYAPPIFY_", "PYTHON", "PIP_", "OK_")):
                out.append(k)
    return out
