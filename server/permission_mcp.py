"""Claude Code 가 --permission-prompt-tool 로 호출하는 stdio MCP 서버.

권한 요청을 오케스트레이터(/api/internal/permission)로 넘기고, 리뷰어 판정을 그대로 돌려준다.
환경변수: POCKETDEV_URL, POCKETDEV_RUN_ID, POCKETDEV_TOKEN
"""
import json
import os

import httpx
try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

mcp = MCPServer("pocketdev")


@mcp.tool(structured_output=False)  # Claude Code 는 text 블록 하나만 받음
async def approve(tool_name: str, input: dict, tool_use_id: str | None = None) -> str:
    """Claude Code 권한 요청을 AI 리뷰어 팀이 심사한다."""
    token = os.environ.get("POCKETDEV_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            res = await client.post(
                f"{os.environ['POCKETDEV_URL']}/api/internal/permission",
                headers={"Authorization": f"Bearer {token}"} if token else {},
                json={"run_id": os.environ["POCKETDEV_RUN_ID"], "tool_name": tool_name, "input": input,
                      "tool_use_id": tool_use_id},
            )
            res.raise_for_status()
            return json.dumps(res.json(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"behavior": "deny", "message": f"권한 심사 서버 오류: {e}"}, ensure_ascii=False)


@mcp.tool(structured_output=False)
async def chrome_account(email: str) -> str:
    """Chrome 콘솔 페이지에서 텍스트로 읽은 로그인 계정 이메일을 보고하고, 이 콘솔에서 쓰기 작업을 해도 되는지 답을 받는다."""
    token = os.environ.get("POCKETDEV_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            res = await client.post(
                f"{os.environ['POCKETDEV_URL']}/api/internal/chrome_account",
                headers={"Authorization": f"Bearer {token}"} if token else {},
                json={"run_id": os.environ["POCKETDEV_RUN_ID"], "email": email},
            )
            res.raise_for_status()
            return res.json()["text"]
    except Exception as e:
        return f"계정 보고 실패: {e} — 쓰기 작업을 하지 말고 '[CHROME-BLOCKED] 로그인 계정 확인 불가' 를 남기세요"


if __name__ == "__main__":
    mcp.run()
