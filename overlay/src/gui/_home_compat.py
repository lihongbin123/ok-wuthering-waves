"""KGR 版 HomeTab 兼容垫片。

coco-ww 的 DashboardTab 只依赖 HomeTab 里的 ACCENT 和 Avatar 两样东西，
KGR 没有 HomeTab，这里按同样的接口最小实现一份，不改 DashboardTab 的布局。
"""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

ACCENT = '#F2C14E'
WHITE = '#FFFFFF'
CHAR_HEADS = Path(__file__).resolve().parents[2] / 'assets' / 'char_heads'


def _cover_rect(src_w, src_h, dst_w, dst_h):
    """按铺满目标区域的比例，返回源图上居中的裁剪矩形。"""
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return 0.0, 0.0, float(src_w), float(src_h)
    scale = max(dst_w / src_w, dst_h / src_h)
    width = dst_w / scale
    height = dst_h / scale
    return (src_w - width) / 2, (src_h - height) / 2, width, height


class Avatar(QWidget):
    """角色头像：assets/char_heads/<类名>.png 剪成圆，没有图就画名字的第一个字。"""

    def __init__(self, size=60, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = None
        self._text = ''
        self._key = None

    def set_char(self, cls_name, label):
        if cls_name == self._key:
            return
        self._key = cls_name
        path = CHAR_HEADS / f'{cls_name}.png'
        pixmap = QPixmap(str(path)) if cls_name and path.exists() else QPixmap()
        self._pixmap = None if pixmap.isNull() else pixmap
        self._text = (label or cls_name or '?')[:1]
        self.setToolTip(label or cls_name or '')
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)
        painter.fillPath(path, QColor('#F4F4F5') if self._pixmap else QColor(255, 255, 255, 40))
        if self._pixmap is not None:
            x, y, w, h = _cover_rect(self._pixmap.width(), self._pixmap.height(), rect.width(), rect.height())
            painter.drawPixmap(rect, self._pixmap, QRectF(x, y, w, h))
        else:
            painter.setPen(QColor(WHITE))
            font = painter.font()
            font.setPixelSize(int(self.height() * 0.36))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()
