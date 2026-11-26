# -*- coding: utf-8 -*-
"""KL 工坊每日自动同步。

绑定 KL 启动：每天首次启动时在后台线程执行一次增量同步。
同步策略为 SHA256 只增不覆盖，不使用 workshop_sync.py 的“大小不同即复制”逻辑。

关闭方式（默认开启）：
- 配置页 “Workshop Autoupdate” -> “Daily Sync on Startup” 设为 False；
- 或环境变量 KL_WORKSHOP_AUTOUPDATE=0/false/off/no。
"""

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kl import Logger

logger = Logger.get_logger(__name__)

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = APP_ROOT / "configs" / "local_workshop"
WORKSPACE_ROOT = APP_ROOT.parents[3] if len(APP_ROOT.parents) > 3 else APP_ROOT
BACKUP_ROOT = WORKSPACE_ROOT / "build-tools"

REPO_INDEX_URL = "https://raw.githubusercontent.com/wk321425/coco-ww-char-code/main/teams.json"
REPO_CODES_URL = "https://raw.githubusercontent.com/wk321425/coco-ww-char-code/main/codes/"

STATE_FILE = "autoupdate_state.json"
CONFIG_NAME = "Workshop Autoupdate"
CONFIG_KEY = "Daily Sync on Startup"
ENV_SWITCH = "KL_WORKSHOP_AUTOUPDATE"
HTTP_TIMEOUT = 20

_VERSION_RE = re.compile(r"^(?P<stem>.+)_(?P<ver>\d+(?:\.\d+)*)\.zip$", re.IGNORECASE)
_RUNTIME_BACKUP_RE = re.compile(r"^backup-workshop-autoupdate-\d{8}-\d{6}$")

_START_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_started = False


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _write_json_atomic(path, payload):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_state(root):
    state = _load_json(Path(root) / STATE_FILE, None)
    return state if isinstance(state, dict) else {}


def _enabled():
    raw = os.environ.get(ENV_SWITCH)
    if raw is not None and raw.strip().lower() in {"0", "false", "off", "no", "disabled"}:
        return False
    try:
        from kl import og
        if og.global_config is not None:
            return bool(og.global_config.get_config(CONFIG_NAME).get(CONFIG_KEY, True))
    except Exception as e:
        logger.warning(f"workshop autoupdate: read config failed, default enabled: {e!r}")
    return True


def start_backlound_sync():
    """由 Globals 在启动时调用；只启动一次，不阻塞调用线程。"""
    global _started
    with _START_LOCK:
        if _started:
            return
        _started = True
    if not _enabled():
        logger.info("workshop autoupdate disabled by config/env")
        return
    t = threading.Thread(target=_thread_main, name="workshop-autoupdate", daemon=True)
    t.start()


def _thread_main():
    try:
        result = run_sync()
        logger.info("workshop autoupdate: " + json.dumps(result, ensure_ascii=False))
    except Exception as e:  # 双保险，绝不影响主线程/启动
        logger.warning(f"workshop autoupdate thread failed: {e!r}")


def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "KL-Workshop-AutoUpdate"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def _code_url(filename):
    return REPO_CODES_URL + urllib.parse.quote(filename)


def _valid_archive(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.testzip() is not None:
                return False
            names = set(z.namelist())
            if "team.json" not in names:
                return False
            json.loads(z.read("team.json").decode("utf-8-sig"))
        return True
    except Exception:
        return False


def _flatten(index):
    out = {}
    for team in index.get("teams", []) or []:
        for code in team.get("codes", []) or []:
            fn = str(code.get("filename") or "")
            if fn:
                out[fn] = code
    return out


def _split_version(filename):
    m = _VERSION_RE.match(filename)
    if not m:
        return None, None
    try:
        version = tuple(int(x) for x in m.group("ver").split("."))
    except ValueError:
        return None, None
    return m.group("stem").casefold(), version


def _older_than_local(filename, local_by_fn):
    stem, version = _split_version(filename)
    if stem is None:
        return False
    for other_fn in local_by_fn:
        if other_fn == filename:
            continue
        other_stem, other_version = _split_version(other_fn)
        if other_stem == stem and other_version and other_version > version:
            return True
    return False


def _same_repo_stamp(record, code):
    return (record.get("size") == code.get("size")
            and record.get("timestamp") == code.get("timestamp"))


def _provenance(code, digest):
    return {"sha256": digest, "size": code.get("size"), "timestamp": code.get("timestamp")}


def _backup_root(backup_root):
    return Path(backup_root) if backup_root else BACKUP_ROOT


def _prune_old_backups(root):
    try:
        dirs = sorted(
            (d for d in root.iterdir() if d.is_dir() and _RUNTIME_BACKUP_RE.match(d.name)),
            key=lambda p: p.name,
        )
        for old in dirs[:-1]:
            shutil.rmtree(old, ignore_errors=True)
            logger.info(f"workshop autoupdate: pruned old backup {old}")
    except Exception as e:
        logger.warning(f"workshop autoupdate: prune backups failed: {e!r}")


def _create_backup(root, backup_root, replaced_files):
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    backup = _backup_root(backup_root) / ("backup-workshop-autoupdate-" + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    for rel in ("teams.json", "repo_source.json", "upstream/teams.json"):
        src = root / rel
        if src.is_file():
            dst = backup / "files" / "configs" / "local_workshop" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for filename in replaced_files:
        src = root / "upstream" / "codes" / filename
        if src.is_file():
            dst = backup / "files" / "configs" / "local_workshop" / "upstream" / "codes" / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return backup


def _write_manifest(backup, root, result, added, updated, replaced_old_sha):
    lines = []
    add = lines.append
    add("KL 工坊自动同步备份（每日首次启动触发）")
    add("=" * 72)
    add("时间: " + datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"))
    add("备份目录: " + str(backup))
    add("触发: KL 启动后台线程（每天首次）")
    add("策略: SHA256 只增不覆盖；已存在档案不覆盖，本地改动不动")
    add("关闭: 配置页 Workshop Autoupdate / Daily Sync on Startup=False，")
    add("      或环境变量 KL_WORKSHOP_AUTOUPDATE=0")
    add("")
    add("一、改动文件（原 -> 新 SHA256）")
    add("-" * 72)
    for rel in ("teams.json", "repo_source.json", "upstream/teams.json"):
        src = backup / "files" / "configs" / "local_workshop" / rel
        cur = root / rel
        if src.is_file():
            add(rel)
            add("  原: " + _sha_file(src))
            add("  新: " + (_sha_file(cur) if cur.is_file() else "（已删除）"))
    for filename, old_sha in replaced_old_sha.items():
        add("upstream/codes/" + filename)
        add("  原: " + old_sha)
        cur = root / "upstream" / "codes" / filename
        add("  新: " + (_sha_file(cur) if cur.is_file() else "（未写入）"))
    add("")
    add("二、新增文件（原不存在）")
    add("-" * 72)
    if added:
        for filename, digest in added:
            add("upstream/codes/" + filename)
            add("  SHA256: " + digest)
            add("  原: N/A（新增）")
    else:
        add("无")
    add("")
    add("三、本次统计")
    add("-" * 72)
    add("新增: %d  更新: %d  本地保留/跳过: %d  旧版本跳过: %d  失败: %d"
        % (len(added), len(updated), len(result["skippedLocal"]),
           len(result["skippedOlder"]), len(result["failures"])))
    add("")
    add("四、回退命令（先关闭 KL）")
    add("-" * 72)
    add('Copy-Item -Recurse -Force "' + str(backup) +
        '\\files\\configs\\local_workshop\\*" "' + str(DEFAULT_ROOT) + '\\"')
    for filename, _ in added:
        add('Remove-Item -Force "' + str(DEFAULT_ROOT / "upstream" / "codes" / filename) + '"')
    add("")
    add("注：本备份只包含被改动/新增的文件，不含全量镜像；旧版自动备份已按保留策略清理。")
    (backup / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_index(index, repo_codes, installed, updated, index_only):
    old_by_fn = {}
    for team in index.get("teams", []) or []:
        for i, code in enumerate(team.get("codes", []) or []):
            fn = str(code.get("filename") or "")
            if fn:
                old_by_fn[fn] = (team, i, code)

    by_members = {
        tuple(sorted((str(m) for m in t.get("members", [])), key=str.casefold)): t
        for t in index.get("teams", []) or []
    }

    def entry_for(code, filename):
        entry = dict(code)
        entry["downloadUrl"] = _code_url(filename)
        entry["rawUrl"] = entry["downloadUrl"]
        entry["localPath"] = "upstream/codes/" + filename
        entry["source"] = "upstream"
        return entry

    for filename, code in installed + index_only:
        if filename in old_by_fn:
            team, i, old = old_by_fn[filename]
            entry = entry_for(code, filename)
            entry["team"] = old.get("team", entry.get("team"))
            entry["members"] = old.get("members", entry.get("members"))
            team["codes"][i] = entry
            old_by_fn[filename] = (team, i, entry)
            continue
        entry = entry_for(code, filename)
        key = tuple(sorted((str(m) for m in entry.get("members", [])), key=str.casefold))
        team = by_members.get(key)
        if team is None:
            team = {
                "team": entry.get("team") or " / ".join(entry.get("members", [])),
                "members": entry.get("members", []),
                "slug": entry.get("name") or "_".join(entry.get("members", [])),
                "codes": [],
            }
            index.setdefault("teams", []).append(team)
            by_members[key] = team
        team.setdefault("codes", []).append(entry)
        old_by_fn[filename] = (team, len(team["codes"]) - 1, entry)

    for filename, code in updated:
        if filename in old_by_fn:
            team, i, old = old_by_fn[filename]
            entry = entry_for(code, filename)
            entry["team"] = old.get("team", entry.get("team"))
            entry["members"] = old.get("members", entry.get("members"))
            team["codes"][i] = entry

    index["repository"] = "wk321425/coco-ww-char-code"
    index["syncedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index["totalTeams"] = len(index.get("teams", []) or [])
    index["totalCodes"] = sum(len(t.get("codes", []) or []) for t in index.get("teams", []) or [])


def _update_source(root, index, repo, counts, started_at):
    path = root / "repo_source.json"
    source = _load_json(path, {}) or {}
    source.update({
        "repository": "wk321425/coco-ww-char-code",
        "repoUrl": "https://github.com/wk321425/coco-ww-char-code",
        "rawBase": "https://raw.githubusercontent.com/wk321425/coco-ww-char-code/main",
        "syncedFrom": "https://raw.githubusercontent.com/wk321425/coco-ww-char-code/main",
        "syncedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "durationSeconds": round(datetime.now(timezone.utc).timestamp() - started_at, 3),
        "repoTeams": len(repo.get("teams", []) or []),
        "repoCodes": len(_flatten(repo)),
        "totalTeams": index.get("totalTeams"),
        "totalCodes": index.get("totalCodes"),
        "copied": counts["added"],
        "updated": counts["updated"],
        "reused": counts["unchanged"],
        "preservedLocal": counts["skippedLocal"],
        "skippedOlder": counts["skippedOlderList"],
        "failures": counts["failures"],
        "note": "auto sync (daily first launch), SHA256 additive: no overwrite of existing/local files",
    })
    _write_json_atomic(path, source)


def run_sync(root=None, today=None, http_get=None, force=False, backup_root=None):
    """执行一次同步；返回结果 dict，不向调用方抛出网络/文件异常。

    root/today/http_get 仅用于测试注入。
    """
    root = Path(root) if root else DEFAULT_ROOT
    today = today or datetime.now().astimezone().date().isoformat()
    http_get = http_get or _http_get
    started_at = datetime.now(timezone.utc).timestamp()

    result = {
        "status": "ok", "date": today, "added": [], "updated": [],
        "skippedOlder": [], "skippedLocal": [], "failures": [],
        "unchanged": 0,
    }

    if not _RUN_LOCK.acquire(blocking=False):
        result.update(status="skipped", reason="another-run-in-progress")
        return result

    try:
        state = _load_state(root)
        if not force and state.get("lastSuccessDate") == today:
            result.update(status="skipped", reason="already-synced-today")
            return result

        index_path = root / "teams.json"
        index = _load_json(index_path, None)
        if not isinstance(index, dict):
            raise RuntimeError(f"local workshop index not found/invalid: {index_path}")

        raw_repo = http_get(REPO_INDEX_URL)
        repo = json.loads(raw_repo.decode("utf-8-sig"))
        repo_codes = _flatten(repo)
        local_codes = _flatten(index)
        codes_dir = root / "upstream" / "codes"
        codes_dir.mkdir(parents=True, exist_ok=True)

        prov = state.get("files") if isinstance(state.get("files"), dict) else {}
        first_run = not state.get("seeded")

        adds = []        # (filename, code, data, sha)
        updates = []     # (filename, code, data, sha)
        index_only = []  # (filename, code) —— 文件已在，索引缺失（崩溃自愈）
        newly_verified = {}
        skipped_older = []

        for filename, code in repo_codes.items():
            path = codes_dir / filename
            if filename in local_codes and path.is_file():
                local_sha = _sha_file(path)
                record = prov.get(filename) if isinstance(prov.get(filename), dict) else None
                if record and record.get("sha256") == local_sha:
                    if _same_repo_stamp(record, code):
                        result["unchanged"] += 1
                        continue
                    try:
                        data = http_get(_code_url(filename))
                        digest = _sha_bytes(data)
                        if digest == local_sha:
                            newly_verified[filename] = _provenance(code, digest)
                            result["unchanged"] += 1
                        elif _valid_archive(data):
                            updates.append((filename, code, data, digest))
                        else:
                            result["failures"].append({"filename": filename, "error": "invalid archive"})
                    except Exception as e:
                        result["failures"].append({"filename": filename, "error": repr(e)})
                elif first_run:
                    # 首次运行：下载仓库版本做一次 SHA256 比对，仅用于建立来源记录。
                    try:
                        data = http_get(_code_url(filename))
                        digest = _sha_bytes(data)
                        if digest == local_sha:
                            newly_verified[filename] = _provenance(code, digest)
                            result["unchanged"] += 1
                        else:
                            result["skippedLocal"].append(filename)
                    except Exception as e:
                        result["failures"].append({"filename": filename, "error": repr(e)})
                else:
                    result["skippedLocal"].append(filename)
            elif path.is_file():
                # 文件存在但索引缺失：只补索引，不重写文件。
                index_only.append((filename, code))
            else:
                if _older_than_local(filename, local_codes):
                    skipped_older.append(filename)
                    continue
                try:
                    data = http_get(_code_url(filename))
                    digest = _sha_bytes(data)
                    if not _valid_archive(data):
                        result["failures"].append({"filename": filename, "error": "invalid archive"})
                        continue
                    adds.append((filename, code, data, digest))
                except Exception as e:
                    result["failures"].append({"filename": filename, "error": repr(e)})

        result["skippedOlder"] = skipped_older
        changed = bool(adds or updates or index_only)

        if changed:
            replaced = {filename: _sha_file(codes_dir / filename)
                        for filename, _, _, _ in updates}
            backup = _create_backup(root, backup_root, list(replaced))

            for filename, _, data, _ in adds + updates:
                target = codes_dir / filename
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, target)

            _update_index(index, repo_codes, [(fn, c) for fn, c, _, _ in adds],
                          [(fn, c) for fn, c, _, _ in updates], index_only)
            _write_json_atomic(index_path, index)
            _write_json_atomic(root / "upstream" / "teams.json", repo)

            installed = [(fn, _sha_bytes(data)) for fn, _, data, _ in adds]
            updated_manifest = [(fn, digest) for fn, _, _, digest in updates]
            selected = {
                "added": len(adds),
                "updated": len(updates),
                "unchanged": result["unchanged"],
                "skippedLocal": len(result["skippedLocal"]),
                "skippedOlderList": [{"filename": fn, "reason": "本地已有同系列更新版本"}
                                     for fn in skipped_older],
                "failures": result["failures"],
            }
            _update_source(root, index, repo, selected, started_at)
            _write_manifest(backup, root, result, installed, updated_manifest, replaced)
            _prune_old_backups(_backup_root(backup_root))

        state_files = dict(prov)
        state_files.update(newly_verified)
        for filename, _, _, digest in adds:
            state_files[filename] = _provenance(repo_codes[filename], digest)
        for filename, _, _, digest in updates:
            state_files[filename] = _provenance(repo_codes[filename], digest)
        _write_json_atomic(root / STATE_FILE, {
            "version": 1,
            "seeded": True,
            "lastSuccessDate": today,
            "lastSuccessAt": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "result": "ok",
            "added": len(adds),
            "indexRepaired": len(index_only),
            "updated": len(updates),
            "unchanged": result["unchanged"],
            "skippedLocal": len(result["skippedLocal"]),
            "skippedOlder": skipped_older,
            "failures": result["failures"],
            "files": state_files,
        })

        result["added"] = [fn for fn, _, _, _ in adds]
        result["indexRepaired"] = [fn for fn, _ in index_only]
        result["updated"] = [fn for fn, _, _, _ in updates]
        result["backup"] = str(backup) if changed else None
        return result
    except Exception as e:
        logger.warning(f"workshop autoupdate failed: {e!r}")
        result.update(status="failed", error=repr(e))
        return result
    finally:
        _RUN_LOCK.release()
