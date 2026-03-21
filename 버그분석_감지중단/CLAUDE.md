# 계획: 감지 중단 버그 분석 폴더 및 문서 생성

## Context
외부 설치본(현장 운용 중)에서 스틸/블랙 감지가 장기 실행 후 중단되는 버그 발생.
- 14일: 정상 (감지 → SIGNOFF 자동 전환 → 텔레그램 알림 모두 작동)
- 15~16일: SIGNOFF 스케줄만 실행, 감지 트리거 없음 (불규칙 시간대 알림 없음)
- 재현 패턴: 프로그램 재시작 후 정상 복귀 → 세션 중 어느 시점에 감지가 멈춤
- 로그에도 에러가 남지 않는 것이 특징 (=silent failure)

## 탐색에서 발견된 핵심 문제

### 원인 1순위: try-except 미보호 (silent failure)
- `main_window.py:_run_detection()` (L230~389) — 전체 메서드에 try-except 없음
  - `detector.detect_frame()` 또는 `detect_audio_roi()` 예외 발생 시
  - 로그 없음 + 타이머는 계속 실행 + 해당 주기 감지 결과만 소실
  - 반복 예외 발생 시 매 주기 silent fail → 감지 완전 중단
- `video_capture.py:run()` (L46~119) — try-except 없음
  - `cap.read()` 예외 시 스레드 크래시 가능 (OpenCV에서 드문 경우)
  - 크래시 시 `frame_ready` 신호 없음 → `_latest_frame = None` 유지
  - `_run_detection()` L231에서 즉시 반환 → 감지 중단

### 원인 2순위: VideoCaptureThread 프레임 중단
- 연속 30프레임 읽기 실패 → `cap = None` (L108~116)
- 재연결 성공 전까지 `_latest_frame` 갱신 안 됨
- 재연결은 1초 간격으로 시도하나 성공 여부가 로그에 상세히 남지 않음

### 원인 3순위: _prev_frames 상태 손실
- `detector.py:update_roi_list()` (L164~192) — ROI 변경 시 `_prev_frames` 정리
- 설정 변경 또는 예외 후 ROI 목록이 갱신되면 이전 프레임 버퍼 소실
- → 첫 프레임에서 비교 대상 없음 → `is_still=False` 고정 → 스틸 감지 불가
- 이후 정상 복귀되어야 하지만 연속 예외 상황에서는 복귀 안 됨

## 생성할 파일 구조

```
g:\내 드라이브\A1. 개인 자료\A1. Python\260222 KBS Peacock v1.0 이후\
└── 버그분석_감지중단/
    ├── CLAUDE.md    ← 다음 세션에서 읽을 컨텍스트 파일 (버그 현상, 원인, 코드 위치)
    └── TODO.md      ← 수정 작업 체크리스트
```

## CLAUDE.md 내용 계획

다음 섹션 포함:
1. **버그 현상** — 로그 파일 요약 (14일 정상 / 15~16일 비정상)
2. **재현 조건** — 장기 실행 후 발생, 재시작 시 복구
3. **원인 분석** — 3가지 경로 (try-except 없음, 프레임 중단, _prev_frames 손실)
4. **관련 코드 위치** — 파일:라인 번호 포함
5. **AudioMonitorThread는 안정적** — try-except 충분히 보호됨 (참고)

## TODO.md 내용 계획

우선순위 순서:

### P1 — 즉시 수정 (silent failure 방지)
- [ ] `main_window._run_detection()`에 try-except 추가 + `logger.error()` 기록
- [ ] `video_capture.VideoCaptureThread.run()`에 try-except 추가

### P2 — 감지 상태 가시성 확보 (진단용 로깅)
- [ ] `_run_detection()`에 주기적 정상 작동 로그 추가 (예: 5분마다 "감지 X회 정상 실행")
- [ ] VideoCaptureThread 재연결 성공/실패 로그 상세화

### P3 — 원인 확인 (현장 자료 수집)
- [ ] 현장 설치본의 `logs/` 폴더 내 날짜별 로그 수집
- [ ] 14일 이후 로그 파일에서 에러/경고 라인 추출 후 분석

### P4 — 장기적 개선
- [ ] `detector.detect_frame()` 내부에도 ROI별 try-except (개별 ROI 실패가 전체를 멈추지 않도록)
- [ ] 감지 비활성 지속 시 TopBar에 경고 표시 (자가 진단 기능)

## 수정 대상 파일 목록 (다음 세션 참고)

| 파일 | 수정 위치 | 내용 |
|------|-----------|------|
| `kbs_monitor/ui/main_window.py` | L230~389 `_run_detection()` | try-except 추가 |
| `kbs_monitor/core/video_capture.py` | L46~119 `run()` | try-except 추가 |
| `kbs_monitor/core/detector.py` | L194~267 `detect_frame()` | ROI별 예외 격리 (선택) |

## 실행 단계

1. `버그분석_감지중단/` 폴더 생성
2. `CLAUDE.md` 파일 작성 (버그 컨텍스트, 로그 첨부)
3. `TODO.md` 파일 작성 (우선순위별 체크리스트)
4. 계획 파일 완료

## 검증 방법

다음 세션에서:
1. `버그분석_감지중단/CLAUDE.md` 열기 → 컨텍스트 즉시 파악 가능한지 확인
2. `TODO.md`의 P1 항목 수정 후 현장 설치본에서 장기 실행 테스트
3. 수정 후 예외 발생 시 로그 파일에 에러가 기록되는지 확인
