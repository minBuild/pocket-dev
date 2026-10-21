"""server/browser.py 순수 함수 테스트.

합성 fixture — T1 프로브(debug_dump)로 실측 구조 확인 후 교체 필요.
"""
import base64
import json

from server import browser

P = browser.PREFIX
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG).decode()


def img_block(data=PNG_B64, media="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


# ── describe ──
def test_describe_navigate_strips_query_and_fragment():
    d = browser.describe({"name": P + "navigate", "input": {"url": "https://admob.google.com/apps?code=secret#frag"}})
    assert d["detail"] == "https://admob.google.com/apps"
    assert "secret" not in d["text"] and "frag" not in d["text"]
    assert d["text"] == "🌐 이동 https://admob.google.com/apps"


def test_describe_type_shows_length_only():
    d = browser.describe({"name": P + "computer", "input": {"action": "type", "text": "my@email.com"}})
    assert d["label"] == "입력 (12자)"
    assert "my@email.com" not in d["text"]
    assert d["action"] == "type"


def test_describe_click_text_is_composed():
    d = browser.describe({"name": P + "computer", "input": {"action": "left_click", "coordinate": [412, 300]}})
    assert d["text"] == "🖱 클릭 (412, 300)"
    d = browser.describe({"name": P + "computer", "input": {"action": "left_click", "ref": "ref_12"}})
    assert d["text"] == "🖱 클릭 ref_12"


def test_describe_form_input_hides_value():
    d = browser.describe({"name": P + "form_input", "input": {"ref": "r1", "value": "hunter2"}})
    assert d["text"] == "📝 폼 입력 (7자)"


def test_describe_unknown_chrome_tool_fallback():
    d = browser.describe({"name": P + "gif_creator", "input": {}})
    assert d["label"] == "gif_creator" and d["icon"] == "🌐"
    assert d["text"] == "🌐 gif_creator"


def test_describe_non_chrome_is_none():
    assert browser.describe({"name": "Bash", "input": {"command": "ls"}}) is None


def test_describe_input_none_no_exception():
    d = browser.describe({"name": P + "computer", "input": None})
    assert d is not None and d["action"] == ""
    assert browser.describe({"name": P + "navigate", "input": "garbage"})["text"] == "🌐 이동"
    assert browser.describe(None) is None


def test_describe_detail_max_80():
    d = browser.describe({"name": P + "navigate", "input": {"url": "https://x.com/" + "a" * 200}})
    assert len(d["detail"]) <= 80


# ── parse_result ──
def test_parse_list_with_image():
    status, images, text = browser.parse_result(
        {"content": [{"type": "text", "text": "done"}, img_block()]}, None)
    assert status == "ok" and len(images) == 1 and images[0] == ("image/png", PNG_B64) and text == "done"


def test_parse_str_content():
    assert browser.parse_result({"content": "page text"}, None) == ("ok", [], "page text")


def test_parse_denied_by_prefix():
    status, _, _ = browser.parse_result({"is_error": True, "content": "[AI 리뷰어] haiku: 결제 화면"}, None)
    assert status == "denied"


def test_parse_denied_by_verdict():
    status, _, _ = browser.parse_result({"is_error": True, "content": "whatever"}, False)
    assert status == "denied"


def test_parse_ok_body_with_user_tag_is_ok():
    status, _, _ = browser.parse_result({"content": [{"type": "text", "text": "[사용자] 님 환영합니다"}]}, None)
    assert status == "ok"


def test_parse_error_with_tag_in_middle_is_error():
    status, _, _ = browser.parse_result({"is_error": True, "content": "timeout reached [설정] blah"}, None)
    assert status == "error"


def test_parse_broken_structure_no_exception():
    assert browser.parse_result({"content": 42}, None)[0] == "ok"
    assert browser.parse_result({"content": 42, "is_error": True}, None)[0] == "error"
    status, images, _ = browser.parse_result({"content": ["str block", None, 3]}, None)
    assert status == "ok" and images == []
    assert browser.parse_result("not a dict", None)[0] in ("ok", "error")


def test_parse_image_only_in_top():
    status, images, _ = browser.parse_result({"content": [{"type": "text", "text": "x"}]}, None,
                                             top=[{"type": "text", "text": "x"}, img_block()])
    assert status == "ok" and len(images) == 1
    _, images, _ = browser.parse_result({"content": "x"}, None, top={"content": [img_block()]})
    assert len(images) == 1


# ── sniff ──
def test_sniff():
    assert browser.sniff(PNG) == "png"
    assert browser.sniff(b"\xff\xd8\xff\xe0" + b"0" * 10) == "jpg"
    assert browser.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert browser.sniff(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>') is None
    assert browser.sniff(b"\x00\x01\x02random") is None


# ── shot_name ──
def test_shot_name_matches_regex():
    for tid in ("toolu_01AbCdEfGh", "a-b_c-d_e", "", None, "___"):
        name = browser.shot_name(1700000000000, tid, "png")
        assert browser.SHOT_RE.match(name), name
    assert browser.shot_name(1700000000000, "toolu_01AbCdEfGh", "jpg") == "1700000000000-CdEfGh.jpg"
    assert browser.shot_name(5, "", "webp") == "0000000000005-x.webp"


# ── structure ──
def test_structure_has_no_body_or_base64():
    body = "secret-revenue-12345 user@example.com"
    block = {"is_error": False, "content": [{"type": "text", "text": body}, img_block()]}
    top = {"content": [img_block()], "extra": body}
    s = browser.structure(P + "computer", block, top, True)
    dumped = json.dumps(s, ensure_ascii=False)
    assert body not in dumped and PNG_B64 not in dumped and PNG_B64[:16] not in dumped
    assert s["tool"] == "computer" and s["content_kind"] == "list" and s["verdict_known"] is True
    assert s["blocks"][0] == {"type": "text", "len": len(body)}
    assert s["blocks"][1]["sniff"] == "png" and s["blocks"][1]["len"] == len(PNG_B64)
    assert s["top_keys"] == ["content", "extra"]
    s2 = browser.structure(P + "find", {"content": body}, None, False)
    assert body not in json.dumps(s2, ensure_ascii=False) and s2["content_kind"] == "str"


# ── summarize ──
def bev(status, step="s", shot=None):
    ev = {"type": "browser", "step": step, "text": "🖱 클릭", "tool": "computer", "status": status}
    if shot:
        ev["shot"] = shot
    return ev


def test_summarize_only_after_last_result():
    events = [bev("ok"), bev("denied"), {"type": "result", "text": "prev"},
              bev("ok", shot="/a.png"), bev("error"), bev("stopped")]
    sm = browser.summarize(events)
    assert sm == {"used": True, "steps": 3, "ok": 1, "denied": 0, "error": 1, "stopped": 1, "shots": 1, "blocked": None}


def test_summarize_blocked_cleared_by_later_ok():
    events = [{"type": "cc", "text": "로그인 화면\n[CHROME-BLOCKED] 사용자가 Chrome 에서 로그인해야 함"}, bev("ok")]
    assert browser.summarize(events)["blocked"] is None


def test_summarize_blocked_kept_after_denied():
    events = [bev("ok"), {"type": "cc", "text": "[CHROME-BLOCKED] 결제 정보 필요"}, bev("denied")]
    assert browser.summarize(events)["blocked"] == "결제 정보 필요"


def test_summarize_zero_steps():
    sm = browser.summarize([{"type": "cc", "text": "hi"}])
    assert sm["used"] is False and sm["steps"] == 0


# ── orphan_steps ──
def test_orphan_steps_unpaired():
    cc = {"type": "cc", "tool": "🖱 클릭 (412, 300)", "step": "toolu_1", "browser": True}
    out = browser.orphan_steps([cc, {"type": "cc", "tool": "Bash ls"}], 123.0)
    assert out == [{"type": "browser", "ts": 123.0, "step": "toolu_1", "text": "🖱 클릭 (412, 300)",
                    "tool": "", "status": "stopped"}]


def test_orphan_steps_paired_empty():
    cc = {"type": "cc", "tool": "🖱 클릭 (412, 300)", "step": "toolu_1", "browser": True}
    assert browser.orphan_steps([cc, bev("ok", step="toolu_1")], 1.0) == []


# ── auto_allow · rememberable · blocked_reason ──
def test_auto_allow():
    assert browser.auto_allow(P + "computer", {"action": "screenshot"})
    assert browser.auto_allow(P + "computer", {"action": "scroll_to"})
    assert not browser.auto_allow(P + "computer", {"action": "left_click"})
    assert not browser.auto_allow(P + "navigate", {"action": "screenshot"})
    assert not browser.auto_allow(P + "computer", None)
    assert not browser.auto_allow("Bash", {"action": "screenshot"})


def test_rememberable():
    assert browser.rememberable("Bash")
    assert browser.rememberable(P + "navigate")
    assert browser.rememberable(P + "tabs_create_mcp")
    assert not browser.rememberable(P + "computer")
    assert not browser.rememberable(P + "form_input")


def test_blocked_reason():
    assert browser.blocked_reason("hello") is None
    assert browser.blocked_reason("a\n  [CHROME-BLOCKED]  로그인 필요  \nb") == "로그인 필요"
    assert browser.blocked_reason("[CHROME-BLOCKED]") == "이유 없음"
    assert len(browser.blocked_reason("[CHROME-BLOCKED] " + "x" * 500)) == 200
    assert browser.blocked_reason(None) is None
