"""Drop-in shim: put this file (and the `rebrand/` package) next to main.py.

    import start_rebrand
    start_rebrand.boot()
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from rebrand.start_rebrand import boot, decoy_paths, ensure_decoy_image  # noqa: E402,F401


def run(brand_path: str | None = None) -> None:
    """Boot once, tolerating repeated imports and non-GUI (headless) starts."""
    boot(brand_path or os.environ.get("KL_BRAND_JSON"))


__all__ = ["boot", "run", "decoy_paths", "ensure_decoy_image"]
