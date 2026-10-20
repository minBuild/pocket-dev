"""화면에서 Claude Code 문제 해결 — 다시 확인 · 설치 · 로그인 (폰에서도).

로그인: `claude auth login` 을 띄워 로그인 주소를 받아 화면에 보여주고, 사용자가 그 주소에서 로그인한 뒤 받은
코드를 화면에 붙여 넣으면 프로세스 입력으로 넘긴다. 한 번에 하나만, 10분 지나면 정리한다.
"""
import asyncio
import re
import subprocess
import threading
import time
from typing import Optional

from . import engines

LOGIN_TIMEOUT = 600
URL_RE = re.compile(r"https://\S*oauth\S+")
INSTALL_CMD = "irm https://claude.ai/install.ps1 | iex"   # Anthropic 공식 Windows 설치 스크립트


def recheck() -> None:
    engines._status_cache.update(at=0, data=None)


class _Login:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.output: list[str] = []
        self.started = 0.0

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def text(self) -> str:
        return "".join(self.output)

    def _pump(self, proc: subprocess.Popen) -> None:
        for chunk in iter(lambda: proc.stdout.read1(4096), b""):
            self.output.append(chunk.decode("utf-8", "replace"))

    async def start(self) -> str:
        self.cancel()
        self.output, self.started = [], time.time()
        # 파이프 입력으로 코드를 넘겨야 해서 스레드로 읽는다 (asyncio 서브프로세스는 프롬프트를 줄 단위로 못 받음)
        self.proc = subprocess.Popen([engines.claude_bin(), "auth", "login", "--claudeai"],
                                     env=engines.subscription_env(), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        asyncio.get_running_loop().call_later(LOGIN_TIMEOUT, self._expire, self.proc)
        for _ in range(60):
            await asyncio.sleep(0.5)
            m = URL_RE.search(self.text())
            if m:
                return m.group(0)
            if not self.running:
                break
        out = self.text().strip()
        self.cancel()
        raise RuntimeError("로그인 주소를 받지 못했어요" + (f":\n{out[-1500:]}" if out else ""))

    async def submit(self, code: str) -> dict:
        if not self.running:
            raise RuntimeError("진행 중인 로그인이 없어요. '로그인'을 다시 눌러 주세요")
        before = len(self.text())
        self.proc.stdin.write(code.strip().encode() + b"\n")
        self.proc.stdin.flush()
        for _ in range(120):
            await asyncio.sleep(0.5)
            if not self.running:
                break
        out = self.text()[before:].strip()
        ok = self.proc.poll() == 0
        if self.running:
            self.cancel()
            out += "\n(응답이 없어 중단했어요)"
        self.proc = None
        recheck()
        return {"ok": ok, "output": out[-2000:]}

    def _expire(self, proc: subprocess.Popen) -> None:
        if self.proc is proc:
            self.cancel()

    def cancel(self) -> None:
        if self.running:
            self.proc.kill()
        self.proc = None


LOGIN = _Login()


async def install() -> tuple[int, str]:
    """공식 설치 스크립트 실행. 설치 위치(~/.local/bin)는 claude_bin() 이 PATH 와 상관없이 찾는다."""
    proc = await asyncio.create_subprocess_exec(
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "$ProgressPreference = 'SilentlyContinue'; " + INSTALL_CMD,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 600)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "10분 안에 끝나지 않아 중단했어요"
    recheck()
    return proc.returncode, out.decode("utf-8", "replace")[-4000:]
