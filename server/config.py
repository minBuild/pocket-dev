"""config.yaml + .env 로딩."""
import hashlib
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG: dict = yaml.safe_load(f)

ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
_roots = CONFIG.get("projects_roots") or CONFIG.get("projects_root") or "~/projects"
PROJECTS_ROOTS = [Path(os.path.expanduser(r)) for r in (_roots if isinstance(_roots, list) else [_roots])]
NEW_PROJECT_ROOT = Path(os.path.expanduser(CONFIG["git"].get("new_project_root") or str(PROJECTS_ROOTS[0])))
if NEW_PROJECT_ROOT not in PROJECTS_ROOTS:
    PROJECTS_ROOTS.append(NEW_PROJECT_ROOT)


def chrome_accounts_setting():
    """Chrome 허용 계정 원본 값 — .env CHROME_ALLOWED_ACCOUNTS 가 있으면 우선 (쉼표 구분), 없으면 config.yaml."""
    return os.environ.get("CHROME_ALLOWED_ACCOUNTS", "").strip() or (CONFIG.get("browser") or {}).get("allowed_chrome_accounts")


def code_sig(files=None) -> str | None:
    """서버가 import 할 때 읽는 파일(server/*.py · config.yaml · .env)의 서명 — 바뀌면 🔄 재시작이 필요. 절대 raise 하지 않음."""
    try:
        fs = files if files is not None else [*(ROOT / "server").glob("*.py"), ROOT / "config.yaml", ROOT / ".env"]
        h = hashlib.sha1()
        for p in sorted(fs):
            h.update(p.name.encode() + b"\0" + (p.read_bytes() if p.exists() else b"-"))
        return h.hexdigest()
    except Exception:
        return None
