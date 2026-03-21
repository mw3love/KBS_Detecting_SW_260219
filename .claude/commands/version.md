버전 업데이트 및 커밋/푸시를 수행합니다.

인수 형식: `1.x.x` (예: `/version 1.5.6`)

**아래 단계를 순서대로 실행하세요:**

## 1단계: 인수 확인

`$ARGUMENTS`가 비어 있으면 현재 버전(`kbs_monitor/main.py`에서 확인)을 알려주고 중단하세요.

`$ARGUMENTS`가 비어 있지 않으면 버전 문자열은 `v$ARGUMENTS` 형식입니다 (예: 입력 `1.5.6` → `v1.5.6`).

## 2단계: 오늘 날짜 확인

Bash 도구로 오늘 날짜를 가져오세요:
```bash
date +%Y-%m-%d
```

## 3단계: 3개 파일 버전 업데이트

아래 3개 위치를 모두 수정하세요 (Edit 도구 사용):

**파일 1** — `kbs_monitor/main.py`
- 변경 대상: `app.setApplicationName("KBS Peacock v...)` 줄의 버전 부분
- 새 값: `app.setApplicationName("KBS Peacock v$ARGUMENTS")`

**파일 2** — `kbs_monitor/ui/main_window.py`
- 변경 대상: `self.setWindowTitle("KBS Peacock v...)` 줄의 버전 부분
- 새 값: `self.setWindowTitle("KBS Peacock v$ARGUMENTS")`

**파일 3** — `kbs_monitor/ui/settings_dialog.py`
- 변경 대상 1: `QLabel("KBS Peacock v...)` 버전 라벨
- 새 값: `QLabel("KBS Peacock v$ARGUMENTS")`
- 변경 대상 2: `QLabel("20...` 날짜 라벨 (4자리 연도로 시작하는 날짜)
- 새 값: `QLabel("오늘날짜")` (2단계에서 가져온 날짜)

## 4단계: CLAUDE.md 버전 업데이트

`CLAUDE.md` 파일의 현재 버전 표기를 새 버전으로 업데이트하세요:
- `**현재: Phase 5 완료 (코드 최적화 완료) + v...` 부분의 버전 번호를 `v$ARGUMENTS`로 변경

## 5단계: 변경 확인 후 커밋

수정한 파일들을 git add하고 커밋하세요:

```bash
git add kbs_monitor/main.py kbs_monitor/ui/main_window.py kbs_monitor/ui/settings_dialog.py CLAUDE.md
```

커밋 메시지 형식:
```
v$ARGUMENTS: 버전 표기 및 About 날짜 업데이트
```

## 6단계: 푸시

```bash
git push
```

푸시 완료 후 결과를 한국어로 요약해서 알려주세요.
