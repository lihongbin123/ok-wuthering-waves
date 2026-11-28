"""起手 dry-run 与动作桥接测试（不启动游戏）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.compat.stub_task import StubTask  # noqa: E402
from host.device import Input  # noqa: E402
from host.scheduler import EventLog  # noqa: E402
from host.tasks.char_opener import run_opener  # noqa: E402

KL_WORKING = Path("I:/KL/data/apps/kl/working")


class ActionBridgeTest(unittest.TestCase):
    def _task(self, tmp):
        logger = EventLog(Path(tmp) / "t.jsonl")
        inp = Input(dry_run=True)
        return StubTask(inp, logger), inp, logger

    def test_macro_spam_and_click_at_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, inp, logger = self._task(tmp)
            task.macro_spam(times=4, interval_ms=0)
            task.click(interval=0)
            logger.close()
            clicks = [e for e in inp.events if e["type"] == "click"]
            self.assertEqual(len(clicks), 5)
            self.assertTrue(all(e["x"] is None and e["y"] is None for e in clicks))
            self.assertTrue(any(e["type"] == "macro_spam" for e in inp.events))

    def test_switch_next_char_sends_wheel_and_rotates(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, inp, logger = self._task(tmp)

            class Mini:
                def __init__(self):
                    self.is_current_char = False

            first, second = Mini(), Mini()
            first.is_current_char = True
            task.chars = [first, second]
            task.switch_next_char(first)
            logger.close()
            self.assertEqual(inp.events[0]["type"], "key")
            self.assertEqual(inp.events[0]["key"], "tab")
            self.assertFalse(first.is_current_char)
            self.assertTrue(second.is_current_char)


@unittest.skipUnless((KL_WORKING / "src" / "char" / "Qingxiao.py").exists(), "KL 安装不存在")
class QingxiaoOpenerTest(unittest.TestCase):
    def test_dry_run_opener_uses_resonance_and_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_opener(KL_WORKING, "Qingxiao", Path(tmp) / "q.jsonl")
            self.assertEqual(result.errors, [])
            keys = [e["key"] for e in result.events if e["type"] == "key"]
            self.assertIn("e", keys)
            self.assertIn("tab", keys)
            self.assertLessEqual(keys.count("e"), 2)
            log_text = Path(result.log_path).read_text(encoding="utf-8")
            self.assertIn("switch_next_char", log_text)


class TemplateScaleTest(unittest.TestCase):
    def test_design_resolution_template_scaled_to_frame(self):
        import cv2
        import numpy as np

        from host.device import Frame
        from host.vision import Matcher

        image = np.zeros((540, 960, 3), np.uint8)
        patch = np.zeros((40, 80, 3), np.uint8)
        patch[::8, :] = 255
        patch[:, ::8] = 200
        image[100:140, 200:280] = patch
        frame = Frame(960, 540, cv2.cvtColor(image, cv2.COLOR_BGR2BGRA).tobytes(), 0.0)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tpl_1920x1080_0_0.png"
            cv2.imwrite(str(path), cv2.resize(patch, (160, 80), interpolation=cv2.INTER_NEAREST))
            match = Matcher(0.9).match(frame, path)
            self.assertIsNotNone(match)
            self.assertLessEqual(abs(match.x - 200), 2)
            self.assertLessEqual(abs(match.y - 100), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
