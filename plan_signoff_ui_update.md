# 계획: 상단바 정파 메시지 + 감지영역 다이얼로그 수정

## Context

상단바 정파 패널이 현재 3줄 구조 (상태명 / 시간 / 설정 시간대)로 운영되고 있으나,
'정파준비 전(IDLE)' 개념을 표시하지 않고 정파준비/정파중일 때만 2줄로 표시하는 방식으로 단순화 요청.
또한 감지영역 다이얼로그의 경고 조건과 요약 표시 형식도 개선 필요.

---

## 수정 파일 목록

| 파일 | 수정 내용 |
|------|----------|
| `kbs_monitor/ui/top_bar.py` | 패널 2줄화, 테두리 상태 표시, HTML 강조 |
| `kbs_monitor/ui/settings_dialog.py` | 경고 조건 수정, 요약 줄바꿈+불릿 |
| `kbs_monitor/ui/main_window.py` | `update_signoff_state()` 파라미터 정리 |

---

## 1. `top_bar.py` 수정

### 1-1. 패널 위젯 초기화 (3줄 → 2줄)

`_s_line3` dict 제거, `_s_panel` dict 추가 (테두리 업데이트용).

```python
self._s_panel: dict[int, QWidget] = {}   # 추가
self._s_line1: dict[int, QLabel] = {}
self._s_line2: dict[int, QLabel] = {}
# _s_line3 제거
```

패널 생성 루프(`for gid in (1, 2):`) 변경:
- `panel = QWidget()` → `panel.setObjectName(f"signoff_panel_{gid}")`
- 초기 스타일: `border: 2px solid #555555; border-radius: 4px; padding: 2px;`
- `line1` (그룹명, Bold 9pt), `line2` (상태+시간, 8pt) 2개만 생성
- `line2.setTextFormat(Qt.RichText)` 추가 (HTML 강조 표시용)
- `self._s_panel[gid] = panel` 저장

### 1-2. `update_signoff_state()` 메서드 시그니처 변경

```python
# 변경 전
def update_signoff_state(self, group_id, state, group_name, elapsed_seconds,
                         start_time, end_time, has_schedule):

# 변경 후
def update_signoff_state(self, group_id, state, group_name, elapsed_seconds):
```

### 1-3. `update_signoff_state()` 동작 로직

**진행시간 포맷 헬퍼 (메서드 내부):**
```python
def _fmt_elapsed(secs: float) -> str:
    s = int(abs(secs))
    return f"{s//3600}시간 {(s%3600)//60}분 {s%60}초"
```

**상태별 처리:**

| state | line1 | line2 (HTML) | 테두리 색 |
|-------|-------|--------------|----------|
| IDLE | 그룹명 | `""` (빈 줄) | `#555555` |
| PREPARATION | 그룹명 | `<b>[정파준비]</b> Nh Nm Ns (진행시간)` | `#e07b00` |
| SIGNOFF | 그룹명 | `<b>[정파 중]</b> Nh Nm Ns (진행시간)` | `#cc0000` |

**테두리 업데이트:**
```python
border_map = {"IDLE": "#555555", "PREPARATION": "#e07b00", "SIGNOFF": "#cc0000"}
color = border_map.get(state, "#555555")
panel.setStyleSheet(
    f"border: 2px solid {color}; border-radius: 4px; padding: 2px;"
)
```

---

## 2. `main_window.py` 수정

`update_signoff_state()` 호출부 두 곳(라인 365, 771) 모두 수정:

```python
# 변경 전
self._top_bar.update_signoff_state(
    gid, state.value, group.name, elapsed,
    group.start_time, group.end_time, has_schedule
)

# 변경 후
self._top_bar.update_signoff_state(gid, state.value, group.name, elapsed)
```

- `has_schedule = self._signoff_manager.has_schedule_in_window(gid)` 줄도 제거 (불필요)

---

## 3. `settings_dialog.py` 수정

### 3-1. `_SignoffRoiDialog._on_ok_clicked()` - 경고 조건 변경

```python
# 변경 전 (둘 다 없음이어야 경고)
if not exit_v and not exit_a:

# 변경 후 (하나라도 없으면 경고)
if not exit_v or not exit_a:
```

경고 메시지 수정:
```python
QMessageBox.warning(
    self, "감지영역 미선택",
    "정파모드 → 정파해제 조건에\n"
    "비디오와 오디오 레벨미터 감지영역을\n"
    "모두 선택해야 합니다."
)
```

### 3-2. `_update_signoff_roi_summary()` - 줄바꿈 + 불릿 기호

QLabel에 `wordWrap(True)` + 줄바꿈 텍스트 사용:

```python
def _update_signoff_roi_summary(self, gid: int):
    lbl = self._signoff_roi_summary.get(gid)
    if lbl is None:
        return

    def _fmt(roi, op_str):
        v = roi.get("video_label", "")
        a = roi.get("audio_label", "")
        if v and a:
            return f"{v} {op_str} {a}"
        return v or a or ""

    enter_str = _fmt(self._signoff_enter_roi.get(gid, {}), "OR")
    exit_str  = _fmt(self._signoff_exit_roi.get(gid, {}),  "AND")

    parts = []
    if enter_str:
        parts.append(f"• 진입: {enter_str}")
    if exit_str:
        parts.append(f"• 해제: {exit_str}")

    lbl.setWordWrap(True)
    lbl.setText("\n".join(parts) if parts else "선택 없음")
```

---

## 검증 방법

1. **정파 패널 표시 확인**
   - IDLE 상태: 그룹명만 표시, 두 번째 줄 비어있음, 회색 테두리
   - PREPARATION: `[정파준비]` 굵게 표시 + 경과 시간, 주황색 테두리
   - SIGNOFF: `[정파 중]` 굵게 표시 + 경과 시간, 빨간색 테두리

2. **감지영역 경고 확인**
   - 비디오만 선택하고 오디오 "없음" → 경고 팝업 발생 확인
   - 오디오만 선택하고 비디오 "없음" → 경고 팝업 발생 확인
   - 둘 다 선택 → 정상 확인

3. **요약 텍스트 확인**
   - 감지영역 선택 후 설정 탭에서 불릿 기호 + 줄바꿈으로 표시되는지 확인

4. **실행 오류 없음 확인**
   - `python -m kbs_monitor.main` 실행 후 에러 로그 없는지 확인
