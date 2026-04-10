# KBS 16채널 비디오 모니터링 시스템 v2

## 프로젝트 개요
- PySide6 기반 16채널 비디오 모니터링 시스템
- 블랙/스틸/오디오레벨미터/임베디드오디오 감지 + 시각/소리 알림
- Python 3.10+

## 개발 규칙
- 모든 응답은 한국어로 작성
- PySide6 사용 (PyQt 아님)
- 다크 모드 UI 기본
- 색상 원칙: 정상=색 없음(기본 배경/텍스트), 이상=빨간색만 사용 (초록색 금지)
  - 예외: `log_widget.py`의 로그 타입 구분 색상은 시각 구별을 위해 다른 색 허용
    (블랙이상=빨간, 스틸이상=보라, 오디오레벨미터이상=초록, 임베디드오디오이상=파란)
- 파일 인코딩: UTF-8
- 들여쓰기: 4 spaces
- docstring: 한국어
- QSpinBox 위아래 버튼 사용 금지

## 용어
- ROI → "감지영역"으로 통일
- 탭 구조: 입력선택, 비디오 감지 설정, 오디오 레벨미터 감지 설정, 감지 설정, 알림설정, 저장/불러오기
- 감지합계 표기: V(영상) A(오디오레벨미터) EA(임베디드오디오)

## 현재 개발 상태
- **현재: Phase 5 완료 (코드 최적화) + v1.6.19**
- 체크포인트: `Fix/KBS_16CH_모니터링_v2_개발계획서/PHASE4_COMPLETE.md` (Phase 5 기록 포함)

## 주의사항
- 스냅샷 탭 없음, 비히스토리 탭 없음 (2차 개발)
- 카카오톡/이메일 없음 (텔레그램만 지원)
- 성능 제한: 감지영역 최대 500×300 (ROI 개수 제한 없음, 많을수록 CPU 부하 증가)
- 비디오 영역에 소스 라벨, LIVE 인디케이터, 상태 선 없음
- 상단바에 "알림 초기화" 버튼 없음 (알림확인 버튼만 존재, core/CLAUDE.md 참조)

## 설치된 외부 패키지
```
PySide6
opencv-python
numpy
sounddevice    (없으면 더미 신호로 동작)
psutil
gputil         (NVIDIA GPU 없으면 N/A 표시)
pycaw          (Windows 시스템 볼륨 제어)
requests       (텔레그램 HTTP 발송)
```

## 외부 도구: ffmpeg (자동 녹화 오디오 합성)
- 용도: 자동 녹화 MP4에 임베디드 오디오 트랙 합성 (`auto_recorder.py`)
- **설치 방법: `winget install ffmpeg`** (권장, 한 번 설치 후 프로그램 업데이트와 무관하게 유지)
- 미설치 시: 비디오 전용 MP4로 폴백 (소리 없음, 에러 아님)
- `_find_ffmpeg()` 탐색 우선순위: PATH(winget) → C:\KBS_Tools\ffmpeg.exe → resources/bin/ffmpeg.exe

## 아키텍처 개요

### 파일 구조
```
kbs_monitor/
├── main.py                      # 진입점, dark_theme.qss 로드, 콘솔 숨기기, faulthandler(logs/fault.log)
├── ui/                          # → ui/CLAUDE.md (PySide6 위젯 패턴)
│   ├── main_window.py           # 오케스트레이터 (3분할 레이아웃)
│   ├── top_bar.py               # 상단 바 (SysMonitor, 시계, 오디오, 감지현황, 버튼)
│   ├── video_widget.py          # 비디오 표시 + ROI 오버레이
│   ├── log_widget.py            # 시스템 로그 (최대 500개)
│   ├── settings_dialog.py       # 6탭 설정 다이얼로그 (비모달)
│   ├── roi_editor.py            # ROIEditorCanvas (반화면) + FullScreenROIEditor
│   └── dual_slider.py           # HSV 듀얼 슬라이더
├── core/                        # → core/CLAUDE.md (감지 루프·알림 설계 원칙)
│   ├── video_capture.py         # VideoCaptureThread (QThread, OpenCV CAP_DSHOW)
│   ├── audio_monitor.py         # AudioMonitorThread (QThread, sounddevice)
│   ├── roi_manager.py           # ROI dataclass + ROIManager
│   ├── detector.py              # 블랙/스틸/HSV레벨미터/임베디드오디오 감지 엔진
│   ├── alarm.py                 # AlarmSystem (winsound WAV + 시각 깜박임)
│   ├── auto_recorder.py         # 사고 발생 MP4 자동 녹화 (ffmpeg 통합)
│   ├── signoff_manager.py       # 정파준비/정파모드 상태 관리 (1초 타이머)
│   └── telegram_notifier.py     # 텔레그램 알림 발송 (큐 기반)
├── utils/
│   ├── config_manager.py        # JSON 설정 저장/불러오기
│   └── logger.py                # 파일(일별 로테이션) + UI 동시 출력
├── config/
│   └── default_config.json
└── resources/
    ├── sounds/                  # black_alarm.wav, still_alarm.wav, audio_alarm.wav, alarm.wav
    └── styles/
        ├── dark_theme.qss
        └── light_theme.qss
```

### 핵심 시그널 흐름
```
VideoCaptureThread.frame_ready   → _on_frame_ready → VideoWidget.update_frame
                                                    → AutoRecorder.push_frame
AudioMonitorThread.level_updated → TopBar.update_audio_levels (LevelMeterBar)
QTimer(200ms, 기본)              → _run_detection → Detector.detect_frame
                                                   → SignoffManager.tick
                                                   → AlarmSystem.trigger/resolve
QTimer(1000ms)                   → _update_summary → TopBar.update_summary
SignoffManager.state_changed     → _on_signoff_state_changed
AlarmSystem.visual_blink         → VideoWidget.set_blink_state
```

### 감지 모드
- **항상 감지 활성**: 프로그램 실행 즉시 감지 시작 (On/Off 버튼 없음)
- 반화면 ROI 편집 중에만 감지 타이머 일시 중단 (`_roi_overlay is not None`)

---

## 핵심 설계 원칙 — 서브 CLAUDE.md 참조

| 파일 수정 시 | 참조 문서 |
|-------------|----------|
| `ui/` 폴더 전체 | **[kbs_monitor/ui/CLAUDE.md](kbs_monitor/ui/CLAUDE.md)** — QScrollArea GC 패턴, 변수명 충돌 규칙 |
| `core/alarm.py` `core/detector.py` `core/video_capture.py` `core/signoff_manager.py` `ui/main_window.py` | **[kbs_monitor/core/CLAUDE.md](kbs_monitor/core/CLAUDE.md)** — alarm 설계 원칙, 감지 루프 안정성, 히스테리시스 원칙 |
