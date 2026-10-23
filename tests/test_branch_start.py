"""작업 시작 브랜치 — 지금 체크아웃된 브랜치가 아니라 저장소 기본 브랜치(main) 기준."""
import asyncio
import shutil
import subprocess

import pytest

from server import executor, runs
from server.runs import Run

if not shutil.which("git"):
    pytest.skip("git 이 없음", allow_module_level=True)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit(cwd, name):
    (cwd / name).write_text("x", encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    monkeypatch.setitem(executor.CONFIG["git"], "require_clean_tree", True)
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "checkout", "-q", "-B", "main")
    _commit(d, "README.md")
    _git(d, "checkout", "-q", "-b", "feat/old")
    _commit(d, "old.txt")                      # HEAD = feat/old (main 보다 1커밋 앞)
    return d


def _prepare(d, branch):
    run = Run(str(d), "idea", {"branch": branch})
    asyncio.run(executor.prepare_branch(run, branch))
    return run


def _branches(d):
    return set(_git(d, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())


def test_default_branch_checked_out_from_old_head(repo):
    run = _prepare(repo, "main")
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _branches(repo) == {"main", "feat/old"}
    assert run.base_branch == "main"


def test_new_branch_forks_from_main_not_head(repo):
    run = _prepare(repo, "feat/new")
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feat/new"
    assert _git(repo, "rev-parse", "feat/new") == _git(repo, "rev-parse", "main")   # feat/old 커밋이 안 섞임
    assert not (repo / "old.txt").exists()
    assert run.base_branch == "main"


def test_existing_branch_just_checked_out(repo):
    _git(repo, "checkout", "-q", "main")
    run = _prepare(repo, "feat/old")
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feat/old"
    assert _branches(repo) == {"main", "feat/old"}
    assert run.base_branch == "main"
