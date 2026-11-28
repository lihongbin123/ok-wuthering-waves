"""角色起手 dry-run：真实角色类 + 剧本化视觉，验证“读屏→判定→发键”链路。

  python -m host.tasks.char_opener --kl-working I:\\KL\\data\\apps\\kl\\working --char Qingxiao

不启动游戏，不发真实按键；只验证角色代码在新宿主里的起手动作顺序。
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from host.compat.stub_task import StubTask
from host.device import Frame, Input
from host.scheduler import EventLog
from host.tasks.char_smoke import load_char_class
from host.vision import Match


class ScriptedVision:
    """按时间窗返回固定命中，模拟技能图标出现/消失。"""

    def __init__(self, hits: dict[str, tuple[float, float]]):
        self.hits = hits
        self.t0 = time.time()

    def match(self, frame, path, region=None, threshold=None):
        window = self.hits.get(str(path))
        if not window:
            return None
        elapsed = time.time() - self.t0
        if window[0] <= elapsed < window[1]:
            return Match(100, 100, 32, 32, 0.99)
        return None


@dataclass
class OpenerResult:
    char: str
    events: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log_path: str = ""


def run_opener(kl_working: str | Path, char_name: str = "Qingxiao",
               log_path: str | Path | None = None, live: bool = False,
               title: str | None = "鸣潮", pid: int | None = None) -> OpenerResult:
    result = OpenerResult(char=char_name)
    log_path = Path(log_path or Path("logs") / f"opener-{char_name}-{int(time.time())}.jsonl")
    logger = EventLog(log_path)
    key_config = {"Resonance Key": "e", "Liberation Key": "r", "Echo Key": "q",
                  "Wheel Key": "tab", "Jump Key": "space", "Dodge Key": "lshift"}
    task = StubTask(Input(dry_run=not live), logger, config={"key_config": key_config})
    task.key_config.update(key_config)
    capture = None

    try:
        cls = load_char_class(kl_working, char_name)
        char = cls(task, 0)
        char.is_current_char = True
        task.chars = [char]
        from src.Labels import Labels
        task.hud_template = None
        if live:
            # 实机：bitblt 抓屏 + 真实模板匹配，发真实键鼠（游戏需在前台）
            from host.device import (BitBltCapture, ensure_foreground,
                                     find_window)
            from host.hud import HudProbe
            from host.vision import Matcher
            hwnd = find_window(title_contains=title, pid=pid)
            focused = ensure_foreground(hwnd)
            logger.write({"e": "activate", "hwnd": hex(hwnd) if hwnd else 0,
                          "ok": focused})
            if not focused:
                raise RuntimeError("游戏未能切到前台，已取消（未发任何按键）")
            capture = BitBltCapture()
            task.vision = Matcher(0.7)
            task.screen_provider = capture.grab
            probe = HudProbe(frame_provider=capture.grab)
            task.team_probe = lambda: probe.team_status()
            # 技能是否可用同样看 HUD：E 图标在 → 可用；按下后图标消失就停手
            task.skill_state = lambda box: probe.hit_any(
                [f"{char.name.lower()}_e"]) is not None
            # 起手前确认强化 E 图标在屏上，否则一个键都不发（避免误切人）
            if probe.hit_any([f"{char.name.lower()}_e"]) is None:
                raise RuntimeError("qingxiao_e 未命中，已取消（未发任何按键）")
        else:
            # 起手：模拟强化 E 图标在开局 0.5s 内可读
            task.hud_templates = {}   # dry-run 用剧本视觉，不查真实模板文件
            task.template_root = None
            task.vision = ScriptedVision({str(Labels.qingxiao_e): (0.0, 0.15)})
            task.screen_provider = lambda: Frame(1, 1, b"\xff\xff\xff\xff", 0.0)
            # 模拟“按下 E 后图标熄灭”，避免 dry-run 一直连按到超时
            pressed = {"e": False}
            original_send_key = task.send_key

            def send_key(key, *a, **k):
                if str(key) == task.get_resonance_key():
                    pressed["e"] = True
                return original_send_key(key, *a, **k)

            task.send_key = send_key
            task.skill_state = lambda box: not pressed["e"]
            task.in_team = lambda: (True, 0, 1)

        logger.write({"e": "opener_start", "char": char_name, "live": live})
        char.do_perform()
        logger.write({"e": "opener_ok", "char": char.name,
                      "events": len(task.input.events)})
    except Exception as exc:  # noqa: BLE001
        import traceback
        result.errors.append(f"{type(exc).__name__}: {exc}")
        logger.write({"e": "opener_fail", "error": result.errors[-1],
                      "traceback": traceback.format_exc()})
    finally:
        if capture is not None:
            capture.close()
        logger.close()

    result.events = task.input.events
    result.log_path = str(log_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kl-working", required=True)
    parser.add_argument("--char", default="Qingxiao")
    parser.add_argument("--log", default=None)
    parser.add_argument("--live", action="store_true",
                        help="真实抓屏+发键（需游戏在前台，Qingxiao 在场）")
    parser.add_argument("--title", default="鸣潮")
    parser.add_argument("--pid", type=int, default=None)
    args = parser.parse_args(argv)

    result = run_opener(args.kl_working, args.char, args.log, live=args.live,
                        title=args.title, pid=args.pid)
    keys = [event.get("key") for event in result.events if event["type"] == "key"]
    clicks = sum(1 for event in result.events if event["type"] == "click")
    print(f"char={result.char} events={len(result.events)} clicks={clicks} "
          f"keys={keys} errors={result.errors}")
    print(f"log={result.log_path}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
