"""KL 自建更新器：只依赖 git + app.json，不经过 PyAppify launcher。

版本规则：远端 tag 必须是四段纯数字 ``v<上游三段>.<构建序号>``，例如
``v3.8.15.1``。``git ls-remote --tags`` 取最大值，与 ``current_version``
按元组比较；联网失败只写日志，不影响启动。

更新流程：校验 tag -> 备份 working（不含用户目录）-> 拉取 tag 树到临时目录
-> 校验 config.py 仍是 KL 品牌 -> 合并用户数据 -> 原地替换 -> 回写 app.json。
任一步失败都会从备份回滚。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
from datetime import datetime
from pathlib import Path

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)$")
PRESERVE = ("configs", "logs", "screenshots", "cache")
SKIP_COPY = set(PRESERVE) | {"__pycache__", ".pytest_cache", ".git"}
logger = None


def log(message: str) -> None:
    if logger is not None:
        logger.info("[kl-updater] %s", message)
    else:
        print("[kl-updater]", message)


def working_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def install_root() -> Path:
    override = os.environ.get("KL_UPDATER_INSTALL_ROOT")
    if override:
        return Path(override).resolve()
    # working -> kl -> apps -> data -> <install>
    return working_dir().parents[3]


def app_json_path() -> Path:
    return working_dir().parent / "app.json"


def _git(args: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    cmd = ["git"]
    proxy = os.environ.get("KL_GIT_PROXY")
    if proxy:
        cmd += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    p = subprocess.run(cmd + args, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()[:500]}")
    return p.stdout


def read_app_json() -> dict:
    with open(app_json_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


def git_url(app: dict) -> str:
    profiles = app.get("profiles") or []
    current = app.get("current_profile")
    for profile in profiles:
        if profile.get("name") == current and profile.get("git_url"):
            return profile["git_url"]
    for profile in profiles:
        if profile.get("git_url"):
            return profile["git_url"]
    raise RuntimeError("app.json 里没有可用的 git_url")


def parse_tag(tag: str) -> tuple[int, int, int, int] | None:
    match = TAG_RE.match(tag)
    return tuple(int(x) for x in match.groups()) if match else None


def current_version(app: dict) -> tuple[int, int, int, int]:
    raw = str(app.get("current_version") or "0.0.0").lstrip("v")
    parts = [int(p) for p in re.findall(r"\d+", raw)[:4]]
    return tuple(parts + [0] * (4 - len(parts)))


def list_tags(url: str) -> list[str]:
    out = _git(["ls-remote", "--tags", url], timeout=60)
    tags = []
    for line in out.splitlines():
        ref = line.split("\t", 1)[-1].strip()
        if ref.startswith("refs/tags/") and not ref.endswith("^{}"):
            tag = ref[len("refs/tags/"):]
            if parse_tag(tag):
                tags.append(tag)
    return sorted(tags, key=parse_tag)


def check() -> tuple[str, str, bool]:
    """返回 (最新 tag, 当前版本, 是否有更新)。"""
    app = read_app_json()
    current = str(app.get("current_version") or "")
    tags = list_tags(git_url(app))
    if not tags:
        return "", current, False
    latest = tags[-1]
    return latest, current, parse_tag(latest) > current_version(app)


def _copy_tree(src: Path, dst: Path, ignore=shutil.ignore_patterns(*SKIP_COPY)) -> None:
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def _backup(working: Path, backup_dir: Path) -> Path:
    target = backup_dir / "working"
    _copy_tree(working, target)
    return target


def _rollback(working: Path, backup_working: Path) -> None:
    for item in working.iterdir():
        if item.name in PRESERVE:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    _copy_tree(backup_working, working, ignore=None)


def _extract_tag(url: str, tag: str, dest: Path) -> None:
    repo = dest / ".repo"
    tree = dest / "tree"
    tree.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet", str(repo)])
    _git(["fetch", "--no-tags", "--depth=1", url, f"refs/tags/{tag}:refs/tags/{tag}"], cwd=repo)
    archive = dest / "tree.tar"
    _git(["archive", "--format=tar", "--output", str(archive), tag], cwd=repo)
    with tarfile.open(archive) as tar:
        tar.extractall(tree, filter="data")
    archive.unlink(missing_ok=True)
    shutil.rmtree(repo, ignore_errors=True)


def update(tag: str) -> dict:
    app = read_app_json()
    url = git_url(app)
    if tag not in list_tags(url):
        raise RuntimeError(f"远端不存在 tag：{tag}")

    working = working_dir()
    root = install_root()
    tools = root / "build-tools"
    tools.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = tools / f"backup-kl-update-{stamp}"

    backup_working = _backup(working, backup_dir)
    log(f"backup -> {backup_dir}")

    staging = Path(tempfile.mkdtemp(prefix=f"kl-update-{tag}-", dir=str(tools)))
    try:
        _extract_tag(url, tag, staging)
        tree = staging / "tree"
        config_text = (tree / "config.py").read_text(encoding="utf-8", errors="replace")
        if "gui_title': 'KL'" not in config_text and "gui_title\": \"KL\"" not in config_text:
            raise RuntimeError("新版本的 config.py 不含 KL 品牌，tag 可能取错，已中止")

        for name in PRESERVE:
            source = working / name
            if source.exists():
                _copy_tree(source, tree / name)

        # 保留 tag 树里没有的顶层用户数据
        for item in working.iterdir():
            if item.name in SKIP_COPY or (tree / item.name).exists():
                continue
            if item.is_dir():
                _copy_tree(item, tree / item.name)
            else:
                shutil.copy2(item, tree / item.name)

        _rollback(working, backup_working)
        _copy_tree(tree, working, ignore=None)

        app["current_version"] = tag
        versions = list(app.get("available_versions") or [])
        if tag not in versions:
            versions.append(tag)
        app["available_versions"] = versions
        tmp = app_json_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(app, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, app_json_path())
        log(f"updated to {tag}")
        return {"updated": True, "tag": tag, "backup": str(backup_dir)}
    except Exception:
        log("更新失败，开始回滚")
        _rollback(working, backup_working)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


_bridge = None


def start_background_check() -> None:
    """启动后台检查；有 Qt 就弹对话框，否则只写日志。"""
    global _bridge
    try:
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QMessageBox
    except Exception:
        threading.Thread(target=lambda: _log_only_check(), daemon=True).start()
        return

    class _Bridge(QObject):
        found = Signal(str, str)

    _bridge = _Bridge()

    def on_found(latest: str, current: str) -> None:
        answer = QMessageBox.question(
            None, "KL 更新",
            f"发现新版本 {latest}（当前 {current}）。\n\n是否现在更新？更新完成后需要重启 KL。")
        if answer != QMessageBox.Yes:
            return
        try:
            result = update(latest)
            QMessageBox.information(
                None, "KL 更新",
                f"已更新到 {result['tag']}，请重启 KL。\n备份：{result['backup']}")
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(None, "KL 更新失败", str(error))

    _bridge.found.connect(on_found)

    def worker() -> None:
        try:
            latest, current, needs = check()
            if needs:
                _bridge.found.emit(latest, current)
        except Exception as error:  # noqa: BLE001
            log(f"检查失败：{error}")

    threading.Thread(target=worker, daemon=True).start()


def _log_only_check() -> None:
    try:
        latest, current, needs = check()
        log(f"latest={latest or '-'} current={current or '-'} update={needs}")
    except Exception as error:  # noqa: BLE001
        log(f"检查失败：{error}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", metavar="TAG")
    parser.add_argument("--root", help="覆盖安装根目录（测试用）")
    args = parser.parse_args()
    if args.root:
        os.environ["KL_UPDATER_INSTALL_ROOT"] = args.root

    if args.update:
        print(json.dumps(update(args.update), ensure_ascii=False))
        return 0
    latest, current, needs = check()
    print(json.dumps({"latest": latest, "current": current, "update": needs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
