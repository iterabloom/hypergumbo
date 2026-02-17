# SPDX-License-Identifier: MPL-2.0
"""Git integration tests for hypergumbo-tracker.

Tests that the merge=union strategy works correctly for concurrent
branch edits, rebase operations, and cross-branch duplicate creation.
These tests use real git repos in tmp_path to validate the CRDT-like
properties of the append-only ops format.

Design rationale:
- merge=union appends unique lines during merge — our nonce-per-line
  strategy ensures each line is globally unique, so union preserves
  all ops from both branches.
- Rebase replays commits, which re-appends ops files. Since each op
  has a unique nonce, the rebased content is preserved without duplicates.
- Cross-branch duplicate creation (both branches add the same item)
  produces two create ops. compile_ops() handles this by using the
  lowest-clock create and applying subsequent updates.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_tracker.models import TrackerConfig
from hypergumbo_tracker.store import (
    Store,
    _parse_ops_file,
    compile_ops,
)


pytestmark = pytest.mark.skipif(
    not shutil.which("git"),
    reason="git not available",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> TrackerConfig:
    """Create a minimal TrackerConfig for testing."""
    from helpers import make_test_config

    return make_test_config(**overrides)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the given repo."""
    return subprocess.run(
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with tracker dirs and initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    # Set up merge=union for .ops files
    (repo / ".gitattributes").write_text("*.ops merge=union\n")

    # Create tracker directory structure with .gitattributes inside
    # (so git tracks the directory even when empty of .ops files)
    ops_dir = repo / ".agent" / "tracker" / ".ops"
    ops_dir.mkdir(parents=True)
    (ops_dir / ".gitattributes").write_text("*.ops merge=union\n")

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    return repo


def _write_ops_file(ops_dir: Path, item_id: str, ops_content: str) -> Path:
    """Write an ops file and return its path."""
    path = ops_dir / f".{item_id}.ops"
    path.write_text(ops_content)
    return path


# ---------------------------------------------------------------------------
# TestMergeUnion
# ---------------------------------------------------------------------------


class TestMergeUnion:
    def test_concurrent_updates_merge_cleanly(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """Branch A updates status, branch B updates priority → merge preserves both."""
        repo = _init_repo(tmp_path)
        ops_dir = repo / ".agent" / "tracker" / ".ops"
        config = _make_config()

        # Create initial item on main
        create_ops = textwrap.dedent("""\
            - op: create  # a1b2
              at: "2026-01-01T00:00:00Z"  # a1b2
              by: agent  # a1b2
              actor: test_agent  # a1b2
              clock: 1  # a1b2
              nonce: a1b2  # a1b2
              data:  # a1b2
                kind: work_item  # a1b2
                title: "Test Item"  # a1b2
                status: todo_hard  # a1b2
                priority: 2  # a1b2
        """)
        _write_ops_file(ops_dir, "WI-test", create_ops)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add item")

        # Branch A: update status
        _git(repo, "checkout", "-b", "branch-a")
        update_a = textwrap.dedent("""\
            - op: update  # c3d4
              at: "2026-01-01T00:01:00Z"  # c3d4
              by: agent  # c3d4
              actor: test_agent  # c3d4
              clock: 2  # c3d4
              nonce: c3d4  # c3d4
              set:  # c3d4
                status: in_progress  # c3d4
        """)
        ops_path = ops_dir / ".WI-test.ops"
        ops_path.write_text(ops_path.read_text() + update_a)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "update status")

        # Branch B: update priority
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "branch-b")
        update_b = textwrap.dedent("""\
            - op: update  # e5f6
              at: "2026-01-01T00:02:00Z"  # e5f6
              by: agent  # e5f6
              actor: test_agent  # e5f6
              clock: 3  # e5f6
              nonce: e5f6  # e5f6
              set:  # e5f6
                priority: 0  # e5f6
        """)
        ops_path = ops_dir / ".WI-test.ops"
        ops_path.write_text(ops_path.read_text() + update_b)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "update priority")

        # Merge branch-a into branch-b
        _git(repo, "merge", "branch-a")

        # Verify: no conflict markers, both ops present
        merged_content = (ops_dir / ".WI-test.ops").read_text()
        assert "<<<" not in merged_content
        assert ">>>" not in merged_content

        ops = _parse_ops_file(ops_dir / ".WI-test.ops")
        item = compile_ops(ops, "WI-test")

        # Both updates should be reflected
        assert item.status == "in_progress"
        assert item.priority == 0

    def test_identical_op_type_merge(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """Both branches do update → nonce keeps them distinct in merge."""
        repo = _init_repo(tmp_path)
        ops_dir = repo / ".agent" / "tracker" / ".ops"
        config = _make_config()

        # Create initial item on main
        create_ops = textwrap.dedent("""\
            - op: create  # aaaa
              at: "2026-01-01T00:00:00Z"  # aaaa
              by: agent  # aaaa
              actor: test_agent  # aaaa
              clock: 1  # aaaa
              nonce: aaaa  # aaaa
              data:  # aaaa
                kind: work_item  # aaaa
                title: "Test"  # aaaa
                status: todo_hard  # aaaa
                priority: 2  # aaaa
        """)
        _write_ops_file(ops_dir, "WI-test2", create_ops)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add item")

        # Branch A: update status to in_progress
        _git(repo, "checkout", "-b", "branch-a")
        update_a = textwrap.dedent("""\
            - op: update  # bbbb
              at: "2026-01-01T00:01:00Z"  # bbbb
              by: agent  # bbbb
              actor: test_agent  # bbbb
              clock: 2  # bbbb
              nonce: bbbb  # bbbb
              set:  # bbbb
                status: in_progress  # bbbb
        """)
        ops_path = ops_dir / ".WI-test2.ops"
        ops_path.write_text(ops_path.read_text() + update_a)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch-a update")

        # Branch B: update status to done (higher clock wins)
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "branch-b")
        update_b = textwrap.dedent("""\
            - op: update  # cccc
              at: "2026-01-01T00:02:00Z"  # cccc
              by: agent  # cccc
              actor: test_agent  # cccc
              clock: 3  # cccc
              nonce: cccc  # cccc
              set:  # cccc
                status: done  # cccc
        """)
        ops_path = ops_dir / ".WI-test2.ops"
        ops_path.write_text(ops_path.read_text() + update_b)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch-b update")

        # Merge branch-a into branch-b
        _git(repo, "merge", "branch-a")

        merged_content = (ops_dir / ".WI-test2.ops").read_text()
        assert "<<<" not in merged_content

        ops = _parse_ops_file(ops_dir / ".WI-test2.ops")
        # Both update ops should be present
        update_ops = [o for o in ops if o["op"] == "update"]
        assert len(update_ops) == 2

        item = compile_ops(ops, "WI-test2")
        # Higher clock (3) wins → status is "done"
        assert item.status == "done"


# ---------------------------------------------------------------------------
# TestRebaseSafety
# ---------------------------------------------------------------------------


class TestRebaseSafety:
    def test_rebase_preserves_both_ops(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """Rebase A onto B → both ops present, no duplicates, unique nonces."""
        repo = _init_repo(tmp_path)
        ops_dir = repo / ".agent" / "tracker" / ".ops"
        config = _make_config()

        # Create initial item on main
        create_ops = textwrap.dedent("""\
            - op: create  # rr01
              at: "2026-01-01T00:00:00Z"  # rr01
              by: agent  # rr01
              actor: test_agent  # rr01
              clock: 1  # rr01
              nonce: rr01  # rr01
              data:  # rr01
                kind: work_item  # rr01
                title: "Rebase Test"  # rr01
                status: todo_hard  # rr01
                priority: 2  # rr01
        """)
        _write_ops_file(ops_dir, "WI-rebase", create_ops)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add item")

        # Branch B: add update (will be the base we rebase onto)
        _git(repo, "checkout", "-b", "branch-b")
        update_b = textwrap.dedent("""\
            - op: update  # rr02
              at: "2026-01-01T00:01:00Z"  # rr02
              by: agent  # rr02
              actor: test_agent  # rr02
              clock: 2  # rr02
              nonce: rr02  # rr02
              set:  # rr02
                priority: 0  # rr02
        """)
        ops_path = ops_dir / ".WI-rebase.ops"
        ops_path.write_text(ops_path.read_text() + update_b)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch-b update")

        # Branch A: add different update (diverged from main)
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "branch-a")
        update_a = textwrap.dedent("""\
            - op: update  # rr03
              at: "2026-01-01T00:02:00Z"  # rr03
              by: agent  # rr03
              actor: test_agent  # rr03
              clock: 3  # rr03
              nonce: rr03  # rr03
              set:  # rr03
                status: in_progress  # rr03
        """)
        ops_path = ops_dir / ".WI-rebase.ops"
        ops_path.write_text(ops_path.read_text() + update_a)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch-a update")

        # Rebase branch-a onto branch-b
        _git(repo, "rebase", "branch-b")

        rebased_content = (ops_dir / ".WI-rebase.ops").read_text()
        ops = _parse_ops_file(ops_dir / ".WI-rebase.ops")

        # Should have 3 ops: create + 2 updates
        assert len(ops) == 3

        # No duplicate nonces
        nonces = [o["nonce"] for o in ops]
        assert len(nonces) == len(set(nonces))

        item = compile_ops(ops, "WI-rebase")
        assert item.status == "in_progress"  # clock 3
        assert item.priority == 0  # clock 2


# ---------------------------------------------------------------------------
# TestCrossBranchDuplicateCreation
# ---------------------------------------------------------------------------


class TestCrossBranchDuplicateCreation:
    def test_duplicate_create_ops_handled(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """Both branches add item with same ID → merge → compile handles it."""
        repo = _init_repo(tmp_path)
        ops_dir = repo / ".agent" / "tracker" / ".ops"
        config = _make_config()

        # Branch A: create item + update
        _git(repo, "checkout", "-b", "branch-a")
        create_a = textwrap.dedent("""\
            - op: create  # dd01
              at: "2026-01-01T00:00:00Z"  # dd01
              by: agent  # dd01
              actor: test_agent  # dd01
              clock: 1  # dd01
              nonce: dd01  # dd01
              data:  # dd01
                kind: work_item  # dd01
                title: "Duplicate Test"  # dd01
                status: todo_hard  # dd01
                priority: 2  # dd01
            - op: update  # dd02
              at: "2026-01-01T00:01:00Z"  # dd02
              by: agent  # dd02
              actor: test_agent  # dd02
              clock: 2  # dd02
              nonce: dd02  # dd02
              set:  # dd02
                status: in_progress  # dd02
        """)
        _write_ops_file(ops_dir, "WI-dup", create_a)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch-a create+update")

        # Branch B: create same item + different update
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "branch-b")
        create_b = textwrap.dedent("""\
            - op: create  # dd03
              at: "2026-01-01T00:00:30Z"  # dd03
              by: agent  # dd03
              actor: test_agent  # dd03
              clock: 3  # dd03
              nonce: dd03  # dd03
              data:  # dd03
                kind: work_item  # dd03
                title: "Duplicate Test"  # dd03
                status: todo_hard  # dd03
                priority: 2  # dd03
            - op: update  # dd04
              at: "2026-01-01T00:02:00Z"  # dd04
              by: agent  # dd04
              actor: test_agent  # dd04
              clock: 4  # dd04
              nonce: dd04  # dd04
              set:  # dd04
                priority: 0  # dd04
        """)
        _write_ops_file(ops_dir, "WI-dup", create_b)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch-b create+update")

        # Merge branch-a into branch-b
        _git(repo, "merge", "branch-a")

        merged_content = (ops_dir / ".WI-dup.ops").read_text()
        assert "<<<" not in merged_content

        ops = _parse_ops_file(ops_dir / ".WI-dup.ops")

        # merge=union should give us all unique lines from both branches
        # Two creates + two updates = 4 ops
        create_ops_list = [o for o in ops if o["op"] == "create"]
        update_ops_list = [o for o in ops if o["op"] == "update"]
        assert len(create_ops_list) == 2
        assert len(update_ops_list) == 2

        # compile_ops uses the lowest-clock create (clock=1) as the base
        item = compile_ops(ops, "WI-dup")
        assert item.title == "Duplicate Test"
        # Both updates reflected: status from clock=2, priority from clock=4
        assert item.status == "in_progress"
        assert item.priority == 0
