"""창 없이 백그라운드로 Pocket-Dev 서버 실행 (작업 스케줄러 · start_hidden.bat 이 pythonw 로 띄움).

- 서버(python -m server.main)를 숨은 콘솔로 띄우고 출력은 data/server.log 에 쓴다.
- 화면의 '서버 재시작'(종료 코드 3)이면 바로 다시 켜고, 예상치 못하게 죽으면 5초 뒤 다시 켠다
  (2분 안에 5번 죽으면 포기 — 로그 확인).
- 한 번에 하나만 돈다 (data/supervisor.lock). 끄기: stop.bat (python -m server.supervisor stop)
"""
import json
import msvcrt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "server.log"
LOCK = DATA / "supervisor.lock"
PIDS = DATA / "server.pids"
RESTART_EXIT = 3
MAX_LOG = 5 * 1024 * 1024
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(text: str) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[supervisor {time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")


def _rotate() -> None:
    if LOG.exists() and LOG.stat().st_size > MAX_LOG:
        LOG.replace(LOG.with_suffix(".log.1"))


def run() -> None:
    DATA.mkdir(exist_ok=True)
    lock = LOCK.open("a+")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return  # 이미 돌고 있음
    env = {**os.environ, "POCKETDEV_SUPERVISED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    crashes: list[float] = []
    while True:
        _rotate()
        _log("서버 시작")
        with LOG.open("a", encoding="utf-8") as out:
            # CREATE_NO_WINDOW: 숨은 콘솔 — 서버가 띄우는 claude·git 같은 자식 프로세스도 창이 안 뜬다
            proc = subprocess.Popen([str(PYTHON), "-m", "server.main"], cwd=str(ROOT), env=env,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    creationflags=NO_WINDOW)
            PIDS.write_text(json.dumps({"supervisor": os.getpid(), "server": proc.pid}), encoding="utf-8")
            code = proc.wait()
        _log(f"서버 종료 (코드 {code})")
        if code == RESTART_EXIT:
            continue
        if code == 0:  # stop.bat 이나 정상 종료
            break
        now = time.time()
        crashes = [t for t in crashes if now - t < 120] + [now]
        if len(crashes) >= 5:
            _log("2분 안에 5번 죽어서 다시 켜지 않아요 — 위 오류를 확인하고 start_hidden.bat 으로 다시 켜세요")
            break
        time.sleep(5)
    PIDS.unlink(missing_ok=True)


def stop() -> None:
    """supervisor 와 서버를 끈다 (자식 프로세스까지)."""
    try:
        pids = json.loads(PIDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("백그라운드 서버가 돌고 있지 않아요")
        return
    for key in ("supervisor", "server"):  # supervisor 먼저 — 서버가 죽어도 다시 켜지 않게
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pids[key])], capture_output=True)
    PIDS.unlink(missing_ok=True)
    _log("stop 으로 종료")
    print("Pocket-Dev 백그라운드 서버를 껐어요")


if __name__ == "__main__":
    stop() if sys.argv[1:] == ["stop"] else run()
