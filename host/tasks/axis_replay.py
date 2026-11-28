"""轴回放任务：把 KL 录制的 axis_runs JSONL 按原始时间轴重放。

用途（第 2 档原型的第一块验证）：
- 证明新宿主能在不引入旧框架调度器的情况下，按毫秒级时间轴驱动事件；
- 用真实录制数据测调度抖动、事件顺序、丢帧/漂移；
- 后续把 log 事件替换成真实的“读屏 -> 判定 -> 发键”动作即可逐步接上任务逻辑。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from host.scheduler import Context, Task


def load_events(path: str | Path) -> list[dict]:
    events = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if "t" in record:
                events.append(record)
    events.sort(key=lambda item: item["t"])
    return events


class AxisReplayTask(Task):
    def __init__(self, events: list[dict], speed: float = 1.0):
        super().__init__(name="axis_replay")
        self.events = events
        self.speed = max(0.01, speed)
        self._index = 0
        self._started = 0.0
        self.drift: list[float] = []

    def setup(self, ctx: Context) -> None:
        self._started = time.perf_counter()
        ctx.logger.write({"e": "replay_plan", "events": len(self.events),
                          "speed": self.speed,
                          "duration": self.events[-1]["t"] if self.events else 0})

    def step(self, ctx: Context) -> bool:
        if self._index >= len(self.events):
            return False
        elapsed = (time.perf_counter() - self._started) * self.speed
        while self._index < len(self.events) and self.events[self._index]["t"] <= elapsed:
            event = self.events[self._index]
            self._index += 1
            self.drift.append(elapsed - event["t"])
            kind = event.get("e")
            if kind == "log":
                ctx.logger.write({"e": "replay_log", "src_t": event["t"],
                                  "m": event.get("m", "")})
            elif kind == "key":
                ctx.input.key(event.get("key", "a"))
            elif kind == "click":
                ctx.input.click(event.get("x", 0), event.get("y", 0))
            else:
                ctx.logger.write({"e": "replay_other", "src_t": event["t"],
                                  "kind": kind})
        return True

    def teardown(self, ctx: Context) -> None:
        total = len(self.drift)
        worst = max(self.drift) if self.drift else 0.0
        avg = sum(self.drift) / total if total else 0.0
        ctx.logger.write({"e": "replay_done", "events": total,
                          "avg_drift_ms": round(avg * 1000, 2),
                          "max_drift_ms": round(worst * 1000, 2)})
