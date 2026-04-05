# core/ 설계 원칙

> `alarm.py`, `detector.py`, `video_capture.py`, `signoff_manager.py` 수정 시 반드시 준수.

---

## alarm.py 핵심 설계 원칙

### 알림음 테스트 재생 (`play_test_sound` / `_play_test_worker`)

**절대 바꾸지 말아야 할 사항:**

1. **winsound를 sounddevice보다 먼저 시도한다**
   - sounddevice는 비기본 오디오 장치 또는 볼륨 0 환경에서 무음이 될 수 있음
   - winsound(`SND_FILENAME | SND_SYNC`)는 항상 시스템 기본 장치 기준 → 안정적
   - 순서를 바꾸면 테스트 버튼 무음 버그 재발

2. **`play_test_sound`에서 `threading.Event()`를 새 인스턴스로 생성한다**
   ```python
   self._stop_sound = threading.Event()  # ← 반드시 새 객체, clear()만 하면 안 됨
   ```
   - `clear()`만 하면 이전 스레드가 되살아나 새 스레드와 충돌함

3. **파일 경로는 반드시 `os.path.abspath`로 절대경로 변환 후 사용한다**
   - UI에서 저장되는 경로는 상대경로일 수 있음 → cwd 의존적이므로 절대경로 필수

4. **sounddevice `sd.play()` 후 반드시 `sd.wait()`를 호출한다**
   - `sd.play()`는 non-blocking → `sd.wait()` 없이 루프 돌면 재생이 덮어씌워져 무음
   - `sd.stop()`을 다른 스레드에서 호출하면 `sd.wait()`가 즉시 반환 → 루프 종료

5. **`_play_test_worker`는 `_play_sound_worker`와 별개로 유지한다**
   - 테스트(1회)와 알림(반복) 로직을 공유하면 반복/블로킹/중지 조건이 충돌함

### 실제 알림 반복 재생 (`_play_sound_worker`)
- **winsound → sounddevice → 내장음 순서** (테스트와 동일)
- winsound: `SND_ASYNC` + `wait(timeout=sound_duration)` 패턴으로 반복 재생
- sounddevice: `sd.play()` + `sd.wait()` 쌍, winsound 없을 때만 사용
- `_stop_playback()`: `_stop_sound.set()` + `sd.stop()`으로 즉시 중단

---

## 상단바 버튼 원칙

> 상단바에는 **"알림확인" 버튼 하나만** 존재한다. "알림 초기화" 류의 버튼을 추가하지 않는다.

### 이유
- "알림 초기화"(감지 상태·로그 집합 리셋)는 "알림확인"(소리·깜빡임 해제)과 체감 차이가 거의 없음
- 감지 카운터를 리셋해도 이상이 지속 중이면 200ms 후 즉시 재트리거 → 실질 효과 없음
- 진짜 전체 리셋이 필요하면 예약 재시작(프로세스 재기동)이 올바른 방법
- **운영자가 모르는 사이에 버튼이 추가되면 혼란을 유발함**

### 절대 하지 말 것
- 상단바에 "알림 초기화", "감지 리셋", "전체 초기화" 등 감지 상태를 소프트웨어적으로 리셋하는 버튼 추가

---

## 감지 루프 안정성 원칙

> 현장 운용 중 장기 실행 후 감지가 조용히 멈추는 버그(silent failure) 발생 이력이 있음.
> 아래 try-except 구조는 이를 방지하기 위한 것으로 **절대 삭제하지 않는다.**

### `MainWindow._run_detection()` — DIAG 블록 독립 보호 + 감지 루프 보호

```python
def _run_detection(self):
    if self._latest_frame is None:
        return
    if self._roi_overlay is not None:
        return

    self._detection_count += 1
    if self._detection_count % 150 == 0:       # 200ms × 150 ≈ 30초
        try:
            # SYSTEM-HB(스레드 수 포함) ~ DIAG-TELEGRAM 전체 로그 출력
            ...
        except Exception as _diag_e:
            _diag_e_type = type(_diag_e).__name__
            if _diag_e_type != getattr(self, "_diag_last_error_type", None):
                self._diag_last_error_type = _diag_e_type
                _log.error("DIAG 로깅 오류 (감지 계속): %s\n%s", _diag_e, traceback.format_exc())
            else:
                _log.error("DIAG 로깅 오류 반복 (감지 계속): %s", _diag_e)

    self._last_detection_time = time.time()    # DIAG 이후·감지 try 이전

    try:
        # ... 감지 로직 전체 ...
    except Exception as e:
        _log.error("감지 루프 오류 (silent fail 방지): %s", e)
```

- **DIAG 블록 독립 try-except**: SYSTEM-HB~DIAG-TELEGRAM 로그 출력 중 예외가 발생해도 감지 로직은 반드시 실행됨
  - 동일 예외 타입 반복 시 traceback 생략, 한 줄 요약만 출력 (로그 폭풍 방지)
  - SYSTEM-HB에 `threads=py:N/os:N` 포함 — OS 스레드 자원 고갈 진단용
- **`_last_detection_time` 위치**: DIAG 블록 이후·감지 try 이전 — DIAG 예외로 함수 조기 종료 시에도 health check가 오탐하지 않도록
- **감지 루프 보호**: 예외 발생 시 타이머는 살아있어 겉으론 정상처럼 보이지만 매 주기 fail → 감지 완전 중단 방지
- **이력**: 장기 실행(20h+) 후 DIAG 블록 예외 → 감지 skip 버그 발생 (2026-04-04, v1.6.12에서 수정)

### `VideoCaptureThread.run()` — while 루프 try-except 보호

```python
while self._running:
    try:
        # ... 연결/캡처 로직 전체 ...
    except Exception as e:
        self.status_changed.emit(f"캡처 스레드 오류: {e}")
        if cap is not None:
            cap.release()
            cap = None
        if was_connected:
            was_connected = False
            self.disconnected.emit()
        self.msleep(1000)
        continue
    self.msleep(33)
```

- **이유**: `cap.read()` 예외 시 스레드 크래시 → `frame_ready` 신호 없음 → `_latest_frame = None` → 감지 중단

### `Detector.detect_frame()` / `detect_audio_roi()` — ROI별 try-except

```python
for roi in rois:
    label = roi.label
    try:
        # ... ROI 처리 로직 ...
    except Exception as e:
        _log.error("detect_frame ROI[%s] 오류: %s", label, e)
```

- **이유**: 특정 ROI 예외가 전체 감지를 멈추지 않도록 격리
- `_log = logging.getLogger(__name__)` — 파일 상단에 선언

### `_update_summary()` / `_check_scheduled_restart()` — try-except 보호

- **이유**: `_run_detection()`과 마찬가지로 QTimer 콜백. unhandled exception 발생 시 타이머가 중단되지는 않지만
  콜백이 매 주기 실패하면 정파 상태 패널이 갱신되지 않거나 예약 재시작이 불능화됨
- 최외곽 try-except로 보호하고 except 블록에서 `_log.error()`만 호출 후 정상 반환

### `_on_frame_ready()` — 프레임 복사

```python
self._latest_frame = frame.copy()  # 캡처 스레드 버퍼 공유 방지
```

- **이유**: `cap.read()` 반환 배열이 캡처 스레드에서 재사용될 수 있음 → 감지 루프 처리 중 데이터 변조 방지

---

## 히스테리시스 원칙

> 히스테리시스 비대칭으로 인해 장기 실행 후 스틸 감지가 멈추는 버그 발생 이력이 있음.
> 캡처 카드 인코딩 노이즈로 인한 단일 프레임 글리치가 원인.

### `DetectionState.update()` — 경보 전/후 대칭 원칙 (절대 변경 금지)

- **경보 전 (not alerting)**: `reset_frames` 연속 정상이어야 타이머 리셋
- **경보 후 (alerting)**: **동일하게** `reset_frames` 연속 정상이어야 복구
- `_do_resolve()` 호출 시 반드시 `_last_reset_time`, `_last_reset_from` 업데이트 (DIAG 추적)
- `_not_still_count`는 `is_abnormal=True` 시 0으로 리셋, `is_abnormal=False` 시 공통 증가

**절대 하지 말 것**: 경보 상태에서 단일 프레임으로 즉시 `_do_resolve()` 호출 (히스테리시스 우회)

### `SignoffManager` 타이머 — 히스테리시스 원칙

- `_tick_preparation()`: `_video_enter_not_still` 카운터로 **3틱 연속** 비-스틸이어야 `_video_enter_start` 리셋
- `_tick_exit_preparation()`: `_video_exit_still` 카운터로 **3틱 연속** 스틸이어야 `_video_exit_start` 리셋
- 단일 틱의 `is_still` 변동으로 타이머를 즉시 리셋하면 안 됨

### `SignoffManager` 상태 진입 시 타이머 초기화 원칙

- **IDLE 진입 시**: `_transition_to()` 내에서 `_reset_enter_timers()` **자동 호출** (이전 주기 `_video_enter_start` stale 방지)
  - 이유: SIGNOFF→IDLE 전환 후 `_video_enter_start[gid]`가 이전 PREPARATION 사이클의 Unix 타임스탬프로 잔류.
    다음 날 PREPARATION 재진입 시 `v_elapsed = now - _video_enter_start`가 수만 초로 즉시 `still_trigger_sec` 초과
    → SIGNOFF 즉시 조기 전환되는 버그 발생 (2026-03-30 eval-freeze 분석에서 확인). `_transition_to()`에서 처리.
- **SIGNOFF 진입 시**: `_reset_exit_timers()` **반드시 호출** (이전 주기 `_video_exit_start` stale 방지)
  - 이유: 이전 주기에서 `end_time` 도달로 SIGNOFF→IDLE 시 exit 타이머가 초기화되지 않음.
    다음 주기 exit_prep_window 진입 시 `v_elapsed ≫ exit_trigger_sec` 조건이 즉시 충족되어
    정파가 즉시 조기 종료되는 버그 발생 (2026-03-27 조사에서 확인). `_transition_to()`에서 처리.
- **PREPARATION 진입 시**: `_dbg_prev_still` `None`으로 초기화 (이전 주기 잔류로 인한 오진단 로그 방지)
- **절대 하지 말 것**: IDLE 진입 시 `_reset_enter_timers()` 생략 / SIGNOFF 진입 시 `_reset_exit_timers()` 생략

---

## 정파모드 알림 억제 설계 원칙

> 정파모드에서 어떤 감지 유형을 억제할지는 **감지 유형별로 다르다.** 절대 일괄 억제하지 않는다.

### 감지 유형별 억제 규칙

| 감지 유형 | 억제 방식 | 이유 |
|-----------|-----------|------|
| 비디오 ROI (블랙/스틸) | `is_signoff_label(label)`로 **그룹별 개별 억제** | 정파 그룹 소속 label만 억제, 다른 그룹은 계속 감지 |
| 오디오 레벨미터 ROI | `is_signoff_label(label)`로 **그룹별 개별 억제** | 동일 |
| 임베디드 오디오 | **억제 없음** | 그룹 귀속 개념이 없는 단일 감지 → `is_any_signoff()` 조건 사용 금지 |

### 과거 버그 (재발 방지)

❌ `_on_embedded_silence` / `_on_audio_level_for_silence`에서 `is_any_signoff()` 사용
→ 한 채널만 정파여도 임베디드 오디오 알림 **전체** 차단됨 (v1.5.2에서 수정)
