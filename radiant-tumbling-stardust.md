# 정파모드 로직 수정 계획

## Context

현재 정파 시스템은 `roi_rules`(여러 행, 행 간 OR, 행 내 AND/OR 선택)를 사용하여
"정파준비→정파모드" 진입 조건과 "정파모드→정파해제" 조건을 동일한 규칙으로 처리한다.
사용자 요청에 따라 두 조건을 **별도 규칙셋**으로 분리하고, 논리 연산자를 각각 OR(진입)/AND(해제)로 고정한다.
또한 수동 정파준비 버튼을 **토글** 방식(on→PREPARATION, off→IDLE)으로 변경한다.

**변경 목표:**
- 진입 조건(`enter_roi`): OR 고정, 1행, video 또는 audio 하나만 지정 가능
- 해제 조건(`exit_roi`): AND 고정, 1행, video와 audio 모두 해제되어야 해제
- 수동 버튼: 현재 상태에 따라 on/off 자동 토글
- 해제 트리거: ① exit_roi AND 조건 모두 해제 OR ② 스케줄 종료

---

## 수정 대상 파일

| 파일 | 주요 변경 |
|------|----------|
| `kbs_monitor/core/signoff_manager.py` | SignoffGroup 구조, 감지 로직, 토글 로직 |
| `kbs_monitor/ui/settings_dialog.py` | _SignoffRoiDialog 2섹션, 저장/로드 메서드 6개 |
| `kbs_monitor/utils/config_manager.py` | DEFAULT_CONFIG signoff 구조 |
| `kbs_monitor/config/kbs_config.json` | 실제 저장 설정 구조 |
| `kbs_monitor/ui/top_bar.py` | 정파준비 버튼 툴팁 |

---

## Step 1: SignoffGroup 데이터 구조 변경

**파일:** `kbs_monitor/core/signoff_manager.py`

**목적:** `roi_rules: List[dict]` → `enter_roi: dict` + `exit_roi: dict` 교체

### 1-1. 모듈 docstring 상단 수정 (파일 1~13행)

```
OLD:
roi_rules 형식:
  [{"video_label": "V1", "operator": "AND", "audio_label": "A1"}, ...]
  - operator: "AND" | "OR" | ""
  - 행 간은 OR 로 결합

NEW:
enter_roi 형식 (정파준비 → 정파모드):
  {"video_label": str, "audio_label": str}
  - 논리: video OR audio (OR 고정)
  - 하나만 지정 가능

exit_roi 형식 (정파모드 → 정파해제):
  {"video_label": str, "audio_label": str}
  - 논리: video AND audio 모두 해제 시 (AND 고정)
  - 해제 트리거: exit_roi 조건 모두 해제 OR 스케줄 종료
```

### 1-2. SignoffGroup 데이터클래스 교체

`roi_rules: List[dict]` 필드를 제거하고 `enter_roi: dict` + `exit_roi: dict` 추가:

```python
@dataclass
class SignoffGroup:
    """그룹별 정파 설정"""
    group_id: int
    name: str
    enter_roi: dict          # {"video_label": str, "audio_label": str} — OR 고정
    exit_roi: dict           # {"video_label": str, "audio_label": str} — AND 고정
    start_time: str
    end_time: str
    every_day: bool
    weekdays: List[int]
    signoff_duration: float
    recovery_duration: float

    def to_dict(self) -> dict:
        return {
            "name":       self.name,
            "enter_roi":  dict(self.enter_roi),
            "exit_roi":   dict(self.exit_roi),
            "start_time": self.start_time,
            "end_time":   self.end_time,
            "every_day":  self.every_day,
            "weekdays":   list(self.weekdays),
        }
```

### 1-3. from_dict 클래스 메서드 교체 (구버전 마이그레이션 포함)

```python
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

    # every_day: weekdays 빈 배열이면 True
    raw_weekdays = list(d.get("weekdays", [0, 1, 2, 3, 4, 5, 6]))
    every_day = d.get("every_day", len(raw_weekdays) == 0)

    return cls(
        group_id=group_id,
        name=d.get("name", f"Group{group_id}"),
        enter_roi=enter_roi,
        exit_roi=exit_roi,
        start_time=d.get("start_time", "00:30"),
        end_time=d.get("end_time",   "06:00"),
        every_day=every_day,
        weekdays=raw_weekdays,
        signoff_duration=signoff_duration,
        recovery_duration=recovery_duration,
    )
```

**검증:** `SignoffGroup.from_dict({}, 1, 120.0, 30.0)` 오류 없이 기본값 반환 확인

---

## Step 2: SignoffManager 감지 조건 로직 수정

**파일:** `kbs_monitor/core/signoff_manager.py`

**목적:** `_check_signoff_condition()` = enter_roi OR 로직, `_check_recovery_condition()` = exit_roi AND 해제 로직

### 2-1. `_check_signoff_condition()` 교체

```python
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
```

### 2-2. `_eval_roi_rule()` 메서드 삭제

기존 `_eval_roi_rule(rule: dict)` 메서드는 더 이상 사용되지 않으므로 삭제한다.

### 2-3. `_check_recovery_condition()` 교체

```python
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
```

### 2-4. `is_signoff_label()` 수정

```python
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
```

**검증:**
- `enter_roi = {"video_label": "V1", "audio_label": ""}`, `_latest_video = {"V1": True}` → `_check_signoff_condition()` = True
- `exit_roi = {"video_label": "V1", "audio_label": "A1"}`, 둘 다 False → `_check_recovery_condition()` = True
- `exit_roi = {"video_label": "V1", "audio_label": "A1"}`, V1=False A1=True → `_check_recovery_condition()` = False

---

## Step 3: SignoffManager 토글 로직 수정 + TopBar 툴팁 업데이트

### 3-1. `force_start_preparation()` 교체 (signoff_manager.py)

```python
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
```

### 3-2. 정파준비 버튼 툴팁 수정 (top_bar.py)

```python
# OLD
self._btn_preparation.setToolTip("정파준비모드 수동 시작\n(설정된 시간에 자동 활성화)")

# NEW
self._btn_preparation.setToolTip(
    "정파준비모드 수동 토글\n"
    "• 비활성 → 클릭: 정파준비 강제 시작\n"
    "• 활성 → 클릭: 정파준비/정파모드 즉시 해제"
)
```

**검증:**
- 버튼 클릭 (IDLE 상태) → PREPARATION 전환, 버튼 빨간색
- 버튼 재클릭 (PREPARATION 상태) → IDLE 전환, 버튼 색 초기화
- SIGNOFF 상태에서 버튼 클릭 → IDLE 전환

---

## Step 4: Config 구조 업데이트

### 4-1. config_manager.py — DEFAULT_CONFIG 수정

`signoff.group1`, `signoff.group2` 내부에서 `roi_rules: []` 제거, `enter_roi` + `exit_roi` 추가:

```python
# OLD
"group1": {
    "name":       "Group1",
    "roi_rules":  [],
    "start_time": "00:30",
    "end_time":   "06:00",
    "every_day":  True,
    "weekdays":   [0, 1, 2, 3, 4, 5, 6],
},

# NEW
"group1": {
    "name":       "Group1",
    "enter_roi":  {"video_label": "", "audio_label": ""},
    "exit_roi":   {"video_label": "", "audio_label": ""},
    "start_time": "00:30",
    "end_time":   "06:00",
    "every_day":  True,
    "weekdays":   [0, 1, 2, 3, 4, 5, 6],
},
```

`group2`도 동일하게 수정.

### 4-2. kbs_config.json — signoff 섹션 전체 교체

`signoff.group1.roi_rules`, `signoff.group2.roi_rules` 키를 제거하고 `enter_roi` + `exit_roi` 추가:

```json
"signoff": {
    "signoff_duration": 120.0,
    "recovery_duration": 30.0,
    "auto_preparation": true,
    "prep_alarm_sound": "",
    "enter_alarm_sound": "",
    "release_alarm_sound": "",
    "group1": {
        "name": "Group1",
        "enter_roi": {"video_label": "", "audio_label": ""},
        "exit_roi":  {"video_label": "", "audio_label": ""},
        "start_time": "00:30",
        "end_time": "06:00",
        "every_day": true,
        "weekdays": [0, 1, 2, 3, 4, 5, 6]
    },
    "group2": {
        "name": "Group2",
        "enter_roi": {"video_label": "", "audio_label": ""},
        "exit_roi":  {"video_label": "", "audio_label": ""},
        "start_time": "00:30",
        "end_time": "06:00",
        "every_day": true,
        "weekdays": [0, 1, 2, 3, 4, 5, 6]
    }
}
```

**검증:** 앱 시작 후 `ConfigManager().load()` 결과에 `enter_roi`, `exit_roi` 키 존재 확인

---

## Step 5: 감지영역 선택 UI 2섹션 분리

**파일:** `kbs_monitor/ui/settings_dialog.py`
**대상 클래스:** `_SignoffRoiDialog` (327~475행 부근)

**목적:** 단일 테이블(행 추가/삭제) → 2섹션 고정 1행 레이아웃으로 전면 교체

### 5-1. `_SignoffRoiDialog` 클래스 전체 교체

생성자 인수 변경: `rules: list` → `enter_roi: dict, exit_roi: dict, video_rois, audio_rois`

```python
class _SignoffRoiDialog(QDialog):
    """
    정파 감지영역 선택 다이얼로그 (2섹션 고정 1행).

    섹션 1 [정파준비→정파모드]: 비디오 OR 오디오 (OR 고정, 수정불가)
    섹션 2 [정파모드→정파해제]: 비디오 AND 오디오 (AND 고정, 수정불가)
    """

    def __init__(self, enter_roi: dict, exit_roi: dict,
                 video_rois: list, audio_rois: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("감지영역 선택")
        self.setModal(True)
        self.setMinimumWidth(620)
        self.setMinimumHeight(260)

        self._enter_roi = dict(enter_roi)
        self._exit_roi  = dict(exit_roi)
        self._video_rois_info = list(video_rois)
        self._audio_rois_info = list(audio_rois)

        self._enter_v_combo: QComboBox = None
        self._enter_a_combo: QComboBox = None
        self._exit_v_combo:  QComboBox = None
        self._exit_a_combo:  QComboBox = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── 섹션 1: 정파준비 → 정파모드 ──
        enter_group = QGroupBox(
            "정파준비 → 정파모드  (OR 조건 — 하나라도 감지되면 정파모드 진입)"
        )
        enter_layout = QHBoxLayout(enter_group)
        enter_layout.setSpacing(8)
        enter_layout.addWidget(QLabel("비디오 감지영역:"))
        self._enter_v_combo = self._make_video_combo(
            self._enter_roi.get("video_label", "")
        )
        enter_layout.addWidget(self._enter_v_combo)
        op_enter = QLabel("OR")
        op_enter.setAlignment(Qt.AlignCenter)
        op_enter.setStyleSheet("font-weight: bold; color: #aaa;")
        op_enter.setFixedWidth(30)
        enter_layout.addWidget(op_enter)
        enter_layout.addWidget(QLabel("오디오 감지영역:"))
        self._enter_a_combo = self._make_audio_combo(
            self._enter_roi.get("audio_label", "")
        )
        enter_layout.addWidget(self._enter_a_combo)
        layout.addWidget(enter_group)

        # ── 섹션 2: 정파모드 → 정파해제 ──
        exit_group = QGroupBox(
            "정파모드 → 정파해제  (AND 조건 — 모두 해제되어야 정파해제)"
        )
        exit_layout = QHBoxLayout(exit_group)
        exit_layout.setSpacing(8)
        exit_layout.addWidget(QLabel("비디오 감지영역:"))
        self._exit_v_combo = self._make_video_combo(
            self._exit_roi.get("video_label", "")
        )
        exit_layout.addWidget(self._exit_v_combo)
        op_exit = QLabel("AND")
        op_exit.setAlignment(Qt.AlignCenter)
        op_exit.setStyleSheet("font-weight: bold; color: #aaa;")
        op_exit.setFixedWidth(36)
        exit_layout.addWidget(op_exit)
        exit_layout.addWidget(QLabel("오디오 감지영역:"))
        self._exit_a_combo = self._make_audio_combo(
            self._exit_roi.get("audio_label", "")
        )
        exit_layout.addWidget(self._exit_a_combo)
        layout.addWidget(exit_group)

        # ── 확인 / 취소 ──
        ok_row = QHBoxLayout()
        ok_row.addStretch()
        btn_ok = QPushButton("확인")
        btn_ok.setFixedWidth(80)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _make_video_combo(self, selected: str = "") -> QComboBox:
        combo = QComboBox()
        combo.addItem("없음", userData="")
        for lbl, media in self._video_rois_info:
            display = f"{lbl}  ({media})" if media else lbl
            combo.addItem(display, userData=lbl)
        idx = combo.findData(selected)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    def _make_audio_combo(self, selected: str = "") -> QComboBox:
        combo = QComboBox()
        combo.addItem("없음", userData="")
        for lbl, media in self._audio_rois_info:
            display = f"{lbl}  ({media})" if media else lbl
            combo.addItem(display, userData=lbl)
        idx = combo.findData(selected)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    def get_enter_roi(self) -> dict:
        """정파준비→정파모드 감지영역 반환."""
        return {
            "video_label": self._enter_v_combo.currentData() or "",
            "audio_label": self._enter_a_combo.currentData() or "",
        }

    def get_exit_roi(self) -> dict:
        """정파모드→정파해제 감지영역 반환."""
        return {
            "video_label": self._exit_v_combo.currentData() or "",
            "audio_label": self._exit_a_combo.currentData() or "",
        }
```

**제거 대상 메서드:** `_make_row_widgets()`, `_add_row()`, `_del_row()`, `get_rules()`

**검증:** 감지영역 선택 버튼 클릭 시 2섹션(OR/AND) 다이얼로그 표시 확인

---

## Step 6: 파라미터 저장/로드 수정

**파일:** `kbs_monitor/ui/settings_dialog.py`

### 6-1. `SettingsDialog.__init__` 인스턴스 변수 수정

```python
# OLD
self._signoff_roi_rules: dict = {}       # {gid: list[dict]}

# NEW
self._signoff_enter_roi: dict = {}       # {gid: dict}  정파준비→정파모드
self._signoff_exit_roi: dict  = {}       # {gid: dict}  정파모드→해제
```

### 6-2. `_create_signoff_group_widget()` 내 초기화 코드 수정

```python
# OLD
self._signoff_roi_rules[gid] = []

# NEW
self._signoff_enter_roi[gid] = {"video_label": "", "audio_label": ""}
self._signoff_exit_roi[gid]  = {"video_label": "", "audio_label": ""}
```

### 6-3. `_open_signoff_roi_dialog()` 수정

```python
def _open_signoff_roi_dialog(self, gid: int):
    """감지영역 선택 다이얼로그를 열고 enter_roi, exit_roi를 저장한다."""
    video_rois = [(r.label, r.media_name) for r in self._roi_manager.video_rois]
    audio_rois = [(r.label, r.media_name) for r in self._roi_manager.audio_rois]
    current_enter = self._signoff_enter_roi.get(
        gid, {"video_label": "", "audio_label": ""}
    )
    current_exit = self._signoff_exit_roi.get(
        gid, {"video_label": "", "audio_label": ""}
    )
    dlg = _SignoffRoiDialog(
        current_enter, current_exit, video_rois, audio_rois, parent=self
    )
    if dlg.exec() == QDialog.Accepted:
        self._signoff_enter_roi[gid] = dlg.get_enter_roi()
        self._signoff_exit_roi[gid]  = dlg.get_exit_roi()
        self._update_signoff_roi_summary(gid)
        self._save_signoff_params()
```

### 6-4. `_update_signoff_roi_summary()` 수정

```python
def _update_signoff_roi_summary(self, gid: int):
    """감지영역 선택 요약 텍스트를 갱신한다 (enter + exit 2섹션 표시)."""
    lbl = self._signoff_roi_summary.get(gid)
    if lbl is None:
        return

    def _fmt(roi: dict, op_str: str) -> str:
        v = roi.get("video_label", "")
        a = roi.get("audio_label", "")
        if v and a:
            return f"{v} {op_str} {a}"
        return v or a or ""

    enter_str = _fmt(self._signoff_enter_roi.get(gid, {}), "OR")
    exit_str  = _fmt(self._signoff_exit_roi.get(gid, {}),  "AND")

    parts = []
    if enter_str:
        parts.append(f"진입:{enter_str}")
    if exit_str:
        parts.append(f"해제:{exit_str}")
    lbl.setText(" / ".join(parts) if parts else "선택 없음")
```

### 6-5. `_get_signoff_params()` 수정 (그룹 루프 내 부분)

```python
# OLD
params[f"group{gid}"] = {
    "name":      self._signoff_name_edit[gid].text() or f"Group{gid}",
    "roi_rules": list(self._signoff_roi_rules.get(gid, [])),
    ...
}

# NEW
params[f"group{gid}"] = {
    "name":      self._signoff_name_edit[gid].text() or f"Group{gid}",
    "enter_roi": dict(self._signoff_enter_roi.get(
                     gid, {"video_label": "", "audio_label": ""})),
    "exit_roi":  dict(self._signoff_exit_roi.get(
                     gid, {"video_label": "", "audio_label": ""})),
    "start_time": self._signoff_start_edit[gid].time().toString("HH:mm"),
    "end_time":   self._signoff_end_edit[gid].time().toString("HH:mm"),
    "weekdays": [
        d for d, chk in enumerate(self._signoff_day_chks[gid])
        if chk.isChecked()
    ],
}
```

### 6-6. `_apply_signoff_params_to_ui()` 수정 (그룹 루프 내 roi 로드 부분)

```python
# OLD
roi_rules = grp.get("roi_rules", [])
# ... 구버전 마이그레이션 코드 ...
self._signoff_roi_rules[gid] = roi_rules
self._update_signoff_roi_summary(gid)

# NEW
enter_roi = grp.get("enter_roi", {})
exit_roi  = grp.get("exit_roi",  {})

# 구버전 roi_rules 마이그레이션
if not enter_roi:
    old_rules = grp.get("roi_rules", [])
    if old_rules:
        first = old_rules[0]
        enter_roi = {
            "video_label": first.get("video_label", ""),
            "audio_label": first.get("audio_label", ""),
        }

# 구버전 roi_labels 마이그레이션
if not enter_roi:
    old_labels = grp.get("roi_labels", [])
    if old_labels:
        v_lbl = next((l for l in old_labels if l.startswith("V")), "")
        a_lbl = next((l for l in old_labels if l.startswith("A")), "")
        enter_roi = {"video_label": v_lbl, "audio_label": a_lbl}

if not enter_roi:
    enter_roi = {"video_label": "", "audio_label": ""}
if not exit_roi:
    exit_roi = {"video_label": "", "audio_label": ""}

self._signoff_enter_roi[gid] = enter_roi
self._signoff_exit_roi[gid]  = exit_roi
self._update_signoff_roi_summary(gid)
```

### 6-7. `_reset_signoff_params()` 내 그룹 기본값 수정

```python
# OLD
"group1": {"name": "Group1", "roi_rules": [], ...},
"group2": {"name": "Group2", "roi_rules": [], ...},

# NEW
"group1": {
    "name": "Group1",
    "enter_roi": {"video_label": "", "audio_label": ""},
    "exit_roi":  {"video_label": "", "audio_label": ""},
    ...
},
"group2": {
    "name": "Group2",
    "enter_roi": {"video_label": "", "audio_label": ""},
    "exit_roi":  {"video_label": "", "audio_label": ""},
    ...
},
```

---

## Step 7: 전체 테스트 및 검증

### 기능 검증 목록

| 테스트 항목 | 예상 결과 |
|---|---|
| 앱 시작 (구버전 roi_rules 포함 kbs_config.json) | 마이그레이션으로 enter_roi 자동 변환, 오류 없음 |
| 정파설정 탭 → 감지영역 선택 버튼 클릭 | 2섹션(OR/AND) 다이얼로그 표시 |
| enter_roi=V1, exit_roi=A1 선택 후 확인 | 요약: `진입:V1 / 해제:A1` |
| enter_roi video=V1만 설정, V1 스틸 감지 | PREPARATION → SIGNOFF 진입 |
| exit_roi video=V1 audio=A1, V1 해제 A1 유지 | 해제 카운트 시작 안 됨 (AND 미충족) |
| exit_roi 설정 양쪽 모두 해제 + recovery_duration 경과 | SIGNOFF → PREPARATION 복귀 |
| 정파준비 버튼 클릭 (IDLE) | PREPARATION 전환, 버튼 빨간색 |
| 정파준비 버튼 재클릭 (PREPARATION) | IDLE 전환, 버튼 초기화 |
| SIGNOFF 중 정파준비 버튼 클릭 | IDLE 전환 |
| 설정 저장 → 앱 재시작 → 불러오기 | enter_roi, exit_roi 값 복원 확인 |
| 기본값 초기화 | enter_roi, exit_roi 모두 빈 값 |

### 회귀 검증

- `is_signoff_label("V1")`: enter_roi 또는 exit_roi에 V1 포함 시 True
- `is_any_signoff()`: SIGNOFF 그룹 존재 시 True (변경 없음)
- 블랙/스틸/오디오 알림 억제: SIGNOFF 중 정상 동작 확인

---

## 단계 실행 순서 및 의존성

```
Step 1 (SignoffGroup 구조)
  └─ Step 2 (감지 로직)
      └─ Step 3 (토글 로직 + TopBar 툴팁)

Step 4 (Config 구조) — Step 1~3과 병행 가능

Step 5 (UI 2섹션)
  └─ Step 6 (저장/로드 메서드)

Step 7 (전체 테스트) — 모든 Step 완료 후
```

> **참고:** Step 1~3은 signoff_manager.py 파일 내 순차 의존성이 있으므로 반드시 순서대로 실행한다.
> Step 4, Step 5~6은 독립적으로 진행 가능하다.
