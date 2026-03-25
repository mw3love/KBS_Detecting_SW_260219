# Phase 1 완료 기록

완료 일시: 2026-02-18
최종 업데이트: 2026-02-19 (Phase 2 이후 변경사항 반영)

---

## 완료된 작업 목록

### 프로젝트 골격
- [x] `kbs_monitor/` 폴더 구조 생성 (config, ui, core, utils, resources)
- [x] `CLAUDE.md` 생성 (프로젝트 규칙 및 개발 지침)
- [x] `kbs_monitor/requirements.txt` 생성

### 핵심 모듈 (core/)
- [x] `core/__init__.py`
- [x] `core/video_capture.py` — OpenCV 비디오 캡처 스레드 (QThread)
  - CAP_DSHOW, 1920x1080, 30fps, 버퍼사이즈=1
  - 포트 변경 지원 (뮤텍스 보호), 재연결 로직 (30회 연속 실패 시 재연결)
  - 시그널: `frame_ready(object)`, `connected()`, `disconnected()`, `status_changed(str)`
- [x] `core/audio_monitor.py` — sounddevice 임베디드 오디오 캡처 스레드 (QThread)
  - 44100Hz, 스테레오(2ch), CHUNK=1024
  - L/R RMS → dB 변환 (-60dB 클램프), 무음 감지 (-50dB 이하)
  - sounddevice 미설치 시 -60dB 더미 신호 자동 발송
  - 시그널: `level_updated(float, float)`, `status_changed(str)`, `silence_detected(float)`
- [x] `core/roi_manager.py` — 감지영역 데이터 관리
  - `ROI` dataclass: label, media_name, x, y, w, h, roi_type
  - `ROIManager`: video/audio ROI 추가/제거/복사/일괄교체, 라벨 자동 부여 (V1..V16, A1..A16)
  - JSON 직렬화/역직렬화, 감지영역 최대 크기 (500×250) 클램프
- [x] `core/detector.py` — 블랙/스틸 감지 엔진
  - `DetectionState`: 이상 지속 시간 추적, 임계값 초과 시 alerting=True
  - `Detector`: 파라미터(임계값, 지속시간, 알림지속시간), detect_frame() → label별 결과 dict
  - 블랙 감지: 평균 밝기 < 임계값
  - 스틸 감지: 이전 프레임과의 픽셀 차이 < 임계값
- [x] `core/alarm.py` — 알림 시스템
  - `AlarmSystem(QObject)`: 시각적 깜박임(0.5초 토글), WAV 재생 (별도 스레드, winsound)
  - 알림 타입별 사운드 파일: black_alarm.wav, still_alarm.wav, audio_alarm.wav, alarm.wav
  - 시그널: `visual_blink(bool)`, `alarm_triggered(str)`
  - `set_volume()` 값 저장만 (winsound 실제 볼륨 제어 미구현)

### UI 모듈 (ui/)
- [x] `ui/__init__.py`
- [x] `ui/main_window.py` — 메인 윈도우 (3분할 레이아웃)
  - TopBar + QSplitter(VideoWidget 75% / LogWidget 25%)
  - 프로그램 시작 시 마지막 설정 자동 로드, 종료 시 저장
  - **프로그램 실행 즉시 감지 활성** (On/Off 버튼 없음, 항상 감지)
- [x] `ui/top_bar.py` — 상단 제어 바
  - **SysMonitorWidget**: CPU%, RAM%, GPU% 실시간 표시 (psutil + GPUtil, 2초 갱신)
    - GPU: GPUtil(NVIDIA) → nvidia-smi 직접 경로 순 탐지, 없으면 N/A
  - **현재시간**: 소제목 "현재시간" + HH:MM:SS (Segoe UI 11 Bold)
  - 스피커 뮤트 버튼 + 볼륨 슬라이더 + L/R 오디오 레벨미터 (커스텀 paintEvent)
  - 감지현황: 소제목 "감지현황" + V/A/EA 수치 (Segoe UI 11 Bold)
  - 감지영역 토글 버튼, 야간/주간 모드 토글, 설정 버튼
  - **폰트 기준**: 소제목 Segoe UI 9 Bold, 수치값 Segoe UI 11 Bold
- [x] `ui/video_widget.py` — 비디오 표시 위젯
  - OpenCV BGR numpy → QImage → scaled QPixmap → QLabel
  - 비율 유지 스케일링 + 레터박스 좌표 변환
  - 감지영역 오버레이: 비디오 ROI 빨간 테두리, 오디오 ROI 초록 테두리
  - 알림 상태: 빨간 반투명 채우기 + 깜박임 (`set_blink_state`)
  - NO SIGNAL INPUT 화면 (1920x1080 캐시)
- [x] `ui/log_widget.py` — 시스템 로그 위젯
  - 시간+메시지, 오류=빨간 배경, 최대 **500개** 유지 (초과 시 오래된 항목 제거)
  - 날짜 변경 시 구분선 자동 삽입, 자동 스크롤
  - "Log 초기화" 버튼
- [x] `ui/settings_dialog.py` — 설정 다이얼로그 (6탭 구조, 비모달)
  - Phase 1 당시: 탭 1(입력선택)만 완전 구현, 나머지 플레이스홀더
  - 현재: Phase 2에서 탭 2~4 완전 구현됨
- [x] `ui/roi_editor.py` — 감지영역 편집기 골격
  - Phase 2에서 완전 구현됨
- [x] `ui/dual_slider.py` — HSV 듀얼 슬라이더 골격
  - Phase 3에서 완전 구현 예정 (현재: get_range/set_range API만 존재)

### 유틸리티 및 리소스
- [x] `utils/__init__.py`
- [x] `utils/config_manager.py` — JSON 설정 저장/불러오기
  - 기본값 자동 병합, config/kbs_config.json ↔ config/default_config.json
- [x] `utils/logger.py` — 파일(일별 로테이션) + UI 로그 동시 출력
  - 파일: logs/YYYYMMDD.txt
  - 시그널: `log_signal(str, bool)`
- [x] `config/default_config.json` — 기본 설정값
- [x] `resources/styles/dark_theme.qss` — 다크 테마 스타일시트

### 엔트리포인트
- [x] `main.py` — 프로그램 시작점 (콘솔 숨기기, QSS 로드, MainWindow 실행)

---

## 현재 파일 상태

```
kbs_monitor/
├── main.py
├── config/
│   └── default_config.json
├── ui/
│   ├── __init__.py
│   ├── main_window.py       ✅ Phase 1 + Phase 2 업데이트
│   ├── top_bar.py           ✅ Phase 1 + Phase 2 이후 수정 (SysMonitor, 폰트 통일)
│   ├── video_widget.py      ✅ Phase 1 구현
│   ├── log_widget.py        ✅ Phase 1 구현
│   ├── settings_dialog.py   ✅ Phase 2 완전 구현
│   ├── roi_editor.py        ✅ Phase 2 완전 구현
│   └── dual_slider.py       ⬜ Phase 3 완전 구현 예정
├── core/
│   ├── __init__.py
│   ├── video_capture.py     ✅ Phase 1 구현
│   ├── audio_monitor.py     ✅ Phase 1 구현
│   ├── roi_manager.py       ✅ Phase 1 + Phase 2 업데이트
│   ├── detector.py          ✅ 블랙/스틸 구현, 레벨미터/임베디드 Phase 3 예정
│   └── alarm.py             ✅ Phase 1 구현
├── utils/
│   ├── __init__.py
│   ├── config_manager.py    ✅ Phase 1 구현
│   └── logger.py            ✅ Phase 1 구현
└── resources/
    ├── sounds/
    └── styles/
        └── dark_theme.qss
```
