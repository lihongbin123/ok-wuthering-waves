"""视觉层：模板匹配（OpenCV），输入为新宿主的 Frame（BGRA）。

只做一件事：给一个模板图，在整屏或指定区域内找它，返回位置与分数。
颜色/分辨率统一在 Frame 层处理，这里不依赖旧框架的 CV 工具。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from host.device import Frame

# 模板文件名里带设计分辨率，例如 qingxiao_e_3840x2160_3205_1892.png
_DESIGN_SIZE_RE = re.compile(r"_(?P<w>\d{3,5})x(?P<h>\d{3,5})_")


@dataclass
class Match:
    x: int
    y: int
    width: int
    height: int
    score: float

    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def frame_to_gray(frame: Frame) -> np.ndarray:
    image = np.frombuffer(frame.bgra, dtype=np.uint8).reshape(frame.height, frame.width, 4)
    return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)


def load_gray(path: str | Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError(path)
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


class Matcher:
    def __init__(self, threshold: float = 0.8):
        self.threshold = threshold
        self._cache: dict[str, np.ndarray] = {}

    def template(self, path: str | Path) -> np.ndarray:
        key = str(path)
        if key not in self._cache:
            self._cache[key] = load_gray(key)
        return self._cache[key]

    def match(self, frame: Frame, template: str | Path | np.ndarray,
              region: tuple[int, int, int, int] | None = None,
              threshold: float | None = None) -> Match | None:
        gray = frame_to_gray(frame)
        tpl = self.template(template) if isinstance(template, (str, Path)) else template
        if isinstance(template, (str, Path)):
            tpl = self._scale_template(tpl, Path(template).name, frame.width)
        offset_x = offset_y = 0
        if region:
            x, y, w, h = region
            gray = gray[y:y + h, x:x + w]
            offset_x, offset_y = x, y
        if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
            return None
        result = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        limit = self.threshold if threshold is None else threshold
        if score < limit:
            return None
        return Match(loc[0] + offset_x, loc[1] + offset_y,
                     tpl.shape[1], tpl.shape[0], float(score))

    @staticmethod
    def _scale_template(tpl: np.ndarray, filename: str, frame_width: int) -> np.ndarray:
        """按模板文件名里的设计分辨率缩放到当前帧宽度（4K 模板跑 2K 屏也能用）。"""
        match = _DESIGN_SIZE_RE.search(filename)
        if not match:
            return tpl
        design_width = int(match.group("w"))
        if design_width <= 0 or design_width == frame_width:
            return tpl
        ratio = frame_width / design_width
        size = (max(1, round(tpl.shape[1] * ratio)), max(1, round(tpl.shape[0] * ratio)))
        return cv2.resize(tpl, size, interpolation=cv2.INTER_AREA)
