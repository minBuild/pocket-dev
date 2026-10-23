"""기록 정렬 — 마지막 user 이벤트 시각 최신순."""
import asyncio

from server import runs
from server.runs import Run


def _run(tmp_path, monkeypatch, created, events):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    r = Run("p", "idea", {})
    r.created, r.events = created, events
    return r


def test_only_user_events_count(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, 100, [
        {"type": "user", "ts": 110, "text": "q"},
        {"type": "user_reply", "ts": 200, "key": "plan", "action": "approve"},
        {"type": "stage", "ts": 210, "stage": "execute"},
        {"type": "status", "ts": 220, "status": "done"},
        {"type": "message_end", "ts": 230, "msg_id": "m"},
        {"type": "log", "ts": 240, "text": "x"},
    ])
    assert r.last_user_at == 110
    assert r.summary()["last_user_at"] == 110


def test_no_user_event_falls_back_to_created(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, 100, []).last_user_at == 100


def test_list_key_order():
    rows = [{"id": "a", "created": 300, "last_user_at": 300},
            {"id": "b", "created": 100, "last_user_at": 500},   # 오래된 기록에 새 질문 → 맨 위
            {"id": "c", "created": 200, "last_user_at": 300},   # a 와 동률 → created 최신(a) 먼저
            {"id": "d", "created": 400}]                        # 옛 서버 형식 → created 로
    assert [r["id"] for r in sorted(rows, key=runs.list_key)] == ["b", "d", "a", "c"]


def test_emit_user_moves_up(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    r = Run("p", "idea", {})
    r.created, r.events = 100, [{"type": "user", "ts": 100, "text": "first"}]
    asyncio.run(r.emit("user", text="again", chat_id="c1"))
    assert r.last_user_at > 100
    before = r.last_user_at
    asyncio.run(r.emit("user_reply", key="plan", action="approve", text=""))
    asyncio.run(r.emit("status", status="done"))
    assert r.last_user_at == before
