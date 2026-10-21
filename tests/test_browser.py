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
    assert sm == {"used": True, "steps": 3, "ok": 1, "denied": 0, "error": 1, "stopped": 1, "shots": 1, "blocked": None,
                  "account": None, "report": None}


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


# ── 로그인 계정 확인 ──
def test_norm_email():
    assert browser.norm_email("A@B.COM") == "a@b.com"
    assert browser.norm_email("<x@y.com>") == "x@y.com"
    assert browser.norm_email("x@y.com.") == "x@y.com"
    assert browser.norm_email("  (x@y.com), ") == "x@y.com"
    assert browser.norm_email("not an email") is None
    assert browser.norm_email("") is None
    assert browser.norm_email(None) is None
    assert browser.norm_email("a" * 249 + "@b.com") is None   # 255자


def test_emails_in():
    assert browser.emails_in("Google 계정: 홍길동 (Hong@Gmail.com)") == {"hong@gmail.com"}
    assert browser.emails_in("이메일 없음") == set()
    assert browser.emails_in(None) == set()
    assert len(browser.emails_in(" ".join(f"u{i}@x.com" for i in range(80)))) == 50


def test_allowed_list():
    assert browser.allowed_list("a@b.com") == ["a@b.com"]   # 글자 단위로 나뉘지 않음
    assert browser.allowed_list("a@b.com, c@d.com") == ["a@b.com", "c@d.com"]
    assert browser.allowed_list(["A@B.com", "c@d.com"]) == ["a@b.com", "c@d.com"]
    assert browser.allowed_list(None) == []
    assert browser.allowed_list(["bad", "a@b.com", 3]) == ["a@b.com"]
    assert browser.allowed_list(["a@b.com", "A@b.com ", "c@d.com"]) == ["a@b.com", "c@d.com"]


def test_account_allowed():
    assert browser.account_allowed(" A@B.com ", ["a@b.com"])
    assert not browser.account_allowed("other@x.com", ["a@b.com"])
    assert not browser.account_allowed("a@b.com", [])


def test_scope_of():
    assert browser.scope_of("http://localhost:3000/x") == ""
    assert browser.scope_of("localhost:3000/x") == ""
    assert browser.scope_of("http://127.0.0.1") == ""
    assert browser.scope_of("https://example.com") == ""
    assert browser.scope_of("https://apps.admob.com/v2/home") == "apps.admob.com#0"
    assert browser.scope_of("apps.admob.com/v2") == "apps.admob.com#0"
    assert browser.scope_of("https://console.firebase.google.com/?authuser=1") == "console.firebase.google.com#1"
    assert browser.scope_of("https://play.google.com/console/u/2/developers") == "play.google.com#2"
    for bad in ("back", "forward", "", None, "chrome://newtab", "about:blank", "javascript:alert(1)"):
        assert browser.scope_of(bad) is None, bad


def test_scope_after():
    assert browser.scope_after(P + "navigate", {"url": "https://apps.admob.com"}, "x#0") == "apps.admob.com#0"
    assert browser.scope_after(P + "tabs_create_mcp", {}, "x#0") is None
    assert browser.scope_after(P + "tabs_create_mcp", {"url": "https://apps.admob.com"}, None) == "apps.admob.com#0"
    assert browser.scope_after(P + "computer", {"action": "left_click"}, "x#0") == "x#0"
    assert browser.scope_after(P + "navigate", None, "x#0") is None


def test_is_evidence():
    for t in ("read_page", "get_page_text", "find"):
        assert browser.is_evidence(P + t)
    for t in ("tabs_context_mcp", "list_connected_browsers", "computer", "navigate"):
        assert not browser.is_evidence(P + t)
    assert not browser.is_evidence("Bash")


def test_is_write():
    assert browser.is_write(P + "computer", {"action": "left_click"})
    assert browser.is_write(P + "computer", {"action": "type", "text": "x"})
    assert browser.is_write(P + "form_input", {"value": "x"})
    assert browser.is_write(P + "something_new", {})
    assert not browser.is_write(P + "computer", {"action": "screenshot"})
    for t in ("navigate", "tabs_create_mcp", "tabs_context_mcp", "read_page", "find"):
        assert not browser.is_write(P + t, {})
    assert not browser.is_write("Bash", {})


def test_account_gate():
    click, al = (P + "computer", {"action": "left_click"}), ["a@b.com"]
    assert browser.account_gate(*click, "apps.admob.com#0", None, []) is None
    assert browser.account_gate(P + "navigate", {"url": "x"}, None, None, al) is None
    assert browser.account_gate(*click, "", None, al) is None
    assert browser.account_gate(*click, "a#0", ("a@b.com", "a#0", True), al) is None
    assert "navigate" in browser.account_gate(*click, None, None, al)
    assert "확인되지 않았어요" in browser.account_gate(*click, "a#0", None, al)
    assert "확인되지 않았어요" in browser.account_gate(*click, "b#0", ("a@b.com", "a#0", True), al)
    assert "허용 목록에 없어요" in browser.account_gate(*click, "a#0", ("other@x.com", "a#0", False), al)


def test_account_reply():
    replies = [browser.account_reply(None, False, False, True),
               browser.account_reply("a@b.com", False, False, True),
               browser.account_reply("a@b.com", True, True, False),
               browser.account_reply("a@b.com", True, True, True),
               browser.account_reply("a@b.com", True, False, True)]
    assert "이메일 형식이 아니에요" in replies[0]
    assert "읽은 내용에 없어요" in replies[1]
    assert "검사 꺼짐" in replies[2]
    assert "허용된 계정" in replies[3]
    assert "허용 목록에 없는 계정" in replies[4] and "[CHROME-BLOCKED] 계정 불일치: a@b.com" in replies[4]
    assert all("other@x.com" not in r for r in replies)


def test_parse_result_account_denied():
    st, _, _ = browser.parse_result({"is_error": True, "content": "[계정 확인] apps.admob.com 의 로그인 계정이…"}, None)
    assert st == "denied"


def _bev(status, **kw):
    return {"type": "browser", "status": status, "step": "s", **kw}


def test_summarize_blocked_kept_by_read_step():
    evs = [{"type": "cc", "text": "[CHROME-BLOCKED] 로그인 필요"}, _bev("ok", write=False)]
    assert browser.summarize(evs)["blocked"] == "로그인 필요"


def test_summarize_blocked_cleared_by_write_or_old_event():
    mark = {"type": "cc", "text": "[CHROME-BLOCKED] x"}
    assert browser.summarize([mark, _bev("ok")])["blocked"] is None
    assert browser.summarize([mark, _bev("ok", write=True)])["blocked"] is None


def test_summarize_account_and_report():
    rep = {"type": "browser_account", "email": "a@b.com", "allowed": True, "verified": True, "gate": True,
           "scope": "apps.admob.com#0"}
    reset = {"type": "browser_account", "email": "", "allowed": False, "verified": False, "gate": True, "scope": ""}
    sm = browser.summarize([rep, reset])
    assert sm["account"]["email"] == "" and sm["report"]["email"] == "a@b.com"
    sm = browser.summarize([rep])
    assert sm["account"] == sm["report"] == {k: rep[k] for k in ("email", "allowed", "verified", "gate", "scope")}
    sm = browser.summarize([rep, {"type": "result", "text": "x"}])
    assert sm["account"] is None and sm["report"] is None
    assert browser.summarize([])["account"] is None
