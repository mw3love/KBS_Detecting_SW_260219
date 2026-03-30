# ui/ PySide6 위젯 생성 규칙

> `settings_dialog.py` 및 모든 `ui/` 파일 수정 시 반드시 준수.

---

## QScrollArea 내부 위젯 패턴

탭 함수에서 `QScrollArea` + 내부 `QWidget` 패턴 사용 시 **반드시 아래 순서**를 지킨다:

```python
# ✅ 올바른 순서
inner = QWidget()
scroll.setWidget(inner)      # ← inner 생성 직후 즉시 호출 (Qt 소유권 이전)
layout = QVBoxLayout(inner)
# ... 위젯 추가 ...
return scroll

# ❌ 잘못된 순서 (함수 끝에서 setWidget 호출)
inner = QWidget()
layout = QVBoxLayout(inner)
# ... 위젯 추가 ...
scroll.setWidget(inner)      # ← 너무 늦음: 중간에 GC가 inner를 삭제할 수 있음
return scroll
```

**이유**: `inner = QWidget()`은 부모 없이 생성된다. `setWidget()` 전까지 Python 로컬 변수만이
유일한 참조이므로 함수 실행 중 Python GC가 `inner`(와 자식 `layout`)를 삭제할 수 있다.
`setWidget()` 즉시 호출로 Qt가 소유권을 가져가면 GC 삭제가 방지된다.

---

## 레이아웃 변수명 충돌 금지

한 함수 안에서 **`inner`, `layout` 등 범용 변수명을 재사용하지 않는다**.

```python
# ❌ 버그 유발 — inner를 덮어씌워 QWidget 참조 소멸 → GC 삭제 → RuntimeError
inner = QWidget()
layout = QVBoxLayout(inner)
group = QGroupBox("...")
inner = QHBoxLayout(group)   # ← inner 덮어씌움! 원래 QWidget 참조 소멸

# ✅ 올바른 패턴 — 역할이 명확한 이름 사용
inner = QWidget()
scroll.setWidget(inner)
layout = QVBoxLayout(inner)
group = QGroupBox("...")
group_row = QHBoxLayout(group)   # ← 별도 변수명 사용
```

**증상**: `RuntimeError: Internal C++ object (PySide6.QtWidgets.QVBoxLayout) already deleted`
→ 이 오류가 발생하면 변수명 덮어쓰기 또는 `setWidget` 호출 순서 문제를 먼저 확인한다.
