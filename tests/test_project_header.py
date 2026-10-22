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
