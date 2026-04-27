# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the WI-rahib auto-pr convergence fix (INV-rahib regression).

Background
----------
INV-rahib ("auto-pr must converge to merged or explicit failure regardless
of concurrent activity") regressed on 2026-04-26 (PR #3392 → #3393, WI-gijot
ship). Two surfaces violated the invariant:

  Surface 1 — Scenario B close-and-repush boundary: the timeout-recovery
  and hung-run paths in scripts/auto-pr close the PR and force a fresh
  push without first verifying that the PR has not actually merged (or
  is not about to). Poll-endpoint flakes hide the merged/about-to-merge
  state under a "no progress in N seconds" signal.

  Surface 2 — Post-rebase merge attempt in do_merge() (forgejo-api.sh):
  after force-push of a rebased SHA, the function tried the merge once
  and emitted `Recovery: ./scripts/merge-pr ... --wait-for-ci`, punting
  to the human. The right behavior is to re-enter the CI poll loop on
  the new SHA and continue to convergence.

The fix:
  - Surface 1: a `_pre_scenario_b_gate` helper checks `_check_pr_merged`
    and `mergeable+poll_ci-retry`; if either succeeds the close is
    skipped. Helper lives in `scripts/lib/forgejo-api.sh`.
  - Surface 2: a labeled while-loop with explicit `continue` after
    re-rebase, capped at 3 iterations. No recursion (Decision 2 from
    the human triage on 2026-04-27 — labeled loop is more greppable
    and avoids the AUTOPR_FROM_REBASE re-entry hazard).

These tests are predominantly structural: the bash code paths require a
live Forgejo to exercise end-to-end, so we lean on grep-style assertions
plus seam-driven subprocess tests of the gate helper itself. The same
shape was used by `test_autopr_exit2_retry.py` for the WI-dotod fix.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGEJO_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


# --------------------------------------------------------------------
# Surface 1 — pre-scenario-b verification gate
# --------------------------------------------------------------------


def test_pre_scenario_b_gate_function_defined() -> None:
    """`_pre_scenario_b_gate` is defined in forgejo-api.sh."""
    text = FORGEJO_LIB.read_text()
    assert "_pre_scenario_b_gate()" in text
    assert "WI-rahib Surface 1" in text


def test_pre_scenario_b_gate_uses_check_pr_merged() -> None:
    """The gate must consult `_check_pr_merged` for the merged-during-timeout
    sub-case (Decision 1 from the human triage, sub-case 1)."""
    text = FORGEJO_LIB.read_text()
    gate_start = text.index("_pre_scenario_b_gate()")
    # Find the body — read up to the next top-level helper definition.
    gate_body_end = text.index("\n# ---", gate_start + 1)
    gate_body = text[gate_start:gate_body_end]
    assert "_check_pr_merged" in gate_body
    assert "AUTOPR_GATE_MERGED=true" in gate_body


def test_pre_scenario_b_gate_uses_mergeable_plus_poll_retry() -> None:
    """The gate must consult mergeable + retry poll_ci once for the
    about-to-merge / poll-endpoint-flake sub-case (Decision 1 sub-case 2)."""
    text = FORGEJO_LIB.read_text()
    gate_start = text.index("_pre_scenario_b_gate()")
    gate_body_end = text.index("\n# ---", gate_start + 1)
    gate_body = text[gate_start:gate_body_end]
    assert 'mergeable=$(echo "$API_RESPONSE" | json_field "mergeable")' in gate_body
    assert "poll_ci" in gate_body
    assert "CI_TIMEOUT_SECONDS=120" in gate_body, (
        "Gate must use a short CI timeout for the retry — long timeouts "
        "defeat the purpose of a one-shot 'maybe this was just a flake' check."
    )


def test_autopr_calls_gate_at_timeout_close_boundary() -> None:
    """The Exit-2 timeout-recovery close path must call the gate first."""
    text = AUTO_PR.read_text()
    # The gate call must appear before the timeout-recovery close.
    timeout_block_start = text.index("Timeout recovery: closing PR")
    upstream_window = text[:timeout_block_start]
    assert "_pre_scenario_b_gate" in upstream_window, (
        "The gate must be consulted before the Exit-2 close-and-repush; "
        "without it the merged/about-to-merge sub-cases can still trigger "
        "a regression of INV-rahib."
    )


def test_autopr_calls_gate_inside_hung_run_loop() -> None:
    """The Exit-3 hung-run retry loop must call the gate first each iter."""
    text = AUTO_PR.read_text()
    # Locate the hung-run loop body and confirm the gate call sits inside it.
    loop_start = text.index("while [[ $poll_result -eq 3 && $hung_retries -lt 4 ]]; do")
    loop_end = text.index("\n\tdone", loop_start)
    loop_body = text[loop_start:loop_end]
    assert "_pre_scenario_b_gate" in loop_body, (
        "The hung-run loop must consult the gate before each close, "
        "since the hung signal can mask a merged-or-about-to-merge state."
    )


def test_autopr_gate_branches_handle_merged_and_about_to_merge() -> None:
    """When the gate returns 0, both `merged` and `about-to-merge` cases
    must be handled — the former exits success, the latter falls through."""
    text = AUTO_PR.read_text()
    # The merged sub-case must set _autopr_state_final="merged".
    assert text.count('_autopr_state_final="merged"') >= 2, (
        "Both Scenario-B close paths should set _autopr_state_final='merged' "
        "when the gate confirms an already-merged PR."
    )
    assert 'AUTOPR_GATE_MERGED' in text


# --------------------------------------------------------------------
# Surface 2 — post-rebase poll-and-merge labeled loop
# --------------------------------------------------------------------


def test_post_rebase_loop_uses_labeled_while_with_max() -> None:
    """The post-rebase block must be a labeled while-loop with a max-iter cap
    (Decision 2: labeled loop, NOT recursion)."""
    text = FORGEJO_LIB.read_text()
    assert "WI-rahib Surface 2" in text
    assert 'while [ "$rebase_attempts" -lt "$rebase_max" ]; do' in text
    assert "rebase_max=3" in text
    assert "rebase_attempts=0" in text


def test_post_rebase_loop_re_polls_ci_on_new_sha() -> None:
    """The loop body must re-enter `poll_ci` on the rebased SHA before
    attempting the merge — that's the convergence guarantee that was
    missing pre-fix."""
    text = FORGEJO_LIB.read_text()
    loop_start = text.index('while [ "$rebase_attempts" -lt "$rebase_max" ]; do')
    # Find loop end (matching `done`)
    loop_end = text.index("\n\t\t\t\t\tdone", loop_start)
    loop_body = text[loop_start:loop_end]
    assert "new_sha=$(git rev-parse HEAD)" in loop_body
    assert 'poll_ci "$new_sha"' in loop_body
    assert "api_post" in loop_body, "Loop must attempt the merge after polling"


def test_post_rebase_loop_has_continue_after_re_rebase() -> None:
    """The 'base advanced again' branch must `continue` so the loop re-polls
    on the post-re-rebase SHA — Decision 2's 'explicit continue' shape."""
    text = FORGEJO_LIB.read_text()
    loop_start = text.index('while [ "$rebase_attempts" -lt "$rebase_max" ]; do')
    loop_end = text.index("\n\t\t\t\t\tdone", loop_start)
    loop_body = text[loop_start:loop_end]
    # `continue` must appear inside the head-behind-base branch
    assert "head behind base branch\\|is behind" in loop_body
    assert "continue" in loop_body
    # And the re-rebase must invoke a fresh fetch+rebase+force-push trio
    assert 'git fetch origin "$base_branch"' in loop_body
    assert 'git rebase "origin/$base_branch"' in loop_body
    assert "force-push=true" in loop_body


def test_post_rebase_loop_no_recursion() -> None:
    """The fix must NOT introduce recursion or a guard-flag re-entry pattern
    (Decision 2 explicitly rejected this shape)."""
    text = FORGEJO_LIB.read_text()
    assert "AUTOPR_FROM_REBASE" not in text, (
        "Decision 2 explicitly rejected the recursion-with-guard-flag shape "
        "in favor of a labeled loop with continue."
    )


def test_post_rebase_loop_recovery_hint_only_after_loop_exhausts() -> None:
    """The Recovery: ... --wait-for-ci hint must come AFTER the labeled loop
    (only on the unrecoverable/exhausted path), not as the default outcome
    of a single merge attempt."""
    text = FORGEJO_LIB.read_text()
    loop_start_idx = text.index('while [ "$rebase_attempts" -lt "$rebase_max" ]; do')
    rec_idx = text.index(
        'Recovery: ./scripts/merge-pr $pr_num --wait-for-ci', loop_start_idx
    )
    # The Recovery must appear after the `done` of the loop, not inside it.
    loop_end_idx = text.index("\n\t\t\t\t\tdone", loop_start_idx)
    assert rec_idx > loop_end_idx, (
        "Recovery hint must only fire if the labeled loop exhausted its "
        "iterations without merging — it is the unrecoverable-failure "
        "leg of the convergence contract, not the default outcome."
    )


# --------------------------------------------------------------------
# Behavioral seam-driven tests for the gate helper.
# Mirrors the test_autopr_exit2_retry pattern: stub _check_pr_merged
# and api_get with bash function definitions, drive poll_ci via the
# AUTOPR_TEST_POLL_EXITS seam, then assert on returncode + globals.
# --------------------------------------------------------------------


def _run_gate(
    *,
    check_merged_rc: int,
    api_get_response: str,
    api_get_rc: int,
    poll_seq: str,
    tmp_path: Path,
) -> tuple[int, str]:
    """Invoke `_pre_scenario_b_gate` with stubs.

    Returns (gate_rc, stdout), where gate_rc is parsed from a `RC=<n>` line
    on stdout (so we capture the function's return code, not the subprocess
    exit code which is the rc of the last `echo`).
    """
    pos_file = tmp_path / "pos"
    script = f"""
set +e
source '{FORGEJO_LIB}' >/dev/null 2>&1

# Stub _check_pr_merged
_check_pr_merged() {{ return {check_merged_rc}; }}

# Stub api_get to set API_RESPONSE and return controlled rc
api_get() {{
    API_RESPONSE='{api_get_response}'
    return {api_get_rc}
}}

API_BASE="https://example/api/v1/repos/test/test"

_pre_scenario_b_gate 12345 dummy-sha
rc=$?
echo "RC=$rc"
echo "MERGED=$AUTOPR_GATE_MERGED"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": "/usr/bin:/bin",
            "AUTOPR_TEST_POLL_EXITS": poll_seq,
            "AUTOPR_TEST_POLL_EXITS_POS": str(pos_file),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    gate_rc = -1
    for line in result.stdout.splitlines():
        if line.startswith("RC="):
            gate_rc = int(line.split("=", 1)[1])
            break
    return gate_rc, result.stdout


def test_gate_merged_subcase_returns_zero_with_merged_true(tmp_path: Path) -> None:
    """Sub-case 1: PR is merged → gate returns 0, AUTOPR_GATE_MERGED=true,
    poll_ci is never called."""
    rc, out = _run_gate(
        check_merged_rc=0,
        api_get_response="{}",
        api_get_rc=0,
        poll_seq="2",  # would return 2 if poll_ci were called
        tmp_path=tmp_path,
    )
    assert rc == 0, f"gate should return 0 when PR is merged, got rc={rc}\n{out}"
    assert "RC=0" in out
    assert "MERGED=true" in out
    assert "[test-seam] poll_ci" not in out, (
        "poll_ci must not be called once the merged sub-case fires"
    )


def test_gate_about_to_merge_returns_zero_with_merged_false(tmp_path: Path) -> None:
    """Sub-case 2: PR mergeable=true and poll_ci returns 0 → gate returns 0,
    AUTOPR_GATE_MERGED=false (caller falls through to merge attempt)."""
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_response='{"mergeable": true}',
        api_get_rc=0,
        poll_seq="0",
        tmp_path=tmp_path,
    )
    assert rc == 0, f"gate should return 0 on about-to-merge, got rc={rc}\n{out}"
    assert "RC=0" in out
    assert "MERGED=false" in out


def test_gate_mergeable_but_poll_still_failing_returns_one(tmp_path: Path) -> None:
    """When mergeable=true but poll_ci still returns nonzero, the gate must
    return 1 — caller proceeds with the close-and-repush as before."""
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_response='{"mergeable": true}',
        api_get_rc=0,
        poll_seq="2",
        tmp_path=tmp_path,
    )
    assert rc == 1, f"gate should fall through when poll_ci still fails: {out}"
    assert "RC=1" in out


def test_gate_not_mergeable_returns_one(tmp_path: Path) -> None:
    """When mergeable is false (or absent), gate returns 1 (proceed close)."""
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_response='{"mergeable": false}',
        api_get_rc=0,
        poll_seq="0",
        tmp_path=tmp_path,
    )
    assert rc == 1
    assert "RC=1" in out


def test_gate_api_get_failure_returns_one(tmp_path: Path) -> None:
    """If api_get itself fails, the gate must fail safe to 1 (proceed close)."""
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_response="",
        api_get_rc=1,
        poll_seq="0",
        tmp_path=tmp_path,
    )
    assert rc == 1
    assert "RC=1" in out
