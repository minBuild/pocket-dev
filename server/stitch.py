"""Google Stitch (공식 MCP) — 기획안 승인 뒤, 구현 전에 화면 디자인을 먼저 뽑는다.

키: Stitch → 프로필 → Stitch settings → API key 에서 만들어 setup_stitch.bat 으로 .env 의 STITCH_API_KEY 에 저장.
화면 파일(HTML·스크린샷)은 Claude Code 가 셸로 내려받지 않고 주소만 screens.json 에 적으면 여기서 받는다.
(셸 다운로드는 매번 권한 심사를 거쳐야 하고, curl 을 통째로 허용하면 프로젝트 밖 쓰기·파일 유출 통로가 된다)
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import CONFIG

DOWNLOAD_HOSTS = {"contribution.usercontent.google.com", "lh3.googleusercontent.com"}  # get_screen 의 downloadUrl
MAX_FILE = 20 * 1024 * 1024
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")


def configured() -> bool:
    return bool(os.environ.get("STITCH_API_KEY"))


def _cfg(key: str, default):
    return (CONFIG.get("stitch") or {}).get(key, default)


def model() -> str:
    return _cfg("model", "claude-sonnet-5-5")


def mcp_servers() -> dict:
    """Claude Code --mcp-config 에 넣을 Stitch 서버 (HTTP, API 키 헤더)."""
    return {"stitch": {"type": "http", "url": _cfg("mcp_url", "https://stitch.googleapis.com/mcp"),
                       "headers": {"X-Goog-Api-Key": os.environ.get("STITCH_API_KEY", "")}}}


def design_prompt(project_name: str) -> str:
    max_screens = _cfg("max_screens", 6)
    return f"""루트의 TASK_SPEC.md 를 읽고, 사용자에게 보이는 화면을 Google Stitch MCP 도구(mcp__stitch__*)로 디자인해.
1) create_project 로 Stitch 프로젝트를 만들어 (제목: "{project_name}").
2) 명세서의 핵심 화면마다 generate_screen_from_text 로 화면을 만들어 (모바일 앱이면 deviceType MOBILE).
   화면은 핵심 위주로 최대 {max_screens}개. 같은 앱처럼 보이게 색·글꼴·톤을 맞춰서 요청해.
   생성은 몇 분 걸릴 수 있어. 실패하거나 시간이 초과돼도 다시 생성하지 말고 list_screens 로 만들어졌는지 확인해.
3) 각 화면을 get_screen 으로 조회해서 design/stitch/screens.json 에 이 형식으로 적어:
   [{{"slug": "home", "title": "홈", "html": "<htmlCode.downloadUrl>", "png": "<screenshot.downloadUrl>"}}]
   slug 는 영문 소문자-하이픈. 파일은 직접 내려받지 마 (curl 등 셸 다운로드 금지) — Pocket-Dev 가 이 목록으로
   design/stitch/<slug>.html · <slug>.png 를 저장한다.
4) design/stitch/README.md 에 화면 목록(slug·역할), Stitch 프로젝트 이름/ID, 색·글꼴·간격 같은 디자인 토큰을 정리해.
제약: 앱 코드 구현은 하지 마 (다음 단계에서 함). git commit/push 하지 마. 첨부 자료가 있으면 참고해."""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60, follow_redirects=False)


async def fetch_screens(project: str) -> list[str]:
    """design/stitch/screens.json 의 Stitch 다운로드 주소로 <slug>.html · <slug>.png 를 저장. 저장한 파일 이름 목록."""
    d = Path(project) / "design" / "stitch"
    listing = d / "screens.json"
    try:
        items = json.loads(listing.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    saved = []
    async with _client() as c:
        for it in items if isinstance(items, list) else []:
            slug = str(it.get("slug", "")) if isinstance(it, dict) else ""
            if not SLUG.fullmatch(slug):
                continue
            for key, ext in (("html", ".html"), ("png", ".png")):
                url = str(it.get(key) or "")
                u = urlparse(url)
                if u.scheme != "https" or u.hostname not in DOWNLOAD_HOSTS:
                    continue
                try:
                    r = await c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code == 200 and 0 < len(r.content) <= MAX_FILE:
                    (d / f"{slug}{ext}").write_bytes(r.content)
                    saved.append(f"{slug}{ext}")
    listing.unlink(missing_ok=True)  # 링크만 있으면 누구나 받을 수 있는 주소라 커밋하지 않는다
    return saved
