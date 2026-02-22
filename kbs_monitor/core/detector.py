"""
영상/오디오 감지 엔진
블랙/스틸/레벨미터/임베디드오디오 감지 로직
"""
import time
import cv2
import numpy as np
from collections import deque
from typing import Dict, List, Optional
from core.roi_manager import ROI


class DetectionState:
    """단일 감지영역의 상태 추적"""

    def __init__(self, roi: ROI):
        self.roi = roi
        self.is_alerting = False       # 현재 알림 발생 중
        self.alert_start_time: Optional[float] = None  # 이상 시작 시간
        self.alert_duration = 0.0     # 이상 지속 시간(초)
        self.last_alert_duration = 0.0  # 직전 알림의 지속 시간(복구 시 참조)
        self.just_resolved = False      # 이번 업데이트에서 정상 복구됨
        self.recovery_start_time: Optional[float] = None  # 복구 대기 시작 시간
        self.last_check_time = time.time()

    def update(self, is_abnormal: bool, threshold_seconds: float, recovery_seconds: float = 0.0) -> bool:
        """
        상태 업데이트. 알림 발생 여부 반환.
        is_abnormal: 현재 이상 상태 여부
        threshold_seconds: 몇 초 이상 지속 시 알림 발생
        recovery_seconds: 알림 상태에서 정상으로 복구되기 위한 최소 정상 지속 시간(초)
                          0이면 즉시 복구 (기존 동작)
        """
        now = time.time()
        was_alerting = self.is_alerting

        if is_abnormal:
            self.just_resolved = False
            self.recovery_start_time = None  # 복구 타이머 리셋
            if self.alert_start_time is None:
                self.alert_start_time = now
            self.alert_duration = now - self.alert_start_time

            if self.alert_duration >= threshold_seconds and not self.is_alerting:
                self.is_alerting = True
        else:
            if was_alerting:
                if recovery_seconds > 0:
                    # 복구 딜레이 적용: recovery_seconds 이상 정상 지속 시에만 복구
                    if self.recovery_start_time is None:
                        self.recovery_start_time = now
                    if now - self.recovery_start_time >= recovery_seconds:
                        self.last_alert_duration = self.alert_duration
                        self.just_resolved = True
                        self._do_resolve()
                    else:
                        # 복구 딜레이 미충족 → 알림 상태 유지
                        self.just_resolved = False
                        self.last_check_time = now
                        return self.is_alerting
                else:
                    # 즉시 복구 (기존 동작)
                    self.last_alert_duration = self.alert_duration
                    self.just_resolved = True
                    self._do_resolve()
            else:
                self.just_resolved = False
                self.alert_start_time = None
                self.alert_duration = 0.0
                self.recovery_start_time = None

        self.last_check_time = now
        return self.is_alerting

    def _do_resolve(self):
        """알림 → 정상 전환 처리"""
        self.alert_start_time = None
        self.alert_duration = 0.0
        self.is_alerting = False
        self.recovery_start_time = None

    def reset(self):
        self.is_alerting = False
        self.alert_start_time = None
        self.alert_duration = 0.0
        self.just_resolved = False
        self.recovery_start_time = None


class Detector:
    """
    영상/오디오 감지 엔진
    블랙/스틸/레벨미터/임베디드오디오 감지 수행
    """

    def __init__(self):
        # 성능 설정
        self.scale_factor = 1.0            # 감지 해상도 스케일 (1.0 / 0.5 / 0.25)
        self.still_detection_enabled = True  # 스틸 감지 활성화 여부

        # 블랙 감지 설정
        self.black_threshold = 10          # 밝기 임계값 (0~255)
        self.black_duration = 10.0         # 몇 초 이상 지속 시 알림 발생
        self.black_alarm_duration = 10.0   # 알림 지속 시간(초)

        # 스틸 감지 설정
        self.still_threshold = 2           # 픽셀 차이 임계값
        self.still_duration = 10.0         # 몇 초 이상 지속 시 알림 발생
        self.still_alarm_duration = 10.0   # 알림 지속 시간(초)

        # 오디오 레벨미터 감지 설정 (HSV)
        self.audio_hsv_h_min = 40    # H 최소값 (0~179)
        self.audio_hsv_h_max = 80    # H 최대값
        self.audio_hsv_s_min = 30    # S 최소값 (0~255)
        self.audio_hsv_s_max = 255   # S 최대값
        self.audio_hsv_v_min = 30    # V 최소값 (0~255)
        self.audio_hsv_v_max = 255   # V 최대값
        self.audio_pixel_ratio = 5.0 # 감지 픽셀 비율 임계값 (%)
        self.audio_level_duration = 5.0         # 비활성 지속 시간(초) → 알림
        self.audio_level_alarm_duration = 10.0  # 알림 지속 시간(초)
        self.audio_level_recovery_seconds = 2.0 # 알림 복구 딜레이(초): 이 시간 이상 정상 지속 시 복구

        # 임베디드 오디오 감지 설정
        self.embedded_silence_threshold = -50  # 무음 판단 dB (-60~0)
        self.embedded_silence_duration = 10.0  # 무음 지속 시간(초) → 알림
        self.embedded_alarm_duration = 10.0    # 알림 지속 시간(초)

        # 비디오 감지 상태
        self._black_states: Dict[str, DetectionState] = {}
        self._still_states: Dict[str, DetectionState] = {}
        self._prev_frames: Dict[str, np.ndarray] = {}

        # 오디오 레벨미터 상태
        self._audio_level_states: Dict[str, DetectionState] = {}
        self._audio_ratio_buffer: Dict[str, deque] = {}  # 이동 평균 버퍼 (최근 5프레임)

        # 임베디드 오디오 상태
        self.embedded_alerting = False
        self._embedded_alert_start: Optional[float] = None

    def _apply_scale_factor(self, frame: np.ndarray) -> np.ndarray:
        """해상도 스케일 적용 (scale_factor < 1.0 인 경우에만 축소)"""
        if self.scale_factor < 1.0:
            return cv2.resize(frame, None, fx=self.scale_factor, fy=self.scale_factor,
                              interpolation=cv2.INTER_AREA)
        return frame

    def update_roi_list(self, rois: List[ROI]):
        """감지영역 목록 변경 시 상태 초기화 및 오래된 버퍼 정리"""
        labels = {roi.label for roi in rois}

        for label in list(self._black_states.keys()):
            if label not in labels:
                del self._black_states[label]
        for label in list(self._still_states.keys()):
            if label not in labels:
                del self._still_states[label]
        for label in list(self._prev_frames.keys()):
            if label not in labels:
                del self._prev_frames[label]
        # 삭제된 ROI의 오디오 버퍼 정리 (메모리 누수 방지)
        for label in list(self._audio_ratio_buffer.keys()):
            if label not in labels:
                del self._audio_ratio_buffer[label]
        for label in list(self._audio_level_states.keys()):
            if label not in labels:
                del self._audio_level_states[label]

        for roi in rois:
            if roi.label not in self._black_states:
                self._black_states[roi.label] = DetectionState(roi)
            else:
                self._black_states[roi.label].roi = roi
            if roi.label not in self._still_states:
                self._still_states[roi.label] = DetectionState(roi)
            else:
                self._still_states[roi.label].roi = roi

    def detect_frame(self, frame: np.ndarray, rois: List[ROI]) -> Dict[str, dict]:
        """
        프레임을 분석하여 각 감지영역의 블랙/스틸 상태 반환.
        반환값: {label: {"black": bool, "still": bool, "black_alerting": bool, "still_alerting": bool}}
        scale_factor 적용 시 감지용 프레임 축소 후 ROI 좌표 보정.
        """
        results = {}

        # 해상도 스케일 적용 (감지 연산 픽셀 수 감소)
        frame = self._apply_scale_factor(frame)
        sf = self.scale_factor

        for roi in rois:
            label = roi.label

            # scale_factor 보정된 ROI 좌표
            h, w = frame.shape[:2]
            x1 = max(0, int(roi.x * sf))
            y1 = max(0, int(roi.y * sf))
            x2 = min(w, int((roi.x + roi.w) * sf))
            y2 = min(h, int((roi.y + roi.h) * sf))

            if x2 <= x1 or y2 <= y1:
                continue

            crop = frame[y1:y2, x1:x2]

            # 블랙 감지
            gray = crop if len(crop.shape) == 2 else crop.mean(axis=2)
            avg_brightness = float(np.mean(gray))
            is_black = avg_brightness < self.black_threshold

            # 스틸 감지 (비활성화 시 float32 변환 및 복사 생략)
            is_still = False
            if self.still_detection_enabled:
                if label in self._prev_frames:
                    prev = self._prev_frames[label]
                    crop_f = crop.astype(np.float32)
                    if prev.shape == crop_f.shape:
                        diff = np.abs(crop_f - prev)
                        avg_diff = float(np.mean(diff))
                        is_still = avg_diff < self.still_threshold
                    else:
                        is_still = False
                # float32로 저장하여 다음 사이클의 재변환 비용 제거
                self._prev_frames[label] = crop.astype(np.float32)
            else:
                # 스틸 감지 비활성 → 이전 프레임 버퍼 불필요
                self._prev_frames.pop(label, None)

            # 상태 업데이트
            if label not in self._black_states:
                self._black_states[label] = DetectionState(roi)
            black_state = self._black_states[label]
            if label not in self._still_states:
                self._still_states[label] = DetectionState(roi)
            still_state = self._still_states[label]

            black_alerting = black_state.update(is_black, self.black_duration)
            still_alerting = still_state.update(is_still, self.still_duration)

            results[label] = {
                "black": is_black,
                "still": is_still,
                "black_alerting": black_alerting,
                "still_alerting": still_alerting,
                "black_duration": black_state.alert_duration,
                "still_duration": still_state.alert_duration,
                "black_resolved": black_state.just_resolved,
                "black_last_duration": black_state.last_alert_duration,
                "still_resolved": still_state.just_resolved,
                "still_last_duration": still_state.last_alert_duration,
            }

        return results

    def detect_audio_roi(self, frame: np.ndarray, audio_rois: List[ROI]) -> Dict[str, dict]:
        """
        오디오 ROI에서 HSV 기반 레벨미터 색상 감지.
        반환값: {label: {"active": bool, "ratio": float, "alerting": bool, "duration": float}}
        레벨미터가 일정 시간 비활성(색 없음)이면 알림 발생.
        전체 프레임 HSV 변환 대신 ROI별 crop 후 변환하여 처리 픽셀 수 대폭 감소.
        """
        results = {}
        lower = np.array([self.audio_hsv_h_min, self.audio_hsv_s_min, self.audio_hsv_v_min])
        upper = np.array([self.audio_hsv_h_max, self.audio_hsv_s_max, self.audio_hsv_v_max])

        # 해상도 스케일 적용 (공통 메서드 사용)
        frame = self._apply_scale_factor(frame)
        sf = self.scale_factor

        for roi in audio_rois:
            label = roi.label
            fh, fw = frame.shape[:2]
            x1 = max(0, int(roi.x * sf))
            y1 = max(0, int(roi.y * sf))
            x2 = min(fw, int((roi.x + roi.w) * sf))
            y2 = min(fh, int((roi.y + roi.h) * sf))

            if x2 <= x1 or y2 <= y1:
                continue

            # BGR crop 후 HSV 변환 (전체 프레임 변환 제거)
            crop_bgr = frame[y1:y2, x1:x2]
            crop = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(crop, lower, upper)
            total_pixels = crop.shape[0] * crop.shape[1]
            if total_pixels == 0:
                continue

            active_pixels = int(np.sum(mask > 0))
            ratio = active_pixels / total_pixels * 100.0

            # 이동 평균 버퍼: 최근 5프레임 ratio 평균으로 판단 (일시적 노이즈 평활화)
            if label not in self._audio_ratio_buffer:
                self._audio_ratio_buffer[label] = deque(maxlen=5)
            self._audio_ratio_buffer[label].append(ratio)
            avg_ratio = sum(self._audio_ratio_buffer[label]) / len(self._audio_ratio_buffer[label])
            is_active = avg_ratio >= self.audio_pixel_ratio

            # 레벨미터 비활성 = 이상 상태 (무음 또는 신호 없음)
            is_abnormal = not is_active

            if label not in self._audio_level_states:
                self._audio_level_states[label] = DetectionState(roi)
            state = self._audio_level_states[label]
            state.roi = roi

            alerting = state.update(is_abnormal, self.audio_level_duration, self.audio_level_recovery_seconds)

            results[label] = {
                "active": is_active,
                "ratio": avg_ratio,
                "alerting": alerting,
                "duration": state.alert_duration,
                "resolved": state.just_resolved,
                "last_duration": state.last_alert_duration,
            }

        return results

    def update_embedded_silence(self, silence_seconds: float) -> bool:
        """
        임베디드 오디오 무음 상태 업데이트.
        silence_seconds: AudioMonitorThread에서 받은 무음 지속 시간(초)
        반환값: 알림 발생 여부
        """
        if silence_seconds > 0:
            if self._embedded_alert_start is None:
                self._embedded_alert_start = time.time() - silence_seconds
            elapsed = time.time() - self._embedded_alert_start
            if elapsed >= self.embedded_silence_duration and not self.embedded_alerting:
                self.embedded_alerting = True
        else:
            self._embedded_alert_start = None
            self.embedded_alerting = False
        return self.embedded_alerting

    def reset_embedded_silence(self):
        """임베디드 오디오 무음 상태 초기화"""
        self._embedded_alert_start = None
        self.embedded_alerting = False

    def reset_all(self):
        """모든 감지 상태 초기화"""
        for state in self._black_states.values():
            state.reset()
        for state in self._still_states.values():
            state.reset()
        for state in self._audio_level_states.values():
            state.reset()
        self.reset_embedded_silence()
