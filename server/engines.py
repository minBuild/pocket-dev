"""구독 로그인된 공식 CLI 를 호출하는 엔진 (API 키 / 크레딧 결제 없음).

1차 MVP: Claude Code (`claude -p`) — Pro/Max 구독 로그인 사용.
generate() 는 토론·명세서·리뷰·권한 심사에 쓰는 "한 번 답하기" 호출이다.
"""
import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from . import usage as usage_log
from .config import CONFIG, DATA_DIR

OnText = Optional[Callable[[str], Awaitable[None]]]

SANDBOX = DATA_DIR / "sandbox"  # 도구 없이 답만 받을 때 쓰는 빈 작업 폴더
SANDBOX.mkdir(parents=True, exist_ok=True)

# 이 변수가 있으면 claude 가 구독 대신 API 과금으로 동작하므로 자식 프로세스에서 지운다
API_AUTH_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# 예: "You've hit your session limit · resets 12:40am (Asia/Seoul)", "Claude AI usage limit reached|1760800000"
LIMIT_RE = re.compile(r"(usage|session|weekly|daily|5-hour|opus|sonnet)[\s-]+limit|limit (reached|exceeded)"
                      r"|hit your .{0,20}limit|rate.?limit|out of (extra )?usage|\b429\b", re.I)
RESET_RE = re.compile(r"resets?\s+(?:at\s+)?(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
                      r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?(?:\s*\((?P<tz>[^)]+)\))?", re.I)


class UsageLimitError(RuntimeError):
    """구독 사용량 한도 도달. reset_at 은 알 수 있으면 epoch 초."""

    def __init__(self, message: str, reset_at: Optional[float] = None):
        super().__init__(message)
        self.reset_at = reset_at


def parse_reset(message: str) -> Optional[float]:
    """한도 메시지에서 리셋 시각(epoch 초)을 읽는다. 'resets 12:40am (Asia/Seoul)' · 'resets Oct 20, 3pm' · '|epoch'"""
    m = re.search(r"\|(\d{10})\b", message)
    if m:
        return float(m.group(1))
    m = RESET_RE.search(message)
    if not m:
        return None
    tz = None
    if m.group("tz"):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(m.group("tz").strip())
        except Exception:
            tz = None  # 시간대 정보를 못 읽으면 이 PC 의 시간대로
    now = datetime.now(tz) if tz else datetime.now().astimezone()
    hour, minute = int(m.group("h")), int(m.group("m") or 0)
    if m.group("ap"):
        hour = hour % 12 + (12 if m.group("ap").lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if m.group("mon"):
        try:
            target = target.replace(month=datetime.strptime(m.group("mon")[:3].title(), "%b").month,
                                    day=int(m.group("day")))
        except ValueError:
            pass
        if target < now - timedelta(days=1):
            target = target.replace(year=target.year + 1)
    else:
        if target <= now:
            target += timedelta(days=1)
        if target - now > timedelta(hours=6):  # 날짜 없는 리셋(세션 한도)이 6시간 넘게 남았다면 잘못 읽은 것
            return None
    return target.timestamp()


def check_limit(message: str) -> None:
    """한도 관련 오류 메시지면 UsageLimitError 로 바꿔 던진다."""
    if message and LIMIT_RE.search(message):
        raise UsageLimitError(message.split("|")[0].strip() or "구독 사용량 한도 도달", parse_reset(message))


def claude_bin() -> str:
    path = shutil.which("claude")
    if not path:
        raise RuntimeError("claude CLI 를 찾을 수 없습니다 — Claude Code 설치 후 `claude auth login`")
    return path


def subscription_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in API_AUTH_VARS}
    env.update(extra)
    return env


def tool_summary(block: dict, root: Optional[str] = None) -> str:
    """도구 호출 한 줄 요약. root 를 주면 그 아래 경로는 상대경로로 줄인다."""
    name, inp = block.get("name", "?"), block.get("input") or {}
    detail = str(inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or "")
    if root:
        for sep in ("\\", "/"):
            base = root.replace("\\", sep).rstrip(sep)
            detail = detail.replace(base + sep, "")
            detail = re.sub(re.escape(base) + r"(?=[\"'\s;]|$)", ".", detail)  # 경로가 딱 끝나는 경우만
    return f"{name} {detail[:200]}".strip()


_status_cache: dict = {"at": 0.0, "data": None}


async def engine_status() -> dict:
    """{"claude": {installed, logged_in, plan, method, detail}}"""
    if _status_cache["data"] and time.time() - _status_cache["at"] < 60:
        return _status_cache["data"]
    claude = {"installed": False, "logged_in": False, "plan": None, "method": None, "detail": ""}
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin(), "auth", "status", env=subscription_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        claude["installed"] = True
        text = out.decode("utf-8", "replace")
        try:
            info = json.loads(text)
            claude.update(logged_in=bool(info.get("loggedIn")), plan=info.get("subscriptionType"),
                          method=info.get("authMethod"))
        except json.JSONDecodeError:
            claude["detail"] = text.strip()[:300]
    except Exception as e:
        claude["detail"] = str(e)
    data = {"claude": claude}
    _status_cache.update(at=time.time(), data=data)
    return data


async def generate(engine: str, model: str, system: str, prompt: str, *,
                   cwd: Optional[str] = None, tools: list | tuple = (), effort: Optional[str] = None,
                   add_dirs: list | tuple = (),
                   on_text: OnText = None, on_tool: OnText = None, timeout: float = 1800) -> dict:
    """한 번 답하기. 반환 {"text": 최종 답변, "usage": 토큰 사용량}.

    tools 를 주면 읽기 전용 도구(Read/Grep/Glob 등)로 cwd 안의 파일을 직접 열어볼 수 있다.
    허용 목록 밖의 도구는 묻지 않고 거부된다.
    """
    if engine != "claude":
        raise RuntimeError(f"'{engine}' 엔진은 아직 준비 중입니다 (1차 MVP 는 claude 만 지원)")

    args = [claude_bin(), "-p", "--model", model,
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--system-prompt", system,
            "--tools", ",".join(tools),
            "--permission-prompts", "none",
            "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
            "--setting-sources", "project,local"]
    if effort:
        args += ["--effort", effort]
    if add_dirs:
        args += ["--add-dir", *add_dirs]
    if tools:
        args += ["--allowedTools", *tools]

    max_out = CONFIG["debate"].get("max_output_tokens")
    env = subscription_env(**({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_out)} if max_out else {}))
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd or str(SANDBOX), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024)
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    parts: list[str] = []
    result: dict = {}

    async def pump():
        nonlocal result
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "stream_event":
                e = ev.get("event", {})
                delta = e.get("delta", {})
                if e.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    parts.append(delta["text"])
                    if on_text:
                        await on_text(delta["text"])
            elif t == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        parts.clear()  # 도구 쓰기 전 중간 멘트는 버리고 마지막 답만 남김
                        if on_tool:
                            await on_tool(tool_summary(block, cwd))
            elif t == "result":
                result = ev

    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        await asyncio.wait_for(pump(), timeout)
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
    stderr = (await stderr_task).decode("utf-8", "replace")

    if not result:
        check_limit(stderr)
        raise RuntimeError(f"claude 실행 실패 (exit {proc.returncode}): {stderr.strip()[-1500:]}")
    usage_log.record("generate", model, result.get("usage") or {})
    if result.get("is_error"):
        message = str(result.get("result") or result.get("subtype") or "알 수 없는 오류")
        check_limit(message)
        raise RuntimeError(message)
    return {"text": result.get("result") or "".join(parts), "usage": result.get("usage") or {}}
