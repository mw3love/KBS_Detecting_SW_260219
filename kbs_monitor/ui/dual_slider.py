"""
HSV 듀얼 슬라이더 위젯
두 핸들을 드래그하여 값 범위를 선택하는 슬라이더
"""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QLinearGradient, QColor, QPen, QBrush


class DualSlider(QWidget):
    """범위 선택용 듀얼 핸들 슬라이더"""

    range_changed = Signal(int, int)  # low, high 값 변경

    _HANDLE_R = 7     # 핸들 반지름
    _BAR_H = 14       # 슬라이더 바 높이
    _PAD = 12         # 좌우 여백

    def __init__(self, minimum: int = 0, maximum: int = 255,
                 gradient_type: str = "gray", parent=None):
        """
        gradient_type: 'hue' | 'saturation' | 'value' | 'gray'
        """
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._low = minimum
        self._high = maximum
        self._gradient_type = gradient_type
        self._dragging = None  # 'low' | 'high' | None
        self.setMinimumSize(180, 36)
        self.setCursor(Qt.PointingHandCursor)

    # ── 공개 API ──────────────────────────────────────

    def get_range(self) -> tuple:
        return self._low, self._high

    def set_range(self, low: int, high: int):
        self._low = max(self._min, min(low, self._max))
        self._high = max(self._min, min(high, self._max))
        if self._low > self._high:
            self._low, self._high = self._high, self._low
        self.update()
        self.range_changed.emit(self._low, self._high)

    def set_gradient_type(self, gradient_type: str):
        self._gradient_type = gradient_type
        self.update()

    # ── 좌표 변환 ─────────────────────────────────────

    def _val_to_x(self, val: int) -> int:
        track_w = self.width() - self._PAD * 2
        ratio = (val - self._min) / max(1, self._max - self._min)
        return self._PAD + int(ratio * track_w)

    def _x_to_val(self, x: int) -> int:
        track_w = self.width() - self._PAD * 2
        ratio = (x - self._PAD) / max(1, track_w)
        ratio = max(0.0, min(1.0, ratio))
        return self._min + int(ratio * (self._max - self._min))

    # ── 그리기 ────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        bar_y = (self.height() - self._BAR_H) // 2
        bar_w = self.width() - self._PAD * 2
        bar_rect = QRect(self._PAD, bar_y, bar_w, self._BAR_H)

        # 배경 그라디언트
        grad = QLinearGradient(bar_rect.left(), 0, bar_rect.right(), 0)
        self._fill_gradient(grad)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(bar_rect, 4, 4)

        # 선택 범위 외부 마스크 (어둡게)
        lx = self._val_to_x(self._low)
        hx = self._val_to_x(self._high)
        mask_color = QColor(0, 0, 0, 130)
        p.setBrush(mask_color)
        if lx > self._PAD:
            p.drawRect(QRect(self._PAD, bar_y, lx - self._PAD, self._BAR_H))
        right_end = self._PAD + bar_w
        if hx < right_end:
            p.drawRect(QRect(hx, bar_y, right_end - hx, self._BAR_H))

        # 바 테두리
        p.setPen(QPen(QColor(90, 90, 100), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(bar_rect, 4, 4)

        # 핸들
        cy = self.height() // 2
        self._draw_handle(p, lx, cy)
        self._draw_handle(p, hx, cy)

        p.end()

    def _fill_gradient(self, grad: QLinearGradient):
        if self._gradient_type == "hue":
            # H: 0~179 (OpenCV) → QColor.fromHsv (0~359)
            steps = 24
            for i in range(steps + 1):
                pos = i / steps
                hue = int(pos * 179 * 2)  # OpenCV H*2 → 0~358
                grad.setColorAt(pos, QColor.fromHsv(hue, 220, 210))
        elif self._gradient_type == "saturation":
            # 무채색 → 채도 최대
            grad.setColorAt(0.0, QColor(210, 210, 210))
            grad.setColorAt(1.0, QColor(220, 40, 40))
        elif self._gradient_type == "value":
            # 검정 → 흰색
            grad.setColorAt(0.0, QColor(15, 15, 15))
            grad.setColorAt(1.0, QColor(220, 220, 220))
        else:  # gray
            grad.setColorAt(0.0, QColor(30, 30, 35))
            grad.setColorAt(1.0, QColor(200, 200, 210))

    def _draw_handle(self, p: QPainter, x: int, cy: int):
        p.setPen(QPen(QColor(210, 210, 220), 2))
        p.setBrush(QBrush(QColor(70, 70, 90)))
        r = self._HANDLE_R
        p.drawEllipse(x - r, cy - r, r * 2, r * 2)

    # ── 마우스 이벤트 ─────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x = event.position().x()
        lx = self._val_to_x(self._low)
        hx = self._val_to_x(self._high)
        if abs(x - lx) <= abs(x - hx):
            self._dragging = "low"
        else:
            self._dragging = "high"
        self._update_drag(x)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._update_drag(event.position().x())

    def mouseReleaseEvent(self, event):
        self._dragging = None

    def _update_drag(self, x: float):
        val = self._x_to_val(int(x))
        if self._dragging == "low":
            self._low = max(self._min, min(val, self._high))
        elif self._dragging == "high":
            self._high = max(self._low, min(val, self._max))
        self.update()
        self.range_changed.emit(self._low, self._high)
