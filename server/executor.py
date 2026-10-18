"""Claude Code CLI 실행 (구독 로그인) + git 작업."""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .config import ACCESS_TOKEN, CONFIG, DATA_DIR
from .engines import check_limit, claude_bin, subscription_env, tool_summary
from .runs import Run

MCP_SCRIPT = Path(__file__).with_name("permission_mcp.py")


# 원격으로 돌릴 때 git 이 로그인 창/프롬프트를 띄우고 멈추지 않도록 바로 실패하게 함
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


async def sh(args: list[str], cwd: str, check: bool = False, timeout: float = 600) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env={**os.environ, **GIT_ENV},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{' '.join(args)} 이(가) {timeout:.0f}초 안에 끝나지 않아 중단했습니다")
    text = out.decode("utf-8", "replace")
    if check and proc.returncode:
        raise RuntimeError(f"{' '.join(args)} 실패:\n{text}")
    return proc.returncode, text


async def git(cwd: str, *args: str, check: bool = True) -> str:
    return (await sh(["git", *args], cwd, check))[1]


def author_args() -> list[str]:
    """커밋 작성자를 config 의 계정(minBuild)으로 — 전역 git 설정은 건드리지 않음."""
    g = CONFIG["git"]
    args = []
    if g.get("author_name"):
        args += ["-c", f"user.name={g['author_name']}"]
    if g.get("author_email"):
        args += ["-c", f"user.email={g['author_email']}"]
    return args


def gh_bin() -> Optional[str]:
    """GitHub CLI 경로 (서버가 켜진 뒤 설치돼 PATH 에 없을 수도 있어 기본 설치 위치도 확인)."""
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe"
    return shutil.which("gh") or (str(default) if default.exists() else None)


async def init_project(path: Path) -> None:
    """새 프로젝트 폴더 + git 저장소 + 첫 커밋."""
    path.mkdir(parents=True)
    await git(str(path), "init", "-b", "main")
    (path / "README.md").write_text(f"# {path.name}\n", encoding="utf-8")
    await git(str(path), "add", "-A")
    await git(str(path), *author_args(), "commit", "-m", "chore: 프로젝트 생성 (Pocket-Dev)")


async def has_origin(cwd: str) -> bool:
    return (await sh(["git", "remote", "get-url", "origin"], cwd))[0] == 0


async def create_origin(run: Run, cwd: str) -> bool:
    """origin 이 없는 프로젝트 → GitHub 에 같은 이름의 저장소를 만들어 연결."""
    gh, owner = gh_bin(), CONFIG["git"].get("github_owner")
    if not gh or not owner:
        await run.emit("log", text="⚠️ 원격 저장소가 없고 GitHub CLI 가 없어 push 를 건너뜁니다")
        return False
    name = Path(cwd).name
    visibility = "--private" if CONFIG["git"].get("new_repo_private", True) else "--public"
    code, out = await sh([gh, "repo", "create", f"{owner}/{name}", visibility,
                          "--source", cwd, "--remote", "origin"], cwd)
    if code:
        await run.emit("log", text=f"⚠️ GitHub 저장소 생성 실패 ({owner}/{name}):\n{out.strip()[-500:]}")
        return False
    await run.emit("log", text=f"📦 GitHub 에 {'비공개 ' if visibility == '--private' else ''}저장소를 만들었어요: {owner}/{name}")
    return True


async def prepare_branch(run: Run, branch: str) -> None:
    cwd = run.project
    if CONFIG["git"].get("require_clean_tree", True):
        status = await git(cwd, "status", "--porcelain")
        if status.strip():
            raise RuntimeError("커밋 안 된 변경사항이 있어 중단합니다 (config.yaml git.require_clean_tree):\n" + status)
    current = (await git(cwd, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    run.base_branch = current
    if current != branch:
        code, _ = await sh(["git", "rev-parse", "--verify", branch], cwd)
        await git(cwd, "checkout", branch) if code == 0 else await git(cwd, "checkout", "-b", branch)
    await run.emit("log", text=f"🌿 브랜치: {branch}")


async def run_claude_code(run: Run, prompt: str, model: str, port: int) -> dict:
    """Claude Code 를 구독 로그인으로 headless 실행. 권한 요청은 permission_mcp → 리뷰어 AI 가 심사."""
    ex = CONFIG["executor"]
    run_dir = DATA_DIR / "runs" / run.id
    run_dir.mkdir(parents=True, exist_ok=True)
    mcp_config = run_dir / "mcp.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"pocketdev": {
        "command": sys.executable, "args": [str(MCP_SCRIPT)],
        "env": {"POCKETDEV_URL": f"http://127.0.0.1:{port}", "POCKETDEV_RUN_ID": run.id,
                "POCKETDEV_TOKEN": ACCESS_TOKEN},
    }}}), encoding="utf-8")

    args = [claude_bin(), "-p", "--model", model, "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(mcp_config), "--strict-mcp-config",
            "--permission-prompt-tool", "mcp__pocketdev__approve",
            "--permission-mode", ex.get("permission_mode", "acceptEdits")]
    if ex.get("effort"):
        args += ["--effort", ex["effort"]]
    if ex.get("always_allow_tools"):
        args += ["--allowedTools", *ex["always_allow_tools"]]

    env = subscription_env(MCP_TOOL_TIMEOUT=str((ex.get("human_timeout_sec", 600) + 300) * 1000))
    await run.emit("log", text=f"🤖 Claude Code 시작 ({model}, 구독 로그인, 권한 모드 {ex.get('permission_mode')})")
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=run.project, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024)
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    result: dict = {}

    async def read_stdout():
        nonlocal result
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                await run.emit("cc", text=line[:500])
                continue
            t = ev.get("type")
            if t == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        await run.emit("cc", text=block["text"][:4000])
                    elif block.get("type") == "tool_use":
                        await run.emit("cc", tool=tool_summary(block, run.project))
            elif t == "result":
                result = ev

    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        await asyncio.wait_for(read_stdout(), ex.get("timeout_sec", 7200))
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
    stderr = (await stderr_task).decode("utf-8", "replace")
    if not result:
        check_limit(stderr)
        raise RuntimeError(f"Claude Code 비정상 종료 ({proc.returncode}):\n{stderr[-3000:]}")
    if result.get("is_error"):
        check_limit(str(result.get("result", "")))
        await run.emit("log", text=f"⚠️ Claude Code 오류 종료: {str(result.get('result') or result.get('subtype'))[:500]}")
    else:
        usage = result.get("usage") or {}
        await run.emit("log", text=f"🤖 Claude Code 종료 — {result.get('num_turns', '?')}턴, "
                                   f"출력 {usage.get('output_tokens', '?')} 토큰 (구독 사용량에서 차감)")
    return result


async def staged_diff(cwd: str, limit: int = 150_000) -> str:
    await git(cwd, "add", "-A")
    await git(cwd, "reset", "-q", "--", "TASK_SPEC.md", check=False)
    stat = await git(cwd, "diff", "--cached", "--stat")
    diff = await git(cwd, "diff", "--cached")
    return f"{stat}\n{diff[:limit]}" + ("\n...(diff 생략)" if len(diff) > limit else "")


async def default_branch(cwd: str, fallback: str = "main") -> str:
    """원격의 기본 브랜치 (origin/HEAD). 모르면 fallback."""
    code, out = await sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd)
    return out.strip().split("/", 1)[-1] if code == 0 and out.strip() else fallback


async def commit_and_push(run: Run, branch: str, message: str) -> dict:
    """{"summary": 커밋 요약, "pushed": push 성공 여부}"""
    cwd = run.project
    await git(cwd, "add", "-A")
    await git(cwd, "reset", "-q", "--", "TASK_SPEC.md", check=False)
    if not (await git(cwd, "diff", "--cached", "--name-only")).strip():
        return {"summary": "변경 사항 없음", "pushed": False}
    await git(cwd, *author_args(), "commit", "-m", message)
    pushed = False
    if CONFIG["git"].get("auto_push", True):
        fresh = not await has_origin(cwd)
        if not fresh or await create_origin(run, cwd):
            base = getattr(run, "base_branch", "")
            if fresh and base and base not in ("HEAD", branch):  # 새 저장소면 원래 브랜치(main 등)도 올림
                await sh(["git", "push", "-u", "origin", base], cwd)
            code, out = await sh(["git", "push", "-u", "origin", branch], cwd)
            pushed = code == 0
            await run.emit("log", text=("⬆️ push 완료" if pushed else f"⚠️ push 실패:\n{out}"))
    return {"summary": await git(cwd, "show", "--stat", "--format=%h %s", "HEAD"), "pushed": pushed}
