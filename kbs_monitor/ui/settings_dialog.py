"""
설정 다이얼로그
6개 탭: 영상설정(입력선택+자동녹화), 비디오 영역 설정, 오디오 레벨미터 영역 설정,
        감도설정, 알림설정(알림음+텔레그램), 저장/불러오기
모든 변경값은 저장 버튼 없이 즉시 적용됨
"""
import os
import time
import numpy as np
import cv2
import psutil

from utils.config_manager import DEFAULT_CONFIG

from PySide6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QGroupBox,
    QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QGridLayout,
    QScrollArea, QFileDialog, QMessageBox, QCheckBox, QApplication,
    QTextBrowser,
)
from PySide6.QtCore import Qt, Signal

from core.roi_manager import ROIManager
from ui.dual_slider import DualSlider

# 버튼 높이 통일 상수
_BTN_H = 30

# ── 성능 설정 안내 마크다운 ──────────────────────────────────────────
_PERF_GUIDE_MD = """\
# 성능 설정 완전 가이드

감도설정 탭 → 성능 설정 섹션에 위치한 5가지 항목에 대한 상세 설명입니다.

---

## 1. 감지 주기

**선택 옵션:** 100ms / **200ms(기본값)** / 300ms / 500ms / 1000ms

### 기술적 설명

프로그램이 영상 이상 여부를 분석하는 내부 타이머 콜백 주기입니다.
100ms로 설정하면 초당 10회 분석하고, 1000ms이면 초당 1회 분석합니다.
블랙/스틸/오디오 감지 알고리즘은 이 주기마다 CPU 연산을 수행하므로,
주기가 짧을수록 CPU 사용량이 직접적으로 증가합니다.

### 쉬운 비유

> 경비원이 CCTV 화면을 얼마나 자주 확인하는지와 같습니다.
>
> - **100ms** = 0.1초마다 확인 → 매우 부지런한 경비원, 단 피로도 높음
> - **200ms** = 0.2초마다 확인 → 적당히 부지런한 경비원 (기본값)
> - **1000ms** = 1초마다 확인 → 여유 있게 확인, 컴퓨터 부하는 최소

### 실무 판단 기준

| 상황 | 기본값 설정 |
|------|-----------|
| PC가 이 프로그램 전용 | 100~200ms |
| 다른 프로그램도 함께 실행 | 200~300ms |
| 오래된 PC / 16채널 모두 사용 | 500ms |
| 알림 반응이 1~2초 늦어도 무방 | 1000ms |

**핵심:** "10초 이상 지속 시 알림" 기준이라면 **500ms까지도 안전**합니다.
0.5초 이내에 블랙/스틸을 감지하면 10초 판정에 충분히 대응 가능합니다.

---

## 2. 감지 해상도

**선택 옵션:** 원본 1920×1080 / 50% 960×540 / 25% 480×270

### 기술적 설명

OpenCV로 프레임을 분석할 때 **실제 처리 픽셀 수**를 줄이는 설정입니다.
50% 축소 시 가로·세로 각각 절반이므로 **처리 픽셀이 75% 감소**합니다(1/4 크기).
블랙 감지(밝기 평균)나 스틸 감지(프레임 차이)는 해상도에 무관하게
감지 정확도가 거의 동일하게 유지됩니다.

### 쉬운 비유

> 사진을 검사할 때 원본 대신 **썸네일**로 확인하는 것입니다.
>
> 화면 전체가 까맣게 변했는지 확인하는 데 굳이 초고화질이 필요 없습니다.
> 작은 썸네일로 봐도 "까맣다"는 사실은 똑같이 알 수 있습니다.

### 처리 픽셀 비교

| 설정 | 픽셀 수 | CPU 절감 효과 |
|------|---------|--------------|
| 원본 (100%) | 2,073,600개 | 기준 |
| 50% | 518,400개 | **약 75% 감소** |
| 25% | 129,600개 | **약 94% 감소** |

**핵심:** 감지 정확도 손실 없이 CPU를 아낄 수 있는 **가장 안전한 절감 방법**입니다.
PC 성능이 낮다면 50%부터 먼저 시도해보세요.

---

## 3. 비디오 감지 (블랙/스틸 감지 활성화)

**타입:** 체크박스 (On/Off)

### 기술적 설명

체크 해제 시 블랙 감지와 스틸 감지 코드 블록 전체가 실행되지 않습니다.
프레임 전처리(그레이스케일 변환, 픽셀 평균 계산, 이전 프레임과의 차이 비교)가 모두 생략됩니다.

### 쉬운 비유

> 영상 담당 경비원을 **아예 비번으로 돌리는** 것입니다.
>
> 오디오 감지(소리)만 필요하고 영상 이상은 감지할 필요가 없는 경우,
> 영상 담당 경비원의 업무를 완전히 중단시켜 인력(CPU)을 절감합니다.

### 언제 비활성화하나요?

- 오디오 레벨미터 감지만 운용하는 채널
- 테스트 중 특정 감지만 확인하고 싶을 때
- 극단적인 CPU 절감이 필요한 경우

> **주의:** 비활성화 시 블랙 화면과 정지 영상 모두 감지되지 않습니다.

---

## 4. 오디오 레벨미터 감지 (HSV 색상 감지 활성화)

**타입:** 체크박스 (On/Off)

### 기술적 설명

**가장 CPU 부하가 큰 연산** 중 하나인 BGR→HSV 색공간 변환을 생략합니다.
HSV 변환 호출과 이후 HSV 마스크 생성, 픽셀 비율 계산 전체가 건너뛰어집니다.
1920×1080 프레임의 전체 HSV 변환은 픽셀당 부동소수 연산이 많아 비교적 무거운 작업입니다.

### 쉬운 비유

> 오디오 레벨미터 화면에 초록색 막대가 있는지 확인하는 **색깔 분석 전담 직원**을 비번으로 돌리는 것입니다.
>
> 이 직원은 매 주기마다 화면 전체를 "초록색 픽셀 찾기" 작업을 수행합니다.
> 오디오 레벨미터를 감지할 필요가 없다면 이 작업 자체를 아예 생략할 수 있습니다.

### 왜 이게 가장 효과적인가?

    BGR → HSV 변환:  프레임당 200~300만 픽셀의 색공간 수학 계산
    마스크 생성:      모든 픽셀에 대한 범위 비교 연산
    픽셀 비율 계산:   카운팅 연산
    → 이 세 단계를 한 번에 생략 = 감지 주기당 가장 큰 폭의 부하 절감

**핵심:** "비활성화 시 HSV 전체변환 생략 — 가장 효과적인 부하 절감" 옵션입니다.

---

## 5. 스틸 감지 (정지 영상 감지 활성화)

**타입:** 체크박스 (On/Off)

### 기술적 설명

스틸 감지는 **이전 프레임과 현재 프레임의 픽셀 차이(absdiff)를 비교**하는 연산입니다.
체크 해제 시 이전 프레임 저장(메모리)과 차이값 계산, 픽셀 평균 계산이 모두 생략됩니다.
블랙 감지(밝기 평균만 계산)보다 연산량이 많습니다.

### 쉬운 비유

> 오늘 찍은 사진과 어제 찍은 사진을 **나란히 놓고 달라진 부분을 찾는** 작업입니다.
>
> 이 비교 작업을 할 필요가 없다면(예: 정지 화면은 괜찮은 채널)
> 굳이 매 주기마다 두 사진을 비교할 필요가 없습니다.

### 블랙 감지 vs 스틸 감지 차이

| | 블랙 감지 | 스틸 감지 |
|--|----------|----------|
| **감지 대상** | 화면이 어두운지 | 화면이 멈췄는지 |
| **연산 방식** | 현재 프레임 밝기 평균 계산 | 현재 + 이전 프레임 차이 비교 |
| **추가 메모리** | 없음 | 이전 프레임 저장 필요 |
| **CPU 부하** | 낮음 | 중간 |

**팁:** 블랙 감지만 필요하다면 스틸 감지를 끄는 것만으로도 의미 있는 부하 절감이 가능합니다.

---

## 자동 성능 감지 버튼

버튼을 누르면 프로그램이 자동으로 **실제 PC 성능을 벤치마크**한 후
감지 주기와 해상도를 적절히 설정해줍니다.

| 측정 결과 | PC 등급 | 자동 설정 |
|-----------|---------|----------|
| 20ms 미만 | 고성능 | 100ms / 원본 해상도 |
| 20~50ms | 표준 | 200ms / 원본 해상도 |
| 50~100ms | 중간 | 300ms / 50% 해상도 |
| 100ms 이상 | 저사양 | 500ms / 50% 해상도 |

---

## 한눈에 보는 성능 조절 우선순위

CPU 부하가 높을 때 조절 순서:

1. **감지 해상도를 50%로 낮추기** — 정확도 손실 없이 75% 절감
2. **오디오 레벨미터 감지 비활성화** — 가장 무거운 연산 생략
3. **감지 주기를 300~500ms로 늘리기** — 알림 지연 최소화하며 절감
4. **스틸 감지 비활성화** — 프레임 비교 연산 생략
5. **비디오 감지 비활성화** — 영상 이상 감지 완전 중단
"""


class _NumEdit(QLineEdit):
    """숫자 입력용 QLineEdit (QSpinBox 위아래 버튼 대체)"""

    def __init__(self, value, min_val, max_val, is_float=False, parent=None):
        super().__init__(str(value), parent)
        self._min = min_val
        self._max = max_val
        self._is_float = is_float
        self.setFixedWidth(90)
        self.setAlignment(Qt.AlignRight)

    def get_value(self):
        try:
            v = float(self.text()) if self._is_float else int(self.text())
            return max(self._min, min(self._max, v))
        except ValueError:
            return self._min


class PerformanceGuideDialog(QDialog):
    """성능 설정 안내 다이얼로그 — 마크다운 형식으로 표시"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("성능 설정 안내")
        self.setMinimumSize(820, 700)
        self.resize(900, 760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setMarkdown(_PERF_GUIDE_MD)
        browser.setObjectName("perfGuideBrowser")
        layout.addWidget(browser, 1)

        btn_close = QPushButton("닫기")
        btn_close.setFixedHeight(_BTN_H)
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)


class _ROITable(QTableWidget):
    """DEL 키 삭제 지원 ROI 테이블 (다중 선택 삭제 지원)"""

    rows_delete_requested = Signal(list)  # 삭제할 행 목록

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            rows = sorted(set(item.row() for item in self.selectedItems()), reverse=True)
            if rows:
                self.rows_delete_requested.emit(rows)
        else:
            super().keyPressEvent(event)


class SettingsDialog(QDialog):
    """설정 다이얼로그"""

    port_changed = Signal(int)
    video_file_changed = Signal(str)          # MP4 파일 소스 변경 (빈 문자열=포트 사용)
    halfscreen_edit_requested = Signal(str)   # "video" or "audio" (편집 시작)
    halfscreen_edit_finished = Signal()        # 편집 완료
    detection_params_changed = Signal(dict)
    performance_params_changed = Signal(dict)
    roi_selection_changed = Signal(str, int)  # (roi_type, row_idx) 테이블 행 선택 동기화
    roi_list_changed = Signal(str)            # ROI 목록 변경 시 반화면 편집 캔버스 갱신용 ("video"/"audio")
    alarm_settings_changed = Signal(dict)     # 알림 설정 변경 (sound_files, volume)
    test_sound_requested = Signal(str)        # 테스트 알림음 재생 요청 (alarm_type)
    telegram_settings_changed = Signal(dict)  # 텔레그램 설정 변경
    telegram_test_requested = Signal(str, str)  # (token, chat_id) 연결 테스트
    recording_settings_changed = Signal(dict) # 자동 녹화 설정 변경
    save_config_requested = Signal(str)       # 설정 저장 요청 (절대경로)
    load_config_requested = Signal(str)       # 설정 불러오기 요청 (절대경로)
    reset_config_requested = Signal()         # 기본값 초기화 요청

    def __init__(self, config: dict, roi_manager: ROIManager, parent=None):
        super().__init__(parent)
        self._config = dict(config)
        self._roi_manager = roi_manager
        self.setWindowTitle("설정")
        self.setMinimumWidth(720)
        self.setMinimumHeight(640)
        self.setModal(False)
        self._setup_ui()
        self._load_config(config)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._create_tab_input(),             "영상설정")
        self._tabs.addTab(self._create_tab_video_roi(),         "비디오 영역 설정")
        self._tabs.addTab(self._create_tab_audio_roi(),         "오디오 레벨미터 영역 설정")
        self._tabs.addTab(self._create_tab_detection_params(),  "감도설정")
        self._tabs.addTab(self._create_tab_alarm(),             "알림설정")
        self._tabs.addTab(self._create_tab_save_load(),         "저장/불러오기")

    # ── 탭 1: 입력선택 ────────────────────────────────

    def _create_tab_input(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── 캡처 포트 그룹 ──
        group = QGroupBox("캡처 포트")
        inner = QHBoxLayout(group)

        lbl = QLabel("포트 번호:")
        lbl.setAlignment(Qt.AlignVCenter)
        inner.addWidget(lbl)

        self._combo_port = QComboBox()
        for i in range(6):
            self._combo_port.addItem(str(i), i)
        self._combo_port.setFixedWidth(80)
        self._combo_port.setToolTip("0~5 고정 선택 / 선택 즉시 소스 변경")
        self._combo_port.currentIndexChanged.connect(self._on_port_changed)
        inner.addWidget(self._combo_port)
        inner.addStretch()

        layout.addWidget(group)

        # ── 파일 입력 그룹 (테스트용) ──
        group_file = QGroupBox("파일 입력 (테스트용)")
        file_layout = QVBoxLayout(group_file)
        file_layout.setSpacing(8)

        desc_file = QLabel(
            "MP4 등 영상 파일을 불러와 포트 대신 소스로 사용합니다.\n"
            "파일을 선택하면 파일 재생으로 전환되며, 초기화하면 포트로 복귀합니다."
        )
        desc_file.setObjectName("paramDescLabel")
        desc_file.setWordWrap(True)
        file_layout.addWidget(desc_file)

        file_row = QHBoxLayout()
        self._edit_video_file = QLineEdit()
        self._edit_video_file.setReadOnly(True)
        self._edit_video_file.setPlaceholderText("(파일 선택 안 함 — 포트 사용)")
        file_row.addWidget(self._edit_video_file)

        btn_browse_file = QPushButton("찾아보기")
        btn_browse_file.setMinimumWidth(80)
        btn_browse_file.setFixedHeight(_BTN_H)
        btn_browse_file.clicked.connect(self._browse_video_file)
        file_row.addWidget(btn_browse_file)

        btn_clear_file = QPushButton("초기화")
        btn_clear_file.setMinimumWidth(72)
        btn_clear_file.setFixedHeight(_BTN_H)
        btn_clear_file.clicked.connect(self._clear_video_file)
        file_row.addWidget(btn_clear_file)

        file_layout.addLayout(file_row)
        layout.addWidget(group_file)

        layout.addWidget(self._make_separator())

        # ── 자동 녹화 그룹 ──
        group_rec_basic = QGroupBox("자동 녹화 설정")
        rec_basic_layout = QVBoxLayout(group_rec_basic)
        rec_basic_layout.setSpacing(8)

        self._chk_recording_enabled = QCheckBox("알림 발생 시 자동 녹화 활성화")
        self._chk_recording_enabled.stateChanged.connect(self._save_recording_params)
        rec_basic_layout.addWidget(self._chk_recording_enabled)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("저장 폴더:"))
        self._edit_rec_dir = QLineEdit()
        self._edit_rec_dir.setPlaceholderText("recordings")
        self._edit_rec_dir.editingFinished.connect(self._save_recording_params)
        dir_row.addWidget(self._edit_rec_dir, 1)

        btn_browse_dir = QPushButton("찾아보기")
        btn_browse_dir.setFixedHeight(_BTN_H)
        btn_browse_dir.setMinimumWidth(80)
        btn_browse_dir.clicked.connect(self._browse_rec_dir)
        dir_row.addWidget(btn_browse_dir)

        btn_open_dir = QPushButton("폴더 열기")
        btn_open_dir.setFixedHeight(_BTN_H)
        btn_open_dir.setMinimumWidth(80)
        btn_open_dir.clicked.connect(self._open_rec_dir)
        dir_row.addWidget(btn_open_dir)

        rec_basic_layout.addLayout(dir_row)
        layout.addWidget(group_rec_basic)

        # ── 녹화 구간 그룹 ──
        group_range = QGroupBox("녹화 구간")
        range_layout = QGridLayout(group_range)
        range_layout.setSpacing(8)

        range_layout.addWidget(QLabel("사고 전 버퍼(초):"), 0, 0)
        self._edit_pre_seconds = _NumEdit(5, 1, 30)
        self._edit_pre_seconds.editingFinished.connect(self._save_recording_params)
        range_layout.addWidget(self._edit_pre_seconds, 0, 1)
        range_layout.addWidget(QLabel("(1~30, 기본값 5)"), 0, 2)

        range_layout.addWidget(QLabel("사고 후 녹화(초):"), 1, 0)
        self._edit_post_seconds = _NumEdit(15, 1, 60)
        self._edit_post_seconds.editingFinished.connect(self._save_recording_params)
        range_layout.addWidget(self._edit_post_seconds, 1, 1)
        range_layout.addWidget(QLabel("(1~60, 기본값 15)"), 1, 2)

        range_layout.setColumnStretch(2, 1)
        layout.addWidget(group_range)

        # ── 파일 관리 그룹 ──
        group_mgmt = QGroupBox("파일 관리")
        mgmt_layout = QGridLayout(group_mgmt)
        mgmt_layout.setSpacing(8)

        mgmt_layout.addWidget(QLabel("최대 보관 기간(일):"), 0, 0)
        self._edit_max_days = _NumEdit(7, 1, 365)
        self._edit_max_days.editingFinished.connect(self._save_recording_params)
        mgmt_layout.addWidget(self._edit_max_days, 0, 1)
        mgmt_layout.addWidget(QLabel("(1~365, 보관 기간 초과 파일은 자동 삭제)"), 0, 2)
        mgmt_layout.setColumnStretch(2, 1)
        layout.addWidget(group_mgmt)

        info_lbl = QLabel(
            "출력 해상도: 960×540  |  FPS: 10  |  버퍼 메모리: 약 2.5 MB\n"
            "녹화 파일 크기: 약 20~40 MB / 20초  |  코덱: mp4v"
        )
        info_lbl.setObjectName("settingsInfoLabel")
        info_lbl.setStyleSheet("color: #808090; font-size: 11px;")
        layout.addWidget(info_lbl)

        layout.addStretch()
        return widget

    def _browse_video_file(self):
        """영상 파일 선택 다이얼로그"""
        path, _ = QFileDialog.getOpenFileName(
            self, "영상 파일 선택",
            "", "영상 파일 (*.mp4 *.avi *.mkv *.mov);;모든 파일 (*)"
        )
        if path:
            self._edit_video_file.setText(path)
            self._edit_video_file.setCursorPosition(0)
            self.video_file_changed.emit(path)

    def _clear_video_file(self):
        """영상 파일 초기화 (포트 소스로 복귀)"""
        self._edit_video_file.clear()
        self.video_file_changed.emit("")

    # ── 탭 2: 비디오 감지 설정 ──────────────────────

    def _create_tab_video_roi(self) -> QWidget:
        return self._create_tab_roi("video")

    # ── 탭 3: 오디오 레벨미터 감지 설정 ────────────

    def _create_tab_audio_roi(self) -> QWidget:
        return self._create_tab_roi("audio")

    def _create_tab_roi(self, roi_type: str) -> QWidget:
        """비디오/오디오 ROI 탭 공통 생성 로직"""
        is_video = (roi_type == "video")
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # 편집 버튼 (토글형: 편집 시작/완료 겸용)
        edit_row = QHBoxLayout()
        label_prefix = "비디오" if is_video else "오디오"
        btn_edit = QPushButton(f"▶  {label_prefix} 감지영역 편집")
        btn_edit.setObjectName("btnHalfscreenEdit")
        btn_edit.setCheckable(True)
        if is_video:
            self._btn_video_edit = btn_edit
            btn_edit.clicked.connect(self._on_video_edit_toggled)
        else:
            self._btn_audio_edit = btn_edit
            btn_edit.clicked.connect(self._on_audio_edit_toggled)
        edit_row.addWidget(btn_edit)
        edit_row.addStretch()
        layout.addLayout(edit_row)

        help_lbl = QLabel(
            "[방향키]\n"
            "• ↑↓←→: 선택 영역 10px 이동\n"
            "• Shift+↑↓←→: 선택 영역 1px 이동\n"
            "• Ctrl+↑↓←→: 선택 영역 크기 10px 조정\n"
            "\n"
            "[클릭·드래그]\n"
            "• 빈 곳 드래그: 새 영역\n"
            "• 영역 드래그: 이동\n"
            "• Shift+드래그: 수직/수평으로만 이동\n"
            "• Ctrl+드래그(빈 곳): 범위 다중 선택\n"
            "• Ctrl+클릭: 선택 추가/제거\n"
            "• 다중선택 후 드래그: 한번에 이동\n"
            "• 다중선택 후 Ctrl+드래그: 복사\n"
            "• Ctrl+Shift+드래그: 수직/수평 복사\n"
            "\n"
            "[기타]\n"
            "• Ctrl+D: 선택 영역 복사\n"
            "• Delete: 선택 영역 삭제"
        )
        help_lbl.setObjectName("roiHelpLabel")
        help_lbl.setWordWrap(True)
        help_lbl.setAlignment(Qt.AlignTop)

        help_scroll = QScrollArea()
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QFrame.NoFrame)
        help_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        help_scroll.setMaximumHeight(160)
        help_scroll.setWidget(help_lbl)
        layout.addWidget(help_scroll)

        layout.addWidget(self._make_separator())

        lbl = QLabel("감지영역 List")
        lbl.setObjectName("roiTableLabel")
        layout.addWidget(lbl)

        table = self._create_roi_table()
        table.rows_delete_requested.connect(
            lambda rows, t=roi_type: self._delete_roi_rows(t, rows)
        )
        table.itemSelectionChanged.connect(
            lambda t=roi_type: self._on_table_row_selected(t)
        )
        if is_video:
            self._table_video = table
        else:
            self._table_audio = table
        layout.addWidget(table, 1)

        btn_row = QHBoxLayout()
        for text, slot in [
            ("추가",       lambda checked, t=roi_type: self._add_roi_last(t)),
            ("삭제",       lambda checked, t=roi_type: self._delete_selected_roi(t)),
            ("▲ 위로",    lambda checked, t=roi_type: self._move_roi(t, -1)),
            ("▼ 아래로",  lambda checked, t=roi_type: self._move_roi(t, 1)),
            ("전체 초기화", lambda checked, t=roi_type: self._reset_all_rois(t)),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("btnMoveRow")
            btn.setFixedHeight(_BTN_H)
            btn.setFocusPolicy(Qt.NoFocus)  # 버튼 클릭 시 테이블 행 선택 유지
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return widget

    # ── 탭 4: 감지 설정 ──────────────────────────────

    def _create_tab_detection_params(self) -> QWidget:
        # 스크롤 영역으로 감싸기 (내용이 길므로)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        # ── 블랙 감지 그룹 ──
        group_black = QGroupBox("블랙 감지")
        grid_b = QGridLayout(group_black)
        grid_b.setHorizontalSpacing(12)
        grid_b.setVerticalSpacing(8)
        grid_b.setColumnStretch(2, 1)

        lbl_bt = QLabel("▪  밝기 임계값:")
        lbl_bt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_threshold = _NumEdit(10, 0, 255)
        self._edit_black_threshold.editingFinished.connect(self._save_detection_params)
        desc_bt = QLabel("0~255 / 값이 낮을수록 더 어두운 영상만 감지  (기본값: 10)")
        desc_bt.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_bt,                    0, 0)
        grid_b.addWidget(self._edit_black_threshold, 0, 1)
        grid_b.addWidget(desc_bt,                   0, 2)

        lbl_btr = QLabel("▪  몇 초 이상시 알림 발생(초):")
        lbl_btr.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_duration = _NumEdit(10, 1, 300)
        self._edit_black_duration.editingFinished.connect(self._save_detection_params)
        desc_btr = QLabel("블랙이 이 시간 이상 지속되면 알림 발생  (기본값: 10초)")
        desc_btr.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_btr,                  1, 0)
        grid_b.addWidget(self._edit_black_duration, 1, 1)
        grid_b.addWidget(desc_btr,                 1, 2)

        lbl_bad = QLabel("▪  알림 지속시간(초):")
        lbl_bad.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_alarm_duration = _NumEdit(10, 1, 300)
        self._edit_black_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_bad = QLabel("알림 발생 시 소리를 울리는 시간  (기본값: 10초)")
        desc_bad.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_bad,                        2, 0)
        grid_b.addWidget(self._edit_black_alarm_duration, 2, 1)
        grid_b.addWidget(desc_bad,                       2, 2)

        layout.addWidget(group_black)

        # ── 스틸 감지 그룹 ──
        group_still = QGroupBox("스틸 감지")
        grid_s = QGridLayout(group_still)
        grid_s.setHorizontalSpacing(12)
        grid_s.setVerticalSpacing(8)
        grid_s.setColumnStretch(2, 1)

        lbl_st = QLabel("▪  픽셀 차이 임계값:")
        lbl_st.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_threshold = _NumEdit(2, 0, 255)
        self._edit_still_threshold.editingFinished.connect(self._save_detection_params)
        desc_st = QLabel(
            "• 0~255 / 값이 낮을수록 미세한 변화도 정지로 판단  (기본값: 2)<br>"
            "• 임계값 2 → \"거의 2픽셀도 달라지면 안 됨\" → 엄격 (정지 감지가 잘 안 됨)<br>"
            "• 임계값 10 → \"10픽셀 정도 변화해도 정지로 봄\" → 느슨 (오감지 위험)"
        )
        desc_st.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_st,                    0, 0)
        grid_s.addWidget(self._edit_still_threshold, 0, 1)
        grid_s.addWidget(desc_st,                   0, 2)

        lbl_str = QLabel("▪  몇 초 이상시 알림 발생(초):")
        lbl_str.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_duration = _NumEdit(30, 1, 300)
        self._edit_still_duration.editingFinished.connect(self._save_detection_params)
        desc_str = QLabel("스틸이 이 시간 이상 지속되면 알림 발생  (기본값: 30초)")
        desc_str.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_str,                  1, 0)
        grid_s.addWidget(self._edit_still_duration, 1, 1)
        grid_s.addWidget(desc_str,                 1, 2)

        lbl_sad = QLabel("▪  알림 지속시간(초):")
        lbl_sad.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_alarm_duration = _NumEdit(10, 1, 300)
        self._edit_still_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_sad = QLabel("알림 발생 시 소리를 울리는 시간  (기본값: 10초)")
        desc_sad.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_sad,                        2, 0)
        grid_s.addWidget(self._edit_still_alarm_duration, 2, 1)
        grid_s.addWidget(desc_sad,                       2, 2)

        layout.addWidget(group_still)

        # ── 오디오 레벨미터 감지 그룹 (HSV) ──
        group_audio = QGroupBox("오디오 레벨미터 감지 (HSV)")
        audio_layout = QVBoxLayout(group_audio)
        audio_layout.setSpacing(8)

        grid_a = QGridLayout()
        grid_a.setHorizontalSpacing(12)
        grid_a.setVerticalSpacing(6)
        grid_a.setColumnStretch(2, 1)

        # H 범위
        lbl_h = QLabel("▪  H 범위 (색조):")
        lbl_h.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._slider_hsv_h = DualSlider(0, 179, "hue")
        self._slider_hsv_h.set_range(40, 80)
        self._slider_hsv_h.range_changed.connect(self._on_hsv_changed)
        self._lbl_h_val = QLabel("40 ~ 80")
        self._lbl_h_val.setObjectName("paramDescLabel")
        self._lbl_h_val.setFixedWidth(80)
        desc_h = QLabel("0~179 / OpenCV HSV H값  (기본값: 40~80 — 초록 계열)")
        desc_h.setObjectName("paramDescLabel")
        grid_a.addWidget(lbl_h,              0, 0)
        grid_a.addWidget(self._slider_hsv_h, 0, 1)
        grid_a.addWidget(self._lbl_h_val,    0, 2)
        grid_a.addWidget(desc_h,             0, 3)

        # S 범위
        lbl_s = QLabel("▪  S 범위 (채도):")
        lbl_s.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._slider_hsv_s = DualSlider(0, 255, "saturation")
        self._slider_hsv_s.set_range(30, 255)
        self._slider_hsv_s.range_changed.connect(self._on_hsv_changed)
        self._lbl_s_val = QLabel("30 ~ 255")
        self._lbl_s_val.setObjectName("paramDescLabel")
        self._lbl_s_val.setFixedWidth(80)
        desc_s = QLabel("0~255 / 색의 선명도  (기본값: 30~255)")
        desc_s.setObjectName("paramDescLabel")
        grid_a.addWidget(lbl_s,              1, 0)
        grid_a.addWidget(self._slider_hsv_s, 1, 1)
        grid_a.addWidget(self._lbl_s_val,    1, 2)
        grid_a.addWidget(desc_s,             1, 3)

        # V 범위
        lbl_v = QLabel("▪  V 범위 (명도):")
        lbl_v.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._slider_hsv_v = DualSlider(0, 255, "value")
        self._slider_hsv_v.set_range(30, 255)
        self._slider_hsv_v.range_changed.connect(self._on_hsv_changed)
        self._lbl_v_val = QLabel("30 ~ 255")
        self._lbl_v_val.setObjectName("paramDescLabel")
        self._lbl_v_val.setFixedWidth(80)
        desc_v = QLabel("0~255 / 밝기  (기본값: 30~255)")
        desc_v.setObjectName("paramDescLabel")
        grid_a.addWidget(lbl_v,              2, 0)
        grid_a.addWidget(self._slider_hsv_v, 2, 1)
        grid_a.addWidget(self._lbl_v_val,    2, 2)
        grid_a.addWidget(desc_v,             2, 3)

        audio_layout.addLayout(grid_a)

        grid_a2 = QGridLayout()
        grid_a2.setHorizontalSpacing(12)
        grid_a2.setVerticalSpacing(8)
        grid_a2.setColumnStretch(2, 1)

        lbl_apr = QLabel("▪  감지 픽셀 비율(%):")
        lbl_apr.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_audio_pixel_ratio = _NumEdit(5, 1, 100)
        self._edit_audio_pixel_ratio.editingFinished.connect(self._save_detection_params)
        desc_apr = QLabel(
            "• ROI 내 HSV 범위 픽셀 비율이 이 값 이상이면 활성  (기본값: 5%)\n"
            "• 소리 없음 → 초록색 없음 → ratio < 5% → is_active=False → 이상(무음) → 알람"
        )
        desc_apr.setObjectName("paramDescLabel")
        desc_apr.setWordWrap(True)
        grid_a2.addWidget(lbl_apr,                     0, 0)
        grid_a2.addWidget(self._edit_audio_pixel_ratio, 0, 1)
        grid_a2.addWidget(desc_apr,                    0, 2)

        lbl_ald = QLabel("▪  몇 초 이상시 알림 발생(초):")
        lbl_ald.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_audio_level_duration = _NumEdit(20, 1, 300)
        self._edit_audio_level_duration.editingFinished.connect(self._save_detection_params)
        desc_ald = QLabel("레벨미터 비활성이 이 시간 이상 지속되면 알림  (기본값: 20초)")
        desc_ald.setObjectName("paramDescLabel")
        grid_a2.addWidget(lbl_ald,                      1, 0)
        grid_a2.addWidget(self._edit_audio_level_duration, 1, 1)
        grid_a2.addWidget(desc_ald,                     1, 2)

        lbl_alad = QLabel("▪  알림 지속시간(초):")
        lbl_alad.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_audio_level_alarm_duration = _NumEdit(10, 1, 300)
        self._edit_audio_level_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_alad = QLabel("알림 발생 시 소리를 울리는 시간  (기본값: 10초)")
        desc_alad.setObjectName("paramDescLabel")
        grid_a2.addWidget(lbl_alad,                          2, 0)
        grid_a2.addWidget(self._edit_audio_level_alarm_duration, 2, 1)
        grid_a2.addWidget(desc_alad,                         2, 2)

        lbl_alrs = QLabel("▪  복구 딜레이(초):")
        lbl_alrs.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_audio_level_recovery_seconds = _NumEdit(2, 0, 30)
        self._edit_audio_level_recovery_seconds.editingFinished.connect(self._save_detection_params)
        desc_alrs = QLabel("알림 발생 후 정상 복구 판정을 위한 최소 정상 지속 시간  (기본값: 2초, 0=즉시복구)")
        desc_alrs.setObjectName("paramDescLabel")
        grid_a2.addWidget(lbl_alrs,                               3, 0)
        grid_a2.addWidget(self._edit_audio_level_recovery_seconds, 3, 1)
        grid_a2.addWidget(desc_alrs,                              3, 2)

        audio_layout.addLayout(grid_a2)
        layout.addWidget(group_audio)

        # ── 임베디드 오디오 감지 그룹 ──
        group_emb = QGroupBox("임베디드 오디오 감지 (무음 감지)")
        grid_e = QGridLayout(group_emb)
        grid_e.setHorizontalSpacing(12)
        grid_e.setVerticalSpacing(8)
        grid_e.setColumnStretch(2, 1)

        lbl_est = QLabel("▪  무음 임계값(dB):")
        lbl_est.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_embedded_silence_threshold = _NumEdit(-50, -60, 0)
        self._edit_embedded_silence_threshold.editingFinished.connect(self._save_detection_params)
        desc_est = QLabel("-60~0 / 이 값 이하를 무음으로 판단  (기본값: -50dB)")
        desc_est.setObjectName("paramDescLabel")
        grid_e.addWidget(lbl_est,                               0, 0)
        grid_e.addWidget(self._edit_embedded_silence_threshold,  0, 1)
        grid_e.addWidget(desc_est,                              0, 2)

        lbl_esd = QLabel("▪  몇 초 이상시 알림 발생(초):")
        lbl_esd.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_embedded_silence_duration = _NumEdit(20, 1, 300)
        self._edit_embedded_silence_duration.editingFinished.connect(self._save_detection_params)
        desc_esd = QLabel("무음이 이 시간 이상 지속되면 알림 발생  (기본값: 20초)")
        desc_esd.setObjectName("paramDescLabel")
        grid_e.addWidget(lbl_esd,                             1, 0)
        grid_e.addWidget(self._edit_embedded_silence_duration, 1, 1)
        grid_e.addWidget(desc_esd,                            1, 2)

        lbl_ead = QLabel("▪  알림 지속시간(초):")
        lbl_ead.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_embedded_alarm_duration = _NumEdit(10, 1, 300)
        self._edit_embedded_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_ead = QLabel("알림 발생 시 소리를 울리는 시간  (기본값: 10초)")
        desc_ead.setObjectName("paramDescLabel")
        grid_e.addWidget(lbl_ead,                          2, 0)
        grid_e.addWidget(self._edit_embedded_alarm_duration, 2, 1)
        grid_e.addWidget(desc_ead,                         2, 2)

        layout.addWidget(group_emb)

        # ── 성능 설정 그룹 ──
        group_perf = QGroupBox("성능 설정")
        perf_layout = QVBoxLayout(group_perf)
        perf_layout.setSpacing(8)
        grid_p = QGridLayout()
        grid_p.setHorizontalSpacing(12)
        grid_p.setVerticalSpacing(8)
        grid_p.setColumnStretch(2, 1)

        lbl_di = QLabel("▪  감지 주기:")
        lbl_di.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._combo_detect_interval = QComboBox()
        for label, val in [("100ms", 100), ("200ms", 200), ("300ms", 300),
                            ("500ms", 500), ("1000ms", 1000)]:
            self._combo_detect_interval.addItem(label, val)
        self._combo_detect_interval.setCurrentIndex(1)  # 기본: 200ms
        self._combo_detect_interval.setFixedWidth(110)
        self._combo_detect_interval.currentIndexChanged.connect(self._save_performance_params)
        desc_di = QLabel("감지 연산 실행 주기 (값이 클수록 CPU 부하 감소, 10초 알람 기준 500ms까지 안전)")
        desc_di.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_di,                        0, 0)
        grid_p.addWidget(self._combo_detect_interval,   0, 1)
        grid_p.addWidget(desc_di,                       0, 2)

        lbl_sf = QLabel("▪  감지 해상도:")
        lbl_sf.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._combo_scale_factor = QComboBox()
        for label, val in [("원본 (1920×1080)", 1.0),
                            ("50% (960×540)",   0.5),
                            ("25% (480×270)",   0.25)]:
            self._combo_scale_factor.addItem(label, val)
        self._combo_scale_factor.setCurrentIndex(0)  # 기본: 원본
        self._combo_scale_factor.setFixedWidth(160)
        self._combo_scale_factor.currentIndexChanged.connect(self._save_performance_params)
        desc_sf = QLabel("50% 시 처리 픽셀 75% 감소 — 감지 정확도 영향 없음")
        desc_sf.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_sf,                    1, 0)
        grid_p.addWidget(self._combo_scale_factor,  1, 1)
        grid_p.addWidget(desc_sf,                   1, 2)

        lbl_vde = QLabel("▪  비디오 감지:")
        lbl_vde.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chk_video_detect = QCheckBox("블랙/스틸 감지 활성화")
        self._chk_video_detect.setChecked(True)
        self._chk_video_detect.stateChanged.connect(self._save_performance_params)
        grid_p.addWidget(lbl_vde,                  2, 0)
        grid_p.addWidget(self._chk_video_detect,   2, 1, 1, 2)

        lbl_ade = QLabel("▪  오디오 레벨미터 감지:")
        lbl_ade.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chk_audio_detect = QCheckBox("HSV 색상 감지 활성화")
        self._chk_audio_detect.setChecked(True)
        self._chk_audio_detect.stateChanged.connect(self._save_performance_params)
        desc_ade = QLabel("비활성화 시 HSV 전체변환 생략 — 가장 효과적인 부하 절감")
        desc_ade.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_ade,                  3, 0)
        grid_p.addWidget(self._chk_audio_detect,   3, 1)
        grid_p.addWidget(desc_ade,                 3, 2)

        lbl_sde = QLabel("▪  스틸 감지:")
        lbl_sde.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chk_still_detect = QCheckBox("정지 영상 감지 활성화")
        self._chk_still_detect.setChecked(True)
        self._chk_still_detect.stateChanged.connect(self._save_performance_params)
        desc_sde = QLabel("비활성화 시 프레임 간 비교 연산 생략")
        desc_sde.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_sde,                  4, 0)
        grid_p.addWidget(self._chk_still_detect,   4, 1)
        grid_p.addWidget(desc_sde,                 4, 2)

        bench_row = QHBoxLayout()
        self._btn_benchmark = QPushButton("자동 성능 감지")
        self._btn_benchmark.setFixedHeight(_BTN_H)
        self._btn_benchmark.setMinimumWidth(130)
        self._btn_benchmark.clicked.connect(self._run_benchmark)
        self._lbl_benchmark = QLabel("")
        self._lbl_benchmark.setObjectName("paramDescLabel")
        self._lbl_benchmark.setWordWrap(True)
        btn_perf_guide = QPushButton("성능 설정 안내")
        btn_perf_guide.setFixedHeight(_BTN_H)
        btn_perf_guide.setMinimumWidth(110)
        btn_perf_guide.setObjectName("btnPerfGuide")
        btn_perf_guide.clicked.connect(self._show_performance_guide)
        bench_row.addWidget(self._btn_benchmark)
        bench_row.addWidget(btn_perf_guide)
        bench_row.addWidget(self._lbl_benchmark, 1)

        perf_layout.addLayout(grid_p)
        perf_layout.addLayout(bench_row)
        layout.addWidget(group_perf)

        layout.addStretch()

        # ── 전체 초기화 버튼 (우하단) ──
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        btn_reset_all = QPushButton("전체 초기화")
        btn_reset_all.setFixedHeight(_BTN_H)
        btn_reset_all.setMinimumWidth(100)
        btn_reset_all.clicked.connect(self._reset_detection_params_to_default)
        reset_row.addWidget(btn_reset_all)
        layout.addLayout(reset_row)

        scroll.setWidget(inner)
        return scroll

    # ── 탭 5: 알림설정 ───────────────────────────────

    def _create_tab_alarm(self) -> QWidget:
        """탭 5: 알림설정 — 알림음 파일 선택 (단일 통합 알림음)"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        # ── 알림음 파일 설정 그룹 ──
        group_sound = QGroupBox("알림음 파일 설정")
        sound_layout = QVBoxLayout(group_sound)
        sound_layout.setSpacing(10)

        file_row = QHBoxLayout()
        lbl = QLabel("알림음 파일:")
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lbl.setFixedWidth(90)
        file_row.addWidget(lbl)

        self._alarm_file_edits: dict = {}
        path_edit = QLineEdit()
        path_edit.setReadOnly(True)
        path_edit.setPlaceholderText("(Windows 내장 경고음 사용 — SystemHand)")
        self._alarm_file_edits["default"] = path_edit
        file_row.addWidget(path_edit)

        btn_browse = QPushButton("찾아보기")
        btn_browse.setMinimumWidth(80)
        btn_browse.setFixedHeight(_BTN_H)
        btn_browse.clicked.connect(lambda: self._browse_sound_file("default"))
        file_row.addWidget(btn_browse)

        btn_clear = QPushButton("초기화")
        btn_clear.setMinimumWidth(72)
        btn_clear.setFixedHeight(_BTN_H)
        btn_clear.clicked.connect(lambda: self._clear_sound_file("default"))
        file_row.addWidget(btn_clear)

        btn_test = QPushButton("테스트")
        btn_test.setMinimumWidth(72)
        btn_test.setFixedHeight(_BTN_H)
        btn_test.clicked.connect(lambda: self.test_sound_requested.emit("default"))
        file_row.addWidget(btn_test)

        sound_layout.addLayout(file_row)
        layout.addWidget(group_sound)

        layout.addWidget(self._make_separator())

        # ── 텔레그램 봇 설정 그룹 ──
        group_tg_basic = QGroupBox("텔레그램 봇 설정")
        tg_basic_layout = QGridLayout(group_tg_basic)
        tg_basic_layout.setSpacing(8)

        self._chk_telegram_enabled = QCheckBox("텔레그램 알림 활성화")
        self._chk_telegram_enabled.stateChanged.connect(self._save_telegram_params)
        tg_basic_layout.addWidget(self._chk_telegram_enabled, 0, 0, 1, 3)

        tg_basic_layout.addWidget(QLabel("Bot Token:"), 1, 0)
        self._edit_bot_token = QLineEdit()
        self._edit_bot_token.setEchoMode(QLineEdit.Password)
        self._edit_bot_token.setPlaceholderText("텔레그램 BotFather에서 발급받은 토큰")
        self._edit_bot_token.editingFinished.connect(self._save_telegram_params)
        tg_basic_layout.addWidget(self._edit_bot_token, 1, 1, 1, 2)

        tg_basic_layout.addWidget(QLabel("Chat ID:"), 2, 0)
        self._edit_chat_id = QLineEdit()
        self._edit_chat_id.setPlaceholderText("수신할 채팅/그룹/채널 ID")
        self._edit_chat_id.editingFinished.connect(self._save_telegram_params)
        tg_basic_layout.addWidget(self._edit_chat_id, 2, 1, 1, 2)

        btn_test_tg = QPushButton("연결 테스트")
        btn_test_tg.setFixedHeight(_BTN_H)
        btn_test_tg.setMinimumWidth(100)
        btn_test_tg.clicked.connect(self._on_telegram_test_clicked)
        self._lbl_telegram_test = QLabel("")
        self._lbl_telegram_test.setWordWrap(True)
        tg_test_row = QHBoxLayout()
        tg_test_row.addWidget(btn_test_tg)
        tg_test_row.addWidget(self._lbl_telegram_test, 1)
        tg_basic_layout.addLayout(tg_test_row, 3, 0, 1, 3)

        layout.addWidget(group_tg_basic)

        # ── 텔레그램 알림 이미지 / 타입 / 쿨다운 그룹 ──
        group_tg_opt = QGroupBox("텔레그램 알림 옵션")
        tg_opt_layout = QVBoxLayout(group_tg_opt)
        tg_opt_layout.setSpacing(6)

        self._chk_telegram_send_image = QCheckBox("알림 발생 시 스냅샷 이미지 첨부")
        self._chk_telegram_send_image.stateChanged.connect(self._save_telegram_params)
        tg_opt_layout.addWidget(self._chk_telegram_send_image)

        self._chk_tg_black = QCheckBox("블랙 감지 알림")
        self._chk_tg_still = QCheckBox("스틸 감지 알림")
        self._chk_tg_audio = QCheckBox("오디오 레벨미터 감지 알림")
        self._chk_tg_embedded = QCheckBox("임베디드 오디오 감지 알림")
        for chk in (self._chk_tg_black, self._chk_tg_still,
                    self._chk_tg_audio, self._chk_tg_embedded):
            chk.stateChanged.connect(self._save_telegram_params)
            tg_opt_layout.addWidget(chk)

        cooldown_row = QHBoxLayout()
        cooldown_row.addWidget(QLabel("쿨다운 시간(초):"))
        self._edit_tg_cooldown = _NumEdit(60, 0, 3600)
        self._edit_tg_cooldown.editingFinished.connect(self._save_telegram_params)
        cooldown_row.addWidget(self._edit_tg_cooldown)
        cooldown_row.addWidget(QLabel("(동일 감지영역 N초 이내 재전송 방지)"))
        cooldown_row.addStretch()
        tg_opt_layout.addLayout(cooldown_row)

        layout.addWidget(group_tg_opt)
        layout.addStretch()

        scroll.setWidget(inner)
        return scroll

    def _on_telegram_test_clicked(self):
        """연결 테스트 버튼 클릭"""
        self._lbl_telegram_test.setText("테스트 중...")
        token = self._edit_bot_token.text()
        chat_id = self._edit_chat_id.text()
        self.telegram_test_requested.emit(token, chat_id)

    def set_telegram_test_result(self, ok: bool, msg: str):
        """텔레그램 테스트 결과 표시 (외부 호출)"""
        color = "#ffffff" if ok else "#ff4444"
        self._lbl_telegram_test.setText(msg)
        self._lbl_telegram_test.setStyleSheet(f"color: {color};")

    def _get_telegram_params(self) -> dict:
        """현재 텔레그램 설정 UI 값을 dict로 반환"""
        return {
            "enabled": self._chk_telegram_enabled.isChecked(),
            "bot_token": self._edit_bot_token.text(),
            "chat_id": self._edit_chat_id.text(),
            "send_image": self._chk_telegram_send_image.isChecked(),
            "cooldown": self._edit_tg_cooldown.get_value(),
            "notify_black": self._chk_tg_black.isChecked(),
            "notify_still": self._chk_tg_still.isChecked(),
            "notify_audio_level": self._chk_tg_audio.isChecked(),
            "notify_embedded": self._chk_tg_embedded.isChecked(),
        }

    def _save_telegram_params(self):
        """텔레그램 설정을 config에 저장하고 신호 발송"""
        params = self._get_telegram_params()
        self._config["telegram"] = params
        self.telegram_settings_changed.emit(params)

    def _load_telegram_config(self, config: dict):
        """텔레그램 설정 UI 로드"""
        tg = config.get("telegram", {})
        self._chk_telegram_enabled.blockSignals(True)
        self._chk_telegram_send_image.blockSignals(True)
        self._chk_tg_black.blockSignals(True)
        self._chk_tg_still.blockSignals(True)
        self._chk_tg_audio.blockSignals(True)
        self._chk_tg_embedded.blockSignals(True)

        self._chk_telegram_enabled.setChecked(bool(tg.get("enabled", False)))
        self._edit_bot_token.setText(tg.get("bot_token", ""))
        self._edit_chat_id.setText(tg.get("chat_id", ""))
        self._chk_telegram_send_image.setChecked(bool(tg.get("send_image", True)))
        self._edit_tg_cooldown.setText(str(int(tg.get("cooldown", 60))))
        self._chk_tg_black.setChecked(bool(tg.get("notify_black", True)))
        self._chk_tg_still.setChecked(bool(tg.get("notify_still", True)))
        self._chk_tg_audio.setChecked(bool(tg.get("notify_audio_level", True)))
        self._chk_tg_embedded.setChecked(bool(tg.get("notify_embedded", True)))

        self._chk_telegram_enabled.blockSignals(False)
        self._chk_telegram_send_image.blockSignals(False)
        self._chk_tg_black.blockSignals(False)
        self._chk_tg_still.blockSignals(False)
        self._chk_tg_audio.blockSignals(False)
        self._chk_tg_embedded.blockSignals(False)

    @staticmethod
    def _to_relative_if_possible(path: str) -> str:
        """프로그램 루트 기준 상대경로로 변환 (내부 경로일 때만)"""
        try:
            rel = os.path.relpath(path, os.getcwd())
            if not rel.startswith(".."):
                return rel
        except ValueError:
            pass
        return path

    def _browse_rec_dir(self):
        """녹화 저장 폴더 선택"""
        init_dir = os.path.abspath("recordings")
        os.makedirs(init_dir, exist_ok=True)
        path = QFileDialog.getExistingDirectory(self, "녹화 저장 폴더 선택", init_dir)
        if path:
            self._edit_rec_dir.setText(self._to_relative_if_possible(path))
            self._save_recording_params()

    def _open_rec_dir(self):
        """녹화 폴더를 탐색기로 열기"""
        import subprocess
        path = self._edit_rec_dir.text() or "recordings"
        os.makedirs(path, exist_ok=True)
        try:
            subprocess.Popen(f'explorer "{os.path.abspath(path)}"')
        except Exception:
            pass

    def _get_recording_params(self) -> dict:
        """현재 녹화 설정 UI 값을 dict로 반환"""
        return {
            "enabled": self._chk_recording_enabled.isChecked(),
            "save_dir": self._edit_rec_dir.text() or "recordings",
            "pre_seconds": self._edit_pre_seconds.get_value(),
            "post_seconds": self._edit_post_seconds.get_value(),
            "max_keep_days": self._edit_max_days.get_value(),
        }

    def _save_recording_params(self):
        """녹화 설정을 config에 저장하고 신호 발송"""
        params = self._get_recording_params()
        self._config["recording"] = params
        self.recording_settings_changed.emit(params)

    def _load_recording_config(self, config: dict):
        """녹화 설정 UI 로드"""
        rec = config.get("recording", {})
        self._chk_recording_enabled.blockSignals(True)
        self._chk_recording_enabled.setChecked(bool(rec.get("enabled", False)))
        self._chk_recording_enabled.blockSignals(False)
        self._edit_rec_dir.setText(rec.get("save_dir", "recordings"))
        self._edit_pre_seconds.setText(str(int(rec.get("pre_seconds", 5))))
        self._edit_post_seconds.setText(str(int(rec.get("post_seconds", 15))))
        self._edit_max_days.setText(str(int(rec.get("max_keep_days", 7))))

    # ── 탭 8: 저장/불러오기 ──────────────────────────

    def _create_tab_save_load(self) -> QWidget:
        """탭 6: 저장/불러오기"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignTop)

        # ── 저장 그룹 ──
        group_save = QGroupBox("설정 파일 저장")
        save_layout = QVBoxLayout(group_save)
        save_layout.setSpacing(8)

        btn_save = QPushButton("현재 설정 저장...")
        btn_save.setFixedHeight(_BTN_H)
        btn_save.clicked.connect(self._on_save_clicked)
        save_layout.addWidget(btn_save)
        layout.addWidget(group_save)

        # ── 불러오기 그룹 ──
        group_load = QGroupBox("설정 파일 불러오기")
        load_layout = QVBoxLayout(group_load)
        load_layout.setSpacing(8)

        btn_load = QPushButton("설정 파일 불러오기...")
        btn_load.setFixedHeight(_BTN_H)
        btn_load.clicked.connect(self._on_load_clicked)
        load_layout.addWidget(btn_load)
        layout.addWidget(group_load)

        # ── 초기화 그룹 ──
        group_reset = QGroupBox("기본값으로 초기화")
        reset_layout = QVBoxLayout(group_reset)
        reset_layout.setSpacing(8)

        btn_reset = QPushButton("기본값으로 초기화")
        btn_reset.setFixedHeight(_BTN_H)
        btn_reset.clicked.connect(self._on_reset_clicked)
        reset_layout.addWidget(btn_reset)
        layout.addWidget(group_reset)

        # ── About 그룹 ──
        group_about = QGroupBox("About")
        about_layout = QGridLayout(group_about)
        about_layout.setSpacing(6)
        about_layout.setColumnStretch(1, 1)

        about_layout.addWidget(QLabel("Version:"), 0, 0)
        lbl_version = QLabel("KBS Peacock v1.02")
        about_layout.addWidget(lbl_version, 0, 1)

        about_layout.addWidget(QLabel("Date:"), 1, 0)
        lbl_date = QLabel("2026-02-22")
        about_layout.addWidget(lbl_date, 1, 1)

        about_layout.addWidget(QLabel("GitHub:"), 2, 0)
        lbl_github = QLabel(
            '<a href="https://github.com/mw3love/KBS_Detecting_SW_260219">'
            "https://github.com/mw3love/KBS_Detecting_SW_260219</a>"
        )
        lbl_github.setOpenExternalLinks(True)
        lbl_github.setTextFormat(Qt.RichText)
        about_layout.addWidget(lbl_github, 2, 1)

        about_layout.addWidget(QLabel("E-mail:"), 3, 0)
        lbl_email = QLabel("minwoo@kbs.co.kr")
        about_layout.addWidget(lbl_email, 3, 1)

        layout.addWidget(group_about)

        layout.addStretch()
        return widget

    # ── 공통 헬퍼 ─────────────────────────────────────

    def _create_roi_table(self) -> _ROITable:
        table = _ROITable()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["라벨", "매체명", "X", "Y", "W", "H"])

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.resizeSection(0, 60)    # 라벨
        hdr.resizeSection(1, 200)   # 매체명
        hdr.resizeSection(2, 60)    # X
        hdr.resizeSection(3, 60)    # Y
        hdr.resizeSection(4, 60)    # W
        hdr.resizeSection(5, 60)    # H

        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.DoubleClicked)
        table.verticalHeader().setVisible(False)
        table.setMinimumHeight(24 * 12 + 34)
        table.itemChanged.connect(self._on_table_item_changed)
        return table

    def _create_placeholder(self, text: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #505068;")
        layout.addWidget(lbl)
        return widget

    @staticmethod
    def _make_separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("settingsSeparator")
        return line

    # ── 설정 로드 ─────────────────────────────────────

    def _apply_detection_params_to_ui(self, det: dict):
        """감지 파라미터 dict를 UI 위젯에 적용 (신호 없이 조용히 갱신)"""
        self._edit_black_threshold.setText(str(int(det.get("black_threshold", 10))))
        self._edit_black_duration.setText(str(int(det.get("black_duration", 10))))
        self._edit_black_alarm_duration.setText(str(int(det.get("black_alarm_duration", 10))))
        self._edit_still_threshold.setText(str(int(det.get("still_threshold", 2))))
        self._edit_still_duration.setText(str(int(det.get("still_duration", 30))))
        self._edit_still_alarm_duration.setText(str(int(det.get("still_alarm_duration", 10))))

        # 오디오 레벨미터 HSV 설정
        h_min = int(det.get("audio_hsv_h_min", 40))
        h_max = int(det.get("audio_hsv_h_max", 80))
        s_min = int(det.get("audio_hsv_s_min", 30))
        s_max = int(det.get("audio_hsv_s_max", 255))
        v_min = int(det.get("audio_hsv_v_min", 30))
        v_max = int(det.get("audio_hsv_v_max", 255))
        self._slider_hsv_h.blockSignals(True)
        self._slider_hsv_s.blockSignals(True)
        self._slider_hsv_v.blockSignals(True)
        self._slider_hsv_h.set_range(h_min, h_max)
        self._slider_hsv_s.set_range(s_min, s_max)
        self._slider_hsv_v.set_range(v_min, v_max)
        self._slider_hsv_h.blockSignals(False)
        self._slider_hsv_s.blockSignals(False)
        self._slider_hsv_v.blockSignals(False)
        self._lbl_h_val.setText(f"{h_min} ~ {h_max}")
        self._lbl_s_val.setText(f"{s_min} ~ {s_max}")
        self._lbl_v_val.setText(f"{v_min} ~ {v_max}")

        self._edit_audio_pixel_ratio.setText(str(int(det.get("audio_pixel_ratio", 5))))
        self._edit_audio_level_duration.setText(str(int(det.get("audio_level_duration", 20))))
        self._edit_audio_level_alarm_duration.setText(str(int(det.get("audio_level_alarm_duration", 10))))
        self._edit_audio_level_recovery_seconds.setText(str(int(det.get("audio_level_recovery_seconds", 2))))

        # 임베디드 오디오 설정
        self._edit_embedded_silence_threshold.setText(str(int(det.get("embedded_silence_threshold", -50))))
        self._edit_embedded_silence_duration.setText(str(int(det.get("embedded_silence_duration", 20))))
        self._edit_embedded_alarm_duration.setText(str(int(det.get("embedded_alarm_duration", 10))))

    def _apply_performance_params_to_ui(self, perf: dict):
        """성능 파라미터 dict를 UI 위젯에 적용 (신호 없이 조용히 갱신)"""
        interval = int(perf.get("detection_interval", 200))
        for i in range(self._combo_detect_interval.count()):
            if self._combo_detect_interval.itemData(i) == interval:
                self._combo_detect_interval.blockSignals(True)
                self._combo_detect_interval.setCurrentIndex(i)
                self._combo_detect_interval.blockSignals(False)
                break
        scale = float(perf.get("scale_factor", 1.0))
        for i in range(self._combo_scale_factor.count()):
            if abs(self._combo_scale_factor.itemData(i) - scale) < 0.01:
                self._combo_scale_factor.blockSignals(True)
                self._combo_scale_factor.setCurrentIndex(i)
                self._combo_scale_factor.blockSignals(False)
                break
        self._chk_video_detect.blockSignals(True)
        self._chk_audio_detect.blockSignals(True)
        self._chk_still_detect.blockSignals(True)
        self._chk_video_detect.setChecked(bool(perf.get("video_detection_enabled", True)))
        self._chk_audio_detect.setChecked(bool(perf.get("audio_detection_enabled", True)))
        self._chk_still_detect.setChecked(bool(perf.get("still_detection_enabled", True)))
        self._chk_video_detect.blockSignals(False)
        self._chk_audio_detect.blockSignals(False)
        self._chk_still_detect.blockSignals(False)

    def _load_config(self, config: dict):
        port = config.get("port", 0)
        idx = self._combo_port.findData(port)
        if idx >= 0:
            self._combo_port.blockSignals(True)
            self._combo_port.setCurrentIndex(idx)
            self._combo_port.blockSignals(False)

        self._apply_detection_params_to_ui(config.get("detection", {}))
        self._apply_performance_params_to_ui(config.get("performance", {}))

        self._load_alarm_config(config)
        self._load_telegram_config(config)
        self._load_recording_config(config)
        self.refresh_roi_tables()

    def _get_current_detection_params(self) -> dict:
        h_min, h_max = self._slider_hsv_h.get_range()
        s_min, s_max = self._slider_hsv_s.get_range()
        v_min, v_max = self._slider_hsv_v.get_range()
        return {
            "black_threshold":      self._edit_black_threshold.get_value(),
            "black_duration":       self._edit_black_duration.get_value(),
            "black_alarm_duration": self._edit_black_alarm_duration.get_value(),
            "still_threshold":      self._edit_still_threshold.get_value(),
            "still_duration":       self._edit_still_duration.get_value(),
            "still_alarm_duration": self._edit_still_alarm_duration.get_value(),
            # 오디오 레벨미터 HSV
            "audio_hsv_h_min": h_min,
            "audio_hsv_h_max": h_max,
            "audio_hsv_s_min": s_min,
            "audio_hsv_s_max": s_max,
            "audio_hsv_v_min": v_min,
            "audio_hsv_v_max": v_max,
            "audio_pixel_ratio":                self._edit_audio_pixel_ratio.get_value(),
            "audio_level_duration":             self._edit_audio_level_duration.get_value(),
            "audio_level_alarm_duration":       self._edit_audio_level_alarm_duration.get_value(),
            "audio_level_recovery_seconds":     self._edit_audio_level_recovery_seconds.get_value(),
            # 임베디드 오디오
            "embedded_silence_threshold":  self._edit_embedded_silence_threshold.get_value(),
            "embedded_silence_duration":   self._edit_embedded_silence_duration.get_value(),
            "embedded_alarm_duration":     self._edit_embedded_alarm_duration.get_value(),
        }

    def _on_hsv_changed(self):
        """HSV 슬라이더 변경 시 값 레이블 갱신 후 즉시 저장"""
        h_min, h_max = self._slider_hsv_h.get_range()
        s_min, s_max = self._slider_hsv_s.get_range()
        v_min, v_max = self._slider_hsv_v.get_range()
        self._lbl_h_val.setText(f"{h_min} ~ {h_max}")
        self._lbl_s_val.setText(f"{s_min} ~ {s_max}")
        self._lbl_v_val.setText(f"{v_min} ~ {v_max}")
        self._save_detection_params()

    def refresh_roi_tables(self):
        """ROI 테이블을 현재 ROI 매니저 상태로 갱신"""
        self._fill_table(self._table_video, self._roi_manager.video_rois)
        self._fill_table(self._table_audio, self._roi_manager.audio_rois)

    def _fill_table(self, table: _ROITable, rois):
        table.blockSignals(True)
        table.setRowCount(len(rois))
        for i, roi in enumerate(rois):
            for col, val in enumerate([roi.label, roi.media_name,
                                        roi.x, roi.y, roi.w, roi.h]):
                item = QTableWidgetItem(str(val))
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(i, col, item)
        table.blockSignals(False)

    def _on_table_item_changed(self, item: QTableWidgetItem):
        table = item.tableWidget()
        row = item.row()
        col = item.column()
        rois = (self._roi_manager.video_rois if table is self._table_video
                else self._roi_manager.audio_rois)
        if row >= len(rois):
            return
        roi = rois[row]
        try:
            if col == 1:
                roi.media_name = item.text()
            elif col == 2:
                roi.x = max(0, int(item.text()))
            elif col == 3:
                roi.y = max(0, int(item.text()))
            elif col == 4:
                roi.w = max(1, min(500, int(item.text())))
            elif col == 5:
                roi.h = max(1, min(250, int(item.text())))
        except ValueError:
            pass

    def _on_table_row_selected(self, roi_type: str):
        """테이블 행 선택 시 감지영역 선택 동기화 신호 발송"""
        table = self._table_video if roi_type == "video" else self._table_audio
        row = table.currentRow()
        self.roi_selection_changed.emit(roi_type, row)

    # ── ROI 관리 ──────────────────────────────────────

    def _add_roi_last(self, roi_type: str):
        """마지막 ROI를 복사하여 추가 (x,y +10씩 증가). ROI가 없으면 기본 ROI 추가."""
        rois = (self._roi_manager.video_rois if roi_type == "video"
                else self._roi_manager.audio_rois)
        if rois:
            last = rois[-1]
            new_x = min(last.x + 10, 1900)
            new_y = min(last.y + 10, 1060)
            if roi_type == "video":
                self._roi_manager.add_video_roi(new_x, new_y, last.w, last.h, last.media_name)
            else:
                self._roi_manager.add_audio_roi(new_x, new_y, last.w, last.h, last.media_name)
        else:
            if roi_type == "video":
                self._roi_manager.add_video_roi(10, 10, 100, 100)
            else:
                # 오디오 첫 추가: y=200 (레벨미터 위치 기본값)
                self._roi_manager.add_audio_roi(10, 200, 100, 100)
        self.refresh_roi_tables()
        self.roi_list_changed.emit(roi_type)

    def _delete_roi_rows(self, roi_type: str, rows: list):
        """지정된 행 목록을 역순으로 삭제"""
        for row in sorted(rows, reverse=True):
            if roi_type == "video":
                self._roi_manager.remove_video_roi(row)
            else:
                self._roi_manager.remove_audio_roi(row)
        self.refresh_roi_tables()
        self.roi_list_changed.emit(roi_type)

    def _delete_selected_roi(self, roi_type: str):
        """선택된 행(들) 삭제 버튼 핸들러 (다중 선택 지원)"""
        table = self._table_video if roi_type == "video" else self._table_audio
        rows = sorted(set(item.row() for item in table.selectedItems()), reverse=True)
        if rows:
            self._delete_roi_rows(roi_type, rows)

    def _reset_all_rois(self, roi_type: str):
        if roi_type == "video":
            self._roi_manager.replace_video_rois([])
        else:
            self._roi_manager.replace_audio_rois([])
        self.refresh_roi_tables()
        self.roi_list_changed.emit(roi_type)

    def _move_roi(self, roi_type: str, direction: int):
        """ROI 행 순서를 위/아래로 이동"""
        table = self._table_video if roi_type == "video" else self._table_audio
        row = table.currentRow()
        rois = (self._roi_manager.video_rois if roi_type == "video"
                else self._roi_manager.audio_rois)
        new_row = row + direction
        if 0 <= row < len(rois) and 0 <= new_row < len(rois):
            rois[row], rois[new_row] = rois[new_row], rois[row]
            # 레이블 재번호화
            prefix = "V" if roi_type == "video" else "A"
            for i, roi in enumerate(rois):
                roi.label = f"{prefix}{i + 1}"
            self.refresh_roi_tables()
            table.setCurrentCell(new_row, 0)
            self.roi_list_changed.emit(roi_type)

    # ── 편집 버튼 토글 ──────────────────────────────

    def _on_video_edit_toggled(self, checked: bool):
        if checked:
            self._btn_video_edit.setText("■  편집 완료 (클릭하여 종료)")
            if self._btn_audio_edit.isChecked():
                self._btn_audio_edit.setChecked(False)
                self._btn_audio_edit.setText("▶  오디오 감지영역 편집")
                self.halfscreen_edit_finished.emit()
            self.halfscreen_edit_requested.emit("video")
        else:
            self._btn_video_edit.setText("▶  비디오 감지영역 편집")
            self.halfscreen_edit_finished.emit()

    def _on_audio_edit_toggled(self, checked: bool):
        if checked:
            self._btn_audio_edit.setText("■  편집 완료 (클릭하여 종료)")
            if self._btn_video_edit.isChecked():
                self._btn_video_edit.setChecked(False)
                self._btn_video_edit.setText("▶  비디오 감지영역 편집")
                self.halfscreen_edit_finished.emit()
            self.halfscreen_edit_requested.emit("audio")
        else:
            self._btn_audio_edit.setText("▶  오디오 감지영역 편집")
            self.halfscreen_edit_finished.emit()

    def reset_edit_button(self, roi_type: str):
        """외부에서 편집 완료 시 버튼 상태 초기화"""
        if roi_type == "video":
            self._btn_video_edit.setChecked(False)
            self._btn_video_edit.setText("▶  비디오 감지영역 편집")
        else:
            self._btn_audio_edit.setChecked(False)
            self._btn_audio_edit.setText("▶  오디오 감지영역 편집")

    # ── 감지 파라미터 즉시 저장 ────────────────────────

    def _save_detection_params(self):
        """감지 파라미터를 config에 저장하고 신호 발송 (즉시 적용)"""
        params = self._get_current_detection_params()
        if "detection" not in self._config:
            self._config["detection"] = {}
        self._config["detection"].update(params)
        self.detection_params_changed.emit(params)

    def _reset_detection_params_to_default(self):
        """감도설정 전체 초기화 (DEFAULT_CONFIG 기본값으로 복구)"""
        self._apply_detection_params_to_ui(DEFAULT_CONFIG.get("detection", {}))
        self._save_detection_params()
        self._apply_performance_params_to_ui(DEFAULT_CONFIG.get("performance", {}))
        self._save_performance_params()

    # ── 성능 파라미터 저장 ──────────────────────────────

    def _get_current_performance_params(self) -> dict:
        """현재 성능 설정 UI 값을 dict로 반환"""
        return {
            "detection_interval":      self._combo_detect_interval.currentData(),
            "scale_factor":            self._combo_scale_factor.currentData(),
            "video_detection_enabled": self._chk_video_detect.isChecked(),
            "audio_detection_enabled": self._chk_audio_detect.isChecked(),
            "still_detection_enabled": self._chk_still_detect.isChecked(),
        }

    def _save_performance_params(self):
        """성능 파라미터를 config에 저장하고 신호 발송 (즉시 적용)"""
        params = self._get_current_performance_params()
        self._config["performance"] = params
        self.performance_params_changed.emit(params)

    def _run_benchmark(self):
        """컴퓨터 성능 측정 후 적정 파라미터 자동 결정"""
        self._btn_benchmark.setEnabled(False)
        self._lbl_benchmark.setText("측정 중...")
        QApplication.processEvents()

        dummy = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
        t0 = time.perf_counter()
        for _ in range(5):
            cv2.cvtColor(dummy, cv2.COLOR_BGR2HSV)
            dummy[100:350, 100:600].astype(np.float32)
        elapsed_ms = (time.perf_counter() - t0) / 5 * 1000

        cpu_cores = psutil.cpu_count(logical=False) or 1
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)

        if elapsed_ms < 20:
            interval, scale_val, grade = 100, 1.0, "고성능"
        elif elapsed_ms < 50:
            interval, scale_val, grade = 200, 1.0, "표준"
        elif elapsed_ms < 100:
            interval, scale_val, grade = 300, 0.5, "중간"
        else:
            interval, scale_val, grade = 500, 0.5, "저사양"

        for i in range(self._combo_detect_interval.count()):
            if self._combo_detect_interval.itemData(i) == interval:
                self._combo_detect_interval.blockSignals(True)
                self._combo_detect_interval.setCurrentIndex(i)
                self._combo_detect_interval.blockSignals(False)
                break
        for i in range(self._combo_scale_factor.count()):
            if abs(self._combo_scale_factor.itemData(i) - scale_val) < 0.01:
                self._combo_scale_factor.blockSignals(True)
                self._combo_scale_factor.setCurrentIndex(i)
                self._combo_scale_factor.blockSignals(False)
                break

        result = (f"{grade} — CPU {cpu_cores}코어, RAM {ram_gb:.0f}GB, "
                  f"처리 {elapsed_ms:.1f}ms → {interval}ms / "
                  f"해상도 {int(scale_val*100)}% 자동 적용")
        self._lbl_benchmark.setText(result)
        self._btn_benchmark.setEnabled(True)
        self._save_performance_params()

    def _show_performance_guide(self):
        """성능 설정 안내 다이얼로그 열기"""
        dlg = PerformanceGuideDialog(self)
        dlg.exec()

    # ── 포트 변경 ─────────────────────────────────────

    def _on_port_changed(self, index: int):
        port = self._combo_port.currentData()
        if port is not None:
            self._config["port"] = port
            self.port_changed.emit(port)

    # ── 닫기 이벤트 (X 버튼) ─────────────────────────

    def closeEvent(self, event):
        # 편집 버튼 상태 초기화
        if self._btn_video_edit.isChecked():
            self._btn_video_edit.setChecked(False)
            self._btn_video_edit.setText("▶  비디오 감지영역 편집")
            self.halfscreen_edit_finished.emit()
        if self._btn_audio_edit.isChecked():
            self._btn_audio_edit.setChecked(False)
            self._btn_audio_edit.setText("▶  오디오 감지영역 편집")
            self.halfscreen_edit_finished.emit()
        event.accept()

    # ── 외부 API ─────────────────────────────────────

    def get_config(self) -> dict:
        cfg = dict(self._config)
        cfg["telegram"] = self._get_telegram_params()
        cfg["recording"] = self._get_recording_params()
        return cfg

    def switch_to_tab(self, index: int):
        self._tabs.setCurrentIndex(index)

    # ── 알림설정 탭 헬퍼 ─────────────────────────────

    def _browse_sound_file(self, alarm_type: str):
        """WAV 파일 선택 다이얼로그"""
        init_dir = os.path.abspath(os.path.join("resources", "sounds"))
        os.makedirs(init_dir, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, f"알림음 파일 선택 ({alarm_type})",
            init_dir, "WAV 파일 (*.wav);;모든 파일 (*)"
        )
        if path:
            self._alarm_file_edits[alarm_type].setText(self._to_relative_if_possible(path))
            self._emit_alarm_settings()

    def _clear_sound_file(self, alarm_type: str):
        """알림음 파일 초기화 (기본값 사용)"""
        self._alarm_file_edits[alarm_type].clear()
        self._emit_alarm_settings()

    def _emit_alarm_settings(self):
        """현재 알림 설정을 신호로 발송"""
        params = self._get_alarm_params()
        self._config.setdefault("alarm", {}).update(params)
        self.alarm_settings_changed.emit(params)

    def _get_alarm_params(self) -> dict:
        """현재 알림 설정 dict 반환"""
        sound_files = {
            atype: self._alarm_file_edits[atype].text()
            for atype in self._alarm_file_edits
        }
        return {
            "sound_files": sound_files,
            "volume": self._config.get("alarm", {}).get("volume", 80),
            "sound_enabled": self._config.get("alarm", {}).get("sound_enabled", True),
        }

    def _load_alarm_config(self, config: dict):
        """알림 설정 UI 로드"""
        alarm = config.get("alarm", {})
        sound_files = alarm.get("sound_files", {})
        for atype, edit in self._alarm_file_edits.items():
            edit.setText(sound_files.get(atype, ""))

    def set_alarm_volume(self, value: int):
        """외부에서 볼륨 설정 (config 값만 갱신, 볼륨 슬라이더는 상단 바에서 관리)"""
        self._config.setdefault("alarm", {})["volume"] = value

    # ── 저장/불러오기 탭 핸들러 ──────────────────────

    def _on_save_clicked(self):
        """설정 파일 저장 버튼"""
        path, _ = QFileDialog.getSaveFileName(
            self, "설정 파일 저장", "", "JSON 파일 (*.json);;모든 파일 (*)"
        )
        if path:
            self.save_config_requested.emit(path)

    def _on_load_clicked(self):
        """설정 파일 불러오기 버튼"""
        path, _ = QFileDialog.getOpenFileName(
            self, "설정 파일 불러오기", "", "JSON 파일 (*.json);;모든 파일 (*)"
        )
        if path:
            self.load_config_requested.emit(path)

    def _on_reset_clicked(self):
        """기본값으로 초기화 버튼"""
        answer = QMessageBox.question(
            self, "초기화 확인",
            "모든 설정을 기본값으로 초기화합니다.\n이 작업은 되돌릴 수 없습니다.\n계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.reset_config_requested.emit()

    def reload_config(self, config: dict):
        """외부에서 config가 교체된 후 UI 전체 갱신"""
        self._config = dict(config)
        self._load_config(config)
