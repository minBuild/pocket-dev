"""Claude in Chrome — 구현 AI 가 이 PC 의 Chrome(로그인된 상태)으로 콘솔 작업(AdMob 앱 등록·광고 단위 ID 발급 등)을 하게 한다.

작업마다 켜는 옵션 (run.settings["browser"]). Chrome 에 Claude 확장이 설치·로그인돼 있어야 한다.
읽기 전용 도구(탭 목록·페이지 읽기·찾기)만 묻지 않고 허용하고, 클릭·입력·이동·스크립트·업로드 같은
실제로 무언가를 하는 도구는 다른 도구와 똑같이 권한 심사(리뷰어 AI → 의견이 갈리면 폰으로 질문)를 거친다.

작업 화면 중계용 함수(describe·parse_result·summarize …)는 서버 모듈을 import 하지 않는 순수 함수다 (stdlib 만).
파일 저장과 emit 은 executor 가 한다.
"""
import base64
import re
from urllib.parse import urlsplit, parse_qs

READ_ONLY = [f"mcp__claude-in-chrome__{t}" for t in (
    "tabs_context_mcp", "read_page", "find", "get_page_text", "list_connected_browsers")]

PREFIX = "mcp__claude-in-chrome__"
AUTO_ALLOW_ACTIONS = {"screenshot", "zoom", "wait", "scroll", "scroll_to"}
REMEMBER_TOOLS = {PREFIX + "navigate", PREFIX + "tabs_create_mcp"}
HARD_DENY_TOOLS = {PREFIX + t for t in ("javascript_tool", "file_upload", "upload_image")}  # 실제 이름은 T1 프로브 때 도구 목록으로 확인
BLOCK_MARK = "[CHROME-BLOCKED]"
SHOT_RE = re.compile(r"^\d{13}-[A-Za-z0-9]{1,8}\.(png|jpe?g|webp)$")
DENY_PREFIX_RE = re.compile(r"^\s*\[(hard-rule|AI 리뷰어|사용자|timeout|설정|이전 판정 재사용|계정 확인)\]")
ACCOUNT_MARK = "[CHROME-ACCOUNT]"
ACCOUNT_TOOL = "mcp__pocketdev__chrome_account"
GOOGLE_SUFFIXES = ("google.com", "admob.com")
NON_WRITE_TOOLS = {PREFIX + t for t in ("navigate", "tabs_create_mcp", "tabs_context_mcp")}
EVIDENCE_TOOLS = {PREFIX + t for t in ("read_page", "get_page_text", "find")}   # 계정 근거로 인정하는 결과 (E2b)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:(?!\d)")   # about: chrome: javascript: … (host:port 는 제외)
_EMAIL_FULL_RE = re.compile(r"^[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+$")

PLAN_NOTE = ("\n\n[구현 환경] 이번 작업의 구현 AI 는 이 PC 의 Chrome(사용자가 로그인한 상태)을 조작할 수 있다 "
             "(클릭·입력은 하나하나 권한 심사를 거침). AdMob·Firebase 같은 웹 콘솔에서 앱 등록, 광고 단위 생성, "
             "ID 확인처럼 사람이 하던 설정 작업도 구현 단계에 넣을 수 있다. "
             "결제·요금제·계정 권한 변경, 기존 항목 삭제, 게시·심사 제출은 하지 않는다.")

EXEC_NOTE = """

[Chrome 사용 가능] 이 PC 의 Chrome 을 claude-in-chrome 도구로 조작할 수 있다 (사용자가 로그인해 둔 상태).
클릭·입력·이동은 하나하나 권한 심사를 거치니, 각 동작이 무엇을 위한 것인지 분명하게 하고 꼭 필요한 동작만 한다.
명세서에 있는 웹 콘솔 작업(예: AdMob 앱 추가·광고 단위 생성·앱 ID/광고 단위 ID 확인)만 한다.
- 시작할 때 tabs_context_mcp 로 연결을 확인하고, 새 탭을 만들어서 작업한다. 사용자가 열어 둔 탭은 건드리지 않는다.
- 만들기 전에 같은 이름의 앱·광고 단위가 이미 있는지 먼저 확인하고, 있으면 새로 만들지 말고 그 ID 를 쓴다 (ID 를 읽기만 할 때도 먼저 계정을 보고한다).
- 로그인 계정 확인 (필수): Google 계정을 쓰는 콘솔(AdMob·Firebase·Play Console·Google Cloud 등)에 들어가면 클릭·입력이나 ID 읽기 전에
  그 페이지에서 find/read_page/get_page_text 로 화면 오른쪽 위 계정 버튼(aria-label "Google 계정: 이름 (이메일)")의 이메일을 텍스트로 읽고,
  곧바로 mcp__pocketdev__chrome_account 도구에 그 이메일을 보고한다. 탭 목록·스크린샷을 근거로 보고하지 말고, 이메일을 짐작하거나
  다른 곳(세션 정보·명세서·이전 콘솔)에서 가져오지 않는다. 다른 콘솔(주소)로 옮기면 그 페이지에서 다시 읽어 보고한다.
  주소는 navigate 로 연다 (링크 클릭으로 다른 콘솔로 넘어가지 않는다).
- 보고 전의 클릭·입력은 서버가 거부한다. 도구 답이 '허용' 이 아니면(허용 목록에 없음·확인 불가) 그 콘솔에서 쓰기 작업을 하지 말고,
  그 콘솔의 ID 를 코드·설정에 반영하지 말고, "[CHROME-BLOCKED] 계정 불일치: <이메일>" 또는 "[CHROME-BLOCKED] 로그인 계정 확인 불가"
  한 줄을 남긴 뒤 Chrome 작업을 멈춘다 (코드 작업은 계속). 계정 전환·로그아웃·계정 추가는 하지 않는다.
- 금지: 결제·요금제·지급 정보, 계정·사용자 권한 설정, 기존 앱·광고 단위·프로젝트 삭제나 비활성화, 약관 동의,
  앱 게시·심사 제출, 로컬 파일 업로드. 이런 단계가 필요하면 멈추고 "[CHROME-BLOCKED] <무엇이 필요한지>" 한 줄을 남긴다.
- 로그인·2단계 인증·캡차가 나오면 우회하지 말고 멈춰서 "[CHROME-BLOCKED] 사용자가 Chrome 에서 로그인해야 함" 한 줄을 남긴다.
- 같은 동작이 2번 거부되거나 실패하면 반복하지 말고, "[CHROME-BLOCKED] <이유>" 한 줄을 남긴 뒤 Chrome 작업을 멈춘다 (코드 작업은 계속한다).
- computer 도구의 screenshot·zoom·wait·scroll 은 심사 없이 허용된다.
- JavaScript 실행 도구와 파일 업로드 도구는 쓸 수 없다 (즉시 거부). ID 는 get_page_text·find 로 읽는다.
- 발급받은 ID 는 코드·설정 파일에 반영하고, 작업 끝에 무엇을 만들었고 어떤 ID 를 어디에 넣었는지 정리한다.
  단 프로젝트 규칙(CLAUDE.md·명세서)이 실제 ID 를 저장소에 넣지 말라고 하면 그 방식(--dart-define 등)을 따르고, ID 는 결과 정리에만 남긴다.
  (AdMob 앱 ID 는 ca-app-pub-…~…, 광고 단위 ID 는 ca-app-pub-…/… 형식. 테스트 빌드에 테스트 광고 ID 를 쓰는 구조면 그대로 지킨다.)
- Chrome 연결이 안 되면 브라우저 작업은 건너뛰고 코드 작업만 끝낸 뒤, "[CHROME-BLOCKED] Chrome 연결 안 됨" 한 줄을 남긴다."""

SHOT_POLICIES = ("key", "minimal")
_SHOT_MINIMAL = ("\n- 스크린샷은 막혔을 때와 Chrome 작업을 마치기 직전에 1장씩만 찍고, 동작마다 찍지 않는다.")
_SHOT_KEY = ("\n- 사용자가 폰에서 Chrome 진행 화면을 본다. 스크린샷은 ① 새 콘솔 페이지로 navigate 한 직후 "
             "② 앱·광고 단위 등을 만들거나 저장하는 버튼을 누른 직후 ③ 막혔을 때 ④ Chrome 작업을 마치기 직전에만 1장씩 찍는다. "
             "그 밖의 클릭·입력·스크롤 뒤에는 찍지 않고, 이번 실행에서 {n}장을 넘기지 않는다.")


def shot_policy(value) -> str:
    """설정값 → 'key' | 'minimal' (모르는 값은 minimal — 토큰을 덜 쓰는 쪽)."""
    v = str(value or "").strip().lower()
    return v if v in SHOT_POLICIES else "minimal"


def clamp_max_shots(value, default: int = 12) -> int:
    try:
        return max(1, min(30, int(value)))
    except Exception:
        return default


def text_only_seen(events) -> bool:
    """이 작업에서 screenshot 이 성공했는데 이미지가 없었던 적이 있는가 (이미지가 전달되지 않는 환경)."""
    try:
        return any(isinstance(e, dict) and e.get("type") == "browser" and e.get("no_image") for e in (events or []))
    except Exception:
        return False


def exec_note(policy, max_shots, text_only: bool) -> str:
    """구현 프롬프트에 붙일 Chrome 규칙 전체 (EXEC_NOTE + 스크린샷 정책 한 줄)."""
    p = "minimal" if text_only else shot_policy(policy)
    return EXEC_NOTE + (_SHOT_KEY.format(n=clamp_max_shots(max_shots)) if p == "key" else _SHOT_MINIMAL)


def enabled(settings: dict) -> bool:
    return bool(settings.get("browser"))


def plan_note(settings: dict) -> str:
    return PLAN_NOTE if enabled(settings) else ""


# ── 작업 화면 중계 (순수 함수, 예외를 던지지 않음) ──

def is_tool(name) -> bool:
    return str(name).startswith(PREFIX)


def short(name) -> str:
    name = str(name)
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def auto_allow(name, inp) -> bool:
    """스크린샷·확대·대기·스크롤은 심사 없이 허용 (아무것도 바꾸지 않음)."""
    return name == PREFIX + "computer" and isinstance(inp or {}, dict) and (inp or {}).get("action") in AUTO_ALLOW_ACTIONS


def rememberable(name) -> bool:
    """같은 입력이면 이전 판정을 재사용해도 되는 도구 — 클릭 좌표는 페이지마다 뜻이 달라서 안 됨."""
    return not is_tool(name) or name in REMEMBER_TOOLS


# ── 로그인 계정 확인 (순수 함수, 예외를 던지지 않음) ──

def is_evidence(name) -> bool:
    """계정 근거로 인정하는 읽기 도구인가 (탭 목록·스크린샷·navigate 결과는 제외)."""
    return name in EVIDENCE_TOOLS


def norm_email(s) -> str | None:
    try:
        e = str(s or "").strip().strip(" \t\r\n<>()[]\"'.,;:").lower()
    except Exception:
        return None
    return e if len(e) <= 254 and _EMAIL_FULL_RE.match(e) else None


def emails_in(text) -> set[str]:
    out: set[str] = set()
    try:
        for m in _EMAIL_RE.findall(str(text)[:200_000]):
            e = norm_email(m)
            if e:
                out.add(e)
                if len(out) >= 50:
                    break
    except Exception:
        pass
    return out


def allowed_list(value) -> list[str]:
    """허용 계정 설정값(문자열·리스트) → 정규화된 이메일 목록 (순서 유지, 중복 제거)."""
    if isinstance(value, str):
        items = re.split(r"[,;\s]+", value)
    elif isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        return []
    out: list[str] = []
    for it in items:
        e = norm_email(it)
        if e and e not in out:
            out.append(e)
    return out


def account_allowed(email, allowed) -> bool:
    e = norm_email(email)
    return bool(e) and e in {norm_email(a) for a in (allowed or [])}


def scope_of(url) -> str | None:
    """URL → 계정 확인 범위. Google 호스트면 "host#authuser", localhost·비-Google 은 "", 모르면 None."""
    try:
        s = str(url or "").strip()
        if not s or s.lower() in ("back", "forward"):
            return None
        if "://" not in s:
            if _SCHEME_RE.match(s):
                return None
            s = "https://" + s.lstrip("/")
        parts = urlsplit(s)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return None
        host = parts.hostname.lower().rstrip(".")
        if (host == "localhost" or host.endswith(".localhost") or host.startswith("127.")
                or host in ("::1", "0.0.0.0")):
            return ""
        if "." not in host:
            return None
        if not any(host == sfx or host.endswith("." + sfx) for sfx in GOOGLE_SUFFIXES):
            return ""
        q = parse_qs(parts.query).get("authuser")
        if q and q[0].strip():
            authuser = q[0].strip().lower()[:64]
        else:
            m = re.search(r"/u/(\d+)(/|$)", parts.path)
            authuser = m.group(1) if m else "0"
        return f"{host}#{authuser}"
    except Exception:
        return None


def scope_after(name, inp, current) -> str | None:
    """허용된 도구 호출 뒤의 범위."""
    inp = inp if isinstance(inp, dict) else {}
    if name == PREFIX + "navigate":
        return scope_of(inp.get("url"))
    if name == PREFIX + "tabs_create_mcp":
        return scope_of(inp["url"]) if inp.get("url") else None
    return current


def is_write(name, inp) -> bool:
    """읽기·이동·자동 허용을 뺀 Chrome 도구 (모르는 도구도 쓰기로 봄)."""
    return (is_tool(name) and name not in READ_ONLY and name not in NON_WRITE_TOOLS
            and not auto_allow(name, inp))


def account_gate(name, inp, scope, account, allowed) -> str | None:
    """Google 콘솔 쓰기 동작을 막아야 하면 사유. account = (email, scope, ok) | None"""
    if not allowed or not is_write(name, inp) or scope == "":
        return None
    if scope is None:
        return "어느 페이지인지 확인되지 않아 쓰기 동작을 막았어요 — navigate 로 콘솔 주소를 연 뒤 로그인 계정을 chrome_account 로 보고하세요"
    host = scope.split("#")[0]
    if not account or account[1] != scope:
        return (f"{host} 의 로그인 계정이 확인되지 않았어요 — 이 페이지에서 find/read_page/get_page_text 로 계정 버튼의 이메일을 "
                "텍스트로 읽고 chrome_account 도구로 보고한 뒤 다시 시도하세요")
    if not account[2]:
        return (f"로그인 계정 {account[0]} 은(는) 허용 목록에 없어요 — 쓰기 작업·ID 코드 반영 금지, "
                "'[CHROME-BLOCKED] 계정 불일치' 한 줄을 남기고 Chrome 작업을 멈추세요")
    return None


def account_reply(email, seen, ok, gate) -> str:
    """chrome_account 도구의 응답 — 허용 목록의 다른 이메일은 절대 넣지 않는다."""
    if not email:
        return ("이메일 형식이 아니에요. 계정 버튼의 이메일을 텍스트로 읽어 다시 보고하세요. "
                "읽을 수 없으면 '[CHROME-BLOCKED] 로그인 계정 확인 불가' 를 남기고 Chrome 작업을 멈추세요.")
    if not seen:
        return (f"{email} 은(는) 지금 콘솔 페이지에서 Chrome 으로 읽은 내용에 없어요. 지금 페이지에서 "
                "find/read_page/get_page_text 로 계정 버튼(aria-label 'Google 계정: 이름 (이메일)')의 이메일을 읽은 뒤 "
                "다시 보고하세요. 다른 콘솔에서 읽었거나 짐작한 이메일은 보고하지 마세요. "
                "읽을 수 없으면 '[CHROME-BLOCKED] 로그인 계정 확인 불가' 를 남기고 멈추세요.")
    if not gate:
        return f"로그인 계정 {email} 확인 (허용 목록 검사 꺼짐). 명세의 작업을 진행하세요."
    if ok:
        return (f"허용된 계정이에요 ({email}). 이 콘솔에서 명세의 작업을 진행하세요. "
                "다른 콘솔로 옮기면 그 페이지에서 다시 읽어 보고하세요.")
    return (f"{email} 은(는) 허용 목록에 없는 계정이에요. 이 콘솔에서 클릭·입력하지 말고, "
            "이 콘솔의 ID 를 코드·설정에 반영하지 말고(이미 있는 ID 포함), "
            f"'[CHROME-BLOCKED] 계정 불일치: {email}' 한 줄을 남긴 뒤 Chrome 작업을 멈추세요. "
            "계정 전환·로그아웃은 하지 마세요.")


def _cut(s, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def describe(block) -> dict | None:
    """Chrome tool_use → {"icon", "label", "detail", "action", "text"}. 화면에 나가는 문자열은 text 하나뿐."""
    try:
        if not isinstance(block, dict) or not is_tool(block.get("name")):
            return None
        tool = short(block["name"])
        inp = block.get("input")
        inp = inp if isinstance(inp, dict) else {}
        action = str(inp.get("action", "") or "")
        icon, label, detail = "🌐", tool, ""
        if tool == "navigate":
            label, detail = "이동", re.split(r"[?#]", str(inp.get("url", "") or ""), maxsplit=1)[0]
        elif tool == "computer":
            if action in ("left_click", "double_click", "right_click", "triple_click"):
                icon, label = "🖱", "클릭"
                coord = inp.get("coordinate")
                if inp.get("ref"):
                    detail = str(inp["ref"])
                elif isinstance(coord, (list, tuple)) and len(coord) >= 2:
                    detail = f"({coord[0]}, {coord[1]})"
            elif action == "type":
                icon, label = "⌨️", f"입력 ({len(str(inp.get('text', '') or ''))}자)"  # 입력 본문은 표시하지 않음
            elif action == "key":
                icon, label, detail = "⌨️", "키 입력", str(inp.get("text", inp.get("key", "")) or "")[:30]
            elif action == "screenshot":
                icon, label = "📷", "화면 확인"
            elif action == "zoom":
                icon, label = "🔍", "확대"
            elif action in ("scroll", "scroll_to"):
                icon, label = "↕️", "스크롤"
            elif action == "wait":
                icon, label = "⏱", "대기"
            else:
                label = f"computer.{action}"
        elif tool == "form_input":
            icon, label = "📝", f"폼 입력 ({len(str(inp.get('value', '')))}자)"  # 값은 표시하지 않음
        elif tool == "tabs_create_mcp":
            icon, label = "🗂", "새 탭"
        elif tool == "tabs_context_mcp":
            icon, label = "🔌", "연결 확인"
        elif tool in ("read_page", "get_page_text", "find"):
            icon, label = "👀", "페이지 읽기"
            if tool == "find":
                detail = str(inp.get("query", "") or "")[:60]
        detail = _cut(detail, 80) if detail else ""
        return {"icon": icon, "label": label, "detail": detail, "action": action,
                "text": " ".join(x for x in (icon, label, detail) if x)}
    except Exception:
        return None


def _image(b) -> tuple[str, str] | None:
    if isinstance(b, dict) and b.get("type") == "image" and isinstance(b.get("source"), dict):
        src = b["source"]
        if isinstance(src.get("data"), str):
            return str(src.get("media_type", "")), src["data"]
    return None


def _top_images(top) -> list[tuple[str, str]]:
    """stream-json 줄 최상위 tool_use_result 안의 image 블록 (중첩은 1단계까지)."""
    found = []
    for item in (top if isinstance(top, list) else [top] if isinstance(top, dict) else []):
        img = _image(item)
        if img:
            found.append(img)
        elif isinstance(item, dict):
            for v in item.values():
                if isinstance(v, list):
                    found += [i for i in map(_image, v) if i]
    return found


def parse_result(block, verdict, top=None) -> tuple[str, list[tuple[str, str]], str]:
    """tool_result → (status, [(media_type, base64)], text). status: ok | denied | error"""
    try:
        is_error = bool(block.get("is_error")) if isinstance(block, dict) else False
    except Exception:
        is_error = False
    try:
        content = block.get("content")
        texts, images = [], []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(str(b.get("text", "")))
                elif img := _image(b):
                    images.append(img)
        elif content is not None:
            raise ValueError("unknown content")
        for img in _top_images(top):
            if img not in images:
                images.append(img)
        text = "".join(texts)
        if verdict is False:
            status = "denied"
        elif is_error and DENY_PREFIX_RE.match(text):
            status = "denied"
        elif is_error:
            status = "error"
        else:
            status = "ok"
        return status, images, text
    except Exception:
        return ("denied" if verdict is False else "error" if is_error else "ok"), [], ""


def sniff(data: bytes) -> str | None:
    """이미지 형식을 내용으로 판별 — PNG·JPEG·WebP 만 (SVG 등은 None)."""
    if not isinstance(data, (bytes, bytearray)):
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def shot_name(ms: int, tool_use_id: str, ext: str) -> str:
    return f"{ms:013d}-{(re.sub(r'[^A-Za-z0-9]', '', tool_use_id or '')[-6:] or 'x')}.{ext}"


def _sniff_b64(data) -> str | None:
    try:
        return sniff(base64.b64decode(str(data)[:24]))
    except Exception:
        return None


def _block_shape(b) -> dict:
    if not isinstance(b, dict):
        return {"type": type(b).__name__, "len": len(b) if isinstance(b, str) else None}
    shape = {"type": str(b.get("type", ""))}
    if isinstance(b.get("text"), str):
        shape["len"] = len(b["text"])
    src = b.get("source")
    if isinstance(src, dict):
        shape["media_type"] = str(src.get("media_type", ""))[:40]
        data = src.get("data")
        shape["len"] = len(data) if isinstance(data, str) else None
        shape["sniff"] = _sniff_b64(data) if isinstance(data, str) else None
    shape.setdefault("len", None)
    return shape


def structure(name, block, top, verdict_known) -> dict:
    """디버그 덤프용 구조 (타입·길이·키). 본문과 base64 는 한 글자도 넣지 않는다."""
    try:
        content = block.get("content") if isinstance(block, dict) else None
        kind = "str" if isinstance(content, str) else "list" if isinstance(content, list) else "other"
        blocks = ([{"type": "text", "len": len(content)}] if kind == "str"
                  else [_block_shape(b) for b in content] if kind == "list" else [])
        if isinstance(top, dict):
            top_keys = sorted(str(k)[:40] for k in top)
        elif isinstance(top, list):
            top_keys = [f"[{i}]:{_block_shape(x)['type']}" for i, x in enumerate(top[:20])]
        else:
            top_keys = []
        return {"tool": short(name), "is_error": bool(isinstance(block, dict) and block.get("is_error")),
                "verdict_known": bool(verdict_known), "content_kind": kind, "blocks": blocks, "top_keys": top_keys}
    except Exception:
        return {"tool": short(name), "is_error": None, "verdict_known": bool(verdict_known),
                "content_kind": "other", "blocks": [], "top_keys": []}


def blocked_reason(text) -> str | None:
    """AI 텍스트의 [CHROME-BLOCKED] 마커 → 이유 (없으면 None)."""
    try:
        for ln in str(text or "").splitlines():
            i = ln.find(BLOCK_MARK)
            if i >= 0:
                return ln[i + len(BLOCK_MARK):].strip()[:200] or "이유 없음"
    except Exception:
        pass
    return None


def summarize(events) -> dict:
    """마지막 result 이벤트 이후의 Chrome 작업 집계 (결과 카드용)."""
    sm = {"used": False, "steps": 0, "ok": 0, "denied": 0, "error": 0, "stopped": 0, "shots": 0, "blocked": None,
          "account": None, "report": None}
    try:
        evs = list(events or [])
        last = max((i for i, e in enumerate(evs) if isinstance(e, dict) and e.get("type") == "result"), default=-1)
        for e in evs[last + 1:]:
            if not isinstance(e, dict):
                continue
            if e.get("type") == "browser":
                sm["steps"] += 1
                st = e.get("status")
                if st in ("ok", "denied", "error", "stopped"):
                    sm[st] += 1
                if e.get("shot"):
                    sm["shots"] += 1
                if st == "ok" and e.get("write", True):  # 마커 뒤 screenshot 같은 읽기 단계는 막힘을 풀지 않음
                    sm["blocked"] = None
            elif e.get("type") == "browser_account":
                acc = {k: e.get(k) for k in ("email", "allowed", "verified", "gate", "scope")}
                sm["account"] = acc
                if e.get("email"):
                    sm["report"] = acc
            elif e.get("type") == "cc" and e.get("text"):
                reason = blocked_reason(e["text"])
                if reason:
                    sm["blocked"] = reason
        sm["used"] = sm["steps"] > 0
    except Exception:
        pass
    return sm


def orphan_steps(events, now: float) -> list[dict]:
    """시작(cc step)만 있고 끝(browser)이 없는 단계 → stopped 마감 이벤트 (서버 재시작 후 ⏳ 가 남지 않게)."""
    try:
        evs = [e for e in (events or []) if isinstance(e, dict)]
        ended = {e.get("step") for e in evs if e.get("type") == "browser"}
        out, seen = [], set()
        for cc in evs:
            step = cc.get("step")
            if cc.get("type") == "cc" and cc.get("browser") and step and step not in ended and step not in seen:
                seen.add(step)
                out.append({"type": "browser", "ts": now, "step": step, "text": cc.get("tool", ""),
                            "tool": "", "status": "stopped"})
        return out
    except Exception:
        return []


def progress(events) -> dict | None:
    """기록 목록용 Chrome 진행 한 줄 — 마지막 result 이후 구간. 쓸 내용이 없으면 None. 예외는 None."""
    try:
        evs = [e for e in (events or []) if isinstance(e, dict)]
        last_res = max((i for i, e in enumerate(evs) if e.get("type") == "result"), default=-1)
        steps, last, shot_ts, blocked, account = 0, None, None, None, None
        started: dict[str, dict] = {}            # step → 시작 cc (끝나면 제거)
        for e in evs[last_res + 1:]:
            t = e.get("type")
            if t == "cc" and e.get("browser") and e.get("step"):
                started[e["step"]] = e
            elif t == "browser":
                steps += 1
                started.pop(e.get("step"), None)
                last = {"text": str(e.get("text") or "")[:120], "status": e.get("status") or "", "ts": e.get("ts")}
                if e.get("shot"):
                    shot_ts = e.get("ts")
                if e.get("status") == "ok" and e.get("write", True):
                    blocked = None
            elif t == "browser_account":
                account = ({"email": e["email"], "ok": bool(e.get("allowed")) or e.get("gate") is False}
                           if e.get("email") else None)
            if t == "cc" and e.get("text"):
                reason = blocked_reason(e["text"])
                if reason:
                    blocked = reason
        cur = list(started.values())[-1] if started else None
        current = {"text": str(cur.get("tool") or "")[:120], "ts": cur.get("ts")} if cur else None
        if not (steps or current or account or blocked):
            return None
        return {"steps": steps, "current": current, "last": last, "shot_ts": shot_ts,
                "blocked": blocked, "account": account}
    except Exception:
        return None
