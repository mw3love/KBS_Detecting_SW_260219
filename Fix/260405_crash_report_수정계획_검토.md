# 먹통 원인 미포착 — crash_report 저장 + eval-freeze 페르소나 보강 수정계획 사전 검토

**검토일**: 2026-04-05
**에이전트**: eval-plan (review 모드)
**대상 계획서**: `Fix/260405_crash_report_수정계획.md`

---

## 검토 요약

| 심각도 | 건수 |
|--------|------|
| Critical | 0 |
| High | 3 |
| Medium | 3 |
| Low | 2 |

---

## 발견 사항

### Critical

발견 사항 없음.

---

### High

| # | 영역 | 계획서 항목 | 현재 코드 | 설명 |
|---|------|-----------|-----------|------|
| H1 | B | Phase 1 — `_save_crash_report`에서 `AlarmSoundThread alive` 조회 | `alarm.py` `_sound_thread` 필드, 스레드명 없음 | 계획서 스냅샷 명세에 "AlarmSoundThread: alive=True/False"라고 기재했지만, `alarm.py`의 사운드 스레드는 `threading.Thread(daemon=True)` 생성 시 `name` 인자를 지정하지 않는다. 실제 속성명은 `self._alarm._sound_thread`이고, 계획서의 "AlarmSoundThread"는 존재하지 않는 스레드 이름이다. 구현 시 `self._alarm._sound_thread` 직접 참조 후 `is_alive()` 호출 코드로 작성해야 한다. 단, `_sound_thread`는 비공개 멤버이므로 `alarm.py`에 `sound_thread_alive()` 공개 메서드를 추가하는 방향이 더 안전하다. |
| H2 | B | Phase 1 — crash_report 저장 경로 `kbs_monitor/logs/crash_YYMMDD_HHMMSS.txt` | `logger.py` `LOG_DIR` = `os.path.join(_BASE_DIR, "logs")` | `logger.py`에서 `_BASE_DIR`은 `kbs_monitor` 상위 디렉터리(프로젝트 루트)이고, `LOG_DIR`은 `프로젝트루트/logs`이다. 계획서가 기재한 `kbs_monitor/logs/`는 `kbs_monitor/` 하위에 `logs/`가 있는 경로로 읽힐 수 있으나, 실제 로그 위치는 프로젝트 루트의 `logs/` 폴더다. 구현 시 `AppLogger.LOG_DIR`을 재활용하거나 동일한 `_BASE_DIR` 계산 로직(`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`)을 사용해야 한다. 하드코딩된 상대경로(`"kbs_monitor/logs/"`)를 사용하면 실행 경로에 따라 파일 생성 위치가 달라진다. |
| H3 | C | Phase 1 — `_do_scheduled_restart()`에서 crash_report 저장 위치 | `main_window.py` L1291 `subprocess.Popen()` 직전 | 계획서는 "로그 메시지 직후, `subprocess.Popen()` 직전에 저장"이라고 기재했다. 그런데 `_do_scheduled_restart()`는 예약 재시작(정상 스케줄 재시작) 전용 메서드다. 이 경우는 먹통이 아니라 정상 운영 중 재시작이므로, `scheduled_restart` 트리거의 crash_report가 실제 먹통 상황과 혼동될 수 있다. 예약 재시작이 빈번해지면 crash 파일이 불필요하게 축적된다. 계획서에서 `_do_scheduled_restart()`에 crash_report를 저장하는 의도와 기대 효과를 더 명확히 정의해야 한다. (단, `_do_scheduled_restart()`는 health check 이상 감지와 독립적이므로 어느 시점에 재시작됐는지 기록 자체는 유용할 수 있다.) |

---

### Medium

| # | 영역 | 계획서 항목 | 현재 코드 | 설명 |
|---|------|-----------|-----------|------|
| M1 | E | Phase 1 — `_save_crash_report()`에서 `DetectionState` 접근 방식 미정 | `main_window.py` L305~329 heartbeat 로그 참조 패턴 | 계획서는 "DetectionState 접근: `self._detector` 통해 내부 상태 직접 접근 또는 summary 메서드 추가"라고 양자택일로 남겨뒀다. 이미 heartbeat 로그(L305~329)에서 `self._detector._still_states`, `self._detector._last_raw` 직접 접근 패턴이 사용되고 있으므로, 일관성상 동일한 방식을 사용할 수 있다. 그러나 계획서 명세의 "V채널" 출력 범위("V1~V8, V5~V8 포함")가 실제 ROI label 체계와 맞는지 보장되지 않는다. `_still_states`는 label 기반 dict이므로 고정 V1~V8 인덱스가 아니라 `_still_states.items()` 전체를 순회해야 한다. |
| M2 | E | Phase 1 — `_health_alarm_logged` 첫 전환 시점의 crash_report 중복 방지 | `main_window.py` L621 `if health_abnormal and not self._health_alarm_logged:` | crash_report 저장 조건이 `_health_alarm_logged = False → True` 전환 시점 1회만이라면 이후 같은 이상 상태에서는 저장되지 않는다. 먹통이 정상 복구 없이 지속되면 1개의 crash 파일만 생성된다. 이는 의도된 동작이지만, 명세에서 명시적으로 "1회만 저장"임을 확인해야 한다. 또한 정상 복구 후 `_health_alarm_logged = False`로 리셋(L638)되므로, 동일 먹통 세션에서 1개 파일이 생성되는 것은 충분하다. 특별한 문제는 아니나 명세에 명시가 없어 검토 사항으로 기재한다. |
| M3 | D | Phase 1 — `open()` 직접 쓰기에서 파일 플러시/닫기 보장 | 계획서 명세 "open(path, 'w', encoding='utf-8') 직접 쓰기" | 계획서가 "logger 우회 — 먹통 상태에서도 동작 보장"으로 직접 쓰기를 선택한 근거는 타당하다. 단, `with open(...) as f:` 컨텍스트 매니저를 사용하지 않고 `open()` + `write()` + `close()` 수동 패턴을 사용할 경우, 예외 발생 시 파일이 닫히지 않을 수 있다. 계획서에서 `with open(...)` 패턴 사용 여부를 명시하거나, try-except 보호 내에 포함한다고 명시해야 한다. (계획서는 try-except 보호를 언급했으나, 파일 닫기 보장 방식은 기재 없음.) |

---

### Low

| # | 영역 | 계획서 항목 | 현재 코드 | 설명 |
|---|------|-----------|-----------|------|
| L1 | B | Phase 1 명세 — `[DetectionState — A채널]` 출력 항목 | `_audio_level_states`는 `Detector`의 dict | 명세에 "A채널 (오디오레벨미터 상태), A5~A6 포함"으로 기재됐는데, audio ROI label 체계가 실제로 A1~A8인지, A5~A6만 있는지 코드로 보장되지 않는다. 구현 시 `_audio_level_states.items()` 전체 순회 방식을 사용하면 label 체계에 무관하게 동작한다. |
| L2 | A | Phase 2 — eval-freeze.md 페르소나 삽입 위치 "기존 `## 핵심 임무` 섹션 앞에 삽입" | `eval-freeze.md` L8 `## 핵심 임무` | 현재 `eval-freeze.md`는 L6에 "당신은 ... 에이전트입니다." 단락이 있고, L8에 `## 핵심 임무`가 온다. 계획서가 "description 다음, 핵심 임무 앞에 삽입"이라고 지시하므로 위치는 명확하다. 단, 삽입 후 "당신은 ... 에이전트입니다." 단락과 새 페르소나 블록이 연달아 나와 역할 정의가 두 번 반복될 수 있다. 두 단락을 합치거나 순서를 정리하면 가독성이 향상된다. |

---

## 영향 범위 누락 목록

계획서에 명시되지 않았지만 수정의 영향을 받는 코드:

| # | 파일 | 위치 | 영향 내용 | 대응 필요 여부 |
|---|------|------|-----------|---------------|
| 1 | `kbs_monitor/core/alarm.py` | `_sound_thread` 필드 (L46, L164, L240, L251) | `_save_crash_report()`에서 사운드 스레드 생존 여부 조회 시 비공개 멤버 직접 접근 필요. 공개 메서드 추가 또는 직접 접근 방식 선택이 필요하지만 계획서에 미언급 | 선택 필요 (Low~Medium) |
| 2 | `kbs_monitor/utils/logger.py` | `AppLogger.LOG_DIR` (L21) | crash_report 파일을 일반 로그와 동일 폴더(`logs/`)에 저장하려면 `LOG_DIR` 상수 재활용이 일관성상 최선. 계획서는 별도 경로 계산을 암시 | 권장 (Low) |

---

## 권장 수정사항

### 계획서 수정 필요 (High)

1. **H1 — AlarmSoundThread 이름 오류**: 계획서 명세의 "AlarmSoundThread: alive=True/False" 항목을 실제 접근 방식에 맞게 수정하라. `self._alarm._sound_thread.is_alive()` 직접 접근 또는 `alarm.py`에 `is_sound_alive() -> bool` 공개 메서드 추가 후 호출하는 방향을 명시하라.

2. **H2 — logs 경로 명세 수정**: 계획서의 `kbs_monitor/logs/crash_YYMMDD_HHMMSS.txt` 표현을 `AppLogger.LOG_DIR` 재활용 또는 `_BASE_DIR` 기반 절대경로 계산으로 명시하라. 하드코딩된 상대경로 사용 금지 사항을 명세에 추가하라.

3. **H3 — `_do_scheduled_restart()` crash_report 저장 의도 명확화**: 이 메서드가 정상 재시작(먹통 아님)임에도 crash_report를 저장하는 이유와 기대 효과를 한 줄이라도 명시하라. "재시작 직전 상태 스냅샷 — 반드시 먹통 상황이 아닐 수 있음" 주석을 코드에 삽입하도록 계획서에 추가하라.

### 계획서 보완 권장 (Medium/Low)

1. **M1 — DetectionState 접근 방식 확정**: 현재 heartbeat 로그와 동일하게 `self._detector._still_states.items()` 전체 순회 방식을 사용하도록 명세에 확정 기재하라. "V1~V8" 같은 하드코딩 인덱스가 아닌 dict 전체 순회임을 명시하라.

2. **M3 — 파일 쓰기 패턴 명시**: `with open(path, 'w', encoding='utf-8') as f:` 컨텍스트 매니저 사용을 명세에 추가하여 파일 닫기 보장을 명시하라.

3. **L2 — eval-freeze.md 중복 서두 정리**: Phase 2 삽입 시 기존 "당신은 ... 에이전트입니다." 단락과 새 페르소나 블록의 관계를 명확히 하라. 기존 단락을 유지하면서 새 블록을 그 뒤에 삽입하거나, 두 단락을 합쳐 하나로 정리하는 방향 중 하나를 선택해 명시하라.

---

## 종합 판정

- **구현 가능 여부**: 수정 후 가능
- **Critical 해소 필요**: 없음 (Critical 0건)
- **특별 주의사항**:
  - High 3건 모두 구현 세부사항에 대한 것이며, 계획서 자체가 재설계를 요구하는 수준은 아니다. 구현 전에 `AlarmSoundThread` 명칭 오류(H1)와 로그 경로 계산(H2)만 확정하면 Phase 1 구현이 가능하다.
  - `_save_crash_report()`가 `_update_summary()` 내부의 health check try-except 블록 안에 위치해야 한다. 이 메서드 자체가 예외를 던지면 health check 전체가 중단될 수 있으므로, 계획서에서 언급한 대로 반드시 별도 try-except로 보호해야 한다.
  - Phase 2(eval-freeze 페르소나 보강)는 코드 수정이 아닌 에이전트 문서 수정이므로 부작용 위험이 없다.
