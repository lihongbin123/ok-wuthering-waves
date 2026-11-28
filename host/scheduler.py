"""调度层：固定节拍的任务循环 + JSONL 事件日志。

原型目标：证明新宿主能在不依赖旧框架的情况下，按时间轴稳定驱动任务、
录制完整事件流，并用它做回放/延迟测试。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    name: str = "task"

    def setup(self, ctx: "Context") -> None:
        pass

    def step(self, ctx: "Context") -> bool:
        """返回 False 结束任务。"""
        return False

    def teardown(self, ctx: "Context") -> None:
        pass


@dataclass
class Context:
    config: object
    device: object
    input: object
    logger: "EventLog"
    state: dict = field(default_factory=dict)

    def log(self, message: str, level: int = 20) -> None:
        self.logger.write({"e": "log", "l": level, "m": message})


class EventLog:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._start = clock()
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        record.setdefault("t", round(self._clock() - self._start, 4))
        with self._lock:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class Scheduler:
    def __init__(self, ctx: Context, tick_ms: int = 10):
        self.ctx = ctx
        self.tick = tick_ms / 1000.0
        self._stop = threading.Event()
        self.jitter: list[float] = []

    def stop(self) -> None:
        self._stop.set()

    def run(self, task: Task, max_ticks: int | None = None) -> None:
        task.setup(self.ctx)
        self.ctx.logger.write({"e": "task_start", "task": task.name})
        next_at = time.perf_counter()
        ticks = 0
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                self.jitter.append(max(0.0, (now - next_at) * 1000))
                self.ctx.logger.write({"e": "tick", "n": ticks, "j": round(self.jitter[-1], 2)})
                if not task.step(self.ctx):
                    break
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                next_at += self.tick
                delay = next_at - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_at = time.perf_counter()
        finally:
            task.teardown(self.ctx)
            self.ctx.logger.write({"e": "task_end", "task": task.name, "ticks": ticks})

    def stats(self) -> dict:
        if not self.jitter:
            return {"ticks": 0}
        ordered = sorted(self.jitter)
        return {
            "ticks": len(self.jitter),
            "avg_ms": round(sum(self.jitter) / len(self.jitter), 2),
            "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 2),
            "max_ms": round(ordered[-1], 2),
        }
