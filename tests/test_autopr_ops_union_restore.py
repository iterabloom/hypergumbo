# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the WI-buhov ``_ops_union_restore_file`` helper in
``scripts/lib/forgejo-api.sh``.

Background
----------
On 2026-04-17 the agent observed two tracker ``discuss`` entries on WI-ripuz
vanish from the TUI mid-session. Root cause: when ``auto-pr`` detects the
feature branch is behind base, it backs up ``.agent/tracker/.ops`` and
``.agent/tracker-workspace/.ops``, rebases, then ``cp``'d the backup back
over the working tree unconditionally. Any ops appended by concurrent
tracker activity between the snapshot and the rebase (either
agent-driven ``discuss``/``add``/``update`` calls during CI polling, or
``tracker: sync`` commits pulled in by the rebase) were silently
overwritten.

Fix: ``_ops_union_restore_file`` performs an order-preserving line-level
union. ``awk '!seen[$0]++'`` is the dedupe primitive; for append-only ops
files this is semantics-preserving because each op's lines are unique to
that op (they carry a ``nonce:`` and ``clock:``).

Invariant under test
--------------------
After ``_ops_union_restore_file backup target``, the resulting ``target``
contains every line that was in EITHER the pre-restore target OR the
backup, with no duplicate lines, and the rebased working-tree lines
remain in their pre-restore order (so existing readers of the file see
a superset of what they saw before).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGEJO_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"


def _run_restore(backup_file: Path, target_file: Path) -> subprocess.CompletedProcess[str]:
    """Invoke ``_ops_union_restore_file`` via a sourced sub-shell."""
    return subprocess.run(
        [
            "bash",
            "-c",
            (
                f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
                f"_ops_union_restore_file '{backup_file}' '{target_file}'"
            ),
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_fresh_target_gets_copied(tmp_path: Path) -> None:
    """When the target does not exist, the backup is copied verbatim."""
    backup = tmp_path / "backup.ops"
    backup.write_text("- op: create\n  clock: 1\n  nonce: aaaa\n")
    target = tmp_path / "target.ops"
    assert not target.exists()

    result = _run_restore(backup, target)
    assert result.returncode == 0, result.stderr
    assert target.read_text() == "- op: create\n  clock: 1\n  nonce: aaaa\n"


def test_target_and_backup_identical_no_duplicates(tmp_path: Path) -> None:
    """Running restore when target and backup are the same produces the same file."""
    content = "- op: create\n  clock: 1\n  nonce: aaaa\n"
    backup = tmp_path / "backup.ops"
    backup.write_text(content)
    target = tmp_path / "target.ops"
    target.write_text(content)

    result = _run_restore(backup, target)
    assert result.returncode == 0, result.stderr
    # awk '!seen[$0]++' is order-preserving; target-first means target's
    # lines come out in target order (which equals backup order here).
    assert target.read_text() == content


def test_backup_has_extra_ops_union_preserved(tmp_path: Path) -> None:
    """Backup has ops the rebased target lost: union appends them."""
    backup = tmp_path / "backup.ops"
    backup.write_text(
        "- op: create\n  clock: 1\n  nonce: aaaa\n"
        "- op: discuss\n  clock: 2\n  nonce: bbbb\n"
        "  message: \"lost discuss entry\"\n"
    )
    target = tmp_path / "target.ops"
    target.write_text("- op: create\n  clock: 1\n  nonce: aaaa\n")

    result = _run_restore(backup, target)
    assert result.returncode == 0, result.stderr
    out = target.read_text()
    assert "nonce: aaaa" in out
    assert "nonce: bbbb" in out
    assert "lost discuss entry" in out


def test_target_has_newer_ops_preserved(tmp_path: Path) -> None:
    """Rebase brought new ops into target; they must survive the restore."""
    backup = tmp_path / "backup.ops"
    backup.write_text("- op: create\n  clock: 1\n  nonce: aaaa\n")
    target = tmp_path / "target.ops"
    target.write_text(
        "- op: create\n  clock: 1\n  nonce: aaaa\n"
        "- op: discuss\n  clock: 5\n  nonce: eeee\n"
        "  message: \"tracker-sync pulled this in during rebase\"\n"
    )

    result = _run_restore(backup, target)
    assert result.returncode == 0, result.stderr
    out = target.read_text()
    assert "nonce: aaaa" in out
    assert "nonce: eeee" in out
    assert "tracker-sync pulled this in during rebase" in out


def test_both_sides_have_unique_ops_both_preserved(tmp_path: Path) -> None:
    """The critical data-loss scenario: target and backup each have ops the
    other lacks. Union must preserve BOTH sets."""
    backup = tmp_path / "backup.ops"
    backup.write_text(
        "- op: create\n  clock: 1\n  nonce: aaaa\n"
        "- op: discuss\n  clock: 2\n  nonce: bbbb\n"
        "  message: \"agent-authored mid-auto-pr\"\n"
    )
    target = tmp_path / "target.ops"
    target.write_text(
        "- op: create\n  clock: 1\n  nonce: aaaa\n"
        "- op: discuss\n  clock: 3\n  nonce: cccc\n"
        "  message: \"tracker-sync pulled this in\"\n"
    )

    result = _run_restore(backup, target)
    assert result.returncode == 0, result.stderr
    out = target.read_text()
    # Common op kept once.
    assert out.count("nonce: aaaa\n") == 1
    # Both sides' unique ops survived.
    assert "nonce: bbbb" in out
    assert "nonce: cccc" in out
    assert "agent-authored mid-auto-pr" in out
    assert "tracker-sync pulled this in" in out


def test_target_ordering_preserved(tmp_path: Path) -> None:
    """Target's existing line order is preserved; backup-only lines tail."""
    backup = tmp_path / "backup.ops"
    backup.write_text("LINE-A\nLINE-X\n")
    target = tmp_path / "target.ops"
    target.write_text("LINE-A\nLINE-B\nLINE-C\n")

    result = _run_restore(backup, target)
    assert result.returncode == 0, result.stderr
    lines = target.read_text().splitlines()
    # target's A, B, C keep their order; backup-only X is appended.
    assert lines == ["LINE-A", "LINE-B", "LINE-C", "LINE-X"]


def test_missing_backup_is_noop(tmp_path: Path) -> None:
    """A non-existent backup is treated as a no-op: return 0, target unchanged."""
    backup = tmp_path / "does-not-exist.ops"
    target = tmp_path / "target.ops"
    target.write_text("- op: create\n  clock: 1\n  nonce: aaaa\n")
    before = target.read_text()

    result = _run_restore(backup, target)
    assert result.returncode == 0, result.stderr
    assert target.read_text() == before


def test_helper_is_sourceable_without_deps(tmp_path: Path) -> None:
    """Sourcing forgejo-api.sh to call the helper must not require network
    side-effects or environment beyond PATH."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
            "declare -F _ops_union_restore_file > /dev/null && echo OK",
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
