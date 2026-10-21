"""화면 문구 회귀 방지 — 사용자에게 보이는 상태는 working/waiting (내부 식별자 .bstep.running 등은 허용)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUOTED = re.compile(r"""["']running["']""")


def test_no_quoted_running():
    files = [*(ROOT / "server").glob("*.py"), ROOT / "web" / "app.js", ROOT / "web" / "index.html"]
    hits = [f"{p.name}:{i}" for p in files
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if QUOTED.search(line)]
    assert hits == []


def test_no_status_running_css():
    assert ".status.running" not in (ROOT / "web" / "style.css").read_text(encoding="utf-8")
