# KBS Peacock 24/7 장기 실행 안정성 코드리뷰

> **목적**: 24시간 365일, 수년간 무중단 운영 시 발생할 수 있는 문제를 체계적으로 검토
> **사용법**: 각 단계를 새로운 Claude 대화 세션에서 독립적으로 수행. 이 문서를 컨텍스트로 제공하면 됨.
> **버전**: v1.6.0 기준 (2026-03-21)

---

## 진행 상황 요약

| 단계 | 주제 | 상태 | 발견/수정 |
|------|------|------|-----------|
| 1 | 메모리 누수 | [x] 완료 | 2건 수정 필요, 1건 예방 권장 |
| 2 | 스레드 안전성 | [x] 완료 | 0건 수정 필요, 전체 안전 확인 |
| 3 | 리소스 관리 | [x] 완료 | 1건 수정 필요 |
| 4 | 카운터/타이머 안정성 | [x] 완료 | 0건 수정 필요, 전체 안전 확인 |
| 5 | 예외 처리 및 복구 | [x] 완료 | 0건 수정 필요, 전체 안전 확인 |
| 6 | 디스크 공간 관리 | [x] 완료 | 3건 수정 완료 (로그 삭제 정책, 임시파일 정리, atomic write) |
| 7 | Qt 시그널/슬롯 안정성 | [x] 완료 | 2건 수정 완료 (deleteLater, 타이머 stop) |
| 8 | 종합 스트레스 시나리오 | [x] 완료 | 3건 수정 완료, 1건 수용 |

---

## 단계 1: 메모리 누수 검토

### 목적
Dict, List, Deque 등 컬렉션이 무한히 성장하지 않는지, numpy 배열 버퍼가 적절히 해제되는지 검토한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 1: 메모리 누수 검토"를 수행해줘.
각 TODO 항목을 순서대로 검토하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 검토 대상 파일
- `kbs_monitor/core/detector.py` — `_black_states`, `_still_states`, `_audio_level_states`, `_prev_frames`, `_audio_ratio_buffer`, `_near_miss_start`, `_last_raw`, `_tone_states`
- `kbs_monitor/core/telegram_notifier.py` — `_last_sent` dict
- `kbs_monitor/core/auto_recorder.py` — `_buffer`, `_audio_buffer`, `_record_queue`, `_audio_record_queue`
- `kbs_monitor/core/alarm.py` — `_active_alarms`, `_acknowledged_alarms`
- `kbs_monitor/core/signoff_manager.py` — 내부 상태 dict들
- `kbs_monitor/ui/main_window.py` — `_roi_label_to_name` 캐시, `_latest_frame`

### TODO 체크리스트

- [x] **1.1** `detector.py` — `update_roi_list()`에서 모든 `_*_states` dict의 stale 키를 정리하는지 확인. 누락된 dict가 없는지 `__init__`의 dict 목록과 대조
- [x] **1.2** `detector.py` — `_near_miss_start` dict가 무한히 성장할 수 있는지 확인. ROI가 항상 near-miss 경계에 있으면 항목이 영구 보존되는 패턴 검토
- [x] **1.3** `detector.py` — `_last_raw` dict가 정리되는지 확인. `update_roi_list()`에서 stale 키 삭제 여부 검토
- [x] **1.4** `telegram_notifier.py` — `_last_sent` dict (쿨다운 캐시)에 만료/정리 로직이 있는지 확인. `{alarm_type}_{label}` 키가 무한 축적되는지 검토
- [x] **1.5** `auto_recorder.py` — `_record_queue`와 `_audio_record_queue`에 `maxlen`이 설정되어 있는지 확인. 긴 알람 시퀀스에서 메모리 스파이크 가능성 검토
- [x] **1.6** `auto_recorder.py` — `_buffer`(비디오)와 `_audio_buffer`(오디오) pre-buffer의 `maxlen` 설정 확인
- [x] **1.7** `alarm.py` — `_active_alarms`, `_acknowledged_alarms` set이 무한히 성장하지 않는지 확인. `resolve()` 호출 시 항목이 제거되는지 검토
- [x] **1.8** `main_window.py` — `_roi_label_to_name` dict 캐시가 ROI 변경 시 갱신되는지 확인
- [x] **1.9** `signoff_manager.py` — 내부 그룹별 상태 dict가 그룹 삭제 시 정리되는지 확인
- [x] **1.10** `detector.py` — `_prev_frames` (numpy float32 배열) 메모리 크기 계산: ROI 수 × ROI 크기 × 4바이트. 최악의 경우 메모리 사용량 추정

### 검토 방법
각 dict/collection에 대해:
1. 항목이 **추가**되는 지점 모두 찾기
2. 항목이 **삭제**되는 지점 모두 찾기
3. 삭제 없이 추가만 되는 경로가 있는지 확인
4. 있다면: 시간당/일당 예상 증가량 계산

### 결과 기록란 (2026-03-21 검토)

#### 발견된 문제

**🔴 수정 필요 (2건)**

| # | 파일 | 대상 | 문제 | 심각도 |
|---|------|------|------|--------|
| 1.3 | `detector.py` | `_last_raw` dict | `update_roi_list()`에서 stale 키 미정리. ROI 삭제 후에도 진단 데이터 잔존 | 낮음 (일관성) |
| 1.4 | `telegram_notifier.py` | `_last_sent` dict | 만료/정리 로직 **완전 부재**. `{alarm_type}_{label}` 키가 영구 보존됨 | 중간 |

**⚠️ 예방 권장 (1건)**

| # | 파일 | 대상 | 문제 | 심각도 |
|---|------|------|------|--------|
| 1.1 | `detector.py` | `_tone_states` dict | `update_roi_list()`에서 정리 코드 누락. 현재 미사용이라 실질적 영향 없으나 향후 톤 감지 구현 시 누수 위험 | 예방 |

**✅ 안전 확인 (7건)**

| # | 파일 | 대상 | 결과 |
|---|------|------|------|
| 1.2 | `detector.py` | `_near_miss_start` | ✅ 안전 — dict 크기 = active ROI 수로 제한. `pop(label, None)`으로 정상화 시 삭제 |
| 1.5 | `auto_recorder.py` | `_record_queue`, `_audio_record_queue` | ✅ 안전 — 녹화 시작 시 `clear()`, 녹화 중 `popleft()`로 소비 |
| 1.6 | `auto_recorder.py` | `_buffer`, `_audio_buffer` | ✅ 안전 — `deque(maxlen=N)` 사용. 비디오 ~55프레임, 오디오 ~230청크 고정 |
| 1.7 | `alarm.py` | `_active_alarms`, `_acknowledged_alarms` | ✅ 안전 — `resolve()`에서 `discard()`, `resolve_all()`에서 `clear()`. 최대 ~120항목 |
| 1.8 | `main_window.py` | `_roi_label_to_name` | ✅ 안전 — `_run_detection()` 내 로컬 변수로 매 감지 주기마다 재생성·GC |
| 1.9 | `signoff_manager.py` | 15개 per-group dict | ✅ 안전 — gid=1,2 고정 (최대 30항목). 그룹 삭제 기능 없으나 실질적 누수 없음 |
| 1.10 | `detector.py` | `_prev_frames` (numpy float32) | ✅ 안전 — 일반 ~2MB, 최악(16 ROI × 500×300) ~10MB. `update_roi_list()`에서 정리됨 |

#### 수정 방안

**1.3 `detector.py` — `_last_raw` stale 키 정리 추가**
`update_roi_list()`의 `_audio_level_states` 정리 블록 뒤에 추가:
```python
for label in list(self._last_raw.keys()):
    if label not in labels:
        del self._last_raw[label]
```

**1.4 `telegram_notifier.py` — `_last_sent` 만료 정리 추가**
`send_alert()` 시작부에 24시간 이상 된 항목 정리:
```python
now = time.time()
cutoff = now - 86400  # 24시간
for key in list(self._last_sent.keys()):
    if self._last_sent[key] < cutoff:
        del self._last_sent[key]
```

**1.1 `detector.py` — `_tone_states` 정리 추가 (예방)**
`update_roi_list()`의 `_last_raw` 정리 뒤에 추가:
```python
for label in list(self._tone_states.keys()):
    if label not in labels:
        del self._tone_states[label]
```

#### 메모
- `_near_miss_start` (1.2): 30초마다 near-miss 경고 로그를 남기는 설계는 의도적. 구조적으로 dict 크기 = ROI 수 이하
- `_prev_frames` (1.10): 최악 시나리오에서도 ~20MB 수준. 메모리 압박 없음
- `signoff_manager.py` (1.9): 향후 3+ 그룹 지원 시 `remove_group()` 메서드 추가 필요
- `alarm.py` (1.7): `_acknowledged_alarms`는 `acknowledge_all()` 시 `_active_alarms` 복사본. 알람 해제 시 `discard()`로 제거

---

## 단계 2: 스레드 안전성 검토

### 목적
멀티스레드 환경에서 공유 가변 상태(shared mutable state)에 대한 락(lock) 보호가 적절한지, 레이스 컨디션이 없는지 검토한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 2: 스레드 안전성 검토"를 수행해줘.
각 TODO 항목을 순서대로 검토하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 스레드 구조 참고
```
메인 스레드 (Qt GUI)
├── _detect_timer (200ms) → _run_detection()
├── _summary_timer (1000ms) → _update_summary()
└── UI 이벤트 처리

VideoCaptureThread (QThread)
└── frame_ready 시그널 → 메인 스레드

AudioMonitorThread (QThread)
└── level_updated, audio_chunk 시그널 → 메인 스레드

TelegramNotifier._worker_thread (threading.Thread, daemon)
└── _queue에서 꺼내서 HTTP 전송

AlarmSystem._sound_thread (threading.Thread, daemon)
└── winsound/sounddevice 재생

AutoRecorder._record_thread (threading.Thread, daemon)
└── 녹화 큐에서 꺼내서 MP4 인코딩
```

### 검토 대상 파일
- `kbs_monitor/core/alarm.py` — `_active_alarms`, `_acknowledged_alarms`, `_sound_files` 크로스스레드 접근
- `kbs_monitor/core/auto_recorder.py` — `_buffer`, `_audio_buffer` 락 사용 패턴, `configure()` 중 버퍼 재할당
- `kbs_monitor/core/telegram_notifier.py` — `_last_sent` 크로스스레드 접근, 워커 재시작 레이스
- `kbs_monitor/core/detector.py` — `_*_states` dict가 설정 변경(`update_roi_list`)과 감지 루프에서 동시 접근
- `kbs_monitor/core/video_capture.py` — `_port`, `_reconnect` 뮤텍스 보호 패턴
- `kbs_monitor/core/audio_monitor.py` — 스트림 상태 관리
- `kbs_monitor/ui/main_window.py` — 다른 객체의 private 멤버 직접 접근 (`_active_alarms`, `_worker_thread` 등)

### TODO 체크리스트

- [x] **2.1** `alarm.py` — `_active_alarms` set이 메인 스레드(`trigger`/`resolve`)와 사운드 스레드에서 동시 접근되는지 확인. 락 보호 여부 검토
- [x] **2.2** `alarm.py` — `_sound_files` dict가 메인 스레드(`set_sound_file`)에서 수정되고 워커 스레드(`_play_sound_worker`)에서 읽히는 패턴 검토
- [x] **2.3** `alarm.py` — `_stop_sound` Event 객체의 `set()`/`clear()` 타이밍 레이스 검토
- [x] **2.4** `auto_recorder.py` — `configure()` 메서드에서 `_buffer = deque(old, maxlen=new)` 재할당 시 `push_frame()`/`push_audio()`가 동시에 기존 버퍼에 쓰는 레이스 확인
- [x] **2.5** `auto_recorder.py` — `_buffer_lock`, `_audio_lock` 사용이 모든 접근 경로에서 일관되게 적용되는지 확인
- [x] **2.6** `telegram_notifier.py` — `notify()` 메서드에서 워커 스레드 사망 감지 + 재시작 시 두 번 호출될 경우 이중 스레드 생성 가능성 검토
- [x] **2.7** `telegram_notifier.py` — `_last_sent` dict가 메인 스레드와 워커 스레드에서 동시 접근되는지 확인
- [x] **2.8** `detector.py` — `update_roi_list()`가 설정 다이얼로그에서 호출될 때, 동시에 `_run_detection()`이 `_black_states` 등을 순회하고 있을 가능성 검토
- [x] **2.9** `video_capture.py` — `QMutex` 보호 패턴이 `set_port()` ↔ `run()` 간 올바른지 확인
- [x] **2.10** `main_window.py` — 다른 객체의 private 멤버 직접 접근 (`self._alarm._active_alarms`, `self._telegram._worker_thread` 등) 목록 작성 및 스레드 안전성 평가

### 검토 방법
각 공유 상태에 대해:
1. **어느 스레드**에서 읽기/쓰기하는지 매핑
2. **동시 접근** 가능한 경로가 있는지 확인
3. 있다면: 데이터 타입의 원자성(atomicity) 확인 (Python GIL 고려)
4. GIL만으로 보호 불가능한 복합 연산(check-then-act 등)이 있는지 확인

### 결과 기록란 (2026-03-21 검토)

#### 핵심 구조적 안전성

이 프로젝트의 스레드 모델은 **Qt 메인 이벤트 루프 중심 설계**를 따른다:
- `Detector`, `AlarmSystem`, `AutoRecorder.trigger()`, `TelegramNotifier.notify()` 등 감지/알림 핵심 로직은 **모두 메인 스레드(Qt 타이머 콜백)에서 실행**
- `VideoCaptureThread`, `AudioMonitorThread`는 QThread로 데이터 생산만 담당하고, **Qt 시그널(queued connection)**을 통해 메인 스레드로 전달
- 별도 `threading.Thread` (사운드, 텔레그램, 녹화)는 daemon으로 **소비 전용** 역할

이 구조 덕분에 공유 가변 상태(shared mutable state)의 크로스스레드 동시 접근이 구조적으로 차단됨.

#### 발견된 문제

**🔴 수정 필요: 0건**

**⚠️ 예방 권장: 0건**

#### ✅ 안전 확인 (10건)

| # | 파일 | 대상 | 결과 |
|---|------|------|------|
| 2.1 | `alarm.py` | `_active_alarms` set | ✅ 안전 — `trigger()`/`resolve()`/`acknowledge_all()` 모두 메인 스레드. 사운드 워커는 `_active_alarms`에 접근하지 않음 |
| 2.2 | `alarm.py` | `_sound_files` dict | ✅ 안전 — `set_sound_file()`(메인)에서 쓰기, `_get_sound_path()`(워커)에서 읽기. 기존 키의 값 변경만 발생하므로 dict 크기 불변 → 반복 순회 안전. CPython GIL로 원자적 |
| 2.3 | `alarm.py` | `_stop_sound` Event | ✅ 안전 — `set()`/`clear()`는 메인 스레드, `is_set()`/`wait()`는 워커. `threading.Event` 내부 lock으로 보호. `play_test_sound()`는 새 Event 객체 생성(CLAUDE.md 규칙), `_play_sound()`는 `is_alive()` 가드로 이중 시작 방지 |
| 2.4 | `auto_recorder.py` | `configure()` 버퍼 재할당 | ✅ 안전 — `_buffer_lock`/`_audio_lock`으로 재할당 보호. `push_frame()`/`push_audio()`도 동일 락 사용 |
| 2.5 | `auto_recorder.py` | `_buffer_lock`/`_audio_lock` 일관성 | ✅ 안전 — `_buffer`: configure(✅) push_frame(✅) trigger(✅). `_audio_buffer`: configure(✅) push_audio(✅) trigger(✅). `_record_queue`/`_audio_record_queue`는 락 없으나 CPython deque `append()`/`popleft()`가 GIL 원자적이고, `trigger()→clear()` 시점에 `_recording=False`이므로 동시 append 불가 |
| 2.6 | `telegram_notifier.py` | 워커 스레드 이중 생성 | ✅ 안전 — `notify()`는 메인 스레드의 Qt 타이머 콜백 경로에서만 호출. Qt 이벤트 루프 단일 스레드 실행으로 두 `notify()`의 동시 실행 불가 |
| 2.7 | `telegram_notifier.py` | `_last_sent` dict | ✅ 안전 — `notify()`(메인 스레드)에서만 읽기/쓰기. 워커 스레드(`_send()`)에서는 접근하지 않음 |
| 2.8 | `detector.py` | `_*_states` dict 동시 접근 | ✅ 안전 — `update_roi_list()`(설정 콜백)과 `detect_frame()`(감지 타이머) 모두 메인 스레드 Qt 이벤트 루프에서 순차 실행. 동시 접근 구조적 불가 |
| 2.9 | `video_capture.py` | QMutex 보호 패턴 | ✅ 안전 — `set_port()`/`set_video_file()`(메인)과 `run()`(캡처 스레드) 간 `_port`, `_video_file`, `_reconnect` 모두 `QMutexLocker`로 보호. `_running` bool은 GIL 원자적 |
| 2.10 | `main_window.py` | private 멤버 직접 접근 | ✅ 안전 — 9개 접근 모두 DIAG 로그 목적의 **읽기 전용**, 메인 스레드 내에서만 실행. `_telegram._worker_thread.is_alive()`와 `_telegram._queue.qsize()`는 스레드 세이프 API |

#### private 멤버 직접 접근 목록 (2.10)

| 줄 | 접근 대상 | 용도 | 위험도 |
|----|-----------|------|--------|
| L253 | `self._detector._last_raw` | DIAG heartbeat 로그 | 없음 (메인↔메인) |
| L254 | `self._detector._still_states` | DIAG heartbeat 로그 | 없음 (메인↔메인) |
| L280 | `self._alarm._active_alarms` | DIAG-ALARM 로그 | 없음 (메인↔메인) |
| L308-309 | `self._detector._audio_level_states`, `_audio_ratio_buffer` | DIAG-AUDIO 로그 | 없음 (메인↔메인) |
| L323-326 | `self._detector.embedded_alerting`, `_embedded_alert_start` | DIAG-AUDIO 로그 | 없음 (메인↔메인) |
| L338 | `self._telegram._enabled` | DIAG-TELEGRAM 로그 | 없음 (메인↔메인) |
| L339 | `self._telegram._worker_thread.is_alive()` | DIAG-TELEGRAM 로그 | 없음 (`is_alive()` 스레드세이프) |
| L340 | `self._telegram._queue.qsize()` | DIAG-TELEGRAM 로그 | 없음 (`qsize()` 스레드세이프) |

> **참고**: 캡슐화 관점에서는 읽기 전용 접근자(property 또는 getter)를 추가하는 것이 바람직하나, 모두 DIAG 로그 전용이고 스레드 안전성 문제가 없으므로 우선순위 낮음.

#### 메모
- 이 프로젝트의 스레드 안전성은 **Qt 시그널/슬롯 메커니즘**에 크게 의존함. 향후 백그라운드 스레드에서 `Detector`나 `AlarmSystem` 메서드를 직접 호출하는 코드가 추가되면 반드시 락 보호 또는 Qt 시그널 경유 필요
- `auto_recorder.py`의 `_record_queue`/`_audio_record_queue`는 명시적 락 없이 CPython GIL에 의존. 향후 PyPy 등 GIL-free 인터프리터로 전환 시 위험. 현재 CPython 3.10+ 한정이므로 문제없음
- `alarm.py`의 `_sound_files` dict 순회(`_get_sound_path()`)는 현재 키 추가/삭제가 없어 안전하나, 향후 알림음 타입 동적 추가 시 복사본 순회(`dict(self._sound_files).values()`) 필요
- `video_capture.py`의 `_running` 플래그는 `volatile` 보장 없으나, CPython에서 bool 할당은 원자적이고 QThread.wait()와 결합되어 안전

---

## 단계 3: 리소스 관리 검토

### 목적
파일 핸들, OpenCV 캡처 장치, 오디오 스트림, 서브프로세스 등 시스템 리소스가 적절히 해제되는지 검토한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 3: 리소스 관리 검토"를 수행해줘.
각 TODO 항목을 순서대로 검토하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 검토 대상 파일
- `kbs_monitor/core/video_capture.py` — OpenCV `VideoCapture` 수명 관리
- `kbs_monitor/core/audio_monitor.py` — sounddevice 스트림 열기/닫기
- `kbs_monitor/core/auto_recorder.py` — ffmpeg 서브프로세스, WAV 임시 파일, MP4 파일
- `kbs_monitor/core/alarm.py` — winsound/sounddevice 오디오 리소스
- `kbs_monitor/utils/logger.py` — 로그 파일 핸들 로테이션
- `kbs_monitor/ui/main_window.py` — `closeEvent()` 종료 절차

### TODO 체크리스트

- [x] **3.1** `video_capture.py` — `cap.release()` 호출 경로 모두 확인: 정상 종료, 포트 변경, 예외 발생 시. 누락된 경로가 있으면 Windows에서 캡처 장치 잠금 발생
- [x] **3.2** `video_capture.py` — `cap.read()` 실패 시 (연결 끊김) 리소스 해제 후 재연결 로직이 올바른지 확인
- [x] **3.3** `audio_monitor.py` — `sounddevice.RawInputStream`/`RawOutputStream` 열기 실패 시 부분 리소스 해제 확인. 출력 스트림만 실패할 경우 입력 스트림도 정리되는지 검토
- [x] **3.4** `audio_monitor.py` — `finally` 블록에서 스트림 `close()` 호출이 누락 없는지 확인
- [x] **3.5** `auto_recorder.py` — ffmpeg `subprocess.run()` 타임아웃(120s) 후 프로세스가 좀비로 남지 않는지 확인
- [x] **3.6** `auto_recorder.py` — 임시 파일 (`_vtmp.mp4`, `_atmp.wav`) 정리 로직 검토. 프로그램 비정상 종료 시 임시 파일 축적 가능성
- [x] **3.7** `auto_recorder.py` — WAV 파일 쓰기에 context manager(`with`)가 사용되는지, 예외 시 파일 핸들 누수 확인
- [x] **3.8** `logger.py` — 일별 로테이션 시 이전 `FileHandler.close()` → `removeHandler()` 순서가 올바른지 확인. 핸들 누수 방지
- [x] **3.9** `alarm.py` — `sounddevice.play()`/`sounddevice.stop()` 호출 후 오디오 장치가 올바르게 해제되는지 확인
- [x] **3.10** `main_window.py` `closeEvent()` — 모든 스레드 `stop()` + `wait()` 호출 확인. 타임아웃 후에도 리소스가 해제되는지 검토

### 검토 방법
각 리소스에 대해:
1. **획득** 시점 찾기
2. **해제** 시점 찾기 (정상 경로 + 예외 경로)
3. `try-finally` 또는 context manager로 보호되는지 확인
4. 해제 실패 시 재시도/로깅이 있는지 확인

### 결과 기록란 (2026-03-21 검토)

#### 발견된 문제

**🔴 수정 필요 (1건)**

| # | 파일 | 대상 | 문제 | 심각도 |
|---|------|------|------|--------|
| 3.6 | `auto_recorder.py` | `_vtmp.mp4`, `_atmp.wav` 임시 파일 | `_record_worker()`의 내부 try-finally(L250-294)는 writer/wav_file만 보호. 이후 임시 파일 삭제 코드(L297-326)가 finally 밖에 있어, while 루프 중 예외 발생 → finally에서 writer/wav 정리 후 함수 종료 → **임시 파일 잔존**. 녹화가 잦은 환경에서 `_vtmp.mp4` + `_atmp.wav` 파일이 누적됨 | 중간 |

**✅ 안전 확인 (9건)**

| # | 파일 | 대상 | 결과 |
|---|------|------|------|
| 3.1 | `video_capture.py` | `cap.release()` 경로 | ✅ 안전 — 5개 경로 모두 확인: ①포트 변경(L65) ②isOpened 실패(L93) ③30프레임 연속 실패(L111) ④예외(L122, try-except 보호) ⑤정상 종료(L138). 모든 경로에서 `cap = None`으로 이중 release 방지 |
| 3.2 | `video_capture.py` | 재연결 로직 | ✅ 안전 — `cap.release()` → `cap = None` → 다음 루프에서 자동 재연결. 예외 경로에서도 `msleep(1000)` 후 재시도 |
| 3.3 | `audio_monitor.py` | 부분 리소스 해제 | ✅ 안전 — 출력 스트림 실패 시 `output_stream = None`, 입력 스트림은 finally에서 정리. 입력 스트림 실패 시 `stream = None`(초기값), finally에서 None 체크로 skip |
| 3.4 | `audio_monitor.py` | finally 스트림 close | ✅ 안전 — `stop()` + `close()` 모두 호출, 각각 try-except 보호. `stream`/`output_stream` 모두 L72-73에서 None 초기화 |
| 3.5 | `auto_recorder.py` | ffmpeg 좀비 프로세스 | ✅ 안전 — Python 3의 `subprocess.run()`은 `TimeoutExpired` 시 내부에서 `process.kill()` + `process.communicate()` 자동 호출 후 예외 재발생 |
| 3.7 | `auto_recorder.py` | WAV 파일 핸들 | ✅ 안전 — `with` 문 미사용이나 try-finally(L291-294)에서 `wav_file.close()` 보장. `wav_file`은 None 초기화 + None 체크로 미할당 시에도 안전 |
| 3.8 | `logger.py` | FileHandler 로테이션 | ✅ 안전 — `h.close()` → `removeHandler()` 순서 올바름. `list()` 복사본 순회. 메인 스레드에서만 호출되어 레이스 없음 |
| 3.9 | `alarm.py` | sounddevice 장치 해제 | ✅ 안전 — `sd.play()` + `sd.wait()` 쌍 사용, `sd.stop()`으로 즉시 중지 가능. PortAudio 기반으로 `stop()` 호출 시 자동 해제 |
| 3.10 | `main_window.py` | closeEvent() 종료 | ✅ 안전 — QThread 2개: `stop()` + `wait(3000)`. TelegramNotifier: 센티넬 + `join(5.0)`. AutoRecorder/AlarmSystem: daemon 스레드로 프로세스 종료 시 자동 정리 |

#### 수정 방안

**3.6 `auto_recorder.py` — 임시 파일 정리를 전체 try-finally로 보호**

현재 `_record_worker()`의 임시 파일 삭제 코드(L297-326)가 내부 try-finally 밖에 있어, while 루프 중 예외 시 실행되지 않음. 해결: 전체 함수를 outer try-finally로 감싸서 임시 파일 삭제 보장.

```python
def _record_worker(self, pre_frames, pre_audio, filepath):
    base = filepath[:-4] if filepath.endswith(".mp4") else filepath
    vtmp = base + "_vtmp.mp4"
    atmp = base + "_atmp.wav"

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vtmp, fourcc, self._out_fps, (self._out_w, self._out_h))
    if not writer.isOpened():
        return

    has_audio = False
    wav_file = None
    merged = False

    try:                                      # ← outer try
        try:                                  # ← inner try (기존)
            wav_file = wave.open(atmp, "wb")
            # ... (기존 코드 동일) ...
        finally:
            writer.release()
            if wav_file is not None:
                wav_file.close()

        # ffmpeg 합성 (기존 L297~ 코드)
        if has_audio:
            # ... (기존 코드 동일) ...
            merged = self._merge_with_ffmpeg(vtmp, atmp, filepath, audio_offset)
        else:
            merged = False

    finally:                                  # ← outer finally: 임시 파일 무조건 정리
        if merged:
            try:
                os.remove(vtmp)
            except Exception:
                pass
        else:
            try:
                if os.path.exists(vtmp):
                    os.rename(vtmp, filepath)
            except Exception:
                pass
        try:
            if os.path.exists(atmp):
                os.remove(atmp)
        except Exception:
            pass
```

#### 메모
- `video_capture.py` (3.1): `stop()` → `_running = False` + `wait(3000)` 후 QThread 종료. 타임아웃 시 루프 다음 `msleep(33)` 후 조건 체크에서 종료되므로 실질적으로 ~3033ms 이내 정상 종료
- `audio_monitor.py` (3.3-3.4): sounddevice 스트림은 `try-finally` 패턴으로 완벽하게 보호됨. 출력 스트림 실패 시에도 입력 스트림만으로 정상 동작 (패스스루만 비활성화)
- `auto_recorder.py` (3.5): `subprocess.run()`이 아닌 `Popen()`으로 직접 관리할 경우 타임아웃 시 수동 `kill()` 필요. 현재 `run()` 사용은 올바른 선택
- `auto_recorder.py` (3.6): 비정상 종료(전원 차단 등) 시에는 어떤 코드로도 임시 파일 정리 불가. 시작 시 `recordings/` 디렉토리에서 `*_vtmp.mp4`, `*_atmp.wav` 패턴을 정리하는 로직 추가도 고려할 수 있으나, 녹화 빈도와 파일 크기(~수 MB)를 감안하면 우선순위 낮음
- `main_window.py` (3.10): AlarmSystem에 명시적 `stop()` 메서드가 없으나 사운드 스레드가 daemon이므로 프로세스 종료 시 자동 정리됨. 향후 non-daemon 스레드 추가 시 `closeEvent()`에 정리 코드 필요

---

## 단계 4: 카운터/타이머 안정성 검토

### 목적
정수 카운터 오버플로우, 부동소수점 누적 오차, 시간 관련 엣지 케이스가 수년 운영에 영향을 미치지 않는지 검토한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 4: 카운터/타이머 안정성 검토"를 수행해줘.
각 TODO 항목을 순서대로 검토하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 검토 대상 파일
- `kbs_monitor/ui/main_window.py` — `_detection_count` 카운터
- `kbs_monitor/core/detector.py` — `DetectionState._resolve_count`, `alert_duration` 누적
- `kbs_monitor/core/audio_monitor.py` — `_silence_duration` 부동소수점 누적
- `kbs_monitor/core/signoff_manager.py` — 시간 비교 로직, 요일/시간 롤오버
- `kbs_monitor/utils/logger.py` — 자정 날짜 변경 로테이션

### TODO 체크리스트

- [x] **4.1** `main_window.py` — `_detection_count` (int, 200ms마다 +1): Python 3 arbitrary precision이므로 오버플로우는 없지만, `% 1500`과 `// 1500 * 5` 연산이 매우 큰 수에서도 정확한지 확인. 주기적 리셋이 필요한지 평가
- [x] **4.2** `audio_monitor.py` — `_silence_duration += chunk_duration` (float, 약 0.023초씩 누적): 1년 연속 무음 시 부동소수점 정밀도 손실 계산. `time.time()` 기반 벽시계 방식으로 대체 필요성 평가
- [x] **4.3** `detector.py` — `DetectionState.alert_duration` 누적 패턴: 경보 지속 중 `time.time() - _last_update` 차이값이 누적되는 구조인지, 아니면 벽시계 기반인지 확인
- [x] **4.4** `detector.py` — `_resolve_count` 카운터: 수년간 누적 시 DIAG 로그 출력에 영향이 있는지 확인
- [x] **4.5** `signoff_manager.py` — `datetime.now().strftime("%H:%M")` 문자열 비교: 자정(00:00) 전후 롤오버, DST(서머타임) 변경 시 동작 확인
- [x] **4.6** `signoff_manager.py` — `weekday()` 기반 요일 판단: 자정 직후 요일 변경 타이밍 문제 확인
- [x] **4.7** `logger.py` — `_rotate_if_needed()`: 자정에 날짜 변경 감지가 `_run_detection()`과 동시에 호출될 때 레이스 가능성 검토
- [x] **4.8** `detector.py` — `time.time()` 사용처 전수 조사: NTP 시간 점프(시스템 시계 동기화로 인한 급격한 시간 변경)가 감지 로직에 미치는 영향 평가
- [x] **4.9** QTimer 정밀도: `_detect_timer`(200ms)와 `_summary_timer`(1000ms)의 장기 드리프트가 감지 품질에 영향을 미치는지 평가

### 검토 방법
각 카운터/타이머에 대해:
1. 초기값과 증가 패턴 확인
2. 리셋 조건이 있는지 확인
3. 최악의 경우 값 범위 계산 (1년, 5년, 10년)
4. 해당 값이 비교/연산에 사용될 때 정밀도 문제가 있는지 확인

### 검토 결과

#### 4.1 `_detection_count` 정수 카운터 — 문제 없음 (경미한 개선 권장)

**현황** (main_window.py:248-250):
```python
self._detection_count += 1
if self._detection_count % 1500 == 0:
    elapsed_min = self._detection_count // 1500 * 5
```

**분석**:
- Python 3 `int`는 arbitrary precision → 오버플로우 불가
- `% 1500`, `// 1500` 연산은 bigint에서도 정확 (Python 정수 나눗셈은 항상 정확)
- 1년 연속 운영: `200ms × 365일 = 157,680,000` → 메모리 ~28바이트 (무시 가능)
- 10년: ~1,576,800,000 → 여전히 ~32바이트

**하트비트 로그 경과 시간 표기**: `_detection_count // 1500 * 5`로 분 단위 계산.
1년 후 `elapsed_min = 525,600분` — 정확하고 읽기에도 문제 없음.

**결론**: 리셋 불필요. 현재 구현이 안전함.

**경미한 개선 권장**: 경과 시간 표기가 `525600분 경과`처럼 큰 수가 되면 가독성이 떨어짐.
`divmod`로 시/분 또는 일/시 형식으로 변환하면 로그 분석이 편해지나, 기능에는 영향 없으므로 선택적.

---

#### 4.2 `_silence_duration` 부동소수점 누적 — ⚠️ 이론적 정밀도 손실, 실질 영향 없음

**현황** (audio_monitor.py:139):
```python
chunk_duration = self.CHUNK / self.SAMPLE_RATE  # 1024/44100 ≈ 0.02322
self._silence_duration += chunk_duration
```

**정밀도 분석**:
- IEEE 754 double: 유효숫자 약 15~16자리
- 1시간 연속 무음: `_silence_duration ≈ 3,600.0` → 15자리 내 (정밀)
- 1일 연속 무음: `≈ 86,400.0` → 여전히 정밀
- 1년 연속 무음: `≈ 31,536,000.0` (8자리 정수부) → 소수점 7자리까지 정밀
  - chunk_duration ≈ 0.023초 → 소수점 3자리 수준이면 충분
  - ∴ 정밀도 손실이 실제 동작에 영향을 미치지 않음
- 10년: `≈ 315,360,000.0` (9자리) → 소수점 6~7자리 정밀 → 여전히 충분

**리셋 조건**: 소리가 감지되면 즉시 `_silence_duration = 0.0` (L142) → 현실적으로 1년 연속 무음 시나리오는 발생 불가

**결론**: 이론적 정밀도 손실은 존재하나, (1) 리셋 빈도가 높고 (2) 임계값 비교에 밀리초 정밀도가 불필요하므로 실질적 문제 없음. 수정 불필요.

---

#### 4.3 `DetectionState.alert_duration` — 문제 없음 (벽시계 기반)

**현황** (detector.py:51-53):
```python
if self.alert_start_time is None:
    self.alert_start_time = now          # time.time() 기록
self.alert_duration = now - self.alert_start_time  # 매번 재계산
```

**분석**:
- `alert_duration`은 **누적 합산이 아닌 벽시계 차이** (`now - start_time`)로 매번 재계산
- 부동소수점 누적 오차 없음 — 두 timestamp의 차이만 계산
- `time.time()`은 epoch 기준 float → 2026년 기준 약 `1.77 × 10^9` (10자리)
  - 두 값의 차이는 초 단위 소수점 6~7자리까지 정밀 (마이크로초 수준)
- `_do_resolve()` 시 `alert_start_time = None`, `alert_duration = 0.0`으로 완전 초기화

**결론**: 안전한 설계. 수정 불필요.

---

#### 4.4 `_resolve_count` 카운터 — 문제 없음

**현황** (detector.py:104):
```python
self._resolve_count += 1  # _do_resolve() 호출 시마다 증가
```

**DIAG 로그 출력** (main_window.py:267, 272):
```python
resolve_cnt = still_state._resolve_count if still_state else 0
_log.info("DIAG - %s: ... [%s/resolve=%d/start=%s]", ..., resolve_cnt, ...)
```

**분석**:
- Python 3 `int` → 오버플로우 없음
- DIAG 로그에서 `%d` 포맷 → 어떤 크기든 정확히 출력
- 최악의 경우 (200ms마다 resolve): 1년 ≈ 157,680,000 → 로그 출력 정상
- `reset()` 호출 시 0으로 리셋됨 (detector.py:122)

**결론**: 문제 없음. 수정 불필요.

---

#### 4.5 `strftime("%H:%M")` 문자열 비교 — 문제 없음

**현황** (signoff_manager.py:482-484):
```python
now = datetime.datetime.now()
weekday = now.weekday()
current_time = now.strftime("%H:%M")  # "00:00" ~ "23:59"
```

**자정 롤오버 분석**:
- `_is_in_time_range()` (L695-715): `end_next_day=True` 경우 자정 교차 처리 구현됨
  - `current_time >= start` (당일 부분) + `current_time < end` (익일 부분) 분리 판단
  - 예: start="23:00", end="06:00" → "23:00"~"23:59"는 당일, "00:00"~"05:59"는 익일
- `_is_in_prep_window()` (L596-618): prep_start > end_time인 경우도 별도 자정 교차 처리

**DST(서머타임) 분석**:
- 한국(KST)은 서머타임 미사용 → 실질적 영향 없음
- 만약 DST 적용 지역에서 사용 시: `datetime.now()`는 로컬 시간 → DST 전환 시 시간이 1시간 건너뛰거나 반복
  - 건너뜀(spring forward): 02:00 → 03:00 → 그 사이 시간대 정파 구간이 단축됨
  - 반복(fall back): 01:00 → 01:00 → 같은 시간이 두 번 → 정파 구간이 연장됨
  - 한국 운용이므로 현재 문제 아님

**"%H:%M" 정밀도**: 1분 단위 → 초 단위 정밀도 없음. 의도된 설계 (정파 스케줄은 분 단위).

**결론**: 한국 운용 기준 문제 없음. DST 미적용 지역이므로 안전.

---

#### 4.6 `weekday()` 요일 판단 — 문제 없음

**현황** (signoff_manager.py:482-484):
```python
now = datetime.datetime.now()
weekday = now.weekday()  # 0=월, 6=일
```

**자정 직후 요일 변경 분석**:
- `_tick()`은 1초마다 호출되므로 자정(00:00:00) 직후 첫 `_tick()`에서 새로운 weekday 반영
- 최대 지연: ~1초 (QTimer 간격) → 실질적 영향 없음
- 자정 교차 스케줄: `end_next_day=True` + `(weekday - 1) % 7` (L706)로 전날 요일 확인 → 올바름
  - 예: 화→수 자정 교차 시, 수요일 "00:30"에 `weekday=2(수)`, `prev_weekday=1(화)` → 화요일 스케줄 체크

**결론**: 문제 없음. 요일 교차가 올바르게 처리됨.

---

#### 4.7 `logger.py` `_rotate_if_needed()` 레이스 조건 — 문제 없음

**현황** (logger.py:30-50):
```python
def _rotate_if_needed(self):
    today = datetime.date.today().strftime("%Y%m%d")
    if today == self._current_date:
        return
    # 핸들러 교체 로직...
```

**레이스 조건 분석**:
- `_rotate_if_needed()`는 `info()`, `error()` 등 로그 메서드 내에서 호출됨
- `_run_detection()`은 QTimer → Qt 메인 스레드에서 실행
- `AppLogger`의 모든 `info()`/`error()` 호출도 Qt 메인 스레드에서 실행
- **Qt 이벤트 루프는 단일 스레드** → 동시 호출 불가 → 레이스 없음

**예외 케이스**: `detector.py`의 `_log = logging.getLogger(__name__)`는 `AppLogger`가 아닌 표준 `logging` 사용.
- `AppLogger._file_logger`와 `logging.getLogger("kbs_monitor")`가 같은 logger일 수 있으나,
  detector의 `_log`는 별도 logger (`kbs_monitor.core.detector`) → 핸들러 공유 없음
- `VideoCaptureThread` 등 QThread에서 `_log` (표준 logging) 사용 시 `AppLogger`와 별개

**결론**: `AppLogger`는 메인 스레드에서만 호출되므로 레이스 없음. 표준 `logging`과는 핸들러를 공유하지 않으므로 간섭 없음.

---

#### 4.8 `time.time()` NTP 시간 점프 영향 — ⚠️ 실질적 위험 낮음, 인지 필요

**time.time() 사용처**:
1. `DetectionState.update()` (detector.py:44) — `now = time.time()`
2. `DetectionState._do_resolve()` (detector.py:103) — `time.time()` fallback
3. `Detector.detect_frame()` near-miss (detector.py:330) — `now_nm = time.time()`
4. `Detector.update_embedded_silence()` (detector.py:429-432) — `time.time()`
5. `SignoffManager._tick_preparation()` / `_tick_exit_preparation()` — `now = time.time()`
6. `SignoffManager._transition_to()` (signoff_manager.py:730,733) — `time.time()`
7. `main_window.py` DIAG 로그 (L252) — `now_hb = time.time()`
8. 텔레그램 테스트 타임아웃 (main_window.py:847) — `time.time()`

**NTP 점프 시나리오**:
- **시간이 앞으로 점프** (예: +2초): `now - alert_start_time` 값이 실제보다 2초 더 큼
  → 경보 threshold 도달이 2초 빨라짐 → 최악: 약간의 조기 경보 (실질 무해)
- **시간이 뒤로 점프** (예: -2초): `now - alert_start_time` 값이 음수 또는 비정상 축소
  → 경보 threshold 도달이 지연됨 → 최악: 2초 늦은 경보 (실질 무해)
  → `DetectionState.update()` L53: `alert_duration = now - alert_start_time` → 음수 가능
  → L55: `alert_duration >= threshold_seconds` → 음수이므로 경보 미발생 (안전)
- **큰 점프** (수십 초~분): NTP는 일반적으로 `slew` 모드로 점진 보정 (큰 점프는 부팅 시에만 발생)

**SignoffManager 영향**:
- `_tick_preparation()`의 `now - _video_enter_start[gid]` → NTP 점프 시 잠깐 지연/가속
- `get_elapsed_seconds()` → 동일

**결론**: 일반적 NTP slew 보정(초당 0.5ms)은 무시 가능. 큰 시간 점프(수십 초+)는 부팅 시에만 발생하며 프로그램 재시작으로 자연 해소. `time.monotonic()` 대체 시 시스템 절전(sleep) 시간이 포함되어 오히려 부정확해질 수 있음. 현재 구현 유지가 적절.

> 참고: `time.monotonic()`은 NTP 영향을 받지 않지만, Windows 절전/하이버네이트 시 정지하지 않으므로 이 프로젝트에서는 `time.time()`이 더 적합.

---

#### 4.9 QTimer 장기 드리프트 — 문제 없음

**현황** (main_window.py:220-228):
```python
self._detect_timer = QTimer(self)
self._detect_timer.setInterval(200)   # 감지 주기
self._detect_timer.start()

self._summary_timer = QTimer(self)
self._summary_timer.setInterval(1000)  # 요약 업데이트
self._summary_timer.start()
```

**드리프트 분석**:
- QTimer는 `setInterval`로 반복 실행 → Qt가 내부적으로 시스템 시계 기반 다음 발화 시각 계산
- 단일 실행 지연(예: 감지 로직이 200ms 이상 소요)은 다음 interval에 영향을 주지 않음 (Qt가 보정)
- 장기 드리프트: Qt의 내부 구현은 절대 시각 기반이 아닌 interval 기반이므로 미세한 드리프트 존재
  - 200ms 타이머: 실제 201~202ms 간격 → 1시간 후 약 36초 드리프트
  - 그러나 이는 "감지 주기가 정확히 200ms가 아님"일 뿐, 감지 **품질**에는 영향 없음

**감지 품질 영향**:
- 블랙/스틸 감지: threshold는 **초 단위** (예: 5초, 10초) → `time.time()` 기반 계산이므로 QTimer 드리프트 무관
- 오디오 레벨미터: AudioMonitorThread는 sounddevice 콜백 기반 (QTimer 무관)
- 하트비트 로그: 5분 간격 → ±수초 오차는 무의미

**결론**: QTimer 드리프트는 감지 품질에 영향 없음. 모든 시간 기반 판단은 `time.time()` 벽시계를 사용하므로 타이머 주기 오차와 독립적. 수정 불필요.

---

### 결과 요약

```
발견된 문제:
  - 없음 (수정 필수 항목 없음)

수정 권장 (선택적):
  - 4.1: _detection_count 하트비트 경과 시간을 일/시/분 형식으로 변환하면 로그 가독성 향상
    (현재 "525600분 경과" → "365일 0시간 경과")

메모:
  - 4.2: _silence_duration float 누적은 이론적 정밀도 손실이 있으나, 리셋 빈도가 높아 현실적 문제 아님
  - 4.3: alert_duration은 벽시계 차이(now - start_time) 방식 → 누적 오차 없는 안전한 설계
  - 4.5: 한국(KST)은 DST 미사용이므로 시간 문자열 비교 안전. 해외 운용 시 재검토 필요
  - 4.7: AppLogger는 Qt 메인 스레드 전용 → 단일 스레드 보장으로 레이스 없음
  - 4.8: NTP 시간 점프는 일반적으로 slew 모드(초당 0.5ms)로 무시 가능.
         time.monotonic() 대체는 Windows 절전 시 오히려 부정확해지므로 비권장
  - 4.9: QTimer 드리프트는 존재하나, 감지 판단이 time.time() 기반이므로 품질 무관
```

---

## 단계 5: 예외 처리 및 복구 검토

### 목적
예외 발생 시 감지가 조용히 멈추지 않는지(silent failure), 복구 로직이 올바른지 검토한다. CLAUDE.md의 "감지 루프 안정성 원칙"을 기준으로 한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 5: 예외 처리 및 복구 검토"를 수행해줘.
각 TODO 항목을 순서대로 검토하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 검토 대상 파일
- `kbs_monitor/ui/main_window.py` — `_run_detection()` 전체 try-except
- `kbs_monitor/core/video_capture.py` — `run()` while 루프 try-except
- `kbs_monitor/core/audio_monitor.py` — `run()` 예외 처리
- `kbs_monitor/core/detector.py` — ROI별 try-except, `detect_frame()`, `detect_audio_roi()`
- `kbs_monitor/core/telegram_notifier.py` — `_worker_loop()` 예외 처리, 워커 자동 재시작
- `kbs_monitor/core/auto_recorder.py` — `_record_worker()` 예외 처리
- `kbs_monitor/core/alarm.py` — `_play_sound_worker()` 예외 처리

### TODO 체크리스트

- [x] **5.1** `main_window.py` `_run_detection()` — 전체 try-except가 존재하는지 확인. except 블록에서 로깅 후 감지 타이머가 계속 작동하는지 확인
- [x] **5.2** `main_window.py` — `_detection_count` 5분 하트비트 로그가 try 블록 **밖**에 있어서 예외 발생 시에도 카운트되는지, 아니면 try 블록 **안**에 있어서 예외 시 카운트가 멈추는지 확인
- [x] **5.3** `video_capture.py` `run()` — `cap.read()` 예외 시 `cap.release()` 후 재연결 시도하는지 확인. `consecutive_failures` 카운터 리셋이 적절한지 검토
- [x] **5.4** `video_capture.py` — 연결 실패 시 무한 재시도 vs 백오프(backoff) 패턴 확인. CPU 100% 방지를 위한 sleep이 있는지
- [x] **5.5** `audio_monitor.py` `run()` — sounddevice 예외 시 스트림 정리 후 재시도 or 종료 패턴 확인
- [x] **5.6** `detector.py` — ROI별 try-except에서 특정 ROI 예외가 다른 ROI 감지를 멈추지 않는 격리 구조 확인
- [x] **5.7** `telegram_notifier.py` — HTTP 요청 실패 시 재시도 로직, 큐 overflow 방지, 워커 사망 감지 + 자동 재시작 패턴 검토
- [x] **5.8** `auto_recorder.py` — 녹화 실패(디스크 풀, 코덱 에러) 시 다음 녹화에 영향을 주지 않는지 확인
- [x] **5.9** `alarm.py` — 사운드 재생 실패 시 시각적 알림(깜박임)이 계속 작동하는지 확인. 사운드/시각 알림 독립성 검토
- [x] **5.10** 전체 — `except Exception as e:` 패턴에서 `BaseException` (KeyboardInterrupt, SystemExit)은 전파되는지 확인. 프로그램 종료가 차단되지 않아야 함

### 검토 방법
각 try-except 블록에 대해:
1. **보호 범위**가 적절한지 (너무 넓으면 의도치 않은 예외 삼킴)
2. **except 블록**에서 상태가 일관되게 유지되는지 (partial update 방지)
3. **로깅**이 충분한지 (예외 메시지 + traceback)
4. **복구 후** 다음 주기에서 정상 동작이 가능한지

### 결과 기록란

#### 5.1 `_run_detection()` 전체 try-except — ✅ 양호
- **L356~529**: 감지 로직 전체가 `try:` 블록 안에 있음
- **L529~530**: `except Exception as e:` → `_logger.error()` 로깅 후 함수 리턴
- 타이머(`_detect_timer`)는 QTimer이므로 콜백이 예외로 끝나도 다음 주기에 다시 호출됨 → **silent failure 방지 확인**

#### 5.2 `_detection_count` 하트비트 위치 — ✅ 양호 (우수 설계)
- **L248~259**: `_detection_count += 1` 및 5분 하트비트 로그가 `try` 블록 **밖**(L356 이전)에 위치
- 예외가 반복 발생해도 카운터는 계속 증가하고 5분 하트비트가 출력됨
- **효과**: 하트비트가 찍히는데 감지 결과가 없으면 → try 블록 내 반복 예외를 의미 → 로그로 추적 가능

#### 5.3 `video_capture.py` 예외 복구 — ✅ 양호
- **L118~132**: `except Exception as e:` 에서:
  - `cap.release()` 호출 (L121~126, 자체 try-except로 이중 보호)
  - `cap = None` 리셋
  - `was_connected = False`, `disconnected.emit()` 상태 전파
  - `consecutive_failures = 0` 리셋 후 1초 sleep → `continue`로 재연결 시도
- `consecutive_failures`는 `cap.read()` 실패(ret=False) 시 증가(L107), 성공 시 0으로 리셋(L101) → 적절

#### 5.4 `video_capture.py` 연결 실패 백오프 — ✅ 양호
- 예외 시 `self.msleep(1000)` (L131) → 1초 대기 후 재시도
- 정상 루프에서도 `self.msleep(33)` (L135) → 약 30fps 속도 제한
- `cap.open()` 실패 시(L59~72) `self.msleep(2000)` → 2초 대기
- CPU 100% 방지 확인

#### 5.5 `audio_monitor.py` 예외 복구 — ✅ 우수
- **이중 구조**:
  - 내부 루프 예외(L146~148): `DEBUG` 로깅 + `-60dB` emit → 루프 계속
  - 외부 스트림 예외(L150~154): 더미 모드 전환 (`-60dB` 지속 emit, 스레드 유지)
- **finally 블록(L155~168)**: output/input 스트림 각각 try-except로 정리 → 정리 실패가 전파되지 않음
- 출력 스트림 실패(L96~98)도 독립 처리 — 입력 감지는 계속 동작

#### 5.6 `detector.py` ROI별 격리 — ✅ 우수
- `detect_frame()`: for 루프 내 각 ROI마다 `try-except` (L257~357)
  - 예외 시 해당 ROI만 건너뛰고 다음 ROI 계속 처리
  - `_log.error("detect_frame ROI[%s] 오류: %s", label, e)` 로깅
- `detect_audio_roi()`: 동일 패턴 (L379~423)
  - 각 ROI 독립 처리, 예외 시 skip + 로깅

#### 5.7 `telegram_notifier.py` 예외 복구 — ✅ 우수
- **`_worker_loop()` (L225~238)**:
  - 아이템별 `try-except` (L234~238) → 전송 실패해도 워커 스레드 유지
  - `queue.Empty` 예외로 1초 폴링 (L229)
- **`_send()` 재시도**: `_SEND_RETRY_COUNT`만큼 재시도, 429(Rate Limit) 시 `retry_after`초 대기
- **큐 overflow 방지**: `Queue(maxsize=...)` 사용, `queue.Full` 예외 시 무시 (L180~182)
- **워커 자동 재시작**: `notify()` 호출 시 `_worker_thread.is_alive()` 확인 → 사망 시 재시작 (L133~138)

#### 5.8 `auto_recorder.py` 녹화 실패 격리 — ✅ 우수
- **이중 try-finally 중첩** (L251~327):
  - 내부 finally(L293~296): 비디오/오디오 writer release
  - 외부 finally(L307~327): 임시 파일 정리
- ffmpeg 실패 시 비디오 전용 MP4로 폴백 (L315~321)
- `push_frame()`/`push_audio()` 개별 예외 무시(L147~148, L155~156) → 개별 프레임 실패가 전체 녹화에 영향 없음
- 녹화 실패해도 다음 `_record_worker()` 호출은 독립적으로 새 파일 생성

#### 5.9 `alarm.py` 사운드/시각 독립성 — ✅ 우수
- **사운드 재생**: daemon 스레드에서 실행 (`_play_sound_worker`)
- **시각 깜박임**: 메인 스레드 QTimer (`_blink_timer`)
- 두 메커니즘이 완전 독립 → 사운드 실패해도 깜박임 계속
- **3단 폴백**: winsound → sounddevice → Windows 내장음
  - 각 단계별 독립 try-except → 상위 실패 시 다음 단계로 이동
- `_play_test_worker()`도 동일 폴백 구조

#### 5.10 `BaseException` 전파 — ✅ 양호
- 모든 예외 핸들러가 `except Exception as e:` 사용 (`BaseException` 아님)
- `KeyboardInterrupt`, `SystemExit`는 `Exception`의 하위 클래스가 아니므로 자연 전파
- 프로그램 종료가 차단되지 않음 확인

#### 종합 평가

| 컴포넌트 | try-except | ROI 격리 | 로깅 | 정리 | 폴백 | 평가 |
|----------|-----------|---------|------|------|------|------|
| `_run_detection()` | ✅ 전체 | N/A | ✅ | N/A | N/A | 양호 |
| `VideoCaptureThread` | ✅ 전체 | N/A | ✅ | ✅ finally | ✅ 재연결 | 우수 |
| `AudioMonitorThread` | ✅ 이중 | N/A | ✅ | ✅ finally | ✅ 더미모드 | 우수 |
| `Detector` | ✅ ROI별 | ✅ | ✅ | N/A | ✅ skip | 우수 |
| `TelegramNotifier` | ✅ 아이템별 | N/A | ✅ | N/A | ✅ 자동재시작 | 우수 |
| `AutoRecorder` | ✅ 이중 중첩 | N/A | 암시적 | ✅ finally×2 | ✅ 비디오전용 | 우수 |
| `AlarmSystem` | ✅ 3단 폴백 | N/A | ✅ | ✅ | ✅ 내장음 | 우수 |

```
발견된 문제:
  - 없음 (CRITICAL/HIGH 문제 없음)

수정 사항:
  - 없음 (현재 코드가 CLAUDE.md 감지 루프 안정성 원칙을 100% 준수)

메모:
  - _detection_count가 try 블록 밖에 위치한 설계가 특히 우수 — 예외 반복 발생 시에도
    하트비트 로그가 계속 출력되어 "예외는 발생하지만 프로그램은 살아있음"을 확인 가능
  - auto_recorder.py의 cleanup 코드(L311~327)에서 bare `except Exception: pass` 사용은
    정리 전용 코드이므로 수용 가능 (디스크 권한 문제 등은 마스킹되지만, 이미 녹화는 완료된 상태)
  - telegram_notifier.py의 워커 자동 재시작 패턴(notify() 호출 시 liveness 체크)은
    감지 루프가 살아있는 한 워커도 자동 복구됨을 보장 — 워치독 역할
```

---

## 단계 6: 디스크 공간 관리 검토

### 목적
로그 파일, 녹화 파일, 임시 파일이 디스크 공간을 무한히 소비하지 않는지 검토한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 6: 디스크 공간 관리 검토"를 수행해줘.
각 TODO 항목을 순서대로 검토하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 검토 대상 파일
- `kbs_monitor/utils/logger.py` — 일별 로그 파일 생성
- `kbs_monitor/core/auto_recorder.py` — MP4 녹화 파일, 임시 파일
- `kbs_monitor/utils/config_manager.py` — 설정 파일 쓰기

### TODO 체크리스트

- [x] **6.1** `logger.py` — 일별 로그 파일(`logs/YYYYMMDD.txt`) 생성 확인. 오래된 로그 삭제/압축 정책이 있는지 확인. **예상**: 하루 ~500줄/시간 × 24시간 = ~12,000줄 ≈ 1~5MB/일 → 1년 ≈ 365~1,825MB → 5년 ≈ 1.8~9.1GB
- [x] **6.2** `auto_recorder.py` — 녹화 파일 저장 경로와 파일명 패턴 확인. 오래된 녹화 자동 삭제 정책이 있는지 확인
- [x] **6.3** `auto_recorder.py` — 임시 파일(`_vtmp.mp4`, `_atmp.wav`)이 정상/비정상 종료 시 모두 정리되는지 확인. 프로그램 시작 시 잔여 임시 파일 정리 로직이 있는지 확인
- [x] **6.4** `config_manager.py` — `save()` 시 기존 파일을 덮어쓰는지, 백업을 남기는지 확인. 쓰기 도중 정전 시 설정 파일 손상 가능성 검토 (atomic write 패턴 사용 여부)
- [x] **6.5** 녹화 빈도 추정: 알람 빈도에 따른 디스크 사용량 계산. (예: 시간당 5회 × 20초 × 5MB = 500MB/시간?)
- [x] **6.6** `logs/` 디렉토리 — 파일 수 자체의 문제: NTFS에서 단일 디렉토리에 수천 개 파일이 쌓일 때 성능 저하 가능성

### 검토 방법
1. 파일 생성 지점 모두 찾기
2. 파일 삭제/정리 지점 찾기
3. 1년/5년 기준 예상 디스크 사용량 계산
4. 디스크 풀(full) 시 프로그램 동작 확인

### 결과 기록란

#### 6.1 logger.py — 오래된 로그 삭제 정책 없음 🔴 문제

**현황**: `_rotate_if_needed()`가 날짜별 파일(`logs/YYYYMMDD.txt`)을 생성하지만, 오래된 로그를 삭제하거나 압축하는 로직이 **전혀 없음**.

**디스크 사용량 추정**:
- 5분마다 "감지 정상 실행 중" 로그 1줄 + 알림/상태 변경 로그
- 정상 운영: ~200~500줄/시간 → ~5,000~12,000줄/일 ≈ **0.5~2MB/일**
- 알림 빈발 시: ~1,000줄/시간 → ~24,000줄/일 ≈ **2~5MB/일**
- **1년**: 180~1,825MB | **5년**: 0.9~9.1GB

**위험도**: 중간. 단독으로는 디스크를 빠르게 채우지 않지만, 5년 이상 무관리 운영 시 수 GB 누적.

**수정 방안**: `_rotate_if_needed()` 호출 시 또는 별도 주기로 `max_keep_days` (예: 90일) 초과 로그 파일 삭제. `auto_recorder.py`의 `_delete_old_files()` 패턴과 동일하게 구현 가능.

---

#### 6.2 auto_recorder.py — 녹화 자동 삭제 ✅ 양호

**현황**:
- 저장 경로: `recordings/` (설정 가능, `_save_dir`)
- 파일명 패턴: `YYYYMMDD_HHMMSS_{label}_{media}_{type}.mp4`
- 자동 삭제: `_cleanup_loop()` → 1시간마다 `_delete_old_files()` 실행
- `_max_keep_days` (기본 7일) 초과 MP4 파일을 `os.path.getmtime()` 기준으로 삭제

**잠재 이슈**:
- `_delete_old_files()`가 `.mp4` 확장자만 대상 → 합성 실패로 `_vtmp.mp4`가 rename 실패 시 고아 파일 가능 (6.3에서 상세 검토)
- 삭제 실패 시 예외를 무시(`except Exception: pass`)하므로 디스크 풀 상태에서도 조용히 실패

---

#### 6.3 auto_recorder.py — 임시 파일 정리 🟡 부분 문제

**정상 종료 시**: `_record_worker()`의 `finally` 블록에서 `_vtmp.mp4`와 `_atmp.wav`를 삭제/rename → **정상 동작**

**비정상 종료 시** (프로세스 kill, 정전, BSOD):
- `_record_worker`는 daemon 스레드 → 메인 프로세스 종료 시 즉시 kill → `finally` 미실행
- 결과: `*_vtmp.mp4`, `*_atmp.wav` 파일이 디스크에 잔류

**프로그램 시작 시 잔여 임시 파일 정리**: **없음** ❌
- `start()` 메서드에서 `_cleanup_loop` 시작만 함, 잔여 임시 파일 스캔 없음
- `_delete_old_files()`는 `.mp4`만 삭제 → `.wav` 임시 파일은 영원히 남음

**수정 방안**:
1. `start()` 또는 `_delete_old_files()`에서 `*_vtmp.mp4`, `*_atmp.wav` 패턴 파일도 삭제
2. 또는 `_cleanup_loop` 첫 실행 시 잔여 임시 파일 스캔 추가

---

#### 6.4 config_manager.py — atomic write 미사용 🟡 부분 문제

**현황**: `_write_json()`이 `open(path, "w")` → `json.dump()` 직접 수행
```python
def _write_json(self, path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

**문제 시나리오**:
1. `open("w")`가 파일을 **즉시 truncate** (0바이트로 만듦)
2. `json.dump()` 중 정전/크래시 발생
3. 결과: 설정 파일이 빈 파일 또는 불완전한 JSON → 다음 실행 시 `json.load()` 실패
4. 현재 `load()`에서 예외 발생 시 `DEFAULT_CONFIG` 반환 → **데이터 손실** (사용자 커스텀 설정 소멸)

**백업**: 없음. 이전 설정의 백업본을 남기지 않음.

**수정 방안** (atomic write 패턴):
```python
import tempfile
def _write_json(self, path: str, data: dict):
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # 원자적 교체 (NTFS/POSIX)
    except Exception:
        os.unlink(tmp_path)
        raise
```
- `os.replace()`는 NTFS에서도 원자적 동작 보장 (Windows Vista+)
- 정전 시 임시 파일만 손상, 원본은 온전

---

#### 6.5 녹화 빈도/디스크 사용량 추정 📊

**단일 녹화 파일 크기 계산**:
- 해상도: 960×540, FPS: 10, 코덱: mp4v (MPEG-4)
- 녹화 길이: pre 5초 + post 15초 = **20초**
- mp4v 960×540@10fps ≈ 1~3Mbps → 20초 ≈ **2.5~7.5MB/파일**
- 오디오(AAC) 추가: +0.1MB → 무시 가능

**시나리오별 디스크 사용량**:

| 시나리오 | 빈도 | 일간 | 7일(기본 보관) | 30일 |
|---------|------|------|-----------|------|
| 정상 (알림 드묾) | 2회/시간 | ~960MB | ~6.7GB | ~28.8GB |
| 보통 | 5회/시간 | ~2.4GB | ~16.8GB | ~72GB |
| 빈발 (장애 상황) | 20회/시간 | ~9.6GB | ~67.2GB | ~288GB |

> ⚠️ `max_keep_days=7` 기본값이라도 **빈발 시나리오에서 67GB** 도달 가능.
> 녹화가 이미 진행 중이면 `trigger()`가 종료 시간만 연장하므로, 연속 알림 시 파일 수는 줄지만 개별 파일 크기 증가.

**디스크 풀 시 동작**:
- `cv2.VideoWriter()` — `isOpened()` 실패 → `_record_worker` 조기 리턴 (안전)
- `wave.open()` — 예외 발생 → 외부 try-except 없음 → **`_record_worker` 크래시** → 임시 파일 미정리 가능
- `os.makedirs()` — 디스크 풀이면 `OSError` → `trigger()`에서 예외 전파 → 감지 루프 try-except에서 포착되긴 하지만 로깅 누락 가능

---

#### 6.6 logs/ 디렉토리 파일 수 🟢 낮은 위험

**현황**: 일별 1개 파일 생성 → 1년 = 365개, 5년 = 1,825개

**NTFS 성능**: 단일 디렉토리에 10,000개 미만 파일은 NTFS에서 성능 문제 없음. 5년 운영 시 ~1,825개 → **문제 없음**.

**recordings/ 디렉토리**: 7일 보관 기준 최대 수백~수천 개 → 자동 삭제로 관리되므로 문제 없음. 단, `max_keep_days`를 30일 이상으로 설정하고 알림 빈발 시 수만 개 가능 → NTFS 성능 저하 가능성 있으나 현실적으로 낮음.

---

### 종합 요약

| 항목 | 위험도 | 문제 |
|------|--------|------|
| 6.1 로그 삭제 정책 | 🔴 중간 | 삭제 정책 전혀 없음, 5년+ 시 수 GB |
| 6.2 녹화 자동 삭제 | ✅ 양호 | `_cleanup_loop`으로 관리됨 |
| 6.3 임시 파일 잔류 | 🟡 낮음 | 비정상 종료 시 `_vtmp`/`_atmp` 잔류, 시작 시 정리 없음 |
| 6.4 설정 파일 손상 | 🟡 중간 | atomic write 미사용, 정전 시 설정 손실 |
| 6.5 녹화 디스크 사용량 | 🟡 참고 | 빈발 시 7일 보관으로도 67GB, 디스크 풀 시 wave.open 크래시 |
| 6.6 디렉토리 파일 수 | 🟢 낮음 | 5년 1,825개, NTFS 문제 없음 |

### 권장 수정 우선순위
1. **6.4** atomic write — 설정 손실은 사용자 경험에 직접 영향 (ROI, 텔레그램 등 재설정 필요)
2. **6.1** 로그 자동 삭제 — `auto_recorder.py`와 동일 패턴으로 간단 구현 가능
3. **6.3** 시작 시 임시 파일 정리 — `start()`에 glob 패턴 삭제 추가
4. **6.5** `wave.open()` 예외 처리 강화 — 디스크 풀 시 안전한 폴백

---

## 단계 7: Qt 시그널/슬롯 안정성 검토

### 목적
시그널/슬롯 연결이 중복되지 않는지, 위젯 수명 관리가 올바른지, 크로스스레드 시그널이 안전한지 검토한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 7: Qt 시그널/슬롯 안정성 검토"를 수행해줘.
각 TODO 항목을 순서대로 검토하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 검토 대상 파일
- `kbs_monitor/ui/main_window.py` — 시그널 연결, 설정 다이얼로그 수명
- `kbs_monitor/ui/settings_dialog.py` — 시그널 emit, 위젯 수명
- `kbs_monitor/ui/video_widget.py` — `paintEvent` 성능
- `kbs_monitor/ui/top_bar.py` — 업데이트 빈도
- `kbs_monitor/ui/roi_editor.py` — 오버레이 위젯 수명
- `kbs_monitor/core/video_capture.py` — `frame_ready` 크로스스레드 시그널
- `kbs_monitor/core/audio_monitor.py` — `level_updated` 크로스스레드 시그널

### TODO 체크리스트

- [x] **7.1** `main_window.py` `_open_settings()` — SettingsDialog가 한 번만 생성되는지 확인. 닫았다 열 때 시그널이 중복 연결되지 않는지 검토
- [x] **7.2** `main_window.py` — `frame_ready.connect(_on_frame_ready)` 연결이 QThread 시작 전에 한 번만 이루어지는지 확인
- [x] **7.3** `video_widget.py` `paintEvent()` — 30fps 프레임 업데이트 시 `update()` 호출 빈도와 실제 페인트 성능. 장기 운영 시 이벤트 큐 누적 가능성
- [x] **7.4** `top_bar.py` — `update_audio_levels()`가 오디오 스레드 속도(~43Hz)로 호출될 때 UI 업데이트 병목 가능성
- [x] **7.5** ROI 에디터 오버레이 — 열기/닫기 시 위젯이 올바르게 생성/파괴되는지 확인. 반복 열기/닫기 시 메모리 누수 가능성
- [x] **7.6** 크로스스레드 시그널 타입 안전성 — `frame_ready(np.ndarray)` 시그널에서 numpy 배열이 Qt 메타타입으로 안전하게 전달되는지 확인
- [x] **7.7** `settings_dialog.py` — 시그널 emit 후 수신측에서 처리 중 다이얼로그가 닫힐 때 dangling reference 가능성
- [x] **7.8** QTimer — `_detect_timer`와 `_summary_timer`가 `closeEvent()`에서 확실히 `stop()` 되는지 확인. stop 누락 시 파괴된 객체에 타이머 콜백 발생

### 검토 방법
1. `.connect()` 호출 모두 찾기
2. 각 연결이 **한 번만** 실행되는지 확인 (반복 호출 경로 없는지)
3. 시그널 수신측 객체 수명 > 시그널 발신측 객체 수명인지 확인
4. 크로스스레드 시그널이 `Qt.QueuedConnection`으로 처리되는지 확인

### 결과 기록란

#### 7.1 SettingsDialog 생성/시그널 중복 — ✅ 정상 (경미한 누수 1건)

**구현 패턴** (`main_window.py:581-621`):
- `_open_settings()`에서 `if self._settings_dialog is None` 가드로 최초 1회만 생성
- 18개 시그널 모두 생성 직후 한 번만 `.connect()` — 중복 연결 없음
- 재오픈 시 `refresh_roi_tables()` + `showNormal()`만 호출

**발견된 문제 — SettingsDialog 메모리 누수 (경미)**:
- `_on_settings_closed()` (L618-621)에서 `self._settings_dialog = None`으로 Python 참조만 해제
- SettingsDialog는 `parent=self`(MainWindow)로 생성되어 Qt 부모-자식 계층에 남아 있음
- 닫았다 다시 열 때마다 **새 SettingsDialog 인스턴스가 생성되고 이전 인스턴스는 Qt 트리에 잔존**
- 10번 열고 닫으면 10개의 SettingsDialog 객체가 메모리에 남음 (MainWindow 종료 시 일괄 해제)

**수정 방안**:
```python
# _on_settings_closed()에 deleteLater() 추가
def _on_settings_closed(self):
    if self._settings_dialog:
        self._config = self._settings_dialog.get_config()
        self._settings_dialog.deleteLater()   # ← Qt 트리에서 제거
    self._settings_dialog = None
```

**위험도**: 낮음 — 사용자가 설정창을 자주 열고 닫아도 SettingsDialog 1개 ≈ 수 MB 수준, 프로그램 종료 시 해제됨. 단, 장기 운용 원칙상 수정 권장.

---

#### 7.2 frame_ready/level_updated 시그널 연결 — ✅ 정상

**구현 패턴** (`main_window.py:190-218`):
- `_start_threads()`에서 캡처/오디오 스레드 생성 직후, `start()` 호출 **전에** 시그널 연결
- `frame_ready.connect(self._on_frame_ready)` → 1회 연결
- `level_updated.connect(self._top_bar.update_audio_levels)` → 1회 연결
- `level_updated.connect(self._on_audio_level_for_silence)` → 1회 연결
- `audio_chunk.connect(... Qt.DirectConnection)` → 녹화용, 1회 연결

**DirectConnection 안전성** (`audio_chunk` → `push_audio`):
- `push_audio()` (auto_recorder.py:177-191)에서 `_audio_buffer.append()`는 `_audio_lock`으로 보호됨
- `_audio_record_queue.append()`는 lock 없이 사용되나 CPython GIL로 `deque.append()` 원자성 보장
- 실질적으로 안전하나, 엄밀히는 `_recording`/`_record_end` 읽기도 lock 없음 (CPython 한정 안전)

**문제 없음.**

---

#### 7.3 VideoWidget paintEvent 성능 — ✅ 정상

**구현 패턴** (`video_widget.py:64-111`):
- `update_frame(frame)` → `_current_frame = frame` 저장 → `_render()` 동기 호출
- `_render()` (L93-111): `_current_frame.copy()` 후 QImage 변환 → `_label.setPixmap(pixmap)`
- QLabel `setPixmap()`은 Qt 내부적으로 paint event를 큐잉, **이벤트 병합(coalescing)** 적용

**~30fps에서의 성능**:
- `_render()` 내부: `cv2.cvtColor(BGR→RGB)` + `QImage` 생성 + QPainter 텍스트 오버레이 — 경량 연산
- Qt paint event coalescing으로 디스플레이 리프레시 속도(~60Hz) 초과 시 자동 병합
- 이벤트 큐 누적 위험 없음 — `setPixmap()`은 즉시 반환, 실제 페인트는 다음 이벤트 루프

**문제 없음.**

---

#### 7.4 TopBar update_audio_levels 고빈도 호출 — ✅ 정상

**구현 패턴** (`top_bar.py:694-697`):
```python
def update_audio_levels(self, l_db: float, r_db: float):
    self._meter_l.set_level(l_db)
    self._meter_r.set_level(r_db)
```

**LevelMeterBar.set_level()** → 내부 `self._level_db` 업데이트 + `self.update()` 호출

**고빈도 처리**:
- `self.update()`는 Qt paint event를 **큐잉만** 함 (즉시 paint 아님)
- 여러 `update()` 호출이 하나의 paint event로 병합 (Qt 표준 동작)
- 실질적으로 ~60Hz 화면 리프레시에 맞춰 paint → ~43Hz 호출에도 병목 없음

**문제 없음.**

---

#### 7.5 ROI 에디터 오버레이 수명 관리 — ✅ 정상

**생성** (`main_window.py:740-760`):
- 기존 오버레이가 있으면 먼저 `_close_overlay()` 호출 후 새로 생성
- `parent=self._video_widget`로 Qt 소유권 설정
- `rois_changed.connect()` 1회 연결

**파괴** (`main_window.py:805-810`):
```python
def _close_overlay(self):
    if self._roi_overlay:
        self._roi_overlay.hide()
        self._roi_overlay.deleteLater()   # ← Qt 이벤트 루프에서 안전 삭제
        self._roi_overlay = None
```

**호출 지점**: `_finish_halfscreen_edit()`, `_start_halfscreen_edit()` (기존 overlay 정리), `closeEvent()`

**내부 타이머**: ROIEditorCanvas 내부 `_key_emit_timer`는 `QTimer(self)`로 생성되어 부모 삭제 시 자동 정리

**문제 없음.** 반복 열기/닫기 시 `deleteLater()`로 안전하게 정리됨.

---

#### 7.6 크로스스레드 numpy 배열 전달 — ✅ 정상

**시그널 정의** (`video_capture.py:13`):
```python
frame_ready = Signal(object)  # numpy 배열 (BGR 프레임)
```

**전달 안전성**:
- `Signal(object)` 타입은 Python 객체 참조를 전달 — Qt 메타타입 등록 불필요
- 크로스스레드 시그널 시 Qt는 QueuedConnection 자동 적용 → Python 참조 카운트 증가
- 수신측 `_on_frame_ready()` (L235-239)에서 `frame.copy()` 호출하여 캡처 스레드 버퍼 독립화

**미세한 주의점**:
- L238 `update_frame(frame)`, L239 `push_frame(frame)`은 원본 `frame`(복사본 아님)을 전달
- 그러나 동일 이벤트 루프 턴에서 동기 실행되므로 캡처 스레드의 `msleep(33)` 내에 완료됨
- `_render()` 내부에서 `frame.copy()` 추가 수행 (L97) → 안전
- `push_frame()`도 `cv2.resize()`로 새 배열 생성 또는 `imencode()`로 바이트 복사 → 안전

**문제 없음.**

---

#### 7.7 SettingsDialog 시그널 emit / dangling reference — ✅ 정상

**시그널 17개** (`settings_dialog.py:637-653`):
- 모든 시그널은 사용자 UI 액션(버튼 클릭, 값 변경)에 의해 emit
- 다이얼로그가 닫힐 때(`finished` 시그널) → `_on_settings_closed()`에서 config 저장 후 참조 해제

**Dangling reference 안전성**:
- 다이얼로그 `parent=self`(MainWindow)로 설정 → MainWindow가 살아있는 한 객체 유효
- `finished` 시그널은 다이얼로그가 **닫힐 때** emit (삭제 전) → 안전
- 수신측 슬롯들(MainWindow 메서드)은 MainWindow 수명 동안 유효
- 시그널 수신 → 처리 중 다이얼로그 닫힘: 불가능 (같은 메인 스레드 이벤트 루프에서 순차 처리)

**문제 없음.**

---

#### 7.8 QTimer closeEvent 정리 — ✅ 정상 (경미한 누락 1건)

**정리 확인** (`main_window.py:1143-1168`):
- ✅ `_detect_timer.stop()` (L1158)
- ✅ `_summary_timer.stop()` (L1159)
- ✅ `_capture_thread.stop()` (L1161)
- ✅ `_audio_thread.stop()` (L1163)
- ✅ `_telegram.stop()` (L1164)
- ✅ `_recorder.stop()` (L1165)

**발견된 문제 — `_tg_test_timer` stop 누락 (경미)**:
- `_on_telegram_test()` (L868-872)에서 `_tg_test_timer = QTimer(self)` 생성
- 텔레그램 테스트 실행 중 프로그램 종료 시 `_tg_test_timer`가 stop되지 않음
- `QTimer(self)` — parent가 MainWindow이므로 삭제 시 자동 정리되나, closeEvent 실행 중 타이머 콜백이 1회 발생할 수 있음
- `_poll_telegram_test()` (L874)이 이미 파괴 중인 `_settings_dialog`에 접근 시 에러 가능

**수정 방안**:
```python
# closeEvent()에 추가
if hasattr(self, "_tg_test_timer"):
    self._tg_test_timer.stop()
```

**위험도**: 매우 낮음 — 텔레그램 테스트 실행 중 프로그램을 닫는 경우에만 발생. 단, 방어적 코딩 원칙상 수정 권장.

---

### 종합 요약

```
발견된 문제:
  1. [경미] SettingsDialog 메모리 누수 — 닫을 때 deleteLater() 미호출 (7.1)
  2. [경미] _tg_test_timer closeEvent에서 stop() 누락 (7.8)

수정 사항:
  1. _on_settings_closed()에 self._settings_dialog.deleteLater() 추가
  2. closeEvent()에 _tg_test_timer.stop() 추가

메모:
  - 전체적으로 시그널/슬롯 아키텍처가 잘 설계되어 있음
  - 시그널 중복 연결 없음 (싱글톤 패턴 + 생성 시 1회 연결)
  - 모든 스레드 시그널이 start() 전에 연결됨
  - frame.copy()로 크로스스레드 버퍼 공유 방지
  - Qt paint event coalescing으로 고빈도 UI 업데이트 처리
  - deleteLater()로 오버레이 안전 정리
  - DirectConnection(audio_chunk)은 CPython GIL 하에서 실질적으로 안전
```

---

## 단계 8: 종합 스트레스 시나리오 검토

### 목적
여러 문제가 동시에 발생하는 복합 장애 시나리오에서 시스템이 안정적으로 동작하는지 검토한다.

### 프롬프트
```
CODE_REVIEW_LONGEVITY.md의 "단계 8: 종합 스트레스 시나리오 검토"를 수행해줘.
각 시나리오를 코드 레벨에서 추적하고, 발견된 문제와 수정 방안을 이 문서에 기록해줘.
```

### 검토 대상
모든 파일 — 시나리오별로 관련 코드를 추적

### 시나리오 체크리스트

- [x] **8.1** **캡처 장치 분리/재연결 반복**: USB 캡처 카드가 30초마다 분리/재연결되는 상황. `VideoCaptureThread`의 재연결 로직이 리소스를 정리하고 안정적으로 복구하는지 추적. 100회 반복 후에도 문제 없는지.
- [x] **8.2** **16채널 동시 알람**: 모든 ROI에서 동시에 블랙+스틸+오디오 알람 발생. AlarmSystem, TelegramNotifier, AutoRecorder의 동시 부하. 메모리 스파이크, 큐 오버플로우, 스레드 병목.
- [x] **8.3** **네트워크 단절 장기화**: 텔레그램 서버 연결 불가 상태가 24시간 지속. 큐 무한 성장, 워커 스레드 재시작 반복, 메모리 누수. 네트워크 복구 후 정상화되는지.
- [x] **8.4** **디스크 풀**: 녹화/로그로 디스크 100% 사용. 로그 쓰기 실패, 녹화 실패, 설정 저장 실패 시 프로그램이 크래시하지 않는지.
- [x] **8.5** **자정 전후 동작**: 23:59:59 → 00:00:00 전환 시 로그 로테이션, 정파 스케줄, 날짜 기반 로직이 모두 올바르게 작동하는지.
- [x] **8.6** **설정 변경 중 감지**: 사용자가 설정 다이얼로그에서 ROI를 추가/삭제하는 동안 감지 루프가 안전하게 동작하는지. `update_roi_list()` 호출 타이밍.
- [x] **8.7** **오디오 장치 변경**: sounddevice 장치가 시스템에서 제거/추가될 때 AudioMonitorThread가 크래시하지 않는지.
- [x] **8.8** **메모리 압박**: 시스템 RAM이 부족할 때 numpy 배열 할당 실패, deque 성장 실패 시 graceful degradation이 가능한지.
- [x] **8.9** **장기 연속 운영 후 성능 저하**: 30일, 90일, 365일 운영 후 감지 루프 지연시간이 증가하지 않는지. 메모리 사용량이 선형/지수적으로 증가하는 패턴이 있는지.
- [x] **8.10** **Windows 업데이트 후 재시작**: 프로그램이 비정상 종료(강제 종료)된 후 다시 시작할 때 잔여 리소스(잠긴 파일, 좀비 프로세스)가 방해하지 않는지.

### 검토 방법
각 시나리오에 대해:
1. **진입 조건**: 어떤 외부 이벤트가 시나리오를 트리거하는지
2. **코드 경로 추적**: 해당 이벤트가 코드에서 어떻게 처리되는지 step-by-step
3. **최악의 결과**: 처리 실패 시 어떤 증상이 나타나는지
4. **복구 가능성**: 외부 조건이 정상화되면 프로그램도 자동 복구되는지

---

### 8.1 캡처 장치 분리/재연결 반복

**진입 조건**: USB 캡처 카드가 물리적으로 분리되거나 드라이버 오류로 연결이 끊어짐

**코드 경로 추적** (`video_capture.py:46-139`):
1. `cap.read()` 실패 → `consecutive_failures` 증가 (L107-108)
2. 30프레임 연속 실패 → `cap.release()` + `cap = None` + `disconnected` 시그널 (L109-116)
3. 다음 루프: `cap is None` → `cv2.VideoCapture(port, CAP_DSHOW)` 재생성 (L71-81)
4. `isOpened()` 성공 시 → `connected` 시그널 + 정상 루프 복귀 (L83-87)
5. `isOpened()` 실패 시 → `cap.release()` + `cap = None` + `msleep(1000)` 후 재시도 (L88-96)
6. 예외 발생 시 → 외부 try-except가 `cap.release()` + 재연결 (L118-132)

**최악의 결과**: 없음 — 재연결 경로가 안정적

**복구 가능성**: **자동 복구됨**. 장치 복귀 시 다음 루프에서 즉시 재연결.

**판정: ✅ 안전**
- `cap.release()`가 모든 분기(정상/예외)에서 호출됨
- 재연결 시 항상 새 `cv2.VideoCapture` 객체 생성 → 이전 리소스 잔류 없음
- 연결 실패 시 1초 대기 → CPU 스핀 방지
- 100회 반복해도 메모리/리소스 누수 경로 없음

**참고**: Windows DirectShow COM 객체는 `cap.release()` 호출 시 정상 해제됨. OpenCV가 내부적으로 `IGraphBuilder::Release()`를 호출하므로 COM 레퍼런스 누적 없음.

---

### 8.2 16채널 동시 알람

**진입 조건**: 모든 비디오/오디오 ROI에서 동시에 블랙+스틸+오디오레벨미터 이상 감지

**코드 경로 추적** (`main_window.py:_run_detection`):

**① AlarmSystem** (`alarm.py`):
- `trigger()` 호출마다 `_active_alarms.add(key)` → 최대 ROI수 × 감지타입수 (유한)
- `_play_sound()`: `_sound_thread.is_alive()` 체크 → **이미 재생 중이면 건너뜀** (L241-242)
- 즉, 동시 16채널 알람이라도 사운드 스레드는 1개만 실행 → **스레드 폭발 없음**
- `_active_alarms`, `_acknowledged_alarms`: 모두 메인 스레드에서만 접근 (QTimer 콜백 + 버튼 클릭) → **레이스 컨디션 없음**

**② TelegramNotifier** (`telegram_notifier.py`):
- `notify()`에서 JPEG 인코딩: 16채널 × `cv2.imencode()` ≈ 80~160ms (메인 스레드)
- `_queue` maxsize=50 → 초과 시 `put_nowait()` → `queue.Full` → 무시 (L181-182)
- 워커 스레드 1개가 순차 전송 → HTTP 타임아웃(20s) × 50개 = 최대 1000s 소진
- **메모리**: 큐 내 JPEG 바이트 최대 50개 × ~200KB ≈ 10MB → **스파이크 경미**

**③ AutoRecorder** (`auto_recorder.py`):
- 첫 `trigger()` 호출 → `_recording = True` + 녹화 스레드 시작
- 이후 `trigger()` 호출 → `_record_end` 연장만 (L206-209) → **스레드 폭발 없음**
- `_record_queue`: **maxlen 미설정**, 하지만 `_record_end`(시간 제한)으로 자연 종료
  - 최대 크기: `post_seconds × fps` 프레임 × ~1.5MB ≈ 15s × 10fps × 1.5MB = **225MB**
- `_audio_record_queue`: 동일 패턴, PCM 데이터 ≈ 15s × 44100 × 2ch × 2byte / 1024 ≈ **2.5MB**

**최악의 결과**: 감지 루프 1주기 지연(JPEG 인코딩 16회), 녹화 메모리 ~230MB 일시 사용

**복구 가능성**: 알람 해제 시 모든 리소스 자동 정리

**판정: ⚠️ MEDIUM — `_record_queue` maxlen 미설정**
- 현재는 `_record_end` 시간 제한으로 실질적으로 bounded이지만, `_record_end`가 반복 연장되면 큐가 무한 성장 가능
- 16채널이 번갈아가며 연속 trigger → `_record_end`가 끝없이 연장 → `_record_queue`에 numpy 배열 축적

**권장 수정**:
```python
# auto_recorder.py — trigger() 또는 push_frame()에서 최대 프레임 수 제한
_MAX_RECORD_FRAMES = 3000  # 10fps × 300초 = 5분 상한
if len(self._record_queue) < _MAX_RECORD_FRAMES:
    self._record_queue.append((now, small))
```

---

### 8.3 네트워크 단절 장기화

**진입 조건**: 인터넷/방화벽 차단으로 텔레그램 API 서버 연결 불가 24시간+

**코드 경로 추적** (`telegram_notifier.py`):

**① 큐 관리**:
- `_queue = queue.Queue(maxsize=50)` (L47) → **50개 상한**, 초과 시 무시
- 24시간 동안 큐가 가득 차도 메모리 50 × ~200KB ≈ 10MB로 제한

**② 워커 스레드 동작**:
- `_worker_loop()` (L225-238): `queue.get(timeout=1.0)` → 아이템 수신 → `_send()` 호출
- `_send()` (L240-325): connect_timeout=5s, read_timeout=15s × (1 + 2회 재시도) = **최대 60s/아이템**
- 재시도 실패 시 아이템 폐기 + 다음 아이템 처리 → **워커 스레드 영구 블록 없음**
- 외부 try-except (L236-238)로 예상 못한 예외도 포착 → **스레드 사망 방지**

**③ `_last_sent` 딕셔너리**:
- 50개 초과 시 24시간 이상 된 항목 정리 (L149-154) → **무한 성장 방지 확인**

**④ 워커 스레드 자동 재시작**:
- `notify()` 호출마다 `_worker_thread.is_alive()` 확인 → 사망 시 새 스레드 생성 (L133-138)
- daemon 스레드이므로 프로그램 종료 시 자동 정리

**최악의 결과**: 알림이 전달되지 않음 (큐 초과분 폐기). 프로그램 기능에 영향 없음.

**복구 가능성**: **자동 복구됨**. 네트워크 복구 후 다음 큐 아이템부터 정상 전송.

**판정: ✅ 안전**
- 큐 크기 제한 (50), `_last_sent` 정리 로직, 재시도 상한 모두 적절
- 워커 스레드 자동 재시작으로 장기 안정성 보장

---

### 8.4 디스크 풀

**진입 조건**: 녹화/로그 축적으로 디스크 100% 도달

**코드 경로 추적**:

**① 로그 시스템** (`logger.py`):
- 일반 쓰기 실패: Python `logging` 모듈이 `Handler.handleError()` 내부에서 stderr 출력 후 계속 → **크래시 없음**
- **날짜 변경 시 `_rotate_if_needed()`** (L31-52): `FileHandler(log_path, encoding="utf-8")` 호출 → 디스크 풀이면 `OSError` 발생 → **try-except 없음!**
  - 이 예외는 `info()`/`error()` 등 호출자로 전파
  - `_run_detection()` 내 heartbeat 로깅은 try-except **바깥**에서 `self._logger.info()` 호출
  - → **날짜 변경 + 디스크 풀 동시 발생 시 감지 루프 1회 예외** (QTimer가 다음 주기 재호출하므로 영구 중단 아님)

**② 설정 저장** (`config_manager.py`):
- `_write_json()` (L196-209): `tempfile.mkstemp()` → 디스크 풀 시 `OSError`
- `save()` (L154-161): `return False` 반환 → **크래시 없음**
- `closeEvent()`: `_config_manager.save()` 호출하지만 반환값 미확인 → 설정 손실 가능하지만 크래시 아님

**③ 녹화** (`auto_recorder.py`):
- `_record_worker()` (L245-349): `cv2.VideoWriter` 열기 실패 시 `writer.isOpened()` → `return` (L259-260)
- `wave.open()` 실패 → `wav_file = None` → 비디오만 저장 시도 (L269-275)
- 임시 파일 정리: finally 블록에서 처리 (L329-349) → **크래시 없음**
- `os.makedirs()` (L224): `trigger()`에서 호출, `_run_detection()` try-except 안에서 호출되므로 포착됨

**④ `_on_frame_ready()`** (`main_window.py:235`):
- `self._recorder.push_frame(frame)` → `cv2.resize()` / `cv2.imencode()` 실패 시 `except: pass` (L162-163) → 안전

**최악의 결과**: 날짜 변경 시점에 감지 1주기 누락, 설정 저장 실패 (다음 재시작 시 이전 설정 사용)

**복구 가능성**: **자동 복구됨**. 디스크 공간 확보 시 다음 주기부터 정상 동작. 자동 삭제 루프(`_cleanup_loop`, `_delete_old_logs`)가 공간 확보.

**판정: ⚠️ LOW — `_rotate_if_needed()` 예외 미포착**

**권장 수정**:
```python
# logger.py — _rotate_if_needed() 전체를 try-except로 보호
def _rotate_if_needed(self):
    today = datetime.date.today().strftime("%Y%m%d")
    if today == self._current_date:
        return
    try:
        for h in list(self._file_logger.handlers):
            h.close()
            self._file_logger.removeHandler(h)
        log_path = os.path.join(self.LOG_DIR, f"{today}.txt")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        # ... 이하 동일 ...
        self._current_date = today
        self._delete_old_logs()
    except Exception:
        pass  # 디스크 풀 등 — 기존 핸들러로 계속 운영
```

---

### 8.5 자정 전후 동작

**진입 조건**: 23:59:59 → 00:00:00 전환

**코드 경로 추적**:

**① 로그 로테이션** (`logger.py:31-52`):
- `_rotate_if_needed()`: `today != self._current_date` → 새 파일 생성
- 매 `info()`/`error()` 호출마다 확인 → **자정 직후 첫 로그 호출 시 자연 전환**
- 핸들러 교체 중 다른 스레드가 로그 호출하면? → `_file_logger.handlers` 리스트가 비어있는 순간 존재
  - 하지만 AppLogger 메서드는 모두 메인 스레드에서 호출 (UI 시그널)
  - `_log = logging.getLogger(__name__)` (모듈 레벨)는 별도 로거이므로 영향 없음
- **판정: ✅ 안전**

**② 정파 스케줄** (`signoff_manager.py`):
- `_tick_impl()` (L480-528): `current_time = now.strftime("%H:%M")`, `weekday = now.weekday()`
- `_is_in_time_range()` (L693-715): 문자열 비교 `start <= current_time < end`
  - `end_next_day=False`인 경우: `"23:00" <= "00:00"` → False — **자정 넘기는 정파는 `end_next_day=True` 필수**
  - `end_next_day=True`인 경우 (L700-711): `current_time >= start` OR `current_time < end` → **정상 동작**
- `_is_in_prep_window()` (L596-618): prep_start > end_time 자정 넘김 별도 처리 → **정상 동작**
- 요일 전환: 23:59(월) → 00:00(화) 시 `weekday` 변경
  - `end_next_day=True` + `current_time < end`: `prev_weekday = (weekday - 1) % 7` (L609) → **정상 처리**
- **판정: ✅ 안전** (end_next_day 설정이 올바른 전제 하에)

**③ 날짜 기반 로직**:
- 로그 파일명: `YYYYMMDD.txt` → 날짜 전환 시 새 파일 → **안전**
- 녹화 파일명: `datetime.now().strftime("%Y%m%d_%H%M%S")` → **안전**
- 자동 삭제: `time.time()` 기반 mtime 비교 → **날짜 무관, 안전**

**최악의 결과**: 없음

**복구 가능성**: 해당 없음 (문제 없음)

**판정: ✅ 안전**

---

### 8.6 설정 변경 중 감지

**진입 조건**: 사용자가 설정 다이얼로그에서 ROI 추가/삭제/편집 중

**코드 경로 추적**:

**① 스레드 안전성**:
- 모든 설정 변경은 Qt 시그널/슬롯을 통해 **메인 스레드**에서 처리
- `_run_detection()`도 QTimer 콜백 → **메인 스레드**
- Qt 이벤트 루프가 콜백을 직렬화 → **동시 실행 불가능** → **레이스 컨디션 없음**

**② ROI 편집 중 감지 중단**:
- 반화면 ROI 편집 시: `_roi_overlay is not None` → `_run_detection()` 즉시 return (L243-244)
- 전체화면 ROI 편집 시: 별도 다이얼로그 → 편집 완료 후 `update_roi_list()` 호출

**③ `update_roi_list()` 호출 흐름**:
- 설정 다이얼로그 ROI 변경 → `_on_settings_roi_list_changed()` → `_roi_manager` 업데이트 + `_detector.update_roi_list()`
- `update_roi_list()` (detector.py:203-237): 삭제된 ROI 키 정리 + 새 ROI 상태 초기화
- 다음 `_run_detection()` 호출 시 새 ROI 목록으로 감지 → **일관성 보장**

**최악의 결과**: 없음 — 메인 스레드 직렬화로 완전 안전

**복구 가능성**: 해당 없음 (문제 없음)

**판정: ✅ 안전**

---

### 8.7 오디오 장치 변경

**진입 조건**: 임베디드 오디오 캡처에 사용 중인 sounddevice 장치가 시스템에서 제거됨

**코드 경로 추적** (`audio_monitor.py:61-168`):

**① 스트림 초기 오픈 실패** (L75-154):
- `sd.RawInputStream(...)` 예외 → 외부 except → 더미 모드 (-60dB 반복 emit) → **안전**

**② 스트림 사용 중 장치 제거** (L102-148):
- `stream.read(self.CHUNK)` → PortAudio 예외 발생
- 내부 try-except (L103-148)가 포착: `_log.debug()` + `level_updated.emit(-60.0, -60.0)`
- **문제**: 루프가 `while self._running` 안에서 계속 → **즉시 다음 `stream.read()` 호출 → 또 예외 → 반복**
- `stream` 객체가 유효하지 않은 상태에서 무한 예외 루프 발생
- **msleep이나 delay 없음** → **CPU 100% 스핀!**
- 복구 로직 없음 → 장치가 다시 연결되어도 `stream` 객체가 이미 무효 → **영구 미복구**

**③ 출력 스트림**:
- `output_stream.write()` 실패 → `except: pass` (L119-120) → 안전하지만 역시 복구 없음

**최악의 결과**: **CPU 100% 스핀 + 오디오 모니터링 영구 중단** (장치 재연결해도 복구 안 됨)

**복구 가능성**: **복구 불가** — 프로그램 재시작 필요

**판정: ⚠️ HIGH — CPU 스핀 + 자동 복구 불가**

**권장 수정**:
```python
# audio_monitor.py — 내부 루프에 연속 실패 카운터 + 재연결 로직 추가
consecutive_errors = 0
while self._running:
    try:
        data, overflowed = stream.read(self.CHUNK)
        consecutive_errors = 0
        # ... 정상 처리 ...
    except Exception as e:
        consecutive_errors += 1
        _log.debug("오디오 루프 예외: %s", e)
        self.level_updated.emit(-60.0, -60.0)
        if consecutive_errors >= 10:
            # 스트림 무효 → 재연결 시도
            _log.warning("오디오 스트림 연속 실패 %d회 — 재연결 시도", consecutive_errors)
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self.msleep(2000)
            try:
                stream = sd.RawInputStream(...)
                stream.start()
                consecutive_errors = 0
                self.status_changed.emit("오디오 스트림 재연결 성공")
            except Exception as re_e:
                self.status_changed.emit(f"오디오 재연결 실패: {re_e}")
                self.msleep(5000)
                consecutive_errors = 0  # 다음 10회 후 재시도
```

---

### 8.8 메모리 압박

**진입 조건**: 시스템 RAM 부족으로 numpy 배열 할당 실패

**코드 경로 추적**:

**① `_run_detection()` 내부** (`main_window.py`):
- `detector.detect_frame()` → numpy 연산(resize, mean, abs 등) → `MemoryError`
- try-except (L~530)가 포착 → 로그 출력 + 해당 주기 건너뜀 → **다음 주기에서 재시도**
- **판정: ✅ 안전**

**② `_on_frame_ready()`** (`main_window.py:235-239`):
- `frame.copy()` → `MemoryError` → **try-except 없음!**
- 하지만 PySide6 시그널 슬롯에서 발생한 예외 → stderr 출력 후 계속 (Qt 동작)
- `_latest_frame`이 업데이트 안 됨 → 이전 프레임으로 감지 계속 (정확도 저하, 크래시 아님)
- `_recorder.push_frame(frame)` → `cv2.resize()` 실패 → `except: pass` (L162-163) → **안전**

**③ `VideoCaptureThread.run()`** (`video_capture.py`):
- `cap.read()` → 내부 버퍼 할당 실패 가능 → 외부 try-except (L118) → 재연결 시도 → **안전**

**④ `AudioMonitorThread.run()`** (`audio_monitor.py`):
- `np.frombuffer()`, RMS 계산 → 내부 try-except (L103-148) → -60dB emit → **안전**

**⑤ 바운드된 데이터 구조**:
- `_buffer`: `deque(maxlen=...)` → 고정 크기
- `_audio_buffer`: `deque(maxlen=...)` → 고정 크기
- `_audio_ratio_buffer`: `deque(maxlen=5)` per ROI → 고정 크기
- `_queue` (Telegram): `Queue(maxsize=50)` → 고정 크기
- `_prev_frames`: ROI당 1 프레임 (float32) → ROI 수에 비례 (최대 ~16개)

**최악의 결과**: 감지 정확도 일시 저하, 일부 프레임 누락

**복구 가능성**: **자동 복구됨**. 메모리 확보 시 다음 주기부터 정상.

**판정: ✅ 안전** — 모든 핵심 경로에 예외 처리 존재

---

### 8.9 장기 연속 운영 후 성능 저하

**진입 조건**: 30일, 90일, 365일 무중단 운영

**코드 경로 추적 — 무한 성장 패턴 점검**:

**① 메모리 바운드 확인**:
| 데이터 구조 | 위치 | 상한 | 정리 메커니즘 |
|---|---|---|---|
| `_black_states` | detector.py | ROI 수 | `update_roi_list()` |
| `_still_states` | detector.py | ROI 수 | `update_roi_list()` |
| `_prev_frames` | detector.py | ROI 수 | `update_roi_list()` |
| `_audio_ratio_buffer` | detector.py | ROI 수 × 5 | `update_roi_list()` |
| `_audio_level_states` | detector.py | ROI 수 | `update_roi_list()` |
| `_last_raw` | detector.py | ROI 수 | `update_roi_list()` |
| `_near_miss_start` | detector.py | ROI 수 | 정상 시 `pop()` (L342) |
| `_tone_states` | detector.py | ROI 수 | `update_roi_list()` |
| `_last_sent` | telegram_notifier.py | ~50 | 50초과 시 24h 정리 (L150-154) |
| `_active_alarms` | alarm.py | ROI × 타입 | `resolve()` |
| `_buffer` | auto_recorder.py | maxlen | deque 자동 |
| `_audio_buffer` | auto_recorder.py | maxlen | deque 자동 |
| `_queue` | telegram_notifier.py | 50 | Queue(maxsize=50) |
| `_detection_count` | main_window.py | 무한 | Python bigint → 실질 무영향 |
| `_black_logged` 등 | main_window.py | ROI 수 | resolve 시 discard |
| `_signoff_suppressed_logged` | main_window.py | ROI 수 | 정리 없지만 유한 |

**② 디스크 성장**:
- 로그: `MAX_KEEP_DAYS = 90` → `_delete_old_logs()` 매일 실행 → **90일분만 보관** (~2~3GB)
- 녹화: `max_keep_days` (기본 7일) → `_cleanup_loop()` 1시간마다 실행 → **보관 기간만큼만**

**③ `_detection_count` 오버플로우**:
- Python int: 임의 정밀도 → **오버플로우 없음**
- 365일 × 86400s / 0.2s = ~157,680,000 → 메모리 추가 사용: 수십 바이트 → **무시 가능**

**④ 감지 루프 지연 시간**:
- `detect_frame()`, `detect_audio_roi()`: O(ROI 수) 복잡도 → ROI 수 변하지 않으면 일정
- `_last_raw` dict 접근: O(1) → 시간에 따른 성능 저하 없음

**최악의 결과**: 없음 — 모든 데이터 구조가 바운드됨

**복구 가능성**: 해당 없음 (문제 없음)

**판정: ✅ 안전** — 무한 성장 패턴 없음, 자동 정리 메커니즘 충분

---

### 8.10 Windows 업데이트 후 재시작

**진입 조건**: 프로그램 강제 종료(SIGKILL/프로세스 킬) 후 재시작

**코드 경로 추적**:

**① 임시 파일 정리**:
- `_cleanup_orphan_temp_files()` (auto_recorder.py:84-96): 시작 시 `*_vtmp.mp4`, `*_atmp.wav` 삭제 → **안전**
- config/ 디렉토리 임시 파일: `tempfile.mkstemp()` → 강제 종료 시 `.tmp` 파일 잔류
  - `os.replace()` 전에 크래시하면 `.tmp` 파일 남음 → **정리 로직 없음**
  - 크기: JSON 설정 파일 < 10KB → 실질적 영향 미미

**② 파일 잠금**:
- 로그 파일: `FileHandler`가 파일 열고 있던 상태에서 프로세스 종료 → Windows가 핸들 자동 해제
- OpenCV `VideoCapture`: 프로세스 종료 시 OS가 DirectShow COM 자동 정리
- sounddevice 스트림: PortAudio → 프로세스 종료 시 OS가 오디오 장치 핸들 해제

**③ 좀비 프로세스**:
- 모든 서브스레드가 `daemon=True` → 메인 프로세스 종료 시 자동 종료
- ffmpeg 서브프로세스: `subprocess.run(timeout=120)` → 프로세스 강제 종료 시 ffmpeg도 종료됨
  - **다만**: Windows에서는 부모 프로세스 종료 시 자식이 자동 종료되지 않음
  - ffmpeg이 실행 중이었다면 고아 프로세스로 남을 수 있음 → 녹화 파일 잠금
  - 재시작 시 해당 파일 삭제 시도 → `_cleanup_orphan_temp_files()`가 실패할 수 있음

**④ 설정 파일 일관성**:
- `_write_json()`: `tempfile.mkstemp()` + `os.replace()` (atomic) → **중간 상태 없음**
- 강제 종료 시점:
  - `os.replace()` 전: 원본 JSON 유지 (마지막 정상 저장)
  - `os.replace()` 후: 새 JSON 유지
  - → **설정 파일 손상 없음**

**최악의 결과**: config/ 디렉토리에 소량의 .tmp 파일 잔류, ffmpeg 고아 프로세스 가능성

**복구 가능성**: **자동 복구됨** (녹화 임시 파일 정리). config .tmp 파일은 수동 정리 필요하지만 무해.

**판정: ⚠️ LOW — ffmpeg 고아 프로세스 + config .tmp 파일 잔류**

**참고**: ffmpeg 고아 프로세스는 녹화 도중 강제 종료라는 매우 드문 시나리오에서만 발생하며, 프로세스가 작업 완료 후 자연 종료됨 (timeout=120s 내). 실질적 위험도 매우 낮음.

---

### 결과 기록란

```
발견된 문제:
  - [HIGH] 8.7: AudioMonitorThread — 장치 제거 시 CPU 스핀 + 자동 복구 불가
  - [MEDIUM] 8.2: AutoRecorder._record_queue — maxlen 미설정, 연속 trigger 시 무한 성장 가능
  - [LOW] 8.4: logger._rotate_if_needed() — 디스크 풀 + 날짜 변경 시 예외 미포착
  - [LOW] 8.10: ffmpeg 고아 프로세스 가능성, config .tmp 파일 잔류

수정 사항:
  - (수정 완료) 8.7: audio_monitor.py — 연속 실패 10회 시 스트림 정리 + 3초 대기 후 재연결 시도, 재연결 실패 시 5초 후 재시도
  - (수정 완료) 8.2: auto_recorder.py — _MAX_RECORD_FRAMES=3000 (5분 상한) 추가, push_frame()에서 큐 크기 체크
  - (수정 완료) 8.4: logger.py — _rotate_if_needed() 전체 try-except 보호 + 핸들러 없는 상태 시 기존 날짜 파일로 폴백
  - (수용) 8.10: 실질적 위험도 매우 낮음, 현재 코드로 수용 가능

메모:
  - 8.1, 8.3, 8.5, 8.6, 8.8, 8.9: 코드 레벨에서 안전 확인 — 수정 불필요
  - 전체적으로 try-except 보호, 바운드된 자료구조, 메인 스레드 직렬화가 잘 적용되어 있음
  - 가장 시급한 수정은 8.7 (오디오 장치 제거 시 CPU 스핀) — 현장 운용 중 발생 가능성 있음
```

---

## 부록: 사전 탐색에서 발견된 주요 의심 항목

> 아래는 코드리뷰 시작 전 탐색에서 발견된 항목이다. 각 단계에서 실제 코드를 확인하며 검증해야 한다.

### HIGH 우선순위
| 항목 | 위치 | 의심 내용 | 검토 단계 |
|------|------|-----------|-----------|
| `_last_sent` 무한 성장 | `telegram_notifier.py` | 쿨다운 캐시 키가 만료 없이 축적 | 1 |
| 로그 파일 무한 축적 | `logger.py` | 오래된 로그 삭제 정책 없음 (5년 ≈ 9GB) | 6 |
| `_record_queue` maxlen 미설정 | `auto_recorder.py` | 긴 알람 시 포스트 버퍼 무한 성장 | 1 |
| `_active_alarms` 락 부재 | `alarm.py` | 메인/사운드 스레드 동시 접근 | 2 |

### MEDIUM 우선순위
| 항목 | 위치 | 의심 내용 | 검토 단계 |
|------|------|-----------|-----------|
| `_silence_duration` float 누적 | `audio_monitor.py` | 부동소수점 오차 연간 1~2초 드리프트 | 4 |
| `configure()` 버퍼 재할당 레이스 | `auto_recorder.py` | 설정 변경 중 push_frame 동시 접근 | 2 |
| `_near_miss_start` 무한 보존 | `detector.py` | 경계값 ROI에서 영구 보존 | 1 |
| 텔레그램 워커 이중 재시작 | `telegram_notifier.py` | 동시 notify() 호출 시 이중 스레드 | 2 |
| private 멤버 직접 접근 | `main_window.py` | 캡슐화 위반으로 스레드 안전성 분석 어려움 | 2 |

### LOW 우선순위
| 항목 | 위치 | 의심 내용 | 검토 단계 |
|------|------|-----------|-----------|
| `_detection_count` 무한 증가 | `main_window.py` | Python bigint이므로 실질 문제 없음 | 4 |
| 자정 시간 롤오버 | `signoff_manager.py` | 문자열 비교 방식의 취약점 | 4 |
| 시그널 재연결 가능성 | `main_window.py` | 현재 캐시 설계로 안전하지만 취약 | 7 |
| ffmpeg 좀비 프로세스 | `auto_recorder.py` | timeout 후 미종료 가능성 | 3 |

---

## 최종 요약 (2026-03-22 완료)

### 발견된 문제 총 수: 13건
- CRITICAL: 0건
- HIGH: 1건 (전체 수정 완료)
- MEDIUM: 5건 (전체 수정 완료)
- LOW: 6건 (5건 수정 완료, 1건 수용)
- 예방: 1건 (수정 완료)

### 수정 완료 (12건)

| 단계 | # | 심각도 | 파일 | 내용 |
|------|---|--------|------|------|
| 8 | 8.7 | HIGH | `audio_monitor.py` | 오디오 장치 제거 시 CPU 스핀 + 자동 복구 불가 → 연속 실패 감지 + 스트림 재연결 로직 추가 |
| 1 | 1.4 | MEDIUM | `telegram_notifier.py` | `_last_sent` dict 만료 정리 부재 → 24시간 초과 항목 자동 삭제 추가 |
| 3 | 3.6 | MEDIUM | `auto_recorder.py` | 임시 파일(`_vtmp.mp4`, `_atmp.wav`) 비정상 종료 시 잔존 → outer try-finally + 시작 시 정리 추가 |
| 6 | 6.1 | MEDIUM | `logger.py` | 로그 파일 삭제 정책 부재 (5년 ≈ 9GB) → MAX_KEEP_DAYS=90 자동 삭제 추가 |
| 6 | 6.4 | MEDIUM | `config_manager.py` | atomic write 미사용 (정전 시 설정 손실) → tempfile.mkstemp + os.replace 패턴 적용 |
| 8 | 8.2 | MEDIUM | `auto_recorder.py` | `_record_queue` maxlen 미설정 → _MAX_RECORD_FRAMES=3000 (5분 상한) 추가 |
| 1 | 1.3 | LOW | `detector.py` | `_last_raw` dict stale 키 미정리 → `update_roi_list()`에서 정리 추가 |
| 6 | 6.3 | LOW | `auto_recorder.py` | 비정상 종료 시 임시 파일 잔류 → `start()`에서 잔여 `*_vtmp*`, `*_atmp*` 파일 삭제 |
| 7 | 7.1 | LOW | `main_window.py` | SettingsDialog 닫을 때 `deleteLater()` 미호출 → 추가 |
| 7 | 7.8 | LOW | `main_window.py` | `_tg_test_timer` closeEvent에서 stop() 누락 → 추가 |
| 8 | 8.4 | LOW | `logger.py` | `_rotate_if_needed()` 디스크 풀 시 예외 미포착 → try-except + 폴백 핸들러 추가 |
| 1 | 1.1 | 예방 | `detector.py` | `_tone_states` dict `update_roi_list()`에서 정리 코드 추가 (미래 톤 감지 대비) |

### 미수정 (수용 가능한 리스크, 1건)

| 단계 | # | 심각도 | 파일 | 내용 | 수용 사유 |
|------|---|--------|------|------|-----------|
| 8 | 8.10 | LOW | `auto_recorder.py` | ffmpeg subprocess 타임아웃 시 고아 프로세스 가능성, config `.tmp` 파일 잔류 | `subprocess.run()` 내부 kill 동작으로 실질적 위험 매우 낮음. `.tmp` 파일은 수 KB 수준 |

### 추가 모니터링 권장 사항

- **5분 하트비트 로그 감시**: `logs/YYYYMMDD.txt`에서 "SYSTEM - 감지 정상 실행 중" 줄이 끊기면 silent failure 발생. `_detection_count`가 try 블록 밖에 위치하여 예외 반복 시에도 하트비트는 계속 출력됨
- **DIAG 로그 활용**: 텔레그램 상태(`DIAG-TELEGRAM`), 알람 상태(`DIAG-ALARM`), 오디오 상태(`DIAG-AUDIO`) 로그로 장기 운영 상태 추적 가능
- **녹화 디스크 사용량**: 알림 빈발 시 7일 보관으로도 ~67GB 도달 가능. `max_keep_days`와 디스크 여유 공간 주기적 확인 권장
- **오디오 장치 재연결**: 8.7 수정으로 자동 재연결 구현됨. 로그에서 "오디오 스트림 재연결 성공/실패" 메시지로 장치 상태 확인 가능
- **8시간 주기 비디오 재연결**: `video_capture.py`의 `_PERIODIC_RECONNECT_INTERVAL = 8 * 3600`으로 DirectShow 캡처 장치를 정기 재연결하여 COM 객체 누적 예방. 로그에서 "포트 N 정기 재연결 (freeze 예방)" 메시지로 확인 가능
- **예약 재시작**: `main_window.py`의 10초 주기 타이머로 설정된 시각(기본 03:00)에 프로세스 재시작. GDI 핸들, COM 객체, PortAudio 컨텍스트, Python 메모리 단편화 등 OS 레벨 리소스를 완전 초기화. 로그에서 "SYSTEM - 예약 재시작 실행" 메시지로 확인 가능

### 후속 검토에서 발견된 누락 항목 (2026-03-26)

> 260326_무중단장기운용_수정계획.md에서 기존 LONGEVITY 문서를 재검토한 결과 발견된 항목.

| # | 심각도 | 내용 | 대응 |
|---|--------|------|------|
| 누락 1 | HIGH | Windows GDI 핸들 누적 가능성 — Qt `QImage→QPixmap` 변환 시 GDI 비트맵 핸들 미세 누수. 2~3일 후 10,000개 상한 도달 가능 | 예약 재시작으로 해소. 작업 관리자 GDI 개체 열로 모니터링 가능 |
| 누락 2 | MEDIUM | Python 프로세스 메모리 단편화 — numpy 배열 반복 생성/해제, pymalloc free-list 누적 | 예약 재시작으로 해소 |
| 누락 3 | LOW | PortAudio 글로벌 상태 누적 — 오디오 장치 재연결 시 내부 리소스 불완전 정리 | 예약 재시작으로 해소 |
| 누락 4 | LOW | 8시간 주기 비디오 재연결 미기재 — `_PERIODIC_RECONNECT_INTERVAL` 기능이 이 문서에 누락 | 모니터링 권장 사항에 추가 |

### 전체 평가

8단계 코드리뷰 결과, **CRITICAL 문제는 0건**이며 발견된 13건 중 12건을 수정 완료하였다. 후속 검토에서 누락 4건을 추가 발견하여 예약 재시작 기능으로 대응하였다. 프로젝트의 장기 실행 안정성은 다음 4가지 설계 원칙에 의해 보호되고 있다:

1. **Qt 메인 이벤트 루프 직렬화**: 감지/알림 핵심 로직이 모두 메인 스레드에서 실행되어 레이스 컨디션을 구조적으로 차단
2. **다층 예외 보호**: `_run_detection()` 전체 try-except, ROI별 격리, 스레드별 독립 복구로 silent failure 방지
3. **바운드된 자료구조**: `deque(maxlen)`, `Queue(maxsize)`, 자동 삭제 정책으로 무한 성장 방지
4. **정기 프로세스 재시작**: 매일 예약 시각에 프로세스 완전 재시작으로 OS 레벨 리소스(GDI, COM, PortAudio, 메모리) 초기화

24/7 장기 운용에 필요한 안정성 수준을 충족한다.
