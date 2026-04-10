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

from utils.config_manager import DEFAULT_CONFIG

from PySide6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QGroupBox,
    QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QGridLayout,
    QScrollArea, QFileDialog, QMessageBox, QCheckBox, QApplication,
    QTextBrowser, QRadioButton, QSpinBox, QDoubleSpinBox,
    QAbstractSpinBox, QMenu,
)
from PySide6.QtCore import Qt, Signal, QEvent, QTimer

from core.roi_manager import ROIManager
from ui.dual_slider import DualSlider

# 버튼 높이 통일 상수 (QLineEdit/QComboBox min-height와 동일하게 유지)
_BTN_H = 30


class _TimePartWidget(QLabel):
    """시 또는 분 표시 라벨 (클릭/더블클릭은 부모 _TimeWidget 이벤트 필터가 처리)."""

    valueChanged = Signal(int)

    def __init__(self, values: list, input_range: tuple, parent=None):
        super().__init__(parent)
        self._values = list(values)
        self._input_range = input_range
        self._current = self._values[0]
        self._update_display()
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(52)
        self.setCursor(Qt.PointingHandCursor)

    def value(self) -> int:
        return self._current

    def setValue(self, v: int):
        lo, hi = self._input_range
        v = max(lo, min(hi, v))
        if v != self._current:
            self._current = v
            self._update_display()
            self.valueChanged.emit(v)

    def _update_display(self):
        self.setText(f"{self._current:02d}")

    def show_menu(self):
        """선택 리스트 팝업 표시 (부모 _TimeWidget에서 호출)."""
        menu = QMenu(self)
        for v in self._values:
            act = menu.addAction(f"{v:02d}")
            act.triggered.connect(lambda _, _v=v: self.setValue(_v))
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))


class _TimeWidget(QWidget):
    """시:분 입력 컨테이너.

    - 시/분 라벨 단일 클릭 : 선택 메뉴(숫자 리스트) 팝업
    - 시/분 라벨 더블 클릭 : 인라인 QLineEdit 오버레이 → HH:MM 수동 입력
                              ESC = 취소, Enter / 포커스 이탈 = 커밋
    - 콜론(:) 클릭         : 바로 수동 입력 에디터 표시
    """

    valueChanged = Signal()

    def __init__(self, default_h: int = 0, default_m: int = 0, parent=None):
        super().__init__(parent)
        inner_layout = QHBoxLayout(self)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(2)

        self._h = _TimePartWidget(list(range(24)), (0, 23))
        self._h.setValue(default_h)
        self._colon = QLabel(":")
        self._colon.setAlignment(Qt.AlignCenter)
        self._colon.setFixedWidth(10)
        self._m = _TimePartWidget(list(range(0, 60, 5)), (0, 59))
        self._m.setValue(default_m)

        inner_layout.addWidget(self._h)
        inner_layout.addWidget(self._colon)
        inner_layout.addWidget(self._m)

        # 인라인 에디터
        self._editor = QLineEdit(self)
        self._editor.setAlignment(Qt.AlignCenter)
        self._editor.setPlaceholderText("HH:MM")
        self._editor.hide()
        self._editor.returnPressed.connect(self._commit)
        self._editor.installEventFilter(self)

        # _h / _m / _colon 어디를 클릭해도 이 컨테이너가 가로챔
        self._h.installEventFilter(self)
        self._m.installEventFilter(self)
        self._colon.installEventFilter(self)
        self._h.valueChanged.connect(self.valueChanged)
        self._m.valueChanged.connect(self.valueChanged)

        # 싱글클릭/더블클릭 구분용 타이머
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click_timeout)
        self._pending_part: '_TimePartWidget | None' = None

        # MouseButtonPress 기반 더블클릭 직접 추적
        # (팝업 메뉴 닫기 클릭이 첫 번째 클릭을 소비하는 문제 해결)
        self._last_press_time: float = 0.0
        self._last_press_obj: '_TimePartWidget | None' = None

    # ── 공개 인터페이스 ───────────────────────────────────────────────

    def hour(self) -> int:
        return self._h.value()

    def minute(self) -> int:
        return self._m.value()

    def setTime(self, h: int, m: int):
        """시그널 없이 조용히 시:분 설정."""
        self._h.blockSignals(True)
        self._h.setValue(h)
        self._h.blockSignals(False)
        self._m.blockSignals(True)
        self._m.setValue(m)
        self._m.blockSignals(False)

    # ── 이벤트 필터 ──────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        # ① 시/분/콜론 영역 마우스 이벤트 처리
        if obj in (self._h, self._m, self._colon):
            t = event.type()
            if t == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    if obj is self._colon:
                        # 콜론 클릭 → 타이머 취소 후 바로 에디터
                        self._click_timer.stop()
                        self._pending_part = None
                        self._last_press_time = 0.0
                        self._last_press_obj = None
                        self._show_editor()
                    else:
                        # Press 기반 더블클릭 직접 감지
                        # (팝업 메뉴 닫기 클릭이 Qt의 DblClick 이벤트를 소비하는 문제 방지)
                        now = time.monotonic()
                        dbl_sec = QApplication.doubleClickInterval() / 1000.0
                        is_dblclick = (
                            now - self._last_press_time < dbl_sec
                            and self._last_press_obj is obj
                        )
                        self._last_press_time = now
                        self._last_press_obj = obj

                        if is_dblclick:
                            # 더블클릭 확정 → 에디터 표시
                            self._click_timer.stop()
                            self._pending_part = None
                            self._last_press_time = 0.0  # 연속 더블클릭 방지
                            self._show_editor()
                        else:
                            # 싱글클릭 → 더블클릭 대기 후 메뉴 표시
                            self._pending_part = obj
                            self._click_timer.start(QApplication.doubleClickInterval())
                    return True
            elif t == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.LeftButton:
                    # MouseButtonPress에서 이미 처리됐을 수 있으나 안전장치로 유지
                    self._click_timer.stop()
                    self._pending_part = None
                    self._last_press_time = 0.0
                    self._show_editor()
                    return True

        # ② 인라인 에디터 키 처리
        if obj is self._editor:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key_Escape:
                    self._editor.hide()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                self._commit()

        return super().eventFilter(obj, event)

    def _on_single_click_timeout(self):
        """싱글클릭 확정: 해당 파트(시/분) 선택 메뉴 표시."""
        part = self._pending_part
        self._pending_part = None
        if part is not None:
            part.show_menu()

    # ── 내부 메서드 ──────────────────────────────────────────────────

    def _show_editor(self):
        """위젯 전체를 덮는 인라인 에디터 표시."""
        self._editor.setGeometry(self.rect())
        self._editor.setText(f"{self.hour():02d}:{self.minute():02d}")
        self._editor.selectAll()
        self._editor.show()
        self._editor.raise_()
        self._editor.setFocus()

    def _commit(self):
        """인라인 에디터 값을 파싱하여 시:분에 반영."""
        text = self._editor.text().strip()
        try:
            if ":" in text:
                h_s, m_s = text.split(":", 1)
                h, m = int(h_s), int(m_s)
            elif len(text) == 4:
                h, m = int(text[:2]), int(text[2:])
            else:
                self._editor.hide()
                return
            self._h.setValue(h)
            self._m.setValue(m)
        except ValueError:
            pass
        self._editor.hide()


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

버튼을 누르면 **현재 설정된 감지영역과 감지 옵션 그대로** 처리 부하를 직접 측정한 후
최적 감지 주기를 자동으로 설정합니다.

### 측정 방식

PC 사양이 아닌 **실제 감지 작업 부하**를 기준으로 합니다.

- 현재 설정된 비디오 감지영역 × (블랙 + 스틸 감지) 처리 시간
- 현재 설정된 오디오 감지영역 × HSV 색상 감지 처리 시간
- 현재 설정된 감지 해상도 (스케일 팩터) 적용 후 측정

10회 반복 평균으로 1회 처리 시간을 계산하며,
처리 시간이 감지 주기의 50% 이하가 되는 최소 주기를 자동 적용합니다.

| 1회 처리 시간 | 자동 설정 주기 |
|-------------|--------------|
| 50ms 미만 | 100ms |
| 50~100ms | 200ms |
| 100~150ms | 300ms |
| 150~250ms | 500ms |
| 250ms 이상 | 1000ms |

> **팁:** 감지영역이 많거나 오디오 ROI가 많을수록 처리 시간이 길어집니다.
> 처리 시간이 500ms를 초과하면 해상도를 낮추거나 감지 항목을 줄이세요.

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


class _SignoffRoiDialog(QDialog):
    """
    정파 감지영역 선택 다이얼로그.

    상단: 진입 트리거 — 스틸 감지로 정파 진입/해제를 판단할 ROI 1개 선택.
    하단: 알림 억제 대상 — 정파(준비/모드) 중 일반 알림을 끄고 싶은 ROI 다중 선택.
          비디오 ROI와 오디오 레벨미터 ROI를 구분하여 표시.
          트리거로 선택한 ROI는 자동으로 체크된다.
    """

    def __init__(self, enter_label: str, suppressed_labels: list,
                 video_rois: list, audio_rois: list = None, parent=None):
        """
        enter_label      : 현재 진입 트리거 label (str)
        suppressed_labels: 현재 억제 대상 label 목록 (list[str], 비디오+오디오 통합)
        video_rois       : [(label, media_name), ...] 형식
        audio_rois       : [(label, media_name), ...] 형식 (없으면 빈 목록)
        """
        super().__init__(parent)
        self.setWindowTitle("감지영역 선택")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setMinimumHeight(340)

        self._enter_label: str = enter_label
        self._suppressed_labels: list = list(suppressed_labels)
        self._video_rois_info: list = list(video_rois)
        self._audio_rois_info: list = list(audio_rois) if audio_rois else []

        # 억제 대상 체크박스 목록 {label: QCheckBox}
        self._suppress_chks: dict = {}
        # 현재 자동 체크+비활성화된 트리거 label 추적
        self._auto_checked_label: str = ""

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── 진입 트리거 ──────────────────────────────────────────────────
        trigger_group = QGroupBox("진입 트리거")
        trigger_group.setToolTip(
            "스틸 감지로 정파 진입/해제를 판단할 비디오 감지영역을 선택합니다.\n"
            "정파준비(Preparation) 구간에서 이 영역이 정해진 시간 이상 스틸이면\n"
            "정파모드(Signoff)로 조기 진입합니다."
        )
        trigger_layout = QHBoxLayout(trigger_group)
        trigger_layout.setContentsMargins(8, 6, 8, 6)

        self._trigger_combo = QComboBox()
        self._trigger_combo.addItem("(선택 없음)", userData="")
        for lbl, media in self._video_rois_info:
            display = f"{lbl}  ({media})" if media else lbl
            self._trigger_combo.addItem(display, userData=lbl)
        idx = self._trigger_combo.findData(self._enter_label)
        if idx >= 0:
            self._trigger_combo.setCurrentIndex(idx)
        self._trigger_combo.currentIndexChanged.connect(self._on_trigger_changed)
        trigger_layout.addWidget(self._trigger_combo, 1)
        layout.addWidget(trigger_group)

        # ── 알림 억제 대상 ───────────────────────────────────────────────
        suppress_group = QGroupBox("알림 억제 대상")
        suppress_group.setToolTip(
            "정파준비/정파모드 중 일반 알림(블랙·스틸·오디오 등)을 끄고 싶은\n"
            "감지영역을 선택합니다. 트리거로 선택한 영역은 자동으로 포함됩니다.\n\n"
            "예) 1TV 온에어가 트리거이면, 1TV MPEG ENC도 여기서 체크하면\n"
            "    정파 중에 1TV MPEG ENC의 알림이 억제됩니다."
        )
        suppress_layout = QVBoxLayout(suppress_group)
        suppress_layout.setContentsMargins(8, 6, 8, 6)
        suppress_layout.setSpacing(4)

        # 비디오 ROI 체크박스
        video_section_lbl = QLabel("▶ 비디오 감지영역")
        video_section_lbl.setStyleSheet("font-weight: bold; margin-top: 2px;")
        suppress_layout.addWidget(video_section_lbl)
        if self._video_rois_info:
            for lbl, media in self._video_rois_info:
                display = f"{lbl}  ({media})" if media else lbl
                chk = QCheckBox(display)
                chk.setChecked(lbl in self._suppressed_labels)
                self._suppress_chks[lbl] = chk
                suppress_layout.addWidget(chk)
        else:
            suppress_layout.addWidget(QLabel("  (비디오 감지영역 없음)"))

        # 오디오 레벨미터 ROI 체크박스
        audio_section_lbl = QLabel("▶ 오디오 레벨미터 감지영역")
        audio_section_lbl.setStyleSheet("font-weight: bold; margin-top: 6px;")
        suppress_layout.addWidget(audio_section_lbl)
        if self._audio_rois_info:
            for lbl, media in self._audio_rois_info:
                display = f"{lbl}  ({media})" if media else lbl
                chk = QCheckBox(display)
                chk.setChecked(lbl in self._suppressed_labels)
                self._suppress_chks[lbl] = chk
                suppress_layout.addWidget(chk)
        else:
            suppress_layout.addWidget(QLabel("  (오디오 레벨미터 감지영역 없음)"))

        # 진입 트리거 ROI 자동 체크 (동기화)
        self._sync_trigger_suppress()

        layout.addWidget(suppress_group, 1)

        # ── 확인 / 취소 ──────────────────────────────────────────────────
        ok_row = QHBoxLayout()
        ok_row.addStretch()
        btn_ok = QPushButton("확인")
        btn_ok.setFixedWidth(80)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _on_trigger_changed(self):
        """트리거 콤보 변경 시 해당 ROI를 억제 대상에 자동 체크+비활성화."""
        self._sync_trigger_suppress()

    def _sync_trigger_suppress(self):
        """
        트리거 label에 해당하는 체크박스를 자동 체크+비활성화한다.
        이전 트리거 체크박스는 체크 해제 후 다시 활성화한다.
        """
        new_trigger = self._trigger_combo.currentData() or ""

        # 이전 트리거 복원: 체크 해제 + 활성화 + 툴팁 제거
        if self._auto_checked_label and self._auto_checked_label != new_trigger:
            old_chk = self._suppress_chks.get(self._auto_checked_label)
            if old_chk:
                old_chk.setChecked(False)
                old_chk.setEnabled(True)
                old_chk.setToolTip("")

        # 새 트리거 자동 체크 + 비활성화
        if new_trigger and new_trigger in self._suppress_chks:
            chk = self._suppress_chks[new_trigger]
            chk.setChecked(True)
            chk.setEnabled(False)
            chk.setToolTip("진입 트리거로 선택된 항목은 자동으로 억제됩니다")

        self._auto_checked_label = new_trigger

    def get_result(self) -> tuple:
        """(enter_label: str, suppressed_labels: list[str]) 반환."""
        enter_label = self._trigger_combo.currentData() or ""
        suppressed = [lbl for lbl, chk in self._suppress_chks.items() if chk.isChecked()]
        # enter_label이 억제 목록에 없으면 자동 포함
        if enter_label and enter_label not in suppressed:
            suppressed.insert(0, enter_label)
        return enter_label, suppressed


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
    signoff_settings_changed = Signal(dict)   # 정파 설정 변경
    system_settings_changed = Signal(dict)    # 시스템 설정 변경 (자동 재시작)

    def __init__(self, config: dict, roi_manager: ROIManager, parent=None):
        super().__init__(parent)
        self._config = dict(config)
        self._roi_manager = roi_manager
        self.setWindowTitle("설정")
        self.setMinimumWidth(1000)
        self.setMinimumHeight(700)
        self.resize(1000, 700)
        self.setModal(False)
        # 정파설정 위젯 참조 딕셔너리 초기화
        self._signoff_name_edit: dict = {}       # {gid: QLineEdit}
        self._signoff_start_edit: dict = {}          # {gid: _TimeWidget}
        self._signoff_end_edit: dict = {}            # {gid: _TimeWidget}
        self._signoff_end_next_day_chk: dict = {}    # {gid: QCheckBox} 종료 익일 여부
        self._signoff_every_day_chk: dict = {}   # {gid: QPushButton}
        self._signoff_day_chks: dict = {}        # {gid: list[QCheckBox]}
        self._signoff_enter_label: dict = {}     # {gid: str}        진입 트리거 label
        self._signoff_suppressed_labels: dict = {}  # {gid: list[str]} 알림 억제 대상 labels
        self._signoff_roi_summary: dict = {}     # {gid: QLabel}  요약 라벨
        self._signoff_prep_min_combo: dict = {}        # {gid: QComboBox} 정파준비 몇 분전 설정
        self._signoff_exit_prep_min_combo: dict = {}   # {gid: QComboBox} 정파해제준비 몇 분전 설정
        self._signoff_exit_trigger_combo: dict = {}    # {gid: QComboBox} 정파해제 트리거 시간(초)
        self._signoff_auto_prep_btn = None            # QCheckBox: 자동 정파 준비 활성화
        self._signoff_hint_fns: dict = {}             # {gid: (prep_hint_fn, exit_prep_hint_fn)}
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
        self._tabs.addTab(self._create_tab_signoff(),           "정파설정")
        self._tabs.addTab(self._create_tab_alarm(),             "알림설정")
        self._tabs.addTab(self._create_tab_save_load(),         "저장/불러오기")

    # ── 탭 1: 입력선택 ────────────────────────────────

    def _create_tab_input(self) -> QWidget:
        # 스크롤 영역으로 감싸기 (내용이 길어 최소 크기 초과 시 창 떨림 방지)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        scroll.setWidget(inner)          # Qt 소유권 즉시 이전 → Python GC 삭제 방지
        layout = QVBoxLayout(inner)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── 캡처 포트 그룹 ──
        group = QGroupBox("캡처 포트")
        port_row = QHBoxLayout(group)

        lbl = QLabel("포트 번호:")
        lbl.setAlignment(Qt.AlignVCenter)
        port_row.addWidget(lbl)

        self._combo_port = QComboBox()
        for i in range(6):
            self._combo_port.addItem(str(i), i)
        self._combo_port.setFixedWidth(80)
        self._combo_port.setToolTip("0~5 고정 선택 / 선택 즉시 소스 변경")
        self._combo_port.currentIndexChanged.connect(self._on_port_changed)
        port_row.addWidget(self._combo_port)
        port_row.addStretch()

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
        self._edit_video_file.setFixedHeight(_BTN_H)
        file_row.addWidget(self._edit_video_file, 1, Qt.AlignVCenter)

        btn_browse_file = QPushButton("찾아보기")
        btn_browse_file.setMinimumWidth(80)
        btn_browse_file.setFixedHeight(_BTN_H)
        btn_browse_file.clicked.connect(self._browse_video_file)
        file_row.addWidget(btn_browse_file, 0, Qt.AlignVCenter)

        btn_clear_file = QPushButton("초기화")
        btn_clear_file.setMinimumWidth(72)
        btn_clear_file.setFixedHeight(_BTN_H)
        btn_clear_file.clicked.connect(self._clear_video_file)
        file_row.addWidget(btn_clear_file, 0, Qt.AlignVCenter)

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
        lbl_dir = QLabel("저장 폴더:")
        lbl_dir.setFixedHeight(_BTN_H)
        lbl_dir.setAlignment(Qt.AlignVCenter)
        dir_row.addWidget(lbl_dir, 0, Qt.AlignVCenter)
        self._edit_rec_dir = QLineEdit()
        self._edit_rec_dir.setPlaceholderText("recordings")
        self._edit_rec_dir.setFixedHeight(_BTN_H)
        self._edit_rec_dir.editingFinished.connect(self._save_recording_params)
        dir_row.addWidget(self._edit_rec_dir, 1, Qt.AlignVCenter)

        btn_browse_dir = QPushButton("찾아보기")
        btn_browse_dir.setFixedHeight(_BTN_H)
        btn_browse_dir.setMinimumWidth(80)
        btn_browse_dir.clicked.connect(self._browse_rec_dir)
        dir_row.addWidget(btn_browse_dir, 0, Qt.AlignVCenter)

        btn_open_dir = QPushButton("폴더 열기")
        btn_open_dir.setFixedHeight(_BTN_H)
        btn_open_dir.setMinimumWidth(80)
        btn_open_dir.clicked.connect(self._open_rec_dir)
        dir_row.addWidget(btn_open_dir, 0, Qt.AlignVCenter)

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

        # ── 녹화 출력 설정 그룹 ──
        group_output = QGroupBox("녹화 출력 설정")
        output_layout = QGridLayout(group_output)
        output_layout.setSpacing(8)

        output_layout.addWidget(QLabel("출력 해상도:"), 0, 0)
        self._combo_rec_resolution = QComboBox()
        for label, (w, h) in [
            ("1920×1080 (원본)", (1920, 1080)),
            ("960×540 (기본값)", (960, 540)),
            ("640×360",          (640, 360)),
            ("480×270",          (480, 270)),
        ]:
            self._combo_rec_resolution.addItem(label, (w, h))
        self._combo_rec_resolution.setCurrentIndex(1)  # 960×540 기본
        self._combo_rec_resolution.currentIndexChanged.connect(self._on_rec_output_changed)
        output_layout.addWidget(self._combo_rec_resolution, 0, 1)

        output_layout.addWidget(QLabel("출력 FPS:"), 1, 0)
        self._combo_rec_fps = QComboBox()
        for fps in [5, 10, 15, 20, 25, 30]:
            self._combo_rec_fps.addItem(f"{fps} fps", fps)
        self._combo_rec_fps.setCurrentIndex(1)  # 10fps 기본
        self._combo_rec_fps.currentIndexChanged.connect(self._on_rec_output_changed)
        output_layout.addWidget(self._combo_rec_fps, 1, 1)

        output_layout.setColumnStretch(2, 1)
        layout.addWidget(group_output)

        self._rec_info_lbl = QLabel()
        self._rec_info_lbl.setObjectName("settingsInfoLabel")
        self._rec_info_lbl.setStyleSheet("color: #808090; font-size: 11px;")
        layout.addWidget(self._rec_info_lbl)
        self._update_rec_info_label()

        layout.addStretch()

        # ── 영상설정 탭 전체 초기화 버튼 ──
        separator = self._make_separator()
        layout.addWidget(separator)

        btn_reset_input_tab = QPushButton("영상설정 전체 초기화")
        btn_reset_input_tab.setFixedHeight(_BTN_H)
        btn_reset_input_tab.setToolTip(
            "포트, 파일 입력, 자동 녹화 설정을 모두 기본값으로 초기화합니다."
        )
        btn_reset_input_tab.clicked.connect(self._reset_input_tab)
        layout.addWidget(btn_reset_input_tab)

        return scroll

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
        scroll.setWidget(inner)          # Qt 소유권 즉시 이전 → Python GC 삭제 방지
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

        # 행 0: 밝기 임계값
        lbl_bt = QLabel("▪  밝기 임계값:")
        lbl_bt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_threshold = _NumEdit(10, 0, 255)
        self._edit_black_threshold.editingFinished.connect(self._save_detection_params)
        desc_bt = QLabel("0~255 / 값이 낮을수록 더 어두운 영상만 감지  (기본값: 5)")
        desc_bt.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_bt,                    0, 0)
        grid_b.addWidget(self._edit_black_threshold, 0, 1)
        grid_b.addWidget(desc_bt,                   0, 2)

        # 행 1: 어두운 픽셀 비율 임계값
        lbl_bdr = QLabel("▪  어두운 픽셀 비율 임계값(%):")
        lbl_bdr.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_dark_ratio = _NumEdit(95.0, 50.0, 100.0, is_float=True)
        self._edit_black_dark_ratio.editingFinished.connect(self._save_detection_params)
        desc_bdr = QLabel(
            "• 50~100% / 감지영역 픽셀 중 이 비율 이상이 어두우면 블랙으로 판단  (기본값: 98%)<br>"
            "• 높일수록 엄격 (자막 등 밝은 요소 허용 줄임)  /  낮출수록 느슨 (자막 있어도 블랙 감지)"
        )
        desc_bdr.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_bdr,                     1, 0)
        grid_b.addWidget(self._edit_black_dark_ratio,  1, 1)
        grid_b.addWidget(desc_bdr,                    1, 2)

        # 행 2: 모션 억제 비율 (블랙 판정 후 움직임으로 취소)
        lbl_bmsr = QLabel("▪  모션 억제 비율(%):")
        lbl_bmsr.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_motion_suppress_ratio = _NumEdit(0.5, 0.0, 5.0, is_float=True)
        self._edit_black_motion_suppress_ratio.editingFinished.connect(self._save_detection_params)
        desc_bmsr = QLabel(
            "• 0.0~5.0% / 블랙 판정 시 움직임(changed)이 이 비율 이상이면 블랙 취소  (기본값: 0.2%)<br>"
            "• 스크롤 자막 등 미세 움직임 있는 화면의 오감지 방지 / 0.0 = 억제 비활성"
        )
        desc_bmsr.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_bmsr,                                2, 0)
        grid_b.addWidget(self._edit_black_motion_suppress_ratio,  2, 1)
        grid_b.addWidget(desc_bmsr,                               2, 2)

        # 행 3: 몇 초 이상시 알림 발생
        lbl_btr = QLabel("▪  몇 초 이상시 알림 발생(초):")
        lbl_btr.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_duration = _NumEdit(20, 1, 120)
        self._edit_black_duration.editingFinished.connect(self._save_detection_params)
        desc_btr = QLabel("1~120초 / 블랙이 이 시간 이상 지속되면 알림 발생  (기본값: 20초)")
        desc_btr.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_btr,                  3, 0)
        grid_b.addWidget(self._edit_black_duration, 3, 1)
        grid_b.addWidget(desc_btr,                 3, 2)

        # 행 4: 알림 지속시간
        lbl_bad = QLabel("▪  알림 지속시간(초):")
        lbl_bad.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_black_alarm_duration = _NumEdit(10, 1, 60)
        self._edit_black_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_bad = QLabel("1~60초 / 알림 발생 시 소리를 울리는 시간  (기본값: 60초)")
        desc_bad.setObjectName("paramDescLabel")
        grid_b.addWidget(lbl_bad,                        4, 0)
        grid_b.addWidget(self._edit_black_alarm_duration, 4, 1)
        grid_b.addWidget(desc_bad,                       4, 2)

        layout.addWidget(group_black)

        # ── 스틸 감지 그룹 ──
        group_still = QGroupBox("스틸 감지")
        grid_s = QGridLayout(group_still)
        grid_s.setHorizontalSpacing(12)
        grid_s.setVerticalSpacing(8)
        grid_s.setColumnStretch(2, 1)

        # 행 0: 픽셀당 변화 기준값 (노이즈 필터)
        lbl_st = QLabel("▪  픽셀당 변화 기준값:")
        lbl_st.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_threshold = _NumEdit(8, 0, 255)
        self._edit_still_threshold.editingFinished.connect(self._save_detection_params)
        desc_st = QLabel(
            "• 0~255 / 각 픽셀의 밝기 차이가 이 값 이상이면 '변화한 픽셀'로 분류  (기본값: 4)<br>"
            "• 높을수록 인코더 압축 노이즈를 무시  /  낮을수록 미세한 변화도 움직임으로 감지"
        )
        desc_st.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_st,                    0, 0)
        grid_s.addWidget(self._edit_still_threshold, 0, 1)
        grid_s.addWidget(desc_st,                   0, 2)

        # 행 1: 블록 움직임 임계값 (5×5 블록 기반 판정)
        lbl_sbt = QLabel("▪  블록 움직임 임계값(%):")
        lbl_sbt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_block_threshold = _NumEdit(10.0, 1.0, 50.0, is_float=True)
        self._edit_still_block_threshold.editingFinished.connect(self._save_detection_params)
        desc_sbt = QLabel(
            "• 1.0~50.0% / 화면을 5×5 블록으로 나눠 블록 하나라도 이 비율 이상 변화하면 스틸 아님  (기본값: 10%)<br>"
            "• 낮출수록 민감 (작은 움직임도 감지)  /  높일수록 둔감 (큰 움직임만 인정)"
        )
        desc_sbt.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_sbt,                          1, 0)
        grid_s.addWidget(self._edit_still_block_threshold,  1, 1)
        grid_s.addWidget(desc_sbt,                         1, 2)

        # 행 2: 타이머 리셋 프레임 수 (히스테리시스)
        lbl_srf = QLabel("▪  타이머 리셋 프레임 수:")
        lbl_srf.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_reset_frames = _NumEdit(3, 1, 10)
        self._edit_still_reset_frames.editingFinished.connect(self._save_detection_params)
        desc_srf = QLabel(
            "• 1~10프레임 / 이 프레임 수만큼 연속으로 정상이어야 스틸 타이머 리셋  (기본값: 3)<br>"
            "• 높을수록 MPEG 아티팩트 1프레임에도 타이머 유지 / 1 = 기존 동작 (즉시 리셋)"
        )
        desc_srf.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_srf,                        2, 0)
        grid_s.addWidget(self._edit_still_reset_frames,  2, 1)
        grid_s.addWidget(desc_srf,                       2, 2)

        # 행 3: 몇 초 이상시 알림 발생
        lbl_str = QLabel("▪  몇 초 이상시 알림 발생(초):")
        lbl_str.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_duration = _NumEdit(60, 1, 120)
        self._edit_still_duration.editingFinished.connect(self._save_detection_params)
        desc_str = QLabel("1~120초 / 스틸이 이 시간 이상 지속되면 알림 발생  (기본값: 60초)")
        desc_str.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_str,                  3, 0)
        grid_s.addWidget(self._edit_still_duration, 3, 1)
        grid_s.addWidget(desc_str,                 3, 2)

        # 행 4: 알림 지속시간
        lbl_sad = QLabel("▪  알림 지속시간(초):")
        lbl_sad.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_still_alarm_duration = _NumEdit(10, 1, 60)
        self._edit_still_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_sad = QLabel("1~60초 / 알림 발생 시 소리를 울리는 시간  (기본값: 60초)")
        desc_sad.setObjectName("paramDescLabel")
        grid_s.addWidget(lbl_sad,                        4, 0)
        grid_s.addWidget(self._edit_still_alarm_duration, 4, 1)
        grid_s.addWidget(desc_sad,                       4, 2)

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
        desc_h = QLabel("0~179 / OpenCV HSV H값  (기본값: 40~95 — 초록 계열)")
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
        desc_s = QLabel("0~255 / 색의 선명도  (기본값: 80~255)")
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
        desc_v = QLabel("0~255 / 밝기  (기본값: 60~255)")
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
        self._edit_audio_pixel_ratio = _NumEdit(5, 1, 50)
        self._edit_audio_pixel_ratio.editingFinished.connect(self._save_detection_params)
        desc_apr = QLabel(
            "• 1~50% / ROI 내 HSV 범위 픽셀 비율이 이 값 이상이면 활성  (기본값: 5%)\n"
            "• 소리 없음 → 초록색 없음 → ratio < 5% → is_active=False → 이상(무음) → 알람"
        )
        desc_apr.setObjectName("paramDescLabel")
        desc_apr.setWordWrap(True)
        grid_a2.addWidget(lbl_apr,                     0, 0)
        grid_a2.addWidget(self._edit_audio_pixel_ratio, 0, 1)
        grid_a2.addWidget(desc_apr,                    0, 2)

        lbl_ald = QLabel("▪  몇 초 이상시 알림 발생(초):")
        lbl_ald.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_audio_level_duration = _NumEdit(20, 1, 120)
        self._edit_audio_level_duration.editingFinished.connect(self._save_detection_params)
        desc_ald = QLabel("1~120초 / 레벨미터 비활성이 이 시간 이상 지속되면 알림  (기본값: 20초)")
        desc_ald.setObjectName("paramDescLabel")
        grid_a2.addWidget(lbl_ald,                      1, 0)
        grid_a2.addWidget(self._edit_audio_level_duration, 1, 1)
        grid_a2.addWidget(desc_ald,                     1, 2)

        lbl_alad = QLabel("▪  알림 지속시간(초):")
        lbl_alad.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_audio_level_alarm_duration = _NumEdit(10, 1, 60)
        self._edit_audio_level_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_alad = QLabel("1~60초 / 알림 발생 시 소리를 울리는 시간  (기본값: 60초)")
        desc_alad.setObjectName("paramDescLabel")
        grid_a2.addWidget(lbl_alad,                          2, 0)
        grid_a2.addWidget(self._edit_audio_level_alarm_duration, 2, 1)
        grid_a2.addWidget(desc_alad,                         2, 2)

        lbl_alrs = QLabel("▪  복구 딜레이(초):")
        lbl_alrs.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_audio_level_recovery_seconds = _NumEdit(2, 0, 10)
        self._edit_audio_level_recovery_seconds.editingFinished.connect(self._save_detection_params)
        desc_alrs = QLabel("0~10초 / 알림 발생 후 정상 복구 판정을 위한 최소 정상 지속 시간  (기본값: 2초, 0=즉시복구)")
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
        self._edit_embedded_silence_duration = _NumEdit(20, 1, 120)
        self._edit_embedded_silence_duration.editingFinished.connect(self._save_detection_params)
        desc_esd = QLabel("1~120초 / 무음이 이 시간 이상 지속되면 알림 발생  (기본값: 20초)")
        desc_esd.setObjectName("paramDescLabel")
        grid_e.addWidget(lbl_esd,                             1, 0)
        grid_e.addWidget(self._edit_embedded_silence_duration, 1, 1)
        grid_e.addWidget(desc_esd,                            1, 2)

        lbl_ead = QLabel("▪  알림 지속시간(초):")
        lbl_ead.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit_embedded_alarm_duration = _NumEdit(10, 1, 60)
        self._edit_embedded_alarm_duration.editingFinished.connect(self._save_detection_params)
        desc_ead = QLabel("1~60초 / 알림 발생 시 소리를 울리는 시간  (기본값: 60초)")
        desc_ead.setObjectName("paramDescLabel")
        grid_e.addWidget(lbl_ead,                              2, 0)
        grid_e.addWidget(self._edit_embedded_alarm_duration,  2, 1)
        grid_e.addWidget(desc_ead,                             2, 2)

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

        lbl_bde = QLabel("▪  블랙 감지:")
        lbl_bde.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chk_black_detect = QCheckBox("블랙 감지 활성화")
        self._chk_black_detect.setChecked(True)
        self._chk_black_detect.stateChanged.connect(self._save_performance_params)
        desc_bde = QLabel("비활성화 시 밝기 계산 생략")
        desc_bde.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_bde,                  2, 0)
        grid_p.addWidget(self._chk_black_detect,   2, 1)
        grid_p.addWidget(desc_bde,                 2, 2)

        lbl_sde = QLabel("▪  스틸 감지:")
        lbl_sde.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chk_still_detect = QCheckBox("정지 영상 감지 활성화")
        self._chk_still_detect.setChecked(True)
        self._chk_still_detect.stateChanged.connect(self._save_performance_params)
        desc_sde = QLabel("비활성화 시 프레임 간 비교 연산 생략")
        desc_sde.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_sde,                  3, 0)
        grid_p.addWidget(self._chk_still_detect,   3, 1)
        grid_p.addWidget(desc_sde,                 3, 2)

        lbl_ade = QLabel("▪  오디오 레벨미터 감지:")
        lbl_ade.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chk_audio_detect = QCheckBox("HSV 색상 감지 활성화")
        self._chk_audio_detect.setChecked(True)
        self._chk_audio_detect.stateChanged.connect(self._save_performance_params)
        desc_ade = QLabel("비활성화 시 HSV 전체변환 생략 — 가장 효과적인 부하 절감")
        desc_ade.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_ade,                  4, 0)
        grid_p.addWidget(self._chk_audio_detect,   4, 1)
        grid_p.addWidget(desc_ade,                 4, 2)

        lbl_ede = QLabel("▪  임베디드 오디오:")
        lbl_ede.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._chk_embedded_detect = QCheckBox("임베디드 오디오 감지 활성화")
        self._chk_embedded_detect.setChecked(True)
        self._chk_embedded_detect.stateChanged.connect(self._save_performance_params)
        desc_ede = QLabel("비활성화 시 무음 감지 연산 생략")
        desc_ede.setObjectName("paramDescLabel")
        grid_p.addWidget(lbl_ede,                   5, 0)
        grid_p.addWidget(self._chk_embedded_detect, 5, 1)
        grid_p.addWidget(desc_ede,                  5, 2)

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

        # ── 전체 초기화 버튼 ──
        separator = self._make_separator()
        layout.addWidget(separator)

        btn_reset_all = QPushButton("감도설정 전체 초기화")
        btn_reset_all.setFixedHeight(_BTN_H)
        btn_reset_all.clicked.connect(self._reset_detection_params_to_default)
        layout.addWidget(btn_reset_all)

        return scroll

    # ── 탭 5: 알림설정 ───────────────────────────────

    def _create_tab_alarm(self) -> QWidget:
        """탭 5: 알림설정 — 알림음 파일 선택 (단일 통합 알림음)"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        scroll.setWidget(inner)          # Qt 소유권 즉시 이전 → Python GC 삭제 방지
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
        lbl.setFixedHeight(_BTN_H)
        file_row.addWidget(lbl, 0, Qt.AlignVCenter)

        self._alarm_file_edits: dict = {}
        path_edit = QLineEdit()
        path_edit.setReadOnly(True)
        path_edit.setPlaceholderText("(Windows 내장 경고음 사용 — SystemHand)")
        path_edit.setFixedHeight(_BTN_H)
        self._alarm_file_edits["default"] = path_edit
        file_row.addWidget(path_edit, 1, Qt.AlignVCenter)

        btn_browse = QPushButton("찾아보기")
        btn_browse.setMinimumWidth(80)
        btn_browse.setFixedHeight(_BTN_H)
        btn_browse.clicked.connect(lambda: self._browse_sound_file("default"))
        file_row.addWidget(btn_browse, 0, Qt.AlignVCenter)

        btn_clear = QPushButton("초기화")
        btn_clear.setMinimumWidth(72)
        btn_clear.setFixedHeight(_BTN_H)
        btn_clear.clicked.connect(lambda: self._clear_sound_file("default"))
        file_row.addWidget(btn_clear, 0, Qt.AlignVCenter)

        btn_test = QPushButton("테스트")
        btn_test.setMinimumWidth(72)
        btn_test.setFixedHeight(_BTN_H)
        btn_test.clicked.connect(
            lambda: self.test_sound_requested.emit(self._alarm_file_edits["default"].text())
        )
        file_row.addWidget(btn_test, 0, Qt.AlignVCenter)

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

        layout.addWidget(self._make_separator())

        # ── 자동 재시작 설정 그룹 ──
        group_restart = QGroupBox("자동 재시작")
        restart_layout = QVBoxLayout(group_restart)
        restart_layout.setSpacing(6)

        restart_grid = QGridLayout()
        restart_grid.setColumnStretch(2, 1)
        restart_grid.setHorizontalSpacing(8)
        restart_grid.setVerticalSpacing(6)

        self._chk_restart_1 = QCheckBox("재시작 시각 1:")
        self._chk_restart_1.stateChanged.connect(self._save_system_params)
        self._restart_time = _TimeWidget(3, 0)
        self._restart_time.valueChanged.connect(self._save_system_params)
        restart_grid.addWidget(self._chk_restart_1, 0, 0)
        restart_grid.addWidget(self._restart_time, 0, 1)

        self._chk_restart_2 = QCheckBox("재시작 시각 2:")
        self._chk_restart_2.stateChanged.connect(self._save_system_params)
        self._restart_time_2 = _TimeWidget(15, 0)
        self._restart_time_2.valueChanged.connect(self._save_system_params)
        restart_grid.addWidget(self._chk_restart_2, 1, 0)
        restart_grid.addWidget(self._restart_time_2, 1, 1)

        restart_layout.addLayout(restart_grid)

        restart_hint = QLabel("프로그램을 종료 후 재시작하여 OS 리소스(GDI, 메모리 등)를 초기화합니다.")
        restart_hint.setStyleSheet("color: #888888; font-size: 11px;")
        restart_hint.setWordWrap(True)
        restart_layout.addWidget(restart_hint)

        layout.addWidget(group_restart)
        layout.addStretch()

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
        w, h = self._combo_rec_resolution.currentData()
        return {
            "enabled": self._chk_recording_enabled.isChecked(),
            "save_dir": self._edit_rec_dir.text() or "recordings",
            "pre_seconds": self._edit_pre_seconds.get_value(),
            "post_seconds": self._edit_post_seconds.get_value(),
            "max_keep_days": self._edit_max_days.get_value(),
            "output_width": w,
            "output_height": h,
            "output_fps": self._combo_rec_fps.currentData(),
        }

    def _save_recording_params(self):
        """녹화 설정을 config에 저장하고 신호 발송"""
        params = self._get_recording_params()
        self._config["recording"] = params
        self.recording_settings_changed.emit(params)

    def _on_rec_output_changed(self):
        """해상도/FPS 콤보박스 변경 시 정보 라벨 갱신 후 저장"""
        self._update_rec_info_label()
        self._save_recording_params()

    def _update_rec_info_label(self):
        """현재 해상도/FPS/버퍼 메모리를 계산해 정보 라벨 갱신"""
        if not hasattr(self, "_combo_rec_resolution"):
            return
        w, h = self._combo_rec_resolution.currentData() or (960, 540)
        fps = self._combo_rec_fps.currentData() or 10
        try:
            pre = self._edit_pre_seconds.get_value()
        except Exception:
            pre = 5
        # JPEG 85% 기준 약 8:1 압축비로 버퍼 메모리 추정
        buf_mb = (w * h * 3 / 8) * (pre * fps) / 1024 / 1024
        post = 15
        try:
            post = self._edit_post_seconds.get_value()
        except Exception:
            pass
        duration = pre + post
        # 파일 크기 추정: 비압축 대비 mp4v 약 1/15 압축
        size_mb_low  = int(w * h * 3 * fps * duration / 15 / 1024 / 1024 * 0.7)
        size_mb_high = int(w * h * 3 * fps * duration / 15 / 1024 / 1024 * 1.3)
        self._rec_info_lbl.setText(
            f"출력 해상도: {w}×{h}  |  FPS: {fps}  |  버퍼 메모리: 약 {buf_mb:.1f} MB\n"
            f"녹화 파일 크기: 약 {size_mb_low}~{size_mb_high} MB / {duration}초  |  코덱: mp4v"
        )

    def _reset_input_tab(self):
        """영상설정 탭 전체를 기본값으로 초기화"""
        from utils.config_manager import DEFAULT_CONFIG
        default_rec = DEFAULT_CONFIG.get("recording", {})

        # 포트 초기화
        self._combo_port.blockSignals(True)
        self._combo_port.setCurrentIndex(0)
        self._combo_port.blockSignals(False)
        self.port_changed.emit(0)

        # 파일 입력 초기화
        self._clear_video_file()

        # 녹화 설정 초기화
        self._chk_recording_enabled.blockSignals(True)
        self._chk_recording_enabled.setChecked(bool(default_rec.get("enabled", False)))
        self._chk_recording_enabled.blockSignals(False)

        self._edit_rec_dir.setText(default_rec.get("save_dir", "recordings"))
        self._edit_pre_seconds.setText(str(int(default_rec.get("pre_seconds", 5))))
        self._edit_post_seconds.setText(str(int(default_rec.get("post_seconds", 15))))
        self._edit_max_days.setText(str(int(default_rec.get("max_keep_days", 7))))

        # 해상도/FPS 초기화 (960×540, 10fps)
        default_w = default_rec.get("output_width", 960)
        default_h = default_rec.get("output_height", 540)
        default_fps = default_rec.get("output_fps", 10)
        self._set_rec_resolution_combo(default_w, default_h)
        self._set_rec_fps_combo(default_fps)

        self._update_rec_info_label()
        self._save_recording_params()

    def _set_rec_resolution_combo(self, w: int, h: int):
        """해상도 콤보박스에서 (w, h)와 일치하는 항목 선택"""
        for i in range(self._combo_rec_resolution.count()):
            if self._combo_rec_resolution.itemData(i) == (w, h):
                self._combo_rec_resolution.setCurrentIndex(i)
                return
        # 일치하는 항목 없으면 첫 번째(원본) 선택
        self._combo_rec_resolution.setCurrentIndex(0)

    def _set_rec_fps_combo(self, fps: int):
        """FPS 콤보박스에서 fps 값과 일치하는 항목 선택"""
        for i in range(self._combo_rec_fps.count()):
            if self._combo_rec_fps.itemData(i) == fps:
                self._combo_rec_fps.setCurrentIndex(i)
                return
        self._combo_rec_fps.setCurrentIndex(1)  # 10fps 기본

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
        # 해상도/FPS 콤보 복원
        self._combo_rec_resolution.blockSignals(True)
        self._combo_rec_fps.blockSignals(True)
        self._set_rec_resolution_combo(
            int(rec.get("output_width", 960)),
            int(rec.get("output_height", 540)),
        )
        self._set_rec_fps_combo(int(rec.get("output_fps", 10)))
        self._combo_rec_resolution.blockSignals(False)
        self._combo_rec_fps.blockSignals(False)
        self._update_rec_info_label()

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
        lbl_version = QLabel("KBS Peacock v1.6.19")
        about_layout.addWidget(lbl_version, 0, 1)

        about_layout.addWidget(QLabel("Date:"), 1, 0)
        lbl_date = QLabel("2026-04-10")
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
        self._edit_black_threshold.setText(str(int(det.get("black_threshold", 5))))
        self._edit_black_dark_ratio.setText(str(float(det.get("black_dark_ratio", 98.0))))
        self._edit_black_duration.setText(str(int(det.get("black_duration", 20))))
        self._edit_black_alarm_duration.setText(str(int(det.get("black_alarm_duration", 10))))
        self._edit_black_motion_suppress_ratio.setText(str(float(det.get("black_motion_suppress_ratio", 0.2))))
        self._edit_still_threshold.setText(str(int(det.get("still_threshold", 4))))
        self._edit_still_block_threshold.setText(str(float(det.get("still_block_threshold", 15.0))))
        self._edit_still_duration.setText(str(int(det.get("still_duration", 60))))
        self._edit_still_alarm_duration.setText(str(int(det.get("still_alarm_duration", 10))))
        self._edit_still_reset_frames.setText(str(int(det.get("still_reset_frames", 3))))

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
        self._chk_black_detect.blockSignals(True)
        self._chk_still_detect.blockSignals(True)
        self._chk_audio_detect.blockSignals(True)
        self._chk_embedded_detect.blockSignals(True)
        self._chk_black_detect.setChecked(bool(perf.get("black_detection_enabled", True)))
        self._chk_still_detect.setChecked(bool(perf.get("still_detection_enabled", True)))
        self._chk_audio_detect.setChecked(bool(perf.get("audio_detection_enabled", True)))
        self._chk_embedded_detect.setChecked(bool(perf.get("embedded_detection_enabled", True)))
        self._chk_black_detect.blockSignals(False)
        self._chk_still_detect.blockSignals(False)
        self._chk_audio_detect.blockSignals(False)
        self._chk_embedded_detect.blockSignals(False)

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
        self._apply_signoff_params_to_ui(config.get("signoff", {}))
        self._load_system_config(config)
        self.refresh_roi_tables()

    def _get_current_detection_params(self) -> dict:
        h_min, h_max = self._slider_hsv_h.get_range()
        s_min, s_max = self._slider_hsv_s.get_range()
        v_min, v_max = self._slider_hsv_v.get_range()
        return {
            "black_threshold":               self._edit_black_threshold.get_value(),
            "black_dark_ratio":              self._edit_black_dark_ratio.get_value(),
            "black_duration":                self._edit_black_duration.get_value(),
            "black_alarm_duration":          self._edit_black_alarm_duration.get_value(),
            "black_motion_suppress_ratio":   self._edit_black_motion_suppress_ratio.get_value(),
            "still_threshold":               self._edit_still_threshold.get_value(),
            "still_block_threshold":         self._edit_still_block_threshold.get_value(),
            "still_duration":                self._edit_still_duration.get_value(),
            "still_alarm_duration":          self._edit_still_alarm_duration.get_value(),
            "still_reset_frames":            self._edit_still_reset_frames.get_value(),
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
        self._refresh_signoff_roi_tags()

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
                self._refresh_signoff_roi_tags()
            elif col == 2:
                roi.x = max(0, int(item.text()))
            elif col == 3:
                roi.y = max(0, int(item.text()))
            elif col == 4:
                roi.w = max(1, min(500, int(item.text())))
            elif col == 5:
                roi.h = max(1, min(300, int(item.text())))
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
            "detection_interval":        self._combo_detect_interval.currentData(),
            "scale_factor":              self._combo_scale_factor.currentData(),
            "black_detection_enabled":   self._chk_black_detect.isChecked(),
            "still_detection_enabled":   self._chk_still_detect.isChecked(),
            "audio_detection_enabled":   self._chk_audio_detect.isChecked(),
            "embedded_detection_enabled": self._chk_embedded_detect.isChecked(),
        }

    def _save_performance_params(self):
        """성능 파라미터를 config에 저장하고 신호 발송 (즉시 적용)"""
        params = self._get_current_performance_params()
        self._config["performance"] = params
        self.performance_params_changed.emit(params)

    def _run_benchmark(self):
        """현재 감지 설정(ROI + 감지 항목 + 해상도) 기반 실제 처리 성능 측정 후 적정 주기 자동 결정"""
        video_rois  = self._roi_manager.video_rois
        audio_rois  = self._roi_manager.audio_rois
        black_on    = self._chk_black_detect.isChecked()
        still_on    = self._chk_still_detect.isChecked()
        audio_on    = self._chk_audio_detect.isChecked()
        embedded_on = self._chk_embedded_detect.isChecked()

        has_work = (
            (video_rois and (black_on or still_on)) or
            (audio_rois and audio_on) or
            embedded_on
        )
        if not has_work:
            self._lbl_benchmark.setText(
                "감지영역 또는 활성화된 감지 항목이 없습니다. "
                "비디오/오디오 감지영역을 먼저 설정하고 감지 항목을 하나 이상 활성화하세요."
            )
            return

        self._btn_benchmark.setEnabled(False)
        self._lbl_benchmark.setText("현재 감지 설정으로 성능 측정 중...")
        QApplication.processEvents()

        sf = self._combo_scale_factor.currentData()

        # 1920×1080 더미 프레임 (실제 입력 해상도 기준)
        frame_orig = np.random.randint(30, 200, (1080, 1920, 3), dtype=np.uint8)
        if sf < 1.0:
            frame = cv2.resize(frame_orig, None, fx=sf, fy=sf,
                               interpolation=cv2.INTER_AREA)
        else:
            frame = frame_orig

        fh, fw = frame.shape[:2]
        # HSV 범위 (처리 시간 측정이 목적이므로 범위값은 무관)
        lower = np.array([0, 30, 30], dtype=np.uint8)
        upper = np.array([180, 255, 255], dtype=np.uint8)

        N_ITER = 10
        prev_frames: dict = {}

        t0 = time.perf_counter()
        for _ in range(N_ITER):
            # 비디오 ROI — 블랙/스틸 감지 시뮬레이션
            for roi in video_rois:
                x1 = max(0, int(roi.x * sf))
                y1 = max(0, int(roi.y * sf))
                x2 = min(fw, int((roi.x + roi.w) * sf))
                y2 = min(fh, int((roi.y + roi.h) * sf))
                if x2 <= x1 or y2 <= y1:
                    continue
                crop = frame[y1:y2, x1:x2]
                if black_on:
                    float(np.mean(crop.mean(axis=2)))
                if still_on:
                    crop_f = crop.astype(np.float32)
                    lbl = roi.label
                    if lbl in prev_frames:
                        prev = prev_frames[lbl]
                        if prev.shape == crop_f.shape:
                            float(np.mean(np.abs(crop_f - prev)))
                    prev_frames[lbl] = crop_f

            # 오디오 ROI — HSV 감지 시뮬레이션
            if audio_on:
                for roi in audio_rois:
                    x1 = max(0, int(roi.x * sf))
                    y1 = max(0, int(roi.y * sf))
                    x2 = min(fw, int((roi.x + roi.w) * sf))
                    y2 = min(fh, int((roi.y + roi.h) * sf))
                    if x2 <= x1 or y2 <= y1:
                        continue
                    crop_bgr = frame[y1:y2, x1:x2]
                    crop_hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(crop_hsv, lower, upper)
                    int(np.sum(mask > 0))

            # 임베디드 오디오 — 무음 감지 시뮬레이션 (update_embedded_silence와 동일한 연산)
            if embedded_on:
                _now = time.perf_counter()
                _elapsed = _now - t0

        elapsed_ms = (time.perf_counter() - t0) / N_ITER * 1000

        # 최적 주기 결정: 처리 시간이 주기의 50% 이하가 되는 최소 단계
        candidates = [100, 200, 300, 500, 1000]
        target_interval = next(
            (c for c in candidates if elapsed_ms <= c * 0.5),
            1000
        )

        # 감지 주기 콤보박스 자동 적용
        for i in range(self._combo_detect_interval.count()):
            if self._combo_detect_interval.itemData(i) == target_interval:
                self._combo_detect_interval.blockSignals(True)
                self._combo_detect_interval.setCurrentIndex(i)
                self._combo_detect_interval.blockSignals(False)
                break

        # 결과 문자열 조합
        detect_parts = []
        if video_rois:
            modes = [m for m, on in [("블랙", black_on), ("스틸", still_on)] if on]
            if modes:
                detect_parts.append(f"영상 {len(video_rois)}개({'+'.join(modes)})")
        if audio_rois and audio_on:
            detect_parts.append(f"오디오 {len(audio_rois)}개(HSV)")
        if embedded_on:
            detect_parts.append("임베디드")

        detect_str = " + ".join(detect_parts)
        sf_pct = int(sf * 100)
        result = (
            f"[{detect_str} | 해상도 {sf_pct}%]  "
            f"1회 처리 {elapsed_ms:.1f}ms → {target_interval}ms 주기 자동 적용"
        )
        if elapsed_ms > 500:
            result += "  ※ 처리 부하가 높습니다. 해상도를 낮추거나 감지 항목을 줄이세요."

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
        cfg["signoff"] = self._get_signoff_params()
        self._save_system_params()
        cfg["system"] = self._config.get("system", {})
        return cfg

    def switch_to_tab(self, index: int):
        self._tabs.setCurrentIndex(index)

    # ── 정파설정 탭 ──────────────────────────────────

    def _create_tab_signoff(self) -> QWidget:
        """정파설정 탭 생성"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        tab_inner = QWidget()
        scroll.setWidget(tab_inner)   # Qt 소유권 즉시 이전
        tab_layout = QVBoxLayout(tab_inner)
        tab_layout.setAlignment(Qt.AlignTop)
        tab_layout.setContentsMargins(10, 10, 10, 10)
        tab_layout.setSpacing(10)

        # ── 자동 정파 설정 ON/OFF ──
        auto_prep_group = QGroupBox("자동 정파 설정")
        auto_prep_v = QVBoxLayout(auto_prep_group)
        auto_prep_v.setSpacing(6)

        prep_chk_row = QHBoxLayout()
        self._signoff_auto_prep_btn = QCheckBox("자동정파 활성화")
        self._signoff_auto_prep_btn.setChecked(True)
        self._signoff_auto_prep_btn.setToolTip(
            "ON: 설정된 시간이 되면 자동으로 정파준비/정파모드로 전환\n"
            "OFF: 시간이 되어도 자동 전환 없음 — 수동 버튼 조작만 가능"
        )
        self._signoff_auto_prep_btn.toggled.connect(self._on_auto_prep_toggled)
        prep_chk_row.addWidget(self._signoff_auto_prep_btn)
        prep_chk_row.addStretch()
        auto_prep_v.addLayout(prep_chk_row)

        prep_guide_row = QHBoxLayout()
        btn_guide = QPushButton("자동정파 안내")
        btn_guide.setFixedHeight(_BTN_H)
        btn_guide.clicked.connect(self._show_signoff_guide)
        prep_guide_row.addWidget(btn_guide)
        prep_guide_row.addStretch()
        auto_prep_v.addLayout(prep_guide_row)

        tab_layout.addWidget(auto_prep_group)

        # Group1 / Group2 (공통 설정 아래에 배치)
        for gid in (1, 2):
            tab_layout.addWidget(self._create_signoff_group_widget(gid))

        # ── 정파 알림음 (최하단) ──
        sound_group = QGroupBox("정파 알림음")
        sound_grid = QGridLayout(sound_group)
        sound_grid.setSpacing(8)

        self._signoff_sound_edits: dict = {}
        sound_items = [
            ("prep",    "정파준비 시작:"),
            ("enter",   "정파모드 진입:"),
            ("release", "정파 해제:"),
        ]
        for row_i, (key, lbl_text) in enumerate(sound_items):
            sound_grid.addWidget(QLabel(lbl_text), row_i, 0)
            edit = QLineEdit()
            edit.setPlaceholderText("기본 알림음 사용")
            edit.setMinimumWidth(100)
            edit.setFixedHeight(_BTN_H)
            edit.editingFinished.connect(self._save_signoff_params)
            self._signoff_sound_edits[key] = edit
            sound_grid.addWidget(edit, row_i, 1)

            btn_browse = QPushButton("파일 선택")
            btn_browse.setFixedHeight(_BTN_H)
            btn_browse.clicked.connect(lambda _, k=key: self._browse_signoff_sound(k))
            sound_grid.addWidget(btn_browse, row_i, 2)

            btn_test = QPushButton("테스트")
            btn_test.setMinimumWidth(72)
            btn_test.setFixedHeight(_BTN_H)
            btn_test.clicked.connect(
                lambda _, k=key: self.test_sound_requested.emit(self._signoff_sound_edits[k].text())
            )
            sound_grid.addWidget(btn_test, row_i, 3)

        sound_grid.setColumnStretch(1, 1)
        tab_layout.addWidget(sound_group)

        # ── 정파설정 전체 초기화 버튼 ──
        separator2 = self._make_separator()
        tab_layout.addWidget(separator2)
        btn_reset_signoff = QPushButton("정파설정 전체 초기화")
        btn_reset_signoff.setFixedHeight(_BTN_H)
        btn_reset_signoff.setToolTip("정파 설정을 모두 기본값으로 되돌립니다")
        btn_reset_signoff.clicked.connect(self._reset_signoff_params)
        tab_layout.addWidget(btn_reset_signoff)

        tab_layout.addStretch()

        return scroll

    def _show_signoff_guide(self):
        """자동정파 안내 팝업 표시."""
        dlg = QDialog(self)
        dlg.setWindowTitle("자동정파 안내")
        dlg.setMinimumWidth(780)

        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(10)
        vbox.setContentsMargins(16, 14, 16, 14)

        lbl = QLabel()
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        lbl.setText(
            "<div style='line-height: 2.0;'>"
            "<span style='font-size: 13pt; font-weight: bold;'>"
            "1. 정파준비 구간 (초록색)</span><br>"
            "• 「몇 분전 정파준비 활성화」에서 설정한 시간을 기준으로 정파준비(초록색)로 자동 전환<br>"
            "• 예) 정파모드 시작 00:30, 30분 전 설정 → 00:00에 정파준비 시작<br>"
            "• 「정파 감지영역」에 설정된 비디오 감지영역에서 스틸이 지속되면 정파모드로 조기 전환<br>"
            "• 「몇 분전」을 「사용안함」으로 설정하면 정파준비 단계 없이 시작 시각에 바로 정파모드로 전환<br>"
            "<br>"
            "<span style='font-size: 13pt; font-weight: bold;'>"
            "2. 정파모드 (빨간색)</span><br>"
            "• 「정파모드 시작」 시각이 되면 자동으로 정파모드(빨간색)로 전환<br>"
            "• 정파모드 중에는 해당 감지영역의 블랙/스틸 알림이 억제<br>"
            "• 「종료」 시각이 되면 자동으로 정파 해제<br>"
            "• 「몇 분전 정파해제준비 활성화」 설정 시:<br> " \
            "   종료 N분 전, 감지영역이 더 이상 스틸이 아님을 감지하면 종료 시각 전이라도 자동 해제<br>"
            "<br>"
            "<span style='font-size: 13pt; font-weight: bold;'>"
            "3. 상단 정파 버튼 수동 조작</span><br>"
            "• 클릭마다 비활성(회색) → 정파준비(초록) → 정파모드(빨강) → 비활성(회색) 순으로 전환<br>"
            "• 단, 정파 시간대 밖에서는 정파준비 → 비활성으로 직접 복귀 (정파모드 전환 불가)<br>"
            "<br>"
            "<span style='font-size: 13pt; font-weight: bold;'>"
            "4. Group1 / Group2</span><br>"
            "• 두 그룹이 독립적으로 운영되어 서로 다른 채널과 시간대를 동시에 관리 가능"
            "</div>"
        )
        vbox.addWidget(lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("확인")
        btn_ok.setFixedHeight(_BTN_H)
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_ok)
        vbox.addLayout(btn_row)

        dlg.adjustSize()
        dlg.exec()

    def _on_auto_prep_toggled(self, checked: bool):
        """자동 정파 준비 모드 체크박스 토글 처리."""
        self._save_signoff_params()

    def _reset_signoff_params(self):
        """정파 설정 전체 초기화."""
        defaults = {
            "auto_preparation":    True,
            "prep_alarm_sound":    "resources/sounds/sign_off.wav",
            "enter_alarm_sound":   "resources/sounds/sign_off.wav",
            "release_alarm_sound": "resources/sounds/sign_off.wav",
            "group1": {
                "name":              "1TV",
                "enter_roi":         {"video_label": ""},
                "suppressed_labels": [],
                "start_time":        "03:30",
                "end_time":          "05:00",
                "prep_minutes":      150,
                "exit_prep_minutes": 180,
                "weekdays":          [0, 1],
            },
            "group2": {
                "name":              "2TV",
                "enter_roi":         {"video_label": ""},
                "suppressed_labels": [],
                "start_time":        "02:00",
                "end_time":          "05:00",
                "prep_minutes":      90,
                "exit_prep_minutes": 30,
                "weekdays":          [0, 1, 2, 3, 4, 5, 6],
            },
        }
        self._apply_signoff_params_to_ui(defaults)
        self._save_signoff_params()

    def _create_signoff_group_widget(self, gid: int) -> QGroupBox:
        """단일 정파 그룹(Group1/Group2) 설정 위젯 반환.
        배치 순서: 시간 → 정파준비 시간 → 요일 → 감지영역 선택
        """
        box = QGroupBox(f"Group {gid}")
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(10)

        # 그룹명 행
        name_row = QHBoxLayout()
        lbl_name = QLabel("① 그룹명:")
        lbl_name.setObjectName("signoffRowLabel")
        name_row.addWidget(lbl_name)
        name_edit = QLineEdit()
        name_edit.setFixedWidth(120)
        name_edit.setPlaceholderText(f"Group{gid}")
        name_row.addWidget(name_edit)
        name_row.addStretch()
        box_layout.addLayout(name_row)
        self._signoff_name_edit[gid] = name_edit

        # ── 1) 시간 행 (정파모드 시작/종료) ──────────────────────────────
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("② 정파모드 시작:"))

        start_tw = _TimeWidget(0, 30)
        start_tw.setToolTip("클릭: 숫자 리스트 선택  |  더블클릭: 직접 입력\n"
                            "이 시각에 정파모드(빨간색)로 전환됩니다")
        time_row.addWidget(start_tw)

        time_row.addSpacing(16)
        time_row.addWidget(QLabel("종료:"))

        end_tw = _TimeWidget(6, 0)
        end_tw.setToolTip("클릭: 숫자 리스트 선택  |  더블클릭: 직접 입력\n"
                          "이 시각에 정파가 해제됩니다")
        time_row.addWidget(end_tw)

        end_next_day_chk = QCheckBox("익일")
        end_next_day_chk.setToolTip("종료 시간이 다음날 기준이면 체크\n예) 시작 23:30 → 익일 06:00")
        time_row.addWidget(end_next_day_chk)

        time_row.addStretch()
        box_layout.addLayout(time_row)

        self._signoff_start_edit[gid] = start_tw
        self._signoff_end_edit[gid] = end_tw
        self._signoff_end_next_day_chk[gid] = end_next_day_chk

        # ── 2) 정파준비 시간 행 ──────────────────────────────────────────
        prep_row = QHBoxLayout()
        prep_row.addWidget(QLabel("③ 몇 분전 정파준비 활성화:"))

        from PySide6.QtWidgets import QComboBox
        prep_combo = QComboBox()
        prep_combo.setFixedWidth(120)
        prep_options = [
            ("사용안함", 0),
            ("30분 전", 30),
            ("1시간 전", 60),
            ("1시간 30분 전", 90),
            ("2시간 전", 120),
            ("2시간 30분 전", 150),
            ("3시간 전", 180),
        ]
        for label_text, val in prep_options:
            prep_combo.addItem(label_text, userData=val)
        prep_combo.setCurrentIndex(1)  # 기본값: 30분 전
        prep_combo.setToolTip("정파모드 시작 전 몇 분 전에 정파준비(초록색)로 전환할지 설정\n"
                              "정파준비 구간에서 스틸이 지속되면 정파모드 조기 전환 가능")
        prep_row.addWidget(prep_combo)
        prep_time_lbl = QLabel("")
        prep_time_lbl.setObjectName("signoffTimeHintLabel")
        prep_row.addWidget(prep_time_lbl)
        prep_row.addStretch()
        box_layout.addLayout(prep_row)
        self._signoff_prep_min_combo[gid] = prep_combo

        # ── 3) 정파해제준비 시간 행 ─────────────────────────────────────
        exit_prep_row = QHBoxLayout()
        exit_prep_row.addWidget(QLabel("④ 몇 분전 정파해제준비 활성화:"))

        from PySide6.QtWidgets import QComboBox as _QComboBox2
        exit_prep_combo = _QComboBox2()
        exit_prep_combo.setFixedWidth(120)
        exit_prep_options = [
            ("사용 안 함", 0),
            ("30분 전", 30),
            ("1시간 전", 60),
            ("1시간 30분 전", 90),
            ("2시간 전", 120),
            ("2시간 30분 전", 150),
            ("3시간 전", 180),
        ]
        for label_text, val in exit_prep_options:
            exit_prep_combo.addItem(label_text, userData=val)
        exit_prep_combo.setCurrentIndex(0)  # 기본값: 사용 안 함
        exit_prep_combo.setToolTip(
            "정파 종료 전 몇 분 전부터 정파해제준비 구간을 활성화할지 설정\n"
            "이 구간에서 스틸 신호가 해제되면 종료 시각 전이라도 정파가 자동 해제됩니다"
        )
        exit_prep_row.addWidget(exit_prep_combo)
        exit_prep_time_lbl = QLabel("")
        exit_prep_time_lbl.setObjectName("signoffTimeHintLabel")
        exit_prep_row.addWidget(exit_prep_time_lbl)
        exit_prep_row.addStretch()
        box_layout.addLayout(exit_prep_row)
        self._signoff_exit_prep_min_combo[gid] = exit_prep_combo

        # ── 3-1) 정파해제 트리거 시간 행 ─────────────────────────────────
        exit_trigger_row = QHBoxLayout()
        exit_trigger_row.addWidget(QLabel("⑤ 몇 초 이상시 정파해제:"))

        from PySide6.QtWidgets import QComboBox as _QComboBox3
        exit_trigger_combo = _QComboBox3()
        exit_trigger_combo.setFixedWidth(120)
        exit_trigger_options = [
            ("즉시", 0),
            ("3초", 3),
            ("5초 (기본)", 5),
            ("10초", 10),
            ("15초", 15),
            ("30초", 30),
        ]
        for label_text, val in exit_trigger_options:
            exit_trigger_combo.addItem(label_text, userData=val)
        exit_trigger_combo.setCurrentIndex(2)  # 기본값: 5초
        exit_trigger_combo.setToolTip(
            "정파해제준비 구간에서 비-스틸 상태가 N초 이상 연속 지속되어야 정파가 해제됩니다.\n"
            "순간적인 화면 변화에 의한 오동작을 방지합니다.\n"
            "'즉시'로 설정하면 비-스틸 감지 즉시 해제됩니다."
        )
        exit_trigger_row.addWidget(exit_trigger_combo)
        exit_trigger_row.addStretch()
        box_layout.addLayout(exit_trigger_row)
        self._signoff_exit_trigger_combo[gid] = exit_trigger_combo

        # ── 4) 요일 행 ──────────────────────────────────────────────────
        day_row = QHBoxLayout()
        day_row.addWidget(QLabel("⑤ 요일:"))
        day_row.addSpacing(4)
        every_btn = QPushButton("매일")
        every_btn.setCheckable(True)
        every_btn.setChecked(True)
        day_row.addWidget(every_btn)
        day_row.addSpacing(8)
        day_names = ["월", "화", "수", "목", "금", "토", "일"]
        day_chks = []
        for dname in day_names:
            chk = QCheckBox(dname)
            chk.setChecked(True)
            day_row.addWidget(chk)
            day_chks.append(chk)
        hint_lbl = QLabel("(정파준비 시작 시각이 속한 날의 요일 기준 · 준비가 전날 밤이면 전날 요일 선택)")
        hint_lbl.setStyleSheet("color: gray; font-size: 11px;")
        day_row.addSpacing(8)
        day_row.addWidget(hint_lbl)
        day_row.addStretch()
        box_layout.addLayout(day_row)
        self._signoff_every_day_chk[gid] = every_btn
        self._signoff_day_chks[gid] = day_chks

        # '매일' 버튼 클릭 → 전체 선택 / 전체 해제 토글
        def _on_every_day_clicked(checked, chks=day_chks):
            for c in chks:
                c.blockSignals(True)
                c.setChecked(checked)
                c.blockSignals(False)
            self._save_signoff_params()
        every_btn.clicked.connect(_on_every_day_clicked)

        # ── 5) 감지영역 선택 행 (정파 진입/해제 감지용) ────────────────────
        roi_row = QHBoxLayout()
        lbl_roi = QLabel("⑥ 정파 감지영역:")
        lbl_roi.setObjectName("signoffRowLabel")
        lbl_roi.setToolTip(
            "스틸 감지로 정파 진입/해제를 판단할 비디오 감지영역을 선택합니다.\n\n"
            "• 정파준비 → 정파: 선택된 영역에서 스틸이 지속되면 정파로 조기 전환\n"
            "• 정파 → 정파해제: 정파해제준비 구간에서 스틸이 해제 상태가\n"
            "  '몇 초 이상시 정파해제' 시간 이상 지속되면 자동 해제"
        )
        roi_row.addWidget(lbl_roi)
        btn_roi = QPushButton("감지영역 선택")
        btn_roi.clicked.connect(lambda _, g=gid: self._open_signoff_roi_dialog(g))
        roi_row.addWidget(btn_roi)

        roi_summary = QLabel("선택 없음")
        roi_summary.setStyleSheet("color: #888;")
        roi_row.addWidget(roi_summary)
        roi_row.addStretch()
        box_layout.addLayout(roi_row)

        self._signoff_enter_label[gid] = ""
        self._signoff_suppressed_labels[gid] = []
        self._signoff_roi_summary[gid] = roi_summary

        # 시그널 연결
        name_edit.textChanged.connect(self._save_signoff_params)

        def _auto_check_next_day(s=start_tw, e=end_tw, chk=end_next_day_chk):
            """시작/종료 시각 변경 시 종료가 시작보다 이르면 익일 자동 체크."""
            start_min = s.hour() * 60 + s.minute()
            end_min = e.hour() * 60 + e.minute()
            chk.blockSignals(True)
            chk.setChecked(end_min < start_min)
            chk.blockSignals(False)
            self._save_signoff_params()

        start_tw.valueChanged.connect(_auto_check_next_day)
        end_tw.valueChanged.connect(_auto_check_next_day)
        end_next_day_chk.stateChanged.connect(self._save_signoff_params)
        prep_combo.currentIndexChanged.connect(self._save_signoff_params)
        exit_prep_combo.currentIndexChanged.connect(self._save_signoff_params)
        exit_trigger_combo.currentIndexChanged.connect(self._save_signoff_params)

        def _update_prep_hint(*_, s=start_tw, c=prep_combo, lbl=prep_time_lbl):
            minutes = c.currentData()
            if not minutes:
                lbl.setText("")
                return
            total = (s.hour() * 60 + s.minute() - minutes) % 1440
            lbl.setText(f"{total // 60:02d}:{total % 60:02d}에 정파준비 시작")

        def _update_exit_prep_hint(*_, e=end_tw, c=exit_prep_combo, lbl=exit_prep_time_lbl):
            minutes = c.currentData()
            if not minutes:
                lbl.setText("")
                return
            total = (e.hour() * 60 + e.minute() - minutes) % 1440
            lbl.setText(f"{total // 60:02d}:{total % 60:02d}에 정파해제준비 시작")

        start_tw.valueChanged.connect(_update_prep_hint)
        prep_combo.currentIndexChanged.connect(_update_prep_hint)
        end_tw.valueChanged.connect(_update_exit_prep_hint)
        exit_prep_combo.currentIndexChanged.connect(_update_exit_prep_hint)
        _update_prep_hint()
        _update_exit_prep_hint()
        self._signoff_hint_fns[gid] = (_update_prep_hint, _update_exit_prep_hint)
        # every_btn 저장은 _on_every_day_clicked 내부에서 처리
        for chk in day_chks:
            chk.stateChanged.connect(self._save_signoff_params)

        return box

    def _open_signoff_roi_dialog(self, gid: int):
        """감지영역 선택 다이얼로그를 열고 결과를 저장한다."""
        video_rois = [(r.label, r.media_name) for r in self._roi_manager.video_rois]
        audio_rois = [(r.label, r.media_name) for r in self._roi_manager.audio_rois]
        enter_label = self._signoff_enter_label.get(gid, "")
        suppressed_labels = self._signoff_suppressed_labels.get(gid, [])

        dlg = _SignoffRoiDialog(enter_label, suppressed_labels, video_rois, audio_rois, parent=self)
        if dlg.exec() == QDialog.Accepted:
            enter_label, suppressed_labels = dlg.get_result()
            self._signoff_enter_label[gid] = enter_label
            self._signoff_suppressed_labels[gid] = suppressed_labels
            self._update_signoff_roi_summary(gid)
            self._save_signoff_params()

    def _update_signoff_roi_summary(self, gid: int):
        """감지영역 선택 요약 텍스트를 갱신한다."""
        lbl = self._signoff_roi_summary.get(gid)
        if lbl is None:
            return
        enter_label = self._signoff_enter_label.get(gid, "")
        suppressed = self._signoff_suppressed_labels.get(gid, [])
        label_to_media = {r.label: r.media_name for r in self._roi_manager.video_rois}
        label_to_media.update({r.label: r.media_name for r in self._roi_manager.audio_rois})
        if enter_label:
            media = label_to_media.get(enter_label, "")
            trigger_text = f"{enter_label}  ({media})" if media else enter_label
            extra = len([s for s in suppressed if s != enter_label])
            if extra:
                lbl.setText(f"트리거: {trigger_text}  |  억제: +{extra}개")
            else:
                lbl.setText(f"트리거: {trigger_text}")
            lbl.setStyleSheet("")
        else:
            lbl.setText("선택 없음")
            lbl.setStyleSheet("color: red;")

    def _refresh_signoff_roi_tags(self):
        """ROI 목록 변경 시 정파설정 탭의 요약 라벨 갱신.
        (구버전 태그 버튼 방식에서 다이얼로그 방식으로 교체됨 — 요약만 갱신)
        """
        for gid in (1, 2):
            self._update_signoff_roi_summary(gid)

    def _get_signoff_params(self) -> dict:
        """현재 정파 설정 UI 값을 dict로 반환."""
        params = {
            "auto_preparation":    (self._signoff_auto_prep_btn is not None
                                    and self._signoff_auto_prep_btn.isChecked()),
            "prep_alarm_sound":    self._signoff_sound_edits["prep"].text(),
            "enter_alarm_sound":   self._signoff_sound_edits["enter"].text(),
            "release_alarm_sound": self._signoff_sound_edits["release"].text(),
        }
        for gid in (1, 2):
            stw = self._signoff_start_edit[gid]
            etw = self._signoff_end_edit[gid]
            prep_combo = self._signoff_prep_min_combo.get(gid)
            prep_minutes = prep_combo.currentData() if prep_combo is not None else 30
            exit_prep_combo = self._signoff_exit_prep_min_combo.get(gid)
            exit_prep_minutes = exit_prep_combo.currentData() if exit_prep_combo is not None else 0
            exit_trigger_combo = self._signoff_exit_trigger_combo.get(gid)
            exit_trigger_sec = exit_trigger_combo.currentData() if exit_trigger_combo is not None else 5
            params[f"group{gid}"] = {
                "name":              self._signoff_name_edit[gid].text() or f"Group{gid}",
                "enter_roi":         {"video_label": self._signoff_enter_label.get(gid, "")},
                "suppressed_labels": list(self._signoff_suppressed_labels.get(gid, [])),
                "start_time":        f"{stw.hour():02d}:{stw.minute():02d}",
                "end_time":          f"{etw.hour():02d}:{etw.minute():02d}",
                "prep_minutes":      prep_minutes,
                "exit_prep_minutes": exit_prep_minutes,
                "exit_trigger_sec":  exit_trigger_sec,
                "end_next_day":      self._signoff_end_next_day_chk[gid].isChecked(),
                "weekdays":          [
                    d for d, chk in enumerate(self._signoff_day_chks[gid])
                    if chk.isChecked()
                ],
            }
        return params

    def _save_signoff_params(self):
        """정파 설정 즉시 저장 + 시그널 발송."""
        params = self._get_signoff_params()
        self._config["signoff"] = params
        self.signoff_settings_changed.emit(params)

    def _apply_signoff_params_to_ui(self, cfg: dict):
        """config dict를 정파설정 UI에 반영 (시그널 없이 조용히)."""
        if not self._signoff_name_edit:
            return  # 위젯 미생성 (초기 호출 타이밍)

        def _block(w, v, setter):
            w.blockSignals(True)
            setter(v)
            w.blockSignals(False)

        auto_prep = bool(cfg.get("auto_preparation", True))
        if self._signoff_auto_prep_btn is not None:
            self._signoff_auto_prep_btn.blockSignals(True)
            self._signoff_auto_prep_btn.setChecked(auto_prep)
            self._signoff_auto_prep_btn.blockSignals(False)

        for key, edit in self._signoff_sound_edits.items():
            sound_key = f"{key}_alarm_sound"
            _block(edit, cfg.get(sound_key, ""), edit.setText)

        for gid in (1, 2):
            grp = cfg.get(f"group{gid}", {})
            _block(self._signoff_name_edit[gid],
                   grp.get("name", f"Group{gid}"),
                   self._signoff_name_edit[gid].setText)

            start_str = grp.get("start_time", "00:30")
            sh, sm = map(int, start_str.split(":"))
            self._signoff_start_edit[gid].setTime(sh, sm)

            end_str = grp.get("end_time", "06:00")
            eh, em = map(int, end_str.split(":"))
            self._signoff_end_edit[gid].setTime(eh, em)

            end_next_day = bool(grp.get("end_next_day", False))
            _block(self._signoff_end_next_day_chk[gid],
                   end_next_day,
                   self._signoff_end_next_day_chk[gid].setChecked)

            # prep_minutes 콤보박스 반영
            prep_combo = self._signoff_prep_min_combo.get(gid)
            if prep_combo is not None:
                raw_prep = int(grp.get("prep_minutes", 30))
                prep_combo.blockSignals(True)
                idx = prep_combo.findData(raw_prep)
                if idx >= 0:
                    prep_combo.setCurrentIndex(idx)
                else:
                    prep_combo.setCurrentIndex(1)  # 기본값: 30분 전
                prep_combo.blockSignals(False)

            # exit_prep_minutes 콤보박스 반영
            exit_prep_combo = self._signoff_exit_prep_min_combo.get(gid)
            if exit_prep_combo is not None:
                raw_exit_prep = int(grp.get("exit_prep_minutes", 0))
                exit_prep_combo.blockSignals(True)
                exit_idx = exit_prep_combo.findData(raw_exit_prep)
                if exit_idx >= 0:
                    exit_prep_combo.setCurrentIndex(exit_idx)
                else:
                    exit_prep_combo.setCurrentIndex(0)  # 기본값: 사용 안 함
                exit_prep_combo.blockSignals(False)

            # exit_trigger_sec 콤보박스 반영
            exit_trigger_combo = self._signoff_exit_trigger_combo.get(gid)
            if exit_trigger_combo is not None:
                raw_exit_trigger = int(grp.get("exit_trigger_sec", 5))
                exit_trigger_combo.blockSignals(True)
                et_idx = exit_trigger_combo.findData(raw_exit_trigger)
                if et_idx >= 0:
                    exit_trigger_combo.setCurrentIndex(et_idx)
                else:
                    exit_trigger_combo.setCurrentIndex(2)  # 기본값: 5초
                exit_trigger_combo.blockSignals(False)

            weekdays = set(grp.get("weekdays", [0, 1, 2, 3, 4, 5, 6]))
            every_day = (len(weekdays) == 7)
            _block(self._signoff_every_day_chk[gid],
                   every_day,
                   self._signoff_every_day_chk[gid].setChecked)
            for d, chk in enumerate(self._signoff_day_chks[gid]):
                _block(chk, d in weekdays, chk.setChecked)

            # enter_roi / suppressed_labels 로드 (구버전 roi_rules/roi_labels 자동 마이그레이션)
            enter_roi = grp.get("enter_roi", {})
            if not enter_roi:
                # 구버전 roi_rules → enter_roi 마이그레이션 (첫 번째 video_label 사용)
                old_rules = grp.get("roi_rules", [])
                if old_rules:
                    enter_roi = {"video_label": old_rules[0].get("video_label", "")}
            if not enter_roi:
                # 구버전 roi_labels → enter_roi 마이그레이션
                old_labels = grp.get("roi_labels", [])
                v_lbl = next((l for l in old_labels if l.startswith("V")), "")
                if v_lbl:
                    enter_roi = {"video_label": v_lbl}
            enter_label = enter_roi.get("video_label", "") if enter_roi else ""
            self._signoff_enter_label[gid] = enter_label

            suppressed_labels = list(grp.get("suppressed_labels", []))
            if not suppressed_labels and enter_label:
                suppressed_labels = [enter_label]  # 구버전 호환: 트리거 자동 포함
            self._signoff_suppressed_labels[gid] = suppressed_labels
            self._update_signoff_roi_summary(gid)

            # 힌트 레이블 갱신 (blockSignals로 인해 자동 갱신이 안 되므로 수동 호출)
            for fn in self._signoff_hint_fns.get(gid, ()):
                if fn:
                    fn()

    def _browse_signoff_sound(self, key: str):
        """정파 알림음 WAV 파일 선택."""
        init_dir = os.path.abspath(os.path.join("resources", "sounds"))
        os.makedirs(init_dir, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "정파 알림음 파일 선택",
            init_dir, "WAV 파일 (*.wav);;모든 파일 (*)"
        )
        if path:
            self._signoff_sound_edits[key].setText(
                self._to_relative_if_possible(path)
            )
            self._save_signoff_params()

    # ── 시스템 설정 (자동 재시작) ─────────────────────

    def _save_system_params(self):
        """자동 재시작 설정을 config에 저장하고 시그널 발송"""
        h = self._restart_time.hour()
        m = self._restart_time.minute()
        if self._chk_restart_2.isChecked():
            h2 = self._restart_time_2.hour()
            m2 = self._restart_time_2.minute()
            time2_str = f"{h2:02d}:{m2:02d}"
        else:
            time2_str = ""
        params = {
            "scheduled_restart_enabled": self._chk_restart_1.isChecked(),
            "scheduled_restart_time":    f"{h:02d}:{m:02d}",
            "scheduled_restart_time_2":  time2_str,
        }
        self._config["system"] = params
        self.system_settings_changed.emit(params)

    def _load_system_config(self, config: dict):
        """시스템 설정 UI 로드"""
        sys_cfg = config.get("system", {})

        self._chk_restart_1.blockSignals(True)
        self._chk_restart_1.setChecked(bool(sys_cfg.get("scheduled_restart_enabled", True)))
        self._chk_restart_1.blockSignals(False)

        time_str = sys_cfg.get("scheduled_restart_time", "03:00")
        try:
            parts = time_str.split(":")
            self._restart_time.setTime(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            self._restart_time.setTime(3, 0)

        time2_str = sys_cfg.get("scheduled_restart_time_2", "")
        self._chk_restart_2.blockSignals(True)
        if time2_str:
            self._chk_restart_2.setChecked(True)
            try:
                p2 = time2_str.split(":")
                self._restart_time_2.setTime(int(p2[0]), int(p2[1]))
            except (ValueError, IndexError):
                self._restart_time_2.setTime(15, 0)
        else:
            self._chk_restart_2.setChecked(False)
        self._chk_restart_2.blockSignals(False)

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
        _DEFAULT_SOUNDS = {
            "default": "resources/sounds/alarm.wav",
        }
        default_path = _DEFAULT_SOUNDS.get(alarm_type, "")
        self._alarm_file_edits[alarm_type].setText(default_path)
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
