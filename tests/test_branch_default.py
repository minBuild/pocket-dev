"""새 작업 브랜치 칸은 저장값이 아니라 config 기본값으로 시작 · 기록 탭은 열 때마다 다시 조회."""
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")


def test_branch_reset_after_localstorage_merge():
    i = JS.index("async function startApp() {")
    merge = JS.index("JSON.parse(localStorage.getItem(LS_KEY)", i)
    reset = JS.index("state.branch = CFG.branch", i)
    render = JS.index("renderTeam();", i)
    assert merge < reset < render, "state.branch = CFG.branch 는 LS 병합 뒤, 첫 renderTeam() 앞"
    start = JS.index('$("#startBtn").onclick')
    post = JS.index('post("/api/runs"', start)
    assert "state.branch = CFG.branch" in JS[post:post + 400], "작업 시작 성공 후 브랜치 칸 되돌리기"


def test_runs_tab_reloads_list():
    i = JS.index("function show(tab) {")
    body = JS[i:JS.index("\n}\n", i)]
    assert 'if (tab === "runs") loadRuns();' in body, "질문 뒤 기록 탭으로 오면 새 순서가 보여야 함"
    assert "projectBar.refresh()" in body


def test_project_bar_stub_has_refresh():
    assert "refresh() {}" in JS[:2000]
