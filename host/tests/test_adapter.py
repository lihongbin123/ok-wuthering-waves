"""适配层测试：真实角色类 + 新宿主垫片（不启动游戏）。"""
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
from host.tasks.char_smoke import run_smoke  # noqa: E402

KL_WORKING = Path("I:/KL/data/apps/kl/working")


@unittest.skipUnless((KL_WORKING / "src" / "char" / "Denia.py").exists(), "KL 安装不存在")
class AdapterTest(unittest.TestCase):
    def test_stub_task_forwards_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLog(Path(tmp) / "t.jsonl")
            inp = Input(dry_run=True)
            task = StubTask(inp, logger)
            task.send_key("e")
            task.click()
            task.sleep(0.01)
            logger.close()
            self.assertEqual([e["type"] for e in inp.events], ["key", "click"])
            self.assertEqual(inp.events[0]["key"], "e")

    def test_real_denia_classloads_and_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_smoke(KL_WORKING, "Denia", 1, Path(tmp) / "denia.jsonl")
            self.assertEqual(result.errors, [])
            keys = [e["key"] for e in result.events if e["type"] == "key"]
            self.assertIn("e", keys)
            self.assertIn("q", keys)
            self.assertIn("r", keys)
            self.assertTrue(any(e["type"] == "click" for e in result.events))

    def test_real_qingxiao_classloads_and_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_smoke(KL_WORKING, "Qingxiao", 0, Path(tmp) / "qing.jsonl")
            self.assertEqual(result.errors, [])
            keys = [e["key"] for e in result.events if e["type"] == "key"]
            self.assertIn("e", keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
