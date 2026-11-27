"""KL 自建更新器：只依赖 app.json + HTTPS，不经过 PyAppify launcher。

版本规则：远端 tag 必须是四段纯数字 ``v<上游三段>.<构建序号>``，例如
``v3.8.15.1``。``git ls-remote --tags`` 取最大值，与 ``current_version``
按元组比较；联网失败只写日志，不影响启动。

安全更新：KL 运行中（进程仍在或文件被占用）一律拒绝，未修改任何文件即中止。
安装采用"先建好新树、再整体换目录"的方式，不再原地删除，避免出现半替换状态。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)$")
PRESERVE = ("configs", "logs", "screenshots", "cache")
SKIP_COPY = set(PRESERVE) | {"__pycache__", ".pytest_cache", ".git", ".kl.pid"}
logger = None


class UpdaterBusyError(RuntimeError):
    """KL 正在运行或目标文件被占用，更新已中止。"""


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


def pending_path() -> Path:
    return install_root() / "build-tools" / "update-pending.json"


def pid_path() -> Path:
    return working_dir() / ".kl.pid"


def mark_running() -> None:
    """启动时写 pid 标记；更新器用它判断 KL 是否在运行（不受权限影响）。"""
    try:
        pid_path().write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _pid_alive() -> int:
    """读 .kl.pid 判断 KL 是否在运行；标记指向自身说明是在 KL 进程内发起更新。"""
    try:
        pid = int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    if pid <= 0:
        return 0
    try:
        import psutil
        return pid if psutil.pid_exists(pid) else 0
    except ImportError:
        return pid


def _repo_parts(url: str) -> tuple[str, str]:
    match = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    if not match:
        raise RuntimeError(f"只支持 GitHub 仓库地址：{url}")
    return match.group(1), match.group(2)


def _open(url: str, timeout: int = 60):
    proxy = os.environ.get("KL_HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={
        "User-Agent": "kl-updater",
        "Accept": "application/vnd.github+json",
    })
    return opener.open(request, timeout=timeout)


def _api_tags(url: str) -> list[str]:
    owner, repo = _repo_parts(url)
    # 优先用 git smart-http 的 refs 列表：不受 GitHub API 60 次/小时限制
    info = f"https://github.com/{owner}/{repo}.git/info/refs?service=git-upload-pack"
    try:
        with _open(info) as response:
            text = response.read().decode("utf-8", errors="replace")
        names = re.findall(r"[0-9a-f]{40} refs/tags/([^\s^]+)", text)
        if names:
            return names
    except Exception:  # noqa: BLE001
        pass
    tags: list[str] = []
    for page in range(1, 6):
        api = f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=100&page={page}"
        with _open(api) as response:
            batch = json.loads(response.read().decode("utf-8"))
        if not batch:
            break
        tags.extend(item.get("name", "") for item in batch)
        if len(batch) < 100:
            break
    return tags


def read_app_json() -> dict:
    with open(app_json_path(), "r", encoding="utf-8-sig") as fh:
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
    tags = [tag for tag in _api_tags(url) if parse_tag(tag)]
    return sorted(set(tags), key=parse_tag)


def check() -> tuple[str, str, bool]:
    """返回 (最新 tag, 当前版本, 是否有更新)。"""
    app = read_app_json()
    current = str(app.get("current_version") or "")
    tags = list_tags(git_url(app))
    if not tags:
        return "", current, False
    latest = tags[-1]
    return latest, current, parse_tag(latest) > current_version(app)


# ---------------------------------------------------------------- 安全检查

def _is_under(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath(
            [os.path.normcase(str(path)), os.path.normcase(str(parent))]) == os.path.normcase(str(parent))
    except ValueError:
        return False


def _running_processes(working: Path, root: Path) -> list[str]:
    """返回属于本安装的进程描述（运行中则不能替换任何文件）。"""
    decoy = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "KL" / "runtime" / "kl.exe"
    hits: list[str] = []
    try:
        import psutil
    except ImportError:
        return hits
    for proc in psutil.process_iter(["pid", "exe", "name"]):
        try:
            if proc.pid == os.getpid():
                continue
            exe = proc.info.get("exe") or ""
            if not exe:
                # 高权限进程可能读不到 exe，按名字兜底识别伪装进程
                if (proc.info.get("name") or "").lower() == "kl.exe":
                    hits.append(f"kl.exe({proc.pid})")
                continue
            path = Path(exe)
            if path == decoy or _is_under(path, root) or _is_under(path, working):
                hits.append(f"{proc.info.get('name') or '?'}({proc.pid})")
        except Exception:  # noqa: BLE001
            continue
    return hits


def _locked_files(working: Path) -> list[str]:
    """尝试以写方式打开每个文件；失败说明被占用或只读。"""
    locked: list[str] = []
    for path in working.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(working)
        if rel.parts and rel.parts[0] in SKIP_COPY:
            continue
        try:
            with open(path, "rb+"):
                pass
        except PermissionError:
            locked.append(str(rel))
        except OSError as error:
            if getattr(error, "errno", None) in (13, 32):
                locked.append(str(rel))
    return locked


def preflight(working: Path | None = None, root: Path | None = None) -> None:
    """更新前检查；不通过时抛 UpdaterBusyError，调用方保证此时尚未改动任何文件。"""
    working = working or working_dir()
    root = root or install_root()
    problems: list[str] = []
    pid = _pid_alive()
    if pid:
        problems.append(f"KL 正在运行（pid {pid}）")
    procs = _running_processes(working, root)
    if procs:
        problems.append("KL 进程：" + "、".join(procs[:3]))
    locked = _locked_files(working)
    if locked:
        head = "、".join(locked[:5])
        more = f" 等 {len(locked)} 个文件" if len(locked) > 5 else ""
        problems.append(f"文件被占用：{head}{more}")
    if problems:
        raise UpdaterBusyError(
            "无法安全更新，尚未修改任何文件。请完全退出 KL 后重试。\n" + "\n".join(problems))


# ---------------------------------------------------------------- 更新说明

CHANGELOG_FILES = ("CHANGELOG.zh-CN.md", "CHANGELOG.zh-TW.md", "CHANGELOG.md")
BUILD_INFO_NAME = "kl-build-info.json"
_HEADING_RE = re.compile(r"^##\s+v?(\d+)\.(\d+)\.(\d+)\s*(.*)$")


def _raw_url(tag: str, name: str) -> str:
    owner, repo = _repo_parts(git_url(read_app_json()))
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{tag}/{name}"


def _fetch_text(url: str) -> str:
    with _open(url, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_changelog(text: str) -> list[dict]:
    """把 CHANGELOG 拆成 [{version, title, lines}]，忽略无版本标题的内容。"""
    sections: list[dict] = []
    current = None
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            current = {
                "version": tuple(int(x) for x in match.group(1, 2, 3)),
                "title": line.lstrip("# ").strip(),
                "lines": [],
            }
            sections.append(current)
        elif current is not None and line.strip():
            current["lines"].append(line.strip())
    return sections


def _base_tuple(version: str) -> tuple[int, int, int]:
    numbers = [int(x) for x in re.findall(r"\d+", str(version))[:3]]
    return tuple(numbers + [0] * (3 - len(numbers)))


def _format_sections(sections, current: str, target: str,
                     max_versions: int = 3, max_lines: int = 10) -> list[str]:
    """当前基线之后、目标基线之内的 CHANGELOG 段落（新的在前）。"""
    current_base, target_base = _base_tuple(current), _base_tuple(target)
    picked = [s for s in sections if current_base < s["version"] <= target_base]
    picked.sort(key=lambda s: s["version"], reverse=True)
    out: list[str] = []
    used = 0
    for section in picked[:max_versions]:
        out.append(section["title"])
        for line in section["lines"]:
            if used >= max_lines:
                out.append("…")
                return out
            out.append(f"• {line.lstrip('-* ').strip()}")
            used += 1
    return out


def overlay_summary(target: str) -> str:
    """从构建时自动生成的 kl-build-info.json 读 KL 增量摘要。"""
    try:
        info = json.loads(_fetch_text(_raw_url(target, BUILD_INFO_NAME)))
    except Exception:  # noqa: BLE001
        return "KL 增量：该版本未附带增量清单"
    files = info.get("overlay_files") or []
    subject = str(info.get("overlay_subject") or "").strip()
    text = f"KL 增量（{len(files)} 个文件）"
    if subject:
        text += f"：{subject}"
    return text


def release_notes(current: str, target: str) -> str:
    """这次升级带来什么：当前版本到目标 tag 的上游说明 + KL 增量摘要。"""
    blocks: list[str] = []
    current_base, target_base = _base_tuple(current), _base_tuple(target)
    if current_base >= target_base:
        blocks.append(f"上游更新：同一上游基线（v{'.'.join(map(str, target_base))}），无上游内容变化。")
    else:
        header = f"上游更新（v{'.'.join(map(str, current_base))} → v{'.'.join(map(str, target_base))}）："
        text = ""
        for name in CHANGELOG_FILES:
            try:
                text = _fetch_text(_raw_url(target, name))
            except Exception:  # noqa: BLE001
                text = ""
            if text.strip():
                break
        body = _format_sections(parse_changelog(text), current, target) if text else []
        blocks.append(header + ("\n" + "\n".join(body) if body else "\n（未取到该区间的更新说明）"))
    blocks.append(overlay_summary(target))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- 排队与安装

def write_pending(tag: str) -> None:
    path = pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tag": tag, "created": datetime.now().isoformat(timespec="seconds")},
                               ensure_ascii=False, indent=2), encoding="utf-8")


def read_pending() -> dict:
    try:
        return json.loads(pending_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def clear_pending() -> None:
    try:
        pending_path().unlink()
    except OSError:
        pass


def _append_log(message: str) -> None:
    try:
        path = working_dir() / "logs" / "kl-updater.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def _copy_tree(src: Path, dst: Path, ignore=shutil.ignore_patterns(*SKIP_COPY)) -> None:
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def _backup(working: Path, backup_dir: Path) -> Path:
    target = backup_dir / "working"
    _copy_tree(working, target)
    return target


def _swap_tree(tree: Path, working: Path, stamp: str) -> Path:
    """整体换目录：working -> old，tree -> working，用户目录随后迁移。

    任一步失败都会把 old 换回 working，不会出现"删了一半"的状态。
    """
    old = working.with_name(f"{working.name}.old-{stamp}")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    os.rename(working, old)
    try:
        os.rename(tree, working)
    except OSError:
        os.rename(old, working)
        raise

    for name in PRESERVE:
        src = old / name
        if not src.exists():
            continue
        dst = working / name
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        try:
            os.replace(src, dst)
        except OSError:
            _copy_tree(src, dst, ignore=None)
    return old


def _extract_tag(url: str, tag: str, dest: Path) -> None:
    owner, repo = _repo_parts(url)
    tree = dest / "tree"
    tree.mkdir(parents=True, exist_ok=True)
    api = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/tags/{tag}"
    with _open(api, timeout=180) as response:
        payload = response.read()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = tar.getmembers()
        roots = {m.name.split("/", 1)[0] for m in members if m.name}
        if len(roots) != 1:
            raise RuntimeError(f"压缩包结构异常，顶层条目：{sorted(roots)}")
        root = roots.pop() + "/"
        for member in members:
            if not member.name.startswith(root):
                continue
            member.name = member.name[len(root):]
            if member.name:
                tar.extract(member, tree, filter="data")


def update(tag: str) -> dict:
    app = read_app_json()
    url = git_url(app)
    if tag not in list_tags(url):
        raise RuntimeError(f"远端不存在 tag：{tag}")

    working = working_dir()
    # 先做占用检查：不通过就直接抛错，此时一个文件都没动
    preflight(working)

    root = install_root()
    tools = root / "build-tools"
    tools.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = tools / f"backup-kl-update-{stamp}"

    staging = Path(tempfile.mkdtemp(prefix=f"kl-update-{tag}-", dir=str(tools)))
    old: Path | None = None
    try:
        _extract_tag(url, tag, staging)
        tree = staging / "tree"
        config_text = (tree / "config.py").read_text(encoding="utf-8", errors="replace")
        if "gui_title': 'KL'" not in config_text and "gui_title\": \"KL\"" not in config_text:
            raise RuntimeError("新版本的 config.py 不含 KL 品牌，tag 可能取错，已中止")

        # 目标版本必须带安全更新器，否则装上去就等于把修复回退掉
        target_updater = tree / "src" / "kl_updater.py"
        target_text = target_updater.read_text(encoding="utf-8", errors="replace") if target_updater.exists() else ""
        if "def preflight(" not in target_text:
            raise RuntimeError("新版本的更新器不含安全安装逻辑，已中止安装。请等重新构建的版本。")

        # 用户目录以当前安装为准，tag 树里自带的同名目录不采用
        for name in PRESERVE:
            if (tree / name).exists():
                shutil.rmtree(tree / name, ignore_errors=True)

        # 保留 tag 树里没有的顶层用户文件
        for item in working.iterdir():
            if item.name in SKIP_COPY or (tree / item.name).exists():
                continue
            if item.is_dir():
                _copy_tree(item, tree / item.name)
            else:
                shutil.copy2(item, tree / item.name)

        _backup(working, backup_dir)
        log(f"backup -> {backup_dir}")

        old = _swap_tree(tree, working, stamp)
        log(f"tree swapped, previous -> {old}")

        app["current_version"] = tag
        versions = list(app.get("available_versions") or [])
        if tag not in versions:
            versions.append(tag)
        app["available_versions"] = versions
        tmp = app_json_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(app, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, app_json_path())
        log(f"updated to {tag}")
    except Exception:
        if old is not None and old.exists():
            failed = working.with_name(f"{working.name}.failed-{stamp}")
            try:
                os.rename(working, failed)
                os.rename(old, working)
                shutil.rmtree(failed, ignore_errors=True)
                log("已从 .old 目录还原")
            except OSError as error:
                log(f"还原失败，备份保留在 {backup_dir}：{error}")
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
    return {"updated": True, "tag": tag, "backup": str(backup_dir)}


def auto_apply() -> dict:
    """启动前执行：有排队版本就安装；失败只记日志，不影响启动。"""
    pending = read_pending()
    tag = str(pending.get("tag") or "")
    if not tag:
        return {"applied": False, "reason": "no-pending"}
    try:
        result = update(tag)
        clear_pending()
        _append_log(f"auto update {tag} ok, backup={result['backup']}")
        return {"applied": True, **result}
    except Exception as error:  # noqa: BLE001
        _append_log(f"auto update {tag} failed: {error}")
        return {"applied": False, "reason": str(error)}


_bridge = None


def start_background_check() -> None:
    """启动后台检查；发现新版本只排队，退出 KL 后由启动脚本安装。"""
    global _bridge
    try:
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QMessageBox
    except Exception:
        threading.Thread(target=lambda: _log_only_check(), daemon=True).start()
        return

    class _Bridge(QObject):
        found = Signal(str, str, str)

    _bridge = _Bridge()

    def on_found(latest: str, current: str, notes: str) -> None:
        text = f"发现新版本 {latest}（当前 {current}）。"
        if notes:
            text += f"\n\n{notes}"
        text += "\n\nKL 运行中无法替换自身文件。是否安排到下次启动时自动安装？"
        answer = QMessageBox.question(
            None, "KL 更新", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            write_pending(latest)
            QMessageBox.information(
                None, "KL 更新",
                f"已安排 {latest}。\n请退出 KL，下次启动时会自动完成安装。")
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(None, "KL 更新", f"安排失败：{error}")

    _bridge.found.connect(on_found)

    def worker() -> None:
        try:
            latest, current, needs = check()
            if needs and not read_pending():
                try:
                    notes = release_notes(current, latest)
                except Exception as error:  # noqa: BLE001
                    log(f"更新说明获取失败：{error}")
                    notes = ""
                _bridge.found.emit(latest, current, notes)
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
    parser.add_argument("--notes", metavar="TAG", help="打印当前版本到 TAG 的更新说明")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--root", help="覆盖安装根目录（测试用）")
    args = parser.parse_args()
    if args.root:
        os.environ["KL_UPDATER_INSTALL_ROOT"] = args.root
    if args.notes:
        print(release_notes(str(read_app_json().get("current_version") or ""), args.notes))
        return 0
    if args.auto:
        print(json.dumps(auto_apply(), ensure_ascii=False))
        return 0
    if args.update:
        print(json.dumps(update(args.update), ensure_ascii=False))
        return 0
    latest, current, needs = check()
    print(json.dumps({"latest": latest, "current": current, "update": needs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
