"""
자동 녹화 모듈
알림 발생 시 사고 전 N초 + 사고 후 M초를 지정 해상도/FPS로 MP4 자동 저장.
순환 버퍼(JPEG 압축)로 "사고 전" 구간 구현, 오래된 파일 자동 삭제.
오디오(임베디드)를 WAV로 동시 버퍼링하여 ffmpeg로 영상과 합성.
ffmpeg 미설치 시 영상만 저장(폴백).
"""
import os
import subprocess
import threading
import time
import wave
import datetime
from collections import deque
from typing import Optional

import logging

import cv2
import numpy as np

_log = logging.getLogger(__name__)

_JPEG_QUALITY = 85
_MAX_RECORD_FRAMES = 9000  # 녹화 큐 최대 프레임 수 (30fps × 300초 = 5분 상한, 메모리 보호)

# AudioMonitorThread 와 동일한 오디오 파라미터
_AUDIO_SR    = 44100   # 샘플레이트 (Hz)
_AUDIO_CH    = 2       # 채널 수 (스테레오)
_AUDIO_CHUNK = 1024    # 청크 크기 (AudioMonitorThread.CHUNK 와 동일)


class AutoRecorder:
    """
    순환 버퍼 기반 자동 녹화기.
    - push_frame(): frame_ready 신호마다 호출, 비디오 버퍼에 JPEG 압축 저장
    - push_audio(): audio_chunk 신호마다 호출, 오디오 버퍼에 raw PCM 저장
    - trigger(): 알림 발생 시 호출, 별도 스레드에서 MP4(+오디오) 생성
    - _cleanup_loop(): 1시간마다 오래된 파일 자동 삭제
    """

    def __init__(self):
        self._enabled: bool = False
        self._save_dir: str = "recordings"
        self._pre_seconds: float = 5.0
        self._post_seconds: float = 15.0
        self._max_keep_days: int = 7

        # 녹화 출력 해상도/FPS
        self._out_w: int = 960
        self._out_h: int = 540
        self._out_fps: int = 10
        self._buf_interval: float = 1.0 / self._out_fps

        # ── 비디오 순환 버퍼: deque[(timestamp, jpeg_bytes)] ──────────────
        maxlen = int(self._pre_seconds * self._out_fps) + 5
        self._buffer: deque = deque(maxlen=maxlen)
        self._buffer_lock = threading.Lock()
        self._last_buf_time: float = 0.0

        # ── 오디오 순환 버퍼: deque[(timestamp, bytes)] ──────────────────
        # 청크 수 = pre_seconds * 샘플레이트 / 청크크기
        audio_maxlen = int(self._pre_seconds * _AUDIO_SR / _AUDIO_CHUNK) + 10
        self._audio_buffer: deque = deque(maxlen=audio_maxlen)
        self._audio_lock = threading.Lock()

        # 녹화 상태
        self._recording: bool = False
        self._record_end: float = 0.0
        self._record_queue: deque = deque()          # 사고 후 비디오 프레임 큐
        self._audio_record_queue: deque = deque()    # 사고 후 오디오 청크 큐
        self._record_thread: Optional[threading.Thread] = None

        # 자동 삭제 스레드
        self._running: bool = False
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="RecorderCleanup"
        )

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self):
        """자동 삭제 스레드 시작 (프로그램 시작 시 1회 호출)"""
        self._cleanup_orphan_temp_files()
        self._running = True
        self._cleanup_thread.start()

    def _cleanup_orphan_temp_files(self):
        """이전 비정상 종료로 남은 임시 파일(*_vtmp.mp4, *_atmp.wav) 삭제"""
        if not os.path.isdir(self._save_dir):
            return
        try:
            for fname in os.listdir(self._save_dir):
                if fname.endswith("_vtmp.mp4") or fname.endswith("_atmp.wav"):
                    try:
                        os.remove(os.path.join(self._save_dir, fname))
                    except OSError:
                        pass
        except Exception:
            pass

    def stop(self):
        """정지 (프로그램 종료 시 호출)"""
        self._running = False

    # ── 설정 ──────────────────────────────────────────────────────────────────

    def configure(
        self,
        enabled: bool,
        save_dir: str,
        pre_seconds: float,
        post_seconds: float,
        max_keep_days: int,
        output_width: int = 960,
        output_height: int = 540,
        output_fps: int = 10,
    ):
        """설정 반영 및 버퍼 크기 재계산"""
        self._enabled = enabled
        self._save_dir = save_dir or "recordings"
        self._pre_seconds = max(1.0, float(pre_seconds))
        self._post_seconds = max(1.0, float(post_seconds))
        self._max_keep_days = max(1, int(max_keep_days))
        self._out_w = max(160, int(output_width))
        self._out_h = max(90, int(output_height))
        self._out_fps = max(1, int(output_fps))
        self._buf_interval = 1.0 / self._out_fps

        # 비디오 버퍼 크기 재계산
        new_maxlen = int(self._pre_seconds * self._out_fps) + 5
        with self._buffer_lock:
            old = list(self._buffer)[-new_maxlen:]
            self._buffer = deque(old, maxlen=new_maxlen)

        # 오디오 버퍼 크기 재계산
        new_audio_maxlen = int(self._pre_seconds * _AUDIO_SR / _AUDIO_CHUNK) + 10
        with self._audio_lock:
            old_audio = list(self._audio_buffer)[-new_audio_maxlen:]
            self._audio_buffer = deque(old_audio, maxlen=new_audio_maxlen)

    # ── 프레임 수신 ───────────────────────────────────────────────────────────

    def push_frame(self, frame: np.ndarray):
        """
        frame_ready 신호마다 호출.
        _out_fps 간격으로 JPEG 인코딩 후 순환 버퍼에 저장.
        녹화 중이면 출력 해상도로 리사이즈한 프레임을 녹화 큐에도 추가.
        """
        if not self._enabled:
            return

        now = time.time()

        if now - self._last_buf_time >= self._buf_interval:
            self._last_buf_time = now
            try:
                small = cv2.resize(frame, (self._out_w, self._out_h))
                ok, buf = cv2.imencode(
                    ".jpg", small,
                    [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY],
                )
                if ok:
                    with self._buffer_lock:
                        self._buffer.append((now, buf.tobytes()))
            except Exception:
                pass

        if self._recording:
            if now < self._record_end and len(self._record_queue) < _MAX_RECORD_FRAMES:
                try:
                    small = cv2.resize(frame, (self._out_w, self._out_h))
                    self._record_queue.append((now, small))
                except Exception:
                    pass
            else:
                if len(self._record_queue) >= _MAX_RECORD_FRAMES:
                    _log.warning(
                        "녹화 큐 상한 도달 (%d프레임) — 녹화 종료 (메모리 보호)",
                        _MAX_RECORD_FRAMES,
                    )
                self._recording = False

    # ── 오디오 청크 수신 ──────────────────────────────────────────────────────

    def push_audio(self, samples: np.ndarray, timestamp: float):
        """
        audio_chunk 신호마다 호출 (AudioMonitorThread → MainWindow → 여기).
        int16 raw PCM을 순환 버퍼에 저장.
        녹화 중이면 녹화 큐에도 추가.
        """
        if not self._enabled:
            return

        raw = samples.tobytes()
        with self._audio_lock:
            self._audio_buffer.append((timestamp, raw))

        if self._recording and timestamp < self._record_end:
            self._audio_record_queue.append((timestamp, raw))

    # ── 알림 발생 트리거 ──────────────────────────────────────────────────────

    def trigger(self, alarm_type: str, label: str, media_name: str = ""):
        """
        알림 발생 시 호출. 이미 녹화 중이면 종료 시간만 연장.
        그렇지 않으면 새 녹화 스레드 시작.
        """
        if not self._enabled:
            return

        now = time.time()
        new_end = now + self._post_seconds

        if self._recording:
            if new_end > self._record_end:
                self._record_end = new_end
            return

        # 이전 스레드 alive 체크 — 상태 변경(_recording=True) 이전에 배치
        # _MAX_RECORD_FRAMES 도달 시 push_frame()이 _recording=False로 전환하지만,
        # 그 이후에도 _record_worker는 ffmpeg 합성(최대 120초)을 계속 실행 중일 수 있음.
        # → 이 체크가 없으면 ffmpeg 합성 중에 새 스레드가 시작되어 동시 실행 가능
        if self._record_thread is not None and self._record_thread.is_alive():
            _log.warning("이전 녹화 스레드 실행 중 (ffmpeg 합성) — 새 녹화 스킵 (%s %s)", alarm_type, label)
            return

        # 새 녹화 시작
        self._recording = True
        self._record_end = new_end
        self._record_queue.clear()
        self._audio_record_queue.clear()

        # 사고 전 버퍼 스냅샷
        with self._buffer_lock:
            pre_frames = list(self._buffer)
        with self._audio_lock:
            pre_audio = list(self._audio_buffer)

        # 파일 경로 생성
        os.makedirs(self._save_dir, exist_ok=True)
        now_dt = datetime.datetime.now()
        ts = now_dt.strftime("%Y%m%d_%H%M%S") + f"_{now_dt.microsecond // 1000:03d}"
        safe_label = label.replace("/", "_").replace("\\", "_")
        safe_media = media_name.replace("/", "_").replace("\\", "_") if media_name else ""
        safe_type = alarm_type.replace("/", "_")
        if safe_media:
            filename = f"{ts}_{safe_label}_{safe_media}_{safe_type}.mp4"
        else:
            filename = f"{ts}_{safe_label}_{safe_type}.mp4"
        filepath = os.path.join(self._save_dir, filename)

        self._record_thread = threading.Thread(
            target=self._record_worker,
            args=(pre_frames, pre_audio, filepath),
            daemon=True,
            name="RecorderWriter",
        )
        self._record_thread.start()

    # ── 녹화 워커 ─────────────────────────────────────────────────────────────

    def _record_worker(self, pre_frames: list, pre_audio: list, filepath: str):
        """
        MP4 녹화 워커 스레드.
        1) 비디오(mp4v)와 오디오(WAV)를 임시 파일로 동시 기록
        2) ffmpeg로 합성 → 최종 MP4
        3) ffmpeg 미설치 시 비디오만 저장(폴백)
        """
        base = filepath[:-4] if filepath.endswith(".mp4") else filepath
        vtmp = base + "_vtmp.mp4"
        atmp = base + "_atmp.wav"

        # ── 비디오 Writer ──────────────────────────────────────────────────
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(vtmp, fourcc, self._out_fps, (self._out_w, self._out_h))
        if not writer.isOpened():
            return

        has_audio = False
        wav_file = None
        merged = False

        try:
            try:
                # ── 오디오 WAV Writer (실패 시 비디오만 저장) ─────────────
                try:
                    wav_file = wave.open(atmp, "wb")
                    wav_file.setnchannels(_AUDIO_CH)
                    wav_file.setsampwidth(2)          # int16 = 2바이트
                    wav_file.setframerate(_AUDIO_SR)
                except Exception:
                    wav_file = None                   # 오디오 없이 비디오만 기록

                # 1) 사고 전 비디오 버퍼 기록
                for _ts, jpeg_bytes in pre_frames:
                    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                    frm = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frm is not None:
                        writer.write(frm)

                # 2) 사고 전 오디오 버퍼 기록
                if wav_file is not None:
                    for _ts, raw in pre_audio:
                        wav_file.writeframes(raw)
                        has_audio = True

                # 3) 사고 후 실시간 프레임/오디오 기록 (녹화 플래그 해제까지)
                while True:
                    while self._record_queue:
                        _ts, frm = self._record_queue.popleft()
                        writer.write(frm)

                    if wav_file is not None:
                        while self._audio_record_queue:
                            _ts, raw = self._audio_record_queue.popleft()
                            wav_file.writeframes(raw)
                            has_audio = True

                    if not self._recording:
                        # 남은 큐 모두 기록 후 종료
                        while self._record_queue:
                            _ts, frm = self._record_queue.popleft()
                            writer.write(frm)
                        if wav_file is not None:
                            while self._audio_record_queue:
                                _ts, raw = self._audio_record_queue.popleft()
                                wav_file.writeframes(raw)
                                has_audio = True
                        break
                    else:
                        time.sleep(0.02)
            finally:
                writer.release()
                if wav_file is not None:
                    wav_file.close()

            # 4) ffmpeg 합성 (오디오가 있을 때만 시도)
            if has_audio:
                # 비디오/오디오 시작 타임스탬프 기반 싱크 오프셋 계산
                v_start = pre_frames[0][0] if pre_frames else None
                a_start = pre_audio[0][0] if pre_audio else None
                audio_offset = (a_start - v_start) if (v_start and a_start) else 0.0

                merged = self._merge_with_ffmpeg(vtmp, atmp, filepath, audio_offset)

        finally:
            # 5) 정리: 임시 파일 삭제, 폴백 처리 (예외 시에도 반드시 실행)
            if merged:
                # 합성 성공 → vtmp 삭제 (atmp는 아래에서 삭제)
                try:
                    os.remove(vtmp)
                except Exception:
                    pass
            else:
                # 합성 실패 또는 오디오 없음 → vtmp를 최종 파일로 사용
                try:
                    if os.path.exists(vtmp):
                        os.rename(vtmp, filepath)
                except Exception:
                    pass

            try:
                if os.path.exists(atmp):
                    os.remove(atmp)
            except Exception:
                pass

    # ── ffmpeg 합성 ───────────────────────────────────────────────────────────

    @staticmethod
    def _find_ffmpeg() -> str:
        """
        ffmpeg 실행파일 경로 탐색.
        우선순위:
          1) 시스템 PATH — winget으로 설치 권장 (winget install ffmpeg)
          2) 전용 설치 위치 (C:\\KBS_Tools\\ffmpeg.exe) — 수동 설치 시
          3) 번들 (resources/bin/ffmpeg.exe) — 직접 동봉 시
        설치 명령: winget install ffmpeg  (한 번만 설치, 프로그램 업데이트와 무관)
        """
        import shutil
        # 1) 시스템 PATH (winget 설치 기본 경로)
        if shutil.which("ffmpeg"):
            return "ffmpeg"
        # 2) KBS 전용 위치 (수동 설치)
        dedicated = r"C:\KBS_Tools\ffmpeg.exe"
        if os.path.isfile(dedicated):
            return dedicated
        # 3) 번들 (resources/bin/ffmpeg.exe)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bundled = os.path.join(base_dir, "resources", "bin", "ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled
        return "ffmpeg"

    @staticmethod
    def _merge_with_ffmpeg(vtmp: str, atmp: str, output: str, audio_offset: float = 0.0) -> bool:
        """
        ffmpeg로 비디오(vtmp)와 오디오(atmp)를 합성하여 output MP4 생성.
        audio_offset > 0: 오디오가 비디오보다 늦게 시작 → itsoffset으로 딜레이
        audio_offset < 0: 오디오가 비디오보다 빨리 시작 → -ss로 앞부분 트림
        반환값: True(성공) / False(ffmpeg 미설치 또는 오류)
        """
        ffmpeg = AutoRecorder._find_ffmpeg()
        cmd = [ffmpeg, "-y", "-i", vtmp]

        if audio_offset > 0.05:
            cmd += ["-itsoffset", f"{audio_offset:.3f}"]
            cmd += ["-i", atmp]
        elif audio_offset < -0.05:
            cmd += ["-ss", f"{-audio_offset:.3f}", "-i", atmp]
        else:
            cmd += ["-i", atmp]

        cmd += [
            "-c:v", "copy",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            output,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=120,
            )
            return result.returncode == 0
        except FileNotFoundError:
            # ffmpeg 미설치
            return False
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False

    # ── 자동 삭제 ─────────────────────────────────────────────────────────────

    def _cleanup_loop(self):
        """1시간마다 max_keep_days 초과 파일 및 고아 임시파일 자동 삭제"""
        while self._running:
            self._delete_old_files()
            self._cleanup_orphan_temp_files()
            for _ in range(3600):
                if not self._running:
                    return
                time.sleep(1)

    def _delete_old_files(self):
        """max_keep_days보다 오래된 MP4 파일 삭제"""
        if not os.path.isdir(self._save_dir):
            return
        cutoff = time.time() - self._max_keep_days * 86400
        try:
            for fname in os.listdir(self._save_dir):
                if not fname.lower().endswith(".mp4"):
                    continue
                fpath = os.path.join(self._save_dir, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                except Exception:
                    pass
        except Exception:
            pass
