"""config.yaml + .env 로딩."""
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
