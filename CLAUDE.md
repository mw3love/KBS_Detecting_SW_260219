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
- 파일 인코딩: UTF-8
- 들여쓰기: 4 spaces
- docstring: 한국어
- QSpinBox 위아래 버튼 사용 금지

## 용어
- ROI → "감지영역"으로 통일
- 탭 구조: 입력선택, 비디오 감지 설정, 오디오 레벨미터 감지 설정, 감지 설정, 알림설정, 저장/불러오기
- 감지합계 표기: V(영상) A(오디오레벨미터) EA(임베디드오디오)

## 현재 개발 상태
- 체크포인트 파일(PHASE*_COMPLETE.md)을 확인하여 현재 진행 상황 파악
- 체크포인트가 없으면 Phase 1부터 시작
- 각 Phase 완료 시 PHASE{N}_COMPLETE.md 파일 생성
- **현재: Phase 5 완료 (코드 최적화 완료)**

## 주의사항
- 스냅샷 탭 없음, 비히스토리 탭 없음 (2차 개발)
- 카카오톡/이메일 없음 (2차 개발)
- 성능 제한: 감지영역 최대 500×250 (ROI 개수 제한 없음, 많을수록 CPU 부하 증가)
- 비디오 영역에 소스 라벨, LIVE 인디케이터, 상태 선 없음

## 설치된 외부 패키지
```
PySide6
opencv-python
numpy
sounddevice  (없으면 더미 신호로 동작)
psutil==7.2.2
gputil==1.4.0  (NVIDIA GPU 없으면 N/A 표시)
```

## 아키텍처 개요

### 파일 구조
```
kbs_monitor/
├── main.py                  # 진입점, dark_theme.qss 로드, 콘솔 숨기기
├── ui/
│   ├── main_window.py       # 오케스트레이터 (3분할 레이아웃)
│   ├── top_bar.py           # 상단 바 (SysMonitor, 시계, 오디오, 감지현황, 버튼)
│   ├── video_widget.py      # 비디오 표시 + ROI 오버레이
│   ├── log_widget.py        # 시스템 로그 (최대 500개)
│   ├── settings_dialog.py   # 6탭 설정 다이얼로그 (비모달)
│   ├── roi_editor.py        # ROIEditorCanvas (반화면 편집) + FullScreenROIEditor
│   └── dual_slider.py       # HSV 듀얼 슬라이더 (두 핸들 드래그 범위 선택)
├── core/
│   ├── video_capture.py     # VideoCaptureThread (QThread, OpenCV CAP_DSHOW)
│   ├── audio_monitor.py     # AudioMonitorThread (QThread, sounddevice)
│   ├── roi_manager.py       # ROI dataclass + ROIManager
│   ├── detector.py          # 블랙/스틸/HSV레벨미터/임베디드오디오 감지 엔진
│   └── alarm.py             # AlarmSystem (winsound WAV + 시각 깜박임, 개별 파일 경로 지원)
├── utils/
│   ├── config_manager.py    # JSON 설정 저장/불러오기
│   └── logger.py            # 파일(일별 로테이션) + UI 동시 출력
├── config/
│   └── default_config.json
└── resources/
    ├── sounds/              # black_alarm.wav, still_alarm.wav, audio_alarm.wav, alarm.wav
    └── styles/
        └── dark_theme.qss
```

### 핵심 시그널 흐름
```
VideoCaptureThread.frame_ready  → MainWindow._on_frame_ready → VideoWidget.update_frame
AudioMonitorThread.level_updated → TopBar.update_audio_levels (LevelMeterBar)
QTimer(200ms, 기본)             → MainWindow._run_detection → Detector.detect_frame
                                  → AlarmSystem.trigger/resolve → VideoWidget.set_alert_state
QTimer(1000ms)                  → MainWindow._update_summary → TopBar.update_summary
```

### 감지 모드 (현재)
- **항상 감지 활성**: 프로그램 실행 즉시 감지 시작 (On/Off 버튼 없음)
- 반화면 ROI 편집 중에만 감지 타이머 일시 중단

### 주요 최적화 사항 (Phase 5)
- `Detector._apply_scale_factor()`: 스케일 로직 단일 공통 메서드로 통합
- `Detector.update_roi_list()`: `_audio_ratio_buffer`, `_audio_level_states` 정리 추가 (메모리 누수 수정)
- `MainWindow._run_detection()`: ROI label→name 조회를 O(n) 탐색에서 dict 캐시 O(1)로 교체
- `SettingsDialog._apply_detection_params_to_ui()`, `_apply_performance_params_to_ui()`: 중복 UI 적용 로직 공통 메서드화
- `AudioMonitorThread`: `_stereo` 플래그를 초기화 시 한 번만 결정, 루프 내 상수 비교 제거

## alarm.py 핵심 설계 원칙 (수정 시 반드시 준수)

### 알림음 테스트 재생 (`play_test_sound` / `_play_test_worker`)

**절대 바꾸지 말아야 할 사항:**

1. **winsound를 sounddevice보다 먼저 시도한다**
   - sounddevice는 시스템 기본 오디오 장치가 아닌 장치로 재생되거나 볼륨이 0이면 소리가 전혀 안 날 수 있음
   - winsound(`SND_FILENAME | SND_SYNC`)는 항상 시스템 기본 장치 + 시스템 볼륨 기준으로 재생 → 안정적
   - 순서를 바꾸면 파일 선택 후 테스트 버튼이 무음이 되는 버그 재발

2. **`play_test_sound`에서 `threading.Event()`를 새 인스턴스로 생성한다**
   ```python
   self._stop_sound = threading.Event()  # ← 반드시 새 객체, clear()만 하면 안 됨
   ```
   - `_stop_playback()`이 기존 이벤트에 `set()`을 한 상태에서 `join(timeout)`이 타임아웃되면 기존 스레드가 살아있을 수 있음
   - `clear()`만 하면 기존 스레드가 되살아나 새 스레드와 충돌함

3. **파일 경로는 반드시 절대경로(`os.path.abspath`)로 변환 후 사용한다**
   - UI에서 저장되는 경로는 상대경로일 수 있음 (`resources\sounds\Alert.wav`)
   - winsound/sounddevice 모두 상대경로는 cwd 의존적이므로 절대경로로 변환해야 안전

4. **sounddevice `sd.play()` 호출 후 반드시 `sd.wait()`를 호출한다**
   - `sd.play()`의 기본값은 `blocking=False` (non-blocking)
   - `sd.wait()` 없이 while 루프를 돌면 재생이 계속 덮어씌워져 무음이 됨
   - `sd.stop()`을 다른 스레드에서 호출하면 `sd.wait()`가 즉시 반환되어 루프 종료됨

5. **`_play_test_worker`는 `_play_sound_worker`와 별개로 유지한다**
   - 테스트 재생(1회) 로직과 알림 재생(반복) 로직을 분리
   - 공유하면 반복 루프 / 블로킹 / 중지 조건이 복잡해져 버그 유발

### 실제 알림 반복 재생 (`_play_sound_worker`)
- **winsound → sounddevice → 내장음 순서** (테스트와 동일, sounddevice 무음 버그 방지)
- winsound: `SND_ASYNC` + `wait(timeout=sound_duration)` 패턴으로 반복 재생
- sounddevice: `sd.play()` + `sd.wait()` 쌍, winsound가 없을 때만 사용
- `_stop_playback()`에서 `_stop_sound.set()` + `sd.stop()`으로 즉시 중단 가능
- 파일 경로는 `os.path.abspath()`로 절대경로 변환 후 사용

## 탭별 구현 현황

| 탭 | 이름 | 구현 상태 |
|----|------|----------|
| 1 | 입력선택 | ✅ 완료 (포트 0~5 콤보박스) |
| 2 | 비디오 감지 설정 | ✅ 완료 (ROI 편집 + 테이블) |
| 3 | 오디오 레벨미터 감지 설정 | ✅ 완료 (ROI 편집 + 테이블 + HSV 설정) |
| 4 | 감지 설정 | ✅ 완료 (블랙/스틸/오디오레벨미터/임베디드 파라미터 + 성능 설정) |
| 5 | 알림설정 | ✅ 완료 (알림음 파일 선택 + 볼륨 슬라이더) |
| 6 | 저장/불러오기 | ✅ 완료 (JSON 저장/불러오기 + 기본값 초기화) |
