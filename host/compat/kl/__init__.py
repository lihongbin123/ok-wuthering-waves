"""极简框架垫片：只提供角色代码 import 时需要的 Config / Logger。

目的：让 `src/char/*` 这类纯逻辑文件可以在新宿主里加载，
不牵入旧框架的 UI、设备、i18n、更新器。
"""
from __future__ import annotations

import logging
from typing import Any


class Config(dict):
    """旧框架 Config 的最小替身：支持 get / [] / 属性式访问。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__["_children"] = {}

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


class Logger:
    """旧框架 Logger 的最小替身：统一输出到 logging。"""

    _root = logging.getLogger("kl-host.compat")

    @classmethod
    def get_logger(cls, name: str | None = None) -> logging.Logger:
        return cls._root.getChild(name or "char")


def init_logger(*args, **kwargs):  # pragma: no cover - 兼容旧调用
    return Logger.get_logger()
