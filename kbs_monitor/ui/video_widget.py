"""
비디오 표시 위젯
OpenCV 프레임을 QLabel에 표시, 감지영역 오버레이 지원
NO SIGNAL 상태에서도 1920×1080 프레임 유지, ROI 항상 렌더링
"""
import numpy as np
import cv2
from itertools import chain
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage
from typing import List, Dict, Optional
from core.roi_manager import ROI

_NO_SIGNAL_W = 1920
_NO_SIGNAL_H = 1080


class VideoWidget(QWidget):
    """16분할 멀티뷰 영상을 표시하는 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_frame: Optional[np.ndarray] = None
        self._show_rois = True
        self._video_rois: List[ROI] = []
        self._audio_rois: List[ROI] = []
        self._alert_labels: Dict[str, bool] = {}
        self._blink_on = False
        self._no_signal_frame: Optional[np.ndarray] = None  # 캐시
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel()
        self._label.setObjectName("videoLabel")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._label.setMinimumSize(640, 360)
        layout.addWidget(self._label)

        self._render()

    def _make_no_signal_frame(self) -> np.ndarray:
        """1920×1080 NO SIGNAL INPUT 프레임 생성 (캐시 사용)"""
        if self._no_signal_frame is not None:
            return self._no_signal_frame

        img = np.zeros((_NO_SIGNAL_H, _NO_SIGNAL_W, 3), dtype=np.uint8)
        text = "NO SIGNAL INPUT"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 3.0
        thickness = 4
        text_size, _ = cv2.getTextSize(text, font, font_scale, thickness)
        tx = (_NO_SIGNAL_W - text_size[0]) // 2
        ty = (_NO_SIGNAL_H + text_size[1]) // 2
        cv2.putText(img, text, (tx, ty), font, font_scale,
                    (80, 80, 80), thickness, cv2.LINE_AA)
        self._no_signal_frame = img
        return img

    def update_frame(self, frame: np.ndarray):
        """새 프레임 수신 시 호출"""
        self._current_frame = frame
        self._render()

    def set_show_rois(self, show: bool):
        self._show_rois = show
        self._render()

    def set_rois(self, video_rois: List[ROI], audio_rois: List[ROI]):
        self._video_rois = video_rois
        self._audio_rois = audio_rois
        self._render()

    def set_alert_state(self, label: str, alerting: bool):
        if self._alert_labels.get(label) == alerting:
            return
        self._alert_labels[label] = alerting
        self._render()

    def set_blink_state(self, blink_on: bool):
        self._blink_on = blink_on
        self._render()

    def clear_signal(self):
        """신호 없음 상태로 전환"""
        self._current_frame = None
        self._render()

    def _render(self):
        """현재 프레임(없으면 NO SIGNAL 1920×1080) + 감지영역 오버레이를 그려서 표시
        show_rois가 False여도 알림 중인 ROI는 깜빡여야 하므로 별도 처리
        """
        frame = (self._current_frame.copy()
                 if self._current_frame is not None
                 else self._make_no_signal_frame().copy())
        h, w = frame.shape[:2]

        # show_rois=False여도 알림 중인 ROI가 있으면 표시 (알림 종료 시 자동으로 사라짐)
        has_alerts = any(
            self._alert_labels.get(r.label, False)
            for r in chain(self._video_rois, self._audio_rois)
        )
        if self._show_rois or has_alerts:
            self._draw_rois(frame, w, h)

        self._display_numpy(frame)

    def _draw_rois(self, frame: np.ndarray, fw: int, fh: int):
        """감지영역을 프레임 위에 그리기
        비디오 ROI: 빨간색, 오디오 ROI: 주황색 (정상) / 빨간색 채우기 (알림)
        show_rois=False 시에는 알림 중인 ROI만 그림
        """
        all_rois = ([("video", r) for r in self._video_rois] +
                    [("audio", r) for r in self._audio_rois])

        for roi_type, roi in all_rois:
            alerting = self._alert_labels.get(roi.label, False)

            # 감지영역 버튼이 OFF 상태이면 알림 중인 ROI만 그림
            if not self._show_rois and not alerting:
                continue

            x1 = max(0, min(roi.x, fw - 1))
            y1 = max(0, min(roi.y, fh - 1))
            x2 = max(0, min(roi.x + roi.w, fw))
            y2 = max(0, min(roi.y + roi.h, fh))

            # 타입별 색상 (BGR)
            if roi_type == "video":
                normal_color = (0, 0, 200)    # 빨간색
                alert_color  = (0, 0, 255)    # 밝은 빨간색
                fill_color   = (0, 0, 180)    # 알림 채우기
            else:  # audio
                normal_color = (0, 165, 255)  # 주황색 (BGR)
                alert_color  = (0, 0, 255)    # 밝은 빨간색
                fill_color   = (0, 0, 180)    # 알림 채우기 (빨간색)

            if alerting and self._blink_on:
                overlay = frame.copy()
                cv2.rectangle(overlay, (x1, y1), (x2, y2), fill_color, -1)
                cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)
                cv2.rectangle(frame, (x1, y1), (x2, y2), alert_color, 2)
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), normal_color, 2)

            # 라벨 텍스트 (매체명 포함): "V1 [매체명]"
            if roi.media_name:
                label_text = f"{roi.label} [{roi.media_name}]"
            else:
                label_text = roi.label
            font = cv2.FONT_HERSHEY_SIMPLEX
            text_y = y1 + 18
            # 검은 외곽선 + 흰 텍스트 (가독성)
            cv2.putText(frame, label_text, (x1 + 3, text_y),
                        font, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, label_text, (x1 + 3, text_y),
                        font, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

    def _display_numpy(self, frame: np.ndarray):
        """numpy BGR 배열을 QLabel에 표시"""
        h, w, ch = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # rgb.tobytes()로 복사본을 전달 → numpy 배열 gc 후 dangling pointer 방지
        image = QImage(rgb.tobytes(), w, h, ch * w, QImage.Format_RGB888)

        lw = self._label.width()
        lh = self._label.height()
        if lw > 0 and lh > 0:
            pixmap = QPixmap.fromImage(image).scaled(
                lw, lh,
                Qt.KeepAspectRatio,
                Qt.FastTransformation,
            )
        else:
            pixmap = QPixmap.fromImage(image)

        self._label.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    def get_frame_size(self) -> tuple:
        """현재 프레임의 실제 크기 반환 (w, h)"""
        if self._current_frame is not None:
            h, w = self._current_frame.shape[:2]
            return w, h
        return _NO_SIGNAL_W, _NO_SIGNAL_H

    def widget_to_frame_coords(self, wx: int, wy: int) -> tuple:
        """위젯 좌표 → 프레임 좌표 변환 (레터박스 고려)"""
        if self._current_frame is not None:
            fh, fw = self._current_frame.shape[:2]
        else:
            fw, fh = _NO_SIGNAL_W, _NO_SIGNAL_H

        lw = self._label.width()
        lh = self._label.height()

        scale = min(lw / fw, lh / fh)
        if scale == 0:
            return 0, 0

        off_x = (lw - fw * scale) / 2
        off_y = (lh - fh * scale) / 2

        fx = int((wx - off_x) / scale)
        fy = int((wy - off_y) / scale)
        fx = max(0, min(fw, fx))
        fy = max(0, min(fh, fy))
        return fx, fy
