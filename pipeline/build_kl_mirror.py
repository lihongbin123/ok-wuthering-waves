"""KL 镜像构建：上游 tag -> 改名/品牌 -> 合并 overlay -> 输出可直接发布的 working 树。

在 CI 里由 .github/workflows/kl-mirror.yml 调用；本地也可单独跑：

    python pipeline/build_kl_mirror.py \
        --repo . --tag v3.8.16 \
        --overlay overlay --out build \
        --report build-report.json

约定：
  --repo    已 fetch 到目标 tag 的 git 仓库（默认当前目录）
  --tag     上游 tag（如 v3.8.16）
  --overlay 我们的增量目录，按 working 内的最终路径组织
  --out     输出目录（内容 = working 根）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

TEXT_EXT = {".py", ".pyi", ".txt", ".md", ".json", ".yml", ".yaml", ".toml",
            ".cfg", ".ini", ".po", ".ts", ".c", ".h", ".gitignore", ".css", ".html"}

_PKG_QUALIFIED = re.compile(
    r"(?<![\w.])ok\.(util|core|ui|gui|device|capture|task|feature|ocr|notification|"
    r"update|rotypes|alas|third_party|assets|test)\b"
)
_FROM_OK = re.compile(r"(?<![\w.])from\s+ok(?=[\s.])")
_IMPORT_OK_DOT = re.compile(r"(?<![\w.])import\s+ok\.")
_IMPORT_OK_LINE = re.compile(r"(?m)^(?P<indent>[ \t]*)import\s+ok\s*$")
_PATH_OK = re.compile(r"(?<![\w/])ok/")

WORKSHOP_OPTION = """
workshop_autoupdate_config_option = ConfigOption('Workshop Autoupdate', {
    'Daily Sync on Startup': True,
}, description='每天首次启动时后台增量同步工坊代码（SHA256 只增不覆盖）', config_description={
    'Daily Sync on Startup': '默认开启：每天第一次启动 KL 时后台同步一次，同一天后续启动跳过；'
                             '关闭后不再自动同步。也可设置环境变量 KL_WORKSHOP_AUTOUPDATE=0 强制关闭。',
}, show_at_tab=True, icon=Icon.GAME)
"""

MAIN_PY = """if __name__ == '__main__':
    # 去指纹层入口：必须在 import PySide6 / 读命令行之前执行
    import start_rebrand
    start_rebrand.boot()

    from config import config
    from kl import OK

    ok = OK(config)
    ok.start()
"""

MAIN_DEBUG_PY = """if __name__ == '__main__':
    import start_rebrand
    start_rebrand.boot()

    from config import config
    from kl import OK

    config['debug'] = True
    ok = OK(config)
    ok.start()
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def rmtree(path: Path) -> None:
    def _onexc(func, target, exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    if path.exists():
        shutil.rmtree(path, onexc=_onexc)


def run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd))}\n"
                           f"{p.stdout}\n{p.stderr}")
    return p


def extract(repo: Path, tag: str, out: Path) -> None:
    rmtree(out)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "upstream.tar"
        run(["git", "-C", repo, "archive", "--format=tar", f"--output={tar_path}", tag])
        with tarfile.open(tar_path, "r") as tf:
            tf.extractall(out)


def iter_text(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in TEXT_EXT or name == ".gitignore":
                yield p


def rewrite_upstream(out: Path, brand: dict) -> dict:
    tokens = [(str(a), str(b)) for a, b in brand.get("tokens", [])]
    tokens.sort(key=lambda kv: -len(kv[0]))
    files = 0
    pkg_hits = 0
    for p in list(iter_text(out)):
        try:
            text = p.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        original = text
        for old, new in tokens:
            text = text.replace(old, new)
        text = _FROM_OK.sub("from kl", text)
        text = _IMPORT_OK_DOT.sub("import kl.", text)
        text = _IMPORT_OK_LINE.sub(lambda m: f"{m.group('indent')}import kl as ok", text)
        text, n = _PKG_QUALIFIED.subn(lambda m: f"kl.{m.group(1)}", text)
        text = _PATH_OK.sub("kl/", text)
        pkg_hits += n
        if text != original:
            p.write_text(text, "utf-8")
            files += 1

    old_dir = out / "ok"
    new_dir = out / "kl"
    if old_dir.is_dir() and not new_dir.exists():
        old_dir.rename(new_dir)

    # 目录/文件名里的品牌词
    for p in sorted([x for x in out.rglob("*") if x.is_dir()], key=lambda x: len(x.parts), reverse=True):
        if not p.exists():
            continue
        name = p.name
        for old, new in tokens:
            name = name.replace(old, new)
        if name != p.name and not (p.parent / name).exists():
            p.rename(p.parent / name)
    return {"files_changed": files, "package_hits": pkg_hits}


def copy_overlay(out: Path, overlay: Path) -> int:
    count = 0
    for p in overlay.rglob("*"):
        if p.is_dir() or "__pycache__" in p.parts or p.suffix in {".pyc", ".pyo"}:
            continue
        rel = p.relative_to(overlay)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        count += 1
    return count


def write_runtime_brand(out: Path, brand: dict) -> None:
    runtime = {k: v for k, v in brand.items() if k != "tokens"}
    (out / "brand.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", "utf-8")


def patch_config(out: Path) -> list[str]:
    p = out / "config.py"
    text = p.read_text("utf-8")
    changes = []
    if "workshop_autoupdate_config_option" not in text:
        anchor = "\nconfig = {"
        if anchor not in text:
            raise RuntimeError("config.py 缺少 config 字典锚点")
        text = text.replace(anchor, WORKSHOP_OPTION + anchor, 1)
        changes.append("+ workshop option")

    old_global = ("'global_configs': [basic_config_option, key_config_option, "
                  "char_config_option, monthly_card_config_option],")
    new_global = ("'global_configs': [basic_config_option, key_config_option, char_config_option, "
                  "monthly_card_config_option, workshop_autoupdate_config_option],")
    if old_global in text:
        text = text.replace(old_global, new_global, 1)
        changes.append("global_configs += workshop")
    elif "workshop_autoupdate_config_option]" not in text:
        raise RuntimeError("config.py global_configs 锚点不匹配")

    if "'custom_tabs'" not in text:
        anchor = "    'global_configs':"
        idx = text.index(anchor)
        line_end = text.index("\n", idx) + 1
        tabs = (
            "    'custom_tabs': [\n"
            '        ["src.gui.CharacterCodeTab", "CharacterCodeTab"],\n'
            '        ["src.gui.MultiAccountConfigTab", "MultiAccountConfigTab"],\n'
            '        ["src.gui.DashboardTab", "DashboardTab"],\n'
            '        ["src.recorder.RecorderTab", "AxisRecorderTab"],\n'
            "    ],\n"
        )
        text = text[:line_end] + tabs + text[line_end:]
        changes.append("+ custom_tabs")

    old_trigger = '        ["src.task.MouseResetTask", "MouseResetTask"],\n    ],'
    new_trigger = ('        ["src.task.MouseResetTask", "MouseResetTask"],\n'
                   '        ["src.recorder.AxisRecorder", "AxisRecorderTask"],\n    ],')
    if old_trigger in text:
        text = text.replace(old_trigger, new_trigger, 1)
        changes.append("trigger += AxisRecorder")

    if '["src.task.TacetEliminationTask", "TacetEliminationTask"]' not in text:
        anchor = '        ["src.task.GardenTask", "GardenTask"],\n'
        if anchor in text:
            text = text.replace(anchor, anchor + '        ["src.task.TacetEliminationTask", '
                                '"TacetEliminationTask"],\n', 1)
            changes.append("+ TacetElimination")

    if "'plugin': False," in text:
        text = text.replace("'plugin': False,", "'plugin': True,", 1)
        changes.append("plugin=True")

    p.write_text(text, "utf-8")
    return changes


def patch_translator(out: Path) -> bool:
    p = out / "kl" / "ui" / "qt" / "util" / "app.py"
    text = p.read_text("utf-8")
    if "_translation_dirs" in text:
        return False
    if not text.lstrip().startswith("import os"):
        text = "import os\n" + text
    helper = '''
def _translation_dirs():
    """优先加载源码目录里的 .qm，避免 resources.py 里的旧副本静默失败。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "i18n"), ":/i18n"


'''
    anchor = "def install_translator(app):"
    if anchor not in text:
        raise RuntimeError("app.py 缺少 install_translator 锚点")
    text = text.replace(anchor, helper + anchor, 1)
    old = '''    translator = QTranslator(app)
    if translator.load(locale.name(), ":/i18n"):
        app.installTranslator(translator)
        _translator = translator
        logger.info(f"translator install success {locale}, {QCoreApplication.translate('MainWindow', 'Debug')}")
'''
    if old not in text:
        raise RuntimeError("app.py translator 锚点不匹配")
    new = '''    translator = QTranslator(app)
    loaded_from = None
    for source in _translation_dirs():
        if translator.load(locale.name(), source):
            loaded_from = source
            break
    if loaded_from is not None:
        app.installTranslator(translator)
        _translator = translator
        logger.info(f"translator install success {locale} from {loaded_from}, "
                    f"{QCoreApplication.translate('MainWindow', 'Debug')}")
'''
    p.write_text(text.replace(old, new, 1), "utf-8")
    return True


def patch_char_factory(out: Path) -> bool:
    cfg = out / "src" / "char" / "CharFactory.py"
    text = cfg.read_text("utf-8")
    if "apply_team_char_classes" in text:
        return False
    anchor = "from src.char.Zhezhi import Zhezhi"
    if anchor not in text:
        raise RuntimeError("CharFactory 缺少 Zhezhi 导入锚点")
    text = text.replace(anchor, anchor + "\nfrom src.char.CustomCharLoader import "
                        "load_team_char_class, normalize_team", 1)
    block = (HERE / "patches" / "char_factory_apply.inc.py").read_text("utf-8").rstrip() + "\n\n\n"
    insert_at = text.index("def is_float")
    text = text[:insert_at] + block + text[insert_at:]
    cfg.write_text(text, "utf-8")
    return True


def _parse_po(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    msgid = msgstr = None
    current = None

    def flush():
        nonlocal msgid, msgstr
        if msgid is not None and msgstr is not None:
            entries[msgid] = msgstr
        msgid = msgstr = None

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            if msgid is not None and msgstr is not None:
                flush()
            current, msgid = "id", json.loads(line[6:])
        elif line.startswith("msgstr "):
            current, msgstr = "str", json.loads(line[7:])
        elif line.startswith('"') and current == "id":
            msgid += json.loads(line)
        elif line.startswith('"') and current == "str":
            msgstr += json.loads(line)
    flush()
    return entries


def _write_mo(entries: dict[str, str], path: Path) -> None:
    import struct

    items = sorted(entries.items())
    keys = [k.encode("utf-8") for k, _ in items]
    vals = [v.encode("utf-8") for _, v in items]
    n = len(items)
    header_size = 28
    key_off = header_size + 8 * n * 2
    key_table = key_data = val_table = val_data = b""
    kpos = key_off
    for k in keys:
        key_table += struct.pack("<II", len(k), kpos)
        key_data += k + b"\x00"
        kpos += len(k) + 1
    vpos = kpos
    for v in vals:
        val_table += struct.pack("<II", len(v), vpos)
        val_data += v + b"\x00"
        vpos += len(v) + 1
    header = struct.pack("<IIIIIII", 0x950412DE, 0, n, header_size,
                         header_size + 8 * n, 0, header_size + 16 * n)
    path.write_bytes(header + key_table + val_table + key_data + val_data)


def compile_i18n(out: Path) -> list[str]:
    done = []
    for po in out.glob("i18n/*/LC_MESSAGES/ok.po"):
        _write_mo(_parse_po(po.read_text("utf-8")), po.with_suffix(".mo"))
        done.append(str(po.relative_to(out)))
    return done


def sanitize(out: Path) -> int:
    removed = 0
    for p in list(out.rglob("__pycache__")):
        rmtree(p)
        removed += 1
    for p in list(out.rglob("*")):
        if p.is_file() and ("pulse-generated-original" in p.name or p.suffix in {".pyc", ".pyo"}):
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--overlay", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--brand", default=str(HERE / "brand.json"))
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    overlay = Path(args.overlay).resolve()
    out = Path(args.out).resolve()
    brand = json.loads(Path(args.brand).read_text("utf-8"))

    extract(repo, args.tag, out)
    stats = rewrite_upstream(out, brand)
    overlay_files = copy_overlay(out, overlay)
    write_runtime_brand(out, brand)
    cfg_changes = patch_config(out)
    translator = patch_translator(out)
    char_patched = patch_char_factory(out)
    i18n = compile_i18n(out)
    removed = sanitize(out)
    (out / "main.py").write_text(MAIN_PY, "utf-8")
    (out / "main_debug.py").write_text(MAIN_DEBUG_PY, "utf-8")

    report = {
        "tag": args.tag,
        "overlay_files": overlay_files,
        "rewrite": stats,
        "config_changes": cfg_changes,
        "translator_patched": translator,
        "char_factory_patched": char_patched,
        "i18n": i18n,
        "sanitized": removed,
        "python_files": sum(1 for _ in out.rglob("*.py")),
        "total_files": sum(1 for p in out.rglob("*") if p.is_file()),
    }
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    log(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
