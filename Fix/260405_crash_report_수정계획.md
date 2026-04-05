# 먹통 원인 미포착 — crash_report 저장 + eval-freeze 페르소나 보강 수정계획

## 문제 현상

- **발생 조건**: 매일 새벽 2TV 정파(00:30~01:00경) 발생 후 2~3일 운영 시 간헐적으로 먹통
- **재현 조건**: 간헐적 — 정파 사이클 2~3회 후 (정확한 트리거 불명)
- **사용자 증상**:
  1. 블랙/스틸 감지 안 됨 (정파 감지도 포함)
  2. 텔레그램 발송 안 됨 (발송중... 상태 고착)
  3. 각종 테스트 버튼 무반응
  4. 비디오 화면은 정상 입력됨
  5. 재시작하면 즉시 정상화 (헬스체크 자동 재시작 또는 수동 재시작)

## 로그 분석 결과

**실제 먹통 사례 확인 (Fix/260401_0403_log/20260402.txt)**:

| 시각 | 이벤트 |
|------|--------|
| 00:42:14 | 2TV 정파 진입 |
| 00:42:17 | 텔레그램 정파 알림 발송 완료 ✓ |
| **00:42~03:00** | **로그 공백 — 먹통 발생 (원인 미포착)** |
| 03:00:13 | SYSTEM - 프로그램 시작 (헬스체크/예약 재시작에 의한 자동 복구) |
| 03:00:14 | 재시작 후 2TV 정파 재감지 → 텔레그램 발송 |
| 04:45:30 | 2TV 정파 해제 |

**로그 구조적 한계 (핵심 문제)**:
- 먹통 발생 시 메인스레드가 블로킹/예외 상태 → 로그 자체도 출력 불가
- 헬스체크가 감지 중단을 감지하고 ERROR 로그를 남기지만, **재시작 직전 진단 스냅샷이 없음**
- 어떤 스레드가 죽었는지, SignoffManager가 어떤 상태였는지 알 수 없음

**DIAG-AUDIO 소실**: 먹통 구간 전체가 공백이므로 소실 시점 불명

## 원인 분석

### 1순위: crash_report 미저장 — 원인 진단 불가 상태
- 파일: `kbs_monitor/ui/main_window.py` L607~640 (`_update_summary()` 내 health check)
- 파일: `kbs_monitor/ui/main_window.py` L1275 (`_do_scheduled_restart()`)
- 설명: 헬스체크가 `detect_stale` 또는 `frame_stale`을 감지했을 때 ERROR 로그만 남기고,
  그 시점의 내부 상태(스레드 생존여부, SignoffManager 상태, DetectionState 값)를
  별도 파일에 저장하지 않음
- 발생 시나리오: 먹통 발생 → 헬스체크 감지 → ERROR 로그 1줄 → 재시작 → 원인 소멸
- 결과: 매번 같은 증상이 반복되지만 코드 수준의 원인 특정 불가

### 2순위: eval-freeze 에이전트 — 정파 사이클 관점 부재
- 파일: `.claude/agents/eval-freeze.md`
- 설명: 현재 eval-freeze는 개별 위험 요소(스레드, 큐, 예외)를 체크리스트로 탐색하지만,
  "정파 사이클 반복 후 상태 누적"이라는 실제 발생 패턴에 맞는 시나리오 관점이 약함
- 발생 시나리오: 정파 1회 정상 → 정파 2~3회째에 이전 사이클의 잔류 상태와 충돌 → 먹통
- 결과: analyze 결과가 정적 체크리스트 나열에 그치고, 실제 원인 시나리오 예측 정확도가 낮음

## 수정 대상 파일

| 파일 | 수정 위치 | 내용 |
|------|-----------|------|
| `kbs_monitor/ui/main_window.py` | `_update_summary()` L621 health check 첫 감지 분기 | crash_report 파일 저장 추가 |
| `kbs_monitor/ui/main_window.py` | `_do_scheduled_restart()` L1277 | 재시작 직전 crash_report 저장 추가 |
| `.claude/agents/eval-freeze.md` | 에이전트 description + 본문 상단 | 페르소나 문단 추가 |

## crash_report 저장 내용 명세

`kbs_monitor/logs/crash_YYMMDD_HHMMSS.txt` 형식으로 저장.

저장 항목:
```
=== KBS Peacock 먹통 진단 스냅샷 ===
시각: YYYY-MM-DD HH:MM:SS
트리거: detect_stale | frame_stale | scheduled_restart

[감지 루프]
마지막 감지: N.Ns 전 (감지횟수: N)
마지막 프레임: N.Ns 전
_detect_timer 활성: True/False
_summary_timer 활성: True/False

[스레드 생존]
TelegramWorker: alive=True/False
AlarmSoundThread: alive=True/False

[SignoffManager]
그룹1(1TV): IDLE/PREPARATION/SIGNOFF
그룹2(2TV): IDLE/PREPARATION/SIGNOFF

[DetectionState — V채널]
V1: still_timer=Ns resolve=N alerting=True/False
V2: ...
(V5~V8 포함)

[DetectionState — A채널]
A1: ... (오디오레벨미터 상태)
(A5~A6 포함)
```

저장 방식: `open(path, 'w', encoding='utf-8')` 직접 쓰기 (logger 우회 — 먹통 상태에서도 동작 보장)

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
> 6. 각 작업 항목 완료 즉시 체크박스를 `[x]`로 업데이트한다

## 단계별 수정 계획

> 각 Phase는 새로운 대화세션에서 시작한다.

### Phase 1 — crash_report 저장 기능 추가

- [ ] `main_window.py` `_update_summary()` health check 첫 감지 시 crash_report 저장
  - `_health_alarm_logged = False` → `True` 전환 시점 (최초 1회만)
  - 저장 경로: `kbs_monitor/logs/crash_YYMMDD_HHMMSS.txt`
  - 저장 내용: 위 명세 참조
  - 저장 방식: `open()` 직접 쓰기 (logger 미사용)
  - try-except로 보호 (crash_report 저장 실패가 health check 자체를 방해하면 안 됨)
- [ ] `main_window.py` `_do_scheduled_restart()` 재시작 직전 crash_report 저장
  - 로그 메시지 직후, `subprocess.Popen()` 직전에 저장
  - 트리거: `scheduled_restart`로 기재
- [ ] `_save_crash_report(trigger: str)` 헬퍼 메서드 추출 (두 호출 지점 공통)
  - `self._signoff_manager`, `self._telegram`, `self._alarm` 접근
  - DetectionState 접근: `self._detector` 통해 내부 상태 직접 접근 또는 summary 메서드 추가
- [ ] 커밋: `feat: 먹통 진단 crash_report 저장 기능 추가`
- [ ] eval-plan evaluate HEAD~1 자동 실행 (Agent 도구, 포그라운드)
- [ ] eval-freeze evaluate HEAD~1 자동 실행 (Agent 도구, 포그라운드)

### Phase 2 — eval-freeze 에이전트 페르소나 보강

- [x] `.claude/agents/eval-freeze.md` 상단에 페르소나 문단 추가:

```
## 분석 관점 (페르소나)

당신은 PySide6 기반 방송 모니터링 시스템을 24/7 운영하며
수십 번의 soft-freeze를 직접 디버깅한 경험이 있는 엔지니어입니다.

이 시스템의 먹통은 항상 같은 조건에서 발생합니다:
**새벽 정파 사이클 2~3회 후**, Qt 이벤트 루프는 살아있는데 감지와 알림이 동시에 멈춥니다.

당신이 코드를 볼 때의 단 하나의 질문은:
"이 코드는 정파 사이클 3번 후 왜 멈추는가?"

당신은 경험적으로 압니다:
- daemon 스레드가 죽은 뒤 재생성되지 않으면 조용히 기능이 사라집니다
- SIGNOFF → IDLE 전환 실패는 다음 사이클에서 감지 자체를 막습니다
- QTimer 콜백의 unhandled exception은 로그에 남지 않고 타이머를 영구 중단시킵니다
- 상태 오염은 첫 번째 사이클에서는 안 터지고, 두 번째나 세 번째에서 터집니다

체크리스트를 기계적으로 수행하되, **"이것이 3번째 정파 사이클에서 어떻게 먹통을 유발하는가"**
라는 시나리오 관점에서 각 위험 요소의 실제 발현 가능성을 판단하세요.
```

- [x] 기존 `## 핵심 임무` 섹션 앞에 삽입 (description 다음)
- [ ] 커밋: `docs: eval-freeze 에이전트 페르소나 보강 — 정파 사이클 관점 추가`
- [ ] eval-plan evaluate HEAD~1 자동 실행 (Agent 도구, 포그라운드)
- [ ] (eval-freeze evaluate 불필요 — 에이전트 자체 수정이므로)

### Phase 3 — 검증

- [ ] 현장 테스트: 다음 번 먹통 발생 후 `kbs_monitor/logs/crash_*.txt` 확인
- [ ] 확인 항목: TelegramWorker alive, SignoffManager 상태, DetectionState still_timer
- [ ] crash_report 내용으로 실제 원인 특정 후 → 별도 수정계획 수립

## 참고

- 관련 로그: `Fix/260401_0403_log/20260402.txt` — 03:00 재시작 사례 (먹통 증거)
- 관련 설계 원칙: `kbs_monitor/core/CLAUDE.md` — 감지 루프 안정성 원칙
- 관련 에이전트: `.claude/agents/eval-freeze.md` — Phase 2 수정 대상
- 자동재시작 현재 설정: `kbs_config.json` — `scheduled_restart_enabled: false`, `time: 21:46`
  (4월2일 03:00 재시작은 당시 설정 또는 헬스체크 트리거로 추정)
