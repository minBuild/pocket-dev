"""Claude Code 권한 요청 → 하드 룰 → 리뷰어 AI 투표 → (필요시) 사람에게 폰으로 질문."""
import asyncio
import json
import re
import uuid
from pathlib import Path

from . import browser
from .agents import judge_permission
from .config import CONFIG
from .runs import Run

_GIT = r"\bgit(?:\s+(?:-[Cc]\s+(?:\"[^\"]*\"|'[^']*'|\S+)|--[\w-]+(?:=\S+)?))*\s+"  # git -C 경로 / -c 키=값 / --옵션 뒤의 하위 명령
HARD_DENY_SHELL = [
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|\*|\.\.|[A-Za-z]:\\)(\s|$)", "루트/홈/상위 폴더 삭제"),
    (r"\b(Remove-Item|rm|del|rd|rmdir)\b[^|;\n]*\s['\"]?([A-Za-z]:\\?|~|\$HOME|\$env:USERPROFILE|\.\.)['\"]?(\s|$)",
     "루트/홈/상위 폴더 삭제"),
    (_GIT + r"push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "강제 push"),
    (_GIT + r"push\b", "push 는 Pocket-Dev 가 실행 끝에 함"),
    (_GIT + r"(commit|merge|rebase|switch|cherry-pick|am)\b",
     "커밋·머지·브랜치는 Pocket-Dev 가 실행 끝에 변경 내용 기준으로 함 — 커밋하지 말고 작업만 끝내"),
    (_GIT + r"checkout\s+(-[bB]\b|-(\s|$)|(main|master)\b|origin/)", "브랜치 전환은 Pocket-Dev 가 함 — 지금 브랜치에서 작업해"),
    (_GIT + r"(reset\s+--hard|clean\s+-[a-z]*f|filter-branch|reflog\s+expire)", "히스토리/작업 삭제"),
    (r"\bsudo\b|\bmkfs\b|\bshutdown\b|\breboot\b|\bformat\s+[A-Za-z]:", "시스템 명령"),
    (r"\b(Format-Volume|Clear-Disk|Stop-Computer|Restart-Computer|Set-ExecutionPolicy)\b", "시스템 명령"),
    (r"(curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)[^|]*\|\s*(sh|bash|zsh|iex|Invoke-Expression|powershell)",
     "원격 스크립트 실행"),
    (r"\b(xcodebuild|pod\s+install|open\s+-a\s+Simulator)\b", "macOS 전용 명령 (CLAUDE.md 규칙)"),
]
SHELL_TOOLS = {"Bash", "PowerShell"}   # Windows 에서는 Claude Code 가 PowerShell 도구도 씀
PATH_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def hard_rule(project: str, tool_name: str, tool_input: dict) -> str | None:
    """무조건 거부해야 하면 사유를 돌려준다."""
    if tool_name in browser.HARD_DENY_TOOLS:
        return "하드 룰 거부: Chrome 스크립트 실행·파일 업로드 금지"
    if tool_name in SHELL_TOOLS:
        cmd = str(tool_input.get("command", ""))
        for pattern, why in HARD_DENY_SHELL:
            if re.search(pattern, cmd, re.I):
                return f"하드 룰 거부: {why}"
    if tool_name in PATH_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if path:
            target = (Path(project) / path).resolve()
            if not target.is_relative_to(Path(project).resolve()):
                return f"하드 룰 거부: 프로젝트 밖 파일 수정 ({path})"
    return None


def _aggregate(votes: list[dict], policy: str) -> str:
    """allow / deny / disagree"""
    allows = sum(v["decision"] == "allow" for v in votes)
    denies = sum(v["decision"] == "deny" for v in votes)
    asks = sum(v["decision"] == "ask_human" for v in votes)
    if policy == "any":
        return "allow" if allows else ("deny" if denies == len(votes) else "disagree")
    if policy == "majority":
        if allows > len(votes) / 2:
            return "allow"
        return "deny" if denies > len(votes) / 2 else "disagree"
    # unanimous
    if allows == len(votes):
        return "allow"
    if denies == len(votes):
        return "deny"
    return "disagree" if (allows or asks) else "deny"


async def decide(run: Run, tool_name: str, tool_input: dict, tool_use_id: str | None = None) -> dict:
    """Claude Code --permission-prompt-tool 형식의 응답을 만든다."""
    if browser.auto_allow(tool_name, tool_input):  # Chrome 스크린샷·스크롤 등 — 심사·카드 없이
        return {"behavior": "allow", "updatedInput": tool_input}
    ex = CONFIG["executor"]
    req_id = uuid.uuid4().hex[:8]
    cache_key = json.dumps([tool_name, tool_input], sort_keys=True, ensure_ascii=False)
    chrome, label = browser.is_tool(tool_name), ""
    if chrome:
        d = browser.describe({"name": tool_name, "input": tool_input})
        label = d["text"] if d else tool_name
    await run.emit("permission_request", req_id=req_id, tool=tool_name, input=tool_input,
                   label=label, browser=chrome)

    def done(allow: bool, reason: str, by: str, remember: bool = True) -> dict:
        if remember and browser.rememberable(tool_name):  # 같은 실행 안에서 똑같은 요청은 다시 심사하지 않음 (구독 한도 절약)
            run.perm_cache[cache_key] = (allow, reason)
        if tool_use_id and chrome:  # 단계 줄의 ✅/⛔ 판정용 (문구 추측보다 확실)
            run.browser_verdicts[tool_use_id] = allow
        asyncio.create_task(run.emit("permission_decision", req_id=req_id, allow=allow,
                                     reason=reason, by=by))
        if allow:
            return {"behavior": "allow", "updatedInput": tool_input}
        return {"behavior": "deny", "message": f"[{by}] {reason}"}

    blocked = hard_rule(run.project, tool_name, tool_input)
    if blocked:
        return done(False, blocked, "hard-rule")
    if cache_key in run.perm_cache:
        allow, reason = run.perm_cache[cache_key]
        return done(allow, reason, "이전 판정 재사용")

    reviewers = run.settings["permission_reviewers"]
    if not reviewers:
        return done(False, "권한 심사 리뷰어가 없습니다", "설정", remember=False)
    votes = await asyncio.gather(*(judge_permission(r, run.spec, run.project, tool_name, tool_input,
                                                    label=label, note=run.last_text if chrome else "")
                                   for r in reviewers))
    await run.emit("permission_votes", req_id=req_id, votes=votes)
    result = _aggregate(votes, ex.get("permission_policy", "unanimous"))
    reasons = " / ".join(f"{v['reviewer']}: {v['reason']}" for v in votes)
    if result == "allow":
        return done(True, reasons, "AI 리뷰어")
    if result == "deny" or ex.get("on_disagreement") == "deny":
        return done(False, reasons, "AI 리뷰어")

    # 의견이 갈림 → 사람에게
    key = f"perm:{req_id}"
    await run.emit("awaiting_user", key=key, kind="permission", tool=tool_name, input=tool_input,
                   votes=votes, label=label)
    try:
        answer = await asyncio.wait_for(run.wait_for(key), ex.get("human_timeout_sec", 600))
    except asyncio.TimeoutError:
        run.waiters.pop(key, None)
        return done(False, "사람 응답 시간 초과", "timeout", remember=False)
    return done(bool(answer.get("allow")), answer.get("text") or "사용자 결정", "사용자")
