# 감지 중단 버그 수정 체크리스트

## P1 — 즉시 수정 (silent failure 방지)
- [x] `main_window._run_detection()`에 try-except 추가 + `logger.error()` 기록
  - `kbs_monitor/ui/main_window.py` — 전체 감지 루프를 try-except로 보호
  - 예외 발생 시 `SYSTEM - 감지 루프 오류 (silent fail 방지): {e}` 로그
- [x] `video_capture.VideoCaptureThread.run()`에 try-except 추가
  - `kbs_monitor/core/video_capture.py` — while 루프 전체를 try-except로 보호
  - 예외 발생 시 cap 상태 초기화 + disconnected 신호 + 1초 후 재연결 시도

## P2 — 감지 상태 가시성 확보 (진단용 로깅)
- [x] `_run_detection()`에 주기적 정상 작동 로그 추가 (5분마다)
  - `_detection_count` 카운터 추가, 1500회(≈5분)마다 `SYSTEM - 감지 정상 실행 중 (N분 경과)` 로그
- [ ] VideoCaptureThread 재연결 성공/실패 로그 상세화
  - 현재 `status_changed` 시그널로 상태 전달 중이나 UI 표시 여부 확인 필요

## P3 — 원인 확인 (현장 자료 수집)
- [x] 현장 설치본의 로그 수집 완료 (20260314.txt, 20260315.txt, 20260316.txt)
- [x] 14일 이후 로그 분석 완료
  - 15~16일: SIGNOFF 스케줄만 실행, 감지 트리거 없음 → silent failure 확인

## P4 — 장기적 개선
- [x] `detector.detect_frame()` 내부에도 ROI별 try-except
  - `kbs_monitor/core/detector.py` — 개별 ROI 실패가 전체를 멈추지 않도록
  - `detect_frame()`, `detect_audio_roi()` 각 ROI 루프에 try-except 추가
  - 오류 시 `logging.getLogger(__name__).error()` 로그
- [ ] 감지 비활성 지속 시 TopBar에 경고 표시 (자가 진단 기능) — 2차 개발
