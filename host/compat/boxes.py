"""屏幕区域与颜色统计：替代旧框架的 Box / calculate_color_percentage。"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from host.device import Frame


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    frame: Frame | None = None
    confidence: float = 0.0
    name: str | None = None

    @property
    def width(self) -> int:
        return self.w

    @property
    def height(self) -> int:
        return self.h

    def crop(self):
        if self.frame is None:
            return None
        image = np.frombuffer(self.frame.bgra, dtype=np.uint8).reshape(
            self.frame.height, self.frame.width, 4)
        return image[self.y:self.y + self.h, self.x:self.x + self.w]

    def crop_frame(self, frame: Frame | None = None):
        target = frame or self.frame
        if target is None:
            return None
        image = np.frombuffer(target.bgra, dtype=np.uint8).reshape(
            target.height, target.width, 4)
        return image[self.y:self.y + self.h, self.x:self.x + self.w]


def scale_region(design_w: int, design_h: int, frame: Frame,
                 x1: int, y1: int, x2: int, y2: int) -> Box:
    sx = frame.width / float(design_w)
    sy = frame.height / float(design_h)
    x = max(0, int(round(x1 * sx)))
    y = max(0, int(round(y1 * sy)))
    w = max(1, int(round((x2 - x1) * sx)))
    h = max(1, int(round((y2 - y1) * sy)))
    return Box(x, y, w, h, frame)


def color_percentage(color_bgr, box: Box, tolerance: int = 12) -> float:
    """返回 box 内接近目标颜色的像素占比（0~1）。支持 {'r':(lo,hi),...} 区间。"""
    if box is None or box.frame is None:
        return 0.0
    crop = box.crop()
    if crop is None or crop.size == 0:
        return 0.0
    if isinstance(color_bgr, dict):
        low = np.array([color_bgr["b"][0], color_bgr["g"][0], color_bgr["r"][0]], np.uint8)
        high = np.array([color_bgr["b"][1], color_bgr["g"][1], color_bgr["r"][1]], np.uint8)
        mask = cv2.inRange(crop[:, :, :3], low, high)
        return float(mask.mean() / 255)
    bgr = crop[:, :, :3].astype(np.int16)
    target = np.array(color_bgr[:3], dtype=np.int16)
    near = np.all(np.abs(bgr - target) <= tolerance, axis=2)
    return float(near.mean())


def white_percentage(box: Box, threshold: int = 235) -> float:
    if box is None or box.frame is None:
        return 0.0
    crop = box.crop()
    if crop is None or crop.size == 0:
        return 0.0
    bgr = crop[:, :, :3].astype(np.int16)
    return float(np.all(bgr >= threshold, axis=2).mean())
