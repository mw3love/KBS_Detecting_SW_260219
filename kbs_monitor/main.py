"""
KBS 16채널 비디오 모니터링 시스템 v2
메인 엔트리포인트
"""
import sys
import os
import faulthandler
import atexit

# Windows 콘솔 창 숨기기
if sys.platform == "win32":
    import ctypes
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

# 스크립트 위치를 작업 디렉토리로 설정 (상대 경로 참조를 위해)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(_BASE_DIR)

# C++ segfault 발생 시 스택트레이스 기록 (Python try-except로 잡히지 않는 크래시 추적용)
os.makedirs(os.path.join(_BASE_DIR, "logs"), exist_ok=True)
_fault_log = open(os.path.join(_BASE_DIR, "logs", "fault.log"), "a", encoding="utf-8")
faulthandler.enable(file=_fault_log)
atexit.register(_fault_log.close)

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("KBS Peacock v1.6.19")
    app.setOrganizationName("KBS")

    # 다크 테마 QSS 로드
    qss_path = os.path.join("resources", "styles", "dark_theme.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
