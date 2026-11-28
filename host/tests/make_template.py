"""把整屏帧裁成模板，验证 bitblt 抓屏 + 模板匹配链路。"""
import sys

import cv2

source, target, x, y, w, h = sys.argv[1], sys.argv[2], *map(int, sys.argv[3:7])
image = cv2.imread(source)
if image is None:
    raise SystemExit(f"cannot read {source}")
crop = image[y:y + h, x:x + w]
cv2.imwrite(target, crop)
print(f"template {target} {crop.shape[1]}x{crop.shape[0]} from ({x},{y})")
