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

    current = getattr(pyappify, "get_version_list", None)
    if current is None or getattr(current, "_kl_patched", False):
        return
    original = current

    def get_version_list(number_versions=10, release_only=True, timeout=120, exit_event=None):
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
