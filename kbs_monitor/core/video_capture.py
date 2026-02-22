"""
비디오 캡처 스레드 모듈
OpenCV를 사용하여 USB 캡처 카드에서 영상을 읽어 UI에 전달
"""
import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker


class VideoCaptureThread(QThread):
    """OpenCV 영상 캡처를 별도 스레드에서 실행하는 클래스"""

    frame_ready = Signal(object)   # numpy 배열 (BGR 프레임)
    status_changed = Signal(str)   # 상태 메시지
    connected = Signal()           # 연결 성공
    disconnected = Signal()        # 연결 끊김

    def __init__(self, port: int = 0, parent=None):
        super().__init__(parent)
        self._port = port
        self._video_file: str = ""   # MP4 파일 경로 (비어있으면 포트 사용)
        self._reconnect = False      # 소스 변경 시 강제 재연결 플래그
        self._running = False
        self._mutex = QMutex()
        self._cap = None
        self._target_fps = 30

    def set_port(self, port: int):
        """캡처 포트(카메라 인덱스) 변경"""
        with QMutexLocker(self._mutex):
            self._port = port
            self._video_file = ""
            self._reconnect = True

    def set_video_file(self, path: str):
        """영상 파일 소스 변경 (빈 문자열이면 포트 소스로 복귀)"""
        with QMutexLocker(self._mutex):
            self._video_file = path
            self._reconnect = True

    def stop(self):
        """스레드 정지"""
        self._running = False
        self.wait(3000)

    def run(self):
        """스레드 메인 루프: 프레임 읽기 및 신호 발송"""
        self._running = True
        cap = None
        was_connected = False
        consecutive_failures = 0
        max_failures = 30  # 30프레임 연속 실패 시 재연결 시도

        while self._running:
            with QMutexLocker(self._mutex):
                current_port = self._port
                current_file = self._video_file
                reconnect = self._reconnect
                if reconnect:
                    self._reconnect = False

            # 소스 변경 시 현재 캡처 강제 종료
            if reconnect and cap is not None:
                cap.release()
                cap = None
                was_connected = False
                consecutive_failures = 0

            # 연결이 없는 경우 새 소스 열기
            if cap is None:
                if current_file:
                    cap = cv2.VideoCapture(current_file)
                    source_name = f"파일: {current_file}"
                else:
                    cap = cv2.VideoCapture(current_port, cv2.CAP_DSHOW)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                    cap.set(cv2.CAP_PROP_FPS, self._target_fps)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    source_name = f"포트 {current_port}"

                if cap.isOpened():
                    was_connected = True
                    consecutive_failures = 0
                    self.connected.emit()
                    self.status_changed.emit(f"{source_name} 연결 성공")
                else:
                    if was_connected:
                        was_connected = False
                        self.disconnected.emit()
                        self.status_changed.emit(f"{source_name} 연결 실패")
                    cap.release()
                    cap = None
                    self.msleep(1000)
                    continue

            # 프레임 읽기
            ret, frame = cap.read()
            if ret and frame is not None:
                consecutive_failures = 0
                self.frame_ready.emit(frame)
            else:
                if current_file and cap is not None:
                    # 파일 끝 → 처음으로 되감기 (루프 재생)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        # 연결 끊김으로 판단
                        cap.release()
                        cap = None
                        if was_connected:
                            was_connected = False
                            self.disconnected.emit()
                            self.status_changed.emit(f"포트 {current_port} 신호 없음")

            # FPS 제어 (대략 30fps)
            self.msleep(33)

        # 정리
        if cap is not None:
            cap.release()
