"""📦 출시 관리 — 순수 파서·그룹·할 일·장부 저장·스캔 병합 (git·gh 는 monkeypatch)."""
import asyncio
import datetime
import json
import re
from pathlib import Path

import pytest

from server import executor, github, release

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.datetime(2026, 10, 28, 12, 0).timestamp()


def ago(days: int) -> str:
    return (datetime.date(2026, 10, 28) - datetime.timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "LEDGER_DIR", tmp_path)
    monkeypatch.setattr(release, "_loaded", True)
    monkeypatch.setattr(release, "_warning", "")
    monkeypatch.setattr(release, "_scan_task", None)
    monkeypatch.setattr(release, "_scan", {"done": 0, "total": 0, "current": "", "errors": [], "gh_ok": True})
    release.LEDGER.clear()
    release.LEDGER.update(release._empty())
    yield
    release.LEDGER.clear()


def app(**kw):
    store = kw.pop("store", {})
    scan = kw.pop("scan", {})
    a = release._norm_app({"name": kw.pop("name", "foo"), "kind": "flutter", "platforms": ["android", "ios"],
                           "store": store, "scan": scan, **kw})
    return a


def ledger(**apps):
    return {"v": 1, "scanned_at": None, "apps": apps}


# ── 키 · git 래퍼 ──
@pytest.mark.parametrize("url, key", [
    ("git@github.com:minBuild/Foo.git", "minbuild/foo"),
    ("https://github.com/minBuild/foo", "minbuild/foo"),
    ("https://github.com/minBuild/foo.git", "minbuild/foo"),
    ("https://github.com/MinBuild/FOO/", "minbuild/foo"),
])
def test_repo_key_github(url, key):
    assert release.repo_key(url, "C:/x/whatever") == key


def test_repo_key_local():
    assert release.repo_key(None, Path("C:/Users/Jang/IdeaProjects/MyApp")) == "local:ideaprojects/myapp"
    assert release.repo_key("https://gitlab.com/a/b.git", "C:/A/B") == "local:a/b"


def test_local_index_first_root_wins(monkeypatch):
    a, b = Path("C:/root1/foo"), Path("C:/root2/foo")
    monkeypatch.setattr(github, "projects", lambda: [a, b])

    async def sh(args, cwd, **kw):
        return 0, "https://github.com/minBuild/foo.git\n"
    monkeypatch.setattr(executor, "sh", sh)
    idx = asyncio.run(github.local_index())
    assert list(idx) == ["minbuild/foo"] and idx["minbuild/foo"]["path"] == str(a)
    assert idx["minbuild/foo"]["owner"] == "minBuild"


def test_git_missing_folder_returns_none(tmp_path):
    assert asyncio.run(release._git(tmp_path / "nope", "status")) is None


# ── 순수 파서 ──
def test_parse_pubspec_version():
    assert release.parse_pubspec_version("name: x\nversion: 1.4.2+37\n") == "1.4.2+37"
    assert release.parse_pubspec_version('version: "1.0.0+1"  # 주석\n') == "1.0.0+1"
    assert release.parse_pubspec_version("version: '2.0.0' # c") == "2.0.0"
    assert release.parse_pubspec_version("name: x\n") is None


def test_parse_gradle_groovy_and_kts():
    groovy = 'android {\n namespace "com.ns"\n defaultConfig {\n  applicationId "com.x.foo"\n  versionName "1.2.3"\n }\n}'
    assert release.parse_gradle(groovy) == {"app_id": "com.x.foo", "version_name": "1.2.3"}
    kts = 'android {\n namespace = "com.ns"\n defaultConfig {\n  applicationId = "com.x.kts"\n  versionName = flutter.versionName\n }\n}'
    assert release.parse_gradle(kts) == {"app_id": "com.x.kts", "version_name": None}
    single = "defaultConfig { applicationId 'com.single' }"
    assert release.parse_gradle(single)["app_id"] == "com.single"


def test_parse_gradle_outside_block_and_namespace():
    text = 'android {\n namespace "com.ns.app"\n flavors { applicationId "com.wrong" }\n defaultConfig {\n  minSdk 21\n }\n}'
    assert release.parse_gradle(text)["app_id"] == "com.ns.app"
    assert release.parse_gradle("versionName flutter.versionName")["version_name"] is None


def test_parse_pbxproj():
    text = """
    PRODUCT_BUNDLE_IDENTIFIER = com.x.foo.RunnerTests;
    PRODUCT_BUNDLE_IDENTIFIER = "$(PRODUCT_BUNDLE_IDENTIFIER).ext";
    PRODUCT_BUNDLE_IDENTIFIER = com.x.foo.widget;
    PRODUCT_BUNDLE_IDENTIFIER = com.x.foo;
    """
    assert release.parse_pbxproj_bundle_id(text) == "com.x.foo"
    assert release.parse_pbxproj_bundle_id("nothing") is None


def test_detect_kind_and_language():
    assert release.detect_kind({"pubspec.yaml", "android/", "ios/"}) == "flutter"
    assert release.detect_kind({"app/build.gradle.kts", "settings.gradle"}) == "android"
    assert release.detect_kind({"Foo.xcodeproj/"}) == "ios"
    assert release.detect_kind({"ios/", "ios/Foo.xcodeproj/"}) == "ios"
    assert release.detect_kind({"package.json"}) == "web"
    assert release.detect_kind({"README.md"}) == "other"
    assert release.kind_from_language(None) == "other"
    assert release.kind_from_language("Dart") == "flutter"
    assert release.platforms_for("flutter", {"pubspec.yaml", "android/"}) == ["android"]
    assert release.platforms_for("flutter", set()) == ["android", "ios"]
    assert release.platforms_for("web", {"package.json"}) == []


def test_parse_heads_nulls():
    data = {"data": {"repositoryOwner": {"repositories": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"name": "foo", "url": "https://github.com/minBuild/foo", "isFork": False, "pushedAt": "2026-10-01T00:00:00Z",
                   "primaryLanguage": None, "defaultBranchRef": None},
                  {"name": "bar", "url": "https://github.com/minBuild/bar", "isFork": True, "pushedAt": None,
                   "primaryLanguage": {"name": "Dart"}, "defaultBranchRef": {"name": "main", "target": {"oid": "a" * 40}}}]}}}}
    heads, info = github.parse_heads(data)
    assert heads[0] == {"name": "foo", "url": "https://github.com/minBuild/foo", "fork": False, "pushed": "2026-10-01T00:00:00Z",
                        "language": None, "default_branch": "", "oid": None}
    assert heads[1]["fork"] is True and heads[1]["language"] == "Dart" and heads[1]["oid"] == "a" * 40
    assert info["hasNextPage"] is False
    assert github.parse_heads({"data": {"repositoryOwner": None}}) == ([], {})


# ── 그룹 · 정렬 ──
def test_group_of_table():
    g = release.group_of
    assert g(app(store={"android": {"state": "rejected"}, "ios": {"state": "in_review"}})) == "rejected"
    assert g(app(store={"android": {"state": "live"}, "ios": {"state": "in_review"}})) == "in_review"
    assert g(app(store={"android": {"state": "live"}}, next="광고 붙이기")) == "update"
    assert g(app(store={"android": {"state": "live"}}, dev="in_dev")) == "update"
    assert g(app(store={"android": {"state": "live"}}, dev="paused", next="x")) == "live"
    assert g(app(store={"android": {"state": "live"}})) == "live"
    assert g(app(dev="idea")) == "in_dev"
    assert g(app(dev="in_dev")) == "in_dev"
    assert g(app(dev="paused")) == "paused"
    assert g(app()) == "unset"


def test_commits_need_matching_sha():
    live = {"android": {"state": "live", "sha": "a" * 40}}
    ok = app(store=live, scan={"since": {"android": {"sha": "a" * 40, "count": 3}}})
    stale = app(store=live, scan={"since": {"android": {"sha": "b" * 40, "count": 3}}})
    assert release.commits_of(ok, "android") == 3 and release.group_of(ok) == "update"
    assert release.commits_of(stale, "android") is None and release.group_of(stale) == "live"
    zero = app(store=live, scan={"since": {"android": {"sha": "a" * 40, "count": 0}}})
    assert release.group_of(zero) == "live"


def test_sort_order():
    led = ledger(**{
        "a/new": app(name="New", store={"android": {"state": "in_review", "since": ago(1)}}),
        "a/old": app(name="Old", store={"android": {"state": "in_review", "since": ago(5)}}),
        "a/nul": app(name="Nul", store={"android": {"state": "in_review"}}),
        "a/c1": app(name="c1", scan={"last_commit": {"at": "2026-10-01T00:00:00+09:00"}}),
        "a/c2": app(name="c2", scan={"last_commit": {"at": "2026-10-20T00:00:00Z"}}),
        "a/c0": app(name="c0"),
        "a/rej": app(name="Rej", store={"ios": {"state": "rejected", "since": ago(2)}}),
    })
    order = [k for k, _, _ in release._sorted(led, NOW)]
    assert order == ["a/rej", "a/old", "a/new", "a/nul", "a/c2", "a/c1", "a/c0"]


# ── 경과일 · 할 일 · 배지 ──
def test_overdue_boundaries():
    warn = {"android": 7, "ios": 3}
    a6 = app(store={"android": {"state": "in_review", "since": ago(6)}})
    a7 = app(store={"android": {"state": "in_review", "since": ago(7)}})
    i3 = app(store={"ios": {"state": "in_review", "since": ago(3)}})
    nul = app(store={"ios": {"state": "in_review", "since": None}})
    assert release.days_of(a6, "android", NOW) == 6 and not release.overdue_of(a6, "android", NOW, warn)
    assert release.overdue_of(a7, "android", NOW, warn)
    assert release.overdue_of(i3, "ios", NOW, warn)
    assert release.days_of(nul, "ios", NOW) is None and not release.overdue_of(nul, "ios", NOW, warn)
    live = app(store={"android": {"state": "live", "since": ago(30)}})
    assert release.days_of(live, "android", NOW) is None


def test_todo_order_text_and_badge():
    led = ledger(**{
        "m/unset": app(name="zed"),
        "m/rej": app(name="foo", store={"ios": {"state": "rejected", "since": ago(1)}}),
        "m/rev": app(name="bar", store={"android": {"state": "in_review", "since": ago(9)}}),
        "m/ok": app(name="ok", store={"android": {"state": "in_review", "since": ago(6)}}),
        "m/rem": app(name="baz", dev="in_dev", scan={"remote_ahead": True}),
        "m/gone": app(name="gone", dev="in_dev", missing=True, scan={"remote_ahead": True}),
        "m/upd": app(name="upd", store={"android": {"state": "live", "sha": "a" * 40}},
                     scan={"since": {"android": {"sha": "a" * 40, "count": 12}}}),
        "m/hid": app(name="hid", hidden=True, store={"ios": {"state": "rejected"}}),
    })
    items = release.todo(led, NOW, {"review_warn_days": {"android": 7, "ios": 3}})
    assert [t["text"] for t in items] == [
        "❌ foo — iOS 심사 거절",
        "⏳ bar — Android 심사 9일째 (기준 7일)",
        "📥 baz — 원격에 새 커밋",
        "🔄 upd — 출시 후 커밋 12개, 다음 할 일 없음",
        "🗂 미분류 1개 — 일괄 분류",
    ]
    assert items[-1]["key"] is None and items[0]["key"] == "m/rej"
    assert release.badge_of(items) == 2


@pytest.mark.parametrize("hidden, auto, vis", [
    (None, None, True), (None, "fork", False), (True, None, False),
    (True, "other", False), (False, "other", True), (False, None, True),
])
def test_visibility(hidden, auto, vis):
    assert release.visible_of(app(hidden=hidden, auto_non_app=auto)) is vis


def test_auto_non_app():
    assert release.auto_non_app_of(app(kind="other"), {"fork": True}, "minBuild") == "fork"
    assert release.auto_non_app_of(app(github="https://github.com/jjangms/x"), {}, "minBuild") == "foreign"
    assert release.auto_non_app_of(app(github="https://github.com/MINBUILD/x"), {}, "minBuild") is None
    assert release.auto_non_app_of(app(kind="other"), {}, "minBuild") == "other"


# ── 장부 로드 · 저장 ──
def test_save_failure_keeps_ledger(monkeypatch):
    release.LEDGER["apps"]["m/a"] = app()

    def boom(*a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(release.os, "replace", boom)

    async def fast(_):
        return None
    monkeypatch.setattr(release.asyncio, "sleep", fast)
    with pytest.raises(release.LedgerSaveError):
        asyncio.run(release.mutate(lambda d: d["apps"]["m/a"].update(next="바뀜")))
    assert release.LEDGER["apps"]["m/a"]["next"] == ""


def test_save_and_bak(tmp_path):
    asyncio.run(release.mutate(lambda d: d["apps"].update({"m/a": app()})))
    asyncio.run(release.mutate(lambda d: d["apps"]["m/a"].update(next="둘째")))
    assert json.loads((tmp_path / "apps.json").read_text(encoding="utf-8"))["apps"]["m/a"]["next"] == "둘째"
    assert json.loads((tmp_path / "apps.json.bak").read_text(encoding="utf-8"))["apps"]["m/a"]["next"] == ""
    assert not (tmp_path / "apps.json.tmp").exists()


def test_load_recovers_from_bak(tmp_path):
    (tmp_path / "apps.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "apps.json.bak").write_text(json.dumps(ledger(**{"m/a": {"name": "a"}})), encoding="utf-8")
    release.load()
    assert "m/a" in release.LEDGER["apps"] and ".bak" in release._warning


def test_load_both_broken(tmp_path):
    (tmp_path / "apps.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "apps.json.bak").write_text("[1,", encoding="utf-8")
    release.load()
    assert release.LEDGER == release._empty() and "빈 장부" in release._warning
    assert list(tmp_path.glob("apps.json.corrupt-*")) and not (tmp_path / "apps.json").exists()


def test_load_missing_file_no_warning():
    release.load()
    assert release.LEDGER == release._empty() and release._warning == ""


@pytest.mark.parametrize("raw", [{"v": 1, "apps": []}, {"v": 1, "apps": {"m/a": "문자열"}},
                                 {"apps": {"m/a": {"store": [], "scan": "x", "platforms": "android", "dev": 3}}}])
def test_odd_structure_snapshot(tmp_path, raw):
    (tmp_path / "apps.json").write_text(json.dumps(raw), encoding="utf-8")
    release.load()
    snap = release.snapshot(NOW)
    assert isinstance(snap["apps"], list) and snap["warning"] == ""


def test_snapshot_does_not_scan():
    release.snapshot(NOW)
    assert release._scan_task is None


def test_scan_start_if_older_zero():
    r = asyncio.run(release.scan_start(if_older_min=0))
    assert r["started"] is False and release._scan_task is None


# ── 스캔 병합 ──
def _seed():
    a = app(name="foo", dev="in_dev", next="광고", notes="메모", hidden=False,
            store={"android": {"state": "live", "since": ago(30), "sha": "a" * 40, "version": "1.0.0"}},
            scan={"code_version": "1.0.0+1", "head": "c" * 40})
    release.LEDGER["apps"]["minbuild/foo"] = a
    return a


def test_scan_merge_keeps_human_fields(monkeypatch):
    _seed()

    async def collect(path, origin, head, prev):
        assert prev["store"]["android"]["sha"] == "a" * 40
        return {"scan": {**release._default_scan(), "code_version": "2.0.0+5"}, "kind": "flutter",
                "platforms": ["android"], "path": path, "github": "https://github.com/minBuild/foo", "name": "foo"}
    monkeypatch.setattr(release, "_collect_local", collect)
    asyncio.run(release._scan_one("minbuild/foo", {"path": "C:/x/foo", "origin": None, "owner": "minBuild"}, None))
    a = release.LEDGER["apps"]["minbuild/foo"]
    assert a["scan"]["code_version"] == "2.0.0+5" and a["platforms"] == ["android"]
    assert (a["dev"], a["next"], a["notes"], a["hidden"]) == ("in_dev", "광고", "메모", False)
    assert a["store"]["android"]["sha"] == "a" * 40 and a["store"]["android"]["version"] == "1.0.0"


def test_scan_error_keeps_previous_scan(monkeypatch):
    _seed()

    async def collect(*a):
        raise RuntimeError("git rev-parse 실패\n두 번째 줄")
    monkeypatch.setattr(release, "_collect_local", collect)
    asyncio.run(release._scan_one("minbuild/foo", {"path": "C:/x/foo"}, None))
    s = release.LEDGER["apps"]["minbuild/foo"]["scan"]
    assert s["code_version"] == "1.0.0+1" and s["stale"] is True and "\n" not in s["error"] and s["error"]
    assert release._scan["errors"]


@pytest.mark.parametrize("heads", [([], False), ([], True)])
def test_scan_all_gh_fail_keeps_missing(monkeypatch, heads):
    release.LEDGER["apps"]["minbuild/ghonly"] = app(name="ghonly", missing=False)
    release.LEDGER["apps"]["minbuild/gone"] = app(name="gone", missing=True)

    async def rh(force=False):
        return heads

    async def li():
        return {}
    monkeypatch.setattr(github, "repo_heads", rh)
    monkeypatch.setattr(github, "local_index", li)
    asyncio.run(release._scan_all())
    assert release.LEDGER["apps"]["minbuild/ghonly"]["missing"] is False
    assert release.LEDGER["apps"]["minbuild/gone"]["missing"] is True
    assert release.LEDGER["scanned_at"] and release._scan["gh_ok"] is heads[1]


def test_scan_all_marks_missing_when_gh_ok(monkeypatch):
    release.LEDGER["apps"]["minbuild/gone"] = app(name="gone")

    async def rh(force=False):
        return [{"name": "here", "url": "https://github.com/minBuild/here", "fork": False, "pushed": None,
                 "language": "Dart", "default_branch": "main", "oid": "d" * 40}], True

    async def li():
        return {}
    monkeypatch.setattr(github, "repo_heads", rh)
    monkeypatch.setattr(github, "local_index", li)
    asyncio.run(release._scan_all())
    assert release.LEDGER["apps"]["minbuild/gone"]["missing"] is True
    here = release.LEDGER["apps"]["minbuild/here"]
    assert here["missing"] is False and here["path"] is None and here["kind"] == "flutter"
    assert here["platforms"] == ["android", "ios"]


# ── update 검증 ──
def _upd(payload):
    return asyncio.run(release.update(payload))


@pytest.mark.parametrize("payload", [
    {"key": "minbuild/foo", "store": {"android": {"state": "published"}}},
    {"key": "minbuild/foo", "dev": "done"},
    {"key": "minbuild/foo", "store": {"android": {"baseline": "HEAD"}}},
    {"key": "minbuild/foo", "store": {"android": {"baseline": "a1b2c3d"}}},
    {"key": "minbuild/foo", "next": "x" * 2001},
    {"key": "minbuild/foo", "notes": "x" * 4001},
    {"key": "minbuild/foo", "store": {"ios": {"version": "x" * 41}}},
    {"key": "minbuild/foo", "hidden": True},
    {"key": "minbuild/foo", "store": {"android": {"sha": "a" * 40}}},
    {"key": "minbuild/foo", "store": {"web": {"state": "live"}}},
    {"key": "minbuild/foo", "store": {"android": {"since": "2026-13-01"}}},
    {"key": "minbuild/foo", "store": {"android": {"since": "어제"}}},
])
def test_update_rejects(payload):
    _seed()
    with pytest.raises(ValueError):
        _upd(payload)


def test_update_unknown_key():
    with pytest.raises(KeyError):
        _upd({"key": "minbuild/nope", "dev": "idea"})


def test_update_since_rules():
    _seed()
    today = datetime.date.today().isoformat()
    _upd({"key": "minbuild/foo", "store": {"ios": {"state": "in_review"}}})
    assert release.LEDGER["apps"]["minbuild/foo"]["store"]["ios"]["since"] == today
    _upd({"key": "minbuild/foo", "store": {"ios": {"since": None}}})
    assert release.LEDGER["apps"]["minbuild/foo"]["store"]["ios"]["since"] is None
    _upd({"key": "minbuild/foo", "store": {"ios": {"version": "1.1"}}})   # state 그대로 → null 유지
    assert release.LEDGER["apps"]["minbuild/foo"]["store"]["ios"]["since"] is None
    _upd({"key": "minbuild/foo", "store": {"ios": {"since": ""}}})
    assert release.LEDGER["apps"]["minbuild/foo"]["store"]["ios"]["since"] == today
    _upd({"key": "minbuild/foo", "store": {"ios": {"since": "2026-10-01"}}})
    assert release.LEDGER["apps"]["minbuild/foo"]["store"]["ios"]["since"] == "2026-10-01"
    snap = _upd({"key": "minbuild/foo", "store": {"ios": {"state": "none"}}, "next": "", "dev": "paused"})
    a = release.LEDGER["apps"]["minbuild/foo"]
    assert a["store"]["ios"]["since"] is None and a["dev"] == "paused" and a["next"] == ""
    assert a["store"]["android"]["sha"] == "a" * 40    # baseline 생략 → 유지
    assert any(x["key"] == "minbuild/foo" for x in snap["apps"])


def test_update_baseline_null_clears():
    _seed()
    _upd({"key": "minbuild/foo", "store": {"android": {"baseline": None}}})
    assert release.LEDGER["apps"]["minbuild/foo"]["store"]["android"]["sha"] is None


def test_hide_validation():
    _seed()
    with pytest.raises(ValueError):
        asyncio.run(release.hide("minbuild/foo", "yes"))
    with pytest.raises(KeyError):
        asyncio.run(release.hide("minbuild/nope", True))
    asyncio.run(release.hide("minbuild/foo", None))
    assert release.LEDGER["apps"]["minbuild/foo"]["hidden"] is None


def test_no_quoted_running_in_release():
    assert not re.search(r"""["']running["']""", (ROOT / "server" / "release.py").read_text(encoding="utf-8"))
