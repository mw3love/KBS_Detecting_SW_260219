# Phase 4 완료 기록

완료 일시: 2026-02-20

---

## Phase 4 완료된 작업 목록

### core/alarm.py (확장)
- [x] `_sound_files: dict` 속성 추가 — `{alarm_type: 개별 절대 경로}` 저장
- [x] `set_sound_file(alarm_type, path)` 메서드 추가 — 개별 알림음 파일 경로 설정
- [x] `get_sound_files()` 메서드 추가 — 현재 개별 경로 dict 반환
- [x] `play_test_sound(alarm_type)` 메서드 추가 — 3초짜리 테스트 재생
- [x] `_get_sound_path(alarm_type)` 메서드 추가 — 개별 경로 우선, 없으면 sounds_dir 폴백
- [x] `_play_sound_worker` 리팩터링 — `_get_sound_path()` 사용으로 단순화

### utils/config_manager.py (업데이트)
- [x] `DEFAULT_CONFIG` 전면 업데이트
  - detection 하위 키: Phase 3 기준 최신 키로 통일 (구 키 hsv_*, audio_silence_duration 등 제거)
  - alarm 하위 `sound_files` 추가: `{black: "", still: "", audio: "", default: ""}`
- [x] `save_to_path(config, abs_path)` 메서드 추가 — 절대 경로로 저장
- [x] `load_from_path(abs_path)` 메서드 추가 — 절대 경로에서 불러오기

### config/default_config.json (업데이트)
- [x] `alarm.sound_files` 키 추가: `{black: "", still: "", audio: "", default: ""}`

### ui/top_bar.py (확장)
- [x] `set_volume_display(value)` 메서드 추가 — 외부에서 슬라이더 값 조정 (시그널 없이)

### ui/settings_dialog.py (탭 5·6 구현)
- [x] imports 추가: `os`, `QSlider`, `QFileDialog`, `QMessageBox`
- [x] 새 시그널 추가:
  - `alarm_settings_changed(dict)` — 알림 설정 변경 (sound_files, volume)
  - `test_sound_requested(str)` — 테스트 알림음 재생 요청
  - `save_config_requested(str)` — 설정 저장 요청 (절대경로)
  - `load_config_requested(str)` — 설정 불러오기 요청 (절대경로)
  - `reset_config_requested()` — 기본값 초기화 요청
- [x] **탭 5 (알림설정) 구현** — `_create_tab_alarm()`
  - 알림음 파일 그룹: 4개 행 (블랙/스틸/오디오/임베디드·기본)
    - 각 행: 라벨 + 경로 표시 QLineEdit(ReadOnly) + [찾아보기] + [초기화] + [테스트]
    - [찾아보기]: WAV 파일 선택 다이얼로그
    - [초기화]: 경로 비워서 기본값으로 복귀
    - [테스트]: 해당 알림음 3초 재생
  - 볼륨 설정 그룹: QSlider (0~100) + 값 레이블
    - TopBar 볼륨 슬라이더와 양방향 동기화
    - ※ 음소거는 상단 바 버튼 사용 안내문 표시
- [x] **탭 6 (저장/불러오기) 구현** — `_create_tab_save_load()`
  - [현재 설정 저장...]: QFileDialog.getSaveFileName → `save_config_requested` 발송
  - [설정 파일 불러오기...]: QFileDialog.getOpenFileName → `load_config_requested` 발송
  - [기본값으로 초기화]: QMessageBox 확인 후 `reset_config_requested` 발송
- [x] `_load_alarm_config(config)` 메서드 — 알림 설정을 UI에 반영
- [x] `_load_config()` 마지막에 `_load_alarm_config()` 호출 추가
- [x] `set_alarm_volume(value)` 메서드 — 볼륨 슬라이더 외부 동기화
- [x] `reload_config(config)` 메서드 — 외부 설정 변경 시 전체 UI 갱신 (`_load_config` 재호출)

### ui/main_window.py (연결)
- [x] `import copy` 추가
- [x] `DEFAULT_CONFIG` import 추가 (초기화 시 사용)
- [x] `__init__`: AlarmSystem 생성 직후 `_apply_alarm_config()` 호출
- [x] `__init__`: `_start_threads()` 후 TopBar 볼륨 초기값 동기화
- [x] `_open_settings()`: 새 시그널 연결
  - `alarm_settings_changed` → `_on_alarm_settings_changed`
  - `test_sound_requested` → `alarm.play_test_sound`
  - `save_config_requested` → `_on_save_config`
  - `load_config_requested` → `_on_load_config`
  - `reset_config_requested` → `_on_reset_config`
- [x] `_apply_alarm_config(alarm_dict)` 메서드 추가
  - AlarmSystem에 sound_enabled / volume / sound_files 일괄 적용
- [x] `_on_alarm_settings_changed(params)` 메서드 추가
  - AlarmSystem 업데이트 + TopBar 볼륨 동기화
- [x] `_on_volume_changed()` 수정
  - AlarmSystem 업데이트 + `self._config` 갱신 + SettingsDialog 볼륨 동기화
- [x] `_on_save_config(filepath)` 메서드 추가
  - 전체 config(rois 포함) 수집 후 `config_manager.save_to_path()` 호출
  - 결과를 로그에 기록
- [x] `_on_load_config(filepath)` 메서드 추가
  - `config_manager.load_from_path()` 호출
  - ROIManager / Detector / AlarmSystem / TopBar 전체 적용
  - SettingsDialog `reload_config()` 호출
- [x] `_on_reset_config()` 메서드 추가
  - `DEFAULT_CONFIG` 깊은 복사 후 전체 적용
  - ROI 전부 삭제, Detector 리셋, AlarmSystem 리셋
  - SettingsDialog `reload_config()` 호출

---

## 현재 파일 상태

```
kbs_monitor/
├── main.py                  ✅ 완전 구현
├── config/
│   └── default_config.json  ✅ Phase 4 완료 (alarm.sound_files 추가)
├── ui/
│   ├── __init__.py
│   ├── main_window.py       ✅ Phase 4 완료 (저장/불러오기/초기화 + 알림 설정)
│   ├── top_bar.py           ✅ Phase 4 완료 (set_volume_display 추가)
│   ├── video_widget.py      ✅ Phase 3 완료
│   ├── log_widget.py        ✅ 완전 구현
│   ├── settings_dialog.py   ✅ Phase 4 완료 (탭 5·6 구현)
│   ├── roi_editor.py        ✅ Phase 2 완전 구현
│   └── dual_slider.py       ✅ Phase 3 완전 구현
├── core/
│   ├── __init__.py
│   ├── video_capture.py     ✅ 완전 구현
│   ├── audio_monitor.py     ✅ 완전 구현
│   ├── roi_manager.py       ✅ 완전 구현
│   ├── detector.py          ✅ Phase 3 완료
│   └── alarm.py             ✅ Phase 4 완료 (개별 파일 경로 + 테스트 재생)
├── utils/
│   ├── __init__.py
│   ├── config_manager.py    ✅ Phase 4 완료 (DEFAULT_CONFIG 정리 + 절대경로 메서드)
│   └── logger.py            ✅ 완전 구현
└── resources/
    ├── sounds/
    └── styles/
        └── dark_theme.qss   ✅ Phase 2 스타일 완료
```

---

## 알림음 파일 우선순위

1. **개별 파일 경로** (설정창 탭 5에서 직접 지정한 경로) — 파일 존재 시 최우선
2. **sounds_dir 내 타입별 파일** (`black_alarm.wav`, `still_alarm.wav`, `audio_alarm.wav`)
3. **sounds_dir 내 기본 파일** (`alarm.wav`)

---

## 볼륨 슬라이더 동기화 흐름

```
TopBar 슬라이더 변경
  → MainWindow._on_volume_changed(v)
  → AlarmSystem.set_volume(v/100)
  → SettingsDialog.set_alarm_volume(v)  [탭 5 슬라이더 동기화]

SettingsDialog 탭 5 슬라이더 변경
  → alarm_settings_changed 시그널
  → MainWindow._on_alarm_settings_changed(params)
  → AlarmSystem.set_volume(v/100)
  → TopBar.set_volume_display(v)        [TopBar 슬라이더 동기화]
```

---

## 알려진 이슈 / 향후 개선 사항

- 설정 저장 시 파일 경로를 입력창으로 직접 편집하는 기능 없음 (파일 다이얼로그만 제공)
- TopBar 음소거 버튼 ↔ SettingsDialog 탭 5 간 음소거 상태 동기화 없음
  (TopBar 음소거 → AlarmSystem.set_sound_enabled, 탭 5에는 별도 체크박스 없음)
- sounds_dir 변경 기능 없음 (현재 `resources/sounds` 고정)

---

## 탭별 구현 현황 (최종)

| 탭 | 이름 | 구현 상태 |
|----|------|----------|
| 1 | 입력선택 | ✅ 완료 |
| 2 | 비디오 감지 설정 | ✅ 완료 |
| 3 | 오디오 레벨미터 감지 설정 | ✅ 완료 |
| 4 | 감지 설정 | ✅ 완료 |
| 5 | 알림설정 | ✅ Phase 4 완료 |
| 6 | 저장/불러오기 | ✅ Phase 4 완료 |

---

재개 프롬프트:
```
이 프로젝트의 CLAUDE.md를 읽고, PHASE*_COMPLETE.md 파일을 확인해서
현재 진행 상황을 파악해줘.
확인 후 중단된 지점부터 이어서 개발해줘.
```

---

# Phase 5 완료 기록 — 코드 최적화

완료 일시: 2026-02-21

## 최적화 작업 목록

### core/detector.py

- [x] **`_apply_scale_factor()` 공통 메서드 추가**
  - `detect_frame()`과 `detect_audio_roi()` 양쪽에서 동일하게 반복되던 스케일 로직을 하나의 메서드로 통합
  - `scale_factor < 1.0` 일 때만 `cv2.resize` 수행, 그 외는 원본 반환
- [x] **`update_roi_list()` 메모리 누수 수정 (버그 수정)**
  - `_audio_ratio_buffer` (이동 평균 deque) 가 ROI 삭제 후에도 정리되지 않던 문제 해결
  - `_audio_level_states` (DetectionState) 도 동일하게 미정리 → 함께 수정
  - ROI 삭제/재생성이 반복될 때 메모리가 점진적으로 증가하는 문제 방지

### ui/main_window.py

- [x] **ROI 선형 탐색 → dict 캐시로 교체 (성능 개선)**
  - `_run_detection()` 에서 매 감지 루프마다 `next((r for r in rois if r.label == label), None)` 패턴으로 O(n) 탐색하던 부분 제거
  - 루프 시작 전 `{r.label: (r.media_name or r.label) for r in rois}` dict를 한 번 생성, O(1) 접근으로 변경
  - 비디오 ROI / 오디오 ROI 양쪽 모두 적용

### ui/settings_dialog.py

- [x] **감지 파라미터 UI 적용 로직 DRY화 (중복 제거)**
  - `_load_config()`와 `_reset_detection_params_to_default()`에서 동일한 ~50줄 감지 파라미터 UI 반영 코드가 두 번 존재하던 문제 해결
  - `_apply_detection_params_to_ui(det: dict)` 공통 메서드 추출
  - `_apply_performance_params_to_ui(perf: dict)` 공통 메서드 추출
  - 두 메서드를 `_load_config()` 및 `_reset_detection_params_to_default()` 양쪽에서 호출
  - `_reset_detection_params_to_default()` 가 4줄로 대폭 단축됨
- [x] **메서드 내 지연 import 제거**
  - `_run_benchmark()` 내부에 있던 `import time, numpy, cv2, psutil` 4개를 파일 상단 import 블록으로 이동

### core/audio_monitor.py

- [x] **루프 내 CHANNELS 상수 조건 분기 제거 (마이크로 최적화)**
  - `while` 루프 내에서 매 청크마다 `if self.CHANNELS == 2:` 를 평가하던 코드를 `__init__`에서 `self._stereo = (self.CHANNELS == 2)` 로 한 번만 결정
  - 루프 내 분기는 `if self._stereo:` 로 변경 (의미는 동일, 상수 비교 제거)

---

## 최적화 작업 분류

| 파일 | 종류 | 효과 |
|------|------|------|
| `core/detector.py` | 버그 수정 (메모리 누수) | ROI 추가/삭제 반복 시 메모리 증가 방지 |
| `core/detector.py` | 코드 중복 제거 | `_apply_scale_factor()` 공통화 |
| `ui/main_window.py` | 성능 개선 | O(n)→O(1) ROI 이름 조회 |
| `ui/settings_dialog.py` | 코드 중복 제거 (DRY) | ~100줄 중복 → 공통 메서드 추출 |
| `ui/settings_dialog.py` | 코드 정리 | 지연 import → 상단 import |
| `core/audio_monitor.py` | 마이크로 최적화 | 루프 내 상수 조건 분기 제거 |

---

## 최종 파일 상태 (Phase 5 완료 후)

```
kbs_monitor/
├── main.py                  ✅ 완전 구현 (변경 없음)
├── config/
│   └── default_config.json  ✅ Phase 4 완료 (변경 없음)
├── ui/
│   ├── __init__.py
│   ├── main_window.py       ✅ Phase 5 완료 (ROI dict 캐시)
│   ├── top_bar.py           ✅ Phase 4 완료 (변경 없음)
│   ├── video_widget.py      ✅ Phase 3 완료 (변경 없음)
│   ├── log_widget.py        ✅ 완전 구현 (변경 없음)
│   ├── settings_dialog.py   ✅ Phase 5 완료 (DRY화 + import 정리)
│   ├── roi_editor.py        ✅ Phase 2 완전 구현 (변경 없음)
│   └── dual_slider.py       ✅ Phase 3 완전 구현 (변경 없음)
├── core/
│   ├── __init__.py
│   ├── video_capture.py     ✅ 완전 구현 (변경 없음)
│   ├── audio_monitor.py     ✅ Phase 5 완료 (루프 최적화)
│   ├── roi_manager.py       ✅ 완전 구현 (변경 없음)
│   ├── detector.py          ✅ Phase 5 완료 (메모리 누수 수정 + 스케일 공통화)
│   └── alarm.py             ✅ Phase 4 완료 (변경 없음)
├── utils/
│   ├── __init__.py
│   ├── config_manager.py    ✅ Phase 4 완료 (변경 없음)
│   └── logger.py            ✅ 완전 구현 (변경 없음)
└── resources/
    ├── sounds/
    └── styles/
        └── dark_theme.qss   ✅ Phase 2 스타일 완료 (변경 없음)
```
