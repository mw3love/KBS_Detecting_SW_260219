# DIAG-AUDIO `_embedded_alert_start` None 연산 오류 수정계획

## 문제 현상
- 장기 실행(20시간+) 도중 DIAG-AUDIO 섹션에서 TypeError 발생
- 발생 이후 프로그램 재시작 전까지 DIAG-AUDIO 정상 출력 완전 소실 (`_diag_last_errors` 고착)
- 감지 자체는 계속 동작 (DIAG-AUDIO 섹션이 독립 try-except로 격리되어 있으므로)
- 재현 조건: 간헐적 — 임베디드 오디오 무음 감지 후 정상 복구 사이클이 발생할 때

## ⚠️ 원인 불확실성 (중요)
- 이 수정계획은 **추정 원인에 기반한 예방적 방어**이며, 근본 원인이 100% 확정되지 않음
- `is not None` 체크가 이미 존재하는데도 TypeError가 발생한 점은 이론적으로 설명이 어려움
- 로그는 이 프로젝트와 유사하지만 다른 컴퓨터의 프로그램에서 수집된 것 (v1.6.15 현장 배포본)
- **수정 후에도 이 추론이 틀릴 가능성이 있음** — Phase 2 검증에서 재발 시 원인 재분석 필요

## 로그 분석 결과
- **20260408**: 정상 운영. SYSTEM-HB 최대 간격 35s, DIAG-AUDIO 정상 출력 지속.
- **20260409 06:54:50**: DIAG-AUDIO 오류 최초 발생 (TypeError: float - NoneType)
  - 이후 30초마다 "오류 반복" 메시지 출력 (총 2,077건)
  - 오류 발생 이후에도 V1~V8 DIAG·SIGNOFF는 정상 출력 (감지 미중단 확인)
- **20260410 00:30:05 ~ 08:18**: 프로그램 재시작 없이 오류 반복 지속 (923건)
  - DIAG-AUDIO 정상 라인 0건 — 하루 종일 소실
- **상세 분석 파일**: `Fix/260410_bug_log/analyze_log.py` (3일치 로그 분석 스크립트)

## 원인 분석

### 1순위: TOCTOU — `_embedded_alert_start` 속성을 두 번 읽는 구조 (추정)
- **파일**: `kbs_monitor/ui/main_window.py` L459, `_run_detection()` DIAG-AUDIO 블록
- **traceback 원문**:
  ```
  File "...\KBS_Detecting_SW_260219-main_v1.6.15\kbs_monitor\ui\main_window.py", line 459
      (time.time() - self._detector._embedded_alert_start)
  TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'
  ```
- **핵심 문제**: 현재 코드가 속성을 두 번 읽는 구조
  ```python
  # 체크 시점에 한 번 읽고
  if self._detector._embedded_alert_start is not None:
      # 연산 시점에 또 한 번 읽음 — 이 사이에 None이 될 수 있음
      elapsed = time.time() - self._detector._embedded_alert_start  # ← TypeError 발생 위치
  ```
- **발생 시나리오 (추정)**:
  1. DIAG 사이클(30초)에서 `is not None` 체크 → True
  2. 체크 직후 `_on_audio_level_for_silence()` → `reset_embedded_silence()` → `_embedded_alert_start = None`
  3. 연산 실행 시점에는 이미 None → TypeError
- **불확실한 점**: Qt 단일 이벤트 루프에서는 이 타이밍이 이론상 불가능. 정확한 발생 메커니즘 미확정.

### 2순위: `_diag_last_errors` 오류 고착화 — 정상 복구 시 클리어 누락
- **파일**: `kbs_monitor/ui/main_window.py`, `_run_detection()` 각 DIAG 섹션 try 블록
- **현상**: 오류 발생 시 `_diag_last_errors["DIAG-AUDIO"] = "TypeError"` 저장
  → try 블록이 정상 완료되어도 클리어 코드 없음
  → 오류가 해소된 뒤에도 재시작 전까지 이전 오류 상태 잔류
- **영향**: 같은 섹션에서 새 오류 발생 시 첫 traceback이 출력되지 않을 수 있음 (진단 품질 저하)

## 수정 대상 파일

| 파일 | 수정 위치 | 내용 |
|------|-----------|------|
| `kbs_monitor/ui/main_window.py` | DIAG-AUDIO 블록 (L459) | `_embedded_alert_start` 로컬 변수 캡처 — TOCTOU 방어 |
| `kbs_monitor/ui/main_window.py` | `_run_detection()` 6개 DIAG 섹션 | `self._diag_last_errors.pop("섹션명", None)` 추가 — 정상 실행 시 오류 상태 클리어 |

## Phase 실행 프로토콜

> **이 계획서를 열고 Phase를 구현하는 Claude는 아래 프로토콜을 반드시 따른다.**
>
> 1. 각 Phase의 코드 수정 및 커밋이 완료되면 (Phase당 커밋 1개 원칙)
> 2. **Agent 도구로 eval-plan 에이전트를 포그라운드로 즉시 실행한다**
>    - 호출 방법: `Agent(subagent_type="eval-plan", prompt="evaluate HEAD~1")`
> 3. **eval-freeze 에이전트도 함께 실행한다** (감지 루프/스레드/타이머 관련 수정이므로 필수)
>    - 호출 방법: `Agent(subagent_type="eval-freeze", prompt="evaluate HEAD~1")`
> 4. 평가 결과를 사용자에게 보고한 후 다음 Phase 진행 여부를 확인한다
> 5. Critical/High 발견 시 — 다음 Phase 진행 전에 수정 여부를 사용자에게 확인한다
> 6. 각 작업 항목 완료 즉시 체크박스를 `[x]`로 업데이트한다 — 중단 후 재개 시 진행 상태 파악용

## 단계별 수정 계획

> 각 Phase는 새로운 대화세션에서 시작한다.
> 중단되어도 다음 세션에서 이 파일을 열어 이어서 진행 가능.

### Phase 1 — TOCTOU 방어 + `_diag_last_errors` 클리어 추가

**수정 1: `_embedded_alert_start` 로컬 캡처 (TOCTOU 방어)**
- [x] `main_window.py` DIAG-AUDIO 블록 (L456~466) 수정
  ```python
  # Before
  silence_elapsed = (
      (time.time() - self._detector._embedded_alert_start)
      if self._detector._embedded_alert_start is not None
      else 0.0
  )

  # After — 속성을 한 번만 읽어 로컬 변수에 저장
  _emb_start = self._detector._embedded_alert_start
  silence_elapsed = (time.time() - _emb_start) if _emb_start is not None else 0.0
  ```

**수정 2: 각 DIAG 섹션 정상 완료 시 `_diag_last_errors` 클리어**
- [x] 6개 섹션 각각 try 블록 **마지막 줄** (if/for 분기 바깥)에 `pop()` 추가
  - SYSTEM-HB: `_log.info(...)` 직후
  - DIAG-V: for 루프 **바깥**, try 마지막
  - DIAG-ALARM: `if active_alarms:` 블록 **바깥**, try 마지막
  - DIAG-SIGNOFF: `_log.info(...)` 직후
  - DIAG-AUDIO: `_log.info(...)` 직후
  - DIAG-TELEGRAM: `if tg_enabled and ...:` 블록 **바깥**, try 마지막
  ```python
  # 올바른 위치 예시 (DIAG-ALARM)
  try:
      active_alarms = self._alarm._active_alarms
      if active_alarms:
          ...
          _log.info("DIAG-ALARM - 활성: ...")
      self._diag_last_errors.pop("DIAG-ALARM", None)  # ← if 바깥, try 마지막
  except Exception as _e:
      ...
  ```
  > **주의**: `pop()` 후 동일 오류가 재발하면 traceback이 다시 출력됨 (의도된 동작).
  > 간헐적 오류가 30초 주기로 반복되는 경우 traceback 반복 출력 가능. 허용 범위 확인 필요.

- [x] 커밋: `fix: DIAG TOCTOU 방어 + 오류 상태 클리어 추가`
- [x] eval-plan evaluate HEAD~1 자동 실행 (Agent 도구, 포그라운드)
- [x] eval-freeze evaluate HEAD~1 자동 실행 (Agent 도구, 포그라운드)

### Phase 2 — 검증
- [x] 현장 테스트 (24시간+ 운영)
- [x] **재발 확인 (2026-04-12)**: v1.6.19에서 동일 TypeError 재현 (로컬 캡처 적용 후에도)
  - Phase 1 추론(TOCTOU) 이 틀렸을 가능성 확정 → Phase 3 추가 방어로 전환

### Phase 3 — 타입 방어 강화 + Watchdog 강화 (2026-04-12)

**배경**: Phase 1 수정(로컬 캡처) 후에도 v1.6.19에서 동일 TypeError 재발.
에이전트 분석 결과 `is not None` 후에도 TypeError가 발생하는 정확한 메커니즘 미확정.
근본 원인을 확정할 수 없으므로 실용적 방어 코드로 접근.

**수정 1: DIAG-AUDIO 타입 방어 강화**
- [x] `main_window.py` DIAG-AUDIO 블록 L462 이하 수정
  ```python
  # After — 비-float 타입 감지 시 실제 타입/값 로그 출력 후 None으로 강제
  _emb_start = self._detector._embedded_alert_start
  if _emb_start is not None and not isinstance(_emb_start, (int, float)):
      _log.error("DIAG-AUDIO _emb_start 타입 이상: %r (type=%s) — None으로 강제", ...)
      _emb_start = None
  silence_elapsed = (time.time() - _emb_start) if _emb_start is not None else 0.0
  ```
  - 효과: TypeError 완전 차단. 재발 시 실제 타입/값이 로그에 기록되어 근본 원인 확정 가능.

**수정 2: Watchdog 로그 강화 + 텔레그램 알림**
- [x] `main_window.py` Health Check 섹션 수정
  - 로그에 `detect_timer`, `latest_frame`, `py_threads` 추가
  - 감지 루프 중단 감지 시 텔레그램 즉시 발송 (사용자가 즉시 인지 → 로그 수집 가능)

- [ ] 커밋: `fix: DIAG-AUDIO 타입 방어 강화 + watchdog 텔레그램 알림 추가`
- [ ] eval-plan evaluate HEAD~1 자동 실행
- [ ] eval-freeze evaluate HEAD~1 자동 실행

### Phase 4 — 검증
- [ ] 현장 테스트 (24시간+ 운영)
- [ ] DIAG-AUDIO TypeError 미발생 확인
- [ ] 감지 루프 중단 시 텔레그램 수신 확인
- [ ] **재발 시**: `_emb_start 타입 이상` 로그 내용으로 근본 원인 확정

## 참고
- **관련 설계 원칙**: `kbs_monitor/core/CLAUDE.md` — 감지 루프 안정성 원칙, `_diag_last_errors` 설계
- **관련 이력**:
  - 2026-04-04 (v1.6.12): DIAG 블록 예외 → 감지 skip 버그 수정 (최외곽 try-except 추가)
  - 2026-04-07 (v1.6.15): DIAG 블록 단일 try-except → 섹션별 독립 분리 수정
  - 2026-04-09 (현장 v1.6.15): `_embedded_alert_start` None 연산 오류 발생 (현재 분석 대상)
- **분석 스크립트**: `Fix/260410_bug_log/analyze_log.py`
- **eval-plan review 결과**: (이번 세션 생성, 2026-04-10)
- **eval-freeze analyze 결과**: (이번 세션 생성, 2026-04-10)
