# KBS Peacock v1.05 수정내역

**버전:** v1.05
**작성일:** 2026-02-26
**기반 버전:** v1.04

---

## 수정 항목 요약

| # | 항목 | 그룹 | 상태 |
|---|------|------|------|
| 1 | 스틸감지 기본값 60초로 변경 | A | ✅ 완료 |
| 2 | 상단바 EA 감지현황 수정 | B | ✅ 완료 |
| 3 | System Log 형식 변경 (라벨. 매체명 - 내용) | C | ✅ 완료 |
| 4 | 녹화 파일명 형식 변경 (YYYYMMDD_HHMMSS_라벨_매체명_원인) | C | ✅ 완료 |
| 5 | F11 전체화면 + 전체화면 버튼 추가 | D | ✅ 완료 |

---

## 상세 수정 내용

### 그룹 A: 스틸감지 기본값 60초

**문제:** 스틸감지 "몇 초 이상시 알림발생" 기본값이 30초였음
**수정:** 60초로 변경

수정 파일:
- `kbs_monitor/config/default_config.json` — `still_duration: 20 → 60`
- `kbs_monitor/ui/settings_dialog.py` — 위젯 초기값 30 → 60, 기본값 fallback 30 → 60
- `kbs_monitor/ui/main_window.py` — Detector 적용 기본값 20.0 → 60.0

---

### 그룹 B: EA 감지현황 수정

**문제:** 임베디드 오디오 감지가 활성화되어 있어도 상단바 EA가 "-"로 표시됨
**원인:** `embedded_alerting`(알림 발생 여부)을 사용했기 때문
**수정:** `embedded_detect_enabled`(기능 활성화 여부)로 교체, 알림 발생 시 빨간색 표시 추가

수정 파일:
- `kbs_monitor/ui/main_window.py` — `_update_summary()`에서 `embedded_detect_enabled` 전달
- `kbs_monitor/ui/top_bar.py` — `update_summary()` 파라미터 추가, 알림 시 색상 변경

---

### 그룹 C: 로그 형식 + 녹화 파일명

**로그 형식 변경**
- 변경 전: `KBS1 - 블랙 감지`
- 변경 후: `V1. KBS1 - 블랙 감지` (라벨. 매체명 - 내용)
- 매체명 없을 경우: `V1 - 블랙 감지`

수정 파일: `kbs_monitor/ui/main_window.py` — `_run_detection()` 내 8곳 수정

**녹화 파일명 변경**
- 변경 전: `20260226_143045_V1_블랙.mp4`
- 변경 후: `20260226_143045_V1_KBS1_블랙.mp4` (타임스탬프_라벨_매체명_원인)
- 매체명 없을 경우: `20260226_143045_V1_블랙.mp4`

수정 파일: `kbs_monitor/core/auto_recorder.py` — `trigger()` 메서드

---

### 그룹 D: F11 전체화면

**추가 기능:**
- F11 키로 전체화면/일반화면 토글
- 상단바 오른쪽 끝에 "전체화면" 버튼 추가
- 전체화면 진입 시 버튼 상태 동기화

수정 파일:
- `kbs_monitor/ui/top_bar.py` — `fullscreen_toggled` 시그널, 전체화면 버튼, `set_fullscreen_button_state()` 메서드
- `kbs_monitor/ui/main_window.py` — `keyPressEvent()`, `_toggle_fullscreen()` 추가, TopBar 시그널 연결

---

## 수정된 파일 목록

| 파일 | 수정 내용 |
|------|---------|
| `kbs_monitor/config/default_config.json` | still_duration 기본값 60 |
| `kbs_monitor/ui/settings_dialog.py` | 스틸 위젯 초기값 60 |
| `kbs_monitor/ui/main_window.py` | EA 수정, 로그 형식, 스틸 기본값, 전체화면 |
| `kbs_monitor/ui/top_bar.py` | EA 표시 수정, 전체화면 버튼 |
| `kbs_monitor/core/auto_recorder.py` | 파일명 형식 변경 |

---

## 다음 대화에서 재개 시 참조

대화를 clear한 후 재개할 경우, 이 문서와 함께 아래 정보를 제공하세요:

```
KBS Peacock v1.05 수정 작업 재개.
Documents/KBS_Peacock_v1.05_수정내역.md 참조.
현재까지 완료된 그룹: [완료된 그룹 표시]
다음 작업: 그룹 [X]
```

### 그룹별 진행 체크리스트

- [x] 그룹 E: Documents 문서 생성
- [x] 그룹 A: 스틸감지 기본값 60초
- [x] 그룹 B: EA 감지현황 수정
- [x] 그룹 C: 로그 형식 + 녹화 파일명
- [x] 그룹 D: F11 전체화면

---

## 검증 방법

1. 프로그램 실행 후 설정 → 감지 설정 → 스틸감지 → 기본값 **60초** 확인
2. 임베디드 오디오 감지 활성화 → 상단바 EA에 **"1"** 표시 확인
3. 감지 발생 시 로그에 **`"V1. KBS1 - 블랙 감지"`** 형식 확인
4. 자동 녹화 파일명 **`YYYYMMDD_HHMMSS_V1_KBS1_블랙.mp4`** 확인
5. **F11** 키 / 전체화면 버튼으로 전체화면 전환 확인
