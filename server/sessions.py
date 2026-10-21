"""💬 Claude 직접 세션 — 폰에서 Claude Code 와 바로 대화 (토론·명세서·리뷰 없이).

메시지마다 `claude -p --resume <세션 id>` 로 이어서 실행한다. 프로젝트 폴더에서 돌고, 켜면 Chrome(Claude in Chrome)도 쓴다.
권한 요청은 리뷰어 AI 가 아니라 사용자에게 묻는다: 허용 / 이 세션 동안 이 도구 계속 허용 / 거부.
읽기 전용 도구만 바로 허용하고, 하드 룰(루트 삭제·강제 push 등)은 그대로 거부한다.
"""
import asyncio
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import browser, permissions
from . import usage as usage_log
from .config import ACCESS_TOKEN, DATA_DIR
from .engines import UsageLimitError, check_limit, claude_bin, subscription_env, tool_summary

SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
MCP_SCRIPT = Path(__file__).with_name("permission_mcp.py")
AUTO_ALLOW = ["Read", "Grep", "Glob", "TodoWrite", "TodoRead"]
PERMISSION_TIMEOUT = 1800   # 사용자 응답을 기다리는 최대 시간 (초) — 지나면 거부
TURN_TIMEOUT = 7200


class ChatSession:
    def __init__(self, project: str, model: str, effort: str = "", use_browser: bool = False,
                 session_id: Optional[str] = None):
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.project = project
        self.model = model
        self.effort = effort
        self.browser = use_browser
        self.title = ""
        self.claude_sid = ""                # claude 의 대화 id (--resume)
        self.created = self.updated = time.time()
        self.status = "idle"                # idle | running | awaiting
        self.allowed: list[str] = []        # 사용자가 '이 세션 동안 계속 허용' 한 도구
        self.events: list[dict] = []
        self.partials: dict[str, dict] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.waiters: dict[str, asyncio.Future] = {}
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.task: Optional[asyncio.Task] = None

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    # ── 이벤트 ──
    async def emit(self, type_: str, **data: Any) -> dict:
        ev = {"type": type_, "ts": time.time(), **data}
        if type_ == "delta":
            p = self.partials.get(data["msg_id"])
            if p is not None:
                p["text"] += data["text"]
        else:
            if type_ == "assistant_start":
                self.partials[data["msg_id"]] = {**ev, "text": ""}
            elif type_ == "assistant_end":
                self.partials.pop(data["msg_id"], None)
            elif type_ == "status":
                self.status = data["status"]
            self.events.append(ev)
            self.updated = ev["ts"]
            self.save()
        for q in list(self.subscribers):
            q.put_nowait(ev)
        return ev

    def snapshot(self) -> list[dict]:
        evs = list(self.events[-400:])
        for p in self.partials.values():
            evs.append({"type": "delta", "msg_id": p["msg_id"], "text": p["text"], "ts": p["ts"]})
        return evs

    def summary(self) -> dict:
        return {"id": self.id, "project": self.project, "title": self.title, "model": self.model,
                "effort": self.effort, "browser": self.browser, "status": self.status,
                "created": self.created, "updated": self.updated, "allowed": self.allowed,
                "pending": list(self.waiters)}

    def save(self) -> None:
        data = {**self.summary(), "claude_sid": self.claude_sid, "events": self.events}
        (SESSIONS_DIR / f"{self.id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ── 대화 ──
    async def send(self, text: str, port: int) -> None:
        if self.busy:
            raise RuntimeError("아직 답하는 중이에요. 끝나거나 ⏹ 중지한 뒤 보내 주세요")
        if not self.title:
            self.title = text.strip().splitlines()[0][:80]
        await self.emit("user", text=text)
        await self.emit("status", status="running")
        self.task = asyncio.create_task(self._turn(text, port))

    def _args(self, port: int) -> list[str]:
        run_dir = SESSIONS_DIR / self.id
        run_dir.mkdir(exist_ok=True)
        mcp_config = run_dir / "mcp.json"
        mcp_config.write_text(json.dumps({"mcpServers": {"pocketdev": {
            "command": sys.executable, "args": [str(MCP_SCRIPT)],
            "env": {"POCKETDEV_URL": f"http://127.0.0.1:{port}", "POCKETDEV_RUN_ID": "session:" + self.id,
                    "POCKETDEV_TOKEN": ACCESS_TOKEN},
        }}}), encoding="utf-8")
        args = [claude_bin(), "-p", "--model", self.model, "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--mcp-config", str(mcp_config), "--strict-mcp-config",
                "--permission-prompt-tool", "mcp__pocketdev__approve", "--permission-mode", "acceptEdits"]
        allowed = [*AUTO_ALLOW, *self.allowed]
        if self.browser:
            args.append("--chrome")
            allowed += browser.READ_ONLY
        args += ["--allowedTools", *allowed]
        if self.effort:
            args += ["--effort", self.effort]
        if self.claude_sid:
            args += ["--resume", self.claude_sid]
        return args

    async def _turn(self, text: str, port: int) -> None:
        msg_id, parts = None, []

        async def close_message(final: Optional[str] = None) -> None:
            nonlocal msg_id, parts
            if msg_id:
                await self.emit("assistant_end", msg_id=msg_id, text=final if final is not None else "".join(parts))
            msg_id, parts = None, []

        try:
            if self.browser and (note := await browser.ensure_chrome()):
                await self.emit("log", text=note)
            env = subscription_env(MCP_TOOL_TIMEOUT=str((PERMISSION_TIMEOUT + 120) * 1000))
            self.proc = await asyncio.create_subprocess_exec(
                *self._args(port), cwd=self.project, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024)
            self.proc.stdin.write(text.encode("utf-8"))
            await self.proc.stdin.drain()
            self.proc.stdin.close()
            result: dict = {}

            async def pump() -> None:
                nonlocal msg_id, result
                async for raw in self.proc.stdout:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("session_id") and ev["session_id"] != self.claude_sid:
                        self.claude_sid = ev["session_id"]
                        self.save()
                    t = ev.get("type")
                    if t == "stream_event":
                        e, delta = ev.get("event", {}), ev.get("event", {}).get("delta", {})
                        if e.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                            if not msg_id:
                                msg_id = uuid.uuid4().hex[:10]
                                await self.emit("assistant_start", msg_id=msg_id, model=self.model)
                            parts.append(delta["text"])
                            await self.emit("delta", msg_id=msg_id, text=delta["text"])
                    elif t == "assistant":
                        for block in ev.get("message", {}).get("content", []):
                            if block.get("type") == "text" and msg_id:
                                await close_message(block.get("text") or "".join(parts))
                            elif block.get("type") == "tool_use":
                                await close_message()
                                await self.emit("tool", text=tool_summary(block, self.project))
                    elif t == "result":
                        result = ev

            stderr_task = asyncio.create_task(self.proc.stderr.read())
            try:
                await asyncio.wait_for(pump(), TURN_TIMEOUT)
                await self.proc.wait()
            finally:
                if self.proc.returncode is None:
                    self.proc.kill()
            stderr = (await stderr_task).decode("utf-8", "replace")
            await close_message()
            if not result:
                check_limit(stderr)
                raise RuntimeError(f"Claude Code 가 비정상 종료했어요 ({self.proc.returncode}):\n{stderr.strip()[-1500:]}")
            usage_log.record("session", self.model, result.get("usage") or {})
            if result.get("is_error"):
                message = str(result.get("result") or result.get("subtype") or "알 수 없는 오류")
                check_limit(message)
                raise RuntimeError(message)
            await self.emit("turn_end", usage=result.get("usage") or {})
        except asyncio.CancelledError:
            await close_message()
            await self.emit("log", text="⏹ 중지했어요")
        except UsageLimitError as e:
            await close_message()
            await self.emit("error", text=f"구독 사용량 한도에 걸렸어요 — 한도가 풀리면 다시 보내 주세요 ({e})")
        except Exception as e:
            await close_message()
            await self.emit("error", text=str(e))
        finally:
            for key in list(self.waiters):
                self._answer(key, {"action": "deny"})
            self.proc = None
            await self.emit("status", status="idle")

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
        if self.task and not self.task.done():
            self.task.cancel()

    # ── 권한 (permission_mcp → /api/internal/permission) ──
    def _answer(self, req_id: str, value: dict) -> bool:
        fut = self.waiters.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(value)
            return True
        return False

    async def answer(self, req_id: str, action: str) -> bool:
        return self._answer(req_id, {"action": action})

    async def decide(self, tool_name: str, tool_input: dict) -> dict:
        blocked = permissions.hard_rule(self.project, tool_name, tool_input, direct=True)
        if blocked:
            await self.emit("log", text=f"⛔ {tool_name} 거부 — {blocked}")
            return {"behavior": "deny", "message": blocked}
        if tool_name in self.allowed:
            return {"behavior": "allow", "updatedInput": tool_input}
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.waiters[req_id] = fut
        await self.emit("permission", req_id=req_id, tool=tool_name, input=tool_input)
        await self.emit("status", status="awaiting")
        try:
            answer = await asyncio.wait_for(fut, PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            self.waiters.pop(req_id, None)
            answer = {"action": "deny", "timeout": True}
        action = answer.get("action")
        if action == "always" and tool_name not in self.allowed:
            self.allowed.append(tool_name)
        await self.emit("permission_done", req_id=req_id, action=action, timeout=bool(answer.get("timeout")))
        if self.busy:
            await self.emit("status", status="running")
        if action in ("allow", "always"):
            return {"behavior": "allow", "updatedInput": tool_input}
        return {"behavior": "deny", "message": "사용자가 거부했어요" + (" (응답 시간 초과)" if answer.get("timeout") else "")}


SESSIONS: dict[str, ChatSession] = {}


def load_saved() -> None:
    for path in sorted(SESSIONS_DIR.glob("*.json"))[-100:]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s = ChatSession(d["project"], d["model"], d.get("effort", ""), d.get("browser", False), session_id=d["id"])
        s.title, s.claude_sid, s.created, s.updated = d.get("title", ""), d.get("claude_sid", ""), d["created"], d["updated"]
        s.allowed, s.events = d.get("allowed", []), d.get("events", [])
        if d.get("status") != "idle":  # 서버 재시작으로 끊긴 대화
            s.events.append({"type": "log", "ts": time.time(), "text": "서버가 다시 시작돼 답이 끊겼어요 — 이어서 말하면 계속해요"})
        SESSIONS[s.id] = s


def delete(session_id: str) -> None:
    SESSIONS.pop(session_id, None)
    (SESSIONS_DIR / f"{session_id}.json").unlink(missing_ok=True)
    shutil.rmtree(SESSIONS_DIR / session_id, ignore_errors=True)
