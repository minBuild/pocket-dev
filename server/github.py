"""GitHub 저장소 목록(비공개 포함) · PC로 받아오기 — GitHub CLI(gh) 로그인 계정 사용."""
import json
import re
import time
from pathlib import Path
from typing import Optional

from . import executor
from .config import CONFIG, NEW_PROJECT_ROOT

_cache: dict = {"at": 0.0, "repos": None}


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
