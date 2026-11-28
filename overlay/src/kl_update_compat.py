"""给 PyAppify 应用更新卡加超时/兜底，避免一直转圈。

背景：UpdateCard 调 ``pyappify.get_version_list`` 时会联网 fetch 远端 tag。
repo 远端改成 GitHub 后，本机直连可能长时间阻塞（默认 timeout=120s），
界面上表现为“正在检查更新…”不结束。

处理：应用内更新卡只读本地 repo 的 tag（毫秒级、无网络）；本地读取失败
才退回官方实现，且超时压到 8 秒。KL 自己的更新链走 ``src.kl_updater``，
不依赖这里。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)
TIMEOUT = float(os.environ.get("KL_APP_UPDATE_TIMEOUT", "8"))


def install() -> None:
    import pyappify
    from pyappify import standalone
    from src import kl_updater

    current = getattr(pyappify, "get_version_list", None)
    if current is None or getattr(current, "_kl_patched", False):
        return
    original = current

    def get_version_list(number_versions=10, release_only=True, timeout=120, exit_event=None):
        # 版本列表以 launcher 的 app.json 为准：本地 repo 缓存只有 v3.8.15 一个 tag，
        # 直接列 tag 会把“当前版本”显示成 v3.8.15。
        try:
            import json

            data = json.loads(kl_updater.app_json_path().read_text(encoding="utf-8"))
            versions = [str(item) for item in (data.get("available_versions") or [])
                        if kl_updater.TAG_RE.match(str(item))]
            if versions:
                versions.sort(
                    key=lambda v: [int(part) for part in v.lstrip("v").split(".")],
                    reverse=True,
                )
                return [{"version": item, "kind": "tag"} for item in versions]
        except Exception as error:  # noqa: BLE001
            logger.warning("app.json 版本读取失败（%s），改用本地 tag", error)
        try:
            path = standalone.find_repo_path()
            return standalone.list_versions(path, number_versions, release_only, (), None)
        except Exception as error:  # noqa: BLE001
            logger.warning("本地 tag 读取失败（%s），退回官方检查", error)
            return original(
                number_versions=number_versions,
                release_only=release_only,
                timeout=min(timeout or TIMEOUT, TIMEOUT),
                exit_event=exit_event,
            )

    get_version_list._kl_patched = True
    pyappify.get_version_list = get_version_list

    original_update = getattr(pyappify, "update_to_version", None)
    if original_update is not None and not getattr(original_update, "_kl_patched", False):

        def update_to_version(version, timeout=300, exit_event=None, language=None):
            """四段数字 tag 走 KL 自有更新器；其余立即失败，避免联网卡死。"""
            tag = str(version)
            if kl_updater.TAG_RE.match(tag):
                return kl_updater.update(tag)
            raise RuntimeError(
                "KL 的应用内切换版本已停用（会去 GitHub 拉整包并卡住）。"
                "请使用启动时的更新提示，或运行 src.kl_updater 升级。")

        update_to_version._kl_patched = True
        pyappify.update_to_version = update_to_version
