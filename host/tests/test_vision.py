"""视觉层测试：合成帧 + 模板匹配 + 与 StubTask 的集成门控。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.compat.stub_task import StubTask  # noqa: E402
from host.device import Frame, Input  # noqa: E402
from host.scheduler import EventLog  # noqa: E402
from host.vision import Matcher  # noqa: E402


def make_frame(width=240, height=120):
    return np.zeros((height, width, 4), dtype=np.uint8)


def to_frame(image) -> Frame:
    return Frame(image.shape[1], image.shape[0], image.tobytes(), 0.0)


class VisionTest(unittest.TestCase):
    def test_match_found_and_missed(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = np.zeros((16, 16, 4), dtype=np.uint8)
            template[:, :8] = (255, 255, 255, 255)
            tpl_path = Path(tmp) / "tpl.png"
            cv2.imwrite(str(tpl_path), template)

            canvas = make_frame()
            canvas[40:56, 70:86] = template
            matcher = Matcher(0.9)
            hit = matcher.match(to_frame(canvas), tpl_path)
            self.assertIsNotNone(hit)
            self.assertEqual((hit.x, hit.y), (70, 40))

            blank = to_frame(make_frame())
            self.assertIsNone(matcher.match(blank, tpl_path))

    def test_region_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = np.zeros((10, 10, 4), dtype=np.uint8)
            template[:5] = (255, 255, 255, 255)
            tpl_path = Path(tmp) / "tpl.png"
            cv2.imwrite(str(tpl_path), template)
            canvas = make_frame()
            canvas[80:90, 180:190] = template
            matcher = Matcher(0.9)
            self.assertIsNone(matcher.match(to_frame(canvas), tpl_path, region=(0, 0, 100, 100)))
            self.assertIsNotNone(matcher.match(to_frame(canvas), tpl_path, region=(150, 50, 90, 70)))

    def test_stub_task_find_one_and_hud_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = np.zeros((12, 12, 4), dtype=np.uint8)
            template[:, 6:] = (255, 255, 255, 255)
            tpl_path = Path(tmp) / "hud.png"
            cv2.imwrite(str(tpl_path), template)

            canvas = make_frame()
            canvas[20:32, 30:42] = template
            frame = to_frame(canvas)
            logger = EventLog(Path(tmp) / "v.jsonl")
            task = StubTask(Input(True), logger, vision=Matcher(0.9),
                            screen_provider=lambda: frame)
            task.hud_template = tpl_path
            self.assertIsNotNone(task.find_one(tpl_path))
            self.assertTrue(task.in_team()[0])

            blank = to_frame(make_frame())
            task.screen_provider = lambda: blank
            self.assertIsNone(task.find_one(tpl_path))
            self.assertFalse(task.in_team()[0])
            logger.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
