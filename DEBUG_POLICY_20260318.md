# 2026-03-18 미감지 장애 분석 및 디버그 정책

> 작성일: 2026-03-18
> 버전: v1.5.4
> 관련 채널: V2 (2TV ON-AIR) — 16분할 우하단

---

## 1. 장애 개요

| 항목 | 내용 |
|------|------|
| 발생 시각 | 2026-03-18 13:15 경 |
| 발견 시각 | 2026-03-18 13:22 |
| 대상 채널 | V2 (2TV ON-AIR) |
| 화면 상태 | 블랙 + PSIP 화면 (정적, 움직임 없음 확인) |
| 알림 발생 | 없음 (소리/깜빡임/텔레그램 모두 미발생) |
| 감지 루프 | 정상 실행 중 (13:21:50 heartbeat 확인) |
| 해결 방법 | 프로그램 재시작 후 즉시 감지 |

---

## 2. 로그 타임라인

```
00:30:00  [INFO] SIGNOFF - 2TV 정파준비모드를 시작합니다
01:00:36  [INFO] SIGNOFF - 2TV 정파모드에 돌입합니다
04:45:05  [INFO] SIGNOFF - 2TV 정파모드를 해제합니다   ← 정파 조기 해제 (exit_prep 30분 구간)
04:45:09  [INFO] TELEGRAM - 정파 복구 전송 완료 (2TV)

13:11:35  [INFO] SYSTEM - 감지 정상 실행 중 (1430분 경과)
13:16:43  [INFO] SYSTEM - 감지 정상 실행 중 (1435분 경과)  ← 이미 이상 발생 중, 미감지
13:21:50  [INFO] SYSTEM - 감지 정상 실행 중 (1440분 경과)  ← 미감지 지속

13:22:59  [INFO] SYSTEM - 감지 중지  (사용자 토글)
13:23:01  [INFO] SYSTEM - 감지 시작  (사용자 토글)        ← 여전히 미감지
13:23:29  [INFO] SYSTEM - 프로그램 종료
13:23:36  [INFO] SYSTEM - 프로그램 시작

13:23:54  [ERROR] V2. 2TV ON-AIR - 블랙 감지            ← 재시작 18초 후 즉시 감지
13:23:59  [INFO] TELEGRAM - 블랙 알림 전송 완료
13:24:40  [ERROR] V2. 2TV ON-AIR - 블랙 감지
13:24:40  [ERROR] A3. 2TV ON-AIR - 무음 감지
13:25:20  [ERROR] V2. 2TV ON-AIR - 스틸 감지
13:25:28  [INFO] TELEGRAM - 스틸 알림 전송 완료
```

---

## 3. 원인 분석

### 원인 1 (주원인): 스틸 감지 타이머 MPEG 아티팩트 리셋

**파일**: `core/detector.py` — `DetectionState.update()` (L29~76), `detect_frame()` (L244~245)

PSIP 화면은 육안으로 정적이었으나, SDI → HDMI → 캡처카드 → OpenCV 디코딩 경로에서
MPEG 압축이 **텍스트 엣지에 프레임마다 다른 블록 아티팩트**를 생성함.

```
ROI 전체 픽셀: 422 × 261 = 110,142px
PSIP 텍스트 아티팩트 영역: ~2,200px 이상 변화 = 2.0% 초과
```

`still_changed_ratio = 2.0%` 기준에서, 아티팩트가 있는 프레임 **1장만 나와도** 타이머 리셋:

```python
# detector.py L70-72
else:  # is_still = False (모션 있음으로 잘못 판단)
    self.alert_start_time = None  # ← 타이머 즉시 0으로 리셋!
    self.alert_duration = 0.0
```

200ms 감지 주기 기준 → MPEG 아티팩트 프레임이 주기적으로 섞이면
**60초 누적이 영구적으로 불가능** → 스틸 알림 미발생

**재시작 후 즉시 감지된 이유**: 13:23 무렵 화면이 완전 블랙(노이즈 없음)으로 전환되어
블랙 감지는 18초 만에, 스틸 감지는 이후 정상 트리거됨.

---

### 원인 2 (부가): 블랙 감지 임계값 미도달

**파일**: `core/detector.py` — `detect_frame()` (L228~229)
**설정**: `config/kbs_config.json` — `black_dark_ratio: 95.0`

```
화면 상태: 블랙 2/3 + PSIP 1/3
dark_ratio 계산값: ≈ 66%
임계값: 95%
판정: 66% < 95% → is_black = False → 알림 미발생
```

부분 블랙(66%) 상황에서는 블랙 감지 자체가 동작하지 않음.
**스틸 감지만이 유일한 경보 수단이었으나 원인 1로 인해 함께 실패.**

---

### 원인 3: 텔레그램 테스트 미응답

**파일**: `ui/main_window.py` — `_on_telegram_test_done()` (L741~744)

```python
def _on_telegram_test_done(self, ok: bool, msg: str):
    if self._settings_dialog:   # ← 이 시점에 None이면 결과 소실
        self._settings_dialog.set_telegram_test_result(ok, msg)
```

10초 타임아웃 대기 중 설정창을 닫았다 다시 열면 결과가 소실됨.
또는 테스트 시점 일시적 네트워크 히컵(재시작 후 즉시 정상 동작 확인).
**테스트 결과가 파일 로그에 전혀 남지 않아 사후 원인 확인 불가.**

---

## 4. 다음 세션 작업 목록

### 4-1. 코드 수정 (버그 픽스)

| 우선순위 | 항목 | 파일 | 내용 |
|---------|------|------|------|
| ★★★ | 스틸 타이머 히스테리시스 | `core/detector.py` | 단 1프레임 모션으로 타이머 리셋되는 구조 수정. 연속 N프레임(예: 3~5프레임) 이상 모션이어야 리셋하도록 변경. `DetectionState`에 `still_reset_count` 카운터 추가 |
| ★★☆ | 텔레그램 테스트 결과 파일 로그 기록 | `core/telegram_notifier.py` | `test_connection()` 성공/실패 결과를 항상 파일 로그에 기록 |
| ★☆☆ | 부분 블랙 감지 옵션 | `core/detector.py` | `black_dark_ratio` 별도 "근접 경고" 임계값 추가 (예: 50~95% 구간) |

### 4-2. 디버그 로그 강화 (새 기능)

| 우선순위 | 항목 | 내용 |
|---------|------|------|
| ★★★ | Heartbeat 로그 확장 | 5분 heartbeat에 각 ROI의 raw 수치 포함 (dark_ratio, changed_ratio, 타이머 상태) |
| ★★★ | 스틸 타이머 리셋 경고 | 타이머가 N초(예: 5초) 이상 누적 후 리셋될 때 WARN 로그 출력 |
| ★★☆ | 임계값 근접 경고 ("near-miss") | dark_ratio > 50% 또는 changed_ratio < 3% 상태가 30초 지속 시 INFO 로그 |
| ★★☆ | SIGNOFF 억제 발동 로그 | `is_signoff_label()` True 시 첫 억제 발동을 DEBUG 로그로 기록 |
| ★☆☆ | 감지 진단 버튼 | 클릭 시 전 ROI의 현재 raw 수치를 즉시 파일+UI 로그에 강제 출력 |

---

## 5. 스틸 타이머 히스테리시스 수정 설계 (참고용)

현재 `DetectionState.update()` 에서 `is_abnormal=False` (모션 있음) 판정 즉시 타이머 리셋.

```python
# 현재 (문제)
else:
    self.alert_start_time = None   # 1프레임만 False여도 즉시 리셋
    self.alert_duration = 0.0
```

수정안: `DetectionState`에 `_not_still_count` 카운터를 추가,
**`still_reset_frames`(예: 3) 이상 연속 False여야만 타이머 리셋.**

```python
# 수정 후 (설계안)
else:
    self._not_still_count = getattr(self, '_not_still_count', 0) + 1
    if self._not_still_count >= self.reset_frames:  # 예: 3프레임
        self.alert_start_time = None
        self.alert_duration = 0.0
        self._not_still_count = 0
    # 카운터 미충족 시 타이머 유지 (아티팩트 1~2프레임 무시)
```

`reset_frames` 기본값 3 = 200ms × 3 = 600ms 연속 모션이어야 리셋.
이 정도면 MPEG 아티팩트(1~2프레임 노이즈)는 무시하고, 실제 화면 전환은 감지 가능.

> **설정 항목**: `still_reset_frames` — 감지 설정 탭에 추가 예정 (기본값 3, 범위 1~10)

---

## 6. 확장 Heartbeat 로그 출력 설계 (참고용)

**위치**: `ui/main_window.py` — `_run_detection()` 내 heartbeat 구간

```python
# 현재
if self._detection_count % 1500 == 0:
    self._logger.info(f"SYSTEM - 감지 정상 실행 중 ({elapsed_min}분 경과)")

# 수정 후
if self._detection_count % 1500 == 0:
    self._logger.info(f"SYSTEM - 감지 정상 실행 중 ({elapsed_min}분 경과)")
    # 각 ROI 상태 요약 (파일 로그 전용)
    for label, state in self._detector._black_states.items():
        ...  # dark_ratio, still_timer 등 출력
```

출력 예시:
```
[INFO] SYSTEM - 감지 정상 실행 중 (1435분 경과)
[INFO] DIAG - V2(2TV ON-AIR): black=66.2%[기준95%] still_timer=0.0s[기준60s] 직전리셋=0.4s전
[INFO] DIAG - A3(2TV ON-AIR): audio_ratio=0.1%[기준5%] timer=45.2s
```

---

## 7. 현재 kbs_config.json 주요 감지 파라미터

```json
"black_dark_ratio": 95.0,    // ← 이번 장애: 66%로 미달
"still_changed_ratio": 2.0,  // ← 이번 장애: MPEG 아티팩트로 지속 초과
"still_duration": 60,        // 스틸 알림까지 필요 누적 시간(초)
"black_duration": 20         // 블랙 알림까지 필요 누적 시간(초)
```

---

*이 파일은 다음 세션 시작 시 Claude에게 전달하여 작업을 이어갈 것.*
