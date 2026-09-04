# SPDX-License-Identifier: MPL-2.0
"""`tracker reconcile` — one command for flush + fast-forward + journal restore.

WHY THIS EXISTS. After ``auto-pr`` merges a PR, its post-merge ``git pull`` can
abort because pending ``.ops`` in the worktree collide with the same paths in the
incoming commit. That is git correctly refusing to clobber, not a durability bug
— the ops are safe in the out-of-repo journal — but it leaves the repo behind
``origin/dev`` with a tree no plain pull can advance. The documented way out was
three commands (``tracker sync`` -> ``git pull --ff-only`` -> ``tracker
recover``): two tracker commands with a raw git command between them, none of
which reports whether the next is still needed. It lost to improvisation the
first time it was needed under pressure, by an operator who had just quoted it
correctly.

WHAT THESE TESTS PIN. Each step runs only when actually needed; the whole thing
is a clean no-op on a healthy repo (so it is safe to run reflexively, which is
the only way it gets run at all); it refuses outright while ``auto-pr`` holds
``.git/PR_PENDING``; a failed flush does NOT proceed to a fast-forward; and it
never leaves the ``tracker-recover-disabled`` marker behind — that marker is
``do_sync``'s to set around its own fetch, and a leak silently disables ops
self-healing with nothing to detect it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import make_test_config
from hypergumbo_tracker import journal
from hypergumbo_tracker.store import Store

_INV_FIELDS = {"statement": "s", "root_cause": "rc"}


#: Resolved to an absolute path so the S607 partial-executable-path rule does
#: not fire, matching how test_journal.py invokes the VCS.
_VCS = shutil.which("git") or "git"


def _run(root: Path, *args: str) -> None:
    subprocess.run(  # nosec B603 - binary resolved via shutil.which
        [_VCS, *args], cwd=root, check=True, capture_output=True, text=True
    )


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "init", "-q", "-b", "dev")
    _run(repo, "config", "user.name", "T")
    _run(repo, "config", "user.email", "t@example.com")


def _repo_with_ops(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with an initialised tracker ops dir and one commit."""
    repo = tmp_path / "repo"
    _init(repo)
    ops_dir = repo / ".agent" / "tracker-workspace" / ".ops"
    ops_dir.mkdir(parents=True)
    (repo / "README").write_text("x\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "init")
    return repo, ops_dir


def _ns(root: Path, **over: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "tracker_root": str(root / ".agent"),
        "dry_run": False,
        "base_branch": "dev",
        "timeout": 300,
    }
    base.update(over)
    return argparse.Namespace(**base)


class _Pre:
    """Stand-in for :class:`hypergumbo_tracker.sync.PreflightResult`."""

    def __init__(self, ok: bool = True, changed: list[str] | None = None) -> None:
        self.ok = ok
        self.error = None if ok else "preflight said no"
        self.changed_files = changed or []


class _SyncOK:
    success = True
    pr_number = 42
    files_synced = 3
    error = None
    exit_code = 0


class _SyncFail:
    success = False
    pr_number = 0
    files_synced = 0
    error = "CI timed out"
    exit_code = 2


def _no_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hypergumbo_tracker.sync.preflight_check", lambda r: _Pre(changed=[])
    )


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_reconcile_errors_outside_a_git_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No git dir means no journal and no remote — say so rather than guess."""
    from hypergumbo_tracker.cli import _cmd_reconcile

    monkeypatch.setattr("hypergumbo_tracker.store._find_git_dir", lambda p: None)
    rc = _cmd_reconcile(_ns(tmp_path))
    assert rc == 1
    assert "not inside a git repository" in capsys.readouterr().err


def test_reconcile_refuses_while_auto_pr_holds_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An auto-pr in flight is the one state where this must not run.

    ``auto-pr`` copies the ops dirs aside and restores them around its own
    rebase; syncing or fast-forwarding underneath it is how the two end up
    fighting over the same files.
    """
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    (repo / ".git" / "PR_PENDING").write_text("")
    rc = _cmd_reconcile(_ns(repo))
    assert rc == 1
    err = capsys.readouterr().err
    assert "PR_PENDING" in err
    assert "auto-pr" in err


# ---------------------------------------------------------------------------
# Step 1 — flush
# ---------------------------------------------------------------------------


def test_reconcile_is_a_clean_noop_on_a_healthy_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing pending, nothing to restore: exit 0, and name each skipped step.

    This is the property that makes it safe to run reflexively after every
    ``auto-pr``.
    """
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    _no_flush(monkeypatch)
    rc = _cmd_reconcile(_ns(repo))
    assert rc == 0
    err = capsys.readouterr().err
    assert "nothing to flush" in err
    assert "nothing to restore" in err


def test_reconcile_flushes_pending_ops_through_do_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pending ops flush via ``do_sync`` — not via a synthetic mutation.

    ``do_sync`` takes no threshold argument (the 80-line gate lives in its
    caller), so the flush needs no canned op to trip a counter, and the
    append-only audit log stays free of entries that mean nothing.
    """
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    monkeypatch.setattr(
        "hypergumbo_tracker.sync.preflight_check", lambda r: _Pre(changed=["a.ops"])
    )
    seen: dict[str, object] = {}

    def _fake_sync(**kw: object) -> _SyncOK:
        seen.update(kw)
        return _SyncOK()

    monkeypatch.setattr("hypergumbo_tracker.sync.do_sync", _fake_sync)
    rc = _cmd_reconcile(_ns(repo))
    assert rc == 0
    assert seen["base_branch"] == "dev"
    assert "flushed 3 file(s) via PR #42" in capsys.readouterr().err


def test_reconcile_stops_when_the_flush_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed flush must not be followed by a fast-forward.

    Pulling on top of ops that never reached the remote is precisely the
    collision this command exists to prevent, so propagate ``do_sync``'s own
    exit code and stop.
    """
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setattr(
        "hypergumbo_tracker.sync.preflight_check", lambda r: _Pre(changed=["a.ops"])
    )
    monkeypatch.setattr("hypergumbo_tracker.sync.do_sync", lambda **kw: _SyncFail())
    pulled: list[str] = []
    monkeypatch.setattr(
        "hypergumbo_tracker.cli._reconcile_fast_forward",
        lambda root: pulled.append(str(root)),
    )
    rc = _cmd_reconcile(_ns(repo))
    assert rc == 2
    assert pulled == []
    assert "CI timed out" in capsys.readouterr().err


def test_reconcile_treats_a_refusing_preflight_as_nothing_to_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A preflight refusal is reported, and the later steps still run.

    The fast-forward and the journal restore are independently useful; a
    preflight that refuses (no remote, wrong branch) must not strand a repo
    whose only real need was its journal back.
    """
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    monkeypatch.setattr(
        "hypergumbo_tracker.sync.preflight_check", lambda r: _Pre(ok=False)
    )
    rc = _cmd_reconcile(_ns(repo))
    assert rc == 0
    err = capsys.readouterr().err
    assert "preflight said no" in err
    assert "nothing to restore" in err


# ---------------------------------------------------------------------------
# Step 2 — fast-forward
# ---------------------------------------------------------------------------


def test_reconcile_reports_a_fast_forward_that_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repo with no remote cannot fast-forward; report git's own words.

    Silence is the worst outcome here — the operator would believe the repo
    advanced when it did not.
    """
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    _no_flush(monkeypatch)
    rc = _cmd_reconcile(_ns(repo))
    assert rc == 0
    assert "fast-forward did not run" in capsys.readouterr().err


def test_reconcile_fast_forwards_a_clone_that_is_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real thing, against real git: a clone behind its origin advances."""
    from hypergumbo_tracker.cli import _cmd_reconcile

    upstream, _ = _repo_with_ops(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(  # nosec B603 - binary resolved via shutil.which
        [_VCS, "clone", "-q", str(upstream), str(clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run(clone, "config", "user.name", "T")
    _run(clone, "config", "user.email", "t@example.com")
    (upstream / "NEW").write_text("y\n")
    _run(upstream, "add", "-A")
    _run(upstream, "commit", "-q", "-m", "second")

    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    _no_flush(monkeypatch)
    rc = _cmd_reconcile(_ns(clone))
    assert rc == 0
    assert "fast-forwarded" in capsys.readouterr().err
    assert (clone / "NEW").exists()


def test_reconcile_leaves_no_recover_disabled_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fast-forward must run with ops self-healing LIVE.

    ``do_sync`` sets ``tracker-recover-disabled`` around its own fetch on
    purpose. This command must not: the post-checkout and reference-transaction
    hooks are exactly what should restore ops the pull disturbs, and a leaked
    marker silently disables that with nothing to detect it.
    """
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    _no_flush(monkeypatch)
    _cmd_reconcile(_ns(repo))
    assert not (repo / ".git" / "tracker-recover-disabled").exists()


# ---------------------------------------------------------------------------
# Step 3 — journal restore
# ---------------------------------------------------------------------------


def test_reconcile_restores_ops_the_worktree_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_agent_uid: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end on the motivating shape: ops dropped, then brought back by the
    third step without a separate invocation."""
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, ops_dir = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "journal"))
    store = Store(ops_dir, config=make_test_config())
    item_id = store.add(kind="invariant", fields=_INV_FIELDS, title="Dropped")
    ops_file = ops_dir / f".{item_id}.ops"
    _run(repo, "clean", "-fdq")
    assert not ops_file.exists()

    _no_flush(monkeypatch)
    rc = _cmd_reconcile(_ns(repo))
    assert rc == 0
    assert ops_file.exists()
    assert "restored 1 ops file(s)" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def test_reconcile_dry_run_neither_syncs_nor_pulls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--dry-run`` reports every step and performs none of them."""
    from hypergumbo_tracker.cli import _cmd_reconcile

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    monkeypatch.setattr(
        "hypergumbo_tracker.sync.preflight_check", lambda r: _Pre(changed=["a.ops"])
    )

    def _boom(**kw: object) -> None:
        raise AssertionError("do_sync must not run under --dry-run")

    monkeypatch.setattr("hypergumbo_tracker.sync.do_sync", _boom)
    pulled: list[str] = []
    monkeypatch.setattr(
        "hypergumbo_tracker.cli._reconcile_fast_forward",
        lambda root: pulled.append(str(root)),
    )
    rc = _cmd_reconcile(_ns(repo, dry_run=True))
    assert rc == 0
    assert pulled == []
    err = capsys.readouterr().err
    assert "would flush 1 file(s)" in err
    assert "would fast-forward" in err


# ---------------------------------------------------------------------------
# main() dispatch, and the deprecation of the bare `recover`
# ---------------------------------------------------------------------------


def test_reconcile_is_reachable_through_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tracker reconcile` dispatches like any other subcommand."""
    from hypergumbo_tracker.cli import main

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    _no_flush(monkeypatch)
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as exc:
        main(["reconcile"])
    assert exc.value.code == 0


def test_recover_still_works_but_points_at_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`recover` is deprecated, not removed.

    It stays as the primitive ``reconcile`` calls and as the hooks' entry point,
    but on its own it restores the journal and leaves the repo behind — which is
    how an operator ends up improvising the other two steps. Say so every time,
    without changing the exit code.
    """
    from hypergumbo_tracker.cli import _cmd_recover

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    rc = _cmd_recover(_ns(repo))
    assert rc == 0
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    assert "reconcile" in err
