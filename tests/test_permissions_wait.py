"""권한 결정을 기다리면 waiting, 처리되면 working — 실제 permissions.decide() 경로 (AI 심사는 가짜)."""
import asyncio

import pytest

from server import browser, permissions, runs
from server.runs import Run

BASH = ("Bash", {"command": "npm test"})
CLICK = (browser.PREFIX + "computer", {"action": "left_click", "coordinate": [1, 2]})


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)   # emit → save 가 실제 data/ 를 건드리지 않게
    monkeypatch.setitem(permissions.CONFIG["executor"], "permission_policy", "unanimous")
    monkeypatch.setitem(permissions.CONFIG["executor"], "on_disagreement", "ask_human")
    monkeypatch.setattr(permissions, "_allowed", lambda: [])   # Chrome 계정 게이트 끔
    gate = {}

    async def fake(r, spec, project, tool, inp, label="", note=""):
        await gate["ev"].wait()
        return {"reviewer": r["model"], "decision": "allow" if r["model"] == "a" else "ask_human", "reason": "t"}
    monkeypatch.setattr(permissions, "judge_permission", fake)
    run = Run(str(tmp_path), "idea", {"permission_reviewers": [{"model": "a"}, {"model": "b"}]})
    return run, gate


async def _until_waiting(run):
    for _ in range(200):
        key = next((k for k in run.waiters if k.startswith("perm:")), None)
        if key:
            return key
        await asyncio.sleep(0)
    raise AssertionError("perm 대기가 생기지 않음")


def _flow(run, gate, tool, inp, answer=True):
    async def main():
        gate["ev"] = asyncio.Event()
        task = asyncio.create_task(permissions.decide(run, tool, inp))
        for _ in range(5):
            await asyncio.sleep(0)
        assert run.summary()["status"] == "working"   # AI 투표 중은 working (D2)
        gate["ev"].set()
        key = await _until_waiting(run)
        sm = run.summary()
        assert sm["status"] == "waiting" and sm["wait"]["kind"] == "perm"
        if answer:
            run.resolve(key, {"allow": True})
        res = await task
        await asyncio.sleep(0)   # permission_decision emit 마무리
        return sm, res
    return asyncio.run(main())


def test_preconditions(tmp_path):
    assert permissions.hard_rule(str(tmp_path), *BASH) is None
    assert permissions.hard_rule(str(tmp_path), *CLICK) is None
    assert not browser.auto_allow(*CLICK)


def test_perm_waiting_then_working(env):
    run, gate = env
    _, res = _flow(run, gate, *BASH)
    assert res["behavior"] == "allow"
    assert run.summary()["status"] == "working" and run.waiters == {}


def test_perm_timeout_back_to_working(env, monkeypatch):
    run, gate = env
    monkeypatch.setitem(permissions.CONFIG["executor"], "human_timeout_sec", 0.05)
    _, res = _flow(run, gate, *BASH, answer=False)
    assert res["behavior"] == "deny" and "[timeout]" in res["message"]
    assert run.summary()["status"] == "working" and run.waiters == {}


def test_chrome_perm_waiting(env):
    run, gate = env
    sm, res = _flow(run, gate, *CLICK)
    assert sm["wait"]["label"] != ""
    assert res["behavior"] == "allow"
    assert run.summary()["status"] == "working" and run.waiters == {}


def test_final_status_wins():
    run = Run("p", "idea", {})
    run.waiters["perm:x"] = object()
    run.status = "done"
    assert run.summary()["status"] == "done"
