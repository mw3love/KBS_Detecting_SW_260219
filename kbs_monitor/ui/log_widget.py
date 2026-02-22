"""
시스템 로그 위젯
시간+메시지를 표시하며, 로그 타입에 따라 색상 구별:
  - 블랙 에러: 빨간 배경
  - 스틸 에러: 보라색 배경
  - 오디오 레벨미터 에러: 초록 배경
  - 임베디드 오디오 에러: 파란 배경
날짜 변경 시 구분선 자동 삽입, Log 초기화 버튼 포함
"""
import datetime
import os
import subprocess
import sys
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QStyle,
    QStyledItemDelegate,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont


class _LogItemDelegate(QStyledItemDelegate):
    """
    로그 타입에 따라 배경색+흰 텍스트를 QSS 테마와 무관하게 렌더링.
    일반 항목은 기본 델리게이트(QSS 적용)에 위임.
    """
    LOG_TYPE_ROLE = Qt.UserRole + 1

    # log_type → (배경색, 글자색)
    _COLORS = {
        "error":    ("#cc0000", "#ffffff"),   # 블랙: 빨간 배경
        "still":    ("#7B2FBE", "#ffffff"),   # 스틸: 보라색 배경 (주간/야간 모두 가시)
        "audio":    ("#006600", "#ffffff"),   # 오디오 레벨미터: 초록 배경
        "embedded": ("#004488", "#ffffff"),   # 임베디드 오디오: 파란 배경
    }

    def paint(self, painter, option, index):
        log_type = index.data(self.LOG_TYPE_ROLE)
        colors = self._COLORS.get(log_type)
        if colors:
            bg_color, fg_color = colors
            painter.save()
            painter.fillRect(option.rect, QColor(bg_color))
            painter.setFont(option.font)
            painter.setPen(QColor(fg_color))
            text_rect = option.rect.adjusted(6, 0, -6, 0)
            painter.drawText(
                text_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                index.data(Qt.DisplayRole) or "",
            )
            painter.restore()
        else:
            super().paint(painter, option, index)


class LogWidget(QWidget):
    """시스템 로그를 표시하는 위젯"""

    MAX_LOG_ITEMS = 500
    LOG_DIR = "logs"

    log_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_date: str = ""  # 마지막 로그 날짜 (YYYY-MM-DD)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 헤더 영역 (SYSTEM LOG + Log 폴더 버튼 + Log 초기화 버튼)
        header_widget = QWidget()
        header_widget.setObjectName("logHeaderArea")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(8, 4, 8, 4)

        self._header = QLabel("SYSTEM LOG")
        self._header.setObjectName("logHeader")
        self._header.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        header_layout.addWidget(self._header)

        header_layout.addStretch()

        # Log 폴더 버튼
        self._btn_open_folder = QPushButton()
        self._btn_open_folder.setObjectName("btnLogFolder")
        self._btn_open_folder.setIcon(
            self.style().standardIcon(QStyle.SP_DirOpenIcon)
        )
        self._btn_open_folder.setFixedSize(32, 26)
        self._btn_open_folder.setToolTip("Log 폴더 열기")
        self._btn_open_folder.clicked.connect(self._open_log_folder)
        header_layout.addWidget(self._btn_open_folder)

        # Log 초기화 버튼
        self._btn_clear = QPushButton("Log 초기화")
        self._btn_clear.setObjectName("btnLogClear")
        self._btn_clear.setFixedSize(80, 26)
        self._btn_clear.setToolTip("화면 로그 초기화 (파일 변경 없음)")
        self._btn_clear.clicked.connect(self.clear_logs)
        header_layout.addWidget(self._btn_clear)

        layout.addWidget(header_widget)

        # 로그 리스트
        self._list = QListWidget()
        self._list.setObjectName("logList")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list.setSelectionMode(QListWidget.NoSelection)
        self._list.setFocusPolicy(Qt.NoFocus)
        # QSS 테마에 무관하게 에러 항목 색상을 렌더링하는 커스텀 델리게이트
        self._list.setItemDelegate(_LogItemDelegate(self._list))
        layout.addWidget(self._list)

    def add_log(self, message: str, log_type: str = "info"):
        """로그 항목 추가
        log_type: "info" | "error" (빨간색) | "still" (보라색) | "audio" (초록색) | "embedded" (파란색)
        """
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        # 날짜 변경 시 구분선 추가 (첫 로그 포함)
        if date_str != self._last_date:
            self._add_date_separator(date_str)
            self._last_date = date_str

        text = f"{time_str}  {message}"
        item = QListWidgetItem(text)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        if log_type in ("error", "still", "audio", "embedded"):
            item.setData(_LogItemDelegate.LOG_TYPE_ROLE, log_type)

        self._list.addItem(item)

        # 최대 항목 수 초과 시 오래된 항목 제거
        while self._list.count() > self.MAX_LOG_ITEMS:
            self._list.takeItem(0)

        self._list.scrollToBottom()

    def _add_date_separator(self, date_str: str):
        """날짜 구분선 항목 추가"""
        item = QListWidgetItem(f"──── {date_str} ────")
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor("#6060a0"))
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        self._list.addItem(item)

    def add_error(self, message: str):
        self.add_log(message, log_type="error")

    def add_info(self, message: str):
        self.add_log(message, log_type="info")

    def clear_logs(self):
        """화면 로그 초기화 (파일 변경 없음)"""
        self._list.clear()
        self._last_date = ""
        self.log_cleared.emit()

    def _open_log_folder(self):
        """로그 폴더를 파일 탐색기로 열기"""
        log_path = os.path.abspath(self.LOG_DIR)
        os.makedirs(log_path, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(log_path)
            else:
                subprocess.Popen(["xdg-open", log_path])
        except Exception:
            pass
