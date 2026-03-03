# 배포 전 버그수정 TODO

## 우선순위 1 — 치명 (프로그램 크래시)

*(모두 완료)*

## 우선순위 2 — 경고 (논리 오류)

- [ ] **main_window.py** `_apply_detection_params()` L490: `self._config["detection"]` 미업데이트 → `_apply_signoff_config`가 still_duration 구버전 값 읽음
- [x] **log_widget.py** L34: 오디오 이상 로그 색상 `#006600`(초록) → `#cc0000`(빨간) (CLAUDE.md 위반)

## 완료

- [x] **detector.py** `__init__`: `_tone_states: Dict[str, DetectionState] = {}` 초기화 추가 (L145)
- [x] **default_config.json**: `still_threshold` 2→8, `still_changed_ratio: 2.0` 추가
