"""
알림 시스템 모듈
시각적 알림(빨간 깜박임)과 소리 알림(WAV 반복 재생)을 담당
기본 알림음: Windows 내장 SystemHand (경고음) / 사용자 지정 WAV 파일 우선 사용
모든 감지 타입(블랙/스틸/오디오레벨미터/임베디드)에 동일한 통합 알림음 사용
"""
import os
import sys
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
    DEFAULT_WINDOWS_SOUND = "SystemHand"

    def __init__(self, sounds_dir: str = "resources/sounds", parent=None):
        super().__init__(parent)
        self._sounds_dir = sounds_dir
        self._sound_files: dict = {}
        self._sound_enabled = True
        self._volume = 0.8
        self._blink_timer = QTimer(self)
        self._blink_state = False
        self._active_alarms: set = set()
        self._sound_thread: threading.Thread = None
        self._stop_sound = threading.Event()
        self._logger = None

        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_timer.setInterval(500)

    def set_logger(self, logger):
        """UI 로그 위젯 출력용 로거 주입"""
        self._logger = logger

    def _log(self, msg: str):
        if self._logger:
            self._logger.warning(msg)
        else:
            print(f"[AlarmSystem] {msg}", file=sys.stderr)

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
            self._stop_playback()
            self._blink_timer.stop()
            self._blink_state = False
            self.visual_blink.emit(False)

    def resolve_all(self):
        """모든 알림 해제"""
        self._active_alarms.clear()
        self._stop_playback()
        self._blink_timer.stop()
        self._blink_state = False
        self.visual_blink.emit(False)

    def _stop_playback(self):
        """사운드 재생 중지 (이벤트 + sounddevice 즉시 정지)"""
        self._stop_sound.set()
        if SOUNDDEVICE_AVAILABLE:
            try:
                sd.stop()
            except Exception:
                pass
        if WINSOUND_AVAILABLE:
            try:
                winsound.PlaySound(None, winsound.SND_ASYNC)
            except Exception:
                pass

    def set_sound_enabled(self, enabled: bool):
        self._sound_enabled = enabled
        if not enabled:
            self._stop_playback()
        else:
            if self._active_alarms:
                self._play_sound("default")

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, volume))

    def set_sounds_dir(self, path: str):
        self._sounds_dir = path

    def set_sound_file(self, alarm_type: str, path: str):
        """알림음 파일 경로 설정"""
        self._sound_files[alarm_type] = path

    def get_sound_files(self) -> dict:
        return dict(self._sound_files)

    def play_test_sound(self, alarm_type: str):
        """테스트용 알림음 1회 재생"""
        # 파일 경로 확인 및 절대경로 변환
        raw_path = self._sound_files.get("default", "")
        if raw_path:
            abs_path = os.path.abspath(raw_path)
            exists = os.path.exists(abs_path)
            self._log(f"알림음 테스트: {os.path.basename(abs_path)}")
            self._log(f"  경로: {abs_path}")
            self._log(f"  파일 존재: {exists}")
            sound_file = abs_path if exists else None
            if not exists:
                self._log("  파일 없음 → Windows 내장음으로 대체")
        else:
            sound_file = None
            self._log("알림음 테스트: 파일 미설정 → Windows 내장음 사용")
            self._log(f"  _sound_files 현재값: {self._sound_files}")

        # 기존 재생 중지 — 새 Event 객체로 이전 스레드와 완전히 분리
        self._stop_playback()
        if self._sound_thread and self._sound_thread.is_alive():
            self._sound_thread.join(timeout=1.5)
        self._stop_sound = threading.Event()  # 새 이벤트 (이전 스레드 잔재 방지)
        self._sound_thread = threading.Thread(
            target=self._play_test_worker,
            args=(sound_file,),
            daemon=True,
        )
        self._sound_thread.start()

    def _play_test_worker(self, sound_file: str | None):
        """테스트 전용 1회 재생 (winsound → sounddevice → 내장음 순)"""
        self._log(f"  적용 볼륨: {self._volume:.2f}")

        # ── winsound + WAV 파일 (우선 시도 — 장치/볼륨 문제 없음) ──────
        if sound_file and WINSOUND_AVAILABLE:
            try:
                self._log("  winsound 재생 시작...")
                winsound.PlaySound(sound_file, winsound.SND_FILENAME | winsound.SND_SYNC)
                self._log("  winsound 재생 완료")
                return
            except Exception as e:
                self._log(f"  winsound 실패: {e}")

        # ── sounddevice (fallback) ───────────────────────────────────────
        if sound_file and SOUNDDEVICE_AVAILABLE:
            try:
                if SOUNDDEVICE_AVAILABLE:
                    self._log(f"  sounddevice 기본 장치: {sd.default.device}")
                with wave.open(sound_file, "rb") as wf:
                    sampwidth = wf.getsampwidth()
                    samplerate = wf.getframerate()
                    n_channels = wf.getnchannels()
                    raw_data = wf.readframes(wf.getnframes())

                # 볼륨 0이면 무음 — 최소 0.5 보장
                vol = max(self._volume, 0.5)
                if sampwidth == 1:
                    audio_raw = np.frombuffer(raw_data, dtype=np.uint8)
                    audio_f = 128 + (audio_raw.astype(np.float64) - 128) * vol
                    audio = np.clip(audio_f, 0, 255).astype(np.uint8)
                else:
                    dtype = {2: np.int16, 4: np.int32}.get(sampwidth, np.int16)
                    audio_raw = np.frombuffer(raw_data, dtype=dtype)
                    audio_f = audio_raw.astype(np.float64) * vol
                    audio = np.clip(
                        audio_f, np.iinfo(dtype).min, np.iinfo(dtype).max
                    ).astype(dtype)

                if n_channels > 1:
                    audio = audio.reshape(-1, n_channels)

                self._log("  sounddevice 재생 시작...")
                sd.play(audio, samplerate=samplerate)
                sd.wait()
                self._log("  sounddevice 재생 완료")
                return
            except Exception as e:
                self._log(f"  sounddevice 실패: {e}")
                try:
                    sd.stop()
                except Exception:
                    pass

        # ── 내장음 fallback ─────────────────────────────────────────────
        self._log("  내장음(SystemHand) 재생")
        if WINSOUND_AVAILABLE:
            try:
                winsound.PlaySound(
                    self.DEFAULT_WINDOWS_SOUND,
                    winsound.SND_ALIAS | winsound.SND_SYNC,
                )
            except Exception:
                try:
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                except Exception:
                    pass

    def _toggle_blink(self):
        self._blink_state = not self._blink_state
        self.visual_blink.emit(self._blink_state)

    def _play_sound(self, alarm_type: str, alarm_duration: float = 0.0):
        """사운드 반복 재생 시작 (별도 스레드). 이미 재생 중이면 건너뜀."""
        if not self._sound_enabled:
            return
        if self._sound_thread and self._sound_thread.is_alive():
            return

        self._stop_sound.clear()
        self._sound_thread = threading.Thread(
            target=self._play_sound_worker,
            args=("default", alarm_duration),
            daemon=True,
        )
        self._sound_thread.start()

    def _get_sound_path(self) -> str | None:
        """통합 알림음 파일 경로 반환 (없으면 None → Windows 내장음)"""
        path = self._sound_files.get("default", "")
        if path and os.path.exists(path):
            return path
        for p in self._sound_files.values():
            if p and os.path.exists(p):
                return p
        return None

    def _play_windows_builtin(self):
        """Windows 내장 경고음 비동기 재생"""
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
        """사운드 반복 재생 (stop_sound 이벤트 또는 alarm_duration 초과 시 종료)"""
        raw_file = self._get_sound_path()
        # 절대경로 변환 (상대경로는 cwd 의존적이므로)
        sound_file = os.path.abspath(raw_file) if raw_file else None
        start_time = time.time()

        # ── winsound + WAV 파일 (우선 시도 — 장치/볼륨 문제 없음) ──────
        if sound_file and WINSOUND_AVAILABLE:
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
                except Exception as e:
                    self._log(f"winsound 파일 재생 실패 → 내장음 대체: {e}")
                    break
                if self._stop_sound.wait(timeout=sound_duration + 0.05):
                    break
            try:
                winsound.PlaySound(None, winsound.SND_ASYNC)
            except Exception:
                pass
            return

        # ── sounddevice + WAV 파일 (fallback, 볼륨 조절 가능) ──────────
        if sound_file and SOUNDDEVICE_AVAILABLE:
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

                while not self._stop_sound.is_set():
                    if alarm_duration > 0 and (time.time() - start_time) >= alarm_duration:
                        break
                    sd.play(audio, samplerate=samplerate)
                    sd.wait()  # 재생 완료 또는 sd.stop() 호출 시까지 블록

                try:
                    sd.stop()
                except Exception:
                    pass
                return

            except Exception as e:
                self._log(f"sounddevice 재생 실패 → 내장음 대체: {e}")
                try:
                    sd.stop()
                except Exception:
                    pass

        # ── 파일 없음: Windows 내장음 ──────────────────────────────────
        while not self._stop_sound.is_set():
            if alarm_duration > 0 and (time.time() - start_time) >= alarm_duration:
                break
            self._play_windows_builtin()
            if self._stop_sound.wait(timeout=2.0):
                break
        if WINSOUND_AVAILABLE:
            try:
                winsound.PlaySound(None, winsound.SND_ASYNC)
            except Exception:
                pass
