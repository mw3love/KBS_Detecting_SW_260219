"""
텔레그램 알림 모듈
알림 발생 시 Bot API를 통해 메시지/이미지를 비동기 전송
메인 감지 루프 블로킹 없이 내부 큐 + daemon 스레드로 처리
"""
import threading
import queue
import time
import datetime

import cv2
import numpy as np

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False


class TelegramNotifier:
    """
    텔레그램 Bot API 클라이언트.
    내부 큐 + daemon 스레드로 HTTP 전송 — notify() 호출 즉시 반환.
    """

    _API_BASE = "https://api.telegram.org/bot{token}"

    def __init__(self):
        self._enabled: bool = False
        self._bot_token: str = ""
        self._chat_id: str = ""
        self._send_image: bool = True
        self._cooldown: float = 60.0        # 동일 채널 재발송 방지(초)
        self._notify_flags: dict = {        # 감지 타입별 전송 활성화
            "블랙": True,
            "스틸": True,
            "오디오": True,
            "무음": True,
        }
        self._last_sent: dict = {}          # {key: timestamp}

        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._running: bool = False
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="TelegramWorker"
        )

        # 로그 콜백 (main_window에서 set_logger()로 주입)
        self._log_info = None
        self._log_error = None

    def set_logger(self, log_info, log_error=None):
        """AppLogger 콜백 주입 — 텔레그램 상태를 메인 로그 창에 표시"""
        self._log_info = log_info
        self._log_error = log_error or log_info

    def _log(self, msg: str, error: bool = False):
        fn = self._log_error if error else self._log_info
        if fn:
            fn(f"TELEGRAM - {msg}")

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self):
        """워커 스레드 시작 (프로그램 시작 시 1회 호출)"""
        self._running = True
        self._worker_thread.start()

    def stop(self):
        """워커 스레드 정지 (프로그램 종료 시 호출)"""
        self._running = False
        try:
            self._queue.put_nowait(None)    # 종료 센티널
        except queue.Full:
            pass
        self._worker_thread.join(timeout=5.0)

    # ── 설정 ──────────────────────────────────────────────────────────────────

    def configure(
        self,
        enabled: bool,
        bot_token: str,
        chat_id: str,
        send_image: bool,
        cooldown: float,
        notify_black: bool = True,
        notify_still: bool = True,
        notify_audio_level: bool = True,
        notify_embedded: bool = True,
    ):
        """설정 반영 (메인 스레드에서 호출)"""
        self._enabled = enabled
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._send_image = send_image
        self._cooldown = max(0.0, cooldown)
        self._notify_flags = {
            "블랙": notify_black,
            "스틸": notify_still,
            "오디오": notify_audio_level,
            "무음": notify_embedded,
        }

    # ── 알림 발생 ─────────────────────────────────────────────────────────────

    def notify(
        self,
        alarm_type: str,
        label: str,
        media_name: str,
        frame: np.ndarray = None,
        is_recovery: bool = False,
    ):
        """
        알림 발생 또는 복구 시 호출 (메인 스레드).
        is_recovery=True이면 복구 메시지 전송 (쿨다운 미적용).
        쿨다운 체크 → JPEG 인코딩 → 큐 삽입 후 즉시 반환.
        """
        if not _REQUESTS_AVAILABLE:
            self._log("requests 라이브러리 미설치 — pip install requests", error=True)
            return
        if not self._enabled:
            return  # 비활성 상태 — 로그 없이 종료
        if not self._bot_token or not self._chat_id:
            self._log("Bot Token 또는 Chat ID가 설정되지 않았습니다.", error=True)
            return
        if not self._notify_flags.get(alarm_type, True):
            return

        # 쿨다운 체크 (복구 메시지는 쿨다운 적용 안 함)
        if not is_recovery:
            key = f"{alarm_type}_{label}"
            now = time.time()
            if now - self._last_sent.get(key, 0.0) < self._cooldown:
                return
            self._last_sent[key] = now

        # 스냅샷 JPEG 인코딩 (메인 스레드에서 복사본으로 처리)
        jpeg_bytes = None
        if self._send_image and frame is not None:
            try:
                success, buf = cv2.imencode(
                    ".jpg", frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 85],
                )
                if success:
                    jpeg_bytes = buf.tobytes()
            except Exception:
                pass

        item = {
            "alarm_type": alarm_type,
            "label": label,
            "media_name": media_name or label,
            "jpeg_bytes": jpeg_bytes,
            "is_recovery": is_recovery,
        }
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            pass   # 큐 가득 참 → 무시 (감지 루프 영향 없음)

    # ── 연결 테스트 ───────────────────────────────────────────────────────────

    def test_connection(self, token: str, chat_id: str) -> tuple:
        """
        연결 테스트 (설정 탭 버튼 클릭 시 동기 호출).
        반환: (성공 여부: bool, 메시지: str)
        """
        if not _REQUESTS_AVAILABLE:
            return False, "requests 라이브러리가 설치되지 않았습니다.\npip install requests"
        token = token.strip()
        chat_id = chat_id.strip()
        if not token or not chat_id:
            return False, "Bot Token과 Chat ID를 입력하세요."
        try:
            url = f"{self._API_BASE.format(token=token)}/sendMessage"
            resp = _requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": "[KBS Argos] 텔레그램 연결 테스트 성공",
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                return True, "연결 테스트 성공"
            else:
                return False, f"오류 {resp.status_code}: {resp.text[:120]}"
        except Exception as exc:
            return False, str(exc)

    # ── 워커 스레드 ───────────────────────────────────────────────────────────

    def _worker_loop(self):
        """백그라운드 전송 워커 루프"""
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:    # 종료 센티널
                break
            self._send(item)

    def _send(self, item: dict):
        """실제 HTTP 전송 (워커 스레드에서 실행)"""
        if not _REQUESTS_AVAILABLE:
            return

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        alarm_type = item["alarm_type"]
        label = item["label"]
        media_name = item["media_name"]
        is_recovery = item.get("is_recovery", False)

        # 이모지는 Python f-string 4바이트 코드포인트(\U)로만 사용
        channel_str = f"{label}"
        if media_name != label:
            channel_str += f" ({media_name})"

        if is_recovery:
            text = (
                f"[KBS Argos \U00002705 복구]\n"
                f"\U000023F0 시각: {now_str}\n"
                f"\U0001F4E1 채널: {channel_str}\n"
                f"\U00002714 복구: {alarm_type} 정상"
            )
        else:
            text = (
                f"[KBS Argos \U0001F6A8 알림]\n"
                f"\U000023F0 시각: {now_str}\n"
                f"\U0001F4E1 채널: {channel_str}\n"
                f"\U000026A0 감지: {alarm_type}"
            )

        base = self._API_BASE.format(token=self._bot_token)
        timeout = 15.0

        try:
            if item.get("jpeg_bytes"):
                resp = _requests.post(
                    f"{base}/sendPhoto",
                    data={"chat_id": self._chat_id, "caption": text},
                    files={
                        "photo": ("snapshot.jpg", item["jpeg_bytes"], "image/jpeg")
                    },
                    timeout=timeout,
                )
            else:
                resp = _requests.post(
                    f"{base}/sendMessage",
                    json={"chat_id": self._chat_id, "text": text},
                    timeout=timeout,
                )
            if resp.status_code == 200:
                kind = "복구" if is_recovery else "알림"
                self._log(f"{alarm_type} {kind} 전송 완료 ({channel_str})")
            else:
                self._log(
                    f"전송 실패 {resp.status_code}: {resp.text[:120]}", error=True
                )
        except Exception as exc:
            self._log(f"전송 오류: {exc}", error=True)
