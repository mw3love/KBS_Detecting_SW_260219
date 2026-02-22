"""
상단 바 위젯
시계, Embedded Audio(볼륨/미터), 감지현황, 시스템 성능, 각종 제어 버튼 포함
이모지 대신 텍스트/QStyle 아이콘 사용 (Windows 렌더링 호환성)
"""
import datetime
import subprocess

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import GPUtil
    GPUTIL_AVAILABLE = True
except ImportError:
    GPUTIL_AVAILABLE = False

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSlider, QFrame, QStyle,
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QFont, QColor, QPainter, QPixmap, QImage, QIcon


class LevelMeterBar(QWidget):
    """L 또는 R 오디오 레벨 미터 (세로 10칸 디지털 세그먼트)"""

    NUM_SEGMENTS = 10
    SEGMENT_GAP = 1

    def __init__(self, channel: str = "L", parent=None):
        super().__init__(parent)
        self._channel = channel
        self._level_db = -60.0  # -60 ~ 0 dB
        self.setFixedWidth(20)
        self.setMinimumHeight(44)

    def set_level(self, db: float):
        self._level_db = max(-60.0, min(0.0, db))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)

        w = self.width()
        h = self.height()
        label_h = 13

        bar_area_h = h - label_h
        n = self.NUM_SEGMENTS
        gap = self.SEGMENT_GAP
        seg_h = max(2, (bar_area_h - gap * (n - 1)) // n)

        # 전체 배경
        painter.fillRect(0, 0, w, h, QColor("#111122"))

        # 레벨 비율 (0~1)
        ratio = (self._level_db + 60.0) / 60.0
        lit_count = round(ratio * n)

        for i in range(n):
            # i=0: 맨 위 세그먼트 (고레벨), i=9: 맨 아래 (저레벨)
            y_top = label_h + i * (seg_h + gap)
            from_bottom = n - 1 - i  # 아래에서 몇 번째

            if from_bottom < lit_count:
                # 켜진 세그먼트: 위쪽일수록 밝은 빨간색
                if i <= 1:          # 상위 2칸: 가장 밝음
                    color = QColor("#ff2222")
                elif i <= 3:        # 3~4번째: 주황빛 빨간
                    color = QColor("#ee3322")
                else:               # 나머지: 기본 빨간
                    color = QColor("#bb1111")
                painter.fillRect(2, y_top, w - 4, seg_h, color)
            else:
                # 꺼진 세그먼트
                painter.fillRect(2, y_top, w - 4, seg_h, QColor("#222233"))

        # 채널 레이블 (상단)
        painter.setPen(QColor("#aaaacc"))
        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.drawText(0, 0, w, label_h, Qt.AlignCenter, self._channel)

        painter.end()


class SysMonitorWidget(QWidget):
    """CPU / RAM / GPU 실시간 성능 수치 표시 (psutil + GPUtil)"""

    # nvidia-smi 검색 경로
    _NVIDIASMI_PATHS = [
        "nvidia-smi",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        r"C:\Windows\System32\nvidia-smi.exe",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gpu_method = None   # 'gputil' | 'nvidiasmi' | None
        self._nvidiasmi_path = ""
        self._setup_ui()
        self._init_backends()
        self._start_timer()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)

        title = QLabel("시스템 성능")
        title.setObjectName("lblSysMonTitle")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        layout.addWidget(title)

        stats_widget = QWidget()
        hbox = QHBoxLayout(stats_widget)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(14)

        _small_style = "font-size: 10px; font-weight: bold;"

        self._lbl_cpu = QLabel("CPU\n--%")
        self._lbl_cpu.setAlignment(Qt.AlignCenter)
        self._lbl_cpu.setStyleSheet(_small_style)

        self._lbl_ram = QLabel("RAM\n--%")
        self._lbl_ram.setAlignment(Qt.AlignCenter)
        self._lbl_ram.setStyleSheet(_small_style)

        self._lbl_gpu = QLabel("GPU\nN/A")
        self._lbl_gpu.setAlignment(Qt.AlignCenter)
        self._lbl_gpu.setStyleSheet(_small_style)

        hbox.addWidget(self._lbl_cpu)
        hbox.addWidget(self._lbl_ram)
        hbox.addWidget(self._lbl_gpu)
        layout.addWidget(stats_widget)

    def _init_backends(self):
        """psutil CPU 기준값 수집 + GPU 감지"""
        if PSUTIL_AVAILABLE:
            psutil.cpu_percent(interval=None)  # 첫 호출 초기화

        # GPU: GPUtil → nvidia-smi 직접 경로 순서로 시도
        self._detect_gpu()

        # 500ms 후 첫 갱신 (CPU 의미 있는 수치 확보)
        QTimer.singleShot(500, self._update_stats)

    def _detect_gpu(self):
        """GPU 감지: GPUtil 우선, 실패 시 nvidia-smi 직접 경로 시도"""
        if GPUTIL_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    self._gpu_method = "gputil"
                    return
            except Exception:
                pass

        # nvidia-smi 직접 호출 시도
        for path in self._NVIDIASMI_PATHS:
            try:
                result = subprocess.run(
                    [path, "--query-gpu=utilization.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode == 0 and result.stdout.strip():
                    self._gpu_method = "nvidiasmi"
                    self._nvidiasmi_path = path
                    return
            except Exception:
                continue

        self._gpu_method = None  # GPU 없음 또는 감지 불가

    def _start_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_stats)
        self._timer.start(2000)

    # ── 주기적 갱신 ──────────────────────────────────

    def _update_stats(self):
        # CPU / RAM (psutil)
        if PSUTIL_AVAILABLE:
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            self._lbl_cpu.setText(f"CPU\n{cpu:.0f}%")
            self._lbl_ram.setText(f"RAM\n{ram:.0f}%")
        else:
            self._lbl_cpu.setText("CPU\nN/A")
            self._lbl_ram.setText("RAM\nN/A")

        # GPU
        if self._gpu_method == "gputil" and GPUTIL_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    self._lbl_gpu.setText(f"GPU\n{gpus[0].load * 100:.0f}%")
            except Exception:
                pass
        elif self._gpu_method == "nvidiasmi":
            try:
                result = subprocess.run(
                    [self._nvidiasmi_path, "--query-gpu=utilization.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    self._lbl_gpu.setText(f"GPU\n{result.stdout.strip()}%")
            except Exception:
                pass


class TopBar(QWidget):
    """상단 제어 바"""

    settings_requested = Signal()
    roi_visibility_changed = Signal(bool)
    detection_toggled = Signal(bool)   # True=ON, False=OFF
    sound_toggled = Signal(bool)
    volume_changed = Signal(int)
    clear_alarm_requested = Signal()
    dark_mode_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roi_visible = True
        self._sound_enabled = True
        self._dark_mode = True
        self._premute_volume = 80   # 임베디드 오디오 음소거 전 볼륨 저장
        self._setup_ui()
        self._start_clock()

    def _setup_ui(self):
        self.setObjectName("topBar")
        self.setFixedHeight(68)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 4)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        # 1. 시스템 성능 수치 (CPU / RAM / GPU)
        self._sys_monitor = SysMonitorWidget()
        layout.addWidget(self._sys_monitor, alignment=Qt.AlignTop)

        layout.addWidget(self._make_separator())

        # 2. 현재 시각 (소제목 + 시간값)
        time_container = QWidget()
        time_vbox = QVBoxLayout(time_container)
        time_vbox.setContentsMargins(4, 0, 4, 0)
        time_vbox.setSpacing(15)

        lbl_time_title = QLabel("현재시간")
        lbl_time_title.setAlignment(Qt.AlignCenter)
        lbl_time_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        time_vbox.addWidget(lbl_time_title)

        self._lbl_time = QLabel("00:00:00")
        self._lbl_time.setObjectName("lblTime")
        self._lbl_time.setAlignment(Qt.AlignCenter)
        self._lbl_time.setFont(QFont("Segoe UI", 13, QFont.Bold))
        time_vbox.addWidget(self._lbl_time)

        layout.addWidget(time_container, alignment=Qt.AlignTop)

        layout.addWidget(self._make_separator())

        # 3. Embedded Audio (소제목 + 음소거버튼 + 볼륨슬라이더 + L/R 레벨미터)
        embed_container = QWidget()
        embed_container.setMaximumWidth(175)   # 제목에 비례하여 폭 제한
        embed_vbox = QVBoxLayout(embed_container)
        embed_vbox.setContentsMargins(4, 0, 4, 0)
        embed_vbox.setSpacing(2)

        lbl_embed_title = QLabel("Embedded Audio")
        lbl_embed_title.setAlignment(Qt.AlignCenter)
        lbl_embed_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        embed_vbox.addWidget(lbl_embed_title)

        embed_content = QWidget()
        embed_hbox = QHBoxLayout(embed_content)
        embed_hbox.setContentsMargins(0, 0, 0, 0)
        embed_hbox.setSpacing(4)

        # 임베디드 오디오 음소거 버튼 (볼륨 슬라이더 왼쪽)
        self._btn_embed_mute = QPushButton()
        self._btn_embed_mute.setObjectName("btnEmbedMute")
        self._btn_embed_mute.setCheckable(True)
        self._btn_embed_mute.setFixedSize(28, 24)
        self._btn_embed_mute.setIcon(self._make_volume_icon(False))
        self._btn_embed_mute.setIconSize(QSize(18, 18))
        self._btn_embed_mute.setToolTip("임베디드 오디오 음소거")
        self._btn_embed_mute.clicked.connect(self._on_embed_mute_clicked)
        embed_hbox.addWidget(self._btn_embed_mute, 0, Qt.AlignVCenter)

        self._slider_volume = QSlider(Qt.Horizontal)
        self._slider_volume.setObjectName("sliderVolume")
        self._slider_volume.setRange(0, 100)
        self._slider_volume.setValue(80)
        self._slider_volume.setFixedWidth(60)
        self._slider_volume.setToolTip("임베디드 오디오 볼륨")
        self._slider_volume.valueChanged.connect(self.volume_changed)
        embed_hbox.addWidget(self._slider_volume, 0, Qt.AlignVCenter)

        self._meter_l = LevelMeterBar("L")
        self._meter_r = LevelMeterBar("R")
        embed_hbox.addWidget(self._meter_l)
        embed_hbox.addWidget(self._meter_r)

        embed_vbox.addWidget(embed_content)
        layout.addWidget(embed_container, alignment=Qt.AlignTop)

        layout.addWidget(self._make_separator())

        # 4. 감지현황 표시 (세로 레이아웃)
        layout.addWidget(self._create_summary_widget(), alignment=Qt.AlignTop)

        layout.addWidget(self._make_separator())

        # 5. 감지 On/Off
        self._btn_detection = QPushButton("감지 ON")
        self._btn_detection.setObjectName("btnDetection")
        self._btn_detection.setCheckable(True)
        self._btn_detection.setChecked(True)
        self._btn_detection.setFixedSize(90, 36)
        self._btn_detection.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._btn_detection.clicked.connect(self._on_detection_clicked)
        layout.addWidget(self._btn_detection)

        # 5b. 감지영역 보이기/숨기기
        self._btn_roi = QPushButton("감지영역")
        self._btn_roi.setObjectName("btnRoi")
        self._btn_roi.setCheckable(True)
        self._btn_roi.setChecked(True)
        self._btn_roi.setFixedSize(90, 36)
        self._btn_roi.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._btn_roi.clicked.connect(self._on_roi_clicked)
        layout.addWidget(self._btn_roi)

        # 5c. Mute 버튼 (프로그램 알림음 음소거)
        self._btn_mute = QPushButton("Mute")
        self._btn_mute.setObjectName("btnMuteText")
        self._btn_mute.setCheckable(True)
        self._btn_mute.setFixedSize(70, 36)
        self._btn_mute.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._btn_mute.setToolTip("알림음 음소거")
        self._btn_mute.clicked.connect(self._on_mute_clicked)
        layout.addWidget(self._btn_mute)

        layout.addWidget(self._make_separator())

        # 6. 다크/라이트 모드 토글
        self._btn_dark = QPushButton("☽  야간 모드")
        self._btn_dark.setObjectName("btnDark")
        self._btn_dark.setCheckable(True)
        self._btn_dark.setChecked(True)
        self._btn_dark.setFixedSize(110, 36)
        self._btn_dark.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._btn_dark.clicked.connect(self._on_dark_mode_clicked)
        layout.addWidget(self._btn_dark)

        # 7. 설정
        self._btn_settings = QPushButton("설정  ⚙")
        self._btn_settings.setObjectName("btnSettings")
        self._btn_settings.setFixedSize(86, 36)
        self._btn_settings.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._btn_settings.clicked.connect(self.settings_requested)
        layout.addWidget(self._btn_settings)

        layout.addStretch()

    def _create_summary_widget(self) -> QWidget:
        """감지현황 표시 위젯 (세로 레이아웃)"""
        container = QWidget()
        container.setObjectName("summaryContainer")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(6, 0, 6, 0)
        vbox.setSpacing(6)

        # 상단 제목
        title = QLabel("감지 현황")
        title.setObjectName("lblSummaryTitle")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        vbox.addWidget(title)

        # 하단 항목들 (가로)
        items_widget = QWidget()
        hbox = QHBoxLayout(items_widget)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(10)

        item_font = QFont("Segoe UI", 10, QFont.Bold)

        self._lbl_v = QLabel("V\n0")
        self._lbl_v.setObjectName("lblSummaryItem")
        self._lbl_v.setAlignment(Qt.AlignCenter)
        self._lbl_v.setFont(item_font)

        self._lbl_a = QLabel("A\n0")
        self._lbl_a.setObjectName("lblSummaryItem")
        self._lbl_a.setAlignment(Qt.AlignCenter)
        self._lbl_a.setFont(item_font)

        self._lbl_ea = QLabel("EA\n-")
        self._lbl_ea.setObjectName("lblSummaryItem")
        self._lbl_ea.setAlignment(Qt.AlignCenter)
        self._lbl_ea.setFont(item_font)

        hbox.addWidget(self._lbl_v)
        hbox.addWidget(self._lbl_a)
        hbox.addWidget(self._lbl_ea)
        vbox.addWidget(items_widget)

        return container

    def _make_separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setObjectName("topBarSeparator")
        line.setFixedHeight(44)
        return line

    def _start_clock(self):
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_time)
        self._clock_timer.start(1000)
        self._update_time()

    def _update_time(self):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self._lbl_time.setText(now)

    def _make_volume_icon(self, muted: bool) -> QIcon:
        """볼륨/음소거 아이콘 반환 (야간모드에서 픽셀 반전으로 밝게 처리)"""
        icon_type = QStyle.SP_MediaVolumeMuted if muted else QStyle.SP_MediaVolume
        px = self.style().standardIcon(icon_type).pixmap(QSize(16, 16))
        if self._dark_mode:
            img = px.toImage().convertedTo(QImage.Format_ARGB32)
            img.invertPixels(QImage.InvertRgb)
            px = QPixmap.fromImage(img)
        return QIcon(px)

    def _on_embed_mute_clicked(self, checked: bool):
        """임베디드 오디오 음소거 토글
        checked=True: 음소거 ON → 볼륨 0 발송, 음소거 아이콘
        checked=False: 음소거 OFF → 이전 볼륨 복원, 볼륨 아이콘
        """
        if checked:
            self._premute_volume = self._slider_volume.value()
            self.volume_changed.emit(0)
            self._btn_embed_mute.setIcon(self._make_volume_icon(True))
        else:
            self.volume_changed.emit(self._premute_volume)
            self._btn_embed_mute.setIcon(self._make_volume_icon(False))

    def _on_mute_clicked(self, checked: bool):
        """checked=True: 알림음 OFF, checked=False: 알림음 ON"""
        self.sound_toggled.emit(not checked)

    def _on_detection_clicked(self, checked: bool):
        """checked=True: 감지 ON, checked=False: 감지 OFF"""
        self._btn_detection.setText("감지 ON" if checked else "감지 OFF")
        self.detection_toggled.emit(checked)

    def _on_roi_clicked(self, checked: bool):
        """
        checked=True: 감지영역 보임
        checked=False: 감지영역 숨김
        """
        self._roi_visible = checked
        self.roi_visibility_changed.emit(checked)

    def _on_dark_mode_clicked(self, checked: bool):
        self._dark_mode = checked
        self._btn_dark.setText("☽  야간 모드" if checked else "☀  주간 모드")
        self._btn_embed_mute.setIcon(self._make_volume_icon(self._btn_embed_mute.isChecked()))
        self.dark_mode_toggled.emit(checked)

    # --- 외부에서 호출하는 메서드 ---

    def update_audio_levels(self, l_db: float, r_db: float):
        """오디오 레벨미터 업데이트"""
        self._meter_l.set_level(l_db)
        self._meter_r.set_level(r_db)

    def update_summary(self, video_count: int, audio_count: int, embedded_active: bool):
        """감지현황 업데이트"""
        self._lbl_v.setText(f"V\n{video_count}")
        self._lbl_a.setText(f"A\n{audio_count}")
        ea_val = "1" if embedded_active else "-"
        self._lbl_ea.setText(f"EA\n{ea_val}")

    def set_volume_display(self, value: int):
        """볼륨 슬라이더 값을 외부에서 설정 (시그널 발송 없이)"""
        self._slider_volume.blockSignals(True)
        self._slider_volume.setValue(max(0, min(100, value)))
        self._slider_volume.blockSignals(False)
