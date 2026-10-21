"""저장소 루트를 sys.path 에 넣어 `from server import browser` 가 되게 한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
