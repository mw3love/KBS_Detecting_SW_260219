"""
로깅 모듈
Python logging을 래핑하여 UI 로그 위젯에도 신호 발송
일별 로그 파일: logs/YYYYMMDD.txt (날짜 변경 시 자동 로테이션)
"""
import logging
import os
import datetime
from PySide6.QtCore import QObject, Signal


class AppLogger(QObject):
    """애플리케이션 로거 - 파일 + UI 로그 위젯 동시 출력"""

    # log_type: "info" | "error" | "audio" | "embedded"
    log_signal = Signal(str, str)

    LOG_DIR = "logs"

    def __init__(self, parent=None):
        super().__init__(parent)
        os.makedirs(self.LOG_DIR, exist_ok=True)
        self._current_date: str = ""
        self._file_logger = logging.getLogger("kbs_monitor")
        self._file_logger.setLevel(logging.DEBUG)
        # propagate=False 로 루트 로거 중복 출력 방지
        self._file_logger.propagate = False
        self._rotate_if_needed()

    def _rotate_if_needed(self):
        """날짜가 바뀌면 새 로그 파일로 교체"""
        today = datetime.date.today().strftime("%Y%m%d")
        if today == self._current_date:
            return

        # 기존 핸들러 제거 (close 먼저 → 파일 잠금 해제 후 removeHandler)
        for h in list(self._file_logger.handlers):
            h.close()
            self._file_logger.removeHandler(h)

        # 새 핸들러 추가 (일별 파일)
        log_path = os.path.join(self.LOG_DIR, f"{today}.txt")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        self._file_logger.addHandler(handler)
        self._current_date = today

    def info(self, message: str):
        self._rotate_if_needed()
        self._file_logger.info(message)
        self.log_signal.emit(message, "info")

    def warning(self, message: str):
        self._rotate_if_needed()
        self._file_logger.warning(message)
        self.log_signal.emit(f"경고: {message}", "info")

    def error(self, message: str):
        """블랙 관련 에러 로그 (빨간색)"""
        self._rotate_if_needed()
        self._file_logger.error(message)
        self.log_signal.emit(message, "error")

    def still_error(self, message: str):
        """스틸 관련 에러 로그 (보라색)"""
        self._rotate_if_needed()
        self._file_logger.error(message)
        self.log_signal.emit(message, "still")

    def file_only(self, message: str):
        """파일에만 기록하고 UI에는 표시하지 않음 (텔레그램 전송 로그 등)"""
        self._rotate_if_needed()
        self._file_logger.info(message)

    def audio_error(self, message: str):
        """오디오 레벨미터 관련 에러 로그 (초록색)"""
        self._rotate_if_needed()
        self._file_logger.error(message)
        self.log_signal.emit(message, "audio")

    def embedded_error(self, message: str):
        """임베디드 오디오 관련 에러 로그 (파란색)"""
        self._rotate_if_needed()
        self._file_logger.error(message)
        self.log_signal.emit(message, "embedded")

    def debug(self, message: str):
        self._rotate_if_needed()
        self._file_logger.debug(message)
