"""Pocket-Dev 오케스트레이터 (FastAPI) — 구독 CLI 엔진 전용.

실행: python -m server.main  (또는 start.bat)
  - 사설망  : 0.0.0.0:8000   (집 와이파이 · Tailscale 100.x)
  - 공개 주소: 127.0.0.1:8001 (Tailscale Funnel 이 https 로 연결) — 로그인 실패 3회면 잠김
"""
import asyncio
import copy
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import attachments, auth, browser, engine_setup, executor, github, permissions, pipeline, release, runs, sessions, stitch, usage
from .config import CONFIG, NEW_PROJECT_ROOT, PROJECTS_ROOTS, ROOT, RUNS_DIR, code_sig
from .engines import engine_status
from .runs import RUNS, Run, load_saved_runs
from .sessions import SESSIONS, ChatSession

app = FastAPI(title="Pocket-Dev Orchestrator")
WEB = ROOT / "web"
PORT = CONFIG["server"]["port"]
PUBLIC_PORT = CONFIG["server"].get("public_port", 8001)
# 아직 연결 안 된 엔진 (UI 에 '준비 중' 으로 표시)
UPCOMING_ENGINES = {"codex": "ChatGPT (2차 예정)", "gemini": "Gemini (3차 예정)"}
OPEN_API = {"/api/login", "/api/session"}  # 로그인 없이 부를 수 있는 API
BOOT_ID = uuid.uuid4().hex[:8]  # 재시작 뒤 화면이 새 서버로 바뀌었는지 알아보는 용도
BOOT_SIG = code_sig()  # 이 프로세스가 켜질 때의 코드 — 바뀌면 화면에 🔄 안내
RESTART_EXIT = 3                # start.bat 은 이 종료 코드면 서버를 다시 켠다
SERVER: "uvicorn.Server | None" = None
restart_requested = False
load_saved_runs()
runs.prune_browser_shots(int((CONFIG.get("browser") or {}).get("retention_days", 14)))
sessions.load_saved()


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
    resp = await call_next(request)
    if path == "/" or path.startswith("/static/"):   # 화면 파일은 항상 재검증 (?v= 범프를 잊어도 새로고침으로 반영)
        resp.headers["Cache-Control"] = "no-cache"
    return resp


_SHOT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


@app.get("/api/runs/{run_id}/browser/{name}")
async def browser_shot(run_id: str, name: str):
    """구현 AI 가 찍은 Chrome 스크린샷 (로그인·잠금은 guard 가 처리, 캐시에 남지 않게)."""
    if not re.fullmatch(r"[\w-]+", run_id) or not browser.SHOT_RE.match(name):
        raise HTTPException(404)
    _run(run_id)  # 삭제된 기록 → 404
    path = RUNS_DIR / run_id / "browser" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type=_SHOT_TYPES[name.rsplit(".", 1)[1]],
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


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
            "configured": auth.configured(), "boot": BOOT_ID}
    if logged_in:  # 실패 기록은 로그인한 사람에게만
        state = auth.load_state()
        info.update(failures=state.get("failures", 0), log=state.get("log", [])[-5:])
        cur = code_sig()
        info["stale"] = bool(BOOT_SIG and cur and cur != BOOT_SIG)
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


# ── 서버 재시작 (Pocket-Dev 가 자기 코드를 고친 뒤 반영) ─────────────
class RestartReq(BaseModel):
    force: bool = False   # 진행 중인 작업을 중단하고라도 재시작


async def _check_new_code() -> str:
    """지금 디스크의 코드로 서버가 뜰 수 있는지 별도 프로세스에서 import 해 본다. 문제가 없으면 빈 문자열."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import server.main", cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
    except asyncio.TimeoutError:
        proc.kill()
        return "새 코드를 불러오는 데 60초가 넘게 걸렸어요"
    return "" if proc.returncode == 0 else out.decode("utf-8", "replace")[-3000:]


@app.post("/api/server/restart")
async def restart_server(req: RestartReq):
    """새 코드를 검사하고, 진행 중인 작업을 확인한 뒤 서버를 다시 켠다.
    start.bat 으로 켠 서버는 종료 코드 3 으로 끝나면 start.bat 이 다시 켜고, 아니면 새 창으로 직접 띄운다."""
    global restart_requested
    print(f"[restart] 요청 (force={req.force}, 이미 요청됨={restart_requested}, supervised={os.environ.get('POCKETDEV_SUPERVISED')})",
          flush=True)
    if restart_requested:  # 이전 요청이 아직 안 끝났으면 종료 신호만 다시
        if SERVER:
            SERVER.should_exit = True
        return {"ok": True, "boot": BOOT_ID}
    alive = [r for r in RUNS.values() if r.alive]
    talking = [s for s in SESSIONS.values() if s.busy]
    if (alive or talking) and not req.force:
        return JSONResponse({"detail": f"진행 중인 작업이 {len(alive) + len(talking)}개 있어요",
                             "alive": [r.idea[:60] for r in alive] + ["💬 " + s.title[:60] for s in talking]},
                            status_code=409)
    for s in talking:
        await s.stop()
    error = await _check_new_code()
    if error:
        print(f"[restart] 새 코드 오류로 취소:\n{error}", flush=True)
        raise HTTPException(400, "새 코드에 오류가 있어서 재시작하지 않았어요 (지금 서버는 그대로):\n" + error)
    for r in alive:  # 중단 → 나중에 '이어서 진행' 으로 재개
        await r.emit("log", text="🔄 서버 재시작으로 중단 — 다시 켜진 뒤 '이어서 진행'으로 재개할 수 있어요")
        r.task.cancel()
    if alive:
        await asyncio.wait([r.task for r in alive], timeout=15)
    auth.save_handoff()
    restart_requested = True
    print(f"[restart] 종료 예약 (boot {BOOT_ID})", flush=True)
    asyncio.get_running_loop().call_later(0.5, lambda: setattr(SERVER, "should_exit", True))
    return {"ok": True, "boot": BOOT_ID}


def _relaunch() -> None:
    """start.bat 밖에서 켜진 서버 → 새 콘솔 창으로 다음 서버를 띄운다 (포트가 비면 바로 받음)."""
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen([sys.executable, "-m", "server.main"], cwd=str(ROOT), creationflags=flags)


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
        "release": {"auto_scan_minutes": CONFIG.get("release", {}).get("auto_scan_minutes", 30)},
    }


# ── Claude Code 문제 해결 (화면에서: 다시 확인 · 설치 · 로그인) ─────────
@app.post("/api/engines/claude/recheck")
async def engine_recheck():
    engine_setup.recheck()
    return (await engine_status())["claude"]


@app.post("/api/engines/claude/install")
async def engine_install():
    code, output = await engine_setup.install()
    return {"code": code, "output": output, "status": (await engine_status())["claude"]}


@app.post("/api/engines/claude/login/start")
async def engine_login_start():
    try:
        return {"url": await engine_setup.LOGIN.start()}
    except RuntimeError as e:
        raise HTTPException(502, str(e))


class LoginCodeReq(BaseModel):
    code: str


@app.post("/api/engines/claude/login/code")
async def engine_login_code(req: LoginCodeReq):
    if not req.code.strip():
        raise HTTPException(400, "코드를 붙여 넣으세요")
    try:
        result = await engine_setup.LOGIN.submit(req.code)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {**result, "status": (await engine_status())["claude"]}


@app.post("/api/engines/claude/login/cancel")
async def engine_login_cancel():
    engine_setup.LOGIN.cancel()
    return {"ok": True}


@app.get("/api/usage")
async def get_usage():
    """토큰 사용량 (오늘 / 최근 7일) — Pocket-Dev 와 이 PC 전체 Claude Code."""
    return await asyncio.to_thread(usage.summary)


_projects = github.projects


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
    for key, info in (await github.local_index()).items():
        name = Path(info["path"]).name.lower() if key.startswith("local:") else key.split("/")[-1]
        local.setdefault(name, info["path"])
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
    try:
        asyncio.create_task(release.note_clone(path.name, str(path)))   # 📦 장부에 경로 반영 (실패해도 clone 응답과 무관)
    except Exception:
        pass
    code, branch = await executor.sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], str(path))
    return {"name": f"{path.parent.name}/{path.name}", "path": str(path),
            "branch": branch.strip() if code == 0 else "?", "github": await github.web_url(str(path))}


# ── 📦 출시 관리 (git 사실 스캔 + 내 장부, AI 안 씀) — 없는 키는 400 (404 는 구 서버 감지용) ──
@app.get("/api/release")
async def release_get():
    release._ensure_loaded()
    return release.snapshot()


class ReleaseScanReq(BaseModel):
    if_older_min: int | None = None
    force: bool = False


@app.post("/api/release/scan")
async def release_scan(req: ReleaseScanReq):
    release._ensure_loaded()
    return await release.scan_start(if_older_min=req.if_older_min, force=req.force)


async def _release_write(coro):
    try:
        return await coro
    except KeyError:
        raise HTTPException(400, "그 앱이 장부에 없어요")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except release.LedgerSaveError:
        raise HTTPException(503, "장부를 저장하지 못했어요 — 잠시 뒤 다시")


@app.post("/api/release/update")
async def release_update(payload: dict = Body(...)):
    release._ensure_loaded()
    return await _release_write(release.update(payload))


@app.post("/api/release/hide")
async def release_hide(payload: dict = Body(...)):
    release._ensure_loaded()
    if not isinstance(payload, dict) or "hidden" not in payload:
        raise HTTPException(400, "hidden 이 필요해요")
    return await _release_write(release.hide(payload.get("key"), payload["hidden"]))


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
        "branch": (req.branch or "").strip() or CONFIG["git"]["branch"],
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


def _row(r: Run) -> dict:
    """기록 목록 한 줄 — Chrome 진행은 목록에서만 계산."""
    return {**r.summary(), "chrome": browser.progress(r.events) if browser.enabled(r.settings) else None}


@app.get("/api/runs")
async def list_runs():
    return sorted((_row(r) for r in RUNS.values()), key=runs.list_key)


def _run(run_id: str) -> Run:
    if run_id not in RUNS:
        raise HTTPException(404, "run not found")
    return RUNS[run_id]


class TitleReq(BaseModel):
    title: str = ""


@app.patch("/api/runs/{run_id}")
async def rename_run(run_id: str, req: TitleReq):
    """기록 대화명 변경 — 빈 값이면 처음 아이디어로 표시."""
    run = _run(run_id)
    run.title = runs.norm_title(req.title)
    run.save()
    return _row(run)


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
                "pending": run.pending_events(), "stage_label": run.stage_label(),
                "browser": browser.summarize(run.events),
                "todo": browser.latest_todo(run.events) if browser.enabled(run.settings) else None}
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
    if req.key not in run.waiters:
        raise HTTPException(409, "이미 처리되었거나 대기 중인 요청이 아닙니다")
    chat_id = uuid.uuid4().hex[:10] if req.key == "plan" and req.text.strip() else None
    if chat_id and req.action in ("approve", "feedback"):
        run.track_chat(chat_id, req.action, req.text, True)
    if not run.resolve(req.key, value):
        raise HTTPException(409, "이미 처리되었거나 대기 중인 요청이 아닙니다")
    if chat_id:
        await run.emit("user", text=req.text.strip(), chat_id=chat_id)
    await run.emit("user_reply", key=req.key, action=req.action, text=req.text)
    if req.key == "plan" and req.action in _PLAN_ACTIONS:
        await run.emit("chat_action", **_rt(chat_id), action=req.action, text=_PLAN_ACTIONS[req.action])
    return {"ok": True}


_PLAN_ACTIONS = {"approve": "🚀 승인 — 코멘트를 명세서에 반영하고 진행해요",
                 "feedback": "✏️ 기획안 수정 요청 → 한 라운드 더 토론해요",
                 "cancel": "⏹ 작업을 취소했어요"}


def _rt(chat_id: str | None) -> dict:
    """채팅 응답 쪽 이벤트의 묶음 키."""
    return {"reply_to": chat_id} if chat_id else {}


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
            "executor_model": s["executor_model"], "browser": bool(s.get("browser")), "status": run.summary()["status"]}


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
    restart = bool(changes) and req.apply_now and run.summary()["status"] == "working"
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
    chat_id = None
    if req.text.strip() or files:
        chat_id = uuid.uuid4().hex[:10]
        await run.emit("user", text=req.text.strip(), files=[f["name"] for f in files], chat_id=chat_id)
    if _is_resume(text):
        limit_keys = [k for k in list(run.waiters) if k.startswith("limit:")]
        for key in limit_keys:
            run.resolve(key, {"action": "retry"})
            await run.emit("user_reply", key=key, action="retry", text="")
        if limit_keys:
            await run.emit("chat_action", **_rt(chat_id), action="resume", text="🔁 지금 재시도할게요")
            return {"mode": "retry"}
        if run.alive:
            mode, msg = runs.busy_reply(run.summary()["status"])
            await run.emit("chat_action", **_rt(chat_id), action="resume", text=msg)
            return {"mode": mode}
        if run.status == "done":
            await run.emit("chat_action", **_rt(chat_id), action="resume",
                           text="ℹ️ 이미 끝난 작업이에요 — 추가로 바꿀 내용을 적어 주세요")
            raise HTTPException(400, "이미 끝난 작업이에요. 추가로 바꿀 내용을 적어 주세요")
        run.track_chat(chat_id, "resume", req.text, True)
        await run.emit("chat_action", **_rt(chat_id), action="resume", text="▶ 멈춘 단계부터 이어서 진행해요")
        pipeline.start(run, PORT)
        return {"mode": "resumed"}
    asyncio.create_task(pipeline.handle_chat(run, text, PORT, reply_to=chat_id))
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
    tool_use_id: str | None = None


@app.post("/api/internal/permission")
async def internal_permission(req: PermissionReq):
    if req.run_id.startswith("session:"):  # 💬 직접 세션 — 사용자에게 묻는다
        return await _session(req.run_id.removeprefix("session:")).decide(req.tool_name, req.input)
    return await permissions.decide(_run(req.run_id), req.tool_name, req.input, req.tool_use_id)


class ChromeAccountReq(BaseModel):
    run_id: str
    email: str = ""


@app.post("/api/internal/chrome_account")
async def internal_chrome_account(req: ChromeAccountReq):
    if req.run_id.startswith("session:"):  # 💬 직접 세션 — 클릭·입력을 사용자가 하나하나 허용하므로 계정 검사 없음
        s = _session(req.run_id.removeprefix("session:"))
        await s.emit("log", text=f"👤 Chrome 로그인 계정: {req.email[:300] or '확인 안 됨'}")
        return {"text": "기록했어요. 이 대화에서는 클릭·입력을 사용자가 직접 허용합니다."}
    return {"text": await permissions.report_account(_run(req.run_id), req.email[:300])}


# ── 💬 Claude 직접 세션 ─────────────────────────────────────────
def _session(session_id: str) -> ChatSession:
    if session_id not in SESSIONS:
        raise HTTPException(404, "session not found")
    return SESSIONS[session_id]


class SessionStartReq(BaseModel):
    project: str
    model: str = "claude-sonnet-5-5"
    effort: str = ""
    browser: bool = False
    text: str = ""


@app.get("/api/sessions")
async def list_sessions():
    return sorted((s.summary() for s in SESSIONS.values()), key=lambda s: -s["updated"])


@app.post("/api/sessions")
async def start_session(req: SessionStartReq):
    if not MODEL_ID.fullmatch(req.model.strip()) or req.effort not in EFFORTS:
        raise HTTPException(400, "모델이나 effort 값이 올바르지 않아요")
    s = ChatSession(_resolve_project(req.project), req.model.strip(), req.effort, req.browser)
    SESSIONS[s.id] = s
    s.save()
    if req.text.strip():
        await s.send(req.text.strip(), PORT)
    return s.summary()


@app.get("/api/sessions/{session_id}/events")
async def session_events(session_id: str):
    s = _session(session_id)
    queue: asyncio.Queue = asyncio.Queue()
    snapshot = {"type": "snapshot", "session": s.summary(), "events": s.snapshot()}
    s.subscribers.add(queue)

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
            s.subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class SessionMessageReq(BaseModel):
    text: str


@app.post("/api/sessions/{session_id}/message")
async def session_message(session_id: str, req: SessionMessageReq):
    if not req.text.strip():
        raise HTTPException(400, "메시지를 입력하세요")
    try:
        await _session(session_id).send(req.text.strip(), PORT)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


class SessionPermissionReq(BaseModel):
    req_id: str
    action: str   # allow | always | deny


@app.post("/api/sessions/{session_id}/permission")
async def session_permission(session_id: str, req: SessionPermissionReq):
    if req.action not in ("allow", "always", "deny"):
        raise HTTPException(400, "action 은 allow · always · deny 중 하나")
    if not await _session(session_id).answer(req.req_id, req.action):
        raise HTTPException(409, "이미 처리됐거나 기다리는 요청이 아니에요")
    return {"ok": True}


class SessionSettingsReq(BaseModel):
    browser: bool | None = None
    model: str | None = None
    effort: str | None = None


@app.post("/api/sessions/{session_id}/settings")
async def session_settings(session_id: str, req: SessionSettingsReq):
    """다음 메시지부터 적용 (지금 답하는 중인 것은 그대로)."""
    s = _session(session_id)
    changes = []
    if req.browser is not None and req.browser != s.browser:
        s.browser = req.browser
        changes.append("🌐 Chrome " + ("켬" if req.browser else "끔"))
    if req.model is not None and req.model.strip() != s.model:
        if not MODEL_ID.fullmatch(req.model.strip()):
            raise HTTPException(400, "모델 이름이 올바르지 않아요")
        s.model = req.model.strip()
        changes.append(f"모델 {s.model}")
    if req.effort is not None and req.effort != s.effort:
        if req.effort not in EFFORTS:
            raise HTTPException(400, "effort 값이 올바르지 않아요")
        s.effort = req.effort
        changes.append(f"effort {req.effort or '기본'}")
    if changes:
        await s.emit("log", text="⚙️ " + " · ".join(changes) + " (다음 메시지부터)")
    return s.summary()


@app.post("/api/sessions/{session_id}/stop")
async def session_stop(session_id: str):
    await _session(session_id).stop()
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def session_delete(session_id: str):
    if _session(session_id).busy:
        raise HTTPException(409, "답하는 중이에요. ⏹ 중지한 뒤 삭제하세요")
    sessions.delete(session_id)
    return {"ok": True}


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
    """재시작 직후엔 이전 서버가 포트를 놓을 때까지 잠깐 기다린다.
    listen 까지 여기서 해 둔다 (uvicorn 이 다시 listen 해도 괜찮음) — 첫 listen 이 실패하면 새 소켓으로 다시."""
    for attempt in range(100):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
            sock.listen(2048)
            sock.set_inheritable(True)
            return sock
        except OSError as e:
            sock.close()
            if attempt == 99:
                raise
            time.sleep(0.02 if _flaky_listen(e) else 1)


# 이 PC 에선 새 소켓의 첫 listen() 이 4번에 1번꼴로 WinError 10014 로 실패한다 (같은 소켓 재시도도 자주 실패,
# 이미 listen 중인 소켓에 다시 listen 하는 건 괜찮음). asyncio 이벤트 루프(socketpair)와 서버 소켓이 모두 listen 을 써서
# 실패하면 서버가 켜지자마자 죽었다 — 화면의 재시작이 안 되던 원인. 새 소켓으로 다시 시도한다.
def _flaky_listen(e: OSError) -> bool:
    return getattr(e, "winerror", None) == 10014


def _retry_socketpair() -> None:
    original = socket.socketpair

    def socketpair(*args, **kwargs):
        for attempt in range(50):
            try:
                return original(*args, **kwargs)
            except OSError as e:
                if not _flaky_listen(e) or attempt == 49:
                    raise
                time.sleep(0.02)

    socket.socketpair = socketpair


if __name__ == "__main__":
    import uvicorn
    _retry_socketpair()
    auth.load_handoff()
    _print_urls()
    sockets = [_listen(CONFIG["server"]["host"], PORT), _listen("127.0.0.1", PUBLIC_PORT)]
    # SSE 연결이 남아 있어도 재시작이 늦어지지 않게 3초만 기다리고 끊는다
    SERVER = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=3))
    SERVER.run(sockets=sockets)
    if restart_requested:
        if os.environ.get("POCKETDEV_SUPERVISED") == "1":
            sys.exit(RESTART_EXIT)
        _relaunch()
