"""
감지영역 편집기 (다중 선택 기능 포함)
- ROIEditorCanvas: 마우스/키보드로 감지영역을 드래그 편집하는 위젯
- FullScreenROIEditor: 전체화면 편집 다이얼로그

다중 선택 기능:
- Ctrl + 드래그 (빈 공간): rubber band로 여러 영역 동시 선택
- Ctrl + 클릭: 해당 영역 선택 목록에 추가/제거
- 다중 선택 후 드래그: 선택된 모든 영역 이동
- Shift + 드래그: 수직/수평으로만 이동
- 다중 선택 후 Del: 선택된 모든 영역 삭제
- 다중 선택 후 Ctrl+D: 선택된 모든 영역 복사 (+10, +10)
- 다중 선택 후 Ctrl+드래그: 선택된 영역들 복사하며 드래그 (선택/미선택 상태 모두 지원)
- 다중 선택 후 Ctrl+Shift+드래그: 수직/수평 방향으로 복사
"""
import cv2
import numpy as np
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QDialog,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QSizePolicy,
    QLineEdit, QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QSizeF, QTimer
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPixmap, QImage,
    QCursor, QFont,
)

from core.roi_manager import ROI, ROIManager


# ─────────────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────────────

def _copy_roi(roi: ROI) -> ROI:
    """ROI 깊은 복사"""
    return ROI(
        label=roi.label,
        media_name=roi.media_name,
        x=roi.x, y=roi.y, w=roi.w, h=roi.h,
        roi_type=roi.roi_type,
    )


# ─────────────────────────────────────────────────────
# ROI 편집 캔버스 (반화면/전체화면 공용)
# ─────────────────────────────────────────────────────

class ROIEditorCanvas(QWidget):
    """
    마우스 드래그 + 키보드로 감지영역을 편집하는 캔버스.
    비디오 위젯의 child로 올라가거나(반화면),
    전체화면 다이얼로그 내부에(전체화면) 배치된다.
    """

    rois_changed = Signal()   # ROI 목록 변경 시

    HANDLE_RADIUS = 5          # 핸들 반지름 (위젯 픽셀)
    MIN_ROI_PX = 8             # 새 ROI 최소 크기 (위젯 픽셀)
    ROI_COLOR = QColor("#cc0000")      # 비디오 기본 색상 (빨간색)
    SEL_COLOR = QColor("#ff4444")      # 비디오 선택 색상
    HANDLE_COLOR = QColor("#ffffff")
    OVERLAY_COLOR = QColor(0, 0, 0, 120)  # 반투명 오버레이

    # 핸들 이름: (상대 x 비율, 상대 y 비율)
    _HANDLES = {
        "nw": (0.0, 0.0), "n": (0.5, 0.0), "ne": (1.0, 0.0),
        "w":  (0.0, 0.5),                   "e":  (1.0, 0.5),
        "sw": (0.0, 1.0), "s": (0.5, 1.0), "se": (1.0, 1.0),
    }

    # 핸들별 리사이즈 커서
    _HANDLE_CURSORS = {
        "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
        "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
        "n":  Qt.SizeVerCursor,   "s":  Qt.SizeVerCursor,
        "w":  Qt.SizeHorCursor,   "e":  Qt.SizeHorCursor,
    }

    def __init__(self, roi_manager: ROIManager, roi_type: str = "video", parent=None):
        super().__init__(parent)
        self._roi_manager = roi_manager
        self._roi_type = roi_type   # "video" or "audio"
        self._frame: Optional[np.ndarray] = None
        self._pixmap: Optional[QPixmap] = None
        self._frame_rect = QRectF()  # 프레임이 그려지는 위젯 영역

        # 편집 중인 ROI 작업 복사본
        self._rois: List[ROI] = []
        self._selected_idx = -1
        self._selected_indices: List[int] = []  # 다중 선택 인덱스 목록

        # 드래그 상태 머신:
        # "idle" | "new" | "move" | "resize" |
        # "rubber_band" | "multi_move" | "ctrl_copy"
        self._state = "idle"
        self._drag_start_w = QPointF()   # 드래그 시작점 (위젯 좌표)
        self._drag_start_f = (0, 0)      # 드래그 시작점 (프레임 좌표)
        self._new_rect_f: Optional[tuple] = None   # 새 ROI 미리보기 (fx,fy,fw,fh)
        self._move_origin: Optional[ROI] = None    # 이동 시작 시 ROI 복사본
        self._resize_handle = ""         # 어느 핸들을 드래그 중

        # 다중 선택 관련 상태
        self._rubber_start_w = QPointF()
        self._rubber_rect_w: Optional[QRectF] = None
        self._multi_origins: List[ROI] = []       # 다중 이동/복사 원본
        self._multi_drag_start_f = (0, 0)
        self._ctrl_copy_preview: List[ROI] = []   # ctrl_copy 미리보기
        self._ctrl_copy_offset_f = (0, 0)
        self._ctrl_copy_shift = False              # Ctrl+Shift 방향 고정

        # ctrl_copy 드래그 없이 클릭만 한 경우 선택 토글 처리용
        self._ctrl_press_idx: int = -1            # ctrl 클릭한 ROI 인덱스
        self._ctrl_was_selected: bool = False     # 클릭 시 이미 선택됐었는지 여부

        # 키보드 이동 debounce 타이머 (버벅거림 방지)
        self._key_emit_timer = QTimer(self)
        self._key_emit_timer.setSingleShot(True)
        self._key_emit_timer.setInterval(120)
        self._key_emit_timer.timeout.connect(self.rois_changed.emit)

        # 오디오 ROI는 초록색으로 표시
        if roi_type == "audio":
            self.ROI_COLOR = QColor("#00cc00")
            self.SEL_COLOR = QColor("#44ff44")

        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(200, 150)

    # ── 공개 API ──────────────────────────────────────

    def set_frame(self, frame: Optional[np.ndarray]):
        """편집용 정지 프레임 설정"""
        if frame is not None:
            self._frame = frame.copy()
        else:
            self._frame = None
        self._rebuild_pixmap()
        self.update()

    def load_rois(self):
        """ROI 매니저에서 현재 목록 로드 (편집 시작 또는 외부 변경 시 호출)"""
        src = self._roi_manager.video_rois if self._roi_type == "video" \
              else self._roi_manager.audio_rois
        self._rois = [_copy_roi(r) for r in src]
        self._selected_idx = -1
        self._selected_indices = []
        self.update()

    def apply_rois(self):
        """편집된 ROI를 ROI 매니저에 반영"""
        if self._roi_type == "video":
            self._roi_manager.replace_video_rois(self._rois)
        else:
            self._roi_manager.replace_audio_rois(self._rois)
        self.rois_changed.emit()

    def get_rois(self) -> List[ROI]:
        return list(self._rois)

    def get_selected_indices(self) -> List[int]:
        """다중 선택 인덱스 목록 반환"""
        return list(self._selected_indices)

    def delete_selected(self):
        """선택된 ROI 삭제 (다중 선택 지원)"""
        if self._selected_indices:
            indices = sorted(self._selected_indices, reverse=True)
        elif 0 <= self._selected_idx < len(self._rois):
            indices = [self._selected_idx]
        else:
            return

        for idx in indices:
            if 0 <= idx < len(self._rois):
                self._rois.pop(idx)

        self._relabel()
        self._selected_indices = []
        self._selected_idx = -1
        self.rois_changed.emit()
        self.update()

    def copy_selected(self):
        """선택된 ROI 복사 (다중 선택 지원, x/y +10씩 이동)"""
        if self._selected_indices:
            sources = [self._rois[i] for i in self._selected_indices
                       if 0 <= i < len(self._rois)]
            new_rois = []
            for src in sources:
                new_roi = _copy_roi(src)
                new_roi.x = min(new_roi.x + 10, 1900)
                new_roi.y = min(new_roi.y + 10, 1060)
                new_rois.append(new_roi)
            self._rois.extend(new_rois)
            self._relabel()
            n = len(self._rois)
            self._selected_indices = list(range(n - len(new_rois), n))
            self._selected_idx = self._selected_indices[-1]
            self.rois_changed.emit()
            self.update()
        elif 0 <= self._selected_idx < len(self._rois):
            src = self._rois[self._selected_idx]
            new_roi = _copy_roi(src)
            new_roi.x = min(new_roi.x + 20, 1900)
            new_roi.y = min(new_roi.y + 20, 1060)
            self._rois.append(new_roi)
            self._relabel()
            self._selected_idx = len(self._rois) - 1
            self._selected_indices = [self._selected_idx]
            self.rois_changed.emit()
            self.update()

    # ── 좌표 변환 ──────────────────────────────────────

    def _update_frame_rect(self):
        """프레임 표시 영역(QRectF)을 재계산"""
        if self._frame is None:
            self._frame_rect = QRectF()
            return
        fh, fw = self._frame.shape[:2]
        w, h = self.width(), self.height()
        scale = min(w / fw, h / fh)
        dw = fw * scale
        dh = fh * scale
        ox = (w - dw) / 2
        oy = (h - dh) / 2
        self._frame_rect = QRectF(ox, oy, dw, dh)

    def _w2f(self, wx: float, wy: float) -> tuple:
        """위젯 좌표 → 프레임 좌표"""
        if self._frame is None or self._frame_rect.isEmpty():
            return (int(wx), int(wy))
        fh, fw = self._frame.shape[:2]
        r = self._frame_rect
        fx = (wx - r.x()) / r.width() * fw
        fy = (wy - r.y()) / r.height() * fh
        return (int(max(0, min(fw, fx))), int(max(0, min(fh, fy))))

    def _f2w(self, fx: float, fy: float) -> QPointF:
        """프레임 좌표 → 위젯 좌표"""
        if self._frame is None or self._frame_rect.isEmpty():
            return QPointF(fx, fy)
        fh, fw = self._frame.shape[:2]
        r = self._frame_rect
        wx = r.x() + fx / fw * r.width()
        wy = r.y() + fy / fh * r.height()
        return QPointF(wx, wy)

    def _roi_to_wrect(self, roi: ROI) -> QRectF:
        """ROI → 위젯 좌표 QRectF"""
        tl = self._f2w(roi.x, roi.y)
        br = self._f2w(roi.x + roi.w, roi.y + roi.h)
        return QRectF(tl, br)

    def _handle_points(self, roi: ROI) -> dict:
        """선택된 ROI의 핸들 중심점 반환 (위젯 좌표)"""
        r = self._roi_to_wrect(roi)
        pts = {}
        for name, (rx, ry) in self._HANDLES.items():
            pts[name] = QPointF(r.x() + rx * r.width(), r.y() + ry * r.height())
        return pts

    def _hit_handle(self, roi: ROI, wx: float, wy: float) -> str:
        """위젯 좌표가 ROI 핸들에 해당하는지 반환. 해당 없으면 ''"""
        handles = self._handle_points(roi)
        pt = QPointF(wx, wy)
        for name, center in handles.items():
            if (pt - center).manhattanLength() <= self.HANDLE_RADIUS * 2.5:
                return name
        return ""

    def _hit_roi_body(self, wx: float, wy: float) -> int:
        """위젯 좌표가 어느 ROI 몸통에 해당하는지 반환. -1이면 없음"""
        pt = QPointF(wx, wy)
        for i in range(len(self._rois) - 1, -1, -1):
            r = self._roi_to_wrect(self._rois[i])
            if r.contains(pt):
                return i
        return -1

    # ── 그리기 ─────────────────────────────────────────

    def _rebuild_pixmap(self):
        self._update_frame_rect()
        if self._frame is None:
            self._pixmap = None
            return
        try:
            h, w = self._frame.shape[:2]
            ch = self._frame.shape[2] if self._frame.ndim == 3 else 1
            if ch == 3:
                rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
            elif ch == 1:
                rgb = cv2.cvtColor(self._frame, cv2.COLOR_GRAY2RGB)
                ch = 3
            else:
                rgb = self._frame[:, :, :3].copy()
                rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)
                ch = 3
            # rgb.tobytes()로 복사본 전달 → numpy gc 후 dangling pointer 방지
            img = QImage(rgb.tobytes(), w, h, ch * w, QImage.Format_RGB888)
            self._pixmap = QPixmap.fromImage(img)
        except Exception:
            self._pixmap = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 배경
        painter.fillRect(self.rect(), QColor("#0d0d1a"))

        # 프레임
        if self._pixmap and not self._frame_rect.isEmpty():
            painter.drawPixmap(self._frame_rect.toRect(), self._pixmap)
            # 프레임 바깥 반투명 오버레이
            painter.fillRect(self.rect(), self.OVERLAY_COLOR)
            # 프레임 영역만 다시 클리어
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(self._frame_rect.toRect(), Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.drawPixmap(self._frame_rect.toRect(), self._pixmap)

        # ROI 그리기
        multi_set = set(self._selected_indices)
        for i, roi in enumerate(self._rois):
            is_selected = (i == self._selected_idx) or (i in multi_set)
            # 선택된 모든 항목에 8점 핸들 표시 (단일/다중 선택 공통)
            show_handles = is_selected
            self._draw_roi(painter, roi, is_selected, show_handles)

        # 새 ROI 미리보기
        if self._state == "new" and self._new_rect_f:
            fx, fy, fw, fh = self._new_rect_f
            if fw > 0 and fh > 0:
                tl = self._f2w(fx, fy)
                br = self._f2w(fx + fw, fy + fh)
                r = QRectF(tl, br)
                pen = QPen(QColor("#ff8888"), 1.5, Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(QBrush(QColor(255, 100, 100, 40)))
                painter.drawRect(r)

        # Rubber band 선택 사각형
        if self._state == "rubber_band" and self._rubber_rect_w:
            pen = QPen(QColor("#88ccff"), 1.5, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(100, 150, 255, 30)))
            painter.drawRect(self._rubber_rect_w)

        # Ctrl+드래그 복사 미리보기
        if self._state == "ctrl_copy" and self._ctrl_copy_preview:
            for preview in self._ctrl_copy_preview:
                r = self._roi_to_wrect(preview)
                pen = QPen(QColor("#ffcc44"), 1.5, Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(QBrush(QColor(255, 200, 68, 40)))
                painter.drawRect(r)

        painter.end()

    def _draw_roi(self, painter: QPainter, roi: ROI, selected: bool, show_handles: bool = True):
        r = self._roi_to_wrect(roi)
        color = self.SEL_COLOR if selected else self.ROI_COLOR

        # 테두리
        pen = QPen(color, 2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)

        # 라벨 (좌상단) - 매체명 포함
        if roi.media_name:
            display_label = f"{roi.label} [{roi.media_name}]"
        else:
            display_label = roi.label
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.drawText(r.adjusted(3, 2, 0, 0), Qt.AlignTop | Qt.AlignLeft, display_label)

        # 핸들 표시 (단일 선택 시에만)
        if show_handles:
            handles = self._handle_points(roi)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(QBrush(color))
            for pt in handles.values():
                painter.drawEllipse(pt, self.HANDLE_RADIUS, self.HANDLE_RADIUS)

    # ── 마우스 이벤트 ──────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        wx, wy = event.position().x(), event.position().y()
        self._drag_start_w = event.position()
        self._drag_start_f = self._w2f(wx, wy)
        self.setFocus()

        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        if ctrl:
            idx = self._hit_roi_body(wx, wy)
            if idx >= 0:
                # ctrl 클릭한 ROI 기록 (release 시 토글 여부 결정)
                self._ctrl_press_idx = idx
                self._ctrl_was_selected = (idx in self._selected_indices)

                # 아직 선택 목록에 없으면 추가
                if idx not in self._selected_indices:
                    self._selected_indices.append(idx)
                self._selected_idx = idx

                # Ctrl+드래그 복사 준비 (선택된 것이 있으면 항상)
                self._state = "ctrl_copy"
                self._multi_origins = [_copy_roi(self._rois[i])
                                       for i in self._selected_indices]
                self._multi_drag_start_f = self._drag_start_f
                self._ctrl_copy_shift = shift
                self._ctrl_copy_preview = []
                self._ctrl_copy_offset_f = (0, 0)
            else:
                # Ctrl+드래그 빈 공간: rubber band 선택
                self._ctrl_press_idx = -1
                self._state = "rubber_band"
                self._rubber_start_w = event.position()
                self._rubber_rect_w = QRectF(event.position(), QSizeF(0, 0))
            self.update()
            return

        # ── Ctrl 없는 일반 클릭 ──

        idx = self._hit_roi_body(wx, wy)

        # 다중 선택된 ROI 위 클릭 → multi_move
        if len(self._selected_indices) > 1 and idx >= 0 and idx in self._selected_indices:
            self._state = "multi_move"
            self._multi_origins = [_copy_roi(self._rois[i])
                                   for i in self._selected_indices]
            self._multi_drag_start_f = self._drag_start_f
            self.update()
            return

        # 다중 선택 초기화
        self._selected_indices = []

        # 1. 선택된 ROI의 핸들 확인
        if 0 <= self._selected_idx < len(self._rois):
            h = self._hit_handle(self._rois[self._selected_idx], wx, wy)
            if h:
                self._state = "resize"
                self._resize_handle = h
                self._move_origin = _copy_roi(self._rois[self._selected_idx])
                return

        # 2. ROI 몸통 확인
        if idx >= 0:
            self._selected_idx = idx
            self._selected_indices = [idx]
            self._state = "move"
            self._move_origin = _copy_roi(self._rois[idx])
            self.update()
            return

        # 3. 빈 공간 → 새 ROI
        self._selected_idx = -1
        self._state = "new"
        fx, fy = self._drag_start_f
        self._new_rect_f = (fx, fy, 0, 0)
        self.update()

    def mouseMoveEvent(self, event):
        wx, wy = event.position().x(), event.position().y()
        fx, fy = self._w2f(wx, wy)

        if self._state == "rubber_band":
            sx = min(self._rubber_start_w.x(), wx)
            sy = min(self._rubber_start_w.y(), wy)
            ex = max(self._rubber_start_w.x(), wx)
            ey = max(self._rubber_start_w.y(), wy)
            self._rubber_rect_w = QRectF(sx, sy, ex - sx, ey - sy)
            self.update()

        elif self._state == "multi_move":
            dfx = fx - self._multi_drag_start_f[0]
            dfy = fy - self._multi_drag_start_f[1]
            # Shift: 수직/수평 고정
            if bool(event.modifiers() & Qt.ShiftModifier):
                if abs(dfx) >= abs(dfy):
                    dfy = 0
                else:
                    dfx = 0
            for idx, orig in zip(self._selected_indices, self._multi_origins):
                if 0 <= idx < len(self._rois):
                    roi = self._rois[idx]
                    new_x = max(0, orig.x + dfx)
                    new_y = max(0, orig.y + dfy)
                    if self._frame is not None:
                        fh, fw = self._frame.shape[:2]
                        new_x = min(new_x, fw - roi.w)
                        new_y = min(new_y, fh - roi.h)
                    roi.x = int(new_x)
                    roi.y = int(new_y)
            self.update()

        elif self._state == "ctrl_copy":
            dfx = fx - self._multi_drag_start_f[0]
            dfy = fy - self._multi_drag_start_f[1]
            # Ctrl+Shift: 수직/수평 방향 고정
            if self._ctrl_copy_shift:
                if abs(dfx) >= abs(dfy):
                    dfy = 0
                else:
                    dfx = 0
            self._ctrl_copy_offset_f = (dfx, dfy)
            # 복사본 미리보기 갱신
            self._ctrl_copy_preview = []
            for orig in self._multi_origins:
                preview = _copy_roi(orig)
                new_x = int(max(0, orig.x + dfx))
                new_y = int(max(0, orig.y + dfy))
                if self._frame is not None:
                    fh, fw = self._frame.shape[:2]
                    new_x = min(new_x, fw - orig.w)
                    new_y = min(new_y, fh - orig.h)
                preview.x = new_x
                preview.y = new_y
                self._ctrl_copy_preview.append(preview)
            self.update()

        elif self._state == "new":
            sx, sy = self._drag_start_f
            nfx = min(sx, fx)
            nfy = min(sy, fy)
            nfw = abs(fx - sx)
            nfh = abs(fy - sy)
            self._new_rect_f = (nfx, nfy, nfw, nfh)
            self.update()

        elif self._state == "move" and self._move_origin is not None:
            dfx = fx - self._drag_start_f[0]
            dfy = fy - self._drag_start_f[1]
            # Shift: 수직/수평 고정
            if bool(event.modifiers() & Qt.ShiftModifier):
                if abs(dfx) >= abs(dfy):
                    dfy = 0
                else:
                    dfx = 0
            roi = self._rois[self._selected_idx]
            orig = self._move_origin
            new_x = max(0, orig.x + dfx)
            new_y = max(0, orig.y + dfy)
            if self._frame is not None:
                fh, fw = self._frame.shape[:2]
                new_x = min(new_x, fw - roi.w)
                new_y = min(new_y, fh - roi.h)
            roi.x = int(new_x)
            roi.y = int(new_y)
            self.update()

        elif self._state == "resize" and self._move_origin is not None:
            self._apply_resize(fx, fy)
            self.update()

        else:
            self._update_cursor(wx, wy)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        wx, wy = event.position().x(), event.position().y()

        if self._state == "rubber_band":
            # rubber band 선택 완료 → 사각형과 교차하는 ROI 선택
            if self._rubber_rect_w and not self._rubber_rect_w.isEmpty():
                new_sel = []
                for i, roi in enumerate(self._rois):
                    wr = self._roi_to_wrect(roi)
                    if self._rubber_rect_w.intersects(wr):
                        new_sel.append(i)
                self._selected_indices = new_sel
                self._selected_idx = new_sel[-1] if new_sel else -1
            self._rubber_rect_w = None

        elif self._state == "ctrl_copy":
            # 드래그 거리가 최소 이상이면 복사 확정
            dfx, dfy = self._ctrl_copy_offset_f
            drag_px = max(
                abs(event.position().x() - self._drag_start_w.x()),
                abs(event.position().y() - self._drag_start_w.y()),
            )
            if drag_px >= self.MIN_ROI_PX and self._multi_origins:
                new_rois = []
                for orig in self._multi_origins:
                    new_roi = _copy_roi(orig)
                    new_x = int(max(0, orig.x + dfx))
                    new_y = int(max(0, orig.y + dfy))
                    if self._frame is not None:
                        fh, fw = self._frame.shape[:2]
                        new_x = min(new_x, fw - orig.w)
                        new_y = min(new_y, fh - orig.h)
                    new_roi.x = new_x
                    new_roi.y = new_y
                    new_rois.append(new_roi)
                self._rois.extend(new_rois)
                self._relabel()
                n = len(self._rois)
                self._selected_indices = list(range(n - len(new_rois), n))
                self._selected_idx = (self._selected_indices[-1]
                                      if self._selected_indices else -1)
                self.rois_changed.emit()
            else:
                # 드래그 없이 클릭만 한 경우 → 선택 토글 처리
                pending = self._ctrl_press_idx
                if pending >= 0 and self._ctrl_was_selected:
                    # 이미 선택된 것을 다시 클릭 → 다중 선택 시에만 제거
                    if len(self._selected_indices) > 1 and pending in self._selected_indices:
                        self._selected_indices.remove(pending)
                        self._selected_idx = (self._selected_indices[-1]
                                              if self._selected_indices else -1)
            self._ctrl_copy_preview = []
            self._ctrl_copy_offset_f = (0, 0)
            self._ctrl_press_idx = -1
            self._ctrl_was_selected = False

        elif self._state == "multi_move":
            self.rois_changed.emit()

        elif self._state == "new":
            fx, fy = self._w2f(wx, wy)
            sx, sy = self._drag_start_f
            rx, ry = int(min(sx, fx)), int(min(sy, fy))
            rw, rh = int(abs(fx - sx)), int(abs(fy - sy))
            if (abs(event.position().x() - self._drag_start_w.x()) >= self.MIN_ROI_PX and
                    abs(event.position().y() - self._drag_start_w.y()) >= self.MIN_ROI_PX and
                    rw > 0 and rh > 0):
                rw = min(rw, 500)
                rh = min(rh, 250)
                new_roi = ROI(
                    label="",
                    media_name="",
                    x=rx, y=ry, w=rw, h=rh,
                    roi_type=self._roi_type,
                )
                self._rois.append(new_roi)
                self._relabel()
                self._selected_idx = len(self._rois) - 1
                self._selected_indices = [self._selected_idx]
                self.rois_changed.emit()

        elif self._state in ("move", "resize"):
            self.rois_changed.emit()

        self._state = "idle"
        self._new_rect_f = None
        self._move_origin = None
        self._multi_origins = []
        self.update()

    def _apply_resize(self, fx: int, fy: int):
        """핸들 드래그 시 ROI 크기/위치 조정"""
        if not (0 <= self._selected_idx < len(self._rois)):
            return
        roi = self._rois[self._selected_idx]
        orig = self._move_origin
        h_name = self._resize_handle

        fx, fy = int(fx), int(fy)
        x1, y1 = orig.x, orig.y
        x2, y2 = orig.x + orig.w, orig.y + orig.h

        if "w" in h_name:
            x1 = min(fx, x2 - 2)
        if "e" in h_name:
            x2 = max(fx, x1 + 2)
        if "n" in h_name:
            y1 = min(fy, y2 - 2)
        if "s" in h_name:
            y2 = max(fy, y1 + 2)

        x1 = max(0, x1)
        y1 = max(0, y1)
        if self._frame is not None:
            fh, fw = self._frame.shape[:2]
            x2 = min(x2, fw)
            y2 = min(y2, fh)

        if x2 - x1 > 500:
            x2 = x1 + 500
        if y2 - y1 > 250:
            y2 = y1 + 250

        roi.x, roi.y = x1, y1
        roi.w, roi.h = x2 - x1, y2 - y1

    def _update_cursor(self, wx: float, wy: float):
        """마우스 위치에 따라 커서 변경"""
        if 0 <= self._selected_idx < len(self._rois) and not self._selected_indices:
            h = self._hit_handle(self._rois[self._selected_idx], wx, wy)
            if h:
                self.setCursor(self._HANDLE_CURSORS[h])
                return
        idx = self._hit_roi_body(wx, wy)
        if idx >= 0:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    # ── 키보드 이벤트 ──────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        # Delete: 선택된 ROI 삭제 (다중 지원)
        if key == Qt.Key_Delete:
            self.delete_selected()
            return

        # Ctrl+D: 선택된 ROI 복사 (다중 지원)
        if ctrl and key == Qt.Key_D:
            self.copy_selected()
            return

        # 이하 단일 ROI 이동/크기 조정
        if not (0 <= self._selected_idx < len(self._rois)):
            return
        roi = self._rois[self._selected_idx]
        step = 1 if shift else 10

        dx, dy = 0, 0
        if key == Qt.Key_Left:    dx = -step
        elif key == Qt.Key_Right: dx = step
        elif key == Qt.Key_Up:    dy = -step
        elif key == Qt.Key_Down:  dy = step
        else:
            return

        if ctrl:
            # 크기 조정 (우하단 기준)
            roi.w = max(2, roi.w + dx)
            roi.h = max(2, roi.h + dy)
            roi.w = min(roi.w, 500)
            roi.h = min(roi.h, 250)
        else:
            # 이동
            roi.x = max(0, roi.x + dx)
            roi.y = max(0, roi.y + dy)
            if self._frame is not None:
                fh, fw = self._frame.shape[:2]
                roi.x = min(roi.x, fw - roi.w)
                roi.y = min(roi.y, fh - roi.h)

        # debounce: 키 연속 입력 시 마지막 동작 후 120ms 뒤에 시그널 발송
        self._key_emit_timer.start()
        self.update()

    # ── 리사이즈 이벤트 ───────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_frame_rect()
        self.update()

    # ── 내부 헬퍼 ──────────────────────────────────────

    def _relabel(self):
        prefix = "V" if self._roi_type == "video" else "A"
        for i, roi in enumerate(self._rois):
            roi.label = f"{prefix}{i + 1}"


# ─────────────────────────────────────────────────────
# 전체화면 ROI 편집 다이얼로그
# ─────────────────────────────────────────────────────

class FullScreenROIEditor(QDialog):
    """
    전체화면 ROI 편집 다이얼로그.
    왼쪽: ROIEditorCanvas, 오른쪽: ROI 테이블 + 완료 버튼
    """

    editing_done = Signal()

    def __init__(self, roi_manager: ROIManager, roi_type: str,
                 frozen_frame, parent=None):
        super().__init__(parent)
        self._roi_manager = roi_manager
        self._roi_type = roi_type
        self.setWindowTitle("감지영역 편집 - 전체화면")
        self.setWindowState(Qt.WindowMaximized)

        self._setup_ui(frozen_frame)

    def _setup_ui(self, frame):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._canvas = ROIEditorCanvas(self._roi_manager, self._roi_type)
        self._canvas.set_frame(frame)
        self._canvas.load_rois()
        self._canvas.rois_changed.connect(self._refresh_table)
        layout.addWidget(self._canvas, stretch=4)

        panel = self._create_side_panel()
        layout.addWidget(panel, stretch=1)

    def _create_side_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("roiSidePanel")
        panel.setFixedWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        label_type = "영상" if self._roi_type == "video" else "오디오 레벨미터"
        title = QLabel(f"{label_type} 감지영역 편집")
        title.setObjectName("roiPanelTitle")
        layout.addWidget(title)

        layout.addWidget(self._make_separator())

        help_lbl = QLabel(
            "[방향키]\n"
            "• ↑↓←→: 이동 10px\n"
            "• Shift+↑↓←→: 이동 1px\n"
            "• Ctrl+↑↓←→: 크기 10px\n"
            "\n"
            "[클릭·드래그]\n"
            "• 빈 곳 드래그: 새 영역\n"
            "• 영역 드래그: 이동\n"
            "• Shift+드래그: 수직/수평 이동\n"
            "• 모서리/변 드래그: 크기\n"
            "• Ctrl+드래그(빈 곳): 범위 선택\n"
            "• Ctrl+클릭: 선택 추가/제거\n"
            "• 다중선택 후 드래그: 한번에 이동\n"
            "• 다중선택 후 Ctrl+드래그: 복사\n"
            "• Ctrl+Shift+드래그: 수직/수평 복사\n"
            "\n"
            "[기타]\n"
            "• Ctrl+D: 선택 영역 복사\n"
            "• Delete: 선택 영역 삭제"
        )
        help_lbl.setObjectName("roiHelpLabel")
        help_lbl.setWordWrap(True)
        help_lbl.setAlignment(Qt.AlignTop)

        help_scroll = QScrollArea()
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QFrame.NoFrame)
        help_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        help_scroll.setMaximumHeight(200)
        help_scroll.setWidget(help_lbl)
        layout.addWidget(help_scroll)

        layout.addWidget(self._make_separator())

        self._table = self._create_roi_table()
        layout.addWidget(self._table)

        # 추가/삭제 버튼
        btn_row = QHBoxLayout()
        btn_add = QPushButton("추가")
        btn_add.setObjectName("btnRoiCopy")
        btn_add.setToolTip("마지막 영역을 복사하여 추가 (x,y +10씩 이동)")
        btn_add.clicked.connect(self._on_add)
        btn_del = QPushButton("🗑 삭제")
        btn_del.setObjectName("btnRoiDelete")
        btn_del.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        layout.addLayout(btn_row)

        layout.addStretch()

        btn_done = QPushButton("편집 완료")
        btn_done.setObjectName("btnEditDone")
        btn_done.clicked.connect(self._on_done)
        layout.addWidget(btn_done)

        self._refresh_table()
        return panel

    def _create_roi_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setObjectName("roiTable")
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["라벨", "X", "Y", "W", "H"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.DoubleClicked)
        table.setAlternatingRowColors(False)
        table.verticalHeader().setVisible(False)
        table.itemSelectionChanged.connect(self._on_table_select)
        return table

    def _refresh_table(self):
        rois = self._canvas.get_rois()
        self._table.blockSignals(True)
        self._table.setRowCount(len(rois))
        for i, roi in enumerate(rois):
            for col, val in enumerate([roi.label, roi.x, roi.y, roi.w, roi.h]):
                item = QTableWidgetItem(str(val))
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(i, col, item)
        self._table.blockSignals(False)

        # 선택 동기화
        idx = self._canvas._selected_idx
        if 0 <= idx < self._table.rowCount():
            self._table.selectRow(idx)

    def _on_table_select(self):
        rows = sorted(set(item.row() for item in self._table.selectedItems()))
        if rows:
            self._canvas._selected_indices = rows
            self._canvas._selected_idx = rows[-1]
            self._canvas.update()

    def _on_add(self):
        """마지막 ROI를 복사하여 추가 (x,y +10씩 증가)"""
        rois = self._canvas.get_rois()
        if rois:
            last = rois[-1]
            new_roi = _copy_roi(last)
            new_roi.x = min(last.x + 10, 1900)
            new_roi.y = min(last.y + 10, 1060)
        else:
            # 오디오 타입 첫 추가: y=200, 비디오 타입: y=10
            init_y = 200 if self._roi_type == "audio" else 10
            new_roi = ROI(label="", media_name="", x=10, y=init_y,
                          w=100, h=100, roi_type=self._roi_type)
        self._canvas._rois.append(new_roi)
        self._canvas._relabel()
        self._canvas._selected_idx = len(self._canvas._rois) - 1
        self._canvas._selected_indices = [self._canvas._selected_idx]
        self._canvas.rois_changed.emit()
        self._canvas.update()
        self._refresh_table()

    def _on_delete(self):
        self._canvas.delete_selected()
        self._refresh_table()

    def _on_done(self):
        self._canvas.apply_rois()
        self.editing_done.emit()
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._on_done()
        else:
            super().keyPressEvent(event)

    @staticmethod
    def _make_separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("roiPanelSeparator")
        return line
