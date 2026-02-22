# Phase 2 완료 기록

완료 일시: 2026-02-18
최종 업데이트: 2026-02-19 (Phase 2 이후 추가 수정사항 반영)

---

## Phase 2 완료된 작업 목록

### core/roi_manager.py
- [x] `replace_video_rois(rois)` 메서드 추가 (편집기에서 일괄 교체)
- [x] `replace_audio_rois(rois)` 메서드 추가

### ui/roi_editor.py (완전 구현)
- [x] `ROIEditorCanvas` — 반화면/전체화면 공용 편집 캔버스
  - 마우스 드래그: 빈 곳 → 새 감지영역 추가
  - 마우스 드래그: ROI 몸통 → 이동
  - 마우스 드래그: 모서리/변 핸들 → 8방향 크기 조정
  - 키보드: 방향키(10px), Shift+방향키(1px), Ctrl+방향키(크기 10px), Ctrl+Shift+방향키(크기 1px), Delete
  - 프레임 좌표 ↔ 위젯 좌표 변환 (레터박스 고려)
  - 선택 ROI 핸들(8방향) 표시 + 핸들별 커서 변경
  - 감지영역 최대 크기(500×250) 클램프, 최소 크기(8px) 검증
  - `rois_changed()` 시그널 → MainWindow에서 ROIManager에 즉시 반영
- [x] `FullScreenROIEditor` — 전체화면 편집 다이얼로그 (구현 완료, 현재 미사용)
  - 왼쪽: ROIEditorCanvas
  - 오른쪽: ROI 테이블, 복사/삭제 버튼, 편집 완료 버튼
  - **현재 메인화면에서 연결 안 됨** (반화면 방식만 사용 중)

### ui/settings_dialog.py (탭 구조 완전 구현)
- [x] `ROIManager` 참조 생성자 파라미터로 수신
- [x] 비모달(non-modal) 다이얼로그 — ROI 편집 모드와 공존
- [x] **탭 1: 입력선택** — 포트 콤보박스(0~5), 선택 즉시 소스 변경
- [x] **탭 2: 비디오 감지 설정**
  - [반화면 편집] 토글 버튼 (ON: 편집 모드 진입 / OFF: 편집 종료)
  - 감지영역 테이블 (라벨, 매체명, X, Y, W, H) — 셀 더블클릭 편집
  - 복사 / ▲위로 / ▼아래로 / 전체 초기화 버튼
  - 키보드 단축키 도움말
- [x] **탭 3: 오디오 레벨미터 감지 설정**
  - 비디오와 동일 구조 (오디오 ROI 편집 + 테이블)
- [x] **탭 4: 감지 설정** (블랙/스틸 파라미터, 정수 입력, 권장값 표시)
  - 블랙 감지: 밝기 임계값(권장 10), 알림 발생 시간(권장 10초), 알림 지속시간(권장 10초)
  - 스틸 감지: 픽셀 차이 임계값(권장 2), 알림 발생 시간(권장 10초), 알림 지속시간(권장 10초)
  - `_NumEdit`: QSpinBox 위아래 버튼 없는 숫자 입력 위젯 (정수 전용)
  - editingFinished → 즉시 Detector에 반영
- [x] **탭 5: 알림설정** — 플레이스홀더 (Phase 4 예정)
- [x] **탭 6: 저장/불러오기** — 플레이스홀더 (Phase 4 예정)
- [x] `refresh_roi_tables()` — 편집 완료 후 테이블 갱신 API
- [x] 시그널: `port_changed`, `halfscreen_edit_requested`, `halfscreen_edit_finished`,
  `detection_params_changed`, `roi_selection_changed`

### ui/main_window.py (Phase 2 업데이트)
- [x] 설정 다이얼로그 비모달 싱글턴으로 변경
- [x] 프로그램 시작 시 ROI 설정 자동 로드 (`config["rois"]`)
- [x] 프로그램 종료 시 ROI 설정 자동 저장
- [x] **반화면 편집 모드**
  - 설정창을 열어둔 채 VideoWidget 위에 ROIEditorCanvas 오버레이 표시
  - 편집 중 감지 타이머 중단, 완료 시 재시작
  - rois_changed 시그널 → ROIManager 즉시 반영 → 설정창 테이블 갱신
  - 설정창 테이블 행 선택 ↔ 오버레이 ROI 선택 동기화
- [x] 감지 파라미터 변경 즉시 Detector에 반영
- [x] 알림 발생/정상복구 로그 기록 (중복 없이 첫 발생 시만)

### resources/styles/dark_theme.qss
- [x] ROI 편집기 관련 스타일 추가
  - `#btnHalfscreenEdit`: 반화면 편집 버튼
  - `QTableWidget`, `QHeaderView::section`: 테이블 스타일
  - `#roiHelpLabel`, `#roiTableLabel`: 도움말/라벨
  - `#btnMoveRow`: 복사/이동/초기화 버튼
  - `#paramDescLabel`: 감지 파라미터 설명 텍스트

---

## Phase 2 이후 추가 수정사항 (2026-02-19)

### ui/top_bar.py 대폭 수정
- [x] **감지 On/Off 버튼 제거** → 프로그램 실행 즉시 항상 감지 활성
  - `is_monitoring_active()` 항상 `True` 반환
  - `monitoring_toggled` Signal은 정의만 유지 (미연결)
- [x] **SysMonitorWidget 추가** (감지 버튼 자리 대체)
  - CPU%, RAM%, GPU% 실시간 표시 (2초 갱신)
  - psutil 우선, GPUtil(NVIDIA) → nvidia-smi 직접 경로 순 GPU 탐지
  - 3개 경로 시도: `nvidia-smi`, `Program Files\NVSMI\nvidia-smi.exe`, `System32\nvidia-smi.exe`
  - GPU 없으면 N/A 표시
- [x] **시계 섹션 개선**
  - "현재시간" 소제목 레이블 추가 (시간값 위)
  - 시간값 폰트: Segoe UI 11 Bold
- [x] **UI 폰트 통일** (Segoe UI 기준)
  - 소제목(시스템 성능, 현재시간, 감지현황): 9 Bold
  - 수치값(CPU/RAM/GPU%, 시각, V/A/EA): 11 Bold
  - 감지현황 V/A/EA 값: 17 Bold → 11 Bold로 축소

### ui/main_window.py 수정
- [x] `monitoring_toggled` 시그널 연결 제거
- [x] `_on_monitoring_toggled` 메서드 제거
- [x] `_on_frame_ready`: `is_monitoring_active()` 체크 제거 (항상 활성)
- [x] `_run_detection`: `is_monitoring_active()` 체크 제거
- [x] `_finish_halfscreen_edit`: `is_monitoring_active()` 체크 제거

### ui/settings_dialog.py 수정 (탭 4: 감지 설정)
- [x] 지속시간 입력값 소수점 제거 → **정수 전용** (`_NumEdit(10, 1, 300)`)
  - 대상: 블랙 알림 발생 시간, 블랙 알림 지속시간, 스틸 알림 발생 시간, 스틸 알림 지속시간
- [x] 각 임계값 설명란에 **권장값 명시**
  - 밝기 임계값: `(권장: 10)`, 픽셀 차이 임계값: `(권장: 2)`
  - 알림 발생/지속 시간: `(권장: 10초)`
- [x] `_load_config`: 저장된 float 값을 int로 변환하여 표시

### 설치된 외부 패키지
- [x] `psutil 7.2.2` 설치
- [x] `gputil 1.4.0` 설치
- [x] `sounddevice 0.5.5` 설치 (pyaudio 대체 — Python 3.10+ 모든 환경 pip 한 줄 설치 가능)

---

## 현재 파일 상태

```
kbs_monitor/
├── main.py                  ✅ 완전 구현
├── config/
│   └── default_config.json  ✅ 완전 구현
├── ui/
│   ├── __init__.py
│   ├── main_window.py       ✅ Phase 2 + 이후 수정 완료
│   ├── top_bar.py           ✅ Phase 2 + 이후 수정 완료 (SysMonitor, 폰트, 시계 소제목)
│   ├── video_widget.py      ✅ 완전 구현
│   ├── log_widget.py        ✅ 완전 구현
│   ├── settings_dialog.py   ✅ Phase 2 + 이후 수정 완료 (정수 입력, 권장값)
│   ├── roi_editor.py        ✅ 완전 구현 (FullScreenROIEditor는 미사용)
│   └── dual_slider.py       ⬜ Phase 3 완전 구현 예정
├── core/
│   ├── __init__.py
│   ├── video_capture.py     ✅ 완전 구현
│   ├── audio_monitor.py     ✅ 완전 구현
│   ├── roi_manager.py       ✅ 완전 구현
│   ├── detector.py          🔶 블랙/스틸 완료, 레벨미터/임베디드 Phase 3 예정
│   └── alarm.py             ✅ 완전 구현
├── utils/
│   ├── __init__.py
│   ├── config_manager.py    ✅ 완전 구현
│   └── logger.py            ✅ 완전 구현
└── resources/
    ├── sounds/
    └── styles/
        └── dark_theme.qss   ✅ Phase 2 스타일 추가 완료
```

---

## 다음 단계 (Phase 3)

**목표:** 오디오 레벨미터(HSV) 감지 + 임베디드 오디오 무음/끊김 감지

구현 예정:
1. `ui/dual_slider.py` 완전 구현 — HSV 범위 선택 듀얼 핸들 슬라이더 (paintEvent, 드래그 UI)
2. `core/detector.py` 확장 — HSV 기반 레벨미터 색상 감지 로직 추가
3. `core/detector.py` 확장 — 임베디드 오디오 무음/끊김 감지 로직 추가
   - `AudioMonitorThread.silence_detected` 시그널 활용
4. `ui/settings_dialog.py` 탭 3 HSV 설정 UI 구현 (DualSlider 사용)
5. `ui/settings_dialog.py` 탭 4 임베디드 오디오 설정 UI 구현
6. `ui/main_window.py` 오디오/임베디드 감지 결과 연결 + 알림 처리
7. 감지현황 EA 수치 실제 연동 (현재 하드코딩 "-")

알려진 이슈:
- `video_widget.py`의 오디오 ROI 색상이 초록(BGR: 0,200,0)으로 표시됨 → CLAUDE.md 색상 원칙(초록 금지)과 상충, Phase 3에서 수정 필요
- `alarm.py`의 `set_volume()`은 값만 저장, 실제 볼륨 제어 미구현

재개 프롬프트:
```
이 프로젝트의 CLAUDE.md를 읽고, PHASE*_COMPLETE.md 파일을 확인해서
현재 진행 상황을 파악해줘.
확인 후 중단된 지점부터 이어서 개발해줘.
```
