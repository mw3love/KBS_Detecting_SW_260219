"""
알림 시스템 모듈
시각적 알림(빨간 깜박임)과 소리 알림(WAV 반복 재생)을 담당
기본 알림음: Windows 내장 SystemHand (경고음) / 사용자 지정 WAV 파일 우선 사용
모든 감지 타입(블랙/스틸/오디오레벨미터/임베디드)에 동일한 통합 알림음 사용
"""
import os
import time
import wave
import threading
import numpy as np
from PySide6.QtCore import QObject, Signal, QTimer

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False

try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False


class AlarmSystem(QObject):
    """알림 시스템: 소리 반복 재생 및 시각적 알림 신호 발송"""

    visual_blink = Signal(bool)    # True=빨간 깜박임 ON, False=OFF
    alarm_triggered = Signal(str)  # 알림 발생 (알림 타입)

    # 통합 알림음 — 모든 감지 타입 공통 사용
    # Windows 내장 경고음: SystemHand (Critical Stop — 가장 강렬한 경고음)
    DEFAULT_WINDOWS_SOUND = "SystemHand"

    def __init__(self, sounds_dir: str = "resources/sounds", parent=None):
        super().__init__(parent)
        self._sounds_dir = sounds_dir
        self._sound_files: dict = {}   # {"default": 절대경로}
        self._sound_enabled = True
        self._volume = 0.8
        self._blink_timer = QTimer(self)
        self._blink_state = False
        self._active_alarms: set = set()
        self._sound_thread: threading.Thread = None
        self._stop_sound = threading.Event()  # 사운드 정지 신호

        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_timer.setInterval(500)  # 0.5초 간격 깜박임

    def trigger(self, alarm_type: str, label: str, alarm_duration: float = 0.0):
        """알림 발생. alarm_duration > 0이면 해당 초 후 소리 자동 중지."""
        key = f"{alarm_type}_{label}"
        if key not in self._active_alarms:
            self._active_alarms.add(key)
            self.alarm_triggered.emit(f"{label} {alarm_type} 감지")
            self._play_sound(alarm_type, alarm_duration)

        if not self._blink_timer.isActive():
            self._blink_timer.start()

    def resolve(self, alarm_type: str, label: str):
        """알림 해제"""
        key = f"{alarm_type}_{label}"
        self._active_alarms.discard(key)

        if not self._active_alarms:
            self._stop_sound.set()   # 모든 알림 해제 시 사운드 정지
            self._blink_timer.stop()
            self._blink_state = False
            self.visual_blink.emit(False)

    def resolve_all(self):
        """모든 알림 해제"""
        self._active_alarms.clear()
        self._stop_sound.set()       # 재생 중인 사운드 즉시 정지
        self._blink_timer.stop()
        self._blink_state = False
        self.visual_blink.emit(False)

    def set_sound_enabled(self, enabled: bool):
        self._sound_enabled = enabled
        if not enabled:
            self._stop_sound.set()   # 음소거 신호
            # 즉시 정지: sounddevice / winsound
            if SOUNDDEVICE_AVAILABLE:
                try:
                    sd.stop()
                except Exception:
                    pass
            elif WINSOUND_AVAILABLE:
                try:
                    winsound.PlaySound(None, winsound.SND_ASYNC)
                except Exception:
                    pass
        else:
            # 음소거 해제 시: 활성 알람이 있으면 소리 재개
            if self._active_alarms:
                self._play_sound("default")

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, volume))

    def set_sounds_dir(self, path: str):
        self._sounds_dir = path

    def set_sound_file(self, alarm_type: str, path: str):
        """알림음 파일 경로 설정 (키 그대로 저장, _get_sound_path에서 통합 처리)"""
        self._sound_files[alarm_type] = path

    def get_sound_files(self) -> dict:
        """현재 설정된 알림음 파일 경로 반환"""
        return dict(self._sound_files)

    def play_test_sound(self, alarm_type: str):
        """테스트용 알림음 재생 (3초 후 자동 종료)"""
        # 기존 재생 중지 후 테스트 시작
        self._stop_sound.set()
        if self._sound_thread and self._sound_thread.is_alive():
            self._sound_thread.join(timeout=0.5)
        self._stop_sound.clear()
        t = threading.Thread(
            target=self._play_sound_worker,
            args=("default", 3.0),
            daemon=True,
        )
        t.start()

    def _toggle_blink(self):
        self._blink_state = not self._blink_state
        self.visual_blink.emit(self._blink_state)

    def _play_sound(self, alarm_type: str, alarm_duration: float = 0.0):
        """사운드 반복 재생 시작 (별도 스레드). 이미 재생 중이면 건너뜀."""
        if not self._sound_enabled:
            return

        # 이미 재생 중이면 새 스레드 불필요 (루프가 알아서 반복)
        if self._sound_thread and self._sound_thread.is_alive():
            return

        self._stop_sound.clear()   # 정지 신호 초기화
        self._sound_thread = threading.Thread(
            target=self._play_sound_worker,
            args=("default", alarm_duration),
            daemon=True,
        )
        self._sound_thread.start()

    def _get_sound_path(self) -> str | None:
        """통합 알림음 파일 경로 반환 (없으면 None → Windows 내장음)
        "default" 키 우선, 레거시 포맷 호환을 위해 다른 키도 fallback 확인.
        """
        path = self._sound_files.get("default", "")
        if path and os.path.exists(path):
            return path
        # 레거시: 구형 설정(black/still/audio 개별 키)에 유효한 파일이 있으면 사용
        for p in self._sound_files.values():
            if p and os.path.exists(p):
                return p
        return None

    def _play_windows_builtin(self):
        """Windows 내장 경고음(SystemHand) 비동기 재생 (즉시 중지 가능)"""
        if not WINSOUND_AVAILABLE:
            return
        try:
            winsound.PlaySound(
                self.DEFAULT_WINDOWS_SOUND,
                winsound.SND_ALIAS | winsound.SND_ASYNC,
            )
        except Exception:
            try:
                winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                pass

    def _play_sound_worker(self, alarm_type: str, alarm_duration: float = 0.0):
        """사운드 반복 재생 (stop_sound 이벤트가 set되거나 alarm_duration 초과 시 종료)"""
        sound_file = self._get_sound_path()
        start_time = time.time()

        if sound_file and SOUNDDEVICE_AVAILABLE:
            # sounddevice로 WAV 파일 재생 (볼륨 적용)
            try:
                with wave.open(sound_file, "rb") as wf:
                    sampwidth = wf.getsampwidth()
                    samplerate = wf.getframerate()
                    n_channels = wf.getnchannels()
                    raw_data = wf.readframes(wf.getnframes())

                if sampwidth == 1:
                    audio_raw = np.frombuffer(raw_data, dtype=np.uint8)
                    audio_f = 128 + (audio_raw.astype(np.float64) - 128) * self._volume
                    audio = np.clip(audio_f, 0, 255).astype(np.uint8)
                else:
                    dtype = {2: np.int16, 4: np.int32}.get(sampwidth, np.int16)
                    audio_raw = np.frombuffer(raw_data, dtype=dtype)
                    audio_f = audio_raw.astype(np.float64) * self._volume
                    audio = np.clip(audio_f,
                                    np.iinfo(dtype).min,
                                    np.iinfo(dtype).max).astype(dtype)

                if n_channels > 1:
                    audio = audio.reshape(-1, n_channels)

                sound_duration = len(audio) / samplerate

                while not self._stop_sound.is_set():
                    if alarm_duration > 0 and (time.time() - start_time) >= alarm_duration:
                        break
                    sd.play(audio, samplerate=samplerate, blocking=False)
                    if self._stop_sound.wait(timeout=sound_duration + 0.05):
                        break

                sd.stop()
            except Exception:
                pass
            return

        if sound_file and WINSOUND_AVAILABLE:
            # winsound로 WAV 파일 비동기 재생 (SND_ASYNC → 즉시 중지 가능)
            # 파일 길이를 미리 계산해서 wait timeout으로 사용
            sound_duration = 2.0
            try:
                with wave.open(sound_file, "rb") as wf:
                    sound_duration = wf.getnframes() / wf.getframerate()
            except Exception:
                pass

            while not self._stop_sound.is_set():
                if alarm_duration > 0 and (time.time() - start_time) >= alarm_duration:
                    break
                try:
                    winsound.PlaySound(sound_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
                except Exception:
                    break
                if self._stop_sound.wait(timeout=sound_duration + 0.05):
                    break
            try:
                winsound.PlaySound(None, winsound.SND_ASYNC)
            except Exception:
                pass
            return

        # 파일 없음 → Windows 내장음 반복 재생 (SND_ASYNC → 즉시 중지 가능)
        while not self._stop_sound.is_set():
            if alarm_duration > 0 and (time.time() - start_time) >= alarm_duration:
                break
            self._play_windows_builtin()  # 이제 SND_ASYNC라서 즉시 반환
            if self._stop_sound.wait(timeout=2.0):  # 내장음 길이 약 2초
                break
        if WINSOUND_AVAILABLE:
            try:
                winsound.PlaySound(None, winsound.SND_ASYNC)
            except Exception:
                pass
