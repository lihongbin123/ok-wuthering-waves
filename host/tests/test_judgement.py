"""判定层测试：区域缩放、颜色百分比、真实角色的 available() 门控。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.compat.boxes import Box, color_percentage, scale_region  # noqa: E402
from host.compat.stub_task import StubTask  # noqa: E402
from host.device import Frame, Input  # noqa: E402
from host.scheduler import EventLog  # noqa: E402
from host.tasks.char_smoke import load_char_class  # noqa: E402

KL_WORKING = Path("I:/KL/data/apps/kl/working")


def frame_with(left_white=True, width=192, height=108):
    image = np.zeros((height, width, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    if left_white:
        image[:, : width // 2, :3] = 255
    return Frame(width, height, image.tobytes(), 0.0)


class BoxTest(unittest.TestCase):
    def test_scale_region_4k_to_1080p(self):
        frame = frame_with(width=1920, height=1080)
        box = scale_region(3840, 2160, frame, 2251, 1993, 2311, 2016)
        self.assertEqual((box.x, box.y, box.w, box.h), (1126, 996, 30, 12))

    def test_color_percentage(self):
        frame = frame_with()
        whole = Box(0, 0, 192, 108, frame)
        left = Box(0, 0, 96, 108, frame)
        right = Box(96, 0, 96, 108, frame)
        self.assertAlmostEqual(color_percentage((255, 255, 255), left, tolerance=5), 1.0, places=2)
        self.assertAlmostEqual(color_percentage((255, 255, 255), right, tolerance=5), 0.0, places=2)
        self.assertAlmostEqual(color_percentage((255, 255, 255), whole, tolerance=5), 0.5, places=2)


class AvailabilityTest(unittest.TestCase):
    def _task(self, tmp, skill_state=None, con=0.0):
        logger = EventLog(Path(tmp) / "j.jsonl")
        task = StubTask(Input(True), logger, screen_provider=lambda: frame_with())
        task.skill_state = skill_state
        task.con_value = con
        return task, logger

    def test_stub_available_and_con(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, logger = self._task(tmp, skill_state=lambda box: box == "liberation", con=1.0)
            self.assertTrue(task.available("liberation"))
            self.assertFalse(task.available("resonance"))
            self.assertTrue(task.is_con_full())
            self.assertEqual(task.get_current_con(), 1.0)
            task.cd_state = lambda box, index: box == "echo"
            self.assertTrue(task.has_cd("echo"))
            logger.close()

    @unittest.skipUnless((KL_WORKING / "src" / "char" / "Denia.py").exists(), "KL 安装不存在")
    def test_real_char_availability_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, logger = self._task(tmp, skill_state=lambda box: box == "liberation")
            cls = load_char_class(KL_WORKING, "Denia")
            char = cls(task, 1)
            char.is_current_char = True
            self.assertTrue(char.liberation_available())
            self.assertFalse(char.resonance_available())
            task.skill_state = lambda box: False
            self.assertFalse(char.liberation_available())
            logger.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
