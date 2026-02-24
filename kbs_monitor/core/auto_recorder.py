"""
자동 녹화 모듈
알림 발생 시 사고 전 N초 + 사고 후 M초를 지정 해상도/FPS로 MP4 자동 저장.
순환 버퍼(JPEG 압축)로 "사고 전" 구간 구현, 오래된 파일 자동 삭제.
감지 루프와 독립적으로 daemon 스레드에서 처리.
"""
import os
import threading
import time
import datetime
from collections import deque
from typing import Optional

import cv2
import numpy as np


_JPEG_QUALITY = 85


class AutoRecorder:
    """
    순환 버퍼 기반 자동 녹화기.
    - push_frame(): frame_ready 신호마다 호출, 버퍼에 JPEG 압축 저장
    - trigger(): 알림 발생 시 호출, 별도 스레드에서 MP4 생성
    - _cleanup_loop(): 1시간마다 오래된 파일 자동 삭제
    """

    def __init__(self):
        self._enabled: bool = False
        self._save_dir: str = "recordings"
        self._pre_seconds: float = 5.0
        self._post_seconds: float = 15.0
        self._max_keep_days: int = 7

        # 녹화 출력 해상도/FPS (configure()로 변경 가능)
        self._out_w: int = 960
        self._out_h: int = 540
        self._out_fps: int = 10
        self._buf_interval: float = 1.0 / self._out_fps

        # 순환 버퍼: deque[(timestamp, jpeg_bytes)]
        maxlen = int(self._pre_seconds * self._out_fps) + 5
        self._buffer: deque = deque(maxlen=maxlen)
        self._buffer_lock = threading.Lock()
        self._last_buf_time: float = 0.0

        # 녹화 상태
        self._recording: bool = False
        self._record_end: float = 0.0
        self._record_queue: deque = deque()
        self._record_thread: Optional[threading.Thread] = None

        # 자동 삭제 스레드
        self._running: bool = False
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="RecorderCleanup"
        )

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self):
        """자동 삭제 스레드 시작 (프로그램 시작 시 1회 호출)"""
        self._running = True
        self._cleanup_thread.start()

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

        new_maxlen = int(self._pre_seconds * self._out_fps) + 5
        with self._buffer_lock:
            old = list(self._buffer)[-new_maxlen:]
            self._buffer = deque(old, maxlen=new_maxlen)

    # ── 프레임 수신 ───────────────────────────────────────────────────────────

    def push_frame(self, frame: np.ndarray):
        """
        frame_ready 신호마다 호출.
        _out_fps 간격으로 JPEG 인코딩 후 순환 버퍼에 저장.
        녹화 중이면 출력 해상도로 리사이즈한 프레임을 녹화 큐에도 추가.
        성능: JPEG 인코딩 약 1~2ms / 호출마다 if 비교 1회
        """
        if not self._enabled:
            return

        now = time.time()

        # 버퍼: _buf_interval 간격으로만 저장 (다운샘플)
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

        # 녹화 중: 녹화 큐에 출력 해상도 리사이즈 프레임 추가
        if self._recording:
            if now < self._record_end:
                try:
                    small = cv2.resize(frame, (self._out_w, self._out_h))
                    self._record_queue.append((now, small))
                except Exception:
                    pass
            else:
                self._recording = False     # 녹화 종료 플래그

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
            # 종료 시간 연장 (연속 알림 시 녹화 이어서 진행)
            if new_end > self._record_end:
                self._record_end = new_end
            return

        # 새 녹화 시작
        self._recording = True
        self._record_end = new_end
        self._record_queue.clear()

        # 사고 전 버퍼 스냅샷
        with self._buffer_lock:
            pre_frames = list(self._buffer)

        # 파일 경로 생성
        os.makedirs(self._save_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = label.replace("/", "_").replace("\\", "_")
        safe_type = alarm_type.replace("/", "_")
        filename = f"{ts}_{safe_label}_{safe_type}.mp4"
        filepath = os.path.join(self._save_dir, filename)

        self._record_thread = threading.Thread(
            target=self._record_worker,
            args=(pre_frames, filepath),
            daemon=True,
            name="RecorderWriter",
        )
        self._record_thread.start()

    # ── 녹화 워커 ─────────────────────────────────────────────────────────────

    def _record_worker(self, pre_frames: list, filepath: str):
        """
        MP4 녹화 워커 스레드.
        pre_frames(JPEG bytes 리스트) → 실시간 record_queue → VideoWriter 저장.
        """
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(filepath, fourcc, self._out_fps, (self._out_w, self._out_h))
        if not writer.isOpened():
            return

        try:
            # 1) 사고 전 버퍼 기록
            for _ts, jpeg_bytes in pre_frames:
                arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frm = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frm is not None:
                    writer.write(frm)

            # 2) 사고 후 실시간 프레임 기록 (녹화 플래그 해제까지)
            while True:
                if self._record_queue:
                    _ts, frm = self._record_queue.popleft()
                    writer.write(frm)
                elif not self._recording:
                    # 남은 큐 모두 기록 후 종료
                    while self._record_queue:
                        _ts, frm = self._record_queue.popleft()
                        writer.write(frm)
                    break
                else:
                    time.sleep(0.02)    # 큐 채워지기 대기
        finally:
            writer.release()

    # ── 자동 삭제 ─────────────────────────────────────────────────────────────

    def _cleanup_loop(self):
        """1시간마다 max_keep_days 초과 파일 자동 삭제"""
        while self._running:
            self._delete_old_files()
            # 1시간 대기 (1초씩 체크하여 stop() 즉시 반응)
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
