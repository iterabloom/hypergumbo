# SPDX-License-Identifier: MPL-2.0
"""Tests for the out-of-repo tracker ops journal (durability substrate).

These reproduce the real loss vectors — ``git reset --hard`` reverting a tracked
``.ops`` (the exact bug that lost a human's approval messages) and ``git clean``
deleting an untracked ``.ops`` — and assert :func:`journal.recover` brings the
ops back from the git-invisible journal. The journal write itself is exercised
through the public ``Store`` API so the wiring at both op-write sites (create and
``_append_op``) is covered.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from helpers import make_test_config
from hypergumbo_tracker import journal
from hypergumbo_tracker.store import Store

_INV_FIELDS = {"statement": "test", "root_cause": "test"}

#: Max time recover's union-step hook waits for a concurrent append in the race
#: test. It is a *ceiling*, not a delay the fixed path depends on: under the fix
#: the append is blocked on the flock so this always times out and recover then
#: proceeds; under the bug the append completes immediately and this returns at
#: once. No assertion depends on its precise value, so the test never flakes on it.
_HOOK_WAIT = 0.5


def _git(root: Path, *args: str) -> None:
    git = shutil.which("git") or "git"
    subprocess.run(  # nosec B603 - git resolved via shutil.which
        [git, *args], cwd=root, check=True, capture_output=True, text=True
    )


def _repo_with_ops(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with the tracker ops dir inside it; returns (repo, ops_dir)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "Test")
    ops_dir = repo / ".agent" / "tracker-workspace" / ".ops"
    ops_dir.mkdir(parents=True)
    return repo, ops_dir


def _repo_with_dropped_update(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """A repo where an appended ``satisfied`` update survives in the journal but
    was dropped from the worktree ``.ops`` by ``git reset --hard``.

    ``recover`` must therefore take the per-file lock and rewrite the file in
    place to restore it. Returns ``(repo, ops_dir, ops_file, item_id)``. Relies on
    the autouse ``_isolate_ops_journal`` fixture to pin the journal under a tmp dir
    (so both the Store append and ``recover`` resolve the same journal root).
    """
    repo, ops_dir = _repo_with_ops(tmp_path)
    store = Store(ops_dir, config=make_test_config())
    item_id = store.add(kind="invariant", fields=_INV_FIELDS, title="Locked")
    ops_file = ops_dir / f".{item_id}.ops"
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base (create op)")
    store.update(item_id, set_fields={"status": "satisfied"})
    _git(repo, "reset", "--hard", "HEAD")  # drops the satisfied op from the worktree
    assert "satisfied" not in ops_file.read_text()
    return repo, ops_dir, ops_file, item_id


def test_recover_restores_tracked_ops_reverted_by_reset_hard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """The exact human-hit bug: a tracked ``.ops`` with an uncommitted appended
    op is reverted by ``git reset --hard``, dropping the op; recover re-adds it.
    """
    repo, ops_dir = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "journal"))

    store = Store(ops_dir, config=make_test_config())
    item_id = store.add(kind="invariant", fields=_INV_FIELDS, title="Durable")
    ops_file = ops_dir / f".{item_id}.ops"

    # Commit the create op so the .ops file is TRACKED.
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base (create op)")

    # Append an update op — an uncommitted MODIFICATION to the tracked .ops.
    store.update(item_id, set_fields={"status": "satisfied"})
    assert "satisfied" in ops_file.read_text()

    # The journal mirrored both ops to a git-invisible location.
    jpath = journal.journal_path_for(ops_file)
    assert jpath is not None and jpath.exists()
    jtext = jpath.read_text()
    assert "create" in jtext and "satisfied" in jtext

    # `git reset --hard` reverts the tracked .ops to the create-only commit.
    _git(repo, "reset", "--hard", "HEAD")
    assert "satisfied" not in ops_file.read_text()  # the loss is real

    # recover union-restores the dropped op from the journal.
    result = journal.recover(repo)
    assert str(ops_file.relative_to(repo)) in result.restored
    assert "satisfied" in ops_file.read_text()
    # The recovered item compiles with the restored status.
    recovered = Store(ops_dir, config=make_test_config()).get(item_id)
    assert recovered.status == "satisfied"


def test_recover_recreates_untracked_ops_deleted_by_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """An untracked new-item ``.ops`` deleted by ``git clean -fd`` is recreated."""
    repo, ops_dir = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "journal"))

    store = Store(ops_dir, config=make_test_config())
    item_id = store.add(kind="invariant", fields=_INV_FIELDS, title="Untracked")
    ops_file = ops_dir / f".{item_id}.ops"
    assert ops_file.exists()

    _git(repo, "clean", "-fd")  # deletes the untracked .ops (and its dirs)
    assert not ops_file.exists()

    journal.recover(repo)
    assert ops_file.exists()
    assert Store(ops_dir, config=make_test_config()).get(item_id).title == "Untracked"


def test_recover_is_idempotent_on_clean_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """A worktree already in sync with the journal recovers to a no-op."""
    repo, ops_dir = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "journal"))
    store = Store(ops_dir, config=make_test_config())
    store.add(kind="invariant", fields=_INV_FIELDS, title="Clean")

    result = journal.recover(repo)
    assert result.restored == []  # nothing lost → nothing rewritten


def test_recover_noop_when_no_journal_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """recover on a repo with no journal yet is a harmless no-op."""
    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    result = journal.recover(repo)
    assert result.restored == []


def test_recover_skips_empty_journal_file(tmp_path: Path) -> None:
    """A 0-byte journal file (mirror_op created it via O_CREAT but the write
    failed) carries no op — recover skips it rather than materializing a spurious
    empty worktree ``.ops``."""
    repo, _ops_dir = _repo_with_ops(tmp_path)
    jdir = journal.journal_root() / journal._repo_id(repo.resolve())
    rel = Path(".agent/tracker-workspace/.ops/.INV-empty.ops")
    jfile = jdir / rel
    jfile.parent.mkdir(parents=True, exist_ok=True)
    jfile.write_text("")  # the partial-write-failure shape

    result = journal.recover(repo)

    assert result.restored == []
    assert not (repo / rel).exists()  # no spurious empty worktree file created


def test_journal_path_none_outside_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``.ops`` file not under git has no journal home; mirror is a silent no-op."""
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "journal"))
    monkeypatch.setattr("hypergumbo_tracker.store._find_git_dir", lambda p: None)
    loose = tmp_path / ".WI-x.ops"
    loose.write_text("- op: create\n")
    assert journal.journal_path_for(loose) is None
    journal.mirror_op(loose, "- op: create\n")  # must not raise and must no-op


def test_mirror_op_degrades_when_journal_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """A journal write failure must NOT fail the tracker mutation."""
    repo, ops_dir = _repo_with_ops(tmp_path)
    # Journal root whose parent is a FILE → mkdir raises NotADirectoryError (OSError).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(blocker / "journal"))

    store = Store(ops_dir, config=make_test_config())
    item_id = store.add(kind="invariant", fields=_INV_FIELDS, title="Degrade")
    # The op still succeeded despite the journal write failing.
    assert (ops_dir / f".{item_id}.ops").exists()


def test_mirror_op_degrades_on_non_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """A NON-OSError must also never fail the mutation.

    The journal's default ``journal_root()`` calls ``Path.home()``, which raises
    ``RuntimeError`` (not OSError) when ``$HOME`` is unset and there is no passwd
    entry (containers, minimal cron/CI); ``resolve()`` raises ``RuntimeError`` on
    a symlink loop. A narrow ``except OSError`` would let these escape and crash
    the very ``store.add()`` the best-effort journal exists to protect.
    """
    repo, ops_dir = _repo_with_ops(tmp_path)

    def boom(_ops_filepath: Path) -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(journal, "journal_path_for", boom)
    store = Store(ops_dir, config=make_test_config())
    item_id = store.add(kind="invariant", fields=_INV_FIELDS, title="NonOSError")
    assert (ops_dir / f".{item_id}.ops").exists()


def test_default_journal_root_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env override the journal lands under ~/hypergumbo_lab_notebook."""
    monkeypatch.delenv(journal.JOURNAL_ROOT_ENV, raising=False)
    assert journal.journal_root() == (
        Path.home() / "hypergumbo_lab_notebook" / "tracker-ops-journal"
    )


# ---------------------------------------------------------------------------
# `tracker recover` CLI command
# ---------------------------------------------------------------------------


def test_recover_command_via_main_restores_dropped_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """`tracker recover` (through main dispatch, no --tracker-root → cwd) brings
    back ops dropped by ``git clean``."""
    from hypergumbo_tracker.cli import main

    repo, ops_dir = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "journal"))
    store = Store(ops_dir, config=make_test_config())
    item_id = store.add(kind="invariant", fields=_INV_FIELDS, title="ViaMain")
    ops_file = ops_dir / f".{item_id}.ops"

    _git(repo, "clean", "-fd")
    assert not ops_file.exists()

    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as exc:
        main(["recover"])
    assert exc.value.code == 0
    assert ops_file.exists()


def test_recover_command_reports_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """recover with no journal reports a no-op and exits 0."""
    import argparse

    from hypergumbo_tracker.cli import _cmd_recover

    repo, _ = _repo_with_ops(tmp_path)
    monkeypatch.setenv(journal.JOURNAL_ROOT_ENV, str(tmp_path / "empty"))
    rc = _cmd_recover(argparse.Namespace(tracker_root=str(repo / ".agent")))
    assert rc == 0
    assert "nothing to restore" in capsys.readouterr().err


def test_recover_command_errors_outside_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """recover outside a git repository exits non-zero with a clear message."""
    import argparse

    from hypergumbo_tracker.cli import _cmd_recover

    monkeypatch.setattr("hypergumbo_tracker.store._find_git_dir", lambda p: None)
    rc = _cmd_recover(argparse.Namespace(tracker_root=str(tmp_path)))
    assert rc == 1
    assert "not inside a git repository" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# INV-hakuv: recover serializes its read-union-rewrite with concurrent appends
# ---------------------------------------------------------------------------


def test_recover_takes_per_file_flock_on_worktree_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """recover acquires Store's per-file ``LOCK_EX`` flock on the worktree ``.ops``
    inode before rewriting it, then releases it (INV-hakuv).

    Store appends under ``fcntl.flock(fd, LOCK_EX)`` on the ``.ops`` file's own fd
    (store.py create / ``_append_op``). For recover's whole-file read-union-rewrite
    to serialize against a concurrent append instead of clobbering it, recover must
    take the *same* per-inode lock — this asserts it does and then releases it.
    """
    import fcntl

    repo, _ops_dir, ops_file, _item_id = _repo_with_dropped_update(tmp_path)

    locked: list[tuple[int, int]] = []  # (inode, flock-operation)
    real_flock = fcntl.flock

    def spy(fd: int, operation: int) -> None:
        locked.append((os.fstat(fd).st_ino, operation))
        return real_flock(fd, operation)

    monkeypatch.setattr("fcntl.flock", spy)
    journal.recover(repo)

    assert "satisfied" in ops_file.read_text()  # the op was restored...
    target_ino = ops_file.stat().st_ino
    assert (target_ino, fcntl.LOCK_EX) in locked  # ...under the per-file lock
    assert (target_ino, fcntl.LOCK_UN) in locked  # ...which was then released


def test_recover_rewrites_ops_in_place_preserving_inode(
    tmp_path: Path, mock_agent_uid: None
) -> None:
    """recover restores a dropped op by an IN-PLACE locked rewrite, never an
    atomic ``os.replace`` — which would swap the inode out from under Store's held
    flock and leave a concurrent ``O_APPEND`` writing to a now-unlinked inode.

    A regression guard for the in-place contract: the inode must be identical
    before and after recover rewrites the file.
    """
    repo, _ops_dir, ops_file, _item_id = _repo_with_dropped_update(tmp_path)
    ino_before = ops_file.stat().st_ino  # the inode recover will rewrite

    result = journal.recover(repo)

    assert str(ops_file.relative_to(repo)) in result.restored
    assert "satisfied" in ops_file.read_text()  # restored
    assert ops_file.stat().st_ino == ino_before  # same inode → in place, not os.replace


def test_recover_does_not_clobber_concurrent_store_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_agent_uid: None
) -> None:
    """The INV-hakuv race, made deterministic: a Store append landing while a
    recover is mid-flight is NOT dropped from the worktree.

    A hook inside recover's union step forces the dangerous interleaving — recover
    reads the (stale) worktree, a concurrent ``Store`` append commits a new op,
    then recover rewrites. Without the shared flock, recover's rewrite clobbers the
    concurrent op (the merge was computed from a now-stale read). With the flock,
    the append blocks until recover releases and then lands on top — so BOTH the
    journal-restored op and the concurrent op survive.
    """
    repo, ops_dir, ops_file, item_id = _repo_with_dropped_update(tmp_path)

    recover_read = threading.Event()
    append_done = threading.Event()
    real_union = journal._union_op_blocks

    def hooked_union(target: str, backup: str) -> str:
        merged = real_union(target, backup)  # computed from recover's (stale) read
        recover_read.set()                   # recover has read the worktree .ops
        # Bug: the *unlocked* append completes and sets this at once, so recover
        # proceeds to clobber it. Fix: the append is blocked on the flock recover
        # holds, so this times out — recover finishes, releases, append lands after.
        append_done.wait(timeout=_HOOK_WAIT)
        return merged

    monkeypatch.setattr(journal, "_union_op_blocks", hooked_union)

    append_store = Store(ops_dir, config=make_test_config())
    errors: list[BaseException] = []

    def concurrent_append() -> None:
        try:
            recover_read.wait(timeout=5.0)
            append_store.update(item_id, set_fields={"status": "needs_human_review"})
            append_done.set()
        except BaseException as exc:  # pragma: no cover - surfaced via the assert below
            errors.append(exc)
            append_done.set()  # unblock recover so the test cannot hang

    worker = threading.Thread(target=concurrent_append)
    worker.start()
    journal.recover(repo)
    worker.join(timeout=10.0)

    assert not worker.is_alive()
    assert not errors, errors
    final = ops_file.read_text()
    assert "satisfied" in final           # the journal-restored op
    assert "needs_human_review" in final  # the concurrent append SURVIVED (not clobbered)
