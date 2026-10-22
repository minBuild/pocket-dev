"""⚙️ 모델 UI 는 작업 화면 입력창(#composer) 안, 입력줄 아래에 하나만 있어야 한다."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_models_ui_below_composer_input():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    a = html.index('<div id="composer"')
    depth = end = 0
    for m in re.finditer(r"<div\b|</div>", html[a:]):
        depth += -1 if m.group() == "</div>" else 1
        if not depth:
            end = a + m.end()
            break
    assert end, "#composer 닫는 태그를 못 찾음"
    for i in ("modelsBtn", "modelsPanel"):
        assert html.count(f'id="{i}"') == 1, f"{i}: 없거나 중복(옛 노드가 남음)"
        assert html.index('id="composerText"') < html.index(f'id="{i}"') < end, f"{i}: #composer 안·입력창 아래가 아님"
