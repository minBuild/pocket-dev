"""📦 출시 관리 (M1) — git 사실 스캔(AI 없음) + 사람이 적는 장부 data/release/apps.json.

- 스캔은 사람 필드(store·dev·next·notes·hidden)를 절대 덮어쓰지 않는다. 자동 필드만 병합한다.
- 모든 변경은 mutate(fn) 한 경로 — 잠금 안에서는 deepcopy → fn(동기) → 저장 → 교체. 잠금 안에서 git·네트워크 금지.
- git 은 전부 --no-optional-locks 로 읽기만 한다 (fetch·pull 금지 — 실행 중인 작업과 충돌하지 않게).

드라이런: python -m server.release --dry  (장부·.bak 에 아무것도 쓰지 않음)
"""
import asyncio
import copy
import datetime
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from . import executor, github
from .config import CONFIG, DATA_DIR
from .runs import RUNS, display_status

LEDGER_DIR = DATA_DIR / "release"   # 테스트가 monkeypatch — 경로는 그때그때 계산
LEDGER: dict = {}
_loaded = False
_warning = ""
_lock: asyncio.Lock | None = None   # 이벤트 루프마다 지연 생성
_lock_loop = None
_scan_task: asyncio.Task | None = None
_scan = {"done": 0, "total": 0, "current": "", "errors": [], "gh_ok": True}   # 메모리 전용

SSH_OPTS = ["-c", "core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=10"]
PLATS = ("android", "ios")
PLAT_LABEL = {"android": "Android", "ios": "iOS"}
STORE_STATES = ("none", "in_review", "rejected", "live")
DEV_STATES = ("unset", "idea", "in_dev", "paused")
KINDS = ("flutter", "android", "ios", "web", "other")
NON_APP = ("fork", "foreign", "other")
GROUPS = [["rejected", "거절됨"], ["in_review", "심사 중"], ["update", "업데이트 준비"], ["in_dev", "개발 중"],
          ["live", "출시됨"], ["paused", "보류"], ["unset", "미분류"]]
GROUP_ORDER = {g: i for i, (g, _) in enumerate(GROUPS)}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LIMITS = {"version": 40, "next": 2000, "notes": 4000}


class LedgerSaveError(Exception):
    pass


def _cfg(key, default):
    return (CONFIG.get("release") or {}).get(key, default)


def _warn_days(cfg=None) -> dict:
    raw = (cfg if cfg is not None else (CONFIG.get("release") or {})).get("review_warn_days") or {}
    out = {"android": 7, "ios": 3}
    for p in PLATS:
        if isinstance(raw.get(p), int) and not isinstance(raw.get(p), bool):
            out[p] = raw[p]
    return out


# ── 정규화 ──────────────────────────────────────────────
def _empty() -> dict:
    return {"v": 1, "scanned_at": None, "apps": {}}


def _str(v, default="") -> str:
    return v if isinstance(v, str) else default


def _opt_str(v):
    return v if isinstance(v, str) and v else None


def _valid_date(v) -> bool:
    if not isinstance(v, str) or not DATE_RE.match(v):
        return False
    try:
        datetime.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _default_scan() -> dict:
    return {"code_version": None, "app_id": {"android": None, "ios": None},
            "head": None, "branch": "", "default_branch": "",
            "last_commit": {"msg": "", "at": None}, "dirty": 0,
            "remote_ahead": None, "remote_ahead_count": None,
            "since": {p: {"sha": None, "count": None} for p in PLATS},
            "tag": None, "error": "", "stale": False, "at": None}


def _norm_scan(raw) -> dict:
    s = _default_scan()
    if not isinstance(raw, dict):
        return s
    s["code_version"] = _opt_str(raw.get("code_version"))
    ids = raw.get("app_id") if isinstance(raw.get("app_id"), dict) else {}
    s["app_id"] = {p: _opt_str(ids.get(p)) for p in PLATS}
    s["head"] = _opt_str(raw.get("head"))
    s["branch"] = _str(raw.get("branch"))
    s["default_branch"] = _str(raw.get("default_branch"))
    lc = raw.get("last_commit") if isinstance(raw.get("last_commit"), dict) else {}
    s["last_commit"] = {"msg": _str(lc.get("msg")), "at": _opt_str(lc.get("at"))}
    s["dirty"] = _int_or_none(raw.get("dirty")) or 0
    ra = raw.get("remote_ahead")
    s["remote_ahead"] = ra if isinstance(ra, bool) else None
    s["remote_ahead_count"] = _int_or_none(raw.get("remote_ahead_count"))
    since = raw.get("since") if isinstance(raw.get("since"), dict) else {}
    for p in PLATS:
        e = since.get(p) if isinstance(since.get(p), dict) else {}
        s["since"][p] = {"sha": _opt_str(e.get("sha")), "count": _int_or_none(e.get("count"))}
    s["tag"] = _opt_str(raw.get("tag"))
    s["error"] = _str(raw.get("error"))[:200]
    s["stale"] = raw.get("stale") is True
    at = raw.get("at")
    s["at"] = at if isinstance(at, (int, float)) and not isinstance(at, bool) else None
    return s


def _norm_store(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    state = raw.get("state") if raw.get("state") in STORE_STATES else "none"
    since = raw.get("since") if _valid_date(raw.get("since")) else None
    return {"state": state, "since": None if state == "none" else since,
            "sha": _opt_str(raw.get("sha")), "version": _str(raw.get("version"))[:LIMITS["version"]]}


def _norm_app(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    store = raw.get("store") if isinstance(raw.get("store"), dict) else {}
    plats = raw.get("platforms") if isinstance(raw.get("platforms"), list) else []
    hidden = raw.get("hidden")
    return {
        "name": _str(raw.get("name")),
        "path": _opt_str(raw.get("path")),
        "github": _opt_str(raw.get("github")),
        "kind": raw.get("kind") if raw.get("kind") in KINDS else "other",
        "platforms": [p for p in PLATS if p in plats],
        "auto_non_app": raw.get("auto_non_app") if raw.get("auto_non_app") in NON_APP else None,
        "hidden": hidden if isinstance(hidden, bool) else None,
        "scan": _norm_scan(raw.get("scan")),
        "dev": raw.get("dev") if raw.get("dev") in DEV_STATES else "unset",
        "store": {p: _norm_store(store.get(p)) for p in PLATS},
        "next": _str(raw.get("next"))[:LIMITS["next"]],
        "notes": _str(raw.get("notes"))[:LIMITS["notes"]],
        "missing": raw.get("missing") is True,
        "suggested": None,   # M2 자리
    }


def _norm_ledger(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("장부 최상위가 객체가 아님")
    apps = raw.get("apps") if isinstance(raw.get("apps"), dict) else {}
    at = raw.get("scanned_at")
    return {"v": 1, "scanned_at": at if isinstance(at, (int, float)) and not isinstance(at, bool) else None,
            "apps": {str(k): _norm_app(a) for k, a in apps.items() if isinstance(a, dict)}}


# ── 로드 · 저장 ──────────────────────────────────────────
def load(readonly: bool = False) -> None:
    global _loaded, _warning
    f, bak = LEDGER_DIR / "apps.json", LEDGER_DIR / "apps.json.bak"
    data, _warning = None, ""
    if f.exists() or bak.exists():
        try:
            data = _norm_ledger(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            try:
                data = _norm_ledger(json.loads(bak.read_text(encoding="utf-8")))
                _warning = "장부가 깨져서 백업(.bak)에서 복구했어요"
            except Exception:
                if f.exists() and not readonly:
                    try:
                        f.rename(LEDGER_DIR / f"apps.json.corrupt-{int(time.time())}")
                    except OSError:
                        pass
                data = None
                _warning = "장부를 읽지 못해 빈 장부로 시작했어요 (data/release/ 확인)"
    LEDGER.clear()
    LEDGER.update(data or _empty())
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        load()


async def _save(data: dict) -> None:
    d = LEDGER_DIR
    f, tmp = d / "apps.json", d / "apps.json.tmp"
    text = json.dumps(data, ensure_ascii=False, indent=1)
    for i in range(4):
        try:
            d.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            if f.exists():
                shutil.copy2(f, d / "apps.json.bak")
            os.replace(tmp, f)
            return
        except PermissionError as e:   # Windows 파일 잠금 — 잠깐 뒤 다시
            if i == 3:
                raise LedgerSaveError(str(e)) from e
            await asyncio.sleep(0.2)
        except OSError as e:
            raise LedgerSaveError(str(e)) from e


def _get_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


async def mutate(fn, persist: bool = True) -> None:
    """fn 은 동기 함수 (git·네트워크 금지). 저장이 실패하면 LEDGER 는 그대로."""
    async with _get_lock():
        new = copy.deepcopy(LEDGER)
        fn(new)
        if persist:
            await _save(new)
        LEDGER.clear()
        LEDGER.update(new)


# ── git ────────────────────────────────────────────────
repo_key = github.repo_key


async def _git(path, *args, timeout=15, ssh=False):
    """(returncode, 출력) — 시간 초과·폴더 없음(WinError 267 등)이면 None."""
    try:
        return await executor.sh(["git", "--no-optional-locks", *(SSH_OPTS if ssh else []), *args],
                                 str(path), timeout=timeout)
    except (RuntimeError, OSError):
        return None


def _last(res):
    """성공한 git 출력의 마지막 줄 (sh 는 stderr 를 합치므로 마지막 줄만)."""
    if not res or res[0] != 0 or not res[1].strip():
        return None
    return res[1].strip().splitlines()[-1].strip()


def _count(res):
    s = _last(res)
    try:
        return int(s) if s is not None else None
    except ValueError:
        return None


# ── 순수 파서 ──────────────────────────────────────────
def parse_pubspec_version(text: str):
    m = re.search(r"^version:\s*(.+)$", text or "", re.M)
    if not m:
        return None
    v = re.sub(r"\s+#.*$", "", m.group(1)).strip().strip("\"'").strip()
    return v or None


def _block(text: str, head: str):
    m = re.search(head + r"\s*\{", text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def parse_gradle(text: str) -> dict:
    text = text or ""
    block = _block(text, r"\bdefaultConfig")
    q = r"""\s*(?:=\s*)?(?:\(\s*)?["']([^"']+)["']"""
    app_id = None
    if block is not None:
        m = re.search(r"\bapplicationId" + q, block)
        app_id = m.group(1) if m else None
    if not app_id:
        m = re.search(r"\bnamespace" + q, text)
        app_id = m.group(1) if m else None
    m = re.search(r"\bversionName" + q, block if block is not None else text)
    return {"app_id": app_id, "version_name": m.group(1) if m else None}


def parse_pbxproj_bundle_id(text: str):
    vals = [v.strip().strip("\"'") for v in re.findall(r"PRODUCT_BUNDLE_IDENTIFIER = (.+?);", text or "")]
    vals = [v for v in vals if v and "Tests" not in v and "$(" not in v]
    return min(vals, key=lambda v: (len(v), v)) if vals else None


def detect_kind(files: set) -> str:
    if "pubspec.yaml" in files:
        return "flutter"
    if "android/" in files or "app/build.gradle" in files or "app/build.gradle.kts" in files:
        return "android"
    if any(re.fullmatch(r"(?:ios/)?[^/]+\.xcodeproj/?", f) for f in files):
        return "ios"
    if "package.json" in files:
        return "web"
    return "other"


def kind_from_language(lang) -> str:
    return {"Dart": "flutter", "Kotlin": "android", "Java": "android", "Swift": "ios",
            "Objective-C": "ios", "JavaScript": "web", "TypeScript": "web"}.get(lang or "", "other")


def platforms_for(kind: str, files: set) -> list:
    if kind == "flutter":
        return [p for p in PLATS if p + "/" in files] or list(PLATS)
    return {"android": ["android"], "ios": ["ios"]}.get(kind, [])


def _list_files(root: Path) -> set:
    """detect_kind 용 상대 경로 — 루트 1단계 + ios/ 1단계 + app/build.gradle(.kts). 폴더는 끝에 '/'."""
    out = set()
    for base in (root, root / "ios"):
        try:
            for e in base.iterdir():
                rel = e.relative_to(root).as_posix()
                out.add(rel + "/" if e.is_dir() else rel)
        except OSError:
            pass
    for g in ("app/build.gradle", "app/build.gradle.kts"):
        if (root / g).is_file():
            out.add(g)
    return out


def _read(p: Path):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_files(root: Path, kind: str, files: set) -> dict:
    """고정 경로만 읽는다 (rglob 금지 — .symlinks·Pods 오검출)."""
    pub = _read(root / "pubspec.yaml")
    code_version = parse_pubspec_version(pub) if pub else None
    gradle = pbx = None
    if kind == "flutter":
        gradle = _read(root / "android/app/build.gradle") or _read(root / "android/app/build.gradle.kts")
        pbx = _read(root / "ios/Runner.xcodeproj/project.pbxproj")
    elif kind == "android":
        gradle = _read(root / "app/build.gradle") or _read(root / "app/build.gradle.kts")
    elif kind == "ios":
        for base in (root, root / "ios"):
            proj = next(iter(sorted(base.glob("*.xcodeproj"))), None) if base.is_dir() else None
            if proj and (pbx := _read(proj / "project.pbxproj")):
                break
    app_id = {"android": None, "ios": None}
    if gradle:
        g = parse_gradle(gradle)
        app_id["android"] = g["app_id"]
        if kind == "android" and not pub:
            code_version = g["version_name"]
    if pbx:
        app_id["ios"] = parse_pbxproj_bundle_id(pbx)
    return {"code_version": code_version, "app_id": app_id}


# ── 판정 · 계산 (순수) ───────────────────────────────────
def _gh_owner(url):
    m = github.GH_RE.search(url or "")
    return m.group(1) if m else None


def auto_non_app_of(app: dict, repo_meta, owner: str):
    meta = repo_meta or {}
    if meta.get("fork"):
        return "fork"
    o = meta.get("owner") or _gh_owner(app.get("github"))
    if o and owner and o.lower() != owner.lower():
        return "foreign"
    if app.get("kind") == "other":
        return "other"
    return None


def visible_of(app: dict) -> bool:
    return app.get("hidden") is False or (app.get("hidden") is None and app.get("auto_non_app") is None)


def _today(now) -> datetime.date:
    return datetime.datetime.fromtimestamp(now).date()


def days_of(app: dict, p: str, now: float):
    st = app["store"][p]
    if st["state"] not in ("in_review", "rejected") or not _valid_date(st["since"]):
        return None
    return (_today(now) - datetime.date.fromisoformat(st["since"])).days


def overdue_of(app: dict, p: str, now: float, warn: dict) -> bool:
    d = days_of(app, p, now)
    return app["store"][p]["state"] == "in_review" and d is not None and d >= warn[p]


def commits_of(app: dict, p: str):
    """기준점 sha 와 같은 sha 로 센 경우만 유효 (스캔 도중 기준점 변경 경합 방지)."""
    sha = app["store"][p]["sha"]
    s = app["scan"]["since"][p]
    return s["count"] if sha and s["sha"] == sha and _int_or_none(s["count"]) is not None else None


def _live_commits(app: dict) -> int:
    return max([c for p in PLATS if app["store"][p]["state"] == "live"
                and (c := commits_of(app, p)) is not None] or [0])


def group_of(app: dict, now: float = None) -> str:
    states = {app["store"][p]["state"] for p in PLATS}
    if "rejected" in states:
        return "rejected"
    if "in_review" in states:
        return "in_review"
    if "live" in states:
        if app["dev"] != "paused" and (app["next"] != "" or app["dev"] == "in_dev" or _live_commits(app) > 0):
            return "update"
        return "live"
    if app["dev"] in ("idea", "in_dev"):
        return "in_dev"
    if app["dev"] == "paused":
        return "paused"
    return "unset"


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def sort_key(app: dict, group: str) -> tuple:
    name = (app.get("name") or "").lower()
    if group in ("rejected", "in_review"):
        dates = [app["store"][p]["since"] for p in PLATS
                 if app["store"][p]["state"] in ("in_review", "rejected") and app["store"][p]["since"]]
        first = min(dates) if dates else None
        return (GROUP_ORDER[group], first is None, first or "", name)
    at = _epoch(app["scan"]["last_commit"]["at"])
    return (GROUP_ORDER[group], at is None, -(at or 0), name)


def _sorted(ledger: dict, now: float) -> list:
    items = [(k, a, group_of(a, now)) for k, a in ledger.get("apps", {}).items()]
    items.sort(key=lambda t: (sort_key(t[1], t[2]), t[0]))
    return items


def todo(ledger: dict, now: float, cfg=None) -> list:
    warn = _warn_days(cfg)
    vis = [(k, a, g) for k, a, g in _sorted(ledger, now) if visible_of(a)]
    out = {"rejected": [], "overdue": [], "remote": [], "no_next": []}
    for k, a, g in vis:
        name = a["name"] or k
        for p in PLATS:
            if a["store"][p]["state"] == "rejected":
                out["rejected"].append({"kind": "rejected", "key": k, "text": f"❌ {name} — {PLAT_LABEL[p]} 심사 거절"})
            if overdue_of(a, p, now, warn):
                out["overdue"].append({"kind": "overdue", "key": k,
                                       "text": f"⏳ {name} — {PLAT_LABEL[p]} 심사 {days_of(a, p, now)}일째 (기준 {warn[p]}일)"})
        if a["scan"]["remote_ahead"] is True and not a["missing"]:
            out["remote"].append({"kind": "remote", "key": k, "text": f"📥 {name} — 원격에 새 커밋"})
        n = _live_commits(a)
        if n > 0 and a["next"] == "" and a["dev"] != "paused" and not a["missing"]:
            out["no_next"].append({"kind": "no_next", "key": k, "text": f"🔄 {name} — 출시 후 커밋 {n}개, 다음 할 일 없음"})
    items = out["rejected"] + out["overdue"] + out["remote"] + out["no_next"]
    unset = sum(1 for _, _, g in vis if g == "unset")
    if unset:
        items.append({"kind": "unclassified", "key": None, "text": f"🗂 미분류 {unset}개 — 일괄 분류"})
    return items


def badge_of(todo_items: list) -> int:
    return sum(1 for t in todo_items if t["kind"] in ("rejected", "overdue"))


def scan_state() -> dict:
    return {"state": "scanning" if _scan_task and not _scan_task.done() else "idle", **copy.deepcopy(_scan)}


def _busy(path) -> bool:
    n = os.path.normcase(str(path))
    return any(r.alive and os.path.normcase(str(r.project or "")) == n for r in list(RUNS.values()))


def _runs_by_path() -> dict:
    last: dict = {}
    for r in list(RUNS.values()):
        k = os.path.normcase(str(r.project or ""))
        if k not in last or r.created > last[k].created:
            last[k] = r
    return last


def snapshot(now: float = None) -> dict:
    """§3.2 응답 — 메모리를 읽기만 한다 (스캔을 시작하지 않음)."""
    _ensure_loaded()
    now = time.time() if now is None else now
    warn = _warn_days()
    by_path = _runs_by_path()
    apps, runs = [], {}
    for k, a, g in _sorted(LEDGER, now):
        live_n = _live_commits(a)
        apps.append({**a, "key": k, "view": {
            "group": g, "visible": visible_of(a),
            "days": {p: days_of(a, p, now) for p in PLATS},
            "overdue": {p: overdue_of(a, p, now, warn) for p in PLATS},
            "commits": {p: commits_of(a, p) for p in PLATS},
            "no_next": live_n > 0 and a["next"] == "" and a["dev"] != "paused" and not a["missing"]}})
        r = by_path.get(os.path.normcase(a["path"])) if a["path"] else None
        if r:
            runs[k] = {"id": r.id, "project": r.project, "title": r.title, "idea": r.idea,
                       "status": display_status(r.status, r.waiters), "alive": r.alive}
    items = todo(LEDGER, now)
    return {"apps": apps, "groups": GROUPS, "todo": items, "badge": badge_of(items),
            "scan": scan_state(), "scanned_at": LEDGER.get("scanned_at"), "warning": _warning,
            "non_app_count": sum(1 for a in apps if not a["view"]["visible"]),
            "hidden_count": sum(1 for a in apps if a["hidden"] is True),
            "runs": runs, "review_warn_days": warn}


# ── 스캔 ───────────────────────────────────────────────
class _ReadError(Exception):
    pass


async def _collect_local(path: str, origin, head, prev) -> dict:
    root = Path(path)
    if not root.is_dir():
        raise _ReadError("폴더가 없어요")

    async def g(*args):
        res = await _git(root, *args)
        if res is None:
            raise _ReadError(f"git {args[0]} 실패 (시간 초과·폴더 없음)")
        return res

    head_sha = _last(await g("rev-parse", "HEAD"))
    branch = _last(await g("rev-parse", "--abbrev-ref", "HEAD")) or ""
    log = _last(await g("log", "-1", "--format=%s%x1f%cI"))
    msg, _, at = (log or "").partition("\x1f")
    st = await g("status", "--porcelain")
    dirty = len([ln for ln in st[1].splitlines() if ln.strip()]) if st[0] == 0 else 0

    files = _list_files(root)
    kind = detect_kind(files)
    facts = _read_files(root, kind, files)

    is_gh = bool(github.GH_RE.search(origin or ""))
    ssh = bool(origin) and not re.match(r"https?://", origin) and not os.path.exists(origin)
    default = (head or {}).get("default_branch") or ""
    remote_sha = (head or {}).get("oid")
    if not default and origin and not is_gh:
        res = await _git(root, "ls-remote", "--symref", "origin", "HEAD", timeout=30, ssh=ssh)
        if res and res[0] == 0:
            m = re.search(r"^ref:\s*refs/heads/(\S+)\s+HEAD", res[1], re.M)
            default = m.group(1) if m else ""
    if not default:
        sym = _last(await _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"))
        default = sym.split("/", 1)[1] if sym and "/" in sym else ""
    if not default and branch != "HEAD":
        default = branch
    if not remote_sha and origin and not is_gh and default:
        res = await _git(root, "ls-remote", "origin", f"refs/heads/{default}", timeout=30, ssh=ssh)
        if res and res[0] == 0:
            m = re.search(r"^([0-9a-f]{40})\s+refs/heads/", res[1], re.M)
            remote_sha = m.group(1) if m else None

    ahead, ahead_n = None, None
    if remote_sha:
        ce = await _git(root, "cat-file", "-e", f"{remote_sha}^{{commit}}")
        if ce is not None and ce[0] != 0:
            ahead = True                  # 로컬에 없는 커밋 → 원격이 앞섬 (개수는 모름)
        elif ce is not None and default:
            ahead_n = _count(await _git(root, "rev-list", "--count", f"refs/heads/{default}..{remote_sha}"))
            ahead = ahead_n > 0 if ahead_n is not None else None

    tag = _last(await _git(root, "describe", "--tags", "--abbrev=0", f"refs/heads/{default}")) if default else None

    since = {}
    for p in PLATS:
        sha = (((prev or {}).get("store") or {}).get(p) or {}).get("sha")
        cnt = None
        if sha and head_sha:
            ce = await _git(root, "cat-file", "-e", f"{sha}^{{commit}}")
            if ce is not None and ce[0] == 0:
                cnt = _count(await _git(root, "rev-list", "--count", f"{sha}..HEAD"))
        since[p] = {"sha": sha, "count": cnt}

    scan = {**_default_scan(), "code_version": facts["code_version"], "app_id": facts["app_id"],
            "head": head_sha, "branch": branch, "default_branch": default,
            "last_commit": {"msg": msg if head_sha else "", "at": (at or None) if head_sha else None},
            "dirty": dirty, "remote_ahead": ahead, "remote_ahead_count": ahead_n,
            "since": since, "tag": tag, "at": time.time()}
    gh_url = (head or {}).get("url")
    if not gh_url and is_gh:
        m = github.GH_RE.search(origin)
        gh_url = f"https://github.com/{m.group(1)}/{m.group(2)}"
    return {"scan": scan, "kind": kind, "platforms": platforms_for(kind, files), "path": str(root),
            "github": gh_url or None, "name": (head or {}).get("name") or root.name}


def _collect_remote(head: dict) -> dict:
    kind = kind_from_language(head.get("language"))
    scan = {**_default_scan(), "head": head.get("oid"), "default_branch": head.get("default_branch") or "",
            "last_commit": {"msg": "", "at": head.get("pushed")}, "remote_ahead": None, "at": time.time()}
    return {"scan": scan, "kind": kind, "platforms": platforms_for(kind, set()), "path": None,
            "github": head.get("url") or None, "name": head.get("name") or ""}


def _one_line(s) -> str:
    return " ".join(str(s).split())[:200]


async def _scan_one(key: str, local, head, persist: bool = True, count: bool = True) -> None:
    try:
        path = (local or {}).get("path")
        if path and _busy(path):   # 실행 중인 작업의 저장소는 건드리지 않음 (이전 값 유지)
            return
        prev = copy.deepcopy(LEDGER.get("apps", {}).get(key))
        _scan["current"] = (head or {}).get("name") or (Path(path).name if path else key)
        facts, err = None, ""
        try:
            if path:
                facts = await _collect_local(path, (local or {}).get("origin"), head, prev)
            elif head:
                facts = _collect_remote(head)
            else:
                return
        except Exception as e:
            err = _one_line(f"{e}" or type(e).__name__)
            _scan["errors"].append(f"{key}: {err}")
        owner = github._owner()
        meta = {"fork": (head or {}).get("fork"), "owner": (local or {}).get("owner")}

        def fn(d):
            apps = d.setdefault("apps", {})
            a = apps.get(key) or _norm_app({})
            if facts is None:
                a["scan"]["error"], a["scan"]["stale"] = err, True
                a["name"] = a["name"] or _scan["current"]
                a["path"] = a["path"] or path
            else:
                a.update(facts)
                a["auto_non_app"] = auto_non_app_of(a, meta, owner)
            a["missing"] = False
            apps[key] = a

        await mutate(fn, persist=persist)
    finally:
        if count:
            _scan["done"] += 1


async def _scan_all(persist: bool = True) -> None:
    _scan.update(done=0, total=0, current="", errors=[])
    gh_ok = False
    try:
        try:
            heads, gh_ok = await github.repo_heads(force=True)
        except Exception:
            heads, gh_ok = [], False
        local = await github.local_index()
        owner = github._owner()
        heads_by_key = {github.repo_key(h.get("url") or f"https://github.com/{owner}/{h['name']}", ""): h for h in heads}
        keys = list(dict.fromkeys([*local, *heads_by_key]))
        _scan["total"] = len(keys)
        sem = asyncio.Semaphore(max(1, int(_cfg("scan_concurrency", 4) or 4)))

        async def one(k):
            async with sem:
                await _scan_one(k, local.get(k), heads_by_key.get(k), persist=persist)

        for k, r in zip(keys, await asyncio.gather(*(one(k) for k in keys), return_exceptions=True)):
            if isinstance(r, BaseException):
                _scan["errors"].append(f"{k}: {_one_line(r)}")

        def fin(d):
            if gh_ok and heads:   # gh 실패·빈 목록이면 GitHub 전용 항목·missing 은 그대로
                cand = set(keys)
                for k, a in d.get("apps", {}).items():
                    a["missing"] = k not in cand
            d["scanned_at"] = time.time()

        await mutate(fin, persist=persist)
    except Exception as e:
        _scan["errors"].append(_one_line(f"스캔 중단: {e}"))
    finally:
        _scan["gh_ok"] = gh_ok
        _scan["current"] = ""


async def scan_start(if_older_min: int | None = None, force: bool = False) -> dict:
    global _scan_task
    _ensure_loaded()
    if _scan_task and not _scan_task.done():
        return {"started": False, "scan": scan_state()}
    if not force and if_older_min is not None:
        at = LEDGER.get("scanned_at")
        if if_older_min <= 0 or (at and time.time() - at < if_older_min * 60):
            return {"started": False, "scan": scan_state()}
    _scan_task = asyncio.create_task(_scan_all())
    return {"started": True, "scan": scan_state()}


async def _rescan(key: str, path: str | None = None) -> None:
    """앱 하나만 백그라운드로 다시 스캔 (기준점 변경·clone 직후). 예외는 삼킨다."""
    try:
        a = LEDGER.get("apps", {}).get(key) or {}
        path = path or a.get("path")
        local = None
        if path:
            origin = _last(await _git(path, "remote", "get-url", "origin"))
            local = {"path": path, "origin": origin, "owner": _gh_owner(origin)}
        heads, _ = await github.repo_heads()
        head = next((h for h in heads if github.repo_key(h.get("url"), "") == key), None)
        await _scan_one(key, local, head, count=False)
    except Exception:
        pass


# ── 사람 필드 수정 ──────────────────────────────────────
async def _head_of(key: str):
    try:
        heads, _ = await github.repo_heads()
    except Exception:
        return None
    return next((h for h in heads if github.repo_key(h.get("url"), "") == key), None)


async def _resolve_baseline(key: str, app: dict, mode: str):
    """→ (sha, pubspec 버전|None). 잠금 밖에서 부른다."""
    path = app.get("path") if app.get("path") and Path(app["path"]).is_dir() else None
    default = app["scan"].get("default_branch") or ""
    sha = None
    if mode == "now":
        sha = (await _head_of(key) or {}).get("oid")
        if not sha and path:
            if default:
                sha = _last(await _git(path, "rev-parse", f"refs/remotes/origin/{default}"))
            sha = sha or _last(await _git(path, "rev-parse", "HEAD"))
        if not sha:
            raise ValueError("PC에 없어서 기준점을 정할 수 없어요")
    else:
        if not path:
            raise ValueError("PC에 없어서 기준점을 정할 수 없어요")
        tag = _last(await _git(path, "describe", "--tags", "--abbrev=0", f"refs/heads/{default or 'HEAD'}"))
        sha = _last(await _git(path, "rev-parse", f"{tag}^{{commit}}")) if tag else None
        if not sha or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("태그가 없어요")
    ver = None
    if path:
        res = await _git(path, "show", f"{sha}:pubspec.yaml")
        if res and res[0] == 0:
            v = parse_pubspec_version(res[1])
            ver = v.split("+")[0] if v else None
    return sha, ver


def _check_text(field: str, v) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{field} 는 글자여야 해요")
    if len(v) > LIMITS[field]:
        raise ValueError(f"{field} 는 {LIMITS[field]}자까지예요")
    return v


async def update(payload: dict) -> dict:
    _ensure_loaded()
    if not isinstance(payload, dict):
        raise ValueError("잘못된 요청이에요")
    extra = set(payload) - {"key", "store", "dev", "next", "notes"}
    if extra:
        raise ValueError(f"바꿀 수 없는 항목이에요: {', '.join(sorted(map(str, extra)))}")
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        raise ValueError("key 가 필요해요")
    app = LEDGER.get("apps", {}).get(key)
    if app is None:
        raise KeyError(key)
    if "dev" in payload and payload["dev"] not in DEV_STATES:
        raise ValueError("개발 상태 값이 잘못됐어요")
    for f in ("next", "notes"):
        if f in payload:
            _check_text(f, payload[f])
    store = payload.get("store", {})
    if not isinstance(store, dict):
        raise ValueError("store 형식이 잘못됐어요")
    for p, sp in store.items():
        if p not in PLATS or not isinstance(sp, dict):
            raise ValueError(f"플랫폼 값이 잘못됐어요: {p}")
        bad = set(sp) - {"state", "since", "version", "baseline"}
        if bad:
            raise ValueError(f"바꿀 수 없는 항목이에요: {', '.join(sorted(map(str, bad)))}")
        if "state" in sp and sp["state"] not in STORE_STATES:
            raise ValueError("스토어 상태 값이 잘못됐어요")
        if "since" in sp and not (sp["since"] is None or sp["since"] == "" or _valid_date(sp["since"])):
            raise ValueError("제출일은 YYYY-MM-DD 로 적어 주세요")
        if "version" in sp:
            _check_text("version", sp["version"])
        if "baseline" in sp and sp["baseline"] not in (None, "now", "tag"):
            raise ValueError("기준점은 지금 코드·최신 태그·없음 중에서 골라 주세요")

    resolved = {}
    for p, sp in store.items():   # git·네트워크는 잠금 밖에서
        if "baseline" in sp:
            resolved[p] = (None, None) if sp["baseline"] is None else await _resolve_baseline(key, app, sp["baseline"])
    today = datetime.date.today().isoformat()
    changed = []

    def fn(d):
        a = d.get("apps", {}).get(key)
        if a is None:
            raise KeyError(key)
        for p, sp in store.items():
            st = a["store"][p]
            old = st["state"]
            if "state" in sp:
                st["state"] = sp["state"]
            if "version" in sp:
                st["version"] = sp["version"].strip()
            if "since" in sp:
                st["since"] = today if sp["since"] == "" else sp["since"]
            elif st["state"] != old and st["state"] != "none":
                st["since"] = today
            if st["state"] == "none":
                st["since"] = None
            if p in resolved:
                sha, ver = resolved[p]
                if sha != st["sha"]:
                    changed.append(p)
                st["sha"] = sha
                if sha and ver and not st["version"]:
                    st["version"] = ver
        for f in ("dev", "next", "notes"):
            if f in payload:
                a[f] = payload[f]

    await mutate(fn)
    if changed:
        asyncio.create_task(_rescan(key))
    return snapshot()


async def hide(key, hidden) -> dict:
    _ensure_loaded()
    if hidden not in (True, False, None) or (hidden is not None and not isinstance(hidden, bool)):
        raise ValueError("hidden 은 true·false·null 중 하나예요")
    if not isinstance(key, str) or key not in LEDGER.get("apps", {}):
        raise KeyError(key)

    def fn(d):
        if key not in d["apps"]:
            raise KeyError(key)
        d["apps"][key]["hidden"] = hidden

    await mutate(fn)
    return snapshot()


async def note_clone(name: str, path: str) -> None:
    """GitHub 저장소를 PC로 받은 직후 — 장부에 경로를 적고 그 앱만 다시 스캔. 예외는 삼킨다."""
    try:
        _ensure_loaded()
        key = f"{github._owner()}/{name}".lower()

        def fn(d):
            if key in d["apps"]:
                d["apps"][key]["path"] = path

        await mutate(fn)
        await _rescan(key, path)   # 장부에 없던 저장소면 이 스캔이 새로 만든다
    except Exception:
        pass


# ── CLI 드라이런 ────────────────────────────────────────
async def dry_run() -> None:
    load(readonly=True)
    await _scan_all(persist=False)
    rows = [("key", "kind", "platforms", "code_version", "app_id(android/ios)", "remote_ahead", "auto_non_app", "error")]
    other = miss = errs = 0
    for k, a in sorted(LEDGER["apps"].items()):
        s = a["scan"]
        ids = f"{s['app_id']['android'] or '-'} / {s['app_id']['ios'] or '-'}"
        rows.append((k, a["kind"], ",".join(a["platforms"]) or "-", s["code_version"] or "-", ids,
                     str(s["remote_ahead"]), a["auto_non_app"] or "-", s["error"] or ""))
        other += a["kind"] == "other"
        miss += bool(a["path"]) and any(s["app_id"][p] is None for p in a["platforms"])
        errs += bool(s["error"])
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for r in rows:
        print(" | ".join(c.ljust(w[i]) for i, c in enumerate(r[:-1])) + " | " + r[-1])
    if _scan["errors"]:
        print("\n".join("! " + e for e in _scan["errors"]))
    print(f"gh_ok={_scan['gh_ok']}")
    print(f"kind=other {other}개, app_id 미검출 {miss}개, 오류 {errs}개")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--dry" in sys.argv[1:]:
        asyncio.run(dry_run())
    else:
        print("사용법: python -m server.release --dry   (장부에 쓰지 않고 스캔 결과만 표로 출력)")
