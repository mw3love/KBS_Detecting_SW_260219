"""
메인 윈도우
3분할 레이아웃: 상단 바 + 비디오 영역(~75%) + 로그 영역(~25%)
"""
import copy
import datetime
import logging
import os
import subprocess
import sys
import time
import traceback
from typing import Optional

_log = logging.getLogger("kbs_monitor")

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QApplication,
)
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent

from ui.top_bar import TopBar
from ui.video_widget import VideoWidget
from ui.log_widget import LogWidget
from ui.settings_dialog import SettingsDialog
from ui.roi_editor import ROIEditorCanvas
from core.video_capture import VideoCaptureThread
from core.audio_monitor import AudioMonitorThread
from core.roi_manager import ROIManager
from core.detector import Detector
from core.alarm import AlarmSystem
from core.telegram_notifier import TelegramNotifier
from core.auto_recorder import AutoRecorder
from core.signoff_manager import SignoffManager, SignoffState
from utils.config_manager import ConfigManager, DEFAULT_CONFIG
from utils.logger import AppLogger


class MainWindow(QMainWindow):
    """KBS 16채널 모니터링 메인 윈도우"""

    _telegram_test_done = Signal(bool, str)   # 백그라운드 테스트 → 메인 스레드 결과 전달

    def __init__(self):
        super().__init__()
        self.setWindowTitle("KBS Peacock v1.6.14")
        self.setMinimumSize(1280, 720)
        self.resize(1600, 900)

        # 핵심 컴포넌트 초기화
        self._config_manager = ConfigManager()
        self._config = self._config_manager.load()
        self._roi_manager = ROIManager()
        self._roi_manager.from_dict(self._config.get("rois", {}))
        self._detector = Detector()
        self._apply_detection_config(self._config.get("detection", {}))
        # 성능 설정 (감지 항목별 활성화 플래그 초기값)
        self._audio_detect_enabled = True
        self._embedded_detect_enabled = True
        self._detection_enabled = True   # 감지 On/Off 전체 상태 플래그
        self._apply_performance_config(self._config.get("performance", {}))
        self._alarm = AlarmSystem(parent=self)
        self._apply_alarm_config(self._config.get("alarm", {}))
        self._telegram = TelegramNotifier()
        self._apply_telegram_config(self._config.get("telegram", {}))
        self._telegram.start()
        self._recorder = AutoRecorder()
        self._apply_recording_config(self._config.get("recording", {}))
        self._recorder.start()
        self._logger = AppLogger()
        self._alarm.set_logger(self._logger)  # 로그 위젯에 알림음 재생 상태 출력

        # 정파준비모드 관리자
        self._signoff_manager = SignoffManager(parent=self)
        self._apply_signoff_config(self._config.get("signoff", {}))

        # 설정 다이얼로그 (비모달 싱글턴)
        self._settings_dialog: Optional[SettingsDialog] = None

        # 반화면 ROI 편집기 오버레이
        self._roi_overlay: Optional[ROIEditorCanvas] = None
        self._roi_overlay_type: str = ""

        # 임베디드 오디오 알림 로그 중복 방지
        self._embedded_log_sent = False
        self._last_silence_seconds = 0.0  # 마지막 무음 지속 시간 (복구 시 참조)

        # 비디오/오디오 알림 로그 중복 방지 (label 기반)
        self._black_logged: set = set()
        self._still_logged: set = set()
        self._audio_level_logged: set = set()

        # SIGNOFF 억제 첫 1회 로그 중복 방지
        self._signoff_suppressed_logged: set = set()

        # 감지 주기 카운터 (silent failure 감지 / 주기적 정상 작동 로그용)
        # 타이머 200ms 기준: 1500회 ≈ 5분
        self._detection_count: int = 0

        # DIAG 섹션별 에러 타입 추적 (로그 폭풍 방지)
        self._diag_last_errors: dict = {}

        # DIAG SYSTEM-HB용 psutil Process 객체 (매 사이클 재생성 금지)
        try:
            import psutil as _psutil_init
            self._diag_proc = _psutil_init.Process(os.getpid())
        except Exception:
            self._diag_proc = None

        # Health Check: 감지 루프 staleness 추적
        self._last_detection_time: float = time.time()
        self._health_alarm_logged: bool = False

        # 현재 연결 중인 캡처 포트 (connected 시점에 고정 — 포트 변경 타이밍 혼동 방지)
        self._active_capture_port: int = self._config.get("port", 0)

        # UI 구성
        self._setup_ui()
        self._connect_signals()
        self._start_threads()

        # TopBar 볼륨/뮤트 초기값 동기화
        init_alarm = self._config.get("alarm", {})
        init_vol = init_alarm.get("volume", 80)
        self._top_bar.set_volume_display(init_vol)
        self._top_bar.set_mute_state(init_alarm.get("sound_enabled", True))

        # TopBar 버튼 상태 복원 (감지 On/Off, 감지영역)
        ui_state = self._config.get("ui_state", {})
        detection_enabled = ui_state.get("detection_enabled", True)
        roi_visible = ui_state.get("roi_visible", True)
        self._detection_enabled = detection_enabled
        self._top_bar.set_detection_state(detection_enabled)
        self._top_bar.set_roi_visible_state(roi_visible)
        if not detection_enabled:
            self._detect_timer.stop()
        if not roi_visible:
            self._video_widget.set_show_rois(False)
        self._restore_fullscreen = ui_state.get("fullscreen", False)

        # 정파 버튼 활성화 상태 복원 (자동 정파 비활성 시 버튼 비활성화)
        auto_prep = self._config.get("signoff", {}).get("auto_preparation", True)
        self._top_bar.set_signoff_buttons_enabled(auto_prep)

        # 프로그램 시작 직후 SignoffManager 초기 상태 전환에서는 소리 억제
        self._startup_complete = False
        QTimer.singleShot(3000, lambda: setattr(self, '_startup_complete', True))

        # 예약 재시작: 마지막으로 재시작을 실행한 날짜(YYYY-MM-DD) 기록 (같은 날 재트리거 방지)
        # --restarted HH:MM 인자가 있으면 오늘 날짜를 기록하여 당일 반복 방지
        self._restart_done_date: str = ""
        if "--restarted" in sys.argv:
            idx = sys.argv.index("--restarted")
            if idx + 1 < len(sys.argv):
                self._restart_done_date = datetime.date.today().isoformat()

        self._logger.info("SYSTEM - 프로그램 시작")

    # ── UI 구성 ────────────────────────────────────────

    def _setup_ui(self):
        """3분할 레이아웃 구성"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._top_bar = TopBar()
        main_layout.addWidget(self._top_bar)

        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setObjectName("mainSplitter")
        main_layout.addWidget(self._splitter, stretch=1)

        self._video_widget = VideoWidget()
        self._video_widget.setObjectName("videoArea")
        self._splitter.addWidget(self._video_widget)

        self._log_widget = LogWidget()
        self._log_widget.setObjectName("logArea")
        self._splitter.addWidget(self._log_widget)

        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([1200, 400])

    # ── 신호 연결 ──────────────────────────────────────

    def _connect_signals(self):
        self._top_bar.settings_requested.connect(self._open_settings)
        self._top_bar.roi_visibility_changed.connect(self._video_widget.set_show_rois)
        self._top_bar.detection_toggled.connect(self._on_detection_toggled)
        self._top_bar.sound_toggled.connect(self._on_sound_toggled)
        self._top_bar.volume_changed.connect(self._on_volume_changed)
        self._top_bar.alarm_acknowledged.connect(self._on_alarm_acknowledged)
        self._top_bar.dark_mode_toggled.connect(self._on_dark_mode_toggled)
        self._top_bar.fullscreen_toggled.connect(self._toggle_fullscreen)
        self._top_bar.signoff_manual_release.connect(self._on_signoff_button_clicked)

        self._signoff_manager.state_changed.connect(self._on_signoff_state_changed)
        self._signoff_manager.event_occurred.connect(self._on_signoff_event)

        self._alarm.visual_blink.connect(self._video_widget.set_blink_state)
        self._alarm.visual_blink.connect(self._top_bar.set_alarm_blink_state)

        self._logger.log_signal.connect(self._on_log_message)

        # 텔레그램 로거 주입 (전송 성공은 파일에만, 오류는 UI에도 표시)
        self._telegram.set_logger(self._logger.file_only, self._logger.error)
        self._telegram_test_done.connect(self._on_telegram_test_done)

    # ── 스레드 시작 ────────────────────────────────────

    def _start_threads(self):
        port = self._config.get("port", 0)

        self._capture_thread = VideoCaptureThread(port=port)
        self._capture_thread.frame_ready.connect(self._on_frame_ready)
        self._capture_thread.connected.connect(self._on_capture_connected)
        self._capture_thread.disconnected.connect(self._on_capture_disconnected)
        self._capture_thread.status_changed.connect(
            lambda msg: self._logger.info(f"SYSTEM - {msg}")
        )
        self._capture_thread.start()

        self._audio_thread = AudioMonitorThread()
        self._audio_thread.level_updated.connect(self._top_bar.update_audio_levels)
        self._audio_thread.level_updated.connect(self._on_audio_level_for_silence)
        self._audio_thread.silence_detected.connect(self._on_embedded_silence)
        self._audio_thread.status_changed.connect(
            lambda msg: self._logger.info(f"AUDIO - {msg}")
        )
        # 녹화용 raw 오디오 → AutoRecorder (DirectConnection: 이벤트 루프 우회, 스레드 안전)
        self._audio_thread.audio_chunk.connect(
            lambda tup: self._recorder.push_audio(tup[0], tup[1]),
            Qt.DirectConnection,
        )
        # 초기 볼륨을 패스스루 출력에도 적용
        init_vol = self._config.get("alarm", {}).get("volume", 80) / 100.0
        self._audio_thread.set_volume(init_vol)
        self._audio_thread.start()

        self._detect_timer = QTimer(self)
        interval = self._config.get("performance", {}).get("detection_interval", 200)
        self._detect_timer.setInterval(interval)
        self._detect_timer.timeout.connect(self._run_detection)
        self._detect_timer.start()

        self._summary_timer = QTimer(self)
        self._summary_timer.setInterval(1000)
        self._summary_timer.timeout.connect(self._update_summary)
        self._summary_timer.start()

        self._restart_timer = QTimer(self)
        self._restart_timer.setInterval(10_000)
        self._restart_timer.timeout.connect(self._check_scheduled_restart)
        self._restart_timer.start()

        self._latest_frame = None

    # ── 캡처 스레드 슬롯 ────────────────────────────────

    def _on_capture_connected(self):
        """캡처 스레드 연결 성공 — 연결 시점의 포트 번호를 고정 기록"""
        self._active_capture_port = self._config.get("port", 0)

    def _on_capture_disconnected(self):
        """캡처 스레드 연결 끊김 — 연결 당시 포트 번호로 오류 로그"""
        self._logger.error(f"SYSTEM - 포트 {self._active_capture_port} 연결 실패")

    # ── 프레임/감지 ────────────────────────────────────

    def _on_frame_ready(self, frame):
        self._latest_frame = frame.copy()  # 캡처 스레드 버퍼 공유 방지
        if self._roi_overlay is None:
            self._video_widget.update_frame(frame)
        self._recorder.push_frame(frame)

    def _run_detection(self):
        if self._latest_frame is None:
            return
        if self._roi_overlay is not None:
            return  # 편집 중 감지 중단

        # 주기적 정상 작동 로그 (200ms × 150 ≈ 30초) — 파일 로그 전용 (UI 미출력)
        self._detection_count += 1
        if self._detection_count % 150 == 0:
            _now_hb = time.time()

            # ── SYSTEM-HB ──────────────────────────────────────────────────
            try:
                total_sec = self._detection_count // 5
                days, rem = divmod(total_sec, 86400)
                hours, mins_rem = divmod(rem, 3600)
                mins, secs = divmod(mins_rem, 60)
                if days > 0:
                    elapsed_str = f"{days}일 {hours}시간 {mins}분"
                elif hours > 0:
                    elapsed_str = f"{hours}시간 {mins}분"
                elif mins > 0:
                    elapsed_str = f"{mins}분 {secs}초"
                else:
                    elapsed_str = f"{secs}초"
                _os_threads = self._diag_proc.num_threads() if self._diag_proc is not None else -1
                _log.info(
                    "SYSTEM-HB [%s 경과] detect=%s summary=%s restart=%s threads=py:%d/os:%d",
                    elapsed_str,
                    "ON" if self._detect_timer.isActive() else "OFF",
                    "ON" if self._summary_timer.isActive() else "OFF",
                    "ON" if self._restart_timer.isActive() else "OFF",
                    threading.active_count(),
                    _os_threads,
                )
            except Exception as _e:
                _etype = type(_e).__name__
                if _etype != self._diag_last_errors.get("SYSTEM-HB"):
                    self._diag_last_errors["SYSTEM-HB"] = _etype
                    try:
                        _log.error("DIAG-SYSTEM-HB 오류 (감지 계속): %s\n%s",
                                   _e, traceback.format_exc())
                    except Exception as _log_e:
                        try:
                            print(f"[FATAL] DIAG-SYSTEM-HB 로깅 실패: {_e} / {_log_e}",
                                  file=sys.stderr, flush=True)
                        except Exception:
                            pass
                else:
                    _log.error("DIAG-SYSTEM-HB 오류 반복 (감지 계속): %s", _e)

            # ── DIAG-V ──────────────────────────────────────────────────────────────
            try:
                for lbl, raw in self._detector._last_raw.items():
                    still_state = self._detector._still_states.get(lbl)
                    dark_r = raw.get("dark_ratio", -1.0)
                    changed_r = raw.get("changed_ratio", -1.0)
                    still_timer = still_state.alert_duration if still_state else 0.0
                    if still_state and still_state._last_reset_time:
                        reset_ago_str = f"직전리셋={_now_hb - still_state._last_reset_time:.1f}s전"
                    else:
                        reset_ago_str = "리셋없음"
                    changed_str = (
                        f" changed={changed_r:.1f}%[블록기준{self._detector.still_block_threshold}%]"
                        if changed_r >= 0 else ""
                    )
                    # resolve 횟수 + alert_start_time 존재 여부 (진단 강화)
                    resolve_cnt = still_state._resolve_count if still_state else 0
                    has_start = still_state.alert_start_time is not None if still_state else False
                    alerting_str = "경보중" if (still_state and still_state.is_alerting) else "정상"
                    _log.info(
                        "DIAG - %s: black=%.1f%%[기준%.0f%%] still_timer=%.1fs[기준%.0fs]%s %s"
                        " [%s/resolve=%d/start=%s]",
                        lbl, dark_r, self._detector.black_dark_ratio,
                        still_timer, self._detector.still_duration,
                        changed_str, reset_ago_str,
                        alerting_str, resolve_cnt, "Y" if has_start else "N",
                    )
            except Exception as _e:
                _etype = type(_e).__name__
                if _etype != self._diag_last_errors.get("DIAG-V"):
                    self._diag_last_errors["DIAG-V"] = _etype
                    try:
                        _log.error("DIAG-V 오류 (감지 계속): %s\n%s",
                                   _e, traceback.format_exc())
                    except Exception as _log_e:
                        try:
                            print(f"[FATAL] DIAG-V 로까 실패: {_e} / {_log_e}",
                                  file=sys.stderr, flush=True)
                        except Exception:
                            pass
                else:
                    _log.error("DIAG-V 오류 반복 (감지 계속): %s", _e)

            # ── DIAG-ALARM ─────────────────────────────────────────────────────────
            try:
                active_alarms = self._alarm._active_alarms
                if active_alarms:
                    alarm_parts = []
                    for key in sorted(active_alarms):
                        label = key.split("_", 1)[1] if "_" in key else key
                        suppressed = (
                            self._signoff_manager.is_signoff_label(label)
                            or self._signoff_manager.is_prep_label(label)
                        )
                        alarm_parts.append(f"{key}{'(억제중)' if suppressed else ''}")
                    _log.info("DIAG-ALARM - 활성: [%s]", ", ".join(alarm_parts))
            except Exception as _e:
                _etype = type(_e).__name__
                if _etype != self._diag_last_errors.get("DIAG-ALARM"):
                    self._diag_last_errors["DIAG-ALARM"] = _etype
                    try:
                        _log.error("DIAG-ALARM 오류 (감지 계속): %s\n%s",
                                   _e, traceback.format_exc())
                    except Exception as _log_e:
                        try:
                            print(f"[FATAL] DIAG-ALARM 로까 실패: {_e} / {_log_e}",
                                  file=sys.stderr, flush=True)
                        except Exception:
                            pass
                else:
                    _log.error("DIAG-ALARM 오류 반복 (감지 계속): %s", _e)

            # ── DIAG-SIGNOFF ──────────────────────────────────────────────────────────
            try:
                signoff_parts = []
                video_name_map_hb = {r.label: r.media_name for r in self._roi_manager.video_rois}
                for gid, group in self._signoff_manager.get_groups().items():
                    state = self._signoff_manager.get_state(gid)
                    enter_lbl = group.enter_roi.get("video_label", "-")
                    enter_media = video_name_map_hb.get(enter_lbl, "")
                    enter_str = f"{enter_lbl}({enter_media})" if enter_media else enter_lbl
                    sup_labels = ",".join(group.suppressed_labels) if group.suppressed_labels else "-"
                    dbg = self._signoff_manager.get_debug_flags(gid)
                    flags = f"exit_rel={'T' if dbg['exit_released'] else 'F'},manual={'T' if dbg['manual'] else 'F'}"
                    signoff_parts.append(
                        f"그룹{gid}=[{group.name}/{state.value}/진입:{enter_str}/억제:{sup_labels}/{flags}]"
                    )
                _log.info("DIAG-SIGNOFF - %s", " ".join(signoff_parts) if signoff_parts else "그룹없음")
            except Exception as _e:
                _etype = type(_e).__name__
                if _etype != self._diag_last_errors.get("DIAG-SIGNOFF"):
                    self._diag_last_errors["DIAG-SIGNOFF"] = _etype
                    try:
                        _log.error("DIAG-SIGNOFF 오류 (감지 계속): %s\n%s",
                                   _e, traceback.format_exc())
                    except Exception as _log_e:
                        try:
                            print(f"[FATAL] DIAG-SIGNOFF 로까 실패: {_e} / {_log_e}",
                                  file=sys.stderr, flush=True)
                        except Exception:
                            pass
                else:
                    _log.error("DIAG-SIGNOFF 오류 반복 (감지 계속): %s", _e)

            # ── DIAG-AUDIO ──────────────────────────────────────────────────────────────
            try:
                audio_diag_parts = []
                if self._audio_detect_enabled:
                    for lbl, a_state in self._detector._audio_level_states.items():
                        buf = self._detector._audio_ratio_buffer.get(lbl)
                        avg_r = (sum(buf) / len(buf)) if buf else -1.0
                        a_alert_str = "알람" if a_state.is_alerting else "정상"
                        audio_diag_parts.append(
                            f"{lbl}:ratio={avg_r:.1f}%[기준{self._detector.audio_pixel_ratio:.0f}%]"
                            f" timer={a_state.alert_duration:.1f}s[기준{self._detector.audio_level_duration:.0f}s]"
                            f" {a_alert_str}"
                        )
                    if not self._detector._audio_level_states:
                        audio_diag_parts.append("오디오ROI없음")
                else:
                    audio_diag_parts.append("오디오레벨미터감지 비활성")
                if self._embedded_detect_enabled:
                    emb_alert_str = "알람중" if self._detector.embedded_alerting else "정상"
                    silence_elapsed = (
                        (time.time() - self._detector._embedded_alert_start)
                        if self._detector._embedded_alert_start is not None
                        else 0.0
                    )
                    audio_diag_parts.append(
                        f"임베디드:{emb_alert_str}"
                        f"[무음{silence_elapsed:.1f}s/기준{self._detector.embedded_silence_duration:.0f}s]"
                    )
                else:
                    audio_diag_parts.append("임베디드감지 비활성")
                _log.info("DIAG-AUDIO - %s", " | ".join(audio_diag_parts))
            except Exception as _e:
                _etype = type(_e).__name__
                if _etype != self._diag_last_errors.get("DIAG-AUDIO"):
                    self._diag_last_errors["DIAG-AUDIO"] = _etype
                    try:
                        _log.error("DIAG-AUDIO 오류 (감지 계속): %s\n%s",
                                   _e, traceback.format_exc())
                    except Exception as _log_e:
                        try:
                            print(f"[FATAL] DIAG-AUDIO 로까 실패: {_e} / {_log_e}",
                                  file=sys.stderr, flush=True)
                        except Exception:
                            pass
                else:
                    _log.error("DIAG-AUDIO 오류 반복 (감지 계속): %s", _e)

            # ── DIAG-TELEGRAM ──────────────────────────────────────────────────────────────
            try:
                tg_enabled = self._telegram._enabled
                tg_worker_alive = self._telegram._worker_thread.is_alive()
                tg_queue_size = self._telegram._queue.qsize()
                if tg_enabled and (not tg_worker_alive or tg_queue_size >= 1):
                    _log.warning(
                        "DIAG-TELEGRAM - worker=%s queue=%d",
                        "alive" if tg_worker_alive else "DEAD",
                        tg_queue_size,
                    )
            except Exception as _e:
                _etype = type(_e).__name__
                if _etype != self._diag_last_errors.get("DIAG-TELEGRAM"):
                    self._diag_last_errors["DIAG-TELEGRAM"] = _etype
                    try:
                        _log.error("DIAG-TELEGRAM 오류 (감지 계속): %s\n%s",
                                   _e, traceback.format_exc())
                    except Exception as _log_e:
                        try:
                            print(f"[FATAL] DIAG-TELEGRAM 로까 실패: {_e} / {_log_e}",
                                  file=sys.stderr, flush=True)
                        except Exception:
                            pass
                else:
                    _log.error("DIAG-TELEGRAM 오류 반복 (감지 계속): %s", _e)
        self._last_detection_time = time.time()

        try:
            video_rois = self._roi_manager.video_rois
            audio_rois = self._roi_manager.audio_rois

            # ── 오디오 ROI 사전 계산 (SignoffManager + 알림 처리 공유) ──
            audio_results = {}
            if audio_rois and self._audio_detect_enabled:
                audio_results = self._detector.detect_audio_roi(self._latest_frame, audio_rois)

            # ── 비디오 ROI 블랙/스틸 감지 ──
            # SignoffManager enter_roi label은 still_detection_enabled와 무관하게 스틸 계산 필요.
            # force_still_labels로 전달하면 detector가 해당 label만 강제 계산한다.
            signoff_enter_labels: set = {
                group.enter_roi.get("video_label", "")
                for group in self._signoff_manager.get_groups().values()
                if group.enter_roi.get("video_label")
            }
            need_still_for_signoff = bool(video_rois and signoff_enter_labels)
            video_results = {}
            if video_rois and (self._detector.black_detection_enabled
                               or self._detector.still_detection_enabled
                               or need_still_for_signoff):
                video_results = self._detector.detect_frame(
                    self._latest_frame, video_rois,
                    force_still_labels=signoff_enter_labels if need_still_for_signoff else None,
                )

            # ── SignoffManager 업데이트 (스틸 감지 결과 전달) ──
            # still_detection_enabled=True : 전체 ROI 스틸 결과 전달
            # still_detection_enabled=False: SignoffManager enter_roi label만 전달
            #   (force_still_labels로 강제 계산됨 → 정파 진입/해제 감지 정상 동작)
            if self._detector.still_detection_enabled:
                still_results = {
                    label: state.get("still", False)
                    for label, state in video_results.items()
                }
            elif signoff_enter_labels:
                still_results = {
                    label: state.get("still", False)
                    for label, state in video_results.items()
                    if label in signoff_enter_labels
                }
            else:
                still_results = {}

            self._signoff_manager.update_detection(still_results=still_results)

            # ── 비디오 ROI 알림 처리 ──
            if video_results:
                # label → media_name 매핑 캐시
                video_name_map = {r.label: r.media_name for r in video_rois}
                results = video_results

                for label, state in results.items():
                    # SIGNOFF 중인 그룹 소속 → 알림/로그 억제
                    if self._signoff_manager.is_signoff_label(label):
                        if label not in self._signoff_suppressed_logged:
                            _log.debug("SIGNOFF - %s 알림 억제 시작 (정파 중)", label)
                            self._signoff_suppressed_logged.add(label)
                        self._alarm.resolve("블랙", label)
                        self._alarm.resolve("스틸", label)
                        self._black_logged.discard(label)
                        self._still_logged.discard(label)
                        self._video_widget.set_alert_state(label, False)
                        continue

                    black_alert    = state.get("black_alerting", False)
                    still_alert    = state.get("still_alerting", False)
                    black_resolved = state.get("black_resolved", False)
                    still_resolved = state.get("still_resolved", False)
                    media = video_name_map.get(label, "")
                    name = media or label                          # 텔레그램/알람용
                    log_prefix = f"{label}. {media}" if media else label  # 로그용

                    # PREPARATION 상태: 스틸 알림만 억제 (블랙 알림은 계속)
                    is_in_prep = self._signoff_manager.is_prep_label(label)
                    if is_in_prep:
                        self._alarm.resolve("스틸", label)
                        self._still_logged.discard(label)

                    # ── 블랙 ──
                    if black_alert:
                        if label not in self._black_logged:
                            self._logger.error(f"{log_prefix} - 블랙 감지")
                            tg = self._config.get("telegram", {})
                            if tg.get("notify_black", True):
                                self._telegram.notify("블랙", label, name, self._latest_frame)
                            self._recorder.trigger("블랙", label, media)
                        self._alarm.trigger("블랙", label, self._detector.black_alarm_duration)
                        self._black_logged.add(label)
                    else:
                        if black_resolved and label in self._black_logged:
                            last_dur = state.get("black_last_duration", 0)
                            self._logger.error(f"{log_prefix} - 블랙 {last_dur:.0f}초")
                            self._logger.info(f"{log_prefix} - 블랙 정상 복구")
                            tg = self._config.get("telegram", {})
                            if tg.get("notify_black", True):
                                self._telegram.notify("블랙", label, name, self._latest_frame, is_recovery=True)
                        self._alarm.resolve("블랙", label)
                        self._black_logged.discard(label)

                    # ── 스틸 (PREPARATION 상태에서는 억제) ──
                    if not is_in_prep:
                        if still_alert:
                            if label not in self._still_logged:
                                self._logger.still_error(f"{log_prefix} - 스틸 감지")
                                tg = self._config.get("telegram", {})
                                if tg.get("notify_still", True):
                                    self._telegram.notify("스틸", label, name, self._latest_frame)
                                self._recorder.trigger("스틸", label, media)
                            self._alarm.trigger("스틸", label, self._detector.still_alarm_duration)
                            self._still_logged.add(label)
                        else:
                            if still_resolved and label in self._still_logged:
                                last_dur = state.get("still_last_duration", 0)
                                self._logger.still_error(f"{log_prefix} - 스틸 {last_dur:.0f}초")
                                self._logger.info(f"{log_prefix} - 스틸 정상 복구")
                                tg = self._config.get("telegram", {})
                                if tg.get("notify_still", True):
                                    self._telegram.notify("스틸", label, name, self._latest_frame, is_recovery=True)
                            self._alarm.resolve("스틸", label)
                            self._still_logged.discard(label)

                    self._video_widget.set_alert_state(label, black_alert or (still_alert and not is_in_prep))

            # ── 오디오 ROI 레벨미터 처리 (사전 계산된 audio_results 재사용) ──
            if audio_rois and self._audio_detect_enabled and audio_results:
                # label → media_name 매핑 캐시
                audio_name_map = {r.label: r.media_name for r in audio_rois}

                for label, state in audio_results.items():
                    # SIGNOFF 중인 그룹 소속 → 알림/로그 억제
                    if self._signoff_manager.is_signoff_label(label):
                        if label not in self._signoff_suppressed_logged:
                            _log.debug("SIGNOFF - %s 오디오 알림 억제 시작 (정파 중)", label)
                            self._signoff_suppressed_logged.add(label)
                        self._alarm.resolve("오디오", label)
                        self._audio_level_logged.discard(label)
                        self._video_widget.set_alert_state(label, False)
                        continue

                    alerting = state.get("alerting", False)
                    resolved = state.get("resolved", False)
                    media = audio_name_map.get(label, "")
                    name = media or label                              # 텔레그램/알람용
                    log_prefix = f"{label}. {media}" if media else label  # 로그용

                    if alerting:
                        if label not in self._audio_level_logged:
                            self._logger.audio_error(f"{log_prefix} - 무음 감지")
                            tg = self._config.get("telegram", {})
                            if tg.get("notify_audio_level", True):
                                self._telegram.notify("오디오", label, name, self._latest_frame)
                            self._recorder.trigger("오디오", label, media)
                        self._alarm.trigger(
                            "오디오", label, self._detector.audio_level_alarm_duration
                        )
                        self._audio_level_logged.add(label)
                    else:
                        if resolved and label in self._audio_level_logged:
                            last_dur = state.get("last_duration", 0)
                            self._logger.audio_error(
                                f"{log_prefix} - 무음 {last_dur:.0f}초"
                            )
                            self._logger.info(f"{log_prefix} - 무음 정상 복구")
                            tg = self._config.get("telegram", {})
                            if tg.get("notify_audio_level", True):
                                self._telegram.notify("오디오", label, name, self._latest_frame, is_recovery=True)
                        self._alarm.resolve("오디오", label)
                        self._audio_level_logged.discard(label)

                    self._video_widget.set_alert_state(label, alerting)

        except Exception as e:
            self._logger.error(f"SYSTEM - 감지 루프 오류 (silent fail 방지): {e}")

    def _update_summary(self):
        try:
            v_count = len(self._roi_manager.video_rois)
            a_count = len(self._roi_manager.audio_rois)
            self._top_bar.update_summary(
                v_count, a_count,
                self._embedded_detect_enabled,
                self._detector.embedded_alerting,
            )
            # 감지영역 정보를 비디오 위젯에도 동기화
            self._video_widget.set_rois(
                self._roi_manager.video_rois,
                self._roi_manager.audio_rois,
            )
            # 정파 상태 패널 갱신 (1초마다)
            for gid, group in self._signoff_manager.get_groups().items():
                state = self._signoff_manager.get_state(gid)
                if state == SignoffState.SIGNOFF:
                    secs = self._signoff_manager.get_end_remaining_seconds(gid)
                else:
                    secs = self._signoff_manager.get_elapsed_seconds(gid)
                self._top_bar.update_signoff_state(
                    gid, state.value, group.name, secs,
                    clock_enabled=self._signoff_manager.is_group_enabled(gid),
                )
        except Exception as e:
            _log.error("_update_summary 오류 (silent fail 방지): %s", e)

        # Health Check: 감지 루프 staleness 검사 (1초마다)
        try:
            now = time.time()
            detect_stale = (now - self._last_detection_time) > 5.0

            # 감지 의도적 중단 상태에서는 오탐 방지
            if not self._detection_enabled or self._roi_overlay is not None:
                detect_stale = False

            self._top_bar.update_health(detect_stale)

            # 상태 변경 시에만 로그 (반복 방지)
            if detect_stale and not self._health_alarm_logged:
                elapsed_d = now - self._last_detection_time
                self._logger.error(
                    f"SYSTEM - 감지 루프 중단 감지 (health check) | "
                    f"마지막 감지: {elapsed_d:.1f}초 전 | "
                    f"감지횟수: {self._detection_count}"
                )
                self._health_alarm_logged = True
            elif not detect_stale and self._health_alarm_logged:
                self._logger.info("SYSTEM - 감지 루프 정상 복구")
                self._health_alarm_logged = False
        except Exception as e:
            _log.error("health check 오류 (silent fail 방지): %s", e)

    # ── 모니터링 제어 ──────────────────────────────────

    def _on_alarm_acknowledged(self):
        """알림확인 버튼 클릭 — 소리 및 깜빡임 해제 (감지기 상태·로그 집합 유지).
        acknowledge_all()로 현재 알람을 confirmed 처리 → 이상이 지속돼도 재알림 없음.
        이상이 실제 해제(resolve)되면 acknowledged 상태도 제거되어 다음 이상 시 정상 알림.
        """
        self._alarm.acknowledge_all()
        self._logger.info("SYSTEM - 알림확인")

    # ── 설정 다이얼로그 ────────────────────────────────

    def _open_settings(self):
        try:
            if self._settings_dialog is None:
                self._settings_dialog = SettingsDialog(
                    self._config, self._roi_manager, parent=self
                )
                self._settings_dialog.port_changed.connect(self._on_port_changed)
                self._settings_dialog.video_file_changed.connect(self._on_video_file_changed)
                self._settings_dialog.halfscreen_edit_requested.connect(self._start_halfscreen_edit)
                self._settings_dialog.halfscreen_edit_finished.connect(self._finish_halfscreen_edit)
                self._settings_dialog.detection_params_changed.connect(self._apply_detection_params)
                self._settings_dialog.performance_params_changed.connect(self._apply_performance_params)

                self._settings_dialog.roi_selection_changed.connect(self._on_settings_roi_selected)
                self._settings_dialog.roi_list_changed.connect(self._on_settings_roi_list_changed)
                self._settings_dialog.alarm_settings_changed.connect(self._on_alarm_settings_changed)
                self._settings_dialog.test_sound_requested.connect(self._alarm.play_test_sound)
                self._settings_dialog.telegram_settings_changed.connect(self._on_telegram_settings_changed)
                self._settings_dialog.telegram_test_requested.connect(self._on_telegram_test)
                self._settings_dialog.recording_settings_changed.connect(self._on_recording_settings_changed)
                self._settings_dialog.save_config_requested.connect(self._on_save_config)
                self._settings_dialog.load_config_requested.connect(self._on_load_config)
                self._settings_dialog.reset_config_requested.connect(self._on_reset_config)
                self._settings_dialog.signoff_settings_changed.connect(self._on_signoff_settings_changed)
                self._settings_dialog.system_settings_changed.connect(self._on_system_settings_changed)
                self._settings_dialog.finished.connect(self._on_settings_closed)
            else:
                self._settings_dialog.refresh_roi_tables()

            self._settings_dialog.showNormal()
            self._settings_dialog.resize(780, 660)
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
        except Exception as e:
            import traceback
            self._logger.error(f"[설정] 창 열기 실패: {e}\n{traceback.format_exc()}")
            self._settings_dialog = None

    def _on_settings_closed(self):
        if self._settings_dialog:
            self._config = self._settings_dialog.get_config()
            self._settings_dialog.deleteLater()
        self._settings_dialog = None

    def _on_port_changed(self, port: int):
        self._config["port"] = port
        self._capture_thread.set_port(port)
        self._video_widget.clear_signal()
        self._logger.info(f"SYSTEM - 포트 {port}로 변경")

    def _on_video_file_changed(self, path: str):
        """영상 파일 소스 변경"""
        if path:
            self._capture_thread.set_video_file(path)
            self._video_widget.clear_signal()
            self._logger.info(f"SYSTEM - 파일 소스로 변경: {os.path.basename(path)}")
        else:
            port = self._config.get("port", 0)
            self._capture_thread.set_port(port)
            self._video_widget.clear_signal()
            self._logger.info(f"SYSTEM - 파일 소스 해제, 포트 {port}로 복귀")

    def _apply_detection_params(self, params: dict):
        """감지 파라미터 Detector에 즉시 반영 후 상태 초기화.
        스틸/톤 기준 시간이 변경될 수 있으므로 SignoffManager도 재적용.
        """
        self._apply_detection_config(params)
        self._detector.reset_all()
        # 감도설정 변경 시 정파 기준 시간도 갱신
        self._apply_signoff_config(self._config.get("signoff", {}))

    def _apply_performance_params(self, params: dict):
        """성능 파라미터 즉시 반영 (타이머 주기, 스케일, 감지 활성화)"""
        self._apply_performance_config(params)
        # 성능 설정 config에도 저장
        self._config["performance"] = dict(params)

    def _apply_performance_config(self, perf: dict):
        """성능 설정을 Detector 및 타이머에 반영"""
        self._audio_detect_enabled = perf.get("audio_detection_enabled", True)
        self._embedded_detect_enabled = perf.get("embedded_detection_enabled", True)
        self._detector.scale_factor = perf.get("scale_factor", 1.0)
        self._detector.black_detection_enabled = perf.get("black_detection_enabled", True)
        self._detector.still_detection_enabled = perf.get("still_detection_enabled", True)
        # 타이머가 이미 생성된 경우에만 주기 변경
        if hasattr(self, "_detect_timer"):
            self._detect_timer.setInterval(perf.get("detection_interval", 200))

    def _apply_detection_config(self, det: dict):
        """config dict에서 감지 파라미터 적용"""
        self._detector.black_threshold = det.get("black_threshold", 5)
        self._detector.black_dark_ratio = det.get("black_dark_ratio", 98.0)
        self._detector.black_duration = det.get("black_duration", 20)
        self._detector.black_alarm_duration = det.get("black_alarm_duration", 60)
        self._detector.black_motion_suppress_ratio = det.get("black_motion_suppress_ratio", 0.2)
        self._detector.still_threshold = det.get("still_threshold", 4)
        self._detector.still_block_threshold = det.get("still_block_threshold", 15.0)
        self._detector.still_duration = det.get("still_duration", 60.0)
        self._detector.still_alarm_duration = det.get("still_alarm_duration", 60)
        self._detector.still_reset_frames = int(det.get("still_reset_frames", 3))
        # 오디오 레벨미터 HSV
        self._detector.audio_hsv_h_min = det.get("audio_hsv_h_min", 40)
        self._detector.audio_hsv_h_max = det.get("audio_hsv_h_max", 95)
        self._detector.audio_hsv_s_min = det.get("audio_hsv_s_min", 80)
        self._detector.audio_hsv_s_max = det.get("audio_hsv_s_max", 255)
        self._detector.audio_hsv_v_min = det.get("audio_hsv_v_min", 60)
        self._detector.audio_hsv_v_max = det.get("audio_hsv_v_max", 255)
        self._detector.audio_pixel_ratio = det.get("audio_pixel_ratio", 5.0)
        self._detector.audio_level_duration = det.get("audio_level_duration", 20.0)
        self._detector.audio_level_alarm_duration = det.get("audio_level_alarm_duration", 60)
        self._detector.audio_level_recovery_seconds = det.get("audio_level_recovery_seconds", 2.0)
        # 임베디드 오디오
        self._detector.embedded_silence_threshold = det.get("embedded_silence_threshold", -50)
        self._detector.embedded_silence_duration = det.get("embedded_silence_duration", 20.0)
        self._detector.embedded_alarm_duration = det.get("embedded_alarm_duration", 60)
        # 정파용 오디오 톤 감지
        self._detector.audio_tone_std_threshold = det.get("audio_tone_std_threshold", 3.0)
        self._detector.audio_tone_duration      = det.get("audio_tone_duration", 60.0)
        self._detector.audio_tone_min_level     = det.get("audio_tone_min_level", 5.0)

    # ── 임베디드 오디오 감지 ───────────────────────────

    def _on_embedded_silence(self, silence_seconds: float):
        """AudioMonitorThread.silence_detected 수신 — 임베디드 오디오 무음 업데이트"""
        if not self._detection_enabled or not self._embedded_detect_enabled:
            return
        self._last_silence_seconds = silence_seconds
        alerting = self._detector.update_embedded_silence(silence_seconds)
        if alerting and not self._embedded_log_sent:
            self._embedded_log_sent = True
            self._logger.embedded_error("Embedded Audio - 무음감지")
            self._alarm.trigger("무음", "Embedded Audio", self._detector.embedded_alarm_duration)
            tg = self._config.get("telegram", {})
            if tg.get("notify_embedded", True):
                self._telegram.notify("무음", "Embedded", "Embedded Audio", self._latest_frame)
            self._recorder.trigger("무음", "Embedded", "Embedded Audio")

    def _on_audio_level_for_silence(self, l_db: float, r_db: float):
        """level_updated 수신 — 정상 오디오 수신 시 임베디드 감지 리셋"""
        avg_db = (l_db + r_db) / 2.0
        if not self._detection_enabled or not self._embedded_detect_enabled:
            return
        if avg_db > self._detector.embedded_silence_threshold:
            if self._detector.embedded_alerting or self._embedded_log_sent:
                was_sent = self._embedded_log_sent
                last_seconds = self._last_silence_seconds
                self._alarm.resolve("무음", "Embedded Audio")
                if was_sent:
                    self._embedded_log_sent = False
                    self._logger.embedded_error(f"Embedded Audio - 무음 {last_seconds:.0f}초")
                    self._logger.info("Embedded Audio - 정상 복구")
                    tg = self._config.get("telegram", {})
                    if tg.get("notify_embedded", True):
                        self._telegram.notify("무음", "Embedded", "Embedded Audio", self._latest_frame, is_recovery=True)
            # 알람 발생 여부와 무관하게 항상 무음 상태 리셋
            # (이전 무음 구간 시작 기록이 남아 다음 무음에서 오산되는 버그 방지)
            self._detector.reset_embedded_silence()
            self._last_silence_seconds = 0.0

    # ── ROI 편집 모드: 반화면 ─────────────────────────

    def _start_halfscreen_edit(self, roi_type: str):
        """반화면 편집 모드 진입: 설정창을 유지하면서 비디오 영역에 편집 오버레이"""
        # 이미 다른 타입 편집 중이면 먼저 종료
        if self._roi_overlay is not None:
            self._close_overlay()

        frame = self._latest_frame

        self._roi_overlay = ROIEditorCanvas(
            self._roi_manager, roi_type,
            parent=self._video_widget
        )
        self._roi_overlay.set_frame(frame)
        self._roi_overlay.load_rois()
        # ROI 변경 시 즉시 ROI 매니저에 반영 + 테이블 갱신
        self._roi_overlay.rois_changed.connect(self._on_roi_overlay_changed)
        self._roi_overlay.resize(self._video_widget.size())
        self._roi_overlay.show()
        self._roi_overlay.raise_()
        self._roi_overlay.setFocus()
        self._roi_overlay_type = roi_type

        # 비디오 업데이트 중단
        self._detect_timer.stop()

    def _on_roi_overlay_changed(self):
        """오버레이에서 ROI 변경 시 즉시 ROI 매니저에 반영 + 테이블 갱신"""
        if self._roi_overlay:
            self._roi_overlay.apply_rois()
            if self._settings_dialog:
                self._settings_dialog.refresh_roi_tables()
            self._sync_signoff_media_names()

    def _finish_halfscreen_edit(self):
        """반화면 편집 완료 (설정창 편집 버튼 재클릭 또는 설정창 닫기 시 호출)"""
        if self._roi_overlay is None:
            return

        self._close_overlay()

        # 설정창 편집 버튼 상태 초기화
        if self._settings_dialog:
            self._settings_dialog.reset_edit_button(self._roi_overlay_type)
            self._settings_dialog.refresh_roi_tables()

        # 감지 재시작
        self._detect_timer.start()

        self._update_summary()

    def _on_settings_roi_selected(self, roi_type: str, row: int):
        """설정창 테이블 행 선택 시 반화면 편집 오버레이의 선택 동기화"""
        if self._roi_overlay and self._roi_overlay_type == roi_type:
            self._roi_overlay._selected_idx = row
            self._roi_overlay.update()

    def _on_settings_roi_list_changed(self, roi_type: str):
        """설정창 버튼(추가/삭제/이동/초기화)으로 ROI 목록이 변경되면 반화면 캔버스 갱신"""
        if self._roi_overlay and self._roi_overlay_type == roi_type:
            self._roi_overlay.load_rois()
        # 비디오 위젯에도 즉시 반영 (감지영역 버튼 ON 상태일 때 실시간 표시)
        self._video_widget.set_rois(
            self._roi_manager.video_rois,
            self._roi_manager.audio_rois,
        )

    def _close_overlay(self):
        """오버레이 위젯 정리"""
        if self._roi_overlay:
            self._roi_overlay.hide()
            self._roi_overlay.deleteLater()
            self._roi_overlay = None

    # ── 알림 설정 ─────────────────────────────────────

    def _apply_alarm_config(self, alarm: dict):
        """알림 설정을 AlarmSystem에 적용"""
        sound_enabled = alarm.get("sound_enabled", True)
        self._alarm.set_sound_enabled(sound_enabled)
        if hasattr(self, '_top_bar'):
            self._top_bar.set_mute_state(sound_enabled)
        self._alarm.set_volume(alarm.get("volume", 80) / 100.0)
        for atype, path in alarm.get("sound_files", {}).items():
            self._alarm.set_sound_file(atype, path)

    def _on_alarm_settings_changed(self, params: dict):
        """SettingsDialog 알림 설정 변경 → AlarmSystem + TopBar 동기화"""
        self._apply_alarm_config(params)
        self._top_bar.set_volume_display(params.get("volume", 80))

    # ── 텔레그램 알림 ─────────────────────────────────

    def _apply_telegram_config(self, tg: dict):
        """텔레그램 설정을 TelegramNotifier에 반영"""
        self._telegram.configure(
            enabled=bool(tg.get("enabled", False)),
            bot_token=tg.get("bot_token", ""),
            chat_id=tg.get("chat_id", ""),
            send_image=bool(tg.get("send_image", True)),
            cooldown=float(tg.get("cooldown", 60)),
            notify_black=bool(tg.get("notify_black", True)),
            notify_still=bool(tg.get("notify_still", True)),
            notify_audio_level=bool(tg.get("notify_audio_level", True)),
            notify_embedded=bool(tg.get("notify_embedded", True)),
            notify_signoff=bool(tg.get("notify_signoff", True)),
        )

    def _on_telegram_settings_changed(self, params: dict):
        """SettingsDialog 텔레그램 설정 변경"""
        self._config["telegram"] = params
        self._apply_telegram_config(params)

    def _on_telegram_test(self, token: str, chat_id: str):
        """연결 테스트를 백그라운드 스레드에서 실행 (메인 스레드 블로킹 방지)"""
        # 결과를 스레드-안전 변수에 저장 → QTimer 폴링으로 메인 스레드에서 UI 업데이트
        # (daemon 스레드에서 Signal.emit()하면 PySide6 이벤트 루프 전달 미보장)
        self._tg_test_result: list = []          # [(ok, msg)] — 완료 시 append
        self._tg_test_start = time.time()

        def _run():
            try:
                ok, msg = self._telegram.test_connection(token, chat_id)
            except Exception as exc:
                ok, msg = False, f"예외: {type(exc).__name__}: {exc}"
            self._tg_test_result.append((ok, msg))

        _t = threading.Thread(target=_run, daemon=True, name="TelegramTestThread")
        _t.start()

        # 메인 스레드 QTimer로 결과 폴링 (500ms 간격, 최대 30초)
        if not hasattr(self, "_tg_test_timer"):
            self._tg_test_timer = QTimer(self)
            self._tg_test_timer.timeout.connect(self._poll_telegram_test)
        self._tg_test_timer.start(500)

    def _poll_telegram_test(self):
        """QTimer 콜백 — 메인 스레드에서 테스트 결과 확인"""
        if self._tg_test_result:
            ok, msg = self._tg_test_result[0]
            self._tg_test_timer.stop()
            self._on_telegram_test_done(ok, msg)
        elif time.time() - self._tg_test_start > 30.0:
            self._tg_test_timer.stop()
            self._on_telegram_test_done(
                False, "연결 시간 초과 (30초) — 네트워크/DNS 문제 확인"
            )

    def _on_telegram_test_done(self, ok: bool, msg: str):
        """테스트 결과 수신 — 메인 스레드에서 UI 업데이트"""
        if self._settings_dialog:
            self._settings_dialog.set_telegram_test_result(ok, msg)

    # ── 자동 녹화 ─────────────────────────────────────

    def _apply_recording_config(self, rec: dict):
        """녹화 설정을 AutoRecorder에 반영"""
        self._recorder.configure(
            enabled=bool(rec.get("enabled", False)),
            save_dir=rec.get("save_dir", "recordings"),
            pre_seconds=float(rec.get("pre_seconds", 5)),
            post_seconds=float(rec.get("post_seconds", 15)),
            max_keep_days=int(rec.get("max_keep_days", 7)),
            output_width=int(rec.get("output_width", 960)),
            output_height=int(rec.get("output_height", 540)),
            output_fps=int(rec.get("output_fps", 10)),
        )

    def _on_recording_settings_changed(self, params: dict):
        """SettingsDialog 녹화 설정 변경"""
        self._config["recording"] = params
        self._apply_recording_config(params)

    # ── 저장/불러오기 ─────────────────────────────────

    def _on_save_config(self, filepath: str):
        """현재 설정을 지정된 경로에 저장"""
        if self._settings_dialog:
            self._config = self._settings_dialog.get_config()
        self._config["rois"] = self._roi_manager.to_dict()
        success = self._config_manager.save_to_path(self._config, filepath)
        if success:
            self._logger.info(f"SYSTEM - 설정 저장 완료: {os.path.basename(filepath)}")
        else:
            self._logger.error(f"SYSTEM - 설정 저장 실패: {filepath}")

    def _on_load_config(self, filepath: str):
        """지정된 경로에서 설정 불러오기 후 전체 적용"""
        try:
            config = self._config_manager.load_from_path(filepath)
            self._config = config

            # ROI 적용
            self._roi_manager.from_dict(config.get("rois", {}))

            # 감지 파라미터 적용
            self._apply_detection_config(config.get("detection", {}))
            self._detector.reset_all()

            # 성능 파라미터 적용
            self._apply_performance_config(config.get("performance", {}))

            # 알림 설정 적용
            alarm_cfg = config.get("alarm", {})
            self._apply_alarm_config(alarm_cfg)
            self._top_bar.set_volume_display(alarm_cfg.get("volume", 80))

            # 텔레그램/녹화 설정 적용
            self._apply_telegram_config(config.get("telegram", {}))
            self._apply_recording_config(config.get("recording", {}))

            # 정파 설정 적용
            self._apply_signoff_config(config.get("signoff", {}))

            # 설정창 UI 갱신
            if self._settings_dialog:
                self._settings_dialog.reload_config(config)

            self._update_summary()
            self._logger.info(f"SYSTEM - 설정 불러오기 완료: {os.path.basename(filepath)}")
        except Exception as e:
            self._logger.error(f"SYSTEM - 설정 불러오기 실패: {e}")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "설정 불러오기 실패",
                f"설정 파일을 적용하는 중 오류가 발생했습니다.\n{e}\n\n기존 설정을 유지합니다.",
            )

    def _on_reset_config(self):
        """모든 설정을 기본값으로 초기화"""
        config = copy.deepcopy(DEFAULT_CONFIG)
        self._config = config

        # ROI 초기화
        self._roi_manager.replace_video_rois([])
        self._roi_manager.replace_audio_rois([])

        # 감지 파라미터 초기화
        self._apply_detection_config(config.get("detection", {}))
        self._detector.reset_all()

        # 성능 파라미터 초기화
        self._apply_performance_config(config.get("performance", {}))

        # 알람 초기화
        alarm_cfg = config.get("alarm", {})
        self._apply_alarm_config(alarm_cfg)
        self._top_bar.set_volume_display(alarm_cfg.get("volume", 80))

        # 텔레그램/녹화 초기화
        self._apply_telegram_config(config.get("telegram", {}))
        self._apply_recording_config(config.get("recording", {}))

        # 정파 설정 초기화
        self._apply_signoff_config(config.get("signoff", {}))

        # 설정창 UI 갱신
        if self._settings_dialog:
            self._settings_dialog.reload_config(config)

        self._update_summary()
        self._logger.info("SYSTEM - 설정 초기화 완료")

    # ── 정파준비모드 ──────────────────────────────────

    def _apply_signoff_config(self, signoff_cfg: dict):
        """signoff 설정을 SignoffManager에 반영.
        감도설정의 스틸/톤 기준 시간을 함께 전달한다.
        설정 적용 중 발생하는 상태 전환은 소리 없이 처리한다.
        """
        self._signoff_settings_applying = True
        try:
            det_cfg = self._config.get("detection", {})
            still_trigger_sec = float(det_cfg.get("still_duration", 60.0))
            self._signoff_manager.configure_from_dict(
                signoff_cfg, still_trigger_sec
            )
            # 자동 정파 준비 비활성화 시 상단 정파 버튼 비활성화
            auto_prep = signoff_cfg.get("auto_preparation", True)
            if hasattr(self, '_top_bar'):
                self._top_bar.set_signoff_buttons_enabled(auto_prep)
        finally:
            self._signoff_settings_applying = False
        self._sync_signoff_media_names()

    def _sync_signoff_media_names(self):
        """ROI 매체명 매핑을 SignoffManager에 동기화. ROI 변경 시에도 호출한다."""
        name_map = {r.label: r.media_name for r in self._roi_manager.video_rois}
        self._signoff_manager.update_media_names(name_map)

    def _on_signoff_settings_changed(self, params: dict):
        """SettingsDialog 정파 설정 변경 → 즉시 적용"""
        self._config["signoff"] = params
        self._apply_signoff_config(params)

    def _on_signoff_state_changed(self, group_id: int, state_str: str):
        """정파 상태 변경 시 TopBar 패널 즉시 갱신"""
        group = self._signoff_manager.get_groups().get(group_id)
        state = self._signoff_manager.get_state(group_id)
        if state == SignoffState.SIGNOFF:
            secs = self._signoff_manager.get_end_remaining_seconds(group_id)
        else:
            secs = self._signoff_manager.get_elapsed_seconds(group_id)
        self._top_bar.update_signoff_state(
            group_id, state_str,
            group.name if group else "",
            secs,
            clock_enabled=self._signoff_manager.is_group_enabled(group_id),
        )

    def _on_signoff_event(self, group_id: int, message: str):
        """정파 이벤트 발생 시 로그 기록 + 알림음 재생 (수동 클릭, 시작 직후, 설정 적용 중엔 소리 없음)"""
        self._logger.info(f"SIGNOFF - {message}")
        if getattr(self, '_signoff_manual_click', False):
            return
        if not self._startup_complete:
            return
        if getattr(self, '_signoff_settings_applying', False):
            return
        signoff_cfg = self._config.get("signoff", {})
        state = self._signoff_manager.get_state(group_id)
        group = self._signoff_manager.get_groups().get(group_id)
        group_name = group.name if group else f"Group{group_id}"
        if state == SignoffState.PREPARATION:
            sound = signoff_cfg.get("prep_alarm_sound", "")
        elif state == SignoffState.SIGNOFF:
            sound = signoff_cfg.get("enter_alarm_sound", "")
        else:
            sound = signoff_cfg.get("release_alarm_sound", "")
            # IDLE(정파 해제) → 억제 로그 기록 초기화
            self._signoff_suppressed_logged.clear()
        if sound:
            self._alarm.play_test_sound(sound)

        # 텔레그램 알림: 정파모드 진입 및 해제 시만 발송
        tg = self._config.get("telegram", {})
        notify_signoff_flag = tg.get("notify_signoff", True)
        self._logger.file_only(
            f"SIGNOFF-TG state={state.name} enabled={self._telegram._enabled} "
            f"notify_signoff={notify_signoff_flag}"
        )
        if notify_signoff_flag:
            if state == SignoffState.SIGNOFF:
                self._telegram.notify("정파", group_name, group_name, self._latest_frame)
            elif state == SignoffState.IDLE:
                self._telegram.notify("정파", group_name, group_name, self._latest_frame, is_recovery=True)

    def _on_signoff_button_clicked(self, group_id: int):
        """정파 버튼 클릭: IDLE→PREPARATION→SIGNOFF→IDLE 순서로 상태 로테이션. 소리 없음."""
        self._signoff_manual_click = True
        self._signoff_manager.cycle_state(group_id)
        self._signoff_manual_click = False

    # ── 기타 ───────────────────────────────────────────

    def _on_detection_toggled(self, enabled: bool):
        """감지 On/Off 버튼 처리"""
        self._detection_enabled = enabled
        if enabled:
            self._detect_timer.start()
            self._logger.info("SYSTEM - 감지 시작")
        else:
            self._detect_timer.stop()
            self._logger.info("SYSTEM - 감지 중지")
            # 감지 OFF 시 진행 중인 알람 및 깜빡임 즉시 해제
            self._alarm.resolve_all()
            for roi in self._roi_manager.video_rois:
                self._video_widget.set_alert_state(roi.label, False)
            for roi in self._roi_manager.audio_rois:
                self._video_widget.set_alert_state(roi.label, False)
            self._black_logged.clear()
            self._still_logged.clear()
            self._audio_level_logged.clear()
            # 임베디드 오디오 알림 상태도 초기화
            self._embedded_log_sent = False
            self._last_silence_seconds = 0.0
            self._detector.reset_embedded_silence()

    def _on_sound_toggled(self, enabled: bool):
        self._alarm.set_sound_enabled(enabled)
        self._config.setdefault("alarm", {})["sound_enabled"] = enabled

    def _on_volume_changed(self, volume: int):
        self._alarm.set_volume(volume / 100.0)
        self._audio_thread.set_volume(volume / 100.0)  # 패스스루 출력 볼륨 동기화
        self._config.setdefault("alarm", {})["volume"] = volume
        if self._settings_dialog:
            self._settings_dialog.set_alarm_volume(volume)

    def _on_dark_mode_toggled(self, dark: bool):
        qss_path = "resources/styles/dark_theme.qss" if dark \
                   else "resources/styles/light_theme.qss"
        if os.path.exists(qss_path):
            with open(qss_path, "r", encoding="utf-8") as f:
                QApplication.instance().setStyleSheet(f.read())
        elif not dark:
            QApplication.instance().setStyleSheet("")

    def _on_log_message(self, message: str, log_type: str):
        self._log_widget.add_log(message, log_type)

    # ── 전체화면 ───────────────────────────────────────

    def show(self):
        """전체화면 복원을 위해 show() 오버라이드"""
        if self._restore_fullscreen:
            self.showFullScreen()
            self._top_bar.set_fullscreen_button_state(True)
        else:
            super().show()

    def _toggle_fullscreen(self):
        """전체화면 / 일반 창 전환"""
        if self.isFullScreen():
            self.showNormal()
            self._top_bar.set_fullscreen_button_state(False)
        else:
            self.showFullScreen()
            self._top_bar.set_fullscreen_button_state(True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self._toggle_fullscreen()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 반화면 오버레이 크기 동기화
        if self._roi_overlay:
            self._roi_overlay.resize(self._video_widget.size())

    def _on_system_settings_changed(self, params: dict):
        """시스템 설정 변경 시 config에 반영하고 즉시 파일 저장"""
        self._config["system"] = params
        self._config_manager.save(self._config)

    # ── 예약 재시작 ─────────────────────────────────────

    def _check_scheduled_restart(self):
        """10초 주기로 예약 재시작 시각 확인 (설정 파일을 매번 직접 읽어 런타임 변경 반영)"""
        try:
            sys_cfg = self._config_manager.load().get("system", {})
            if not sys_cfg.get("scheduled_restart_enabled", True):
                return

            time_str = sys_cfg.get("scheduled_restart_time", "03:00")
            try:
                t = datetime.datetime.strptime(time_str, "%H:%M")
                restart_hour, restart_minute = t.hour, t.minute
            except ValueError:
                return

            now = datetime.datetime.now()
            today_str = datetime.date.today().isoformat()
            if (now.hour == restart_hour and now.minute == restart_minute
                    and self._restart_done_date != today_str):
                self._do_scheduled_restart(time_str)
        except Exception as e:
            _log.error("_check_scheduled_restart 오류 (silent fail 방지): %s", e)

    def _do_scheduled_restart(self, time_str: str):
        """새 프로세스를 시작하고 현재 프로세스를 종료한다."""
        self._restart_done_date = datetime.date.today().isoformat()
        self._logger.info("SYSTEM - 예약 재시작 실행 (설정된 시각) — 30초 후 재시작")
        # --restarted HH:MM 전달: 새 프로세스에서 같은 시각 재트리거 방지
        clean = []
        skip = False
        for a in sys.argv:
            if a == "--restarted":
                skip = True
                continue
            if skip:
                skip = False
                continue
            clean.append(a)
        args = clean + ["--restarted", time_str]
        subprocess.Popen(
            [sys.executable] + args,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        # 500ms 지연: 로그 시그널이 UI에 표시된 뒤 종료
        QTimer.singleShot(500, QApplication.instance().quit)

    def closeEvent(self, event: QCloseEvent):
        # 설정 저장
        if self._settings_dialog:
            self._config = self._settings_dialog.get_config()
        self._config["rois"] = self._roi_manager.to_dict()
        self._config["ui_state"] = {
            "detection_enabled": self._detection_enabled,
            "roi_visible": self._top_bar._btn_roi.isChecked(),
            "fullscreen": self.isFullScreen(),
        }
        self._config_manager.save(self._config)

        # 오버레이 정리
        self._close_overlay()

        # 스레드 종료
        self._detect_timer.stop()
        self._summary_timer.stop()
        self._restart_timer.stop()
        if hasattr(self, "_tg_test_timer"):
            self._tg_test_timer.stop()
        if hasattr(self, "_capture_thread"):
            self._capture_thread.stop()
            self._capture_thread.wait(5000)
        if hasattr(self, "_audio_thread"):
            self._audio_thread.stop()
            self._audio_thread.wait(5000)
        self._telegram.stop()
        self._recorder.stop()

        self._logger.info("SYSTEM - 프로그램 종료")
        event.accept()
