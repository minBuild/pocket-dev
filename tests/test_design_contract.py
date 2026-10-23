"""디자인 리뉴얼 회귀 방지 — 선택자·id·동작에 필요한 선언·금지 항목."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

# fb9b9f3:web/style.css 에서 selectors() 로 뽑은 리뉴얼 전 선택자 — 수정 금지, 손으로 늘리지 말 것
OLD_SELECTORS = {
    '#composer', '#composer .models-panel', '#composer .models-panel .actions', '#composer > *',
    '#composerInfo', '#composerInfo:not(:empty)', '#composerText.mono', '#runTitle', '#sessComposer',
    '#sessComposer > *', '#sessModelPick', '#sessStream .perm .actions', '#sessStream .perm .actions button',
    '#staleBanner', '*', '.actions', '.actions button', '.actions:not(:empty)', '.attach-btn',
    '.attach-btn.icon', '.attach-row', '.badge', '.bo-close', '.bo-head', '.bo-shot', '.bo-shot a',
    '.bo-shot a.gone', '.bo-shot figcaption', '.bo-shot img', '.bo-steps', '.bo-steps .bo-title', '.bo-strip',
    '.bo-strip > *', '.bo-todo', '.bo-todo .bo-title', '.bo-todo .todo-item', '.bo-todo .todo-item.completed',
    '.body pre', '.body table', '.body td', '.body th', '.browser-chip', '.browser-chip .bc-badge',
    '.browser-chip .bc-text', '.browser-chip .bc-thumb', '.browser-chip.blocked',
    '.browser-chip.blocked .bc-badge', '.browser-chip.decide .bc-badge', '.browser-chip.limit',
    '.browser-chip.limit .bc-badge', '.browser-chip.suspect', '.browser-chip.suspect .bc-badge',
    '.browser-chip.textonly .bc-badge', '.browser-overlay', '.bstep', '.bstep .shot', '.bstep.account',
    '.bstep.denied', '.bstep.error', '.bstep.ok', '.bstep.running', '.btn-link', '.card', '.card button + h2',
    '.card h2 + div + h2', '.cc', '.cc.tool.bstep::before', '.cc.tool::before', '.chat-action',
    '.chat-action.done', '.chip', '.chip button', '.composer-row', '.composer-row .term-toggle',
    '.composer-row button', '.composer-row textarea', '.composer-tools', '.composer-tools button',
    '.engine-fix', '.engine-fix .composer-row', '.engine-fix .fix-msg', '.engine-fix .fix-msg .btn-link',
    '.engine-fix pre', '.engine-status', '.error', '.file-chips', '.file-chips:not(:empty)', '.gate',
    '.gh-link', '.links', '.lock-banner', '.lock-banner .primary', '.log', '.login-card',
    '.login-card .primary', '.login-card h1', '.login-card label', '.login-card label input', '.member',
    '.member-row', '.member-row button', '.member-row input[type=checkbox]', '.member.e-claude',
    '.member.e-codex', '.member.e-gemini', '.member.off', '.model-pick select', '.model-pick select.effort',
    '.model-pick select:nth-child(2)', '.models-panel .actions', '.models-panel .member-row',
    '.models-panel .model-pick select.exec-model', '.models-panel .model-pick select:nth-child(2)',
    '.models-panel .model-pick select:nth-child(3)', '.models-panel .role', '.msg', '.msg .toggle',
    '.msg .tools', '.msg .tools:not(:empty)', '.msg .who', '.msg .who b', '.msg.claude', '.msg.codex',
    '.msg.collapsed .body', '.msg.failed', '.msg.gemini', '.msg.result', '.msg.spec', '.msg.user', '.muted',
    '.older-bar', '.perm', '.perm .no', '.perm .ok', '.perm .shot', '.perm pre', '.perm.compact',
    '.perm.compact .shot', '.perm.compact .votes', '.perm.compact details', '.presets', '.presets button',
    '.radio-group', '.radio-group input', '.radio-group label', '.radio-group legend', '.rename-dialog',
    '.rename-dialog .actions', '.rename-dialog .rename-hint', '.rename-dialog label',
    '.rename-dialog::backdrop', '.restarting', '.row', '.row input[type=checkbox]', '.row input[type=number]',
    '.run-chrome', '.run-chrome.blocked', '.run-del', '.run-del:not(:disabled):hover', '.run-edit',
    '.run-head', '.run-head > div', '.run-item', '.run-item .status', '.run-row', '.run-sub',
    '.run-sub .status', '.run-title', '.runs-meta', '.sess-browser', '.sess-browser input', '.shot',
    '.shot a', '.shot img', '.shot-ago', '.stage', '.stage::after', '.stage::before', '.status',
    '.status.cancelled', '.status.done', '.status.error', '.status.waiting', '.status.working', '.table-wrap',
    '.term', '.term .no', '.term .ok', '.term pre', '.term-chips', '.term-chips button', '.term-cmd',
    '.term-toggle.active', '.thread', '.thread > .msg:last-child', '.top', '.top nav',
    '.top nav button.active', '.top select', '.usage summary', '.usage summary .muted', '.usage-table',
    '.usage-table td', '.usage-table td:first-child', '.usage-table th', '.usage-table th:first-child',
    ':root', '[hidden]', 'body', 'button', 'button.danger', 'button.primary', 'button:disabled', 'code',
    'details summary', 'h2', 'h2 small', 'input', 'main', 'select', 'textarea',
}

FROZEN = [
    ("[hidden]", "display", "none !important"),
    (".top", "position", "sticky"),
    ("#composer, #sessComposer", "position", "fixed"),
    (".msg.collapsed .body", "max-height", "220px"),
    ("#composer .models-panel", "overflow-y", "auto"),
    ("#composer .models-panel .actions", "position", "sticky"),
    (".perm.compact .votes, .perm.compact details, .perm.compact .shot", "display", "none"),
    (".browser-overlay", "z-index", "20"),
    (".restarting", "z-index", "50"),
    (".composer-tools button", "min-height", "40px"),
    ("button", "min-height", "40px"),
    (".badge", "overflow-wrap", "anywhere"),          # A 의 ⚠️/🔎 열람 배지가 360px 에서 넘치지 않게
]


def rules(css):
    """주석 제거 → (@media 안쪽 포함) [(선택자, [(속성, 값)])]"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(" ".join(m[1].split()),
             [tuple(map(str.strip, d.split(":", 1))) for d in m[2].split(";") if ":" in d])
            for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css)]


def selectors(css):
    return {" ".join(s.split()) for sel, _ in rules(css) for s in sel.split(",") if s.strip()}


def test_old_selectors_kept():
    missing = OLD_SELECTORS - selectors(CSS)
    assert not missing, f"사라진 선택자: {sorted(missing)}"


def test_functional_declarations():
    rs = rules(CSS)
    for sel, p, v in FROZEN:
        assert any(s == sel and k == p and v in x for s, d in rs for k, x in d), f"{sel} {{{p}: {v}}} 사라짐"
    assert any("268px" in x for s, d in rs if s == "main" for k, x in d if k in ("padding", "padding-bottom"))


LIGHT = "html[data-theme=light]"
HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def tok(sel):
    return {k: v for s, d in rules(CSS) if s == sel for k, v in d}


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def _over(fg, alpha, bg):
    """fg 를 alpha 만큼 bg 위에 합성한 #RRGGBB (알약 배지의 틴트 배경)."""
    f, b = ([int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in (fg, bg))
    return "#" + "".join(f"{round(alpha * x + (1 - alpha) * y):02X}" for x, y in zip(f, b))


def _alpha(rgba):
    return float(re.search(r",\s*([\d.]+)\s*\)\s*$", rgba)[1])


def test_theme_token_parity():
    """판정은 JS 전담 — CSS 에 미디어쿼리 없음, :root 1개, 라이트 블록이 토큰을 빠짐없이 덮음."""
    assert "prefers-color-scheme" not in CSS
    assert len(re.findall(r"(?m)^\s*:root\s*\{", CSS)) == 1
    dark, light = tok(":root"), tok(LIGHT)
    assert light, f"{LIGHT} 블록 없음(선택자는 따옴표 없이)"
    want = {k for k in dark if k.startswith("--")} - {"--sans", "--mono"}
    got = {k for k in light if k.startswith("--")}
    assert got == want, f"빠짐 {sorted(want - got)} / 남음 {sorted(got - want)}"
    assert light.get("color-scheme") == "light", "네이티브 select·스크롤바·autofill 용"
    assert 'name="color-scheme" content="dark light"' in HTML
    assert HTML.index('name="theme-color"') < HTML.index("pdTheme") < HTML.index("/static/style.css"), \
        "인라인 테마 스크립트는 theme-color meta 뒤, style.css 앞"


def test_theme_contrast():
    """WCAG AA. 실패하면 임계값을 낮추지 말고 전경 토큰을 한 단계(700→800) 어둡게."""
    dark, light_only = tok(":root"), tok(LIGHT)
    assert light_only, f"{LIGHT} 블록 없음"
    fg = ("--text", "--muted", "--accent", "--ok", "--warn", "--danger", "--wait", "--code",
          "--claude", "--gemini", "--codex", "--b-indigo", "--b-ok", "--b-no", "--b-warn")
    for name, t in (("dark", dark), ("light", {**dark, **light_only})):
        pairs = [(f, b, 4.5) for f in fg for b in ("--bg", "--card")]
        pairs += [("--on-accent", "--accent", 4.5), ("--accent", "--raised", 4.5), ("--muted", "--panel", 4.5)]
        pairs += [(f, b, 3) for f in ("--faint", "--human") for b in ("--bg", "--card")]
        for f, b, need in pairs:
            assert HEX.fullmatch(t[f]) and HEX.fullmatch(t[b]), f"{name} {f}/{b}: #RRGGBB 6자리로"
            r = _ratio(t[f], t[b])
            assert r >= need, f"{name}: {f} on {b} = {r:.2f} < {need}"
        for f, a in (("--accent", _alpha(t["--accent-wash"])), ("--wait", _alpha(t["--wait-wash"])),
                     ("--b-warn", .12), ("--b-indigo", .12)):
            r = _ratio(t[f], _over(t[f], a, t["--card"]))
            assert r >= 4.5, f"{name}: {f} 알약(틴트 {a}) = {r:.2f}"


def test_document_scroll_untouched():
    bad = {"height", "overflow", "overflow-y", "overflow-x", "position"}
    for s, d in rules(CSS):
        if {x.strip() for x in s.split(",")} & {"html", "body", ":root"}:
            for k, v in d:
                assert k not in bad, f"{s} 에 {k} — 문서 스크롤(자동 스크롤·loadOlder)이 깨짐"
                assert not (k.startswith("overscroll-behavior") and "none" in v), "당겨서 새로고침이 죽음"
    assert "::-webkit-scrollbar" not in CSS


def test_viewport_and_no_awaiting_color():
    assert "user-scalable=no" not in HTML and "maximum-scale" not in HTML
    assert ".status.awaiting_user" not in CSS


def test_static_ids_exist_in_html():
    used = set(re.findall(r'\$\("#([\w-]*\w)["\s.:\[]', JS)) | set(re.findall(r'getElementById\("([\w-]+)"\)', JS))
    made = set(re.findall(r'\.id\s*=\s*"([\w-]+)"', JS)) | set(re.findall(r'\bid="([\w-]+)"', JS))
    html_ids = set(re.findall(r'\bid="([\w-]+)"', HTML))
    assert not (used - made - html_ids), f"index.html 에 없는 id: {sorted(used - made - html_ids)}"


def test_header_ids_for_repo_switch():
    """Part A — 레포 전환이 쓰는 헤더 요소가 마크업에 그대로 있다."""
    for i in ("project", "branchBadge", "ghLink"):
        assert HTML.count(f'id="{i}"') == 1, f"#{i} 없음/중복"
