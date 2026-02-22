"""
메인 윈도우
3분할 레이아웃: 상단 바 + 비디오 영역(~75%) + 로그 영역(~25%)
"""
import copy
import os
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QApplication,
)
from PySide6.QtCore import Qt, QTimer
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
from utils.config_manager import ConfigManager, DEFAULT_CONFIG
from utils.logger import AppLogger


class MainWindow(QMainWindow):
    """KBS 16채널 모니터링 메인 윈도우"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("KBS Peacock v1.02")
        self.setMinimumSize(1280, 720)
        self.resize(1600, 900)

        # 핵심 컴포넌트 초기화
        self._config_manager = ConfigManager()
        self._config = self._config_manager.load()
        self._roi_manager = ROIManager()
        self._roi_manager.from_dict(self._config.get("rois", {}))
        self._detector = Detector()
        self._apply_detection_config(self._config.get("detection", {}))
        # 성능 설정 (비디오/오디오 감지 활성화 플래그 초기값)
        self._video_detect_enabled = True
        self._audio_detect_enabled = True
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

        # UI 구성
        self._setup_ui()
        self._connect_signals()
        self._start_threads()

        # TopBar 볼륨/뮤트 초기값 동기화
        init_alarm = self._config.get("alarm", {})
        init_vol = init_alarm.get("volume", 80)
        self._top_bar.set_volume_display(init_vol)
        self._top_bar.set_mute_state(init_alarm.get("sound_enabled", True))

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
        self._top_bar.clear_alarm_requested.connect(self._on_clear_alarm)
        self._top_bar.dark_mode_toggled.connect(self._on_dark_mode_toggled)

        self._alarm.visual_blink.connect(self._video_widget.set_blink_state)

        self._logger.log_signal.connect(self._on_log_message)

        # 텔레그램 로거 주입 (전송 성공은 파일에만, 오류는 UI에도 표시)
        self._telegram.set_logger(self._logger.file_only, self._logger.error)

    # ── 스레드 시작 ────────────────────────────────────

    def _start_threads(self):
        port = self._config.get("port", 0)

        self._capture_thread = VideoCaptureThread(port=port)
        self._capture_thread.frame_ready.connect(self._on_frame_ready)
        self._capture_thread.connected.connect(
            lambda: self._logger.info(f"SYSTEM - 포트 {self._config.get('port',0)} 연결 성공")
        )
        self._capture_thread.disconnected.connect(
            lambda: self._logger.error(f"SYSTEM - 포트 {self._config.get('port',0)} 연결 실패")
        )
        self._capture_thread.start()

        self._audio_thread = AudioMonitorThread()
        self._audio_thread.level_updated.connect(self._top_bar.update_audio_levels)
        self._audio_thread.level_updated.connect(self._on_audio_level_for_silence)
        self._audio_thread.silence_detected.connect(self._on_embedded_silence)
        self._audio_thread.status_changed.connect(
            lambda msg: self._logger.info(f"AUDIO - {msg}")
        )
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

        self._latest_frame = None

    # ── 프레임/감지 ────────────────────────────────────

    def _on_frame_ready(self, frame):
        self._latest_frame = frame
        if self._roi_overlay is None:
            self._video_widget.update_frame(frame)
        self._recorder.push_frame(frame)

    def _run_detection(self):
        if self._latest_frame is None:
            return
        if self._roi_overlay is not None:
            return  # 편집 중 감지 중단

        video_rois = self._roi_manager.video_rois
        audio_rois = self._roi_manager.audio_rois

        # 비디오 ROI 블랙/스틸 감지
        if video_rois and self._video_detect_enabled:
            results = self._detector.detect_frame(self._latest_frame, video_rois)
            # label → name 매핑 캐시 (O(n) 선형 탐색 제거)
            video_name_map = {r.label: (r.media_name or r.label) for r in video_rois}

            for label, state in results.items():
                black_alert    = state.get("black_alerting", False)
                still_alert    = state.get("still_alerting", False)
                black_resolved = state.get("black_resolved", False)
                still_resolved = state.get("still_resolved", False)
                name = video_name_map.get(label, label)

                # ── 블랙 ──
                if black_alert:
                    if label not in self._black_logged:
                        self._logger.error(f"{name} - 블랙 감지")
                        tg = self._config.get("telegram", {})
                        if tg.get("notify_black", True):
                            self._telegram.notify("블랙", label, name, self._latest_frame)
                        self._recorder.trigger("블랙", label, name)
                    self._alarm.trigger("블랙", label, self._detector.black_alarm_duration)
                    self._black_logged.add(label)
                else:
                    if black_resolved and label in self._black_logged:
                        last_dur = state.get("black_last_duration", 0)
                        self._logger.error(f"{name} - 블랙 {last_dur:.0f}초")
                        self._logger.info(f"{name} - 블랙 정상 복구")
                        tg = self._config.get("telegram", {})
                        if tg.get("notify_black", True):
                            self._telegram.notify("블랙", label, name, self._latest_frame, is_recovery=True)
                    self._alarm.resolve("블랙", label)
                    self._black_logged.discard(label)

                # ── 스틸 ──
                if still_alert:
                    if label not in self._still_logged:
                        self._logger.still_error(f"{name} - 스틸 감지")
                        tg = self._config.get("telegram", {})
                        if tg.get("notify_still", True):
                            self._telegram.notify("스틸", label, name, self._latest_frame)
                        self._recorder.trigger("스틸", label, name)
                    self._alarm.trigger("스틸", label, self._detector.still_alarm_duration)
                    self._still_logged.add(label)
                else:
                    if still_resolved and label in self._still_logged:
                        last_dur = state.get("still_last_duration", 0)
                        self._logger.still_error(f"{name} - 스틸 {last_dur:.0f}초")
                        self._logger.info(f"{name} - 스틸 정상 복구")
                        tg = self._config.get("telegram", {})
                        if tg.get("notify_still", True):
                            self._telegram.notify("스틸", label, name, self._latest_frame, is_recovery=True)
                    self._alarm.resolve("스틸", label)
                    self._still_logged.discard(label)

                self._video_widget.set_alert_state(label, black_alert or still_alert)

        # 오디오 ROI 레벨미터 감지 (HSV)
        if audio_rois and self._audio_detect_enabled:
            audio_results = self._detector.detect_audio_roi(
                self._latest_frame, audio_rois
            )
            # label → name 매핑 캐시 (O(n) 선형 탐색 제거)
            audio_name_map = {r.label: (r.media_name or r.label) for r in audio_rois}

            for label, state in audio_results.items():
                alerting = state.get("alerting", False)
                resolved = state.get("resolved", False)
                name = audio_name_map.get(label, label)

                if alerting:
                    if label not in self._audio_level_logged:
                        self._logger.audio_error(f"{name} - 무음 감지")
                        tg = self._config.get("telegram", {})
                        if tg.get("notify_audio_level", True):
                            self._telegram.notify("오디오", label, name, self._latest_frame)
                        self._recorder.trigger("오디오", label, name)
                    self._alarm.trigger(
                        "오디오", label, self._detector.audio_level_alarm_duration
                    )
                    self._audio_level_logged.add(label)
                else:
                    if resolved and label in self._audio_level_logged:
                        last_dur = state.get("last_duration", 0)
                        self._logger.audio_error(
                            f"{name} - 무음 {last_dur:.0f}초"
                        )
                        self._logger.info(f"{name} - 무음 정상 복구")
                        tg = self._config.get("telegram", {})
                        if tg.get("notify_audio_level", True):
                            self._telegram.notify("오디오", label, name, self._latest_frame, is_recovery=True)
                    self._alarm.resolve("오디오", label)
                    self._audio_level_logged.discard(label)

                self._video_widget.set_alert_state(label, alerting)

    def _update_summary(self):
        v_count = len(self._roi_manager.video_rois)
        a_count = len(self._roi_manager.audio_rois)
        self._top_bar.update_summary(v_count, a_count, self._detector.embedded_alerting)
        # 감지영역 정보를 비디오 위젯에도 동기화
        self._video_widget.set_rois(
            self._roi_manager.video_rois,
            self._roi_manager.audio_rois,
        )

    # ── 모니터링 제어 ──────────────────────────────────

    def _on_clear_alarm(self):
        self._alarm.resolve_all()
        self._detector.reset_all()
        for roi in self._roi_manager.video_rois:
            self._video_widget.set_alert_state(roi.label, False)
        self._black_logged.clear()
        self._still_logged.clear()
        self._audio_level_logged.clear()
        self._embedded_log_sent = False
        self._last_silence_seconds = 0.0
        self._logger.info("SYSTEM - 알림 초기화")

    # ── 설정 다이얼로그 ────────────────────────────────

    def _open_settings(self):
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
            self._settings_dialog.finished.connect(self._on_settings_closed)
        else:
            self._settings_dialog.refresh_roi_tables()

        self._settings_dialog.showNormal()
        self._settings_dialog.resize(780, 660)
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_settings_closed(self):
        if self._settings_dialog:
            self._config = self._settings_dialog.get_config()

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
        """감지 파라미터 Detector에 즉시 반영 후 상태 초기화"""
        self._apply_detection_config(params)
        self._detector.reset_all()

    def _apply_performance_params(self, params: dict):
        """성능 파라미터 즉시 반영 (타이머 주기, 스케일, 감지 활성화)"""
        self._apply_performance_config(params)
        # 성능 설정 config에도 저장
        self._config["performance"] = dict(params)

    def _apply_performance_config(self, perf: dict):
        """성능 설정을 Detector 및 타이머에 반영"""
        self._video_detect_enabled = perf.get("video_detection_enabled", True)
        self._audio_detect_enabled = perf.get("audio_detection_enabled", True)
        self._detector.scale_factor = perf.get("scale_factor", 1.0)
        self._detector.still_detection_enabled = perf.get("still_detection_enabled", True)
        # 타이머가 이미 생성된 경우에만 주기 변경
        if hasattr(self, "_detect_timer"):
            self._detect_timer.setInterval(perf.get("detection_interval", 200))

    def _apply_detection_config(self, det: dict):
        """config dict에서 감지 파라미터 적용"""
        self._detector.black_threshold = det.get("black_threshold", 10)
        self._detector.black_duration = det.get("black_duration", 10.0)
        self._detector.black_alarm_duration = det.get("black_alarm_duration", 10.0)
        self._detector.still_threshold = det.get("still_threshold", 2)
        self._detector.still_duration = det.get("still_duration", 20.0)
        self._detector.still_alarm_duration = det.get("still_alarm_duration", 10.0)
        # 오디오 레벨미터 HSV
        self._detector.audio_hsv_h_min = det.get("audio_hsv_h_min", 40)
        self._detector.audio_hsv_h_max = det.get("audio_hsv_h_max", 80)
        self._detector.audio_hsv_s_min = det.get("audio_hsv_s_min", 30)
        self._detector.audio_hsv_s_max = det.get("audio_hsv_s_max", 255)
        self._detector.audio_hsv_v_min = det.get("audio_hsv_v_min", 30)
        self._detector.audio_hsv_v_max = det.get("audio_hsv_v_max", 255)
        self._detector.audio_pixel_ratio = det.get("audio_pixel_ratio", 5.0)
        self._detector.audio_level_duration = det.get("audio_level_duration", 20.0)
        self._detector.audio_level_alarm_duration = det.get("audio_level_alarm_duration", 10.0)
        self._detector.audio_level_recovery_seconds = det.get("audio_level_recovery_seconds", 2.0)
        # 임베디드 오디오
        self._detector.embedded_silence_threshold = det.get("embedded_silence_threshold", -50)
        self._detector.embedded_silence_duration = det.get("embedded_silence_duration", 20.0)
        self._detector.embedded_alarm_duration = det.get("embedded_alarm_duration", 10.0)

    # ── 임베디드 오디오 감지 ───────────────────────────

    def _on_embedded_silence(self, silence_seconds: float):
        """AudioMonitorThread.silence_detected 수신 — 임베디드 오디오 무음 업데이트"""
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
        if avg_db > self._detector.embedded_silence_threshold:
            if self._detector.embedded_alerting or self._embedded_log_sent:
                was_sent = self._embedded_log_sent
                last_seconds = self._last_silence_seconds
                self._detector.reset_embedded_silence()
                self._alarm.resolve("무음", "Embedded Audio")
                if was_sent:
                    self._embedded_log_sent = False
                    self._logger.embedded_error(f"Embedded Audio - 무음 {last_seconds:.0f}초")
                    self._logger.info("Embedded Audio - 정상 복구")
                    tg = self._config.get("telegram", {})
                    if tg.get("notify_embedded", True):
                        self._telegram.notify("무음", "Embedded", "Embedded Audio", self._latest_frame, is_recovery=True)
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
        )

    def _on_telegram_settings_changed(self, params: dict):
        """SettingsDialog 텔레그램 설정 변경"""
        self._config["telegram"] = params
        self._apply_telegram_config(params)

    def _on_telegram_test(self, token: str, chat_id: str):
        """연결 테스트 결과를 설정창에 반환"""
        ok, msg = self._telegram.test_connection(token, chat_id)
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

        # 설정창 UI 갱신
        if self._settings_dialog:
            self._settings_dialog.reload_config(config)

        self._update_summary()
        self._logger.info(f"SYSTEM - 설정 불러오기 완료: {os.path.basename(filepath)}")

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

        # 설정창 UI 갱신
        if self._settings_dialog:
            self._settings_dialog.reload_config(config)

        self._update_summary()
        self._logger.info("SYSTEM - 설정 초기화 완료")

    # ── 기타 ───────────────────────────────────────────

    def _on_detection_toggled(self, enabled: bool):
        """감지 On/Off 버튼 처리"""
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

    def _on_sound_toggled(self, enabled: bool):
        self._alarm.set_sound_enabled(enabled)
        self._config.setdefault("alarm", {})["sound_enabled"] = enabled

    def _on_volume_changed(self, volume: int):
        self._alarm.set_volume(volume / 100.0)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 반화면 오버레이 크기 동기화
        if self._roi_overlay:
            self._roi_overlay.resize(self._video_widget.size())

    def closeEvent(self, event: QCloseEvent):
        # 설정 저장
        if self._settings_dialog:
            self._config = self._settings_dialog.get_config()
        self._config["rois"] = self._roi_manager.to_dict()
        self._config_manager.save(self._config)

        # 오버레이 정리
        self._close_overlay()

        # 스레드 종료
        self._detect_timer.stop()
        self._summary_timer.stop()
        if hasattr(self, "_capture_thread"):
            self._capture_thread.stop()
        if hasattr(self, "_audio_thread"):
            self._audio_thread.stop()
        self._telegram.stop()
        self._recorder.stop()

        self._logger.info("SYSTEM - 프로그램 종료")
        event.accept()
