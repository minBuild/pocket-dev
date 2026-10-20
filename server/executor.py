"""Claude Code CLI 실행 (구독 로그인) + git 작업."""
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

from .config import ACCESS_TOKEN, CONFIG, DATA_DIR
from . import browser
from . import usage as usage_log
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


TERMINAL_MAX_OUTPUT = 20000


def _decode_line(raw: bytes) -> str:
    """git·gh 는 UTF-8 로, cmd 내장 명령(echo 등)은 Windows 코드 페이지(한글이면 cp949)로 내보낸다."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if sys.platform == "win32" else "latin-1", "replace")


async def terminal(command: str, cwd: str, timeout: float = 180) -> tuple[int, str]:
    """작업 화면 터미널 칸: 사용자가 적은 명령을 cmd 로 실행 (입력 프롬프트 없이, 출력은 UTF-8).
    PowerShell 은 stderr 를 파이프로 받으면 진행 표시를 CLIXML 로 섞어 내보내서 cmd 를 쓴다.
    여러 줄이면 한 줄씩 차례로 (& 로 이어서) 실행한다."""
    lines = [ln.strip() for ln in command.splitlines() if ln.strip()]
    env = {**os.environ, **GIT_ENV, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
    gh = gh_bin()
    if gh:  # 서버가 켜진 뒤 gh 를 설치했어도 쓸 수 있게
        env["PATH"] = str(Path(gh).parent) + os.pathsep + env.get("PATH", "")
    proc = await asyncio.create_subprocess_shell(
        "chcp 65001 >nul & " + " & ".join(lines), cwd=cwd, env=env,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        await sh(["taskkill", "/T", "/F", "/PID", str(proc.pid)], cwd)  # git 같은 자식 프로세스까지
        return -1, f"{timeout:.0f}초 안에 끝나지 않아 중단했어요"
    text = "".join(_decode_line(b) for b in out.splitlines(keepends=True))
    if len(text) > TERMINAL_MAX_OUTPUT:
        text = "…(앞부분 생략)\n" + text[-TERMINAL_MAX_OUTPUT:]
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


async def run_claude_code(run: Run, prompt: str, model: str, port: int,
                          extra_mcp: Optional[dict] = None, extra_allowed: list | tuple = ()) -> dict:
    """Claude Code 를 구독 로그인으로 headless 실행. 권한 요청은 permission_mcp → 리뷰어 AI 가 심사.
    extra_mcp: 추가 MCP 서버 (예: Stitch), extra_allowed: 묻지 않고 허용할 도구 (예: mcp__stitch)"""
    ex = CONFIG["executor"]
    run_dir = DATA_DIR / "runs" / run.id
    run_dir.mkdir(parents=True, exist_ok=True)
    mcp_config = run_dir / "mcp.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"pocketdev": {
        "command": sys.executable, "args": [str(MCP_SCRIPT)],
        "env": {"POCKETDEV_URL": f"http://127.0.0.1:{port}", "POCKETDEV_RUN_ID": run.id,
                "POCKETDEV_TOKEN": ACCESS_TOKEN},
    }, **(extra_mcp or {})}}), encoding="utf-8")

    args = [claude_bin(), "-p", "--model", model, "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(mcp_config), "--strict-mcp-config",
            "--permission-prompt-tool", "mcp__pocketdev__approve",
            "--permission-mode", ex.get("permission_mode", "acceptEdits")]
    if ex.get("effort"):
        args += ["--effort", ex["effort"]]
    if run.attachments:  # 사용자 첨부 자료도 읽을 수 있게
        args += ["--add-dir", run.attach_dir]
    allowed = [*ex.get("always_allow_tools", []), *extra_allowed]
    if browser.enabled(run.settings):  # Claude in Chrome — 읽기 도구만 바로 허용, 나머지는 권한 심사
        args.append("--chrome")
        allowed += browser.READ_ONLY
    if allowed:
        args += ["--allowedTools", *allowed]

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
    usage_log.record("executor", model, result.get("usage") or {})
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
    staged = bool((await git(cwd, "diff", "--cached", "--name-only")).strip())
    unpushed = "" if staged else await _unpushed(cwd, branch, getattr(run, "base_branch", ""))
    if not staged and not unpushed:
        return {"summary": "변경 사항 없음", "pushed": False}
    if staged:
        code, out = await sh(["git", *author_args(), "commit", "-m", message], cwd)
        if code and re.search(r"hook|commit-msg|pre-commit|husky", out, re.I):
            # 프로젝트의 git hook 이 실행 환경 문제(예: node 없음)로 실패 → 이유를 남기고 hook 없이 커밋
            await run.emit("log", text=f"⚠️ 프로젝트 git hook 이 실패해서 hook 없이 커밋했어요:\n{out.strip()[-400:]}")
            code, out = await sh(["git", *author_args(), "commit", "--no-verify", "-m", message], cwd)
        if code:
            raise RuntimeError(f"git commit 실패:\n{out.strip()[-1500:]}")
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
    if not staged:  # 새로 커밋할 건 없지만 아직 GitHub 에 안 올라간 커밋이 있었음 (push 만 함)
        return {"summary": f"새 변경은 없고, 아직 안 올라간 커밋을 push 했어요:\n{unpushed}", "pushed": pushed}
    return {"summary": await git(cwd, "show", "--stat", "--format=%h %s", "HEAD"), "pushed": pushed}


async def _unpushed(cwd: str, branch: str, base: str = "") -> str:
    """branch 에서 아직 GitHub 에 안 올라간 커밋 목록 (원격이 없으면 빈 문자열)."""
    if not await has_origin(cwd):
        return ""
    if (await sh(["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"], cwd))[0] == 0:
        since = f"origin/{branch}"
    elif base and base not in ("HEAD", branch):
        since = base  # 원격에 아직 없는 브랜치 → 시작 브랜치 이후 커밋
    else:
        return ""
    return (await git(cwd, "log", "--format=%h %s", f"{since}..{branch}", check=False)).strip()


async def _ok(cwd: str, *args: str) -> bool:
    return (await sh(["git", *args], cwd))[0] == 0


async def _base_branch(cwd: str) -> str:
    """머지 대상 기본 브랜치: origin/HEAD → main/master 중 있는 것 → main."""
    base = await default_branch(cwd, "")
    if base:
        return base
    for name in ("main", "master"):
        if await _ok(cwd, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}") \
                or await _ok(cwd, "rev-parse", "--verify", "-q", f"refs/heads/{name}"):
            return name
    return "main"


async def _merge_commit(run: Run, cwd: str, base: str, branch: str, target: str, head: str) -> str:
    """갈라진 경우: 작업 폴더와 별개인 임시 worktree 에서 머지 커밋. 충돌이면 취소하고 RuntimeError."""
    tmp = DATA_DIR / "merge" / run.id
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    await git(cwd, "worktree", "add", "--detach", str(tmp), target)
    try:
        merge = ["git", *author_args(), "merge", "--no-ff", "--no-edit", "-m", f"chore(merge): {branch} 브랜치를 {base} 에 병합"]
        code, out = await sh([*merge, head], str(tmp))
        conflicts = (await git(str(tmp), "diff", "--name-only", "--diff-filter=U", check=False)).split() if code else []
        if code and not conflicts and re.search(r"hook|commit-msg|pre-merge|husky", out, re.I):
            await sh(["git", "merge", "--abort"], str(tmp))  # 프로젝트 hook 이 환경 문제로 실패 → hook 없이 (커밋과 같은 규칙)
            await run.emit("log", text=f"⚠️ 프로젝트 git hook 이 실패해서 hook 없이 머지했어요:\n{out.strip()[-400:]}")
            code, out = await sh([*merge, "--no-verify", head], str(tmp))
        if code:
            await sh(["git", "merge", "--abort"], str(tmp))
            raise RuntimeError(f"{base} 와 {branch} 가 충돌해서 머지하지 않았어요 (아무것도 안 바뀜). 충돌 파일: "
                               + (", ".join(conflicts[:10]) or out.strip()[-300:]))
        return (await git(str(tmp), "rev-parse", "HEAD")).strip()
    finally:
        await sh(["git", "worktree", "remove", "--force", str(tmp)], cwd)
        await sh(["git", "worktree", "prune"], cwd)


async def merge_into_default(run: Run, branch: str) -> dict:
    """작업 브랜치를 기본 브랜치(main)에 머지하고 push → {"text", "base", "pushed"}.
    빨리 감기면 작업 폴더·현재 브랜치를 건드리지 않고 ref 만 옮긴다. 강제 push 는 하지 않는다."""
    cwd = run.project
    if not await has_origin(cwd):
        raise RuntimeError("GitHub 원격(origin)이 없어요 — 커밋·push 가 한 번 끝난 뒤에 머지할 수 있어요")
    code, out = await sh(["git", "fetch", "origin"], cwd)
    if code:
        raise RuntimeError(f"git fetch 실패:\n{out.strip()[-500:]}")
    base = await _base_branch(cwd)
    if base == branch:
        raise RuntimeError(f"작업 브랜치가 기본 브랜치({base})와 같아서 머지할 게 없어요")
    head = (await git(cwd, "rev-parse", f"refs/heads/{branch}")).strip()
    code, out = await sh(["git", "push", "origin", f"refs/heads/{branch}"], cwd)  # 작업 브랜치부터 GitHub 에
    if code:
        raise RuntimeError(f"{branch} push 실패:\n{out.strip()[-500:]}")
    local = (await sh(["git", "rev-parse", "--verify", "-q", f"refs/heads/{base}"], cwd))[1].strip()
    remote = (await sh(["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{base}"], cwd))[1].strip()
    target = remote or local
    if not target:  # 기본 브랜치가 아예 없음 → 작업 브랜치로 만든다
        new, how = head, f"{base} 브랜치가 없어서 {branch} 로 새로 만들었어요"
    elif await _ok(cwd, "merge-base", "--is-ancestor", head, target):
        return {"text": f"✅ {base} 에 {branch} 의 커밋이 이미 다 들어가 있어요 (머지할 것 없음)", "base": base, "pushed": False}
    elif await _ok(cwd, "merge-base", "--is-ancestor", target, head):
        count = (await git(cwd, "rev-list", "--count", f"{target}..{head}")).strip()
        new, how = head, f"{base} ← {branch} 빨리 감기 (커밋 {count}개)"
    else:
        new = await _merge_commit(run, cwd, base, branch, target, head)
        how = f"{base} ← {branch} 머지 커밋 {new[:7]}"
    code, out = await sh(["git", "push", "origin", f"{new}:refs/heads/{base}"], cwd)
    if code:
        raise RuntimeError(f"{base} push 실패 (GitHub 의 {base} 가 그사이 바뀌었을 수 있어요):\n{out.strip()[-500:]}")
    note = ""
    current = (await git(cwd, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    if not local:
        await git(cwd, "branch", base, new)
    elif not await _ok(cwd, "merge-base", "--is-ancestor", local, new):
        note = f" (PC 의 {base} 에는 따로 커밋이 있어서 그대로 뒀어요)"
    elif current == base:  # main 이 체크아웃돼 있으면 ref 만 옮기면 작업 폴더가 어긋남 → 작업 폴더째 빨리 감기
        if not await _ok(cwd, "merge", "--ff-only", "-q", new):
            note = f" (PC 의 {base} 작업 폴더는 커밋 안 된 변경 때문에 그대로 뒀어요)"
    else:
        await git(cwd, "update-ref", f"refs/heads/{base}", new, local)  # PC 의 main 도 같이 (작업 폴더는 그대로)
    return {"text": f"🔀 {how} · push 완료{note}", "base": base, "pushed": True}
