"""
텔레그램 알림 모듈
알림 발생 시 Bot API를 통해 메시지/이미지를 비동기 전송
메인 감지 루프 블로킹 없이 내부 큐 + daemon 스레드로 처리
"""
import threading
import queue
import time
import datetime

_SEND_RETRY_COUNT = 2      # 전송 실패 시 최대 재시도 횟수
_SEND_RETRY_DELAY = 5.0    # 재시도 대기 시간(초)

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
            "정파": True,
        }
        self._last_sent: dict = {}          # {key: timestamp}

        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._running: bool = False
        self._worker_lock = threading.Lock()  # 워커 스레드 재시작 원자성 보장
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="TelegramWorker"
        )

        # 연속 실패 카운터 (워커 스레드에서만 읽기/쓰기)
        self._consecutive_failures: int = 0
        # 메인 스레드 → 워커 스레드 리셋 플래그 (configure 시 설정)
        self._reset_failure_count: bool = False

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
        notify_signoff: bool = True,
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
            "정파": notify_signoff,
        }
        # 설정 변경 시 연속 실패 카운터 리셋 요청 (워커 스레드에서 처리)
        self._reset_failure_count = True

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
            self._log(f"[DIAG] notify() 비활성 상태 — 스킵 (alarm_type={alarm_type}, label={label})", error=False)
            return
        # 워커 스레드 사망 감지 → 자동 재시작 (장기 실행 안정성)
        if self._running and not self._worker_thread.is_alive():
            with self._worker_lock:
                if not self._worker_thread.is_alive():  # double-check
                    self._log("워커 스레드 비정상 종료 감지 — 재시작", error=True)
                    self._worker_thread = threading.Thread(
                        target=self._worker_loop, daemon=True, name="TelegramWorker"
                    )
                    self._worker_thread.start()
        if not self._bot_token or not self._chat_id:
            self._log("Bot Token 또는 Chat ID가 설정되지 않았습니다.", error=True)
            return
        if not self._notify_flags.get(alarm_type, True):
            self._log(f"[DIAG] notify() {alarm_type} 알림 플래그 비활성 — 스킵 (label={label})", error=False)
            return

        # 쿨다운 체크 (복구 메시지는 쿨다운 적용 안 함)
        if not is_recovery:
            key = f"{alarm_type}_{label}"
            now = time.time()
            # 24시간 이상 된 쿨다운 항목 정리 (메모리 누수 방지)
            if len(self._last_sent) > 50:
                cutoff = now - 86400
                for k in list(self._last_sent.keys()):
                    if self._last_sent[k] < cutoff:
                        del self._last_sent[k]
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
                else:
                    self._log(f"JPEG 인코딩 실패 ({alarm_type} {label}) — 텍스트만 전송", error=True)
            except Exception as e:
                self._log(f"JPEG 인코딩 예외 ({alarm_type} {label}): {e} — 텍스트만 전송", error=True)

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
            self._log(f"알림 큐 가득참 (최대 50) — {alarm_type} {label} 알림 손실", error=True)

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
                    "text": "[KBS Peacock] 텔레그램 연결 테스트 성공",
                },
                timeout=(5.0, 10.0),  # (connect_timeout, read_timeout)
            )
            if resp.status_code == 200:
                self._log("연결 테스트 성공")
                return True, "연결 테스트 성공"
            else:
                msg = f"오류 {resp.status_code}: {resp.text[:120]}"
                self._log(f"연결 테스트 실패 — {msg}", error=True)
                return False, msg
        except _requests.exceptions.Timeout:
            msg = "타임아웃 (10초 초과) — 네트워크 또는 Bot Token 확인"
            self._log(f"연결 테스트 실패 — {msg}", error=True)
            return False, msg
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            self._log(f"연결 테스트 실패 — {msg}", error=True)
            return False, msg

    # ── 워커 스레드 ───────────────────────────────────────────────────────────

    def _worker_loop(self):
        """백그라운드 전송 워커 루프"""
        while self._running:
            # configure()에서 설정된 리셋 플래그 확인 (스레드 안전: 워커에서만 쓰기)
            if self._reset_failure_count:
                self._consecutive_failures = 0
                self._reset_failure_count = False
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:    # 종료 센티널
                break
            try:
                success = self._send(item)
                if success:
                    self._consecutive_failures = 0
            except Exception as exc:
                # _send() 내부 try-except가 놓친 예외 → 스레드 사망 방지
                self._consecutive_failures += 1
                self._log_with_suppression(
                    f"전송 처리 중 예외 (스레드 유지): {type(exc).__name__}: {exc}"
                )

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """예외 유형을 사용자 친화적 문자열로 분류"""
        if _REQUESTS_AVAILABLE:
            if isinstance(exc, _requests.exceptions.ConnectionError):
                return "네트워크 차단"
            if isinstance(exc, _requests.exceptions.Timeout):
                return "응답 시간 초과"
        return type(exc).__name__

    def _log_with_suppression(self, msg: str):
        """연속 실패 카운터 기반 로그 빈도 제어 (워커 스레드 전용)"""
        n = self._consecutive_failures
        if n <= 3:
            # 1~3회: UI에 표시
            self._log(msg, error=True)
        elif n % 10 == 0:
            # 10, 20, 30...: 요약 1회 UI 표시
            self._log(f"텔레그램 {n}회 연속 실패 중 — 네트워크/방화벽 확인", error=True)
        else:
            # 4~9, 11~19...: 파일 로그만
            self._log(msg, error=False)

    def _send(self, item: dict) -> bool:
        """실제 HTTP 전송 (워커 스레드에서 실행). 성공 시 True, 최종 실패 시 False."""
        if not _REQUESTS_AVAILABLE:
            return False

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
                f"[KBS Peacock \U00002705 복구]\n"
                f"\U000023F0 시각: {now_str}\n"
                f"\U0001F4E1 채널: {channel_str}\n"
                f"\U00002714 복구: {alarm_type} 정상"
            )
        else:
            text = (
                f"[KBS Peacock \U0001F6A8 알림]\n"
                f"\U000023F0 시각: {now_str}\n"
                f"\U0001F4E1 채널: {channel_str}\n"
                f"\U000026A0 감지: {alarm_type}"
            )

        base = self._API_BASE.format(token=self._bot_token)
        timeout = (5.0, 15.0)  # (connect_timeout, read_timeout) — DNS는 별도 적용 안됨

        for attempt in range(1 + _SEND_RETRY_COUNT):
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
                    return True
                elif resp.status_code == 429:
                    # Rate Limit — 응답의 retry_after만큼 대기 후 재시도
                    try:
                        retry_after = resp.json()["parameters"]["retry_after"]
                    except Exception:
                        retry_after = 10
                    self._log(
                        f"전송 실패 429 (Rate Limit, {retry_after}초 후 재시도): "
                        f"{resp.text[:80]}",
                        error=True,
                    )
                    time.sleep(retry_after + 1)
                    # attempt 루프를 계속 진행하여 재시도
                else:
                    self._consecutive_failures += 1
                    self._log_with_suppression(
                        f"전송 실패 {resp.status_code}: {resp.text[:120]}"
                    )
                    return False  # 그 외 HTTP 오류는 재시도 없이 종료
            except Exception as exc:
                error_desc = self._classify_error(exc)
                if attempt < _SEND_RETRY_COUNT:
                    # 연속 실패 중(4회+)이면 재시도 중간 로그도 파일 전용
                    retry_msg = (
                        f"전송 오류 (재시도 {attempt + 1}/{_SEND_RETRY_COUNT}): "
                        f"{error_desc} — {exc}"
                    )
                    show_ui = self._consecutive_failures < 3
                    self._log(retry_msg, error=show_ui)
                    time.sleep(_SEND_RETRY_DELAY)
                else:
                    # 마지막 재시도도 실패 — 카운터 기반 로그
                    self._consecutive_failures += 1
                    self._log_with_suppression(
                        f"전송 실패 (재시도 소진): {error_desc} — {exc}"
                    )
                    return False
        # 429 재시도 루프 모두 소진
        self._consecutive_failures += 1
        self._log_with_suppression("전송 실패 (Rate Limit 재시도 소진)")
        return False
