"""표시 상태(working/waiting)·대기 정보·채팅 완료 요약 추적 — emit() 없이 이벤트와 waiters 를 직접 넣는다."""
import asyncio

from server import runs
from server.runs import (FEEDBACK_DONE, MAX_OPEN_CHATS, Run, busy_reply, count_stat_files, display_status, done_line,
                         norm_title, wait_info)


# ── display_status ──
def test_display_status():
    assert display_status("working", ["perm:a"]) == "waiting"
    assert display_status("working", ["plan"]) == "waiting"
    assert display_status("working", ["limit:x"]) == "working"
    assert display_status("working", []) == "working"
    assert display_status("done", ["perm:a"]) == "done"
    assert display_status("error", []) == "error"


# ── wait_info ──
def test_wait_info_perm():
    e = {"kind": "permission", "label": "🖱 클릭", "expires_at": 100}
    assert wait_info([e]) == {"kind": "perm", "label": "🖱 클릭", "until": 100}


def test_wait_info_perm_falls_back_to_tool():
    assert wait_info([{"kind": "permission", "tool": "Bash"}])["label"] == "Bash"


def test_wait_info_plan_over_limit_and_limit_alone():
    plan, limit = {"kind": "plan"}, {"kind": "limit", "label": "구현", "retry_at": 50}
    assert wait_info([limit, plan])["kind"] == "plan"
    assert wait_info([limit]) == {"kind": "limit", "label": "구현", "until": 50}
    assert wait_info([]) is None


# ── summary ──
def test_summary_title_and_status():
    r = Run("p", "i", {})
    sm = r.summary()
    assert sm["title"] == "" and sm["status"] == "working" and sm["wait"] is None
    r.waiters["plan"] = object()
    assert r.summary()["status"] == "waiting"


def test_summary_wait_from_pending_event():
    r = Run("p", "i", {})
    r.events.append({"type": "awaiting_user", "ts": 0, "key": "perm:1", "kind": "permission",
                     "label": "🖱 클릭 '앱 추가'", "expires_at": 9})
    r.waiters["perm:1"] = object()
    assert r.summary()["wait"] == {"kind": "perm", "label": "🖱 클릭 '앱 추가'", "until": 9}


# ── wait_for 정리 ──
def test_wait_for_cancel_removes_key():
    async def main():
        r = Run("p", "i", {})
        fut = r.wait_for("plan")
        fut.cancel()
        await asyncio.sleep(0)
        return r.waiters
    assert "plan" not in asyncio.run(main())


def test_wait_for_timeout_removes_key():
    async def main():
        r = Run("p", "i", {})
        try:
            await asyncio.wait_for(r.wait_for("limit:x"), 0.01)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0)
        return r.waiters
    assert "limit:x" not in asyncio.run(main())


def test_wait_for_resolve_passes_value():
    async def main():
        r = Run("p", "i", {})
        fut = r.wait_for("perm:a")
        assert r.resolve("perm:a", {"allow": True})
        value = await fut
        return r.waiters, value
    waiters, value = asyncio.run(main())
    assert "perm:a" not in waiters and value == {"allow": True}


# ── open_chats ──
def test_track_chat_none_id_noop():
    r = Run("p", "i", {})
    r.track_chat(None, "note", "x", True)
    assert r.open_chats == []


def test_queued_not_popped_until_taken():
    r = Run("p", "i", {})
    r.track_chat("c1", "note", "  지시   하나 ", False)
    assert r.pop_taken_chats() == []
    r.mark_chats_taken()
    out = r.pop_taken_chats()
    assert [c["id"] for c in out] == ["c1"] and out[0]["quote"] == "지시 하나"
    assert r.open_chats == []


def test_pop_taken_by_kind():
    r = Run("p", "i", {})
    r.track_chat("f", "feedback", "고쳐", True)
    r.track_chat("u", "followup", "더 해", True)
    assert [c["id"] for c in r.pop_taken_chats(("feedback",))] == ["f"]
    assert [c["id"] for c in r.open_chats] == ["u"]


def test_open_chats_capped_and_deduped():
    r = Run("p", "i", {})
    for i in range(MAX_OPEN_CHATS + 1):
        r.track_chat(f"c{i}", "note", "x", True)
    assert len(r.open_chats) == MAX_OPEN_CHATS and r.open_chats[0]["id"] == "c1"
    r.track_chat("c5", "note", "y", False)
    assert [c["id"] for c in r.open_chats].count("c5") == 1
    assert r.open_chats[-1]["state"] == "queued"


# ── done_line ──
def test_done_line_ok():
    assert done_line("feat: x", 3, None, False, 0) == "✅ 완료 — feat: x · 파일 3개"


def test_done_line_warning_title():
    assert (done_line("feat: x", 3, "⚠️ 코드는 완료 · Chrome 작업 미완료", False, 2)
            == "⚠️ 코드는 완료 · Chrome 작업 미완료 — feat: x (자세히는 위 결과 카드)")


def test_done_line_push_failed():
    assert done_line("feat: x", 1, None, True, 0).endswith(" · push 안 됨")


def test_done_line_no_change_with_chrome():
    assert done_line("", 0, None, True, 4) == "ℹ️ 코드 변경 없이 마쳤어요 · 🌐 Chrome 성공 4단계"


def test_done_line_max_length():
    assert len(done_line("x" * 500, 9, "⚠️ " + "y" * 300, True, 0)) <= 160


# ── 기타 ──
def test_count_stat_files():
    assert count_stat_files(" a.py | 2 +-\n b.js | 1 +\n 2 files changed") == 2
    assert count_stat_files("") == 0


def test_feedback_done_text():
    assert FEEDBACK_DONE.startswith("✏️")


# ── busy_reply · norm_title ──
def test_busy_reply():
    mode, msg = busy_reply("waiting")
    assert mode == "waiting" and "결정" in msg
    assert busy_reply("working") == ("busy", "ℹ️ 이미 진행 중이에요")


def test_norm_title():
    assert norm_title("  a\t\nb  ") == "a b"
    assert norm_title("x" * 101) == "x" * 100
    assert norm_title(None) == "" and norm_title("") == ""
    assert norm_title("<b>x</b>") == "<b>x</b>"


def test_title_save_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runs, "RUNS", {})
    r = Run("p", "idea", {})
    r.title = norm_title(" 새 이름 ")
    r.status = "done"
    r.save()
    runs.load_saved_runs()
    assert runs.RUNS[r.id].title == "새 이름"
