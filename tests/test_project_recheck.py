"""상단 레포 재확인(목록에 없는 기록) — 늦게 온 응답이 사용자 선택을 덮지 않게, 응답에 project 가 있게."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    i = JS.find(start)
    assert i >= 0, f"{start} 를 못 찾음"
    j = JS.find(end, i)
    assert j > i, f"{start} 의 끝을 못 찾음"
    return JS[i:j]


def test_recheck_responses_are_generation_guarded():
    view = _block("projectBar.view = (path, recheck = true) => {", "\n  };\n")
    assert "++viewGen" in view and "recheckProjects(path, gen)" in view
    assert "viewGen++" in _block("projectBar.unview = () => {", "\n  };\n")
    onchange = _block("sel.onchange = async () => {", "\n  };\n")
    final = [ln for ln in onchange.splitlines() if "last = chosen = sel.value" in ln]
    assert final and "viewGen++" in final[0], "직접 선택 확정 줄에서 세대를 올려야 늦은 재확인 응답이 선택을 덮지 않음"
    rc = _block("const recheckProjects = (path, gen) => {", "\n  };\n")
    assert rc.count("gen === viewGen") >= 2, "성공·실패 두 경로 모두 세대 확인"
    assert "rechecked.add(path)" in rc


def test_summaries_carry_project(tmp_path, monkeypatch):
    from server import runs, sessions
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    p = r"C:\Users\x\IdeaProjects\demo"
    assert runs.Run(p, "idea", {}).summary()["project"] == p
    assert sessions.ChatSession(p, "claude-sonnet-5-5").summary()["project"] == p
