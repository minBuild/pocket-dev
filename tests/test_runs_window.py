"""Run.window() 의 채팅 질문(user) 당기기와 재시작 때 끊긴 메시지 마감."""
from server.runs import Run, close_orphan_messages


def _ev(type_, **kw):
    return {"type": type_, "ts": 0, "msg_id": None, "req_id": None, **kw}


def _run(n_logs, reply="message_start"):
    r = Run("p", "i", {})
    r.events.append(_ev("user", text="질문", chat_id="c1"))
    r.events += [_ev("log", text=f"l{i}") for i in range(n_logs)]
    if reply == "message_start":
        r.events.append(_ev("message_start", kind="assistant", msg_id="m1", reply_to="c1"))
        r.events.append(_ev("message_end", msg_id="m1", text="답"))
    else:
        r.events.append(_ev("chat_action", reply_to="c1", action="note", text="📌"))
    return r


def test_window_pulls_user_question():
    start, _ = _run(2).window(None, 3)
    assert start == 0


def test_window_does_not_pull_far_user():
    r = _run(10)
    start, _ = r.window(None, 3)
    assert start == len(r.events) - 3


def test_window_pulls_user_for_chat_action():
    start, _ = _run(2, reply="chat_action").window(None, 2)
    assert start == 0


def test_window_old_events_unchanged():
    r = Run("p", "i", {})
    r.events.append(_ev("user", text="질문"))
    r.events += [_ev("log", text="l"), _ev("log", text="l")]
    r.events.append(_ev("message_start", kind="assistant", msg_id="m1"))
    r.events.append(_ev("message_end", msg_id="m1", text="답"))
    start, _ = r.window(None, 1)
    assert start == 3   # message_start 까지만 당김 (user 는 그대로)


def test_close_orphan_messages():
    evs = [_ev("message_start", msg_id="m1"), _ev("message_start", msg_id="m2"), _ev("message_end", msg_id="m2")]
    out = close_orphan_messages(evs, 5.0)
    assert len(out) == 1 and out[0]["msg_id"] == "m1" and out[0]["error"] is True and out[0]["ts"] == 5.0
    assert close_orphan_messages(evs[1:], 5.0) == []
