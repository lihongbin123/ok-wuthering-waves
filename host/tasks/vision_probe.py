"""视觉探针：抓一帧，在整屏/指定区域找模板。

  python -m host.tasks.vision_probe --template <png> [--capture bitblt|fake]
  python -m host.tasks.vision_probe --template <png> --frame <png>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from host.device import BitBltCapture, FakeCapture, Frame
from host.vision import Matcher


def frame_from_png(path: str | Path) -> Frame:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError(path)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return Frame(image.shape[1], image.shape[0], image.tobytes(), 0.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--frame", default=None)
    parser.add_argument("--capture", choices=["fake", "bitblt", "window"], default="fake")
    parser.add_argument("--region", default=None, help="x,y,w,h")
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--save", default=None, help="把抓到的帧存成 png，便于校准")
    parser.add_argument("--title", default=None, help="窗口模式：标题关键字")
    parser.add_argument("--pid", type=int, default=None, help="窗口模式：进程号")
    args = parser.parse_args(argv)

    region = tuple(int(v) for v in args.region.split(",")) if args.region else None
    if args.frame:
        frame = frame_from_png(args.frame)
    elif args.capture == "bitblt":
        frame = BitBltCapture().grab()
    elif args.capture == "window":
        from host.device import WindowCapture
        frame = WindowCapture(title=args.title, pid=args.pid).grab()
    else:
        frame = FakeCapture(1920, 1080).grab()

    if args.save:
        import cv2 as _cv2
        import numpy as _np
        image = _np.frombuffer(frame.bgra, dtype=_np.uint8).reshape(frame.height, frame.width, 4)
        _cv2.imwrite(args.save, _cv2.cvtColor(image, _cv2.COLOR_BGRA2BGR))
        print(f"frame saved {args.save} ({frame.width}x{frame.height})")

    match = Matcher(args.threshold).match(frame, args.template, region=region)
    if match:
        print(f"found x={match.x} y={match.y} w={match.width} h={match.height} score={match.score:.4f}")
        return 0
    print("not found")
    return 2


if __name__ == "__main__":
    sys.exit(main())
