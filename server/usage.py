"""토큰 사용량 — 오늘 / 최근 7일.

- Pocket-Dev: 서버가 부른 모든 호출(토론·리뷰·비서·구현)을 data/usage.jsonl 에 기록
- 이 PC 전체: Claude Code 가 ~/.claude/projects 에 남기는 대화 기록의 토큰 수(내용은 안 읽음)
  + 기록을 남기지 않는 Pocket-Dev 의 답변 호출(--no-session-persistence)
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR

LOG = DATA_DIR / "usage.jsonl"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_cache: dict = {"at": 0.0, "data": None}


def _tokens(u: dict) -> dict:
    return {"input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
            "cache_read": int(u.get("cache_read_input_tokens") or 0),
            "cache_write": int(u.get("cache_creation_input_tokens") or 0)}


def record(source: str, model: str, usage: dict) -> None:
    """source: generate(토론·리뷰·비서 등) | executor(Claude Code 구현)"""
    if not usage:
        return
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "source": source, "model": model, **_tokens(usage)}) + "\n")


def _family(model: str) -> str:
    m = (model or "").lower()
    return next((k for k in ("opus", "sonnet", "haiku", "fable") if k in m), "기타")


def _bucket() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "by_model": {}}


def _add(b: dict, model: str, t: dict) -> None:
    for k in ("input", "output", "cache_read", "cache_write"):
        b[k] += t[k]
    m = b["by_model"].setdefault(_family(model), {"output": 0, "total": 0})
    m["output"] += t["output"]
    m["total"] += sum(t.values())


def _claude_code_logs(since: float):
    """~/.claude/projects/**/*.jsonl 의 assistant 메시지 usage (같은 메시지 중복 제거)."""
    seen = set()
    if not CLAUDE_PROJECTS.exists():
        return
    for path in CLAUDE_PROJECTS.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < since:
                continue
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    msg = d.get("message") or {}
                    usage = msg.get("usage")
                    if d.get("type") != "assistant" or not usage:
                        continue
                    key = (msg.get("id"), d.get("requestId"))
                    if key in seen:
                        continue
                    seen.add(key)
                    ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
                    if ts >= since:
                        yield ts, msg.get("model", ""), _tokens(usage)
        except OSError:
            continue


def summary() -> dict:
    if _cache["data"] and time.time() - _cache["at"] < 60:
        return _cache["data"]
    now = datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    week = (now - timedelta(days=7)).timestamp()
    data = {"pocketdev": {"today": _bucket(), "week": _bucket()},
            "pc": {"today": _bucket(), "week": _bucket()}, "updated": time.time()}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = {k: e.get(k, 0) for k in ("input", "output", "cache_read", "cache_write")}
            for span, start in (("today", today), ("week", week)):
                if e["ts"] >= start:
                    _add(data["pocketdev"][span], e["model"], t)
                    if e["source"] == "generate":  # Claude Code 기록에 안 남는 호출만 PC 전체에 더함 (구현은 기록에 남음)
                        _add(data["pc"][span], e["model"], t)
    for ts, model, t in _claude_code_logs(week):
        for span, start in (("today", today), ("week", week)):
            if ts >= start:
                _add(data["pc"][span], model, t)
    _cache.update(at=time.time(), data=data)
    return data
