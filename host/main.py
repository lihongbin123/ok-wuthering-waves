"""KL 新宿主原型入口。

示例：
  python -m host.main --replay I:\\KL\\data\\apps\\kl\\working\\logs\\axis_runs\\xxx.jsonl
  python -m host.main --replay xxx.jsonl --live --capture bitblt --speed 1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from host.config import HostConfig
from host.device import BitBltCapture, FakeCapture, Input
from host.scheduler import Context, EventLog, Scheduler
from host.tasks.axis_replay import AxisReplayTask, load_events


def build_device(cfg: HostConfig):
    kind = cfg.get("device.capture", "fake")
    if kind == "bitblt":
        return BitBltCapture()
    return FakeCapture(cfg.get("device.fake_width", 1920),
                       cfg.get("device.fake_height", 1080))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KL host prototype")
    parser.add_argument("--config", default=None)
    parser.add_argument("--replay", required=True, help="axis_runs JSONL 路径")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--live", action="store_true", help="真正发送键鼠（默认 dry-run）")
    parser.add_argument("--capture", choices=["fake", "bitblt"], default=None)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--max-ticks", type=int, default=None)
    args = parser.parse_args(argv)

    cfg = HostConfig.load(args.config)
    if args.capture:
        cfg.data["device"]["capture"] = args.capture

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"host-{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    logger = EventLog(log_path)
    ctx = Context(config=cfg, device=build_device(cfg),
                  input=Input(dry_run=not args.live), logger=logger)
    events = load_events(args.replay)
    task = AxisReplayTask(events, speed=args.speed)

    scheduler = Scheduler(ctx, tick_ms=cfg.get("scheduler.tick_ms", 10))
    logger.write({"e": "start", "version": cfg.get("app.version"),
                  "replay": str(args.replay), "events": len(events),
                  "live": args.live, "capture": cfg.get("device.capture")})
    try:
        scheduler.run(task, max_ticks=args.max_ticks)
    finally:
        stats = scheduler.stats()
        logger.write({"e": "scheduler_stats", **stats})
        logger.close()

    print(f"log: {log_path}")
    print(f"scheduler: {stats}")
    print(f"input_events: {len(ctx.input.events)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
