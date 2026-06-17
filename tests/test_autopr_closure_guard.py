# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the WI-bunag commit-message / PR-body tracker-closure guard.

WI-bunag (human-approved 2026-06-17, hybrid): this tracker is CLI-driven, so a
bare ``Closes WI-…`` line in a commit message / PR body is inert and leaves the
item stale after merge. ``do_merge_guarded`` (scripts/lib/forgejo-api.sh) wraps
``do_merge`` with:

  * PRE-merge  — a bare ``Closes|Fixes|Resolves (WI|INV|META)-<id>`` referencing
    a still-OPEN item ABORTS the merge (return 1).
  * POST-merge — a ``Closes-with-evidence: <id> (<evidence>)`` line AUTO-CLOSES
    the item (``tracker update <id> --status done --note "…"``).

These are seam-driven subprocess tests: they source forgejo-api.sh, inject a
stub tracker CLI via ``AUTOPR_TRACKER_CLI`` (so no live tracker is touched), and
drive the guard helpers directly. Mirrors the test_autopr_rahib_convergence.py
seam pattern. (Bash code path — no pytest coverage contribution.)
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGEJO_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"
MERGE_PR = REPO_ROOT / "scripts" / "merge-pr"

# Stub tracker: `show <ID>` prints a status line (driven by AUTOPR_TEST_STATUS;
# "__unknown__" prints nothing → simulates an unknown id); `update …` records
# its full argv to AUTOPR_TEST_UPDATE_LOG for assertions.
_STUB_TRACKER = """#!/bin/bash
case "$1" in
  show)
    if [ "${AUTOPR_TEST_STATUS:-}" = "__unknown__" ]; then exit 0; fi
    echo "  status: ${AUTOPR_TEST_STATUS:-todo_hard}  priority: P2"
    ;;
  update)
    echo "UPDATE_ARGS: $*" >> "$AUTOPR_TEST_UPDATE_LOG"
    ;;
esac
exit 0
"""


def _run(call: str, *, text: str, status: str, tmp_path: Path) -> tuple[int, str, str, str]:
    """Source forgejo-api.sh, run `call` (a bash snippet using $TEXT), with a
    stub tracker. Returns (rc, stdout, stderr, update_log_contents)."""
    stub = tmp_path / "tracker_stub"
    stub.write_text(_STUB_TRACKER)
    stub.chmod(0o755)
    update_log = tmp_path / "update_log"
    script = f"""
set +e
source '{FORGEJO_LIB}' >/dev/null 2>&1
TEXT={shlex.quote(text)}
{call}
echo "RC=$?"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": "/usr/bin:/bin",
            "AUTOPR_TRACKER_CLI": str(stub),
            "AUTOPR_TEST_STATUS": status,
            "AUTOPR_TEST_UPDATE_LOG": str(update_log),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    rc = -1
    for line in result.stdout.splitlines():
        if line.startswith("RC="):
            rc = int(line.split("=", 1)[1])
    log = update_log.read_text() if update_log.exists() else ""
    return rc, result.stdout, result.stderr, log


# ---- _closure_refs extraction --------------------------------------

def test_closure_refs_bare_extracts_and_excludes_evidence(tmp_path: Path) -> None:
    text = (
        "feat: thing\n\nCloses WI-bunag-vopab. Fixes: INV-rahib\n"
        "Closes-with-evidence: WI-tubuv (PR #9; repro: x)\n"
    )
    rc, out, _err, _log = _run(
        '_closure_refs "$TEXT" bare', text=text, status="todo_hard",
        tmp_path=tmp_path,
    )
    ids = [ln for ln in out.splitlines() if not ln.startswith("RC=")]
    assert "WI-bunag-vopab" in ids
    assert "INV-rahib" in ids
    # The evidence form must NOT be picked up as a bare closure.
    assert "WI-tubuv" not in ids


def test_closure_refs_evidence_extracts_id_and_evidence(tmp_path: Path) -> None:
    text = "Closes-with-evidence: WI-tubuv (PR #9; repro: core 100%)\n"
    rc, out, _err, _log = _run(
        '_closure_refs "$TEXT" evidence', text=text, status="todo_hard",
        tmp_path=tmp_path,
    )
    rows = [ln for ln in out.splitlines() if ln.startswith("WI-tubuv")]
    assert rows, f"expected an evidence row, got: {out!r}"
    assert "(PR #9; repro: core 100%)" in rows[0]


# ---- _closure_guard_pre_merge --------------------------------------

def test_pre_merge_aborts_on_bare_closes_to_open_item(tmp_path: Path) -> None:
    rc, _out, err, _log = _run(
        '_closure_guard_pre_merge "$TEXT"',
        text="Closes WI-bunag\n", status="todo_hard", tmp_path=tmp_path,
    )
    assert rc == 1, "bare Closes to an OPEN item must abort the merge"
    assert "WI-bunag" in err


def test_pre_merge_passes_when_item_already_resolved(tmp_path: Path) -> None:
    rc, _out, _err, _log = _run(
        '_closure_guard_pre_merge "$TEXT"',
        text="Closes WI-bunag\n", status="done", tmp_path=tmp_path,
    )
    assert rc == 0, "bare Closes to an already-resolved item must NOT abort"


def test_pre_merge_passes_with_no_closure_ref(tmp_path: Path) -> None:
    rc, _out, _err, _log = _run(
        '_closure_guard_pre_merge "$TEXT"',
        text="feat: unrelated change\n", status="todo_hard", tmp_path=tmp_path,
    )
    assert rc == 0


def test_pre_merge_does_not_abort_on_evidence_form(tmp_path: Path) -> None:
    """The evidence opt-in is handled post-merge, not aborted pre-merge."""
    rc, _out, _err, _log = _run(
        '_closure_guard_pre_merge "$TEXT"',
        text="Closes-with-evidence: WI-bunag (PR #9; repro: x)\n",
        status="todo_hard", tmp_path=tmp_path,
    )
    assert rc == 0, "Closes-with-evidence must not trip the bare-closure abort"


def test_pre_merge_fails_open_on_unknown_item(tmp_path: Path) -> None:
    """An unrecognized id (tracker show prints nothing) must NOT block merge."""
    rc, _out, _err, _log = _run(
        '_closure_guard_pre_merge "$TEXT"',
        text="Closes WI-ghost-xyz\n", status="__unknown__", tmp_path=tmp_path,
    )
    assert rc == 0


# ---- _closure_guard_post_merge -------------------------------------

def test_post_merge_auto_closes_evidence_item(tmp_path: Path) -> None:
    rc, _out, _err, log = _run(
        '_closure_guard_post_merge "$TEXT" 42 abc123def456ghi',
        text="Closes-with-evidence: WI-tubuv (PR #42; repro: core 100%)\n",
        status="todo_hard", tmp_path=tmp_path,
    )
    assert rc == 0
    assert "UPDATE_ARGS:" in log, f"expected a tracker update call, got: {log!r}"
    assert "WI-tubuv" in log
    assert "--status done" in log
    assert "--note" in log
    assert "PR #42" in log
    # The 12-char short sha is included in the note.
    assert "abc123def456" in log


def test_post_merge_noop_without_evidence_line(tmp_path: Path) -> None:
    rc, _out, _err, log = _run(
        '_closure_guard_post_merge "$TEXT" 42 abc123',
        text="Closes WI-bunag\n", status="todo_hard", tmp_path=tmp_path,
    )
    assert rc == 0
    assert log == "", "a bare Closes must NOT trigger an auto-close"


# ---- structural: callers route through the guarded wrapper ---------

def test_callers_use_do_merge_guarded() -> None:
    """Both auto-pr and merge-pr must call do_merge_guarded (not bare do_merge),
    so the closure guard covers every merge-completion path."""
    for path in (AUTO_PR, MERGE_PR):
        text = path.read_text()
        # An actual call looks like `do_merge "$PR_NUM" …` (function name +
        # quoted first arg). Match that form precisely so comment/echo mentions
        # ("do_merge will retry", "do_merge discovers …") don't false-positive.
        bare_calls = [
            ln for ln in text.splitlines()
            if 'do_merge "' in ln and "do_merge_guarded" not in ln
        ]
        assert not bare_calls, (
            f"{path.name} still has a bare do_merge call (must be "
            f"do_merge_guarded): {bare_calls}"
        )
        assert "do_merge_guarded" in text, f"{path.name} must call do_merge_guarded"
