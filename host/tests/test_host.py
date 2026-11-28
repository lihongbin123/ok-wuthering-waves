"""新宿主原型的离线测试（不需要游戏、不需要旧框架）。"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.config import HostConfig  # noqa: E402
from host.device import FakeCapture, Input  # noqa: E402
from host.scheduler import Context, EventLog, Scheduler, Task  # noqa: E402
from host.tasks.axis_replay import AxisReplayTask, load_events  # noqa: E402


class ConfigTest(unittest.TestCase):
    def test_defaults_and_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.json"
            path.write_text(json.dumps({"scheduler": {"tick_ms": 5}}), encoding="utf-8")
            cfg = HostConfig.load(path)
            self.assertEqual(cfg.get("scheduler.tick_ms"), 5)
            self.assertEqual(cfg.get("device.capture"), "bitblt")
            self.assertEqual(cfg.get("app.name"), "KL Host")


class DeviceTest(unittest.TestCase):
    def test_fake_capture(self):
        frame = FakeCapture(64, 32).grab()
        self.assertEqual(frame.size(), (64, 32))
        self.assertEqual(len(frame.bgra), 64 * 32 * 4)

    def test_dry_run_input(self):
        inp = Input(dry_run=True)
        inp.key("r")
        inp.click(10, 20)
        self.assertEqual([e["type"] for e in inp.events], ["key", "click"])
        self.assertEqual(inp.events[0]["key"], "r")


class SchedulerTest(unittest.TestCase):
    def test_tick_loop_runs_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "run.jsonl")
            ctx = Context(config=HostConfig(), device=FakeCapture(8, 8),
                          input=Input(True), logger=log)
            ticks = {"n": 0}

            class Counter(Task):
                def step(self, context):
                    ticks["n"] += 1
                    return ticks["n"] < 5

            sched = Scheduler(ctx, tick_ms=5)
            sched.run(Counter(name="counter"))
            log.close()
            records = [json.loads(line) for line in
                       (Path(tmp) / "run.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(sched.stats()["ticks"], 5)
            self.assertTrue(any(r["e"] == "task_end" for r in records))


class ReplayTest(unittest.TestCase):
    def test_replay_order_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "axis.jsonl"
            events = [
                {"e": "log", "t": 0.00, "m": "A"},
                {"e": "key", "t": 0.05, "key": "e"},
                {"e": "log", "t": 0.10, "m": "R"},
                {"e": "log", "t": 0.15, "m": "end"},
            ]
            src.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

            log = EventLog(Path(tmp) / "replay.jsonl")
            ctx = Context(config=HostConfig(), device=FakeCapture(8, 8),
                          input=Input(True), logger=log)
            sched = Scheduler(ctx, tick_ms=2)
            sched.run(AxisReplayTask(load_events(src)), max_ticks=500)
            log.close()

            self.assertEqual(len(ctx.input.events), 1)
            self.assertEqual(ctx.input.events[0]["key"], "e")
            records = [json.loads(line) for line in
                       (Path(tmp) / "replay.jsonl").read_text(encoding="utf-8").splitlines()]
            done = [r for r in records if r["e"] == "replay_done"][0]
            self.assertEqual(done["events"], 4)
            self.assertLess(done["max_drift_ms"], 60)
            self.assertLess(sched.stats()["p95_ms"], 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
