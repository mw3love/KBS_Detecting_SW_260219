"""
정파준비모드 / 정파모드 상태 관리 모듈

상태 흐름:
  IDLE → PREPARATION(정파준비) → SIGNOFF(정파모드) → IDLE
  수동 토글: IDLE → PREPARATION, PREPARATION/SIGNOFF → IDLE

enter_roi 형식 (정파준비 → 정파모드):
  {"video_label": str, "audio_label": str}
  - 논리: video OR audio (OR 고정)
  - 하나만 지정 가능

exit_roi 형식 (정파모드 → 정파해제):
  {"video_label": str, "audio_label": str}
  - 논리: video AND audio 모두 해제 시 (AND 고정)
  - 해제 트리거: exit_roi 조건 모두 해제 OR 스케줄 종료
"""
import time
import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal


class SignoffState(Enum):
    """정파 상태"""
    IDLE        = "IDLE"         # 비활성 (시간대 밖)
    PREPARATION = "PREPARATION"  # 정파준비모드 (시간 도달, 조건 감시 중)
    SIGNOFF     = "SIGNOFF"      # 정파모드 (조건 충족 후 N초 유지)


@dataclass
class SignoffGroup:
    """그룹별 정파 설정"""
    group_id: int
    name: str
    enter_roi: dict          # {"video_label": str, "audio_label": str} — OR 고정
    exit_roi: dict           # {"video_label": str, "audio_label": str} — AND 고정
    start_time: str          # "HH:MM" 형식
    end_time: str            # "HH:MM" 형식
    end_next_day: bool       # True이면 종료 시간이 익일 기준
    every_day: bool          # True이면 weekdays 무시하고 매일 적용
    weekdays: List[int]      # 0=월 ~ 6=일
    signoff_duration: float  # 정파 판단 지속 시간(초)
    recovery_duration: float # 정파 해제 판단 지속 시간(초)

    def to_dict(self) -> dict:
        return {
            "name":         self.name,
            "enter_roi":    dict(self.enter_roi),
            "exit_roi":     dict(self.exit_roi),
            "start_time":   self.start_time,
            "end_time":     self.end_time,
            "end_next_day": self.end_next_day,
            "every_day":    self.every_day,
            "weekdays":     list(self.weekdays),
        }

    @classmethod
    def from_dict(cls, d: dict, group_id: int,
                  signoff_duration: float,
                  recovery_duration: float) -> "SignoffGroup":
        """
        구버전(roi_rules, roi_labels) → 신버전(enter_roi, exit_roi) 자동 마이그레이션.
        """
        # 신버전 키 우선 사용
        enter_roi = d.get("enter_roi", {})
        exit_roi  = d.get("exit_roi",  {})

        # 구버전 roi_rules 마이그레이션 → enter_roi로만 변환 (첫 번째 행)
        if not enter_roi:
            old_rules = d.get("roi_rules", [])
            if old_rules:
                first = old_rules[0]
                enter_roi = {
                    "video_label": first.get("video_label", ""),
                    "audio_label": first.get("audio_label", ""),
                }

        # 구버전 roi_labels 마이그레이션
        if not enter_roi:
            old_labels = d.get("roi_labels", [])
            if old_labels:
                v_lbl = next((l for l in old_labels if l.startswith("V")), "")
                a_lbl = next((l for l in old_labels if l.startswith("A")), "")
                if v_lbl or a_lbl:
                    enter_roi = {"video_label": v_lbl, "audio_label": a_lbl}

        # 기본값 보장
        if not enter_roi:
            enter_roi = {"video_label": "", "audio_label": ""}
        if not exit_roi:
            exit_roi = {"video_label": "", "audio_label": ""}

        # every_day: weekdays가 7개(전체)이면 True, 빈 배열은 "요일 미설정" = False
        raw_weekdays = list(d.get("weekdays", [0, 1, 2, 3, 4, 5, 6]))
        every_day = d.get("every_day", len(raw_weekdays) == 7)

        return cls(
            group_id=group_id,
            name=d.get("name", f"Group{group_id}"),
            enter_roi=enter_roi,
            exit_roi=exit_roi,
            start_time=d.get("start_time", "00:30"),
            end_time=d.get("end_time",   "06:00"),
            end_next_day=bool(d.get("end_next_day", False)),
            every_day=every_day,
            weekdays=raw_weekdays,
            signoff_duration=signoff_duration,
            recovery_duration=recovery_duration,
        )


class SignoffManager(QObject):
    """
    정파준비/정파모드 상태 관리자.
    QTimer 기반으로 1초마다 상태 전환 조건 점검.
    """

    # (group_id, state_str)
    state_changed = Signal(int, str)
    # (group_id, message) — 로그/알림음 용
    event_occurred = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: Dict[int, SignoffGroup] = {}
        self._states: Dict[int, SignoffState] = {}
        self._condition_start: Dict[int, Optional[float]] = {}       # 정파 조건 시작 시각
        self._recovery_start: Dict[int, Optional[float]] = {}        # 복구 조건 시작 시각
        self._signoff_entered_at: Dict[int, Optional[float]] = {}    # SIGNOFF 진입 시각
        self._preparation_entered_at: Dict[int, Optional[float]] = {}  # PREPARATION 진입 시각 (Running Time용)
        self._manual_override: Dict[int, bool] = {}  # 수동 정파준비 오버라이드 (시간창 무시)

        # 최신 감지 결과 캐시
        self._latest_video: Dict[str, bool] = {}   # label → still/비디오 감지 여부
        self._latest_tone: Dict[str, bool] = {}    # label → tone(active) 여부

        self._auto_preparation: bool = True  # 자동 정파 준비 모드 (False이면 시간 도달해도 PREPARATION 진입 안함)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── 그룹 설정 ─────────────────────────────────────────────────────────

    def set_group(self, group: SignoffGroup):
        """그룹 정보 설정. 기존 상태 유지(IDLE에서만 초기화)."""
        gid = group.group_id
        self._groups[gid] = group
        if gid not in self._states:
            self._states[gid] = SignoffState.IDLE
            self._condition_start[gid] = None
            self._recovery_start[gid] = None
            self._signoff_entered_at[gid] = None
            self._preparation_entered_at[gid] = None
            self._manual_override[gid] = False

    def get_state(self, group_id: int) -> SignoffState:
        return self._states.get(group_id, SignoffState.IDLE)

    def get_groups(self) -> Dict[int, SignoffGroup]:
        return dict(self._groups)

    def configure_from_dict(self, signoff_cfg: dict):
        """config["signoff"] dict에서 그룹 설정 전체 로드."""
        signoff_duration  = float(signoff_cfg.get("signoff_duration",  120.0))
        recovery_duration = float(signoff_cfg.get("recovery_duration",  30.0))
        self._auto_preparation = bool(signoff_cfg.get("auto_preparation", True))

        for gid in (1, 2):
            key = f"group{gid}"
            grp_data = signoff_cfg.get(key, {})
            group = SignoffGroup.from_dict(
                grp_data, gid,
                signoff_duration, recovery_duration
            )
            self.set_group(group)

    # ── 감지 데이터 수신 인터페이스 ──────────────────────────────────────

    def update_detection(self,
                         audio_results: dict,
                         still_results: dict = None,
                         white_results: dict = None):
        """
        _run_detection()에서 매 감지 주기마다 호출.
        audio_results : {label: {"active": bool, ...}}
        still_results : {label: bool}  (신버전: 스틸 감지 결과)
        white_results : {label: bool}  (구버전 호환)
        """
        video_results = still_results if still_results is not None else (white_results or {})
        self._latest_video.update(video_results)
        for label, state in audio_results.items():
            # tone_alerting 우선 사용: 1kHz 톤 감지(ratio 표준편차 기반)
            # tone_alerting 없으면 active(무음 역방향)으로 폴백 (하위 호환)
            self._latest_tone[label] = state.get("tone_alerting", state.get("active", False))

    # ── 수동 해제 ─────────────────────────────────────────────────────────

    def force_manual_release(self, group_id: int):
        """수동 해제: SIGNOFF → PREPARATION 복귀."""
        if self._states.get(group_id) == SignoffState.SIGNOFF:
            self._condition_start[group_id] = None
            self._recovery_start[group_id] = None
            self._signoff_entered_at[group_id] = None
            self._transition_to(group_id, SignoffState.PREPARATION)

    def force_start_preparation(self):
        """
        수동 정파준비 토글.
        - 모든 그룹이 IDLE이면: IDLE → PREPARATION (수동 on)
        - 하나라도 PREPARATION/SIGNOFF이면: 해당 그룹 → IDLE (수동 off)
        """
        any_active = any(
            self._states.get(gid) != SignoffState.IDLE
            for gid in self._groups
        )

        if any_active:
            # 수동 off: PREPARATION/SIGNOFF → IDLE
            for gid in list(self._groups.keys()):
                state = self._states.get(gid)
                if state in (SignoffState.PREPARATION, SignoffState.SIGNOFF):
                    self._condition_start[gid] = None
                    self._recovery_start[gid] = None
                    self._signoff_entered_at[gid] = None
                    self._manual_override[gid] = False
                    self._transition_to(gid, SignoffState.IDLE)
        else:
            # 수동 on: IDLE → PREPARATION
            for gid in list(self._groups.keys()):
                if self._states.get(gid) == SignoffState.IDLE:
                    self._manual_override[gid] = True
                    self._transition_to(gid, SignoffState.PREPARATION)

    # ── 알림 차단 판단 ────────────────────────────────────────────────────

    def is_signoff_label(self, label: str) -> bool:
        """해당 label이 현재 SIGNOFF 상태인 그룹의 enter_roi 또는 exit_roi에 속하는지 반환."""
        for gid, group in self._groups.items():
            if self._states.get(gid) == SignoffState.SIGNOFF:
                relevant = {
                    group.enter_roi.get("video_label", ""),
                    group.enter_roi.get("audio_label", ""),
                    group.exit_roi.get("video_label", ""),
                    group.exit_roi.get("audio_label", ""),
                }
                relevant.discard("")  # 빈 문자열 제거
                if label in relevant:
                    return True
        return False

    def is_any_signoff(self) -> bool:
        """그룹 중 하나라도 SIGNOFF 상태이면 True (임베디드 오디오 억제용)."""
        return any(
            self._states.get(gid) == SignoffState.SIGNOFF
            for gid in self._groups
        )

    def is_group_enabled(self, group_id: int) -> bool:
        """
        그룹 시계 표시 가능 여부 반환.
        자동정파준비 비활성화 또는 유효 요일 없으면 False.
        """
        if not self._auto_preparation:
            return False
        group = self._groups.get(group_id)
        if group is None:
            return False
        if group.every_day:
            return True
        return len(group.weekdays) > 0

    # ── 잔여/경과 시간 ────────────────────────────────────────────────────

    def get_elapsed_seconds(self, group_id: int) -> float:
        """
        IDLE: 다음 유효한 start_time까지 남은 초 반환 (요일 필터 적용).
        PREPARATION: Running Time 용도로 get_preparation_elapsed() 사용 권장.
                     여기서는 SIGNOFF 진입까지 남은 시간(end_time 기준)을 반환.
        SIGNOFF: 정파모드 진입 후 경과 초 반환.
        """
        state = self._states.get(group_id, SignoffState.IDLE)
        group = self._groups.get(group_id)
        if group is None:
            return 0.0

        now = datetime.datetime.now()

        if state == SignoffState.IDLE:
            if not group.start_time:
                return 0.0
            start_h, start_m = map(int, group.start_time.split(":"))
            # 요일 필터 적용하여 다음 유효 날짜 탐색 (최대 8일 탐색)
            for offset in range(8):
                candidate = now.replace(
                    hour=start_h, minute=start_m, second=0, microsecond=0
                ) + datetime.timedelta(days=offset)
                if candidate <= now:
                    continue  # 이미 지난 시간
                wd = candidate.weekday()
                if group.every_day or wd in group.weekdays:
                    return max(0.0, (candidate - now).total_seconds())
            return 0.0

        elif state == SignoffState.PREPARATION:
            end_h, end_m = map(int, group.end_time.split(":"))
            end_dt = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            if end_dt <= now:
                end_dt += datetime.timedelta(days=1)
            return max(0.0, (end_dt - now).total_seconds())

        elif state == SignoffState.SIGNOFF:
            entered = self._signoff_entered_at.get(group_id)
            if entered is None:
                return 0.0
            return time.time() - entered

        return 0.0

    def get_preparation_elapsed(self, group_id: int) -> float:
        """
        PREPARATION 상태 진입 후 경과 초 반환 (Running Time 표시용).
        PREPARATION 상태가 아니면 0.0 반환.
        """
        if self._states.get(group_id) != SignoffState.PREPARATION:
            return 0.0
        entered = self._preparation_entered_at.get(group_id)
        if entered is None:
            return 0.0
        return time.time() - entered

    def has_schedule_in_window(self, group_id: int) -> bool:
        """
        당일 09:00 ~ 익일 09:00 범위 내에 유효한 스케줄이 있는지 반환.

        판단 기준:
        - weekdays 비어있고 every_day=False → False
        - every_day=True → True (요일 무관)
        - start_time >= "09:00": 오늘 요일로 weekdays 확인
        - start_time < "09:00": 익일 요일로 weekdays 확인
        """
        group = self._groups.get(group_id)
        if group is None:
            return False
        if not group.every_day and not group.weekdays:
            return False
        if group.every_day:
            return True

        now = datetime.datetime.now()
        start_h = int(group.start_time.split(":")[0])
        if start_h >= 9:
            check_weekday = now.weekday()
        else:
            check_weekday = (now + datetime.timedelta(days=1)).weekday()
        return check_weekday in group.weekdays

    # ── 1초 주기 상태 점검 ────────────────────────────────────────────────

    def _tick(self):
        """매 1초 호출: 시간 기반 + 감지 결과 기반 상태 전환."""
        now = datetime.datetime.now()
        weekday = now.weekday()                   # 0=월 ~ 6=일
        current_time = now.strftime("%H:%M")

        for gid, group in self._groups.items():
            current_state = self._states[gid]
            in_window = self._is_in_time_window(group, current_time, weekday)

            if current_state == SignoffState.IDLE:
                if in_window and self._auto_preparation:
                    self._transition_to(gid, SignoffState.PREPARATION)

            elif current_state == SignoffState.PREPARATION:
                if not in_window and not self._manual_override.get(gid, False):
                    self._condition_start[gid] = None
                    self._transition_to(gid, SignoffState.IDLE)
                else:
                    if self._check_signoff_condition(group):
                        if self._condition_start[gid] is None:
                            self._condition_start[gid] = time.time()
                        elapsed = time.time() - self._condition_start[gid]
                        if elapsed >= group.signoff_duration:
                            self._condition_start[gid] = None
                            self._transition_to(gid, SignoffState.SIGNOFF)
                    else:
                        self._condition_start[gid] = None

            elif current_state == SignoffState.SIGNOFF:
                if not in_window:
                    self._recovery_start[gid] = None
                    self._signoff_entered_at[gid] = None
                    self._transition_to(gid, SignoffState.IDLE)
                else:
                    if self._check_recovery_condition(group):
                        if self._recovery_start[gid] is None:
                            self._recovery_start[gid] = time.time()
                        elapsed = time.time() - self._recovery_start[gid]
                        if elapsed >= group.recovery_duration:
                            self._recovery_start[gid] = None
                            self._signoff_entered_at[gid] = None
                            self._transition_to(gid, SignoffState.PREPARATION)
                    else:
                        self._recovery_start[gid] = None

    def _is_in_time_window(self, group: SignoffGroup,
                            current_time: str, weekday: int) -> bool:
        """현재 시각이 그룹 시간 범위 내인지 판단.

        end_next_day=True 이면 종료 시간이 익일 기준으로 처리된다.
        예) 시작 23:30 → 익일 02:00 구간:
          - 23:30 이후(당일 요일 확인) OR 02:00 미만(전날 요일 확인)
        """
        if group.end_next_day:
            if current_time >= group.start_time:
                # 당일 구간 → 당일 요일로 확인
                if not group.every_day and weekday not in group.weekdays:
                    return False
                return True
            elif current_time < group.end_time:
                # 익일 구간 → 전날 요일로 확인
                prev_weekday = (weekday - 1) % 7
                if not group.every_day and prev_weekday not in group.weekdays:
                    return False
                return True
            else:
                return False
        else:
            if not group.every_day and weekday not in group.weekdays:
                return False
            return group.start_time <= current_time < group.end_time

    def _check_signoff_condition(self, group: SignoffGroup) -> bool:
        """
        enter_roi 평가 → 정파 진입 조건 판정 (OR 고정).
        video 또는 audio 중 하나라도 감지되면 True.
        enter_roi 미설정 시 항상 False.
        """
        roi = group.enter_roi
        v_label = roi.get("video_label", "")
        a_label = roi.get("audio_label", "")

        if not v_label and not a_label:
            return False  # 감지영역 미설정

        v_result = self._latest_video.get(v_label, False) if v_label else False
        a_result = self._latest_tone.get(a_label, False)  if a_label else False

        return v_result or a_result  # OR 고정

    def _check_recovery_condition(self, group: SignoffGroup) -> bool:
        """
        exit_roi 평가 → 정파 해제 조건 판정 (AND 고정).
        exit_roi에 지정된 video AND audio가 모두 '감지 해제'(False) 상태여야 True.

        exit_roi 미설정 시: enter_roi의 역(기존 동작)으로 폴백.
        """
        roi = group.exit_roi
        v_label = roi.get("video_label", "")
        a_label = roi.get("audio_label", "")

        # exit_roi 미설정 → enter_roi의 역으로 폴백
        if not v_label and not a_label:
            return not self._check_signoff_condition(group)

        v_result = self._latest_video.get(v_label, False) if v_label else None
        a_result = self._latest_tone.get(a_label, False)  if a_label else None

        # AND 고정: 지정된 것 모두 False(해제)여야 복구
        if v_result is not None and a_result is not None:
            return (not v_result) and (not a_result)
        elif v_result is not None:
            return not v_result
        else:
            return not a_result  # a_result is not None

    def _transition_to(self, group_id: int, new_state: SignoffState):
        """상태 전환 + 시그널 발송."""
        old_state = self._states.get(group_id)
        if old_state == new_state:
            return

        self._states[group_id] = new_state

        # IDLE 진입 시 수동 오버라이드 해제
        if new_state == SignoffState.IDLE:
            self._manual_override[group_id] = False
            self._preparation_entered_at[group_id] = None

        if new_state == SignoffState.PREPARATION:
            self._preparation_entered_at[group_id] = time.time()

        if new_state == SignoffState.SIGNOFF:
            self._signoff_entered_at[group_id] = time.time()
            self._preparation_entered_at[group_id] = None

        group = self._groups[group_id]
        if new_state == SignoffState.PREPARATION:
            if old_state == SignoffState.IDLE:
                msg = f"{group.name} 정파준비모드를 시작합니다"
            else:
                msg = f"{group.name} 정파모드를 해제합니다"
        elif new_state == SignoffState.SIGNOFF:
            msg = f"{group.name} 정파모드에 돌입합니다"
        else:  # IDLE
            if old_state == SignoffState.SIGNOFF:
                msg = f"{group.name} 정파모드를 해제합니다"
            else:
                msg = f"{group.name} 정파준비모드를 종료합니다"

        self.state_changed.emit(group_id, new_state.value)
        self.event_occurred.emit(group_id, msg)
