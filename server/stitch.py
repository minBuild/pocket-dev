"""Google Stitch (공식 MCP) — 기획안 승인 뒤, 구현 전에 화면 디자인을 먼저 뽑는다.

키: Stitch → 프로필 → Stitch settings → API key 에서 만들어 setup_stitch.bat 으로 .env 의 STITCH_API_KEY 에 저장.
"""
import os

from .config import CONFIG


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
2) 명세서의 핵심 화면마다 generate_screen_from_text 로 화면을 만들어 (모바일 앱이면 모바일 기기 타입).
   화면은 핵심 위주로 최대 {max_screens}개. 같은 앱처럼 보이게 색·글꼴·톤을 맞춰서 요청해.
3) 각 화면을 get_screen 으로 조회해서 HTML 코드와 스크린샷을 받아
   design/stitch/<화면이름>.html, design/stitch/<화면이름>.png 로 저장해 (화면이름은 영문 소문자-하이픈).
4) design/stitch/README.md 에 화면 목록(파일·역할), Stitch 프로젝트 이름/ID, 색·글꼴·간격 같은 디자인 토큰을 정리해.
제약: 앱 코드 구현은 하지 마 (다음 단계에서 함). git commit/push 하지 마. 첨부 자료가 있으면 참고해."""
