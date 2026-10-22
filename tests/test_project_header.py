"""⏪ 롤백은 헤더 선택값이 아니라 그 기록의 프로젝트(currentRun.project)에 적용돼야 한다."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_rollback_uses_current_run_project():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"function addRollback\(\) \{.*?\n\}\n", js, re.S)
    assert m, "addRollback 을 못 찾음"
    body = m.group(0)
    assert '$("#project")' not in body
    assert "currentRun" in body and ".project" in body


def test_view_placeholder_never_becomes_target():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'VIEW = "__view"' in js, "임시 열람 항목 값이 바뀌면 아래 가드도 같이 확인"
    for fn in ('$("#startBtn").onclick', '$("#sessStart").onclick'):
        i = js.find(fn)
        assert i >= 0, f"{fn} 를 못 찾음"
        assert 'startsWith("__")' in js[i:i + 600], f"{fn} 에 __ 값 가드가 없음"
    assert 'localStorage.setItem("pocketdev.project"' in js
    for m in re.finditer(r'localStorage\.setItem\("pocketdev\.project", ([^)]+)\)', js):
        assert "VIEW" not in m.group(1)
