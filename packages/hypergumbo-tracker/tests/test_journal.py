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

import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import make_test_config
from hypergumbo_tracker import journal
from hypergumbo_tracker.store import Store

_INV_FIELDS = {"statement": "test", "root_cause": "test"}


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
