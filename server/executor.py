"""Claude Code CLI 실행 (구독 로그인) + git 작업."""
import asyncio
import base64
import binascii
import contextlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import ACCESS_TOKEN, CONFIG, DATA_DIR, ROOT, chrome_accounts_setting
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


def git_bash() -> Optional[str]:
    """Git for Windows 의 bash.exe (Git\\bin\\bash.exe — /usr/bin 을 PATH 에 넣어 주는 래퍼).
    System32\\bash.exe 는 WSL 이라 쓰지 않는다."""
    if sys.platform != "win32":
        return shutil.which("bash")
    roots = [Path(p).parent for p in [shutil.which("git")] if p]
    roots.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd")
    for start in roots:
        for p in [start, *start.parents]:
            bash = p / "bin" / "bash.exe"
            if bash.exists() and (p / "usr" / "bin").is_dir():
                return str(bash)
    return None


async def terminal(command: str, cwd: str, timeout: float = 180) -> tuple[int, str]:
    """작업 화면 터미널 칸: 사용자가 적은 명령을 Git Bash 로 실행 (pwd·ls 같은 명령이 되게, 입력 프롬프트 없이).
    Git Bash 가 없으면 cmd 로 — PowerShell 은 stderr 를 파이프로 받으면 진행 표시를 CLIXML 로 섞어 내보내서 쓰지 않는다.
    여러 줄이면 한 줄씩 차례로 실행한다."""
    lines = [ln.strip() for ln in command.splitlines() if ln.strip()]
    env = {**os.environ, **GIT_ENV, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
    gh = gh_bin()
    if gh:  # 서버가 켜진 뒤 gh 를 설치했어도 쓸 수 있게
        env["PATH"] = str(Path(gh).parent) + os.pathsep + env.get("PATH", "")
    bash = git_bash()
    if bash:
        env.setdefault("LANG", "C.UTF-8")
        proc = await asyncio.create_subprocess_exec(
            bash, "-c", "\n".join(lines), cwd=cwd, env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    else:
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


async def branch_exists(cwd: str, name: str) -> bool:
    """로컬 브랜치가 있는지 — 같은 이름의 태그·커밋에 속지 않게 refs/heads 로만 본다."""
    return await _ok(cwd, "rev-parse", "--verify", "-q", f"refs/heads/{name}")


async def prepare_branch(run: Run, branch: str) -> None:
    cwd = run.project
    if CONFIG["git"].get("require_clean_tree", True):
        status = await git(cwd, "status", "--porcelain")
        if status.strip():
            raise RuntimeError("커밋 안 된 변경사항이 있어 중단합니다 (config.yaml git.require_clean_tree):\n" + status)
    current = (await git(cwd, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    base = await _base_branch(cwd)            # 저장소 기본 브랜치: origin/HEAD → main/master → main
    if not await branch_exists(cwd, base):
        base = current                        # 로컬에 기본 브랜치가 없으면 예전처럼 지금 브랜치 기준
    run.base_branch = base
    pull = CONFIG["git"].get("pull_before_work", True) and await has_origin(cwd)
    if current != branch:
        if await branch_exists(cwd, branch):
            await git(cwd, "checkout", branch)
        else:                                 # 새 브랜치는 (지금 HEAD 가 아니라) 최신 기본 브랜치에서 딴다
            if current != base:
                await git(cwd, "checkout", base)
            if pull and base != "HEAD":
                await ff_pull(run, cwd, base)
            await git(cwd, "checkout", "-b", branch)
    if pull:
        await ff_pull(run, cwd, branch)
    await run.emit("log", text=f"🌿 브랜치: {branch}")


async def ff_pull(run: Run, cwd: str, branch: str) -> None:
    """체크아웃된 branch 를 GitHub(origin) 의 최신 커밋으로 빨리 감기. 원격에 없거나 갈라졌으면 건드리지 않고 알리기만 한다
    (main 에 바로 작업할 때 GitHub 에서 머지된 PR 때문에 push 가 거부되지 않도록)."""
    code, out = await sh(["git", "fetch", "origin", branch], cwd, timeout=120)
    if code:
        if not re.search(r"couldn't find remote ref", out, re.I):  # 원격에 없는 브랜치면 조용히 넘어감
            await run.emit("log", text=f"⚠️ GitHub 에서 {branch} 최신 내용을 못 받아왔어요 (지금 상태로 진행):\n{out.strip()[-300:]}")
        return
    remote = f"refs/remotes/origin/{branch}"
    if not await _ok(cwd, "rev-parse", "--verify", "-q", remote) or await _ok(cwd, "merge-base", "--is-ancestor", remote, "HEAD"):
        return  # 이미 최신
    if not await _ok(cwd, "merge-base", "--is-ancestor", "HEAD", remote):
        await run.emit("log", text=f"⚠️ {branch} 브랜치가 로컬과 GitHub 에서 서로 갈라져 있어서 받아오지 않았어요. "
                                   f"push 가 거부되면 터미널에서 git pull --rebase 후 git push 하세요")
        return
    count = (await git(cwd, "rev-list", "--count", f"HEAD..{remote}")).strip()
    code, out = await sh(["git", "merge", "--ff-only", remote], cwd)
    await run.emit("log", text=(f"⬇️ GitHub 에서 {branch} 최신 커밋 {count}개를 받아왔어요" if code == 0
                                else f"⚠️ {branch} 최신 내용 반영 실패 (지금 상태로 진행):\n{out.strip()[-300:]}"))


SHOT_MAX_BYTES = 5 * 1024 * 1024


@dataclass
class _Relay:
    """run_claude_code 한 번 동안의 Chrome 화면 중계 상태."""
    run_dir: Path
    keep: int            # CONFIG browser.keep_shots
    dump: bool           # CONFIG browser.debug_dump
    shot_fail: int = 0   # 연속 해독·형식 실패 수
    shots_off: bool = False
    warned: bool = False # 중계 오류 로그 1회만
    dumped: int = 0      # raw.jsonl 에 쓴 줄 수 (≤5)


async def _relay_browser(run: Run, relay: _Relay, step_id: str, info: dict, block: dict, top) -> None:
    """Chrome tool_result → 스크린샷 저장 + browser 이벤트 (단계 끝 줄)."""
    verdict = run.browser_verdicts.pop(step_id, None)
    status, images, text = browser.parse_result(block, verdict, top)
    if status == "ok" and browser.is_evidence(info["name"]) and info.get("scope") == run.chrome_scope:
        with contextlib.suppress(Exception):  # 지금 범위에서 읽은 페이지에 나온 이메일 → 계정 보고 근거
            run.chrome_seen |= browser.emails_in(text)

    shot_url = None
    if relay.keep > 0 and not relay.shots_off and images:
        d = relay.run_dir / "browser"
        try:
            data = base64.b64decode(images[0][1], validate=True)
            if len(data) > SHOT_MAX_BYTES:
                raise ValueError(f"화면 이미지가 너무 큼 ({len(data) // 1024}KB)")
            ext = browser.sniff(data)
            if ext is None:
                raise ValueError("PNG/JPEG/WebP 가 아닌 화면 이미지")
            d.mkdir(exist_ok=True)
            name = browser.shot_name(int(time.time() * 1000), step_id, ext)
            part = d / (name + ".part")
            part.write_bytes(data)
            os.replace(part, d / name)
        except (binascii.Error, ValueError, OSError) as e:
            relay.shot_fail += 1
            if relay.shot_fail == 1 and not relay.warned:
                relay.warned = True
                await run.emit("log", text=f"⚠️ Chrome 화면 저장 실패(구현은 계속): {e!s:.200}")
            if relay.shot_fail >= 3:
                relay.shots_off = True
        else:
            shot_url = f"/api/runs/{run.id}/browser/{name}"
            relay.shot_fail = 0
            for p in sorted(x for x in d.iterdir() if browser.SHOT_RE.match(x.name))[:-relay.keep]:
                with contextlib.suppress(OSError):  # Windows 파일 잠금(WinError 32) 무시
                    p.unlink()

    no_image = info["action"] == "screenshot" and status == "ok" and not images
    fields = {"step": step_id, "text": info["text"], "tool": browser.short(info["name"]), "status": status,
              "write": bool(info.get("write", True))}
    if info.get("note"):
        fields["note"] = info["note"]
    if shot_url:
        fields["shot"] = shot_url
    if no_image:
        fields["no_image"] = True
    if status in ("error", "denied"):
        fields["result"] = text[:200]  # ok 본문은 싣지 않는다 (개인정보)
    await run.emit("browser", **fields)

    if relay.dump and relay.dumped < 5:  # 구조만 (본문·base64 없음). 파일명이 SHOT_RE 에 안 맞아 서빙되지 않음
        with contextlib.suppress(Exception):
            d = relay.run_dir / "browser"
            d.mkdir(exist_ok=True)
            with open(d / "raw.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(browser.structure(info["name"], block, top, verdict is not None),
                                   ensure_ascii=False) + "\n")
            relay.dumped += 1


async def run_claude_code(run: Run, prompt: str, model: str, port: int,
                          extra_mcp: Optional[dict] = None, extra_allowed: list | tuple = (),
                          chrome: bool = True) -> dict:
    """Claude Code 를 구독 로그인으로 headless 실행. 권한 요청은 permission_mcp → 리뷰어 AI 가 심사.
    extra_mcp: 추가 MCP 서버 (예: Stitch), extra_allowed: 묻지 않고 허용할 도구 (예: mcp__stitch)
    chrome: False 면 작업 설정과 상관없이 Chrome 을 쓰지 않음 (Stitch 디자인 단계)"""
    ex = CONFIG["executor"]
    use_chrome = chrome and browser.enabled(run.settings)
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
    bcfg = CONFIG.get("browser") or {}
    if use_chrome:  # Claude in Chrome — 규칙은 여기서 한 번만 붙임. 읽기 도구만 바로 허용, 나머지는 권한 심사
        keep = int(bcfg.get("keep_shots", 100))
        prompt += browser.exec_note(bcfg.get("shot_policy", "key"), bcfg.get("max_shots", 12),
                                    text_only=keep <= 0 or browser.text_only_seen(run.events))
        args.append("--chrome")
        allowed += [*browser.READ_ONLY, browser.ACCOUNT_TOOL]
        if note := await browser.ensure_chrome():  # 꺼져 있으면 (PC 재부팅 뒤 등) 켠다
            await run.emit("log", text=note)
        # 로그인 계정 상태는 실행마다 새로 (그 사이 사용자가 계정을 바꿀 수 있음) — 칩의 옛 ✅ 도 지움
        run.chrome_account = None
        run.chrome_scope = None
        run.chrome_seen = set()
        gate = bool(browser.allowed_list(chrome_accounts_setting()))
        await run.emit("browser_account", email="", allowed=False, verified=False, gate=gate, scope="")
        if not gate:
            await run.emit("log", text="👤 Chrome 로그인 계정 검사 꺼짐 — browser.allowed_chrome_accounts"
                                       "(또는 .env CHROME_ALLOWED_ACCOUNTS)가 비어 있어 계정은 표시만 해요")
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
    relay = _Relay(run_dir, int(bcfg.get("keep_shots", 100)), bool(bcfg.get("debug_dump", False)))
    pending: dict[str, dict] = {}  # tool_use_id → describe() + name (Chrome 단계 시작됨, 결과 대기)

    async def end_step(step_id: str, b: dict, top) -> None:
        info = pending.pop(step_id)
        try:
            await _relay_browser(run, relay, step_id, info, b, top)
        except Exception as e:
            with contextlib.suppress(Exception):
                if not relay.warned:
                    relay.warned = True
                    await run.emit("log", text=f"⚠️ Chrome 화면 중계 오류(구현은 계속): {e!s:.200}")
                await run.emit("browser", step=step_id, text=info["text"], tool=browser.short(info["name"]),
                               status="error", **({"note": info["note"]} if info.get("note") else {}))

    async def read_stdout():
        nonlocal result
        note = ""  # Chrome 동작 바로 앞 AI 문장 한 줄 → 그 단계의 의도 (다음 tool_use 가 나오면 비움)
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
                        run.last_text = block["text"][-300:]  # 권한 심사 참고용 (근거 아님)
                        if use_chrome:
                            note = browser.note_of(block["text"])
                    elif block.get("type") == "tool_use":
                        todo = browser.todo_of(block) if use_chrome else None
                        try:
                            d = browser.describe(block) if use_chrome and not todo else None
                        except Exception:
                            d = None
                        if todo:  # cc "TodoWrite" 줄 대신 할 일 진행 줄
                            await run.emit("todo", **todo)
                        elif d and block.get("id"):
                            pending[block["id"]] = {**d, "name": block["name"],
                                                    "write": browser.is_write(block["name"], block.get("input")),
                                                    "scope": run.chrome_scope,  # 이 시점의 범위 (E2a)
                                                    "note": note}
                            await run.emit("cc", tool=d["text"], step=block["id"], browser=True,
                                           **({"note": note} if note else {}))
                        else:
                            await run.emit("cc", tool=tool_summary(block, run.project))
                        note = ""  # 문장 하나는 단계 하나에만
            elif t == "user" and pending:  # Chrome 도구 결과 → 단계 끝 줄 (+ 스크린샷)
                try:
                    content = (ev.get("message") or {}).get("content")
                    for b in content if isinstance(content, list) else []:
                        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                            await end_step(b["tool_use_id"], b, ev.get("tool_use_result"))
                except Exception:
                    pass  # 중계 문제로 구현을 멈추지 않는다
            elif t == "result":
                result = ev

    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        await asyncio.wait_for(read_stdout(), ex.get("timeout_sec", 7200))
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
        # 결과 없이 끝난 Chrome 단계 → ⏹ 마감 (취소 중이라 못 보내면 재시작 때 orphan_steps 가 처리)
        for step_id, info in list(pending.items()):
            with contextlib.suppress(Exception):
                await run.emit("browser", step=step_id, text=info["text"], tool=browser.short(info["name"]),
                               status="stopped", **({"note": info["note"]} if info.get("note") else {}))
        pending.clear()
    stderr =(await stderr_task).decode("utf-8", "replace")
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


# hook 이 '규칙 위반'이 아니라 실행 환경 문제(node 없음 등)로 실패했다는 신호
HOOK_ENV_FAIL = re.compile(r"command not found|not recognized as an internal|No such file or directory|"
                           r"cannot (?:run|spawn|exec)|ENOENT|is not installed", re.I)


async def _commit(cwd: str, message: str, *extra: str) -> tuple[int, str]:
    """메시지를 UTF-8(BOM 없음) 파일로 넘긴다 (-m 에 한글을 넣으면 Windows 에서 깨지거나 hook 이 거부하는 프로젝트가 있음)."""
    path = DATA_DIR / f"commit_msg_{os.getpid()}_{time.time_ns()}.txt"
    path.write_text(message.strip() + "\n", encoding="utf-8")
    try:
        return await sh(["git", *author_args(), "commit", *extra, "-F", str(path)], cwd)
    finally:
        path.unlink(missing_ok=True)


async def commit_and_push(run: Run, branch: str, message: str, rewrite=None, max_rewrites: int = 2) -> dict:
    """{"summary": 커밋 요약, "pushed": push 성공 여부, "message": 실제로 커밋한 메시지}
    rewrite: 프로젝트 hook 이 메시지를 거부하면 async rewrite(이전 메시지, hook 출력) -> 새 메시지 로 고쳐 다시 커밋."""
    cwd = run.project
    await git(cwd, "add", "-A")
    await git(cwd, "reset", "-q", "--", "TASK_SPEC.md", check=False)
    staged = bool((await git(cwd, "diff", "--cached", "--name-only")).strip())
    unpushed = "" if staged else await _unpushed(cwd, branch, getattr(run, "base_branch", ""))
    if not staged and not unpushed:
        return {"summary": "변경 사항 없음", "pushed": False}
    if staged:
        code, out = await _commit(cwd, message)
        tries = 0
        while code and rewrite and tries < max_rewrites and not HOOK_ENV_FAIL.search(out):
            # 프로젝트 규칙(커밋 메시지 형식 등)에 걸림 → hook 출력을 보고 메시지를 고쳐 다시 커밋 (작업 트리는 그대로)
            tries += 1
            await run.emit("log", text=f"✏️ 프로젝트 hook 이 커밋을 거부해서 메시지를 고쳐 다시 커밋해요 ({tries}/{max_rewrites}):\n"
                                       f"{out.strip()[-400:]}")
            message = await rewrite(message, out)
            await run.emit("log", text=f"📝 커밋 메시지: {message.splitlines()[0]}")
            code, out = await _commit(cwd, message)
        if code and HOOK_ENV_FAIL.search(out) and re.search(r"hook|commit-msg|pre-commit|husky", out, re.I):
            # 프로젝트의 git hook 이 실행 환경 문제(예: node 없음)로 실패 → 이유를 남기고 hook 없이 커밋
            await run.emit("log", text=f"⚠️ 프로젝트 git hook 이 실패해서 hook 없이 커밋했어요:\n{out.strip()[-400:]}")
            code, out = await _commit(cwd, message, "--no-verify")
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
    return {"summary": await git(cwd, "show", "--stat", "--format=%h %s", "HEAD"), "pushed": pushed, "message": message}


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


def _repo_key(url: str) -> str:
    """origin 주소 → 비교용 키. GitHub 이면 'owner/repo'(소문자, .git·끝 슬래시 제거), 아니면 정규화한 주소 그대로."""
    u = url.strip().replace("\\", "/").lower().rstrip("/").removesuffix(".git").rstrip("/")
    return re.split(r"github\.com[:/]", u)[-1] if "github.com" in u else u


async def _origin(cwd: str) -> str:
    """origin 주소. 없거나 실패하면 "" (sh 는 stderr 를 섞으므로 종료 코드로 판단)."""
    code, out = await sh(["git", "remote", "get-url", "origin"], cwd, timeout=15)
    return out.strip() if code == 0 else ""


async def self_deploy(project: str, branch: str) -> str | None:
    """서버가 별도 clone(예: jarvis)에서 돌 때, 같은 저장소·같은 브랜치로 push/머지가 끝나면 서버 폴더를 FF pull.
    알릴 게 없으면 None. pull 실패만 ⚠️ 문자열. 절대 raise 하지 않음 (CancelledError 는 그대로 통과)."""
    r, name, pulling = str(ROOT), ROOT.name, False
    try:
        if Path(project).resolve() == ROOT.resolve() or not CONFIG["git"].get("self_deploy", True):
            return None
        mine = await _origin(r)
        if not mine or _repo_key(mine) != _repo_key(await _origin(project)):
            return None
        code, cur = await sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], r, timeout=15)
        if code or cur.strip() != branch:
            return None
        code, old = await sh(["git", "rev-parse", "HEAD"], r, timeout=15)
        if code:
            return None
        old = old.strip()
        pulling = True
        code, out = await sh(["git", "pull", "--ff-only", "origin", branch], r, timeout=120)
        if code:
            return f"⚠️ 서버 폴더({name}) pull 실패 — PC 에서 확인: {out.strip()[-200:]}"
        new = (await sh(["git", "rev-parse", "HEAD"], r, timeout=15))[1].strip()
        if new == old:
            return None
        files = (await sh(["git", "diff", "--name-only", old, new], r, timeout=15))[1]
        msg = f"🛰 서버 폴더({name}) 도 {branch} 최신으로 받았어요 — 새로고침 ({old[:7]} → {new[:7]})"
        if re.search(r"^(server/|config\.yaml$)", files, re.M):
            msg += "\n🔄 서버 코드가 바뀌었어요 — 새로고침한 뒤 상단 🔄 로 재시작해야 반영돼요 (미루지 마세요)"
        return msg
    except Exception as e:   # 시간 초과(RuntimeError) 포함
        return f"⚠️ 서버 폴더({name}) pull 실패 — PC 에서 확인: {str(e)[-200:]}" if pulling else None
