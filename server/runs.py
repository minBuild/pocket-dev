"""실행(Run) 상태 저장 + 이벤트 스트리밍(SSE) 허브 + 재개용 체크포인트."""
import asyncio
import json
import re
import shutil
import time
import uuid
from typing import Any, Optional

from . import browser
from .config import RUNS_DIR

FINAL = ("done", "error", "cancelled")
MAX_OPEN_CHATS = 20
FEEDBACK_DONE = "✏️ 기획안을 다시 썼어요 — 아래에서 승인/수정해 주세요"


def display_status(status: str, waiter_keys) -> str:
    """목록·snapshot 에 보일 상태 — 사람 결정 대기(plan·권한)면 waiting."""
    if status in FINAL:
        return status
    return "waiting" if any(k == "plan" or str(k).startswith("perm:") for k in waiter_keys) else "working"


def busy_reply(display: str) -> tuple[str, str]:
    """진행 중인 작업에 '이어서 진행' 을 보냈을 때 (mode, 안내 문구)."""
    if display == "waiting":
        return "waiting", "✋ 내 결정을 기다리고 있어요 — 아래 🚀 승인 또는 ✅ 허용/⛔ 거부를 눌러 주세요"
    return "busy", "ℹ️ 이미 진행 중이에요"


def norm_title(s) -> str:
    """대화명 정규화 — 공백·탭·개행은 한 칸, 앞뒤 공백 제거, 100자. HTML 이스케이프는 표시하는 쪽 책임."""
    return " ".join(str(s or "").split())[:100]


def wait_info(pending: list[dict]) -> dict | None:
    """대기 중 요청 → {kind: perm|plan|limit, label, until(epoch|None)}. 권한 > 기획안 > 한도 순."""
    by = {}
    for e in pending or []:
        by[e.get("kind")] = e
    if e := by.get("permission"):
        return {"kind": "perm", "label": str(e.get("label") or e.get("tool") or "")[:120], "until": e.get("expires_at")}
    if "plan" in by:
        return {"kind": "plan", "label": "", "until": None}
    if e := by.get("limit"):
        return {"kind": "limit", "label": str(e.get("label") or "")[:120], "until": e.get("retry_at")}
    return None


def count_stat_files(diff: str) -> int:
    """staged_diff 앞부분 --stat 의 파일 줄 수."""
    return len(re.findall(r"^ (\S+)\s+\|", diff or "", re.M))


def done_line(subject: str, files: int, title: str | None, push_failed: bool, chrome_ok: int) -> str:
    """채팅 묶음 아래 완료 요약 1줄 (AI 없이, 결과 카드와 같은 사실만)."""
    subject = (subject or "").strip()[:90]
    if not subject:
        s = "ℹ️ 코드 변경 없이 마쳤어요" + (f" · 🌐 Chrome 성공 {chrome_ok}단계" if chrome_ok else "")
    elif title and title.startswith("⚠️"):
        s = f"{title} — {subject} (자세히는 위 결과 카드)"
    else:
        s = f"✅ 완료 — {subject} · 파일 {files}개"
    if subject and push_failed:
        s += " · push 안 됨"
    return s[:160]


class Run:
    def __init__(self, project: str, idea: str, settings: dict, run_id: Optional[str] = None):
        self.id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.project = project
        self.idea = idea
        self.settings = settings
        self.status = "working"      # working | done | error | cancelled (waiting 은 summary 에서 계산)
        self.title: str = ""         # 사용자가 바꾼 대화명 (비면 idea 로 표시)
        self.open_chats: list[dict] = []  # 완료 요약을 붙일 채팅 {id, kind, quote, state: queued|taken}
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
        self.browser_verdicts: dict[str, bool] = {}  # Chrome tool_use_id → 권한 판정 (메모리 전용)
        self.last_text: str = ""    # 구현 AI 가 마지막으로 남긴 말 (Chrome 권한 심사 참고용, 메모리 전용)
        self.chrome_account: tuple | None = None   # (email, scope, ok) — 근거가 확인된 보고만
        self.chrome_scope: str | None = None       # 마지막으로 허용된 navigate/tabs_create 의 scope_of()
        self.chrome_seen: set[str] = set()         # 현재 chrome_scope 에서 근거 도구 결과 텍스트에 나온 이메일 (범위 바뀌면 비움)
        # ── 재개용 ──
        self.history: list[dict] = []   # 토론 기록 {round, pid, name, model, text}
        self.checkpoint: dict = {"phase": "debate", "round_from": 1,
                                 "round_until": settings.get("max_rounds", 2)}
        self.notes: list[str] = []      # 진행 중에 받은 사용자 메시지 (다음 단계에 반영)
        self.attachments: list[dict] = []  # 사용자가 올린 파일 {name, path, text_path?}

    @property
    def attach_dir(self) -> str:
        return str(RUNS_DIR / self.id / "attachments")

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

    def window(self, before: Optional[int], limit: int) -> tuple[int, list[dict]]:
        """events[start:before] — 메시지·권한 요청의 시작 이벤트가 잘리지 않게 시작점을 앞으로 당긴다."""
        evs = self.events
        end = len(evs) if before is None else max(0, min(before, len(evs)))
        start = max(0, end - limit)
        floor = max(0, start - limit)  # 채팅 질문(user)은 이 안쪽일 때만 당김
        while start:
            msg_ids = {e["msg_id"] for e in evs[start:end] if e["type"] in ("tool", "message_end")}
            if before is None:
                msg_ids |= set(self.partials)
            req_ids = {e["req_id"] for e in evs[start:end] if e["type"] in ("permission_votes", "permission_decision")}
            reply_ids = {e.get("reply_to") for e in evs[start:end]
                         if e["type"] in ("message_start", "chat_action") and e.get("reply_to")}
            first = next((i for i, e in enumerate(evs[:start])
                          if (e["type"] == "message_start" and e.get("msg_id") in msg_ids)
                          or (e["type"] == "permission_request" and e.get("req_id") in req_ids)
                          or (i >= floor and e["type"] == "user" and e.get("chat_id")
                              and e.get("chat_id") in reply_ids)), start)
            if first == start:
                break
            start = first
        return start, evs[start:end]

    def snapshot_events(self, limit: int) -> tuple[int, list[dict]]:
        """최근 이벤트만 (+ 스트리밍 중인 메시지). 앞부분은 window() 로 나눠 받는다."""
        start, evs = self.window(None, limit)
        evs = list(evs)
        for p in self.partials.values():
            evs.append({"type": "delta", "msg_id": p["msg_id"], "text": p["text"], "ts": p["ts"]})
        return start, evs

    def pending_events(self) -> list[dict]:
        """지금 대기 중인 사람 응답 요청 — 앞부분이 안 불러와져도 채팅창 버튼을 그릴 수 있게."""
        last = {e["key"]: e for e in self.events if e["type"] == "awaiting_user"}
        return [last[k] for k in self.waiters if k in last]

    def stage_label(self) -> str:
        return next((e.get("label", "") for e in reversed(self.events) if e["type"] == "stage"), "")

    # ── 사람 응답 대기 ──
    def wait_for(self, key: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        fut.add_done_callback(lambda f, k=key: self.waiters.get(k) is f and self.waiters.pop(k, None))
        self.waiters[key] = fut
        return fut

    def resolve(self, key: str, value: Any) -> bool:
        fut = self.waiters.pop(key, None)
        if fut and not fut.done():
            fut.set_result(value)
            return True
        return False

    # ── 채팅 완료 요약 추적 (디스크에는 다음 emit/save 가 씀) ──
    def track_chat(self, chat_id, kind: str, text: str, taken: bool) -> None:
        if not chat_id:
            return
        self.open_chats = [c for c in self.open_chats if c["id"] != chat_id]
        self.open_chats.append({"id": chat_id, "kind": kind, "quote": " ".join(str(text or "").split())[:60],
                                "state": "taken" if taken else "queued"})
        self.open_chats = self.open_chats[-MAX_OPEN_CHATS:]

    def mark_chats_taken(self) -> None:
        for c in self.open_chats:
            c["state"] = "taken"

    def pop_taken_chats(self, kinds: tuple[str, ...] | None = None) -> list[dict]:
        out = [c for c in self.open_chats if c["state"] == "taken" and (kinds is None or c["kind"] in kinds)]
        self.open_chats = [c for c in self.open_chats if c not in out]
        return out

    # ── 저장 ──
    def summary(self) -> dict:
        return {"id": self.id, "project": self.project, "idea": self.idea, "title": self.title,
                "status": display_status(self.status, self.waiters), "stage": self.stage, "created": self.created,
                "pending": list(self.waiters.keys()), "alive": self.alive,
                "phase": self.checkpoint.get("phase"), "wait": wait_info(self.pending_events())}

    def save(self) -> None:
        data = {**self.summary(), "status": self.status, "settings": self.settings, "events": self.events,
                "spec": self.spec, "history": self.history, "checkpoint": self.checkpoint, "notes": self.notes,
                "attachments": self.attachments, "open_chats": self.open_chats}
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


def close_orphan_messages(events, now: float) -> list[dict]:
    """시작만 있고 끝이 없는 메시지 → error 마감 이벤트 (재시작 후 '읽는 중…' 이 남지 않게)."""
    evs = [e for e in (events or []) if isinstance(e, dict)]
    ended = {e.get("msg_id") for e in evs if e.get("type") == "message_end"}
    out, seen = [], set()
    for e in evs:
        mid = e.get("msg_id")
        if e.get("type") == "message_start" and mid and mid not in ended and mid not in seen:
            seen.add(mid)
            out.append({"type": "message_end", "ts": now, "msg_id": mid,
                        "text": "⚠️ 서버가 다시 시작돼 응답이 끊겼어요", "error": True})
    return out


RUNS: dict[str, Run] = {}


def delete_run(run_id: str) -> None:
    """기록 파일과 첨부 폴더를 지우고 목록에서 뺀다."""
    RUNS.pop(run_id, None)
    (RUNS_DIR / f"{run_id}.json").unlink(missing_ok=True)
    shutil.rmtree(RUNS_DIR / run_id, ignore_errors=True)


def prune_browser_shots(days: int) -> None:
    """오래된 Chrome 스크린샷 폴더(data/runs/<id>/browser) 삭제. 0 이면 안 지움."""
    if days <= 0:
        return
    cutoff = time.time() - days * 86400
    for d in RUNS_DIR.glob("*/browser"):
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def load_saved_runs() -> None:
    for path in sorted(RUNS_DIR.glob("*.json"))[-50:]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        r = Run(d["project"], d["idea"], d.get("settings", {}), run_id=d["id"])
        r.events, r.stage, r.created, r.spec = d["events"], d["stage"], d["created"], d.get("spec", "")
        r.history, r.notes, r.attachments = d.get("history", []), d.get("notes", []), d.get("attachments", [])
        r.title, r.open_chats = str(d.get("title") or ""), d.get("open_chats") or []
        orphans = browser.orphan_steps(r.events, time.time())  # 끝 이벤트 없이 끊긴 Chrome 단계 → ⏹
        if orphans:
            r.events.extend(orphans)
        r.events.extend(close_orphan_messages(r.events, time.time()))  # 끊긴 메시지 → ⚠️ 마감
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
