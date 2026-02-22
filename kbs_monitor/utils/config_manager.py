"""
설정 저장/불러오기 모듈
JSON 파일 기반
"""
import os
import sys
import json
from typing import Any


DEFAULT_CONFIG = {
    "port": 0,
    "detection": {
        "black_threshold": 10,
        "black_duration": 10,
        "black_alarm_duration": 10,
        "still_threshold": 2,
        "still_duration": 30,
        "still_alarm_duration": 10,
        "audio_hsv_h_min": 40,
        "audio_hsv_h_max": 80,
        "audio_hsv_s_min": 30,
        "audio_hsv_s_max": 255,
        "audio_hsv_v_min": 30,
        "audio_hsv_v_max": 255,
        "audio_pixel_ratio": 5,
        "audio_level_duration": 20,
        "audio_level_alarm_duration": 10,
        "audio_level_recovery_seconds": 2,
        "embedded_silence_threshold": -50,
        "embedded_silence_duration": 20,
        "embedded_alarm_duration": 10,
    },
    "alarm": {
        "sound_enabled": True,
        "volume": 80,
        "sounds_dir": "resources/sounds",
        "sound_files": {
            "black": "",
            "still": "",
            "audio": "",
            "default": "",
        },
    },
    "rois": {
        "video": [],
        "audio": [],
    },
    "performance": {
        "detection_interval":      200,    # ms, QTimer 감지 주기 (100~1000)
        "scale_factor":            1.0,    # 감지 해상도 스케일 (1.0 / 0.5 / 0.25)
        "video_detection_enabled": True,   # 비디오 블랙/스틸 감지 활성화
        "audio_detection_enabled": True,   # 오디오 레벨미터 HSV 감지 활성화
        "still_detection_enabled": True,   # 스틸 감지 활성화
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
    },
    "recording": {
        "enabled": False,
        "save_dir": "recordings",  # 저장 폴더 경로
        "pre_seconds": 5,          # 사고 전 버퍼 시간(초)
        "post_seconds": 15,        # 사고 후 녹화 시간(초)
        "max_keep_days": 7,        # 최대 보관 일수
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
        return result

    def _read_json(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: str, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
