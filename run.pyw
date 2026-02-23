"""
KBS 16채널 비디오 모니터링 시스템 v2
콘솔 창 없이 실행하기 위한 진입점 (.pyw = pythonw.exe로 실행됨)
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kbs_monitor"))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kbs_monitor"))

from ui.main_window import MainWindow
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setApplicationName("KBS Peacock v1.03")
app.setOrganizationName("KBS")

qss_path = os.path.join("resources", "styles", "dark_theme.qss")
if os.path.exists(qss_path):
    with open(qss_path, "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

window = MainWindow()
window.show()
sys.exit(app.exec())
