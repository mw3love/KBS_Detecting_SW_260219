버전 업데이트 및 커밋/푸시를 수행합니다.

인수 형식: `1.x.x` (예: `/version 1.5.6`)

**아래 단계를 순서대로 실행하세요:**

## 1단계: 인수 확인

`$ARGUMENTS`가 비어 있으면 `kbs_monitor/main.py`에서 현재 버전을 읽은 뒤 **패치 버전(세 번째 숫자)을 자동으로 +1**하여 그 버전으로 진행하세요 (사용자에게 묻지 않음). 자동 결정된 버전을 한 줄로 알리고 바로 다음 단계로 넘어가세요.

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

## 5단계: README.md 버전 이력 업데이트

`README.md`의 "버전 업데이트 이력" 테이블 맨 윗줄에 새 버전을 추가하세요.

형식:
```
| **v$ARGUMENTS** | (사용자에게 변경사항 설명을 물어보거나, 커밋 메시지에서 추론한 핵심 변경사항 한 줄) |
```

이전 최신 버전의 `**굵음**` 강조를 일반 텍스트로 변경하세요 (새 버전만 굵게 표시).

## 6단계: 릴리즈 노트 문서 생성

`Fix/릴리즈노트/` 폴더에 릴리즈 노트 파일을 생성하세요.

파일명: `Fix/릴리즈노트/v$ARGUMENTS.md`

내용은 이전 버전 이후의 git 커밋 로그를 분석하여 작성합니다:

```bash
git log --oneline $(git tag --sort=-version:refname | head -1)..HEAD 2>/dev/null || git log --oneline -20
```

문서 구조:
```markdown
# v$ARGUMENTS 릴리즈 노트

**릴리즈 날짜**: 오늘날짜

## 변경사항 요약
(커밋 메시지를 분석하여 핵심 변경사항을 카테고리별로 정리)

### 버그 수정
- (fix: 커밋에서 추출)

### 개선
- (docs/chore/refactor 커밋에서 추출)

## 커밋 이력
(해당 버전에 포함된 커밋 목록, 해시 앞 7자리 + 메시지)

## 정적 검증 결과
(이번 버전에 eval-plan/eval-freeze 결과가 있으면 요약, 없으면 "해당 없음")
```

> `Fix/릴리즈노트/` 폴더가 없으면 Write 도구로 파일 생성 시 자동으로 만들어집니다.

## 7단계: 변경 확인 후 커밋

수정한 파일들을 git add하고 커밋하세요:

```bash
git add kbs_monitor/main.py kbs_monitor/ui/main_window.py kbs_monitor/ui/settings_dialog.py CLAUDE.md README.md "Fix/릴리즈노트/v$ARGUMENTS.md"
```

커밋 메시지 형식:
```
v$ARGUMENTS: 버전 표기 및 About 날짜 업데이트
```

## 8단계: 푸시

```bash
git push
```

푸시 완료 후 결과를 한국어로 요약해서 알려주세요.
