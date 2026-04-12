# KBS Peacock — 16채널 비디오 모니터링 시스템

PySide6 기반 방송 현장용 16채널 비디오 모니터링 솔루션.
블랙·스틸·오디오 레벨미터·임베디드 오디오 감지 + 시각·소리·텔레그램 알림.

---

## 버전 업데이트 이력

| 버전 | 주요 변경사항 |
|------|--------------|
| **v1.6.20** | DIAG-AUDIO 타입 방어 강화 + watchdog 텔레그램 알림 추가 — 장기 실행 후 TypeError 재발 차단, 감지 루프 중단 시 즉시 텔레그램 발송 |
| v1.6.19 | 자동 재시작 UI 개선 — 개별 체크박스 + GridLayout 정렬 |
| v1.6.18 | 예약 재시작 하루 2회 지원 (시각 2 선택적 활성화) |
| v1.6.17 | DIAG-AUDIO 오류 고착 수정 + alarm/telegram/recorder 장기 실행 안정성 개선 |
| v1.6.16 | install.bat 상대경로 절대경로화 + pycaw 버전 제한 제거 — 다른 PC 설치 시 requirements.txt 탐색 실패·pycaw 버전 불일치 오류 해결 |
| **v1.6.15** | DIAG 블록 6개 섹션 독립 격리 + traceback 보호 — 단일 try-except 구조로 인한 25시간 침묵 버그 수정, psutil.Process() 매 사이클 재생성 제거 |
| **v1.6.14** | UI 로그 노이즈 제거 — 사운드 백엔드 폴백 메시지·SIGNOFF 내부 상태 덤프를 파일 로그 전용으로 전환, Health Check 복구 메시지 사용자 친화적 문구로 개선 |
| **v1.6.13** | 부분 freeze 수정 — DIAG 블록 try-except 보호로 장기 실행 후 감지 중단 버그 해결, Health Check 단순화(영상 중단 알림 제거) |
| v1.6.12 | faulthandler 크래시 추적 추가(logs/fault.log), 텔레그램 silent 실패 진단 로그, 예약 재시작 날짜 중복방지 수정, 텔레그램 재시도 오류 UI 오표시 수정 |
| v1.6.11 | 비디오 캡처 8시간 주기 강제 재연결 제거 (부분 freeze와 무관한 불필요 코드) |
| **v1.6.10** | Health Check 시각 인디케이터 추가 — 감지 루프/비디오 프레임 중단 시 상단바 빨간 경고 표시 |
| v1.6.9 | 포트 연결 실패 로그 정확도 개선 + 텔레그램 연속 실패 로그 스팸 억제 |
| v1.6.8 | 캡처 스레드 heartbeat 로그 추가, 감지 루프 heartbeat 주기 단축 + 타이머 상태 로그 |
| v1.6.7 | 로그 개선 — 매체명 추가, NEAR-MISS/EXIT-DBG/DIAG-ALARM/DIAG-TELEGRAM 로그 정제 |
| v1.6.6 | signoff_manager 퇴출 타이머 stale 버그 수정 |
| v1.6.5 | 코드 최적화 Phase 5 완료 (crop 검증, requirements 상한선, 에러 처리 강화) |
| v1.6.4 | 스틸 감지 블록 격자 세분화 (3×3→5×5), 블록 임계값 하향 (15%→10%) |
| v1.6.3 | 약식 버전 표기 업데이트 |
| v1.6.2 | 스틸 감지 블록 방식 전환, 감지 설정 UI 순서 재배치 |
| v1.6.1 | 오디오 장치 자동 재연결, 녹화 큐 메모리 보호, 로그 로테이션 안전성 강화 |
| v1.6.0 | 장기 실행 안정성 강화 — 히스테리시스, 텔레그램 자동복구 |
| v1.5.8 | 알림음 기본값 설정, 감지 파라미터 기본값 조정, DIAG 로그 강화 |
| v1.5.7 | 블랙/스틸 감지 기본값 완화 (오감지 감소) |
| v1.5.5 | 스틸 히스테리시스 수정, 블랙 모션 억제, 텔레그램 429 Rate Limit 재시도 처리 |
| v1.5.4 | 정파모드 스틸 감지 연동 수정 |
| v1.5.3 | 감지 중단 silent failure 방지 (try-except 전면 추가) |
| v1.5.2 | 임베디드 오디오 정파모드 억제 버그 수정 |

---

## ROI 감지 워크플로우

### 전체 흐름

```
VideoCaptureThread
  │  (OpenCV CAP_DSHOW, 33ms 간격)
  │  frame_ready 시그널
  ▼
MainWindow._on_frame_ready()
  │  frame.copy() → _latest_frame 저장
  │  VideoWidget.update_frame()  (화면 표시)
  └→ AutoRecorder.push_frame()  (자동 녹화 큐)

QTimer (200ms 주기)
  │
  ▼
MainWindow._run_detection()
  │
  ├─ Detector.detect_frame(frame, video_rois)
  │    │
  │    ├─ [블랙 감지]  ROI 평균 밝기 < 임계값 → is_abnormal
  │    │
  │    ├─ [스틸 감지]  블록 격자(5×5) 차분 합계 < 임계값 → is_abnormal
  │    │               히스테리시스: 연속 N프레임 정상이어야 타이머 리셋
  │    │
  │    └─ [레벨미터 감지]  HSV 마스크로 VU 바 색상 검출 → 신호 없으면 is_abnormal
  │
  ├─ Detector.detect_audio_roi(audio_levels, audio_rois)
  │    └─ [임베디드 오디오]  RMS 레벨 < 임계값 지속 → is_abnormal
  │
  └─ DetectionState.update(is_abnormal, threshold_sec, recovery_sec)
       │  이상 지속 ≥ threshold_sec → is_alerting = True
       │  정상 복구 ≥ recovery_sec (또는 N프레임) → just_resolved = True
       ▼
      AlarmSystem.trigger() / resolve()
        ├─ 소리 알림  (winsound WAV 반복 재생)
        ├─ 시각 알림  (VideoWidget 빨간 테두리 깜박임)
        └─ 텔레그램   (큐 기반 비동기 발송)
```

### 감지 유형별 억제 규칙 (정파모드)

| 감지 유형 | 정파모드 동작 |
|-----------|--------------|
| 블랙 / 스틸 | 해당 정파 그룹 label만 억제, 나머지 채널은 계속 감지 |
| 오디오 레벨미터 | 동일 (그룹별 개별 억제) |
| 임베디드 오디오 | 억제 없음 (그룹 귀속 없는 단일 감지) |

### 주요 파라미터

- **감지 주기**: 200ms (기본값, 설정 변경 가능)
- **감지영역(ROI) 최대 크기**: 500×300 px (성능 제한)
- **히스테리시스**: 연속 N프레임 정상이어야 타이머 리셋 (단일 글리치 오복구 방지)
- **silent failure 보호**: 감지 루프 전체 try-except 보호 + 5분마다 SYSTEM 로그

---

## 설치 요구사항

```
Python 3.10+
PySide6, opencv-python, numpy
sounddevice, psutil, gputil, pycaw, requests
ffmpeg  (winget install ffmpeg)  ← 자동 녹화 오디오 합성용
```
