"""HUD 判定：用已校准的模板/区域判断“角色是否在场”。

模板与区域来自 host/config.py 的 hud.templates，不再固定返回 True。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from host.config import HostConfig
from host.vision import Matcher


class HudProbe:
    def __init__(self, frame_provider: Callable, config: HostConfig | None = None,
                 templates: dict | None = None, root: str | Path | None = None):
        cfg = config or HostConfig.load()
        self.frame_provider = frame_provider
        self.templates = templates if templates is not None else cfg.get("hud.templates", {})
        self.root = Path(root or cfg.get("hud.template_root"))

    def _match(self, frame, name: str):
        spec = self.templates.get(name)
        if not spec:
            return None
        return Matcher(spec["threshold"]).match(
            frame, self.root / spec["file"], region=tuple(spec["region"]))

    def hit_any(self, names, frame=None) -> str | None:
        if frame is None:
            frame = self.frame_provider()
        if frame is None:
            return None
        for name in names:
            if self._match(frame, name) is not None:
                return name
        return None

    def in_team(self, char_name: str) -> bool:
        """技能图标视角的在场判定（保留兼容；主判定用 team_status）。"""
        prefix = char_name.lower()
        names = [f"{prefix}_e", f"{prefix}_h1", f"{prefix}_h2"]
        if not any(name in self.templates for name in names):
            return True  # 没校准过的角色不阻塞
        return self.hit_any(names) is not None

    TEAM_SLOTS = ("char_1_text", "char_2_text", "char_3_text")

    def team_status(self, frame=None) -> tuple[bool, int, int]:
        """队伍栏名字文字判定：命中 1~2 个即在队伍中（与真实 BaseWWTask.in_team 一致）。"""
        if frame is None:
            frame = self.frame_provider()
        if frame is None:
            return True, 0, 1
        present = [self._match(frame, name) is not None for name in self.TEAM_SLOTS]
        count = sum(present)
        current = present.index(False) if False in present else -1
        if count in (1, 2):
            return True, current, count + 1
        return False, -1, count + 1
