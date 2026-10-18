"""Pocket-Dev 오케스트레이터 (FastAPI) — 구독 CLI 엔진 전용.

실행: python -m server.main  (또는 start.bat)
  - 사설망  : 0.0.0.0:8000   (집 와이파이 · Tailscale 100.x)
  - 공개 주소: 127.0.0.1:8001 (Tailscale Funnel 이 https 로 연결) — 로그인 실패 3회면 잠김
"""
import asyncio
import copy
import json
import re
import socket
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, executor, permissions, pipeline
from .config import CONFIG, NEW_PROJECT_ROOT, PROJECTS_ROOTS, ROOT
from .engines import engine_status
from .runs import RUNS, Run, load_saved_runs

app = FastAPI(title="Pocket-Dev Orchestrator")
WEB = ROOT / "web"
PORT = CONFIG["server"]["port"]
PUBLIC_PORT = CONFIG["server"].get("public_port", 8001)
# 아직 연결 안 된 엔진 (UI 에 '준비 중' 으로 표시)
UPCOMING_ENGINES = {"codex": "ChatGPT (2차 예정)", "gemini": "Gemini (3차 예정)"}
OPEN_API = {"/api/login", "/api/session"}  # 로그인 없이 부를 수 있는 API
load_saved_runs()


def _via(request: Request) -> str:
    """요청이 들어온 출입구: 공개 주소(Funnel) 전용 포트면 public, 아니면 private."""
    server = request.scope.get("server") or ("", PORT)
    return "public" if server[1] == PUBLIC_PORT else "private"


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


@app.middleware("http")
async def guard(request: Request, call_next):
    path, via = request.url.path, _via(request)
    if path.startswith("/api/internal/"):
        # Claude Code 권한 심사 MCP 전용 — 이 PC 안에서, 사설 포트로, 액세스 토큰이 있어야 함
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        local = request.client and request.client.host in ("127.0.0.1", "::1")
        if via == "public" or not local or not auth.check_token(bearer):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        return await call_next(request)
    if via == "public" and auth.is_locked() and path.startswith("/api/") and path != "/api/session":
        return JSONResponse({"detail": "locked"}, status_code=423)
    if path.startswith("/api/") and path not in OPEN_API:
        if not auth.valid_session(request.cookies.get(auth.SESSION_COOKIE)):
            return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


# ── 로그인 ────────────────────────────────────────────────────
class LoginReq(BaseModel):
    login_id: str
    password: str
    token: str


@app.get("/api/session")
async def session(request: Request):
    logged_in = auth.valid_session(request.cookies.get(auth.SESSION_COOKIE))
    info = {"logged_in": logged_in, "via": _via(request), "locked": auth.is_locked(),
            "configured": auth.configured()}
    if logged_in:  # 실패 기록은 로그인한 사람에게만
        state = auth.load_state()
        info.update(failures=state.get("failures", 0), log=state.get("log", [])[-5:])
    return info


@app.post("/api/login")
async def login(req: LoginReq, request: Request):
    via = _via(request)
    if not auth.configured():
        return JSONResponse({"detail": "집 PC에서 setup_login.bat 을 먼저 실행해 로그인 정보를 정하세요"}, status_code=503)
    result = auth.attempt_login(req.login_id, req.password, req.token, _client(request), via)
    if not result["ok"]:
        await asyncio.sleep(1.5)  # 연속 시도 지연
        if result["locked"] and via == "public":
            return JSONResponse({"detail": "locked", "locked": True}, status_code=423)
        return JSONResponse({"detail": "아이디, 비밀번호, 액세스 토큰 중 틀린 게 있어요",
                             "remaining": result["remaining"], "locked": result["locked"]}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.SESSION_COOKIE, result["session"], max_age=auth.session_max_age(), path="/",
                    httponly=True, samesite="strict", secure=(via == "public"))
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.post("/api/unlock")
async def unlock(request: Request):
    """공개 주소 잠금 해제 — 사설망(Tailscale·집 와이파이)으로 로그인했을 때만."""
    if _via(request) != "private":
        raise HTTPException(403, "잠금 해제는 Tailscale 이나 집 와이파이로 접속했을 때만 가능해요")
    auth.unlock()
    return {"ok": True}


# ── 설정 / 엔진 / 프로젝트 ─────────────────────────────────────
@app.get("/api/config")
async def get_config():
    status = await engine_status()
    engines = {eid: {**e, "status": status.get(eid, {})} for eid, e in CONFIG["engines"].items()}
    return {
        "team": CONFIG["team"],
        "engines": engines,
        "upcoming": UPCOMING_ENGINES,
        "debate": {"max_rounds": CONFIG["debate"]["max_rounds"]},
        "executor_model": CONFIG["executor"]["claude_model"],
        "branch": CONFIG["git"]["branch"],
    }


def _projects() -> list[Path]:
    found: list[Path] = []
    for root in PROJECTS_ROOTS:
        if root.exists():
            found += [p for p in sorted(root.iterdir()) if (p / ".git").exists()]
    return found


@app.get("/api/projects")
async def get_projects():
    out = []
    for p in _projects():
        code, branch = await executor.sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], str(p))
        out.append({"name": f"{p.parent.name}/{p.name}", "path": str(p),
                    "branch": branch.strip() if code == 0 else "?"})
    return {"roots": [str(r) for r in PROJECTS_ROOTS], "projects": out}


def _resolve_project(key: str) -> str:
    """UI 가 보낸 프로젝트 경로가 설정된 폴더 안의 git 저장소일 때만 허용."""
    for p in _projects():
        if key in (str(p), p.name):
            return str(p)
    raise HTTPException(400, f"설정된 프로젝트 폴더에 '{key}' 가 없습니다")


class NewProjectReq(BaseModel):
    name: str


@app.post("/api/projects/new")
async def new_project(req: NewProjectReq):
    """새 프로젝트 폴더 + git 저장소. GitHub 저장소는 첫 push 때 자동으로 만들어진다."""
    name = req.name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise HTTPException(400, "프로젝트 이름은 영문·숫자·-·_·. 만 쓸 수 있어요 (예: my-app)")
    path = NEW_PROJECT_ROOT / name
    if path.exists():
        raise HTTPException(409, f"{path} 가 이미 있어요")
    await executor.init_project(path)
    return {"name": f"{path.parent.name}/{path.name}", "path": str(path), "branch": "main"}


class RollbackReq(BaseModel):
    project: str


@app.post("/api/projects/rollback")
async def rollback(req: RollbackReq):
    """마지막 커밋을 revert 커밋으로 되돌리고 push (히스토리를 지우지 않음)."""
    cwd = _resolve_project(req.project)
    code, out = await executor.sh(["git", *executor.author_args(), "revert", "--no-edit", "HEAD"], cwd)
    if code:
        raise HTTPException(400, out)
    branch = (await executor.git(cwd, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    _, push = await executor.sh(["git", "push", "origin", branch], cwd)
    return {"ok": True, "output": out + push}


# ── 실행(Run) ─────────────────────────────────────────────────
class StartReq(BaseModel):
    project: str
    idea: str
    participants: list[dict] | None = None
    synthesizer: dict | None = None
    reviewers: list[dict] | None = None
    permission_reviewers: list[dict] | None = None
    executor_model: str | None = None
    max_rounds: int | None = None
    plan_gate: bool = True
    branch: str | None = None


def _check_engines(members: list[dict]) -> None:
    for m in members:
        m.setdefault("engine", "claude")
        if m["engine"] not in CONFIG["engines"]:
            raise HTTPException(400, f"'{m['engine']}' 엔진은 아직 준비 중입니다 (현재: Claude)")


@app.post("/api/runs")
async def start_run(req: StartReq):
    team = copy.deepcopy(CONFIG["team"])
    pick = lambda value, default: value if value is not None else default  # noqa: E731
    settings = {
        "participants": pick(req.participants, team["participants"]),
        "synthesizer": req.synthesizer or team["synthesizer"],
        "reviewers": pick(req.reviewers, team["reviewers"]),
        "permission_reviewers": pick(req.permission_reviewers, team["permission_reviewers"]),
        "executor_model": req.executor_model or CONFIG["executor"]["claude_model"],
        "max_rounds": max(1, min(req.max_rounds or CONFIG["debate"]["max_rounds"], 10)),
        "plan_gate": req.plan_gate,
        "branch": req.branch or CONFIG["git"]["branch"],
    }
    active = [p for p in settings["participants"] if p.get("enabled", True)]
    _check_engines(active + [settings["synthesizer"]] + settings["reviewers"] + settings["permission_reviewers"])
    for p in settings["participants"]:
        p.setdefault("name", p["model"])
        p.setdefault("persona", "당신은 소프트웨어 설계 토론 참가자입니다.")
    run = Run(_resolve_project(req.project), req.idea, settings)
    RUNS[run.id] = run
    await run.emit("user", text=req.idea)
    run.task = asyncio.create_task(pipeline.run_pipeline(run, PORT))
    return run.summary()


@app.get("/api/runs")
async def list_runs():
    return sorted((r.summary() for r in RUNS.values()), key=lambda r: -r["created"])


def _run(run_id: str) -> Run:
    if run_id not in RUNS:
        raise HTTPException(404, "run not found")
    return RUNS[run_id]


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    """SSE: 지금까지의 이벤트를 먼저 보내고 이후 실시간 이벤트를 이어서 보낸다."""
    run = _run(run_id)
    queue: asyncio.Queue = asyncio.Queue()
    snapshot = run.snapshot_events()
    run.subscribers.add(queue)

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'snapshot', 'run': run.summary(), 'events': snapshot}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), 20)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            run.subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ReplyReq(BaseModel):
    key: str
    action: str = "approve"     # plan: approve|feedback|cancel · permission: allow|deny · limit: retry
    text: str = ""


@app.post("/api/runs/{run_id}/reply")
async def reply(run_id: str, req: ReplyReq):
    run = _run(run_id)
    value = {"action": req.action, "text": req.text, "allow": req.action in ("allow", "approve")}
    if not run.resolve(req.key, value):
        raise HTTPException(409, "이미 처리되었거나 대기 중인 요청이 아닙니다")
    if req.key == "plan" and req.text.strip():
        await run.emit("user", text=req.text.strip())
    await run.emit("user_reply", key=req.key, action=req.action, text=req.text)
    return {"ok": True}


class MessageReq(BaseModel):
    text: str = ""


def _is_resume(text: str) -> bool:
    """'이어서 진행해줘' 처럼 재개만 요청하는 말인지 (지시 내용이 없는지)."""
    t = re.sub(r"[\s.!~,]+", "", text)
    return t == "" or re.fullmatch(r"(이어서|계속|재개|다시)(진행|시작)?(해|해줘|해주세요|하자|가자|ㄱㄱ)?"
                                   r"|진행(해|해줘|해주세요|하자)?|go|continue|resume", t, re.I) is not None


@app.post("/api/runs/{run_id}/message")
async def run_message(run_id: str, req: MessageReq):
    """작업 화면 채팅 — 기획안 수정 요청 · 진행 중 메모 · 멈춘 작업 재개 · 완료 후 추가 요청."""
    run = _run(run_id)
    text, resume_only = req.text.strip(), _is_resume(req.text)
    if "plan" in run.waiters:  # 기획안 승인 대기 중 → 수정 요청
        if resume_only:
            raise HTTPException(400, "승인은 '승인하고 진행' 버튼으로, 수정할 점은 내용을 적어서 보내 주세요")
        await run.emit("user", text=text)
        run.resolve("plan", {"action": "feedback", "text": text})
        await run.emit("user_reply", key="plan", action="feedback", text=text)
        return {"mode": "feedback"}
    if text:
        await run.emit("user", text=text)
    limit_keys = [k for k in list(run.waiters) if k.startswith("limit:")]
    if limit_keys:  # 한도 대기 중 → 바로 재시도
        if not resume_only:
            run.notes.append(text)
        for key in limit_keys:
            run.resolve(key, {"action": "retry"})
            await run.emit("user_reply", key=key, action="retry", text="")
        return {"mode": "retry"}
    if run.alive:  # 진행 중 → 다음 단계에 반영
        if not resume_only:
            run.notes.append(text)
            run.save()
        return {"mode": "queued" if not resume_only else "running"}
    if run.status == "done":
        if resume_only:
            raise HTTPException(400, "이미 끝난 작업이에요. 추가로 바꿀 내용을 적어 주세요")
        pipeline.start_followup(run, text)
        mode = "followup"
    else:  # 멈춘 작업 → 멈춘 단계부터 재개
        if not resume_only:
            run.notes.append(text)
        mode = "resumed"
    run.task = asyncio.create_task(pipeline.run_pipeline(run, PORT))
    return {"mode": mode}


@app.post("/api/runs/{run_id}/cancel")
async def cancel(run_id: str):
    run = _run(run_id)
    if run.task and not run.task.done():
        run.task.cancel()
    return {"ok": True}


# ── Claude Code 권한 심사 (permission_mcp.py 가 호출) ───────────
class PermissionReq(BaseModel):
    run_id: str
    tool_name: str
    input: dict


@app.post("/api/internal/permission")
async def internal_permission(req: PermissionReq):
    return await permissions.decide(_run(req.run_id), req.tool_name, req.input)


# ── 실행 ─────────────────────────────────────────────────────
def _public_url() -> str:
    """Tailscale Funnel 공개 주소 (https://<기기>.<tailnet>.ts.net)."""
    try:
        out = subprocess.run([r"C:\Program Files\Tailscale\tailscale.exe", "status", "--json"],
                             capture_output=True, timeout=10).stdout
        name = json.loads(out)["Self"]["DNSName"].rstrip(".")
        return f"https://{name}" if name else ""
    except Exception:
        return ""


def _print_urls() -> None:
    ips = sorted({ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")})
    print("\n  Pocket-Dev 서버 시작")
    public = _public_url()
    if public:
        print(f"   {public}   <- 공개 주소 (Funnel 이 켜져 있을 때, 어디서나)")
    for ip in ips:
        note = "Tailscale (잠금 해제 가능)" if ip.startswith("100.") else "같은 와이파이"
        print(f"   http://{ip}:{PORT}   <- {note}")
    if not auth.configured():
        print("   [안내] 로그인 정보가 없습니다. setup_login.bat 을 먼저 실행하세요.")
    if auth.is_locked():
        print("   [잠김] 로그인 실패로 공개 주소가 잠겨 있습니다. unlock.bat 으로 해제하세요.")
    print()


def _listen(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock


if __name__ == "__main__":
    import uvicorn
    _print_urls()
    sockets = [_listen(CONFIG["server"]["host"], PORT), _listen("127.0.0.1", PUBLIC_PORT)]
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=sockets)
