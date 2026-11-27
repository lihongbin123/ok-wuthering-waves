"""Single entry point executed before anything else in the app.

Wire it in as the first statement of `main.py`:

    import start_rebrand
    start_rebrand.boot()

Order matters:
  1. brand profile
  2. environment block (PEB env, os.environ)
  3. window-class detours (must be in place before QApplication is built)
  4. PEB fields (command line / image path / cwd / title)
  5. runtime resource names

Everything is guarded by an env marker so a double import cannot re-hook.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

from . import envbrand, peb, respaths, wndclass
from .brand import Brand, bootstrap

BOOT_FLAG = "KL_BOOTED"
_DLL_NEEDLES = ("python3", "python31", "vcruntime", "ucrtbase", "api-ms-win")


def decoy_paths(brand: Brand, root: Optional[Path] = None) -> Dict[str, str]:
    """A plausible install root that has nothing to do with the upstream name."""
    base = Path(root or os.environ.get("ProgramData", r"C:\ProgramData"))
    folder = base / str(brand.get("product", "KL")) / "runtime"
    exe = folder / f"{str(brand.get('product_short', 'kl'))}.exe"
    return {
        "root": str(folder),
        "image": str(exe),
        "command_line": f'"{exe}" --profile {brand.get("profile", "default")} --service',
        "window_title": str(brand.get("gui_title", "KL")),
    }


def ensure_decoy_image(source_exe: str, brand: Brand) -> Optional[str]:
    """Copy the interpreter (plus its runtime DLLs) to the decoy path."""
    src = Path(source_exe)
    if not src.is_file():
        return None
    target = Path(decoy_paths(brand)["image"])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.stat().st_size != src.stat().st_size:
            shutil.copy2(src, target)
        for dll in src.parent.glob("*.dll"):
            low = dll.name.lower()
            if low.startswith(_DLL_NEEDLES) and not (target.parent / dll.name).exists():
                shutil.copy2(dll, target.parent / dll.name)
        return str(target)
    except OSError:
        return None


def boot(brand_path: Optional[str] = None, use_decoy_cwd: bool = False,
         patch_peb: bool = True) -> Brand:
    if os.environ.get(BOOT_FLAG) == "1":
        return bootstrap(brand_path)

    brand = bootstrap(brand_path)
    result: Dict[str, object] = {"brand": brand.summary()}

    env = envbrand.apply(brand)
    result["env_keys"] = len(env)

    try:
        result["window_slots"] = wndclass.install(brand)
    except Exception as exc:  # keep the app starting even if the hook fails
        result["window_slots"] = f"error: {exc}"

    if patch_peb:
        decoy = decoy_paths(brand)
        # Materialise the decoy interpreter before touching the PEB: a PEB that
        # points at a non-existent file is itself a tell, and starting from the
        # decoy image is what makes the kernel image path clean as well.
        if not os.path.isfile(decoy["image"]):
            ensure_decoy_image(sys.executable, brand)
        fields: Dict[str, str] = {"window_title": decoy["window_title"]}
        if os.path.isfile(decoy["image"]):
            fields["image_path"] = decoy["image"]
            fields["command_line"] = decoy["command_line"]
        if use_decoy_cwd:
            fields["current_dir"] = decoy["root"]
        try:
            result["peb"] = peb.apply(environment=env, sync_python=True, sync_argv=False,
                                       sync_executable=False, rewrite_argv0=False, **fields)
        except Exception as exc:
            result["peb"] = f"error: {exc}"

    result["runtime"] = respaths.install(brand)
    os.environ[BOOT_FLAG] = "1"
    if os.environ.get("KL_VERBOSE"):
        import json

        print(json.dumps(result, ensure_ascii=False, default=str))
    return brand


if __name__ == "__main__":
    os.environ["KL_VERBOSE"] = "1"
    info = boot()
    print("brand:", info.summary())
    print("peb  :", peb.snapshot())
    print("env  :", sorted(k for k in os.environ if k.startswith("KL_")))
    print("argv :", sys.argv)
