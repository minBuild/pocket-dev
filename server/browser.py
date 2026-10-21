"""Claude in Chrome — 구현 AI 가 이 PC 의 Chrome(로그인된 상태)으로 콘솔 작업(AdMob 앱 등록·광고 단위 ID 발급 등)을 하게 한다.

작업마다 켜는 옵션 (run.settings["browser"]). Chrome 에 Claude 확장이 설치·로그인돼 있어야 한다.
읽기 전용 도구(탭 목록·페이지 읽기·찾기)만 묻지 않고 허용하고, 클릭·입력·이동·스크립트·업로드 같은
실제로 무언가를 하는 도구는 다른 도구와 똑같이 권한 심사(리뷰어 AI → 의견이 갈리면 폰으로 질문)를 거친다.

작업 화면 중계용 함수(describe·parse_result·summarize …)는 서버 모듈을 import 하지 않는 순수 함수다 (stdlib 만).
파일 저장과 emit 은 executor 가 한다.
"""
import base64
import re

READ_ONLY = [f"mcp__claude-in-chrome__{t}" for t in (
    "tabs_context_mcp", "read_page", "find", "get_page_text", "list_connected_browsers")]

PREFIX = "mcp__claude-in-chrome__"
AUTO_ALLOW_ACTIONS = {"screenshot", "zoom", "wait", "scroll", "scroll_to"}
REMEMBER_TOOLS = {PREFIX + "navigate", PREFIX + "tabs_create_mcp"}
HARD_DENY_TOOLS = {PREFIX + t for t in ("javascript_tool", "file_upload", "upload_image")}  # 실제 이름은 T1 프로브 때 도구 목록으로 확인
BLOCK_MARK = "[CHROME-BLOCKED]"
SHOT_RE = re.compile(r"^\d{13}-[A-Za-z0-9]{1,8}\.(png|jpe?g|webp)$")
DENY_PREFIX_RE = re.compile(r"^\s*\[(hard-rule|AI 리뷰어|사용자|timeout|설정|이전 판정 재사용)\]")

PLAN_NOTE = ("\n\n[구현 환경] 이번 작업의 구현 AI 는 이 PC 의 Chrome(사용자가 로그인한 상태)을 조작할 수 있다 "
             "(클릭·입력은 하나하나 권한 심사를 거침). AdMob·Firebase 같은 웹 콘솔에서 앱 등록, 광고 단위 생성, "
             "ID 확인처럼 사람이 하던 설정 작업도 구현 단계에 넣을 수 있다. "
             "결제·요금제·계정 권한 변경, 기존 항목 삭제, 게시·심사 제출은 하지 않는다.")

EXEC_NOTE = """

[Chrome 사용 가능] 이 PC 의 Chrome 을 claude-in-chrome 도구로 조작할 수 있다 (사용자가 로그인해 둔 상태).
클릭·입력·이동은 하나하나 권한 심사를 거치니, 각 동작이 무엇을 위한 것인지 분명하게 하고 꼭 필요한 동작만 한다.
명세서에 있는 웹 콘솔 작업(예: AdMob 앱 추가·광고 단위 생성·앱 ID/광고 단위 ID 확인)만 한다.
- 시작할 때 tabs_context_mcp 로 연결을 확인하고, 새 탭을 만들어서 작업한다. 사용자가 열어 둔 탭은 건드리지 않는다.
- 만들기 전에 같은 이름의 앱·광고 단위가 이미 있는지 먼저 확인하고, 있으면 새로 만들지 말고 그 ID 를 쓴다.
- 금지: 결제·요금제·지급 정보, 계정·사용자 권한 설정, 기존 앱·광고 단위·프로젝트 삭제나 비활성화, 약관 동의,
  앱 게시·심사 제출, 로컬 파일 업로드. 이런 단계가 필요하면 멈추고 "[CHROME-BLOCKED] <무엇이 필요한지>" 한 줄을 남긴다.
- 로그인·2단계 인증·캡차가 나오면 우회하지 말고 멈춰서 "[CHROME-BLOCKED] 사용자가 Chrome 에서 로그인해야 함" 한 줄을 남긴다.
- 같은 동작이 2번 거부되거나 실패하면 반복하지 말고, "[CHROME-BLOCKED] <이유>" 한 줄을 남긴 뒤 Chrome 작업을 멈춘다 (코드 작업은 계속한다).
- computer 도구의 screenshot·zoom·wait·scroll 은 심사 없이 허용된다. 스크린샷은 막혔을 때와 Chrome 작업을 마치기 직전에 1장씩만 찍고, 동작마다 찍지 않는다.
- JavaScript 실행 도구와 파일 업로드 도구는 쓸 수 없다 (즉시 거부). ID 는 get_page_text·find 로 읽는다.
- 발급받은 ID 는 코드·설정 파일에 반영하고, 작업 끝에 무엇을 만들었고 어떤 ID 를 어디에 넣었는지 정리한다.
  단 프로젝트 규칙(CLAUDE.md·명세서)이 실제 ID 를 저장소에 넣지 말라고 하면 그 방식(--dart-define 등)을 따르고, ID 는 결과 정리에만 남긴다.
  (AdMob 앱 ID 는 ca-app-pub-…~…, 광고 단위 ID 는 ca-app-pub-…/… 형식. 테스트 빌드에 테스트 광고 ID 를 쓰는 구조면 그대로 지킨다.)
- Chrome 연결이 안 되면 브라우저 작업은 건너뛰고 코드 작업만 끝낸 뒤, "[CHROME-BLOCKED] Chrome 연결 안 됨" 한 줄을 남긴다."""


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
    sm = {"used": False, "steps": 0, "ok": 0, "denied": 0, "error": 0, "stopped": 0, "shots": 0, "blocked": None}
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
                if st == "ok":
                    sm["blocked"] = None
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
