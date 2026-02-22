"""
임베디드 오디오 모니터링 모듈
sounddevice를 사용하여 시스템 오디오(임베디드)를 캡처하고 L/R 레벨을 분석
"""
import numpy as np
import math
from PySide6.QtCore import QThread, Signal

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False


class AudioMonitorThread(QThread):
    """오디오 레벨 모니터링 스레드"""

    level_updated = Signal(float, float)   # L dB, R dB (-60 ~ 0)
    status_changed = Signal(str)
    silence_detected = Signal(float)       # 무음 지속 시간(초)

    CHUNK = 1024
    SAMPLE_RATE = 44100
    CHANNELS = 2
    SILENCE_THRESHOLD_DB = -50.0  # -50dB 이하를 무음으로 판단

    def __init__(self, device_index: int = None, parent=None):
        super().__init__(parent)
        self._running = False
        self._device_index = device_index
        self._silence_duration = 0.0
        self._muted = False
        self._stereo = (self.CHANNELS == 2)  # 초기화 시 한 번만 결정

    def set_muted(self, muted: bool):
        """음소거 설정 (레벨 모니터링은 계속, 표시만 변경)"""
        self._muted = muted

    def stop(self):
        self._running = False
        self.wait(3000)

    def _linear_to_db(self, linear: float) -> float:
        """선형 값을 dB로 변환"""
        if linear <= 0:
            return -60.0
        db = 20 * math.log10(max(linear, 1e-10))
        return max(-60.0, min(0.0, db))

    def run(self):
        """오디오 캡처 및 레벨 계산 루프"""
        if not SOUNDDEVICE_AVAILABLE:
            self.status_changed.emit("sounddevice 없음 - 오디오 모니터링 불가")
            self._running = True
            while self._running:
                self.level_updated.emit(-60.0, -60.0)
                self.msleep(500)
            return

        self._running = True

        try:
            # 오디오 스트림 열기
            stream = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.CHUNK,
                device=self._device_index,
                channels=self.CHANNELS,
                dtype='int16',
            )
            stream.start()
            self.status_changed.emit("오디오 스트림 시작")

            chunk_duration = self.CHUNK / self.SAMPLE_RATE  # 초 단위

            while self._running:
                try:
                    data, overflowed = stream.read(self.CHUNK)
                    samples = np.frombuffer(data, dtype=np.int16)

                    # 스테레오 분리 (초기화 시 결정된 플래그 사용)
                    if self._stereo:
                        left = samples[0::2].astype(np.float32) / 32768.0
                        right = samples[1::2].astype(np.float32) / 32768.0
                    else:
                        left = right = samples.astype(np.float32) / 32768.0

                    # RMS 레벨 계산
                    l_rms = float(np.sqrt(np.mean(left ** 2))) if len(left) > 0 else 0.0
                    r_rms = float(np.sqrt(np.mean(right ** 2))) if len(right) > 0 else 0.0

                    l_db = self._linear_to_db(l_rms)
                    r_db = self._linear_to_db(r_rms)

                    # 무음 감지
                    avg_db = (l_db + r_db) / 2.0
                    if avg_db <= self.SILENCE_THRESHOLD_DB:
                        self._silence_duration += chunk_duration
                        self.silence_detected.emit(self._silence_duration)
                    else:
                        self._silence_duration = 0.0

                    self.level_updated.emit(l_db, r_db)

                except Exception:
                    self.level_updated.emit(-60.0, -60.0)

            stream.stop()
            stream.close()

        except Exception as e:
            self.status_changed.emit(f"오디오 스트림 오류: {e}")
            while self._running:
                self.level_updated.emit(-60.0, -60.0)
                self.msleep(500)
