# KBS Peacock — 16채널 비디오 모니터링 시스템

PySide6 기반 방송 현장용 16채널 비디오 모니터링 솔루션.
블랙·스틸·오디오 레벨미터·임베디드 오디오 감지 + 시각·소리·텔레그램 알림.

---

## 버전 업데이트 이력

| 버전 | 주요 변경사항 |
|------|--------------|
| **v1.6.7** | 로그 개선 — 매체명 추가, NEAR-MISS/EXIT-DBG/DIAG-ALARM/DIAG-TELEGRAM 로그 정제 |
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
