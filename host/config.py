"""KL 新宿主：配置层（独立于旧框架，不依赖 Qt / ok-script）。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 项目根目录与日志目录：repo 布局 (host/config.py) 与原型目录 (I:\KL-host\host) 都成立
ROOT = Path(os.environ.get("KLHOST_ROOT") or Path(__file__).resolve().parents[1])
LOG_DIR = Path(os.environ.get("KLHOST_LOGS") or ROOT / "logs")

DEFAULTS: dict[str, Any] = {
    "app": {
        "name": "KL Host",
        "version": "0.1.0",
        "log_dir": "logs",
        "data_dir": "data",
    },
    "device": {
        "capture": "bitblt",          # bitblt | fake
        "fake_width": 1920,
        "fake_height": 1080,
        "window_title": "Wuthering Waves",
    },
    "input": {
        "dry_run": True,              # 默认只记录、不真正发键
        "key_interval_ms": 30,
    },
    "scheduler": {
        "tick_ms": 10,
        "max_jitter_ms": 25,
    },
    # HUD 模板与区域：2026-11-28 实机 3840x2160 校准（DPI 感知开启后）
    "hud": {
        "template_root": os.environ.get(
            "KL_TEMPLATES", r"I:\KL\data\apps\kl\working\assets\templates"),
        "templates": {
            "qingxiao_e": {
                "file": "qingxiao_e_3840x2160_3205_1892.png",
                "region": [3145, 1832, 200, 160],
                "threshold": 0.9,
                "hit": [3205, 1892],
                "score": 0.999,
            },
            "qingxiao_h1": {
                "file": "qingxiao_h1_3840x2160_2822_1887.png",
                "region": [2762, 1827, 200, 160],
                "threshold": 0.9,
                "hit": [2822, 1887],
                "score": 0.9993,
            },
            # h1/h2 是同一按钮的两种状态，共用区域；h2 需在强化重击可用时校准
            "qingxiao_h2": {
                "file": "qingxiao_h2_3840x2160_2818_1892.png",
                "region": [2762, 1827, 200, 160],
                "threshold": 0.9,
                "hit": None,
                "score": None,
            },
            # 在场判定：队伍栏三个槽位的名字文字（与真实 BaseWWTask.in_team 一致）
            "char_1_text": {
                "file": "char_1_text_1920x1080_1738_214.png",
                "region": [3300, 340, 540, 180],
                "threshold": 0.8,
                "hit": None,
            },
            "char_2_text": {
                "file": "char_2_text_1920x1080_1738_345.png",
                "region": [3300, 600, 540, 180],
                "threshold": 0.8,
                "hit": [3476, 690],
                "score": 0.9326,
            },
            "char_3_text": {
                "file": "char_3_text_1920x1080_1737_477.png",
                "region": [3300, 870, 540, 180],
                "threshold": 0.8,
                "hit": [3474, 954],
                "score": 0.9292,
            },
        },
    },
}


@dataclass
class HostConfig:
    path: Path | None = None
    data: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULTS)))

    @classmethod
    def load(cls, path: str | Path | None = None) -> "HostConfig":
        cfg = cls(path=Path(path) if path else None)
        if cfg.path and cfg.path.exists():
            overlay = json.loads(cfg.path.read_text(encoding="utf-8"))
            cfg.data = _merge(cfg.data, overlay)
        return cfg

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out
