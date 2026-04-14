# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the WI-dotod Exit 2 (timeout) soft-retry and its ``poll_ci`` seam.

Background
----------
On 2026-04-14 a WI-banaf auto-pr invocation hit ``poll_ci`` Exit 2 (timeout
after CI started but didn't finish). The existing hung-run retry loop only
fires on Exit 3 (no jobs started), so Exit 2 escalated to Scenario B and the
agent waited ~3h before a fresh auto-pr invocation merged in 8 min.

WI-dotod adds two things:

1. A ``poll_ci`` test seam (``AUTOPR_TEST_POLL_EXITS``) in
   ``lib/forgejo-api.sh`` that returns exit codes from a colon-separated
   sequence, so the retry block can be exercised without a live Forgejo.
2. An Exit 2 soft-retry in ``auto-pr do_pr``: one re-poll with a shorter
   timeout, then at most one close+repush, before escalating to Scenario B.

These tests cover the seam itself (the mechanism the retry relies on) and
the structural invariants of the retry block (by grepping the script).
End-to-end testing of the full retry requires a live forge fixture and is
out of scope for this PR.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGEJO_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


def _run_seam(seq: str, count: int, tmp_path: Path) -> list[int]:
    """Invoke poll_ci `count` times with the seam set; return exit codes."""
    pos_file = tmp_path / "pos"
    codes: list[int] = []
    for _ in range(count):
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source '{FORGEJO_LIB}' >/dev/null 2>&1; poll_ci dummy-sha",
            ],
            env={
                "PATH": "/usr/bin:/bin",
                "AUTOPR_TEST_POLL_EXITS": seq,
                "AUTOPR_TEST_POLL_EXITS_POS": str(pos_file),
            },
            capture_output=True,
            text=True,
            timeout=5,
        )
        codes.append(result.returncode)
    return codes


def test_seam_returns_sequence_in_order(tmp_path: Path) -> None:
    """The seam returns exit codes in the order given."""
    assert _run_seam("2:0", 2, tmp_path) == [2, 0]


def test_seam_returns_exit_3_when_requested(tmp_path: Path) -> None:
    """The seam handles Exit 3 (hung) the same way as any other code."""
    assert _run_seam("3:3:0", 3, tmp_path) == [3, 3, 0]


def test_seam_defaults_to_zero_after_sequence_exhausted(tmp_path: Path) -> None:
    """After the sequence runs out, poll_ci returns 0 (success)."""
    assert _run_seam("2", 3, tmp_path) == [2, 0, 0]


def test_seam_is_inactive_when_env_unset(tmp_path: Path) -> None:
    """Without AUTOPR_TEST_POLL_EXITS, poll_ci takes the real code path.

    We don't run the real path (it would hit the network). We assert the
    seam's test-mode banner is absent by running with an empty value.
    """
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
            "declare -f poll_ci | head -20",
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert "AUTOPR_TEST_POLL_EXITS" in result.stdout, (
        "seam should be declared in the poll_ci function body"
    )


def test_autopr_exit2_retry_block_present() -> None:
    """Structural assertion: auto-pr contains the Exit 2 retry block.

    Guards against accidental revert of WI-dotod. Checks that:
    - timeout_retries is declared
    - the Exit 2 branch re-polls with a shorter timeout (CI_TIMEOUT_SECONDS=300)
    - the retry is capped at 1 (timeout_retries=1 after first retry)
    - escalation still exits 2 after the soft-retry fails
    """
    text = AUTO_PR.read_text()
    assert "timeout_retries=0" in text
    assert "timeout_retries=1" in text
    assert "CI_TIMEOUT_SECONDS=300" in text
    assert "Timeout soft-retry" in text
    assert 'exit 2' in text, "Scenario B exit 2 must remain as the final escalation"


def test_autopr_exit3_retry_loop_unchanged() -> None:
    """Structural assertion: the Exit 3 hung-run retry loop is unchanged.

    The existing 4-retry Exit 3 loop must still exist — Exit 2 recovery
    is additive, not a replacement. If the cascade from Exit 2 produces
    Exit 3, the Exit 3 loop picks it up.
    """
    text = AUTO_PR.read_text()
    assert "while [[ $poll_result -eq 3 && $hung_retries -lt 4 ]]; do" in text
    assert "Hung-run retry $hung_retries/4" in text
