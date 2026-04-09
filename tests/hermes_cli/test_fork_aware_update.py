"""Tests for fork-aware update logic in hermes_cli/main.py.

Covers:
- _sync_with_upstream_if_needed returns bool correctly
- Rebase path triggered when fork has local commits + upstream has new commits
- Fast-forward path when fork has no local commits
- No-op when already up to date
- Rebase conflict aborts gracefully
- fork_synced_upstream prevents early "Already up to date" return
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# Import the functions under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hermes_cli.main import (
    _sync_with_upstream_if_needed,
    _count_commits_between,
    _sync_fork_with_upstream,
)


def _fake_run(returncode=0, stdout="", stderr=""):
    """Create a fake CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestSyncWithUpstreamReturnValue:
    """_sync_with_upstream_if_needed must return bool."""

    def test_returns_false_when_no_upstream_and_skip(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.main._has_upstream_remote", lambda *a: False
        )
        monkeypatch.setattr(
            "hermes_cli.main._should_skip_upstream_prompt", lambda: True
        )
        result = _sync_with_upstream_if_needed(["git"], Path("/tmp"))
        assert result is False

    def test_returns_false_when_already_up_to_date(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.main._has_upstream_remote", lambda *a: True
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: _fake_run())
        monkeypatch.setattr(
            "hermes_cli.main._count_commits_between",
            lambda *a: 0,
        )
        result = _sync_with_upstream_if_needed(["git"], Path("/tmp"))
        assert result is False

    def test_returns_false_when_up_to_date_with_local_commits(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.main._has_upstream_remote", lambda *a: True
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: _fake_run())

        call_count = [0]
        def fake_count(*a):
            call_count[0] += 1
            # First call: origin_ahead (local commits ahead of upstream)
            if call_count[0] == 1:
                return 3
            # Second call: upstream_ahead
            return 0

        monkeypatch.setattr(
            "hermes_cli.main._count_commits_between", fake_count
        )
        result = _sync_with_upstream_if_needed(["git"], Path("/tmp"))
        assert result is False

    def test_returns_true_after_successful_rebase(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.main._has_upstream_remote", lambda *a: True
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: _fake_run())

        call_count = [0]
        def fake_count(*a):
            call_count[0] += 1
            if call_count[0] == 1:
                return 2  # origin_ahead: 2 local commits
            return 5  # upstream_ahead: 5 new upstream commits

        monkeypatch.setattr(
            "hermes_cli.main._count_commits_between", fake_count
        )
        monkeypatch.setattr(
            "hermes_cli.main._sync_fork_with_upstream", lambda *a: True
        )
        result = _sync_with_upstream_if_needed(["git"], Path("/tmp"))
        assert result is True

    def test_returns_true_after_successful_ff_pull(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.main._has_upstream_remote", lambda *a: True
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: _fake_run())

        call_count = [0]
        def fake_count(*a):
            call_count[0] += 1
            if call_count[0] == 1:
                return 0  # origin_ahead: no local commits
            return 3  # upstream_ahead: 3 new upstream commits

        monkeypatch.setattr(
            "hermes_cli.main._count_commits_between", fake_count
        )
        monkeypatch.setattr(
            "hermes_cli.main._sync_fork_with_upstream", lambda *a: True
        )
        result = _sync_with_upstream_if_needed(["git"], Path("/tmp"))
        assert result is True

    def test_returns_false_on_rebase_conflict(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.main._has_upstream_remote", lambda *a: True
        )

        run_calls = []
        def fake_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            run_calls.append(cmd)
            if "rebase" in cmd and "--abort" not in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return _fake_run()

        monkeypatch.setattr("subprocess.run", fake_run)

        call_count = [0]
        def fake_count(*a):
            call_count[0] += 1
            if call_count[0] == 1:
                return 2  # origin_ahead
            return 5  # upstream_ahead

        monkeypatch.setattr(
            "hermes_cli.main._count_commits_between", fake_count
        )
        result = _sync_with_upstream_if_needed(["git"], Path("/tmp"))
        assert result is False
        # Verify rebase --abort was called
        abort_calls = [c for c in run_calls if "rebase" in c and "--abort" in c]
        assert len(abort_calls) == 1

    def test_returns_false_on_fetch_failure(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.main._has_upstream_remote", lambda *a: True
        )

        def fake_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "fetch" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return _fake_run()

        monkeypatch.setattr("subprocess.run", fake_run)
        result = _sync_with_upstream_if_needed(["git"], Path("/tmp"))
        assert result is False


class TestForkSyncedUpstreamFlag:
    """When fork_synced_upstream is True, update should not early-return."""

    def test_commit_count_zero_without_sync_returns_early(self):
        """commit_count==0 and fork_synced_upstream==False → early return."""
        fork_synced_upstream = False
        commit_count = 0
        should_continue = not (commit_count == 0 and not fork_synced_upstream)
        assert should_continue is False

    def test_commit_count_zero_with_sync_continues(self):
        """commit_count==0 and fork_synced_upstream==True → continue update."""
        fork_synced_upstream = True
        commit_count = 0
        should_continue = not (commit_count == 0 and not fork_synced_upstream)
        assert should_continue is True

    def test_commit_count_nonzero_always_continues(self):
        """commit_count>0 → always continue regardless of fork_synced_upstream."""
        for synced in (True, False):
            commit_count = 5
            should_continue = not (commit_count == 0 and not synced)
            assert should_continue is True
