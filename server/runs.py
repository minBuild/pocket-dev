"""실행(Run) 상태 저장 + 이벤트 스트리밍(SSE) 허브 + 재개용 체크포인트."""
import asyncio
import json
import re
import time
import uuid
from typing import Any, Optional

from .config import RUNS_DIR


class Run:
    def __init__(self, project: str, idea: str, settings: dict, run_id: Optional[str] = None):
        self.id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.project = project
        self.idea = idea
        self.settings = settings
        self.status = "running"      # running | awaiting_user | done | error | cancelled
        self.stage = "init"
        self.created = time.time()
        self.events: list[dict] = []          # delta 를 제외한 이벤트 기록
        self.partials: dict[str, dict] = {}   # 스트리밍 중인 메시지
        self.subscribers: set[asyncio.Queue] = set()
        self.waiters: dict[str, asyncio.Future] = {}  # 사람 응답 대기
        self.task: Optional[asyncio.Task] = None
        self.spec: str = ""
        self.perm_cache: dict[str, tuple[bool, str]] = {}  # 같은 권한 요청 재심사 방지
        self.base_branch: str = ""  # 작업 브랜치를 만들기 전 원래 브랜치 (새 저장소에 함께 push)
        # ── 재개용 ──
        self.history: list[dict] = []   # 토론 기록 {round, pid, name, model, text}
        self.checkpoint: dict = {"phase": "debate", "round_from": 1,
                                 "round_until": settings.get("max_rounds", 2)}
        self.notes: list[str] = []      # 진행 중에 받은 사용자 메시지 (다음 단계에 반영)

    @property
    def alive(self) -> bool:
        return self.task is not None and not self.task.done()

    # ── 이벤트 ──
    async def emit(self, type_: str, **data: Any) -> dict:
        ev = {"type": type_, "ts": time.time(), **data}
        if type_ == "delta":
            p = self.partials.get(data["msg_id"])
            if p is not None:
                p["text"] += data["text"]
        else:
            if type_ == "message_start":
                self.partials[data["msg_id"]] = {**ev, "text": ""}
            elif type_ == "tool" and data["msg_id"] in self.partials:
                self.partials[data["msg_id"]]["text"] = ""  # 도구 사용 전 중간 멘트는 버림
            elif type_ == "message_end":
                self.partials.pop(data["msg_id"], None)
            elif type_ == "stage":
                self.stage = data["stage"]
            elif type_ == "status":
                self.status = data["status"]
            self.events.append(ev)
            self.save()
        for q in list(self.subscribers):
            q.put_nowait(ev)
        return ev

    def snapshot_events(self) -> list[dict]:
        evs = list(self.events)
        for p in self.partials.values():
            evs.append({"type": "delta", "msg_id": p["msg_id"], "text": p["text"], "ts": p["ts"]})
        return evs

    # ── 사람 응답 대기 ──
    def wait_for(self, key: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self.waiters[key] = fut
        return fut

    def resolve(self, key: str, value: Any) -> bool:
        fut = self.waiters.pop(key, None)
        if fut and not fut.done():
            fut.set_result(value)
            return True
        return False

    # ── 저장 ──
    def summary(self) -> dict:
        return {"id": self.id, "project": self.project, "idea": self.idea,
                "status": self.status, "stage": self.stage, "created": self.created,
                "pending": list(self.waiters.keys()), "alive": self.alive,
                "phase": self.checkpoint.get("phase")}

    def save(self) -> None:
        data = {**self.summary(), "settings": self.settings, "events": self.events, "spec": self.spec,
                "history": self.history, "checkpoint": self.checkpoint, "notes": self.notes}
        (RUNS_DIR / f"{self.id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def reconstruct(run: Run) -> None:
    """체크포인트가 없던 예전 기록 → 이벤트 로그에서 토론 기록과 진행 단계를 복원."""
    names = {p.get("name"): p.get("id") or p.get("name") for p in run.settings.get("participants", [])}
    rnd, until, starts, phase = 1, run.settings.get("max_rounds", 2), {}, "debate"
    approved = False
    for ev in run.events:
        t = ev["type"]
        if t == "stage":
            m = re.match(r"설계 토론 (\d+)/(\d+)", ev.get("label", ""))
            if m:
                rnd, until = int(m.group(1)), int(m.group(2))
            phase = {"spec": "spec", "plan_review": "plan_review", "execute": "execute",
                     "code_review": "code_review", "git": "git", "done": "done"}.get(ev["stage"], phase)
        elif t == "message_start" and ev.get("kind") == "debate":
            starts[ev["msg_id"]] = ev
        elif t == "message_end" and ev["msg_id"] in starts and not ev.get("error"):
            s = starts[ev["msg_id"]]
            run.history.append({"round": rnd, "pid": names.get(s["name"], s["name"]), "name": s["name"],
                                "model": s["model"], "text": ev["text"]})
        elif t == "awaiting_user" and ev.get("kind") == "plan":
            phase = "plan_gate"
        elif t == "user_reply" and ev.get("key") == "plan" and ev.get("action") == "approve":
            approved = True
    if phase in ("spec", "plan_review") and not run.spec:
        phase = "spec"
    if phase == "plan_gate" and approved:
        phase = "execute"
    run.checkpoint = {"phase": phase, "round_from": 1, "round_until": until,
                      "branch_ready": phase in ("execute", "code_review", "git"),
                      "impl_started": phase in ("execute", "code_review", "git")}


RUNS: dict[str, Run] = {}


def load_saved_runs() -> None:
    for path in sorted(RUNS_DIR.glob("*.json"))[-50:]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        r = Run(d["project"], d["idea"], d.get("settings", {}), run_id=d["id"])
        r.events, r.stage, r.created, r.spec = d["events"], d["stage"], d["created"], d.get("spec", "")
        r.history, r.notes = d.get("history", []), d.get("notes", [])
        if d.get("checkpoint"):
            r.checkpoint = d["checkpoint"]
        else:
            reconstruct(r)
        # 서버 재시작으로 끊긴 실행은 '멈춤' 처리 → 채팅이나 '이어서 진행' 으로 재개
        r.status = d["status"] if d["status"] in ("done", "error", "cancelled") else "error"
        if d["status"] not in ("done", "error", "cancelled"):
            r.events.append({"type": "error", "ts": time.time(),
                             "text": "서버가 다시 시작돼 중단됐어요 — '이어서 진행'으로 재개할 수 있어요"})
            r.events.append({"type": "status", "ts": time.time(), "status": "error"})
        RUNS[r.id] = r
