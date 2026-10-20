"""로그인 (아이디 + 비밀번호 + 액세스 토큰) · 세션 쿠키 · 실패 시 공개 주소 잠금.

- 비밀번호는 PBKDF2 해시로만 .env 에 저장 (setup_login.bat 으로 설정)
- 로그인 실패가 max_failures 번 쌓이면 공개 주소(Funnel)를 잠근다. 서버를 재시작해도 유지.
- 잠금 해제: 사설망(Tailscale·집 와이파이)으로 로그인해서 해제하거나 unlock.bat 실행
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from .config import CONFIG, DATA_DIR

STATE_FILE = DATA_DIR / "auth_state.json"
HANDOFF_FILE = DATA_DIR / "session_handoff.json"
SESSION_COOKIE = "pd_session"
# 세션 해시 → 만료 시각. 메모리에만 보관해서 서버를 새로 켜면 다시 로그인하고,
# 화면의 '서버 재시작' 때만 handoff 파일로 다음 프로세스에 넘긴다 (파일에는 해시만 — 쿠키 값은 남지 않음).
_sessions: dict[str, float] = {}


def _key(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def _cfg(key: str, default):
    return (CONFIG.get("auth") or {}).get(key, default)


def max_failures() -> int:
    return int(_cfg("max_failures", 3))


# ── 비밀번호 해시 ──────────────────────────────────────────────
def hash_password(password: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _creds() -> tuple[str, str, str]:
    return (os.environ.get("LOGIN_ID", ""), os.environ.get("LOGIN_PASSWORD_HASH", ""),
            os.environ.get("ACCESS_TOKEN", ""))


def configured() -> bool:
    return all(_creds())


def check_token(token: str) -> bool:
    expected = _creds()[2]
    return bool(expected) and hmac.compare_digest(token.encode(), expected.encode())


# ── 잠금 상태 (파일 — unlock.bat 같은 다른 프로세스와 공유) ─────────
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"failures": 0, "locked": False, "log": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def is_locked() -> bool:
    return bool(load_state().get("locked"))


def unlock() -> None:
    state = load_state()
    state.update(failures=0, locked=False, unlocked_at=time.time())
    _save_state(state)


# ── 로그인 ───────────────────────────────────────────────────
def attempt_login(login_id: str, password: str, token: str, client: str, via: str) -> dict:
    """{"ok": bool, "session"?: str, "locked": bool, "remaining": int}"""
    state = load_state()
    if via == "public" and state.get("locked"):
        return {"ok": False, "locked": True, "remaining": 0}

    exp_id, exp_hash, exp_token = _creds()
    # 어느 항목이 틀렸는지 시간 차로도 드러나지 않게 셋 다 검사
    id_ok = bool(exp_id) and hmac.compare_digest(login_id.strip().lower().encode(), exp_id.lower().encode())
    pw_ok = bool(exp_hash) and verify_password(password, exp_hash)
    token_ok = check_token(token)

    if id_ok and pw_ok and token_ok:
        if not state.get("locked"):
            state["failures"] = 0
            _save_state(state)
        return {"ok": True, "session": new_session(), "locked": bool(state.get("locked")),
                "remaining": max_failures()}

    state["failures"] = state.get("failures", 0) + 1
    state["log"] = (state.get("log", []) + [{"at": time.time(), "client": client, "via": via}])[-20:]
    if state["failures"] >= max_failures() and not state.get("locked"):
        state.update(locked=True, locked_at=time.time())
    _save_state(state)
    return {"ok": False, "locked": bool(state["locked"]),
            "remaining": max(0, max_failures() - state["failures"])}


# ── 세션 ────────────────────────────────────────────────────
def session_max_age() -> int:
    return int(float(_cfg("session_hours", 12)) * 3600)


def new_session() -> str:
    sid = secrets.token_urlsafe(32)
    _sessions[_key(sid)] = time.time() + session_max_age()
    return sid


def valid_session(sid: Optional[str]) -> bool:
    if not sid:
        return False
    expires = _sessions.get(_key(sid))
    if not expires:
        return False
    if expires < time.time():
        _sessions.pop(_key(sid), None)
        return False
    return True


def end_session(sid: Optional[str]) -> None:
    if sid:
        _sessions.pop(_key(sid), None)


def save_handoff() -> None:
    """서버 재시작 직전: 살아 있는 세션(해시·만료 시각)을 다음 프로세스에 넘긴다."""
    now = time.time()
    HANDOFF_FILE.write_text(json.dumps({"written": now, "sessions": {k: v for k, v in _sessions.items() if v > now}}),
                            encoding="utf-8")


def load_handoff() -> None:
    """시작할 때: 2분 안에 쓰인 handoff 만 받아들이고 바로 지운다 (오래된 파일로 로그인이 살아나지 않게)."""
    try:
        data = json.loads(HANDOFF_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    finally:
        HANDOFF_FILE.unlink(missing_ok=True)
    if time.time() - data.get("written", 0) < 120:
        _sessions.update({k: float(v) for k, v in data.get("sessions", {}).items()})
