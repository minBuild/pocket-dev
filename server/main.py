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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import attachments, auth, executor, github, permissions, pipeline, runs, stitch, usage
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
        "stitch": stitch.configured(),
    }


@app.get("/api/usage")
async def get_usage():
    """토큰 사용량 (오늘 / 최근 7일) — Pocket-Dev 와 이 PC 전체 Claude Code."""
    return await asyncio.to_thread(usage.summary)


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
                    "branch": branch.strip() if code == 0 else "?", "github": await github.web_url(str(p))})
    return {"roots": [str(r) for r in PROJECTS_ROOTS], "projects": out}


@app.get("/api/github/repos")
async def github_repos(refresh: bool = False):
    """GitHub 계정의 저장소 (비공개 포함) + PC에 이미 받아 둔 경로."""
    try:
        repos = await github.list_repos(force=refresh)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    local: dict[str, str] = {}
    for p in _projects():
        url = await github.web_url(str(p))
        local[(url.rsplit("/", 1)[-1] if url else p.name).lower()] = str(p)
        local.setdefault(p.name.lower(), str(p))
    return {"owner": CONFIG["git"].get("github_owner", ""),
            "repos": [{**r, "local_path": local.get(r["name"].lower())} for r in repos]}


class CloneReq(BaseModel):
    name: str


@app.post("/api/github/clone")
async def github_clone(req: CloneReq):
    """GitHub 저장소를 PC로 받아와서 프로젝트 목록에 추가."""
    try:
        path = await github.clone(req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    code, branch = await executor.sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], str(path))
    return {"name": f"{path.parent.name}/{path.name}", "path": str(path),
            "branch": branch.strip() if code == 0 else "?", "github": await github.web_url(str(path))}


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
@app.post("/api/uploads")
async def upload(files: list[UploadFile] = File(...)):
    """첨부 파일 올리기 (작업 시작·채팅 때 id 로 붙임)."""
    if len(files) > 10:
        raise HTTPException(400, "한 번에 10개까지 올릴 수 있어요")
    out = []
    for f in files:
        try:
            out.append(await attachments.save_upload(f))
        except ValueError as e:
            raise HTTPException(400, str(e))
    return {"files": out}


class StartReq(BaseModel):
    project: str
    idea: str
    attachments: list[str] = []
    participants: list[dict] | None = None
    synthesizer: dict | None = None
    reviewers: list[dict] | None = None
    permission_reviewers: list[dict] | None = None
    executor_model: str | None = None
    max_rounds: int | None = None
    plan_gate: bool = True
    branch: str | None = None
    stitch: bool = False
    browser: bool = False


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
        "stitch": req.stitch and stitch.configured(),
        "browser": req.browser,
    }
    active = [p for p in settings["participants"] if p.get("enabled", True)]
    _check_engines(active + [settings["synthesizer"]] + settings["reviewers"] + settings["permission_reviewers"])
    for p in settings["participants"]:
        p.setdefault("name", p["model"])
        p.setdefault("persona", "당신은 소프트웨어 설계 토론 참가자입니다.")
    run = Run(_resolve_project(req.project), req.idea, settings)
    RUNS[run.id] = run
    run.attachments = attachments.attach(Path(run.attach_dir).parent, req.attachments)
    await run.emit("user", text=req.idea, files=[a["name"] for a in run.attachments])
    run.task = asyncio.create_task(pipeline.run_pipeline(run, PORT))
    return run.summary()


@app.get("/api/runs")
async def list_runs():
    return sorted((r.summary() for r in RUNS.values()), key=lambda r: -r["created"])


def _run(run_id: str) -> Run:
    if run_id not in RUNS:
        raise HTTPException(404, "run not found")
    return RUNS[run_id]


PAGE_EVENTS = 200  # 대화창에 한 번에 보내는 이벤트 수 (나머지는 위로 스크롤할 때)


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    """기록 삭제 (첨부 파일 포함). 진행 중이거나 응답을 기다리는 작업은 먼저 중단해야 한다."""
    if _run(run_id).alive:
        raise HTTPException(409, "진행 중인 작업이에요. 먼저 중단한 뒤 삭제하세요")
    runs.delete_run(run_id)
    return {"ok": True}


@app.get("/api/runs/{run_id}/older")
async def older_events(run_id: str, before: int, limit: int = PAGE_EVENTS):
    """대화창을 위로 올렸을 때 그 앞의 이벤트."""
    first, events = _run(run_id).window(before, max(1, min(limit, 1000)))
    return {"first": first, "events": events}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    """SSE: 최근 이벤트를 먼저 보내고 이후 실시간 이벤트를 이어서 보낸다 (앞부분은 /older)."""
    run = _run(run_id)
    queue: asyncio.Queue = asyncio.Queue()
    first, events = run.snapshot_events(PAGE_EVENTS)
    snapshot = {"type": "snapshot", "run": run.summary(), "events": events, "first": first,
                "pending": run.pending_events(), "stage_label": run.stage_label()}
    run.subscribers.add(queue)

    async def gen():
        try:
            yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
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


# ── 진행 중인 작업의 역할별 모델 변경 ─────────────────────────────
MODEL_ID = re.compile(r"[A-Za-z0-9._:@/\[\]-]{1,100}")
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
_MEMBER_KEYS = ("id", "name", "engine", "model", "effort", "enabled")


def _models_view(run: Run) -> dict:
    s = run.settings
    pick = lambda m: {k: m.get(k, "") for k in _MEMBER_KEYS}  # noqa: E731
    return {"participants": [pick(p) for p in s["participants"]], "synthesizer": pick(s["synthesizer"]),
            "reviewers": [pick(r) for r in s["reviewers"]],
            "permission_reviewers": [pick(r) for r in s["permission_reviewers"]],
            "executor_model": s["executor_model"], "browser": bool(s.get("browser")), "status": run.status}


@app.get("/api/runs/{run_id}/models")
async def get_models(run_id: str):
    return _models_view(_run(run_id))


class ModelsReq(BaseModel):
    participants: list[dict] | None = None
    synthesizer: dict | None = None
    reviewers: list[dict] | None = None
    permission_reviewers: list[dict] | None = None
    executor_model: str | None = None
    browser: bool | None = None   # 구현 AI 가 Chrome 을 쓸 수 있는지 (다음 구현 실행부터)
    apply_now: bool = False       # 지금 진행 중인 단계도 새 모델로 다시 시작


@app.post("/api/runs/{run_id}/models")
async def set_models(run_id: str, req: ModelsReq):
    """역할별 모델·effort 변경 — 다음 AI 호출부터 적용 (참가자 구성은 그대로). 전부 검사한 뒤에 한꺼번에 바꾼다."""
    run = _run(run_id)
    s = run.settings
    plan: list[tuple[str, dict, dict]] = []

    def check(label: str, cur: dict, new: dict) -> None:
        engine = new.get("engine") or cur.get("engine") or "claude"
        model = str(new.get("model") or "").strip()
        effort = str(new.get("effort") or "")
        if engine not in CONFIG["engines"]:
            raise HTTPException(400, f"'{engine}' 엔진은 아직 준비 중입니다 (현재: Claude)")
        if not MODEL_ID.fullmatch(model):
            raise HTTPException(400, f"{label}: 모델 이름이 올바르지 않아요 ({model!r})")
        if effort not in EFFORTS:
            raise HTTPException(400, f"{label}: effort 값이 올바르지 않아요 ({effort!r})")
        plan.append((label, cur, {"engine": engine, "model": model, "effort": effort}))

    for key, label in (("participants", ""), ("reviewers", "리뷰어"), ("permission_reviewers", "권한 심사")):
        new_list = getattr(req, key)
        if new_list is None:
            continue
        if len(new_list) != len(s[key]):
            raise HTTPException(400, "인원 구성은 바꿀 수 없어요 — 모델만 바꿀 수 있어요")
        for i, (cur, new) in enumerate(zip(s[key], new_list)):
            check(cur.get("name") or cur.get("model") if key == "participants" else f"{label} {i + 1}", cur, new)
    if req.synthesizer is not None:
        check("명세서 작성자", s["synthesizer"], req.synthesizer)
    if req.executor_model is not None and not MODEL_ID.fullmatch(req.executor_model.strip()):
        raise HTTPException(400, f"구현 모델 이름이 올바르지 않아요 ({req.executor_model!r})")

    fmt = lambda m: m["model"] + (f" ⚡{m['effort']}" if m.get("effort") else "")  # noqa: E731
    changes = []
    for label, cur, new in plan:  # 같은 dict 를 고쳐서, 이미 돌고 있는 파이프라인도 다음 호출부터 새 모델을 쓴다
        if (cur.get("engine"), cur.get("model"), cur.get("effort") or "") != (new["engine"], new["model"], new["effort"]):
            changes.append(f"{label}: {fmt(cur)} → {fmt(new)}")
            cur.update(new)
    if req.executor_model is not None and req.executor_model.strip() != s["executor_model"]:
        changes.append(f"구현(Claude Code): {s['executor_model']} → {req.executor_model.strip()}")
        s["executor_model"] = req.executor_model.strip()
    if req.browser is not None and req.browser != bool(s.get("browser")):
        changes.append("🌐 Chrome 사용 " + ("켬" if req.browser else "끔"))
        s["browser"] = req.browser
    restart = bool(changes) and req.apply_now and run.status == "running"
    if changes:
        await run.emit("log", text="⚙️ 모델 변경 — " + " · ".join(changes)
                       + (" (지금 단계부터 새 모델로 다시 시작)" if restart else " (다음 AI 호출부터 적용)"))
        run.save()
    if restart:
        await pipeline.restart_step(run, PORT)
    return {**_models_view(run), "changed": changes, "restarted": restart}


class MessageReq(BaseModel):
    text: str = ""
    attachments: list[str] = []


def _is_resume(text: str) -> bool:
    """'이어서 진행해줘' 처럼 재개만 요청하는 말인지 (지시 내용이 없는지)."""
    t = re.sub(r"[\s.!~,]+", "", text)
    return t == "" or re.fullmatch(r"(이어서|계속|재개|다시)(진행|시작)?(해|해줘|해주세요|하자|가자|ㄱㄱ)?"
                                   r"|진행(해|해줘|해주세요|하자)?|go|continue|resume", t, re.I) is not None


@app.post("/api/runs/{run_id}/message")
async def run_message(run_id: str, req: MessageReq):
    """작업 화면 채팅. 빈 메시지·'이어서 진행해줘' 는 바로 재개하고,
    나머지는 진행 비서가 읽고 판단한다 (질문 → 답만 / 작업 요청 → 수정·반영·추가 작업)."""
    run = _run(run_id)
    text = req.text.strip()
    files = attachments.attach(Path(run.attach_dir).parent, req.attachments) if req.attachments else []
    if files:
        run.attachments += files
        run.save()
        names = ", ".join(f["name"] for f in files)
        text = f"{text}\n\n[첨부 파일 추가: {names}]" if text else f"첨부 파일을 올렸어요: {names}"
    if req.text.strip() or files:
        await run.emit("user", text=req.text.strip(), files=[f["name"] for f in files])
    if _is_resume(text):
        limit_keys = [k for k in list(run.waiters) if k.startswith("limit:")]
        for key in limit_keys:
            run.resolve(key, {"action": "retry"})
            await run.emit("user_reply", key=key, action="retry", text="")
        if limit_keys:
            return {"mode": "retry"}
        if run.alive:
            return {"mode": "running"}
        if run.status == "done":
            raise HTTPException(400, "이미 끝난 작업이에요. 추가로 바꿀 내용을 적어 주세요")
        pipeline.start(run, PORT)
        return {"mode": "resumed"}
    asyncio.create_task(pipeline.handle_chat(run, text, PORT))
    return {"mode": "assistant"}


class TerminalReq(BaseModel):
    command: str


@app.post("/api/runs/{run_id}/terminal")
async def run_terminal(run_id: str, req: TerminalReq):
    """작업 화면 터미널 칸 — 이 작업의 프로젝트 폴더에서 명령 실행 (git·gh 등). 결과는 대화 기록에도 남긴다."""
    run = _run(run_id)
    command = req.command.strip()
    if not command:
        raise HTTPException(400, "명령을 입력하세요")
    if len(command) > 4000:
        raise HTTPException(400, "명령이 너무 길어요")
    code, output = await executor.terminal(command, _resolve_project(run.project))
    await run.emit("terminal", command=command, code=code, output=output)
    return {"code": code, "output": output}


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
