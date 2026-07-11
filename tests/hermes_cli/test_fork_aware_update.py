import subprocess
from pathlib import Path

import pytest

from hermes_cli import main


def _completed():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


@pytest.fixture
def upstream_remote(monkeypatch):
    monkeypatch.setattr(main, "_has_upstream_remote", lambda *args: True)


def test_up_to_date_fork_returns_false(monkeypatch, upstream_remote):
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: _completed())
    monkeypatch.setattr(main, "_count_commits_between", lambda *args: 0)

    assert main._sync_with_upstream_if_needed(["git"], Path("/tmp")) is False


def test_diverged_fork_rebases_and_pushes(monkeypatch, upstream_remote):
    counts = iter([3, 5])
    calls = []
    monkeypatch.setattr(main, "_count_commits_between", lambda *args: next(counts))
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _completed(),
    )
    monkeypatch.setattr(main, "_sync_fork_with_upstream", lambda *args: True)

    changed = main._sync_with_upstream_if_needed(["git"], Path("/tmp"))

    assert changed is True
    assert ["git", "rebase", "upstream/main"] in calls


def test_rebase_conflict_is_aborted(monkeypatch, upstream_remote):
    counts = iter([2, 4])
    calls = []
    monkeypatch.setattr(main, "_count_commits_between", lambda *args: next(counts))

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-2:] == ["rebase", "upstream/main"]:
            raise subprocess.CalledProcessError(1, cmd)
        return _completed()

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    changed = main._sync_with_upstream_if_needed(["git"], Path("/tmp"))

    assert changed is False
    assert ["git", "rebase", "--abort"] in calls


def test_non_diverged_fork_fast_forwards(monkeypatch, upstream_remote):
    counts = iter([0, 4])
    calls = []
    monkeypatch.setattr(main, "_count_commits_between", lambda *args: next(counts))
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _completed(),
    )
    monkeypatch.setattr(main, "_sync_fork_with_upstream", lambda *args: True)

    changed = main._sync_with_upstream_if_needed(["git"], Path("/tmp"))

    assert changed is True
    assert ["git", "pull", "--ff-only", "upstream", "main"] in calls


def test_fetch_failure_returns_false(monkeypatch, upstream_remote):
    def fake_run(cmd, **kwargs):
        if "fetch" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return _completed()

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    assert main._sync_with_upstream_if_needed(["git"], Path("/tmp")) is False
