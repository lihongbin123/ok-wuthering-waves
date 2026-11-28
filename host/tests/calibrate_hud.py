"""实机 HUD 校准：激活游戏 → 4K 抓屏 → 按 config 的 region/threshold 匹配。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host import device  # noqa: E402  导入即声明 DPI 感知
from host.config import LOG_DIR, HostConfig  # noqa: E402
from host.device import BitBltCapture, ensure_foreground, find_window  # noqa: E402
from host.vision import Matcher  # noqa: E402


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="鸣潮")
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--save", default=str(LOG_DIR / "hud-calibration.png"))
    parser.add_argument("--out", default=str(LOG_DIR / "hud-calibration.json"))
    args = parser.parse_args(argv)

    cfg = HostConfig.load()
    hwnd = find_window(title_contains=args.title, pid=args.pid)
    print("dpi", device.DPI_MODE, "hwnd", hex(hwnd) if hwnd else 0)
    if hwnd:
        print("focused", ensure_foreground(hwnd))
        time.sleep(0.6)

    cap = BitBltCapture()
    frame = cap.grab()
    cap.close()
    image = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    cv2.imwrite(args.save, cv2.cvtColor(image, cv2.COLOR_BGRA2BGR))
    print(f"frame {frame.width}x{frame.height} -> {args.save}")

    root = Path(cfg.get("hud.template_root"))
    measured = {}
    for name, spec in cfg.get("hud.templates", {}).items():
        match = Matcher(spec["threshold"]).match(
            frame, root / spec["file"], region=tuple(spec["region"]))
        measured[name] = None if not match else {
            "x": match.x, "y": match.y, "score": round(match.score, 4)}
        print(f"{name}: {measured[name]}")
    Path(args.out).write_text(json.dumps(measured, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print("saved", args.out)
    return 0 if all(measured.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
