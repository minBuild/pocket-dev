"""서버 폴더(별도 clone) 자동 반영 — executor.self_deploy 와 merge 경로 연결 (임시 git 저장소로)."""
import asyncio
import shutil
import subprocess

import pytest

from server import executor, github, pipeline, runs
from server.runs import Run

if not shutil.which("git"):
    pytest.skip("git 이 없음", allow_module_level=True)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit(cwd, name, text="x"):
    path = cwd / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)


@pytest.fixture(autouse=True)
def guard(monkeypatch, tmp_path):
    monkeypatch.setattr(executor, "ROOT", tmp_path / "noop")   # 실수로 진짜 서버 폴더에서 pull 하지 않게
    monkeypatch.setitem(executor.CONFIG["git"], "self_deploy", True)


@pytest.fixture
def repos(monkeypatch, tmp_path):
    origin, dev, live = tmp_path / "origin.git", tmp_path / "dev", tmp_path / "live"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    dev.mkdir()
    _git(dev, "init", "-q")
    _git(dev, "checkout", "-q", "-B", "main")
    _commit(dev, "README.md")
    _git(dev, "remote", "add", "origin", str(origin))
    _git(dev, "push", "-q", "-u", "origin", "main")
    _git(tmp_path, "clone", "-q", "-b", "main", str(origin), str(live))
    monkeypatch.setattr(executor, "ROOT", live)
    return dev, live


def _deploy(dev, branch="main"):
    return asyncio.run(executor.self_deploy(str(dev), branch))


def _push(dev, name):
    _commit(dev, name)
    _git(dev, "push", "-q", "origin", "main")


# P0-1
def test_same_root_or_off_calls_nothing(monkeypatch, tmp_path):
    calls = []

    async def fake_sh(args, cwd, check=False, timeout=600):
        calls.append(args)
        return 0, ""
    monkeypatch.setattr(executor, "sh", fake_sh)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(executor, "ROOT", root)
    assert asyncio.run(executor.self_deploy(str(root), "main")) is None
    monkeypatch.setitem(executor.CONFIG["git"], "self_deploy", False)
    assert asyncio.run(executor.self_deploy(str(tmp_path / "other"), "main")) is None
    assert calls == []


# P0-2
def test_origin_mismatch(repos, tmp_path):
    dev, live = repos
    _git(tmp_path, "init", "-q", "--bare", str(tmp_path / "other.git"))
    _git(dev, "remote", "set-url", "origin", str(tmp_path / "other.git"))
    assert _deploy(dev) is None


def test_both_without_origin(repos):
    dev, live = repos
    _git(dev, "remote", "remove", "origin")
    _git(live, "remote", "remove", "origin")
    assert _deploy(dev) is None


@pytest.mark.parametrize("move", [["checkout", "-q", "-b", "other"], ["checkout", "-q", "--detach"]])
def test_branch_mismatch(repos, move):
    dev, live = repos
    _git(live, *move)
    head = _git(live, "rev-parse", "HEAD")
    _push(dev, "a.txt")
    assert _deploy(dev) is None
    assert _git(live, "rev-parse", "HEAD") == head


# P0-3
def test_pull_success_then_none(repos):
    dev, live = repos
    _push(dev, "a.txt")
    out = _deploy(dev)
    assert "🛰" in out and "→" in out
    assert _git(live, "rev-parse", "HEAD") == _git(dev, "rev-parse", "HEAD")
    assert _deploy(dev) is None


# P0-4
def test_diverged_pull_fails(repos):
    dev, live = repos
    _commit(live, "local.txt")
    head = _git(live, "rev-parse", "HEAD")
    _push(dev, "a.txt")
    out = _deploy(dev)
    assert out.startswith("⚠️")
    assert _git(live, "rev-parse", "HEAD") == head


def test_pull_timeout_is_warning(repos, monkeypatch):
    dev, live = repos
    _push(dev, "a.txt")
    real = executor.sh

    async def slow(args, cwd, check=False, timeout=600):
        if "pull" in args:
            raise RuntimeError("git pull 이(가) 120초 안에 끝나지 않아 중단했습니다")
        return await real(args, cwd, check, timeout)
    monkeypatch.setattr(executor, "sh", slow)
    out = _deploy(dev)
    assert out.startswith("⚠️")


# P0-5
def test_server_change_adds_restart_hint(repos):
    dev, live = repos
    _push(dev, "server/x.py")
    assert "🔄" in _deploy(dev)


def test_web_change_no_restart_hint(repos):
    dev, live = repos
    _push(dev, "web/x.js")
    out = _deploy(dev)
    assert "🛰" in out and "🔄" not in out


# P1-6
@pytest.mark.parametrize("url", [
    "https://github.com/minBuild/Pocket-Dev.git",
    "git@github.com:minBuild/pocket-dev.git",
    "ssh://git@github.com/minBuild/pocket-dev.git",
    "https://github.com/minBuild/pocket-dev/",
    "https://user:tok@github.com/minBuild/pocket-dev",
])
def test_repo_key_github(url):
    assert executor._repo_key(url) == "minbuild/pocket-dev"


def test_repo_key_local_paths():
    assert executor._repo_key("C:\\repos\\a.git") == executor._repo_key("C:/repos/a")
    assert executor._repo_key("C:/repos/a") != executor._repo_key("C:/repos/b")


# P1-7
def test_merge_calls_self_deploy_with_base(monkeypatch, tmp_path):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    seen, web = [], []

    async def fake_merge(run, branch):
        return {"text": "t", "base": "main", "pushed": False}

    async def fake_deploy(project, branch):
        seen.append((project, branch))
        return "🛰 x"

    async def fake_web(project):
        web.append(project)
        return None
    monkeypatch.setattr(executor, "merge_into_default", fake_merge)
    monkeypatch.setattr(executor, "self_deploy", fake_deploy)
    monkeypatch.setattr(github, "web_url", fake_web)
    run = Run(str(tmp_path), "idea", {"branch": "feat/x"})
    asyncio.run(pipeline.merge(run, "c1"))
    assert seen == [(str(tmp_path), "main")]
    assert any(e["type"] == "log" and e.get("text") == "🛰 x" and e.get("reply_to") == "c1" for e in run.events)
    assert web == []
