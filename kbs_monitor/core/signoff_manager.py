"""
정파준비모드 / 정파모드 상태 관리 모듈

상태 흐름:
  IDLE → PREPARATION(정파준비) → SIGNOFF(정파모드) → IDLE
  수동 토글: 버튼 클릭마다 IDLE→PREPARATION→SIGNOFF→IDLE 순서로 로테이션

시간 의미:
  start_time : 정파모드(SIGNOFF)가 시작되는 시각
  end_time   : 정파가 종료되는 시각
  prep_minutes : start_time 몇 분 전에 정파준비(PREPARATION)를 활성화할지
                 0이면 정파준비 없이 start_time에 바로 SIGNOFF

전환 규칙:
  IDLE → PREPARATION : (start_time - prep_minutes) 도달 시 자동 전환
                       (prep_minutes=0이면 이 단계 없이 바로 SIGNOFF)
  PREPARATION → SIGNOFF :
      1) start_time 도달 시 자동 전환 (시간 기반, 최우선)
      2) 그 전에 enter_roi 스틸이 still_trigger_sec 이상 지속 시 조기 전환
  SIGNOFF → IDLE : end_time 도달 시 자동 전환
                   또는 정파해제준비 구간에서 스틸 해제가 exit_trigger_sec 이상 지속 시 조기 종료

enter_roi 형식 (정파준비 → 정파모드):
  {"video_label": str}
  - 진입/해제 트리거: 비디오 감지영역 스틸 감지
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
    PREPARATION = "PREPARATION"  # 정파준비모드 (정파모드 전 준비 구간)
    SIGNOFF     = "SIGNOFF"      # 정파모드 (정파 진행 중)


@dataclass
class SignoffGroup:
    """그룹별 정파 설정"""
    group_id: int
    name: str
    enter_roi: dict          # {"video_label": str} — 정파 진입 트리거 ROI
    suppressed_labels: List[str]  # 정파(SIGNOFF/PREPARATION) 중 알림을 억제할 video ROI label 목록
    start_time: str          # "HH:MM" 형식 — 정파모드(SIGNOFF) 시작 시각
    end_time: str            # "HH:MM" 형식 — 정파 종료 시각
    prep_minutes: int        # 정파준비 시작 = start_time - prep_minutes (0, 30, 60, 90, 120, 150, 180)
    exit_prep_minutes: int   # 정파해제준비 시작 = end_time - exit_prep_minutes (0=사용 안 함)
    end_next_day: bool       # True이면 종료 시간이 익일 기준
    every_day: bool          # True이면 weekdays 무시하고 매일 적용
    weekdays: List[int]      # 0=월 ~ 6=일
    still_trigger_sec: float  # 스틸 감지 기준 시간 (감도설정 still_duration)
    exit_trigger_sec: float   # 정파해제 트리거 시간: 비-스틸 상태가 N초 이상 지속 시 해제

    def to_dict(self) -> dict:
        return {
            "name":              self.name,
            "enter_roi":         dict(self.enter_roi),
            "suppressed_labels": list(self.suppressed_labels),
            "start_time":        self.start_time,
            "end_time":          self.end_time,
            "prep_minutes":      self.prep_minutes,
            "exit_prep_minutes": self.exit_prep_minutes,
            "exit_trigger_sec":  self.exit_trigger_sec,
            "end_next_day":      self.end_next_day,
            "every_day":         self.every_day,
            "weekdays":          list(self.weekdays),
        }

    @classmethod
    def from_dict(cls, d: dict, group_id: int,
                  still_trigger_sec: float) -> "SignoffGroup":
        """
        구버전(roi_rules, roi_labels, exit_roi) → 신버전(enter_roi) 자동 마이그레이션.
        """
        # 신버전 enter_roi 우선 사용
        enter_roi = d.get("enter_roi", {})

        # 구버전 roi_rules 마이그레이션 → enter_roi로 변환 (첫 번째 행의 video_label만 사용)
        if not enter_roi:
            old_rules = d.get("roi_rules", [])
            if old_rules:
                first = old_rules[0]
                enter_roi = {"video_label": first.get("video_label", "")}

        # 구버전 roi_labels 마이그레이션
        if not enter_roi:
            old_labels = d.get("roi_labels", [])
            if old_labels:
                v_lbl = next((l for l in old_labels if l.startswith("V")), "")
                if v_lbl:
                    enter_roi = {"video_label": v_lbl}

        # 기본값 보장
        if not enter_roi:
            enter_roi = {"video_label": ""}

        # suppressed_labels 로드 (구버전 호환: 없으면 enter_roi.video_label 자동 포함)
        suppressed_labels = list(d.get("suppressed_labels", []))
        if not suppressed_labels:
            v_label = enter_roi.get("video_label", "")
            if v_label:
                suppressed_labels = [v_label]

        # every_day: weekdays가 7개(전체)이면 True, 빈 배열은 "요일 미설정" = False
        raw_weekdays = list(d.get("weekdays", [0, 1, 2, 3, 4, 5, 6]))
        every_day = d.get("every_day", len(raw_weekdays) == 7)

        prep_minutes = int(d.get("prep_minutes", 30))
        # 30분 단위, 0~180 범위로 클램프
        prep_minutes = max(0, min(180, (prep_minutes // 30) * 30))

        exit_prep_minutes = int(d.get("exit_prep_minutes", 0))
        exit_prep_minutes = max(0, min(180, (exit_prep_minutes // 30) * 30))

        exit_trigger_sec = max(0.0, float(d.get("exit_trigger_sec", 5.0)))

        return cls(
            group_id=group_id,
            name=d.get("name", f"Group{group_id}"),
            enter_roi=enter_roi,
            suppressed_labels=suppressed_labels,
            start_time=d.get("start_time", "00:30"),
            end_time=d.get("end_time",   "06:00"),
            prep_minutes=prep_minutes,
            exit_prep_minutes=exit_prep_minutes,
            exit_trigger_sec=exit_trigger_sec,
            end_next_day=bool(d.get("end_next_day", False)),
            every_day=every_day,
            weekdays=raw_weekdays,
            still_trigger_sec=still_trigger_sec,
        )


class SignoffManager(QObject):
    """
    정파준비/정파모드 상태 관리자.
    QTimer 기반으로 1초마다 상태 전환 조건 점검.

    정파준비→정파 전환 (스틸 단독):
      1순위: start_time 도달 시 자동 전환
      2순위: 스틸이 still_trigger_sec 이상 지속 시 조기 전환

    정파→정파해제:
      end_time 도달 시 자동 전환
      또는 정파해제준비 구간에서 비-스틸 상태가 exit_trigger_sec 이상 지속 시 조기 종료
    """

    # (group_id, state_str)
    state_changed = Signal(int, str)
    # (group_id, message) — 로그/알림음 용
    event_occurred = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: Dict[int, SignoffGroup] = {}
        self._states: Dict[int, SignoffState] = {}

        # 정파준비→정파 진입 타이머
        self._video_enter_start: Dict[int, Optional[float]] = {}   # 스틸 감지 시작 시각
        # 정파해제준비 구간: 비-스틸 지속 타이머
        self._video_exit_start: Dict[int, Optional[float]] = {}    # 비-스틸 감지 시작 시각

        self._signoff_entered_at: Dict[int, Optional[float]] = {}    # SIGNOFF 진입 시각
        self._preparation_entered_at: Dict[int, Optional[float]] = {}  # PREPARATION 진입 시각
        self._manual_override: Dict[int, bool] = {}  # 수동 상태 오버라이드 여부
        self._exit_released: Dict[int, bool] = {}    # 정파해제준비로 조기 해제 후 자동 재진입 차단 플래그

        # 최신 감지 결과 캐시
        self._latest_video: Dict[str, bool] = {}   # label → still 감지 여부

        self._auto_preparation: bool = True  # 자동 정파 준비 모드

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── 그룹 설정 ─────────────────────────────────────────────────────────

    def set_group(self, group: SignoffGroup):
        """그룹 정보 설정. 기존 상태 유지(IDLE에서만 초기화)."""
        gid = group.group_id
        old_group = self._groups.get(gid)
        self._groups[gid] = group
        if gid not in self._states:
            self._states[gid] = SignoffState.IDLE
            self._video_enter_start[gid] = None
            self._video_exit_start[gid] = None
            self._signoff_entered_at[gid] = None
            self._preparation_entered_at[gid] = None
            self._manual_override[gid] = False
            self._exit_released[gid] = False
        elif old_group is not None:
            schedule_changed = (
                old_group.start_time != group.start_time
                or old_group.end_time != group.end_time
                or set(old_group.weekdays) != set(group.weekdays)
                or old_group.every_day != group.every_day
                or old_group.prep_minutes != group.prep_minutes
                or old_group.exit_prep_minutes != group.exit_prep_minutes
                or old_group.end_next_day != group.end_next_day
            )
            if schedule_changed:
                # 조기 해제 후 자동 재진입 차단 플래그 리셋
                self._exit_released[gid] = False

                # 수동 오버라이드가 아닌 경우, 새 스케줄 기준으로 현재 상태 재검사
                if not self._manual_override.get(gid, False):
                    now = datetime.datetime.now()
                    weekday = now.weekday()
                    current_time = now.strftime("%H:%M")
                    current_state = self._states.get(gid, SignoffState.IDLE)
                    in_prep_window = self._is_in_prep_window(group, current_time, weekday)
                    in_signoff_window = self._is_in_signoff_window(group, current_time, weekday)

                    if current_state == SignoffState.SIGNOFF:
                        if not in_prep_window:
                            # 정파 시간창 완전히 벗어남 → IDLE
                            self._signoff_entered_at[gid] = None
                            self._transition_to(gid, SignoffState.IDLE)
                        elif not in_signoff_window:
                            # 정파준비 구간이지만 정파 시간 미도달 → PREPARATION으로 다운그레이드
                            self._signoff_entered_at[gid] = None
                            self._transition_to(gid, SignoffState.PREPARATION)
                    elif current_state == SignoffState.PREPARATION:
                        if not in_prep_window:
                            # 정파준비 시간창 벗어남 → IDLE
                            self._reset_enter_timers(gid)
                            self._transition_to(gid, SignoffState.IDLE)

    def get_state(self, group_id: int) -> SignoffState:
        return self._states.get(group_id, SignoffState.IDLE)

    def get_groups(self) -> Dict[int, SignoffGroup]:
        return dict(self._groups)

    def configure_from_dict(self, signoff_cfg: dict,
                            still_trigger_sec: float = 60.0):
        """config["signoff"] dict에서 그룹 설정 전체 로드."""
        self._auto_preparation = bool(signoff_cfg.get("auto_preparation", True))

        for gid in (1, 2):
            key = f"group{gid}"
            grp_data = signoff_cfg.get(key, {})
            group = SignoffGroup.from_dict(
                grp_data, gid,
                still_trigger_sec
            )
            self.set_group(group)

    # ── 감지 데이터 수신 인터페이스 ──────────────────────────────────────

    def update_detection(self, still_results: dict):
        """
        _run_detection()에서 매 감지 주기마다 호출.
        still_results : {label: bool}  — 비디오 ROI별 스틸 감지 여부
        """
        self._latest_video.update(still_results)

    # ── 수동 상태 전환 ────────────────────────────────────────────────────

    def cycle_state(self, group_id: int):
        """
        수동 버튼 클릭으로 상태 로테이션.
        정파 시간 범위 내: IDLE → PREPARATION → SIGNOFF → IDLE 순서로 순환.
        정파 시간 범위 밖: IDLE → PREPARATION → IDLE 순서로 순환
                          (SIGNOFF 상태를 거치지 않고 바로 비활성화).
        """
        current = self._states.get(group_id, SignoffState.IDLE)

        if current == SignoffState.IDLE:
            self._manual_override[group_id] = True
            self._reset_enter_timers(group_id)
            self._transition_to(group_id, SignoffState.PREPARATION)

        elif current == SignoffState.PREPARATION:
            now = datetime.datetime.now()
            group = self._groups.get(group_id)
            in_signoff = (
                group is not None
                and self._is_in_signoff_window(
                    group, now.strftime("%H:%M"), now.weekday()
                )
            )
            if in_signoff:
                # 정파 시간 범위 내: SIGNOFF로 전환
                self._manual_override[group_id] = True
                self._reset_enter_timers(group_id)
                self._transition_to(group_id, SignoffState.SIGNOFF)
            else:
                # 정파 시간 범위 밖: 바로 IDLE로 복귀
                self._reset_enter_timers(group_id)
                self._manual_override[group_id] = False
                self._transition_to(group_id, SignoffState.IDLE)

        elif current == SignoffState.SIGNOFF:
            self._signoff_entered_at[group_id] = None
            self._manual_override[group_id] = False
            self._transition_to(group_id, SignoffState.IDLE)

    # ── 알림 차단 판단 ────────────────────────────────────────────────────

    def is_signoff_label(self, label: str) -> bool:
        """해당 label이 현재 SIGNOFF 상태인 그룹의 억제 대상인지 반환.
        enter_roi.video_label 또는 suppressed_labels 포함 여부를 확인한다."""
        for gid, group in self._groups.items():
            if self._states.get(gid) == SignoffState.SIGNOFF:
                v_label = group.enter_roi.get("video_label", "")
                if v_label and label == v_label:
                    return True
                if label in group.suppressed_labels:
                    return True
        return False

    def is_prep_label(self, label: str) -> bool:
        """해당 label이 현재 PREPARATION 상태인 그룹의 억제 대상인지 반환.
        True이면 스틸 알림을 억제한다."""
        for gid, group in self._groups.items():
            if self._states.get(gid) == SignoffState.PREPARATION:
                v_label = group.enter_roi.get("video_label", "")
                if v_label and label == v_label:
                    return True
                if label in group.suppressed_labels:
                    return True
        return False

    def is_any_signoff(self) -> bool:
        """그룹 중 하나라도 SIGNOFF 상태이면 True."""
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
        IDLE: 다음 유효한 정파준비(prep_start_time)까지 남은 초 반환.
              prep_minutes=0이면 start_time 기준.
        PREPARATION: 정파모드 시작(start_time)까지 남은 초 반환.
        SIGNOFF: 정파모드 진입 후 경과 초 반환.
        """
        state = self._states.get(group_id, SignoffState.IDLE)
        group = self._groups.get(group_id)
        if group is None:
            return 0.0

        now = datetime.datetime.now()

        if state == SignoffState.IDLE:
            # 다음 정파준비 시작 시각 기준 잔여 시간
            prep_start = self._calc_prep_start_str(group)
            if not prep_start:
                return 0.0
            h, m = map(int, prep_start.split(":"))
            for offset in range(8):
                candidate = now.replace(
                    hour=h, minute=m, second=0, microsecond=0
                ) + datetime.timedelta(days=offset)
                if candidate <= now:
                    continue
                wd = candidate.weekday()
                if group.every_day or wd in group.weekdays:
                    return max(0.0, (candidate - now).total_seconds())
            return 0.0

        elif state == SignoffState.PREPARATION:
            # 정파모드 시작(start_time)까지 남은 시간
            start_h, start_m = map(int, group.start_time.split(":"))
            signoff_dt = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            if signoff_dt <= now:
                signoff_dt += datetime.timedelta(days=1)
            return max(0.0, (signoff_dt - now).total_seconds())

        elif state == SignoffState.SIGNOFF:
            entered = self._signoff_entered_at.get(group_id)
            if entered is None:
                return 0.0
            return time.time() - entered

        return 0.0

    def get_end_remaining_seconds(self, group_id: int) -> float:
        """SIGNOFF 상태에서 end_time까지 남은 초 반환. 잔여시간 표시용."""
        group = self._groups.get(group_id)
        if group is None:
            return 0.0
        now = datetime.datetime.now()
        end_h, end_m = map(int, group.end_time.split(":"))
        end_dt = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        if end_dt <= now:
            end_dt += datetime.timedelta(days=1)
        return max(0.0, (end_dt - now).total_seconds())

    def get_preparation_elapsed(self, group_id: int) -> float:
        """PREPARATION 상태 진입 후 경과 초 반환 (Running Time 표시용)."""
        if self._states.get(group_id) != SignoffState.PREPARATION:
            return 0.0
        entered = self._preparation_entered_at.get(group_id)
        if entered is None:
            return 0.0
        return time.time() - entered

    def has_schedule_in_window(self, group_id: int) -> bool:
        """당일 09:00 ~ 익일 09:00 범위 내에 유효한 스케줄이 있는지 반환."""
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

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────

    def _calc_prep_start_str(self, group: SignoffGroup) -> str:
        """정파준비 시작 시각 문자열 반환 (HH:MM). prep_minutes=0이면 start_time 반환."""
        if group.prep_minutes == 0:
            return group.start_time
        start_h, start_m = map(int, group.start_time.split(":"))
        total_min = start_h * 60 + start_m - group.prep_minutes
        total_min = total_min % (24 * 60)
        return f"{total_min // 60:02d}:{total_min % 60:02d}"

    def _reset_enter_timers(self, gid: int):
        """정파 진입 타이머 초기화."""
        self._video_enter_start[gid] = None

    # ── 1초 주기 상태 점검 ────────────────────────────────────────────────

    def _tick(self):
        """매 1초 호출: 시간 기반 + 감지 결과 기반 상태 전환."""
        now = datetime.datetime.now()
        weekday = now.weekday()
        current_time = now.strftime("%H:%M")

        for gid, group in self._groups.items():
            current_state = self._states[gid]
            in_prep_window   = self._is_in_prep_window(group, current_time, weekday)
            in_signoff_window = self._is_in_signoff_window(group, current_time, weekday)

            if current_state == SignoffState.IDLE:
                if self._auto_preparation:
                    # 정파해제준비로 조기 해제된 경우: end_time(prep_window 종료)까지 자동 재진입 차단
                    if self._exit_released.get(gid, False):
                        if not in_prep_window:
                            self._exit_released[gid] = False  # 시간창 완전히 벗어남 → 락 해제
                    elif in_signoff_window:
                        # 이미 정파 시간 내 → prep_minutes 상관없이 바로 SIGNOFF
                        self._transition_to(gid, SignoffState.SIGNOFF)
                    elif in_prep_window:
                        self._transition_to(gid, SignoffState.PREPARATION)

            elif current_state == SignoffState.PREPARATION:
                is_manual = self._manual_override.get(gid, False)
                if not in_prep_window and not is_manual:
                    # 정파준비 시간창을 벗어남 → IDLE로 복귀
                    self._reset_enter_timers(gid)
                    self._transition_to(gid, SignoffState.IDLE)
                elif in_signoff_window:
                    # start_time 도달 → 자동 SIGNOFF 전환 (감지 결과 무관)
                    self._reset_enter_timers(gid)
                    self._transition_to(gid, SignoffState.SIGNOFF)
                else:
                    # 아직 PREPARATION 구간: 스틸/톤 감지로 조기 전환 판단
                    self._tick_preparation(gid, group)

            elif current_state == SignoffState.SIGNOFF:
                is_manual = self._manual_override.get(gid, False)
                if not in_prep_window and not is_manual:
                    # end_time 도달 → IDLE로 전환 (수동 오버라이드 상태에서는 유지)
                    self._signoff_entered_at[gid] = None
                    self._manual_override[gid] = False
                    self._transition_to(gid, SignoffState.IDLE)
                elif group.exit_prep_minutes > 0:
                    # 정파해제준비 구간: 감지 조건 해제 시 조기 종료
                    if self._is_in_exit_prep_window(group):
                        self._tick_exit_preparation(gid, group)

    def _tick_preparation(self, gid: int, group: SignoffGroup):
        """
        PREPARATION 구간에서 스틸 감지로 조기 SIGNOFF 전환 판단 (스틸 단독).
        enter_roi의 video_label 스틸이 still_trigger_sec 이상 지속되면 SIGNOFF 조기 전환.
        """
        v_label = group.enter_roi.get("video_label", "")

        if not v_label:
            return  # video_label 미설정 — 감지 기반 조기 전환 없음

        now = time.time()

        # ── 스틸 타이머 갱신 ──
        if self._latest_video.get(v_label, False):
            if self._video_enter_start[gid] is None:
                self._video_enter_start[gid] = now
        else:
            self._video_enter_start[gid] = None

        # ── 판단: still_trigger_sec 이상 지속 시 SIGNOFF 전환 ──
        v_elapsed = (now - self._video_enter_start[gid]) if self._video_enter_start[gid] is not None else 0.0

        if v_elapsed >= group.still_trigger_sec:
            self._reset_enter_timers(gid)
            self._transition_to(gid, SignoffState.SIGNOFF)

    def _is_in_signoff_window(self, group: SignoffGroup,
                               current_time: str, weekday: int) -> bool:
        """현재 시각이 정파모드(start_time ~ end_time) 범위 내인지 판단."""
        return self._is_in_time_range(
            group, current_time, weekday,
            group.start_time, group.end_time
        )

    def _is_in_prep_window(self, group: SignoffGroup,
                            current_time: str, weekday: int) -> bool:
        """현재 시각이 정파준비(prep_start ~ end_time) 범위 내인지 판단.
        prep_start가 end_time보다 클 때(자정을 넘기는 prep 구간)는
        group.end_next_day와 무관하게 날짜 넘김 로직으로 처리한다."""
        prep_start = self._calc_prep_start_str(group)
        # prep_start > end_time이면 prep 구간 자체가 자정을 넘김
        if prep_start > group.end_time:
            if current_time >= prep_start:
                if not group.every_day and weekday not in group.weekdays:
                    return False
                return True
            elif current_time < group.end_time:
                prev_weekday = (weekday - 1) % 7
                if not group.every_day and prev_weekday not in group.weekdays:
                    return False
                return True
            else:
                return False
        return self._is_in_time_range(
            group, current_time, weekday,
            prep_start, group.end_time
        )

    def _is_in_exit_prep_window(self, group: SignoffGroup) -> bool:
        """현재 시각이 정파해제준비 구간(end_time - exit_prep_minutes) 내인지 판단.
        이미 SIGNOFF 상태에서만 호출되므로 요일/기본 범위 체크는 생략하고
        '종료 N분 전 이내인지'만 판별한다."""
        if group.exit_prep_minutes == 0:
            return False
        now = datetime.datetime.now()
        end_h, end_m = map(int, group.end_time.split(":"))
        end_dt = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        if end_dt <= now:
            end_dt += datetime.timedelta(days=1)
        remaining = (end_dt - now).total_seconds()
        return remaining <= group.exit_prep_minutes * 60

    def _tick_exit_preparation(self, gid: int, group: SignoffGroup):
        """정파해제준비 구간에서 스틸 해제가 exit_trigger_sec 이상 지속 시 SIGNOFF 조기 종료.
        스틸이 해제된 상태(비-스틸)가 group.exit_trigger_sec 초 이상 연속 지속되어야 정파 해제.
        순간적인 화면 변화에 의한 오동작 방지.
        """
        v_label = group.enter_roi.get("video_label", "")

        if not v_label:
            return  # video_label 미설정 — 감지 기반 조기 해제 없음

        now = time.time()

        # 비-스틸 상태이면 타이머 갱신, 스틸 상태이면 리셋
        if not self._latest_video.get(v_label, True):  # 스틸 아님 (기본값 True=스틸 상태)
            if self._video_exit_start[gid] is None:
                self._video_exit_start[gid] = now
            v_elapsed = now - self._video_exit_start[gid]
            if v_elapsed >= group.exit_trigger_sec:
                self._video_exit_start[gid] = None
                self._signoff_entered_at[gid] = None
                self._manual_override[gid] = False
                self._exit_released[gid] = True  # 조기 해제 후 자동 재진입 차단
                self._transition_to(gid, SignoffState.IDLE)
        else:
            # 스틸 상태 복귀 → 해제 타이머 리셋
            self._video_exit_start[gid] = None

    def _is_in_time_range(self, group: SignoffGroup,
                           current_time: str, weekday: int,
                           start: str, end: str) -> bool:
        """
        [start, end) 범위 내에 현재 시각이 있는지 판단.
        end_next_day=True이면 종료 시간이 익일 기준으로 처리.
        """
        if group.end_next_day:
            if current_time >= start:
                if not group.every_day and weekday not in group.weekdays:
                    return False
                return True
            elif current_time < end:
                prev_weekday = (weekday - 1) % 7
                if not group.every_day and prev_weekday not in group.weekdays:
                    return False
                return True
            else:
                return False
        else:
            if not group.every_day and weekday not in group.weekdays:
                return False
            return start <= current_time < end

    def _transition_to(self, group_id: int, new_state: SignoffState):
        """상태 전환 + 시그널 발송."""
        old_state = self._states.get(group_id)
        if old_state == new_state:
            return

        self._states[group_id] = new_state

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
