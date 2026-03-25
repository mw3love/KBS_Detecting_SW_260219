# Phase 3 완료 기록

완료 일시: 2026-02-19

---

## Phase 3 완료된 작업 목록

### ui/dual_slider.py (완전 구현)
- [x] `DualSlider` — 두 핸들 드래그로 범위 선택하는 슬라이더 위젯
  - `paintEvent`: 그라디언트 배경 + 선택 범위 마스크 + 핸들(원형) 그리기
  - 마우스 드래그: 가장 가까운 핸들 자동 선택 → 이동
  - `gradient_type`: 'hue' (무지개) / 'saturation' (무채→채도) / 'value' (검정→흰) / 'gray'
  - `get_range()` / `set_range(low, high)` / `set_gradient_type(type)` API
  - `range_changed(int, int)` 시그널

### core/detector.py (확장)
- [x] **오디오 레벨미터 감지 파라미터 추가**
  - `audio_hsv_h_min/max` (0~179), `audio_hsv_s_min/max`, `audio_hsv_v_min/max` (0~255)
  - `audio_pixel_ratio`: HSV 범위 픽셀 비율 임계값 (%) — 이 비율 이상이면 레벨미터 활성
  - `audio_level_duration`: 비활성 지속 시간(초) → 알림 발생
  - `audio_level_alarm_duration`: 알림 지속 시간(초)
- [x] `detect_audio_roi(frame, audio_rois)` 메서드 추가
  - 오디오 ROI를 BGR→HSV 변환 후 `cv2.inRange()`로 마스크
  - 마스크 픽셀 비율 계산 → 활성/비활성 판단
  - 비활성이 `audio_level_duration`초 지속되면 alerting=True
- [x] **임베디드 오디오 감지 파라미터 추가**
  - `embedded_silence_threshold`: 무음 판단 dB (-60~0, 권장 -50)
  - `embedded_silence_duration`: 무음 지속 시간(초) → 알림 발생
  - `embedded_alarm_duration`: 알림 지속 시간(초)
  - `embedded_alerting`: 현재 알림 발생 여부 (property)
- [x] `update_embedded_silence(silence_seconds)` 메서드 추가
  - AudioMonitorThread.silence_detected 시그널에서 받은 무음 시간으로 상태 업데이트
  - `embedded_silence_duration` 초과 시 `embedded_alerting = True`
- [x] `reset_embedded_silence()` 메서드 추가
- [x] `reset_all()`에 오디오 레벨미터 상태 + 임베디드 오디오 상태 초기화 추가
- [x] `import cv2` 추가 (HSV 변환에 사용)

### ui/settings_dialog.py (탭 4 확장)
- [x] `from ui.dual_slider import DualSlider` import 추가
- [x] `QScrollArea` import 추가 (탭 내용이 길어져 스크롤 지원)
- [x] **탭 4를 QScrollArea로 감쌈** (블랙+스틸+레벨미터+임베디드 전체)
- [x] **오디오 레벨미터 감지 그룹 추가**
  - H 범위 DualSlider (hue 그라디언트), S 범위 DualSlider (saturation), V 범위 DualSlider (value)
  - 각 슬라이더 옆에 현재 값 레이블 (예: "40 ~ 80")
  - 감지 픽셀 비율(%) `_NumEdit`, 비활성 지속 시간(초) `_NumEdit`, 알림 지속시간(초) `_NumEdit`
  - `_on_hsv_changed()`: 슬라이더 변경 시 값 레이블 갱신 + 즉시 `_save_detection_params()`
- [x] **임베디드 오디오 감지 그룹 추가**
  - 무음 임계값(dB) `_NumEdit`(-60~0), 알림 발생 시간(초) `_NumEdit`, 알림 지속시간(초) `_NumEdit`
- [x] `_load_config()` — 새 파라미터(HSV, 레벨미터, 임베디드) 로드 추가
- [x] `_get_current_detection_params()` — 새 파라미터 반환에 포함
- [x] `detection_params_changed` 시그널에 전체 파라미터 포함

### ui/main_window.py (연결)
- [x] `_embedded_log_sent` 플래그 추가 (임베디드 로그 중복 방지)
- [x] `_start_threads()`: `level_updated` → `_on_audio_level_for_silence` 연결
- [x] `_start_threads()`: `silence_detected` → `_on_embedded_silence` 연결
- [x] `_run_detection()` 리팩터링
  - 비디오 ROI: `if video_rois:` 가드 추가
  - 오디오 ROI: `detect_audio_roi()` 호출 + 알림/로그 처리
  - 오디오 ROI 알림 로그: `{매체명}({라벨}) - 레벨미터 비활성 {초}초`
- [x] `_update_summary()`: EA 카운트를 `self._detector.embedded_alerting`으로 실제 연동
- [x] `_apply_detection_params()`: 오디오 레벨미터 + 임베디드 파라미터 반영
- [x] `_apply_detection_config()`: 오디오 레벨미터 + 임베디드 파라미터 로드
- [x] `_on_embedded_silence(silence_seconds)` 메서드 추가
  - `_detector.update_embedded_silence()` 호출
  - 알림 최초 발생 시 로그 + `_alarm.trigger("무음", "EA", ...)`
- [x] `_on_audio_level_for_silence(l_db, r_db)` 메서드 추가
  - 평균 dB > `embedded_silence_threshold`이면 임베디드 감지 리셋
  - 정상 복구 시 로그 + `_alarm.resolve("무음", "EA")`

### ui/video_widget.py (색상 수정)
- [x] 오디오 ROI 테두리 색상 변경: 초록(BGR 0,200,0) → **파란색(BGR 200,120,0)**
  - `normal_color`: (200, 120, 0) — 파란색
  - `alert_color`: (255, 150, 0) — 밝은 파란색
  - `fill_color`: (180, 100, 0) — 알림 채우기
  - CLAUDE.md 색상 원칙(초록 금지) 준수

### config/default_config.json (업데이트)
- [x] 구 키 제거: `audio_silence_duration`, `audio_check_interval`, `embedded_check_interval`, `hsv_*`
- [x] 블랙/스틸 alarm_duration 키 추가: `black_alarm_duration`, `still_alarm_duration`
- [x] 오디오 레벨미터 키 추가: `audio_hsv_h_min/max`, `audio_hsv_s_min/max`, `audio_hsv_v_min/max`, `audio_pixel_ratio`, `audio_level_duration`, `audio_level_alarm_duration`
- [x] 임베디드 오디오 키 추가: `embedded_silence_threshold`, `embedded_silence_duration`, `embedded_alarm_duration`
- [x] 전체 수치를 float → int로 통일

---

## 현재 파일 상태

```
kbs_monitor/
├── main.py                  ✅ 완전 구현
├── config/
│   └── default_config.json  ✅ Phase 3 완료 (HSV + 임베디드 파라미터 추가)
├── ui/
│   ├── __init__.py
│   ├── main_window.py       ✅ Phase 3 완료 (오디오 ROI 감지 + EA 연동 + 임베디드 오디오)
│   ├── top_bar.py           ✅ Phase 2 이후 수정 완료
│   ├── video_widget.py      ✅ Phase 3 완료 (오디오 ROI 파란색으로 수정)
│   ├── log_widget.py        ✅ 완전 구현
│   ├── settings_dialog.py   ✅ Phase 3 완료 (탭 4 HSV/임베디드 UI 추가)
│   ├── roi_editor.py        ✅ Phase 2 완전 구현
│   └── dual_slider.py       ✅ Phase 3 완전 구현 (paintEvent + 드래그)
├── core/
│   ├── __init__.py
│   ├── video_capture.py     ✅ 완전 구현
│   ├── audio_monitor.py     ✅ 완전 구현
│   ├── roi_manager.py       ✅ 완전 구현
│   ├── detector.py          ✅ Phase 3 완료 (HSV 레벨미터 + 임베디드 오디오)
│   └── alarm.py             ✅ 완전 구현
├── utils/
│   ├── __init__.py
│   ├── config_manager.py    ✅ 완전 구현
│   └── logger.py            ✅ 완전 구현
└── resources/
    ├── sounds/
    └── styles/
        └── dark_theme.qss   ✅ Phase 2 스타일 완료
```

---

## 감지 동작 방식

### 오디오 레벨미터 감지 (HSV)
1. 오디오 ROI를 BGR→HSV로 변환
2. `cv2.inRange()`로 설정된 H/S/V 범위의 픽셀 추출
3. 마스크 픽셀 / 전체 픽셀 비율 계산
4. 비율 ≥ `audio_pixel_ratio`% → 레벨미터 활성(정상)
5. 비율 < `audio_pixel_ratio`% → 비활성
6. 비활성이 `audio_level_duration`초 이상 지속 → 알림 발생
7. 알림 해제 시 `_alarm.resolve("오디오", label)` 호출

### 임베디드 오디오 감지
1. `AudioMonitorThread.silence_detected(seconds)` 시그널 수신
2. `_detector.update_embedded_silence(seconds)` 호출
3. 무음이 `embedded_silence_duration`초 이상 → `embedded_alerting = True`
4. `level_updated`에서 평균 dB > `embedded_silence_threshold`이면 정상 복구 처리
5. 정상 복구 시 로그 + 알림 해제

### EA 카운트 연동
- `_update_summary()`에서 `self._detector.embedded_alerting` 값 사용
- `True`이면 TopBar EA 값 "1", `False`이면 "-" 표시

---

## 알려진 이슈 / 향후 개선 사항

- `alarm.py`의 `set_volume()`은 값만 저장, 실제 볼륨 제어 미구현
- HSV 권장값(H:40~80)은 초록 계열 레벨미터 기준 — 실제 장비의 색상에 맞게 조정 필요
- 오디오 ROI 감지에서 레벨미터 비활성 기준이 무음이 아닌 "색 없음"이므로,
  레벨미터 UI 색상이 다른 장비에서는 HSV 범위를 재설정해야 함

---

## 다음 단계 (Phase 4)

**목표:** 알림설정 탭 + 저장/불러오기 탭 구현

구현 예정:
1. `ui/settings_dialog.py` 탭 5 (알림설정)
   - 알림음 파일 선택 (블랙/스틸/오디오/임베디드 각각)
   - 볼륨 설정 (현재 TopBar 슬라이더와 연동)
2. `ui/settings_dialog.py` 탭 6 (저장/불러오기)
   - 설정 프리셋 저장/불러오기 (JSON)
   - ROI 설정 내보내기/가져오기

재개 프롬프트:
```
이 프로젝트의 CLAUDE.md를 읽고, PHASE*_COMPLETE.md 파일을 확인해서
현재 진행 상황을 파악해줘.
확인 후 중단된 지점부터 이어서 개발해줘.
```
