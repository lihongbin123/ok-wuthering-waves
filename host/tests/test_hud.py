"""in_team 的 HUD 模板判定（离线，用真实模板贴到 4K 画布）。"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.compat.stub_task import StubTask  # noqa: E402
from host.config import HostConfig  # noqa: E402
from host.device import Frame, Input  # noqa: E402
from host.hud import HudProbe  # noqa: E402
from host.scheduler import EventLog  # noqa: E402


class HudProbeTest(unittest.TestCase):
    def setUp(self):
        self.cfg = HostConfig.load()
        self.root = Path(self.cfg.get("hud.template_root"))
        self.templates = self.cfg.get("hud.templates")
        self.frame = None

    def _frame_with(self, placements: dict) -> Frame:
        canvas = np.zeros((2160, 3840, 4), np.uint8)
        canvas[..., 3] = 255
        for name, (x, y) in placements.items():
            spec = self.templates[name]
            image = cv2.imread(str(self.root / spec["file"]))
            # 模板若按 1920 设计，离线画布按 2 倍贴图，模拟 4K 下的实际字形
            design = re.search(r"_(\d{3,5})x(\d{3,5})_", spec["file"])
            if design and int(design.group(1)) != 3840:
                ratio = 3840 / int(design.group(1))
                image = cv2.resize(image, (round(image.shape[1] * ratio),
                                           round(image.shape[0] * ratio)),
                                   interpolation=cv2.INTER_NEAREST)
            height, width = image.shape[:2]
            canvas[y:y + height, x:x + width, :3] = image
        return Frame(3840, 2160, canvas.tobytes(), 0.0)

    def _probe(self):
        return HudProbe(frame_provider=lambda: self.frame, config=self.cfg)

    def test_two_party_slots_mean_in_team(self):
        self.frame = self._frame_with({"char_2_text": (3476, 690),
                                       "char_3_text": (3474, 954)})
        ok, current, count = self._probe().team_status()
        self.assertTrue(ok)
        self.assertEqual(current, 0)
        self.assertEqual(count, 3)

    def test_single_party_slot_still_in_team(self):
        self.frame = self._frame_with({"char_3_text": (3474, 954)})
        ok, current, _ = self._probe().team_status()
        self.assertTrue(ok)
        self.assertEqual(current, 0)

    def test_blank_frame_means_off_field(self):
        self.frame = self._frame_with({})
        ok, current, _ = self._probe().team_status()
        self.assertFalse(ok)
        self.assertEqual(current, -1)

    def test_skill_icon_alone_is_not_in_team(self):
        self.frame = self._frame_with({"qingxiao_e": (3205, 1892)})
        self.assertFalse(self._probe().team_status()[0])
        # 技能图标仍用于技能可用性判断
        self.assertEqual(self._probe().hit_any(["qingxiao_e", "qingxiao_h1"]), "qingxiao_e")


class StubTaskTeamProbeTest(unittest.TestCase):
    def test_uses_probe_and_reports_current_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLog(Path(tmp) / "t.jsonl")
            task = StubTask(Input(dry_run=True), logger)

            class Mini:
                def __init__(self, index, current):
                    self.index = index
                    self.is_current_char = current

            task.chars = [Mini(0, False), Mini(1, True)]
            task.team_probe = lambda: (False, -1, 1)
            self.assertEqual(task.in_team(), (False, 1, 1))
            task.team_probe = lambda: (True, 0, 3)
            self.assertEqual(task.in_team(), (True, 0, 3))
            task.team_probe = lambda: False
            self.assertEqual(task.in_team(), (False, 1, 2))
            logger.close()

    def test_probe_error_falls_back_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLog(Path(tmp) / "t.jsonl")
            task = StubTask(Input(dry_run=True), logger)

            def boom():
                raise RuntimeError("probe failed")

            task.team_probe = boom
            self.assertEqual(task.in_team()[0], True)
            logger.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
