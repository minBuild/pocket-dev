"""📦 출시 관리 탭 — 마크업·JS·CSS 계약 (문자열 검사)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "style.css").read_text(encoding="utf-8")


def _body(sig: str) -> str:
    i = JS.index(sig)
    return JS[i:JS.index("\n}\n", i)]


def test_nav_has_release_and_short_chat():
    nav = HTML[HTML.index("<nav>"):HTML.index("</nav>")]
    assert 'data-tab="release"' in nav
    assert "💬 Claude" not in nav and 'title="Claude 대화"' in nav


def test_static_ids():
    for i in "tab-release relMeta relBanner relTodo relList relClassify relSheet relLaunch".split():
        assert HTML.count(f'id="{i}"') == 1, i
    assert re.search(r'<dialog id="relSheet" class="rename-dialog rel-sheet">', HTML)


def test_show_wires_release():
    body = _body("function show(tab) {")
    assert body.count('"release"]') >= 2
    assert 'if (tab === "release") loadRelease();' in body
    assert 'if (tab === "release") scrollTo(0, 0);' in body


def test_load_release_keeps_scroll():
    body = _body("async function loadRelease() {")
    assert not re.search(r"scrollTo|scrollTop|scrollIntoView|scrollBy", body)


def test_project_bar_pick():
    assert "pick() {}" in JS[:2000]
    assert "projectBar.pick = async" in JS


def test_api_old_server_404():
    body = _body("async function api(")
    assert '"Not Found"' in body and "res.status === 404" in body


def test_presets():
    i = JS.index("const REL_PRESETS = [")
    block = JS[i:JS.index("];", i)]
    for pid in ("update", "check", "reject"):
        assert f'id: "{pid}"' in block
    for word in ("Info.plist", "pod install", "맥북에서 할 일"):
        assert word in block


def test_classify_labels():
    body = _body("function classifyMode() {")
    for label in ("라이브", "심사중", "개발", "보류"):
        assert f'"{label}"' in body


def test_start_badge_fetch():
    body = _body("async function startApp() {")
    assert 'api("/api/release").then(paintRelBadge).catch(() => {})' in body


def test_css_rules():
    assert re.search(r"\.top nav button \{[^}]*white-space: nowrap", CSS)
    for sel in (".rel-pill.danger", ".rel-pill.warn", ".rel-pill.ok", ".rel-pill.muted", ".rel-pill.unset", ".rel-on"):
        assert sel + " {" in CSS, sel
    assert re.search(r"\.rel-sheet \{[^}]*max-height", CSS)


def test_pull_css():
    assert ".rel-go, .rel-pull {" in CSS
    assert ".rel-note {" in CSS and ".rel-note.warn {" in CSS


def test_pull_js():
    for w in ("/api/release/pull", "rel-pull", "relPulling", "rel-note"):
        assert w in JS, w
    assert "a.view.pullable" in JS or "v.pullable" in JS
    assert "스캔이 끝나면 받을 수 있어요" in JS and "빨리 감기만" in JS
    body = _body("async function relPull(")
    assert "alert(" not in body and "++relGen" in body and "relFocus(" in body
    assert "relNote = null" in _body("function relLeave() {")


def test_user_data_escaped():
    """카드·시트에 넣는 사용자 데이터는 esc()/textContent 로만."""
    body = _body("function relCard(a) {")
    for field in ("a.next", "a.name", "run.title", "s.error", "relNote.text"):
        for m in re.finditer(r"\$\{([^}]*" + re.escape(field) + r"[^}]*)\}", body):
            assert "esc(" in m.group(1), m.group(0)
