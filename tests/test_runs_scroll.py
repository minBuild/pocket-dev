"""기록 목록 스크롤 — 진입하면 맨 위, 제자리 재렌더는 위치 유지, 늦은 터미널 스크롤이 목록을 끌어내리지 않음."""
import re
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")


def _body(sig: str) -> str:
    i = JS.index(sig)
    return JS[i:JS.index("\n}\n", i)]


def test_runs_tab_starts_at_top():
    body = _body("function show(tab) {")
    assert 'if (tab === "runs") scrollTo(0, 0);' in body, "기록 탭에 들어오면 맨 위부터 (runs 조건 필수)"
    assert 'if (tab === "runs") loadRuns();' in body


def test_list_rerender_keeps_position():
    body = _body("async function loadRuns() {")
    assert not re.search(r"scrollTo|scrollTop|scrollIntoView|scrollBy", body), "✏️·🗑·앱 복귀 재렌더는 자리 유지"


def test_terminal_scroll_stays_on_detail():
    body = _body("async function runTerminal(")
    assert 'if (!$("#tab-run").hidden) scrollTo(0, document.body.scrollHeight);' in body, \
        "명령이 끝났을 때 상세를 떠나 있으면 기록 목록을 끝으로 내리지 않음"
