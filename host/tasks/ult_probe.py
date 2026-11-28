"""大招采样：等大招图标亮起→按 R→采样队伍栏与帧差，验证 in_team 的 True→False→True。

  python -m host.tasks.ult_probe --pid 21652 --wait-seconds 600
没有能量时只待命，不按键。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from host import device  # noqa: E402  DPI 感知
from host.config import LOG_DIR  # noqa: E402
from host.device import BitBltCapture, Input, ensure_foreground, find_window  # noqa: E402
from host.hud import HudProbe  # noqa: E402
from host.scheduler import EventLog  # noqa: E402

LIB_REGION = (3480, 1810, 340, 170)   # 大招按钮区域（4K）


def region_mean(frame, region=LIB_REGION) -> float:
    image = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    x, y, w, h = region
    crop = image[y:y + h, x:x + w, :3]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(gray.mean())


def save_frame(frame, path: Path) -> None:
    image = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_BGRA2BGR))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="鸣潮")
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--wait-seconds", type=float, default=600)
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--rise", type=float, default=1.12, help="亮度相对基线涨幅")
    parser.add_argument("--confirm", type=int, default=3, help="连续命中几次才按 R")
    parser.add_argument("--press-now", action="store_true",
                        help="能量已满：跳过等待，确认队伍栏后立即按 R")
    parser.add_argument("--key", default="r")
    parser.add_argument("--log", default=str(LOG_DIR / "ult-sample.jsonl"))
    args = parser.parse_args(argv)

    hwnd = find_window(title_contains=args.title, pid=args.pid)
    focused = ensure_foreground(hwnd)
    print("focused", focused, flush=True)
    if not focused:
        print("游戏未在前台，退出", flush=True)
        return 1

    logger = EventLog(Path(args.log))
    cap = BitBltCapture()
    probe = HudProbe(frame_provider=cap.grab)
    base_frame = cap.grab()
    baseline = region_mean(base_frame)
    logger.write({"e": "baseline", "mean": round(baseline, 2),
                  "region": list(LIB_REGION)})
    print("baseline_mean", round(baseline, 2), "waiting for ult icon...", flush=True)

    start = time.time()
    ready_frame = None
    last_log = 0.0
    hits = 0
    if args.press_now:
        frame = cap.grab()
        if not probe.team_status(frame)[0]:
            logger.write({"e": "abort", "reason": "team not detected"})
            logger.close()
            cap.close()
            print("队伍栏未识别，取消（未按键）", flush=True)
            return 1
        ready_frame = frame
        logger.write({"e": "ready", "mean": round(region_mean(frame), 2),
                      "force": True})
        print("press-now: 立即按 R", flush=True)
    while not args.press_now and time.time() - start < args.wait_seconds:
        if time.time() - last_log > 10:
            last_log = time.time()
            print("waiting", round(time.time() - start, 1), "s", flush=True)
        frame = cap.grab()
        mean = region_mean(frame)
        team_ok = probe.team_status(frame)[0]
        if not team_ok:
            # 不在正常主世界（读不到队伍栏）时一律不判定、不按 R
            hits = 0
            time.sleep(0.5)
            continue
        if mean >= baseline * args.rise and mean - baseline >= 5:
            hits += 1
        else:
            hits = 0
        if hits >= max(1, args.confirm):
            ready_frame = frame
            break
        time.sleep(0.5)

    if ready_frame is None:
        logger.write({"e": "standby", "reason": "ult icon not detected",
                      "baseline": round(baseline, 2)})
        logger.close()
        cap.close()
        print("standby: 大招未亮起，未按键", flush=True)
        return 2

    save_frame(ready_frame, LOG_DIR / "ult-ready.png")
    logger.write({"e": "ready", "mean": round(region_mean(ready_frame), 2)})
    before = probe.team_status(ready_frame)
    logger.write({"e": "before", "value": before[0], "current": before[1],
                  "count": before[2]})
    print("ready", before, flush=True)

    inp = Input(dry_run=False)
    inp.key(args.key)
    logger.write({"e": "send_key", "key": args.key})

    pre = ready_frame
    pre_bytes = np.frombuffer(pre.bgra, np.uint8)
    start = time.time()
    samples = []
    transitions = 0
    last_state = None
    while time.time() - start < args.seconds:
        frame = cap.grab()
        ok, current, count = probe.team_status(frame)
        e_match = probe._match(frame, "qingxiao_e")
        h1_match = probe._match(frame, "qingxiao_h1")
        diff = float(np.abs(
            np.frombuffer(frame.bgra, np.uint8).astype(np.int16)
            - pre_bytes.astype(np.int16)).mean())
        elapsed = round(time.time() - start, 3)
        sample = {
            "t": elapsed, "team": ok, "count": count,
            "e": bool(e_match), "e_score": round(e_match.score, 3) if e_match else 0,
            "h1": bool(h1_match), "h1_score": round(h1_match.score, 3) if h1_match else 0,
            "lib": round(region_mean(frame), 1), "diff": round(diff, 2),
        }
        samples.append(sample)
        state = (ok, count, sample["e"], sample["h1"])
        if state != last_state:
            path = LOG_DIR / f"ult-change-{transitions}.png"
            save_frame(frame, path)
            logger.write({"e": "state_change", **sample, "frame": str(path)})
            transitions += 1
            last_state = state
        time.sleep(0.02)
    cap.close()

    compressed = []
    for item in samples:
        key = (item["team"], item["count"], item["e"], item["h1"])
        if not compressed or compressed[-1][1:] != key:
            compressed.append((item["t"],) + key)
    summary = {
        "samples": len(samples),
        "team_false": sum(1 for s in samples if not s["team"]),
        "e_false": sum(1 for s in samples if not s["e"]),
        "h1_false": sum(1 for s in samples if not s["h1"]),
        "max_diff": max(s["diff"] for s in samples),
        "max_lib": max(s["lib"] for s in samples),
        "compressed": compressed,
    }
    logger.write({"e": "summary", **{k: v for k, v in summary.items()
                                     if k != "compressed"}, "compressed": compressed})
    logger.close()

    (LOG_DIR / "ult-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("samples", summary["samples"], "team_false", summary["team_false"],
          "e_false", summary["e_false"], "h1_false", summary["h1_false"],
          "max_diff", summary["max_diff"], "max_lib", summary["max_lib"], flush=True)
    print("compressed", compressed, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
