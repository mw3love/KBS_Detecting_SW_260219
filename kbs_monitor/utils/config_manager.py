"""
설정 저장/불러오기 모듈
JSON 파일 기반
"""
import os
import sys
import json
import tempfile
from typing import Any


DEFAULT_CONFIG = {
    "port": 0,
    "detection": {
        "black_threshold": 5,
        "black_dark_ratio": 98.0,
        "black_duration": 20,
        "black_alarm_duration": 60,
        "black_motion_suppress_ratio": 0.2,
        "still_threshold": 4,
        "still_block_threshold": 15.0,
        "still_duration": 60,
        "still_alarm_duration": 60,
        "still_reset_frames": 3,
        "audio_hsv_h_min": 40,
        "audio_hsv_h_max": 95,
        "audio_hsv_s_min": 80,
        "audio_hsv_s_max": 255,
        "audio_hsv_v_min": 60,
        "audio_hsv_v_max": 255,
        "audio_pixel_ratio": 5,
        "audio_level_duration": 20,
        "audio_level_alarm_duration": 60,
        "audio_level_recovery_seconds": 2,
        "embedded_silence_threshold": -50,
        "embedded_silence_duration": 20,
        "embedded_alarm_duration": 60,
        "audio_tone_std_threshold": 3.0,   # 정파 톤 감지: ratio 표준편차 임계값(%) — 이 값 이하면 일정 톤으로 판단
        "audio_tone_duration":      60.0,  # 정파 톤 감지: 톤 상태 지속 시간(초) — 정파 진입 트리거 조건
        "audio_tone_min_level":     5.0,   # 정파 톤 감지: 최소 레벨(%) — 이 값 미만이면 무음(톤 아님)으로 처리
    },
    "alarm": {
        "sound_enabled": True,
        "volume": 80,
        "sounds_dir": "resources/sounds",
        "sound_files": {
            "black": "",
            "still": "",
            "audio": "",
            "default": "resources/sounds/alarm.wav",
        },
    },
    "rois": {
        "video": [],
        "audio": [],
    },
    "performance": {
        "detection_interval":        200,   # ms, QTimer 감지 주기 (100~1000)
        "scale_factor":              1.0,   # 감지 해상도 스케일 (1.0 / 0.5 / 0.25)
        "black_detection_enabled":   True,  # 블랙 감지 활성화
        "still_detection_enabled":   True,  # 스틸 감지 활성화
        "audio_detection_enabled":   True,  # 오디오 레벨미터 HSV 감지 활성화
        "embedded_detection_enabled": True, # 임베디드 오디오 무음 감지 활성화
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "send_image": True,
        "cooldown": 60,            # 동일 채널 재발송 방지 (초)
        "notify_black": True,
        "notify_still": True,
        "notify_audio_level": True,
        "notify_embedded": True,
        "notify_signoff": True,
    },
    "recording": {
        "enabled": True,
        "save_dir": "recordings",  # 저장 폴더 경로
        "pre_seconds": 5,          # 사고 전 버퍼 시간(초)
        "post_seconds": 15,        # 사고 후 녹화 시간(초)
        "max_keep_days": 7,        # 최대 보관 일수
        "output_width": 960,       # 녹화 출력 가로 해상도
        "output_height": 540,      # 녹화 출력 세로 해상도
        "output_fps": 10,          # 녹화 출력 FPS
    },
    "ui_state": {
        "detection_enabled": True,
        "roi_visible": True,
    },
    "signoff": {
        "auto_preparation":    True,   # 자동 정파 준비 활성화
        "prep_alarm_sound":    "resources/sounds/sign_off.wav",   # 정파준비 시작 알림음 WAV 경로
        "enter_alarm_sound":   "resources/sounds/sign_off.wav",   # 정파모드 진입 알림음 WAV 경로
        "release_alarm_sound": "resources/sounds/sign_off.wav",   # 정파 해제 알림음 WAV 경로
        "group1": {
            "name":              "1TV",
            "enter_roi":         {"video_label": ""},
            "suppressed_labels": [],
            "start_time":        "03:00",
            "end_time":          "05:00",
            "end_next_day":      False,
            "prep_minutes":      150,
            "exit_prep_minutes": 30,
            "exit_trigger_sec":  5,
            "weekdays":          [0, 1],
        },
        "group2": {
            "name":              "2TV",
            "enter_roi":         {"video_label": ""},
            "suppressed_labels": [],
            "start_time":        "02:00",
            "end_time":          "05:00",
            "end_next_day":      False,
            "prep_minutes":      90,
            "exit_prep_minutes": 30,
            "exit_trigger_sec":  5,
            "weekdays":          [0, 1, 2, 3, 4, 5, 6],
        },
    },
}


class ConfigManager:
    """JSON 기반 설정 저장/불러오기"""

    CONFIG_DIR = "config"
    CONFIG_FILE = "kbs_config.json"
    DEFAULT_FILE = "default_config.json"

    def __init__(self):
        os.makedirs(self.CONFIG_DIR, exist_ok=True)
        self._default_path = os.path.join(self.CONFIG_DIR, self.DEFAULT_FILE)
        self._config_path = os.path.join(self.CONFIG_DIR, self.CONFIG_FILE)

        # 기본 설정 파일 생성 (없는 경우)
        if not os.path.exists(self._default_path):
            self._write_json(self._default_path, DEFAULT_CONFIG)

    def load(self, filename: str = None) -> dict:
        """설정 불러오기. 파일 없으면 기본값 반환"""
        path = os.path.join(self.CONFIG_DIR, filename) if filename else self._config_path

        if os.path.exists(path):
            try:
                data = self._read_json(path)
                # 기본값 병합 (새 키가 추가된 경우 대비)
                return self._merge_defaults(data)
            except Exception as e:
                print(f"[ConfigManager] 설정 로드 실패 ({path}): {e}", file=sys.stderr)

        return dict(DEFAULT_CONFIG)

    def save(self, config: dict, filename: str = None):
        """설정 저장"""
        path = os.path.join(self.CONFIG_DIR, filename) if filename else self._config_path
        try:
            self._write_json(path, config)
            return True
        except Exception:
            return False

    def save_to_path(self, config: dict, abs_path: str) -> bool:
        """절대 경로로 설정 저장"""
        try:
            parent = os.path.dirname(abs_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._write_json(abs_path, config)
            return True
        except Exception:
            return False

    def load_from_path(self, abs_path: str) -> dict:
        """절대 경로에서 설정 불러오기"""
        try:
            data = self._read_json(abs_path)
            return self._merge_defaults(data)
        except Exception:
            return dict(DEFAULT_CONFIG)

    def _merge_defaults(self, data: dict) -> dict:
        """기본값과 병합하여 누락된 키 보완"""
        result = dict(DEFAULT_CONFIG)
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = {**result[key], **value}
            else:
                result[key] = value
        # 마이그레이션: still_changed_ratio → still_block_threshold
        det = result.get("detection", {})
        if "still_changed_ratio" in det:
            det.pop("still_changed_ratio")
            if "still_block_threshold" not in det:
                det["still_block_threshold"] = 15.0
        return result

    def _read_json(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: str, data: dict):
        """atomic write: 임시 파일에 쓴 뒤 os.replace()로 원자적 교체"""
        dir_name = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
