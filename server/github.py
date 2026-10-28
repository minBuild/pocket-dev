"""GitHub 저장소 목록(비공개 포함) · PC로 받아오기 — GitHub CLI(gh) 로그인 계정 사용."""
import json
import re
import time
from pathlib import Path
from typing import Optional

from . import executor
from .config import CONFIG, NEW_PROJECT_ROOT, PROJECTS_ROOTS

_cache: dict = {"at": 0.0, "repos": None}
_heads_cache: dict = {"at": 0.0, "heads": None}
GH_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
HEADS_Q = ("query($owner:String!,$cursor:String){repositoryOwner(login:$owner){repositories(first:100,after:$cursor,"
           "ownerAffiliations:OWNER,orderBy:{field:PUSHED_AT,direction:DESC}){pageInfo{hasNextPage endCursor} "
           "nodes{name url isFork pushedAt primaryLanguage{name} defaultBranchRef{name target{oid}}}}}}")


def _owner() -> str:
    return CONFIG["git"].get("github_owner", "")


async def list_repos(force: bool = False) -> list[dict]:
    """[{name, private, archived, updated, url, description, default_branch}] — 최근 수정 순."""
    if not force and _cache["repos"] is not None and time.time() - _cache["at"] < 120:
        return _cache["repos"]
    gh, owner = executor.gh_bin(), _owner()
    if not gh or not owner:
        return []
    code, out = await executor.sh(
        [gh, "repo", "list", owner, "--limit", "300",
         "--json", "name,isPrivate,isArchived,updatedAt,url,description,defaultBranchRef"],
        str(NEW_PROJECT_ROOT), timeout=60)
    start, end = out.find("["), out.rfind("]")
    if code or start < 0:
        raise RuntimeError(f"GitHub 저장소 목록을 가져오지 못했어요: {out.strip()[-300:]}")
    repos = [{"name": r["name"], "private": r["isPrivate"], "archived": r["isArchived"],
              "updated": r["updatedAt"], "url": r["url"], "description": r.get("description") or "",
              "default_branch": (r.get("defaultBranchRef") or {}).get("name") or ""}
             for r in json.loads(out[start:end + 1])]
    repos.sort(key=lambda r: r["updated"], reverse=True)
    _cache.update(at=time.time(), repos=repos)
    return repos


async def clone(name: str) -> Path:
    """목록에 있는 저장소만 new_project_root 아래로 받아온다 (비공개 포함)."""
    repo = next((r for r in await list_repos() if r["name"].lower() == name.lower()), None)
    if not repo:
        raise ValueError(f"GitHub 목록에 '{name}' 저장소가 없어요")
    path = NEW_PROJECT_ROOT / repo["name"]
    if path.exists():
        raise FileExistsError(f"{path} 가 이미 있어요")
    code, out = await executor.sh([executor.gh_bin(), "repo", "clone", f"{_owner()}/{repo['name']}", str(path)],
                                  str(NEW_PROJECT_ROOT), timeout=900)
    if code:
        raise RuntimeError(f"받아오기 실패: {out.strip()[-500:]}")
    return path


async def web_url(cwd: str) -> Optional[str]:
    """origin 이 GitHub 이면 웹 주소 (https://github.com/owner/repo)."""
    code, url = await executor.sh(["git", "remote", "get-url", "origin"], cwd)
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url.strip()) if code == 0 else None
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def projects() -> list[Path]:
    found: list[Path] = []
    for root in PROJECTS_ROOTS:
        if root.exists():
            found += [p for p in sorted(root.iterdir()) if (p / ".git").exists()]
    return found


def repo_key(origin_url: Optional[str], path) -> str:
    """📦 장부 키 — GitHub origin 이면 owner/name, 아니면 local:<parent>/<name> (모두 소문자)."""
    m = GH_RE.search((origin_url or "").strip())
    if m:
        return f"{m.group(1)}/{m.group(2)}".lower()
    p = Path(path)
    return f"local:{p.parent.name}/{p.name}".lower()


async def local_index() -> dict[str, dict]:
    """{key: {path, origin, owner}} — 같은 저장소가 두 루트에 있으면 PROJECTS_ROOTS 앞쪽이 이김."""
    out: dict[str, dict] = {}
    for p in projects():
        try:
            code, url = await executor.sh(["git", "--no-optional-locks", "remote", "get-url", "origin"], str(p), timeout=15)
        except (RuntimeError, OSError):
            code, url = 1, ""
        origin = url.strip().splitlines()[-1].strip() if code == 0 and url.strip() else None
        m = GH_RE.search(origin or "")
        out.setdefault(repo_key(origin, p), {"path": str(p), "origin": origin, "owner": m.group(1) if m else None})
    return out


def parse_heads(data: dict) -> tuple[list[dict], dict]:
    """GraphQL 응답 1페이지 → ([{name, url, fork, pushed, language, default_branch, oid}], pageInfo)."""
    repos = ((((data or {}).get("data") or {}).get("repositoryOwner") or {}).get("repositories") or {})
    out = []
    for n in repos.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        ref = n.get("defaultBranchRef") or {}
        out.append({"name": n["name"], "url": n.get("url") or "", "fork": bool(n.get("isFork")),
                    "pushed": n.get("pushedAt"), "language": (n.get("primaryLanguage") or {}).get("name"),
                    "default_branch": ref.get("name") or "", "oid": (ref.get("target") or {}).get("oid")})
    return out, repos.get("pageInfo") or {}


async def repo_heads(force: bool = False) -> tuple[list[dict], bool]:
    """GitHub 저장소의 기본 브랜치 끝 sha — gh api graphql 한 번(최대 3페이지). .git 에 아무것도 쓰지 않음. 120초 캐시."""
    if not force and _heads_cache["heads"] is not None and time.time() - _heads_cache["at"] < 120:
        return _heads_cache["heads"], True
    gh, owner = executor.gh_bin(), _owner()
    if not gh or not owner:
        return [], False
    heads: list[dict] = []
    cursor = None
    try:
        for _ in range(3):
            args = [gh, "api", "graphql", "-f", f"query={HEADS_Q}", "-F", f"owner={owner}"]
            if cursor:
                args += ["-F", f"cursor={cursor}"]
            code, out = await executor.sh(args, str(NEW_PROJECT_ROOT), timeout=60)
            start = out.find("{")
            if code or start < 0:
                return [], False
            page, info = parse_heads(json.loads(out[start:out.rfind("}") + 1]))
            heads += page
            if not info.get("hasNextPage") or not info.get("endCursor"):
                break
            cursor = info["endCursor"]
    except (RuntimeError, OSError, ValueError):
        return [], False
    if not heads:
        return [], False
    _heads_cache.update(at=time.time(), heads=heads)
    return heads, True
