"""
영상/오디오 감지 엔진
블랙/스틸/레벨미터/임베디드오디오 감지 로직
"""
import logging
import time
import cv2
import numpy as np
from collections import deque
from typing import Dict, List, Optional
from core.roi_manager import ROI

_log = logging.getLogger(__name__)


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
        # 히스테리시스: 연속 N프레임 정상이어야 타이머 리셋
        self._not_still_count: int = 0
        self._last_reset_time: float = 0.0   # 타이머 리셋 발생 시각
        self._last_reset_from: float = 0.0   # 리셋 직전 누적 시간(진단용)
        self._resolve_count: int = 0         # resolve 발생 횟수(DIAG용)

    def update(self, is_abnormal: bool, threshold_seconds: float,
               recovery_seconds: float = 0.0, reset_frames: int = 1) -> bool:
        """
        상태 업데이트. 알림 발생 여부 반환.
        is_abnormal: 현재 이상 상태 여부
        threshold_seconds: 몇 초 이상 지속 시 알림 발생
        recovery_seconds: 알림 상태에서 정상으로 복구되기 위한 최소 정상 지속 시간(초)
                          0이면 reset_frames 히스테리시스 적용
        reset_frames: 연속 정상 프레임 수 임계값 (경보 전/후 동일 적용)
        """
        now = time.time()
        was_alerting = self.is_alerting

        if is_abnormal:
            self.just_resolved = False
            self.recovery_start_time = None  # 복구 타이머 리셋
            self._not_still_count = 0        # 히스테리시스 카운터 리셋
            if self.alert_start_time is None:
                self.alert_start_time = now
            self.alert_duration = now - self.alert_start_time

            if self.alert_duration >= threshold_seconds and not self.is_alerting:
                self.is_alerting = True
        else:
            # 정상 프레임 카운터 — 경보 전/후 동일하게 적용
            self._not_still_count += 1

            if was_alerting:
                if recovery_seconds > 0:
                    # 복구 딜레이 적용: recovery_seconds 이상 정상 지속 시에만 복구
                    if self.recovery_start_time is None:
                        self.recovery_start_time = now
                    if now - self.recovery_start_time >= recovery_seconds:
                        self.last_alert_duration = self.alert_duration
                        self.just_resolved = True
                        self._do_resolve(now)
                    else:
                        # 복구 딜레이 미충족 → 알림 상태 유지
                        self.just_resolved = False
                        self.last_check_time = now
                        return self.is_alerting
                elif self._not_still_count >= reset_frames:
                    # 히스테리시스 충족 → 복구 (단일 프레임 글리치 방지)
                    self.last_alert_duration = self.alert_duration
                    self.just_resolved = True
                    self._do_resolve(now)
                else:
                    # 히스테리시스 미충족 → 알림 상태 유지 (타이머도 유지)
                    self.just_resolved = False
                    self.last_check_time = now
                    return self.is_alerting
            else:
                # 비경보 상태: reset_frames 연속 프레임 정상이어야 타이머 리셋
                self.just_resolved = False
                if self._not_still_count >= reset_frames:
                    self._last_reset_from = self.alert_duration
                    self._last_reset_time = now
                    self.alert_start_time = None
                    self.alert_duration = 0.0
                    self.recovery_start_time = None
                    self._not_still_count = 0
                # 카운터 미충족 → 타이머 유지 (alert_start_time, alert_duration 유지)

        self.last_check_time = now
        return self.is_alerting

    def _do_resolve(self, now: float = None):
        """알림 → 정상 전환 처리"""
        if now is None:
            now = time.time()
        self._resolve_count += 1
        self._last_reset_from = self.alert_duration
        self._last_reset_time = now
        self.alert_start_time = None
        self.alert_duration = 0.0
        self.is_alerting = False
        self.recovery_start_time = None
        self._not_still_count = 0

    def reset(self):
        self.is_alerting = False
        self.alert_start_time = None
        self.alert_duration = 0.0
        self.just_resolved = False
        self.recovery_start_time = None
        self._not_still_count = 0
        self._last_reset_time = 0.0
        self._last_reset_from = 0.0
        self._resolve_count = 0


class Detector:
    """
    영상/오디오 감지 엔진
    블랙/스틸/레벨미터/임베디드오디오 감지 수행
    """

    def __init__(self):
        # 성능 설정
        self.scale_factor = 1.0              # 감지 해상도 스케일 (1.0 / 0.5 / 0.25)
        self.black_detection_enabled = True  # 블랙 감지 활성화 여부
        self.still_detection_enabled = True  # 스틸 감지 활성화 여부

        # 블랙 감지 설정
        self.black_threshold = 5           # 픽셀당 어두움 기준 (0~255): 이 값 미만이면 '어두운 픽셀'로 분류
        self.black_dark_ratio = 98.0       # 어두운 픽셀 비율 임계값 (%): 이 비율 이상이면 블랙으로 판단
        self.black_duration = 10.0         # 몇 초 이상 지속 시 알림 발생
        self.black_alarm_duration = 10.0   # 알림 지속 시간(초)
        self.black_motion_suppress_ratio = 0.2  # 블랙 판정 시 움직임(changed_ratio)이 이 값 이상이면 블랙 취소 (0=비활성)

        # 스틸 감지 설정
        self.still_threshold = 4           # 픽셀당 변화 기준값 (0~255): 이 값 이상 차이나면 '변화한 픽셀'로 분류
        self.still_block_threshold = 10.0  # 블록 움직임 임계값 (%): 5×5 블록 중 하나라도 이 비율 이상 변화하면 스틸 아님
        self.still_duration = 10.0         # 몇 초 이상 지속 시 알림 발생
        self.still_alarm_duration = 10.0   # 알림 지속 시간(초)
        self.still_reset_frames = 3        # 타이머 리셋에 필요한 연속 정상 프레임 수 (히스테리시스)

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

        # 오디오 레벨미터 감지 상태
        self._audio_level_states: Dict[str, DetectionState] = {}
        self._audio_ratio_buffer: Dict[str, deque] = {}  # 이동 평균 버퍼 (최근 5프레임)

        # 임베디드 오디오 상태
        self.embedded_alerting = False
        self._embedded_alert_start: Optional[float] = None
        self._tone_states: Dict[str, DetectionState] = {}

        # 진단용 raw 수치 (마지막 계산값 — heartbeat 로그 덤프용)
        self._last_raw: Dict[str, dict] = {}
        # near-miss 추적 (임계값 근접 상태 지속 시간)
        self._near_miss_start: Dict[str, float] = {}

    def _check_still_by_blocks(self, changed_mask: np.ndarray) -> bool:
        """5×5 블록 기반 스틸 판정. 블록 중 하나라도 움직임 임계값 초과 시 False(스틸 아님) 반환."""
        bh, bw = changed_mask.shape[:2]
        # 채널 차원이 있으면 any 축으로 2D로 축소 (RGB diff > threshold → 어느 채널이든 변화)
        if changed_mask.ndim == 3:
            changed_mask = changed_mask.any(axis=2)
        rows, cols = 5, 5
        row_edges = np.linspace(0, bh, rows + 1, dtype=int)
        col_edges = np.linspace(0, bw, cols + 1, dtype=int)
        threshold = self.still_block_threshold
        for r in range(rows):
            for c in range(cols):
                block = changed_mask[row_edges[r]:row_edges[r + 1],
                                     col_edges[c]:col_edges[c + 1]]
                if block.size == 0:
                    continue
                block_ratio = float(np.mean(block)) * 100.0
                if block_ratio >= threshold:
                    return False  # 이 블록에 움직임 있음 → 스틸 아님
        return True  # 모든 블록이 정적 → 스틸

    def _apply_scale_factor(self, frame: np.ndarray) -> np.ndarray:
        """해상도 스케일 적용 (scale_factor < 1.0 인 경우에만 축소)"""
        if self.scale_factor < 1.0:
            return cv2.resize(frame, None, fx=self.scale_factor, fy=self.scale_factor,
                              interpolation=cv2.INTER_AREA)
        return frame

    def _get_scaled_bounds(self, roi: ROI, frame_h: int, frame_w: int) -> tuple:
        """scale_factor 보정된 ROI 경계 좌표 반환 (x1, y1, x2, y2)"""
        sf = self.scale_factor
        x1 = max(0, int(roi.x * sf))
        y1 = max(0, int(roi.y * sf))
        x2 = min(frame_w, int((roi.x + roi.w) * sf))
        y2 = min(frame_h, int((roi.y + roi.h) * sf))
        return x1, y1, x2, y2

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
        for label in list(self._near_miss_start.keys()):
            if label not in labels:
                del self._near_miss_start[label]
        # 삭제된 ROI의 오디오 버퍼 정리 (메모리 누수 방지)
        for label in list(self._audio_ratio_buffer.keys()):
            if label not in labels:
                del self._audio_ratio_buffer[label]
        for label in list(self._audio_level_states.keys()):
            if label not in labels:
                del self._audio_level_states[label]
        for label in list(self._last_raw.keys()):
            if label not in labels:
                del self._last_raw[label]
        for label in list(self._tone_states.keys()):
            if label not in labels:
                del self._tone_states[label]
        for roi in rois:
            if roi.label not in self._black_states:
                self._black_states[roi.label] = DetectionState(roi)
            else:
                self._black_states[roi.label].roi = roi
            if roi.label not in self._still_states:
                self._still_states[roi.label] = DetectionState(roi)
            else:
                self._still_states[roi.label].roi = roi

    def detect_frame(self, frame: np.ndarray, rois: List[ROI],
                     force_still_labels: Optional[set] = None) -> Dict[str, dict]:
        """
        프레임을 분석하여 각 감지영역의 블랙/스틸 상태 반환.
        반환값: {label: {"black": bool, "still": bool, "black_alerting": bool, "still_alerting": bool}}
        scale_factor 적용 시 감지용 프레임 축소 후 ROI 좌표 보정.

        force_still_labels: still_detection_enabled=False이어도 스틸 계산을 강제할 label 집합.
                            SignoffManager의 enter_roi label에 대해 정파 감지 목적으로 사용.
        """
        results = {}

        # 해상도 스케일 적용 (감지 연산 픽셀 수 감소)
        frame = self._apply_scale_factor(frame)

        h, w = frame.shape[:2]
        for roi in rois:
            label = roi.label
            try:
                # scale_factor 보정된 ROI 좌표 (공통 메서드)
                x1, y1, x2, y2 = self._get_scaled_bounds(roi, h, w)

                if x2 <= x1 or y2 <= y1:
                    continue

                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue

                # 블랙 감지 (어두운 픽셀 비율 방식 — 비활성화 시 계산 생략)
                dark_ratio = -1.0
                is_black = False
                if self.black_detection_enabled:
                    gray = crop if len(crop.shape) == 2 else crop.mean(axis=2)
                    dark_ratio = float(np.mean(gray < self.black_threshold)) * 100.0
                    is_black = dark_ratio >= self.black_dark_ratio

                # 스틸 감지 (변화 픽셀 비율 방식 — 비활성화 시 float32 변환 및 복사 생략)
                # force_still_labels에 포함된 label은 still_detection_enabled와 무관하게 계산
                changed_ratio = -1.0
                is_still = False
                should_calc_still = self.still_detection_enabled or (
                    force_still_labels is not None and label in force_still_labels
                )
                if should_calc_still:
                    if label in self._prev_frames:
                        prev = self._prev_frames[label]
                        crop_f = crop.astype(np.float32)
                        if prev.shape == crop_f.shape:
                            diff = np.abs(crop_f - prev)
                            changed_mask = diff > self.still_threshold
                            # 전체 changed_ratio (블랙 모션 억제 + 진단용)
                            changed_ratio = float(np.mean(changed_mask)) * 100.0
                            # 블록 기반 스틸 판정: 5×5 격자 중 하나라도 움직임 있으면 스틸 아님
                            is_still = self._check_still_by_blocks(changed_mask)
                        else:
                            is_still = False
                    # float32로 저장하여 다음 사이클의 재변환 비용 제거
                    self._prev_frames[label] = crop.astype(np.float32)
                else:
                    # 스틸 감지 비활성 + force 대상 아님 → 이전 프레임 버퍼 불필요
                    self._prev_frames.pop(label, None)

                # 블랙+모션 억제: 움직임이 있으면 블랙 오감지 취소 (스크롤 자막 등)
                if is_black and self.black_motion_suppress_ratio > 0 and changed_ratio >= 0:
                    if changed_ratio >= self.black_motion_suppress_ratio:
                        is_black = False

                # 진단용 raw 수치 저장 (heartbeat 덤프용)
                self._last_raw[label] = {
                    "dark_ratio": dark_ratio,
                    "changed_ratio": changed_ratio,
                }

                # 상태 업데이트
                if label not in self._black_states:
                    self._black_states[label] = DetectionState(roi)
                black_state = self._black_states[label]
                if label not in self._still_states:
                    self._still_states[label] = DetectionState(roi)
                still_state = self._still_states[label]

                black_alerting = black_state.update(is_black, self.black_duration)
                prev_reset_time = still_state._last_reset_time
                still_alerting = still_state.update(is_still, self.still_duration,
                                                     reset_frames=self.still_reset_frames)
                # 스틸 타이머 리셋 진단 경고 (5초 이상 누적 중 아티팩트로 리셋 시)
                if (still_state._last_reset_from >= 5.0
                        and still_state._last_reset_time != prev_reset_time):
                    _log.warning(
                        "DIAG - ROI[%s] 스틸 타이머 리셋 (누적 %.1f초 → 0, %d프레임 연속 모션)",
                        label, still_state._last_reset_from, self.still_reset_frames,
                    )

                # near-miss 추적: 임계값에 근접한 상태가 30초 이상 지속 시 진단 로그
                # dark_ratio > 80%: 블랙 기준(98%)에 실질적으로 근접한 경우만 추적
                now_nm = time.time()
                is_near_miss = (dark_ratio > 80.0) or (changed_ratio >= 0 and changed_ratio < 3.0)
                if is_near_miss:
                    if label not in self._near_miss_start:
                        self._near_miss_start[label] = now_nm
                    elif now_nm - self._near_miss_start[label] >= 30.0:
                        _log.debug(
                            "NEAR-MISS - ROI[%s]: dark=%.1f%% changed=%.2f%% (30초 지속)",
                            label, dark_ratio, changed_ratio,
                        )
                        self._near_miss_start[label] = now_nm
                else:
                    self._near_miss_start.pop(label, None)

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
            except Exception as e:
                _log.error("detect_frame ROI[%s] 오류: %s", label, e)

        return results

    def detect_audio_roi(self, frame: np.ndarray, audio_rois: List[ROI]) -> Dict[str, dict]:
        """
        오디오 ROI에서 HSV 기반 레벨미터 색상 감지.
        반환값: {label: {"active": bool, "ratio": float, "alerting": bool, "duration": float,
                         "resolved": bool, "last_duration": float}}
        레벨미터가 일정 시간 비활성(색 없음)이면 알림 발생.
        전체 프레임 HSV 변환 대신 ROI별 crop 후 변환하여 처리 픽셀 수 대폭 감소.
        """
        results = {}
        lower = np.array([self.audio_hsv_h_min, self.audio_hsv_s_min, self.audio_hsv_v_min])
        upper = np.array([self.audio_hsv_h_max, self.audio_hsv_s_max, self.audio_hsv_v_max])

        # 해상도 스케일 적용 (공통 메서드 사용)
        frame = self._apply_scale_factor(frame)
        fh, fw = frame.shape[:2]

        for roi in audio_rois:
            label = roi.label
            try:
                # scale_factor 보정된 ROI 좌표 (공통 메서드)
                x1, y1, x2, y2 = self._get_scaled_bounds(roi, fh, fw)

                if x2 <= x1 or y2 <= y1:
                    continue

                # BGR crop 후 HSV 변환 (전체 프레임 변환 제거)
                crop_bgr = frame[y1:y2, x1:x2]
                if crop_bgr.size == 0:
                    continue
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
            except Exception as e:
                _log.error("detect_audio_roi ROI[%s] 오류: %s", label, e)

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
        for state in self._tone_states.values():
            state.reset()
        self.reset_embedded_silence()
