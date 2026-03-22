"""
임베디드 오디오 모니터링 모듈
sounddevice를 사용하여 시스템 오디오(임베디드)를 캡처하고 L/R 레벨을 분석
"""
import logging
import time
import numpy as np
import math
from PySide6.QtCore import QThread, Signal

_log = logging.getLogger(__name__)

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
    audio_chunk = Signal(object)           # (np.ndarray int16, timestamp float) — 녹화용 raw 샘플

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
        self._volume = 1.0              # 패스스루 출력 볼륨 (0.0 ~ 1.0)
        self._stereo = (self.CHANNELS == 2)  # 초기화 시 한 번만 결정

    def set_muted(self, muted: bool):
        """음소거 설정 (패스스루 출력 차단)"""
        self._muted = muted

    def set_volume(self, volume: float):
        """패스스루 출력 볼륨 설정 (0.0 ~ 1.0)"""
        self._volume = max(0.0, min(1.0, volume))

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
        stream = None
        output_stream = None

        try:
            # 입력 스트림 열기
            stream = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.CHUNK,
                device=self._device_index,
                channels=self.CHANNELS,
                dtype='int16',
            )
            stream.start()

            # 출력 스트림 열기 (패스스루용, 기본 출력 장치)
            try:
                output_stream = sd.RawOutputStream(
                    samplerate=self.SAMPLE_RATE,
                    blocksize=self.CHUNK,
                    channels=self.CHANNELS,
                    dtype='int16',
                )
                output_stream.start()
                self.status_changed.emit("오디오 스트림 시작 (패스스루 활성)")
            except Exception as e:
                output_stream = None
                self.status_changed.emit(f"오디오 스트림 시작 (출력 오류: {e})")

            chunk_duration = self.CHUNK / self.SAMPLE_RATE  # 초 단위
            consecutive_errors = 0       # 연속 실패 카운터 (장치 제거 감지)
            _MAX_CONSECUTIVE_ERRORS = 10  # 이 횟수 연속 실패 시 스트림 재연결 시도

            while self._running:
                try:
                    data, overflowed = stream.read(self.CHUNK)
                    consecutive_errors = 0  # 성공 시 리셋
                    samples = np.frombuffer(data, dtype=np.int16)

                    # 녹화용 raw 샘플 emit (복사본, 타임스탬프 포함)
                    self.audio_chunk.emit((samples.copy(), time.time()))

                    # 패스스루: 캡처 오디오를 출력 장치로 전송
                    if output_stream is not None and not self._muted and self._volume > 0:
                        if self._volume < 1.0:
                            out_f = samples.astype(np.float32) * self._volume
                            out_samples = np.clip(out_f, -32768, 32767).astype(np.int16)
                        else:
                            out_samples = samples
                        try:
                            output_stream.write(out_samples.tobytes())
                        except Exception:
                            pass

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

                except Exception as e:
                    consecutive_errors += 1
                    _log.debug("오디오 루프 예외 (%d회): %s", consecutive_errors, e)
                    self.level_updated.emit(-60.0, -60.0)

                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        # 스트림 무효 (장치 제거 등) → 정리 후 재연결 시도
                        _log.warning("오디오 스트림 연속 실패 %d회 — 재연결 시도", consecutive_errors)
                        self.status_changed.emit("오디오 장치 오류 — 재연결 시도 중...")
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            pass
                        if output_stream is not None:
                            try:
                                output_stream.stop()
                                output_stream.close()
                            except Exception:
                                pass
                            output_stream = None

                        # 재연결 대기 후 시도
                        self.msleep(3000)
                        if not self._running:
                            return
                        try:
                            stream = sd.RawInputStream(
                                samplerate=self.SAMPLE_RATE,
                                blocksize=self.CHUNK,
                                device=self._device_index,
                                channels=self.CHANNELS,
                                dtype='int16',
                            )
                            stream.start()
                            try:
                                output_stream = sd.RawOutputStream(
                                    samplerate=self.SAMPLE_RATE,
                                    blocksize=self.CHUNK,
                                    channels=self.CHANNELS,
                                    dtype='int16',
                                )
                                output_stream.start()
                            except Exception:
                                output_stream = None
                            consecutive_errors = 0
                            self._silence_duration = 0.0
                            self.status_changed.emit("오디오 스트림 재연결 성공")
                            _log.info("오디오 스트림 재연결 성공")
                        except Exception as re_e:
                            self.status_changed.emit(f"오디오 재연결 실패: {re_e}")
                            _log.warning("오디오 스트림 재연결 실패: %s — 5초 후 재시도", re_e)
                            self.msleep(5000)
                            if not self._running:
                                return
                            consecutive_errors = 0  # 리셋하여 다음 10회 후 재시도

        except Exception as e:
            self.status_changed.emit(f"오디오 스트림 오류: {e}")
            while self._running:
                self.level_updated.emit(-60.0, -60.0)
                self.msleep(500)
        finally:
            # 예외 발생 여부와 무관하게 스트림 반드시 정리
            if output_stream is not None:
                try:
                    output_stream.stop()
                    output_stream.close()
                except Exception:
                    pass
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
