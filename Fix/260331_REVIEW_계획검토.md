# Health Check 시각 인디케이터 구현 계획 사전 검토

**검토일**: 2026-03-31
**에이전트**: eval-plan (review 모드)
**대상 계획서**: Fix/260330_Health Check_시각_계획.md

---

## 검토 요약

| 심각도 | 건수 |
|--------|------|
| Critical | 0 |
| High | 1 |
| Medium | 2 |
| Low | 2 |

---

## 발견 사항

### Critical

없음.

---

### High

| # | 영역 | 계획서 항목 | 현재 코드 | 설명 |
|---|------|-----------|-----------|------|
| H-1 | main_window.py `_update_summary` | `detect_stale` 오탐 방지 조건에 `not self._detection_enabled` 포함 | `_run_detection` 진입부(line 257~261): `_roi_overlay is not None`이면 early return, `_detect_timer.stop()` 시 `_run_detection` 자체가 호출 안 됨 | **감지 OFF 시 `_detect_timer.stop()` 호출 (line 1117)되고 `_detection_enabled=False`(line 1112)**이므로 이 조건은 필수이며 올바르다. 문제 없음. 단, 계획서가 "감지 OFF(`_detection_enabled=False`, 타이머 stop)" 설명에서 두 조건을 동일 케이스로 묶었는데, 실제로는 ROI 편집 중 `_detect_timer.stop()` + `_detection_enabled=True` 조합이 존재한다(line 784). 이 케이스는 `_roi_overlay is not None` 조건으로 커버되지만 계획서 설명이 불완전하여 구현자 혼란 가능성이 있다. 로직 자체는 올바르게 작성되어 있어 그대로 구현하면 동작한다. |

---

### Medium

| # | 영역 | 계획서 항목 | 현재 코드 | 설명 |
|---|------|-----------|-----------|------|
| M-1 | top_bar.py `_create_health_indicator` | `container.setFixedWidth(80)` + `setWordWrap(True)` + 2줄 텍스트 ("감지 중단\n영상 중단") | `TopBar.setFixedHeight(68)` (line 274), `layout.setContentsMargins(10, 6, 10, 4)` (line 277), `layout.setAlignment(Qt.AlignTop)` (line 279) | TopBar 고정 높이 68px에서 상하 마진(6+4=10px)을 빼면 유효 높이 58px. `_create_health_indicator`의 컨테이너는 VBoxLayout + 상하마진 2px + 라벨 2개 줄(`감지 중단\n영상 중단`, font size 9, Bold). wordWrap=True로 두 줄이 모두 표시될 경우 약 36~40px 이상 필요. 이론적으로 68px 내에 맞지만 **`alignment=Qt.AlignVCenter`** 추가(계획서 `layout.addWidget(self._health_indicator, alignment=Qt.AlignVCenter)`)와 컨테이너 `setFixedHeight` 미설정으로 인해 위젯 높이가 레이아웃에 의해 과다 확장될 수 있다. 다른 위젯들은 `alignment=Qt.AlignTop`으로 배치되는데 health_indicator만 `Qt.AlignVCenter` → 레이아웃이 주변 항목과 수직 정렬 기준이 달라져 UI 어색함 발생 가능. |
| M-2 | main_window.py `_update_summary` | 삽입 위치 "line 560, 메서드 끝부분 `except` 전에 추가" | `_update_summary`는 line 560에서 시작하며 전체 메서드가 try-except로 래핑(line 561: `try:`, line 585~586: `except Exception as e`). 계획서 코드는 try 블록 **내부** 끝에 삽입 | Health check 코드도 `_update_summary`의 `try` 블록 내에 위치하게 되어 **health check 코드 자체에서 예외 발생 시 silent fail** 이 된다. 실제 `update_health()` 호출 중 예외가 발생하면 로그만 남고 다음 1초 tick에서 정상 작동하므로 치명적 문제는 아니다. 그러나 `_top_bar.update_health(detect_stale, frame_stale)` 호출이 실패해도 `_health_alarm_logged` 상태가 업데이트되어 이후 로그 기록이 왜곡될 수 있다. 계획서는 이 점을 언급하지 않음. |

---

### Low

| # | 영역 | 계획서 항목 | 현재 코드 | 설명 |
|---|------|-----------|-----------|------|
| L-1 | top_bar.py `_create_health_indicator` 삽입 위치 | "line 283, SysMonitorWidget 뒤, separator 앞에 삽입" | line 283: `layout.addWidget(self._sys_monitor, alignment=Qt.AlignTop)`, line 285: `layout.addWidget(self._make_separator())` | 삽입 후 레이아웃 순서: `_sys_monitor → health_indicator → separator → time → ...`. SysMonitor 바로 옆에 health indicator가 위치하게 된다. 논리적으로 이상하지 않으나 separator 없이 SysMonitor와 health indicator가 바로 붙어 있어 시각적으로 경계가 불명확할 수 있다. 기능적 문제는 없으나 separator 배치 재검토 여지가 있다. |
| L-2 | main_window.py `__init__` 변수 초기화 위치 | "line 101 근처, `_detection_count` 옆" | 실제 line 101: `self._detection_count: int = 0`. `_start_threads()`는 line 105 이후 `_setup_ui()` → `_connect_signals()` → `_start_threads()` 순으로 호출됨 | `self._last_detection_time = time.time()`, `self._last_frame_time = time.time()` 초기화는 `__init__` 초반(line 101 근처)에 이루어진다. `_start_threads()`에서 `_detect_timer.start()`(line 235)와 `_summary_timer.start()`(line 240)가 시작되는데, 두 타이머가 시작되기 전에 초기화가 완료되므로 경쟁 조건 없음. 단, 계획서에 `_start_threads()` 호출 순서에 대한 언급이 없어 구현자가 위치를 잘못 판단할 경우(예: `_start_threads()` 이후 초기화) 1~2ms 이내 false alarm 발생 가능성이 있으므로 설명 보완 권장. |

---

## 영향 범위 누락 목록

| # | 파일 | 위치 | 영향 내용 | 대응 필요 여부 |
|---|------|------|-----------|---------------|
| 1 | top_bar.py | `update_health()` | 이 메서드가 새로 추가되므로 외부 호출부(`main_window.py`)와 서명이 일치해야 한다. 계획서에서 두 파일 모두 명시하고 있어 직접 영향은 없음. | 불필요 |
| 2 | top_bar.py | `_setup_ui()` 내 separator 배치 | health_indicator 삽입으로 기존 separator가 `_sys_monitor`와 `time_container` 사이가 아닌 `health_indicator`와 `time_container` 사이로 이동. separator를 추가하지 않으면 SysMonitor와 health_indicator 사이의 시각적 구분이 없음 | 선택적 보완 (separator 추가 고려) |
| 3 | dark_theme.qss / light_theme.qss | `#healthIndicator` objectName 스타일 | `container.setObjectName("healthIndicator")`로 지정되어 있으나 QSS 파일에 해당 ID 스타일이 없어도 인라인 스타일로 동작함. 다크/라이트 모드 전환 시 `update_health()`의 인라인 `setStyleSheet`가 모드와 무관하게 항상 `#cc0000` 배경을 사용하므로 라이트 모드에서도 색상은 동일하게 적용됨. 의도적이면 문제 없음. | 불필요 (의도적 동작) |

---

## 권장 수정사항

### 계획서 수정 필요 (Critical/High)

1. **H-1 보완**: 계획서의 오탐 방지 설명에서 "ROI 편집 중(`_roi_overlay is not None`, 감지 의도적 중단) → `detect_stale = False`" 설명을 더 명확히 보완 권장. `_start_halfscreen_edit`(line 784)에서 `_detect_timer.stop()`이 호출되지만 `_detection_enabled`는 여전히 `True`인 상태임을 명시할 것. 구현 코드 자체는 올바르게 작성되어 있으므로 그대로 구현해도 동작함.

### 계획서 보완 권장 (Medium/Low)

1. **M-1 보완**: `_create_health_indicator` 내 컨테이너에 `setFixedHeight` 추가를 검토하거나, `layout.addWidget(self._health_indicator, alignment=Qt.AlignVCenter)` 대신 다른 위젯들과 동일하게 `alignment=Qt.AlignTop`으로 변경 고려. 또는 health indicator 삽입 위치를 기존 separator 뒤로 이동하여 `_sys_monitor` 영역과 분리.

2. **M-2 보완**: `_update_summary`의 try 블록 내에 health check 코드가 포함되므로, `_top_bar.update_health()`에서 예외 발생 시 `_health_alarm_logged` 상태 불일치가 발생할 수 있다는 점 인지 필요. 실용적 해결 방안: health check 코드 블록을 별도 `try-except`로 감싸거나, `update_health()` 내부에서 예외를 처리.

3. **L-2 보완**: 계획서에 "세 변수를 `_start_threads()` 호출 이전에 초기화"라는 명시 추가 권장.

---

## 종합 판정

- **구현 가능 여부**: 가능 (Critical 없음, 수정 없이 그대로 구현 가능)
- **Critical 해소 필요**: 없음
- **특별 주의사항**:
  - `_update_summary` 내 삽입 위치가 기존 try 블록 내부임을 확인하고 구현할 것 (라인 단순 삽입 시 들여쓰기 8칸 필요)
  - `_setup_ui` 삽입 후 레이아웃 결과(`_sys_monitor → health_indicator → separator`)가 시각적으로 자연스러운지 실행하여 확인 필요
  - 계획서 변경량 표기("기존 코드 수정 없음, 추가만")는 정확함. `_on_frame_ready` 및 `_run_detection`에 한 줄씩 타임스탬프 갱신 코드가 추가되는 것 포함.
