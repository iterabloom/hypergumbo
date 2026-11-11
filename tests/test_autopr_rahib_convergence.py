# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for auto-pr convergence: every run ends merged or in an explicit,
declared terminal state, regardless of concurrent activity (INV-rahib).

The surfaces covered:

  Surface 1 — Scenario B close-and-repush boundary. The timeout-recovery
  and hung-run paths in scripts/auto-pr close the PR and force a fresh
  push. Poll-endpoint flakes can hide a merged or about-to-merge PR under
  a "no progress in N seconds" signal, so before closing, both paths call
  `_pre_scenario_b_gate` (scripts/lib/forgejo-api.sh), which checks
  `_check_pr_merged` and then mergeability plus one short `poll_ci` retry;
  if either succeeds the close is skipped.

  Surface 2 — Post-rebase merge in do_merge() (forgejo-api.sh). After
  force-pushing a rebased SHA, the function re-enters the CI poll on the
  new SHA inside a labeled while-loop (explicit `continue` after a
  re-rebase, capped at 3 iterations) instead of punting to a human with
  `Recovery: ./scripts/merge-pr ... --wait-for-ci`. A loop rather than
  recursion keeps it greppable and avoids a guard-flag re-entry hazard.

  Surface 3 — poll_ci requires a failure-with-no-pending signal to be
  stable across consecutive polls before returning failure.

  Also pinned: the tri-state `json_bool_state` reader the gate depends on,
  the declared terminal-state vocabulary, and the post-merge boundary that
  keeps housekeeping failures from turning a landed merge into a failure.

The bash paths need a live forge to exercise end-to-end, so these tests
mix grep-style structural assertions with subprocess tests that drive the
real bash functions through stubs and test seams (the same shape as
`test_autopr_exit2_retry.py`).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

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
    # Deliberately loose: pinning the literal line that reads `mergeable`
    # would assert implementation text rather than behavior, and would break
    # whenever that read is corrected. The behavioral `_run_gate` cases below
    # prove the gate consults mergeability; this only needs to keep the
    # dependency from being deleted.
    assert "mergeable" in gate_body
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
    attempting the merge — that is the convergence guarantee."""
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
    api_get_rc: int,
    poll_seq: str,
    tmp_path: Path,
    api_get_response: str | None = None,
    api_get_responses: list[str] | None = None,
) -> tuple[int, str]:
    """Invoke `_pre_scenario_b_gate` with stubs.

    Returns (gate_rc, stdout), where gate_rc is parsed from a `RC=<n>` line
    on stdout (so we capture the function's return code, not the subprocess
    exit code which is the rc of the last `echo`).

    `api_get_responses` supplies a SEQUENCE, one payload per api_get call,
    with the last entry repeating for any further calls. It exists because
    the gate re-queries `/pulls/<n>` when mergeability comes back null, and
    a stub that returns the same payload forever cannot tell "the re-query
    happened and returned the same thing" apart from "no re-query happened".
    `CI_POLL_NO_SLEEP` is set so the inter-query delay does not cost the
    suite real seconds — the same seam poll_ci already uses.
    """
    if (api_get_response is None) == (api_get_responses is None):
        raise ValueError("pass exactly one of api_get_response/api_get_responses")
    payloads = api_get_responses or [api_get_response or ""]

    pos_file = tmp_path / "pos"
    resp_dir = tmp_path / "responses"
    resp_dir.mkdir()
    for idx, payload in enumerate(payloads):
        (resp_dir / f"resp_{idx}").write_text(payload)
    resp_pos = tmp_path / "resp_pos"
    last_idx = len(payloads) - 1

    script = f"""
set +e
source '{FORGEJO_LIB}' >/dev/null 2>&1

# Stub _check_pr_merged
_check_pr_merged() {{ return {check_merged_rc}; }}

# Stub api_get: serve the next scripted payload, then repeat the last one.
api_get() {{
    local n idx
    n=$(cat '{resp_pos}' 2>/dev/null || echo 0)
    echo $((n + 1)) > '{resp_pos}'
    # The reported call number must come from the UNCLAMPED counter; the
    # clamp only picks which payload to repeat. Reporting the clamped index
    # would print "call #1" twice and make a re-query invisible.
    idx=$n
    if [[ $idx -gt {last_idx} ]]; then idx={last_idx}; fi
    API_RESPONSE=$(cat '{resp_dir}/resp_'"$idx")
    echo "[test-seam] api_get call #$((n + 1))"
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
            "CI_POLL_NO_SLEEP": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
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
    """When the forge says mergeable=false, gate returns 1 (proceed close).

    Scoped deliberately to the explicit `false`. Absent and null are covered
    by their own cases below, because they are NOT the same fact and the
    gate's handling of them differs in consequence.
    """
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_response='{"mergeable": false}',
        api_get_rc=0,
        poll_seq="0",
        tmp_path=tmp_path,
    )
    assert rc == 1
    assert "RC=1" in out


def test_gate_mergeable_absent_returns_one(tmp_path: Path) -> None:
    """An absent `mergeable` key fails safe to 1 (proceed with the close).

    Characterization, and defensible as-is: a response with no `mergeable`
    field at all is malformed relative to both forge schemas, and refusing to
    skip the close on a payload we cannot read is the conservative direction.
    Recorded explicitly so that the null case below is visibly a DIFFERENT
    situation rather than an unexamined sibling of this one.
    """
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_response='{"state": "open"}',
        api_get_rc=0,
        poll_seq="0",
        tmp_path=tmp_path,
    )
    assert rc == 1
    assert "RC=1" in out


def test_gate_mergeable_null_must_not_close_a_green_pr(tmp_path: Path) -> None:
    """Unknown mergeability + green CI must not produce a close-and-repush.

    The narrow claim, and the one this whole surface turns on: "the forge has
    not computed mergeability yet" and "the forge says this cannot merge" are
    different facts, and destroying a GREEN PR on the first is not defensible.
    The gate re-queries once, then lets the CI retry decide.
    """
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_responses=['{"mergeable": null}'],  # still null on re-query
        api_get_rc=0,
        poll_seq="0:0",  # CI is GREEN on the retry
        tmp_path=tmp_path,
    )
    assert rc == 0, (
        f"gate ordered a close-and-repush of a green PR on unknown "
        f"mergeability (rc={rc})\n{out}"
    )
    assert "[test-seam] api_get call #2" in out, (
        "the gate must RE-QUERY once before concluding — GitHub's first read "
        "is often what schedules the mergeability computation"
    )


def test_gate_mergeable_null_then_red_ci_still_closes(tmp_path: Path) -> None:
    """Unknown mergeability with a RED CI retry still proceeds to the close.

    The complement of the case above, and the one that keeps the fix from
    being a blanket "never close". Deferring to CI only helps if CI actually
    gets to decide in both directions.
    """
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_responses=['{"mergeable": null}'],
        api_get_rc=0,
        poll_seq="1:1",  # CI FAILS on the retry
        tmp_path=tmp_path,
    )
    assert rc == 1, f"gate must fall through to the close on a red retry: {out}"


def test_gate_mergeable_null_resolves_to_true_on_requery(tmp_path: Path) -> None:
    """A null that becomes `true` on the re-query takes the mergeable path.

    This is the case the re-query exists for: GitHub computes mergeability
    asynchronously and the first read frequently schedules it, so the second
    read returns a real boolean. Asserting the log line proves the gate took
    the `true` branch rather than the still-unknown fallback — the two return
    the same rc, so rc alone cannot distinguish them (L17: a passing
    assertion that cannot tell the arms apart is not evidence).
    """
    rc, out = _run_gate(
        check_merged_rc=1,
        api_get_responses=['{"mergeable": null}', '{"mergeable": true}'],
        api_get_rc=0,
        poll_seq="0:0",
        tmp_path=tmp_path,
    )
    assert rc == 0, f"gate should skip the close once mergeable resolves: {out}"
    assert "mergeable=true" in out
    assert "still uncomputed" not in out, (
        "gate took the unknown-fallback branch when the re-query had already "
        "resolved mergeability to true"
    )


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


# --------------------------------------------------------------------
# Surface 3 — poll_ci confirmation re-poll under concurrency.
#
# A single failure snapshot during concurrent CI activity can be transiently
# inconsistent (e.g. the hung-run retry's PR-close cancels its in-flight run,
# whose error statuses shadow the live run's still-pending status for the same
# context). poll_ci therefore requires the failure-with-no-pending signal to be
# STABLE across FAILURE_CONFIRM_POLLS consecutive polls before returning 1.
#
# These drive the REAL poll_ci loop (NOT the AUTOPR_TEST_POLL_EXITS seam, which
# would bypass the loop under test) against a scripted api_get response
# sequence, with CI_POLL_NO_SLEEP skipping the inter-poll sleeps.
# --------------------------------------------------------------------

_RESP_FAIL = (
    '{"state": "failure", "statuses": '
    '[{"context": "CI / pytest (pull_request)", "status": "failure"}]}'
)
_RESP_PENDING = (
    '{"state": "pending", "statuses": '
    '[{"context": "CI / pytest (pull_request)", "status": "pending"}]}'
)
_RESP_SUCCESS = (
    '{"state": "success", "statuses": '
    '[{"context": "CI / pytest (pull_request)", "status": "success"}]}'
)


def _run_poll_ci(responses: list[str], tmp_path: Path) -> tuple[int, str]:
    """Drive the real poll_ci loop against a scripted api_get sequence.

    Each element is the JSON body api_get sets into API_RESPONSE on successive
    polls (clamped to the last once exhausted). Returns (poll_rc, stdout)."""
    seq_dir = tmp_path / "aget"
    seq_dir.mkdir()
    for i, body in enumerate(responses):
        (seq_dir / str(i)).write_text(body)
    pos_file = tmp_path / "aget_pos"
    n = len(responses)
    script = f"""
set +e
source '{FORGEJO_LIB}' >/dev/null 2>&1

# Walk the scripted api_get response sequence (clamp to last when exhausted).
api_get() {{
    local pos
    pos=$(cat '{pos_file}' 2>/dev/null || echo 0)
    if [ "$pos" -ge {n} ]; then
        pos=$(( {n} - 1 ))
    else
        echo $((pos + 1)) > '{pos_file}'
    fi
    API_RESPONSE=$(cat '{seq_dir}/'"$pos")
    return 0
}}
# The failure path fetches a job log over the network — stub it out.
fetch_job_log() {{ return 1; }}

API_BASE="https://example/api/v1/repos/test/test"
poll_ci dummy-sha
rc=$?
echo ""
echo "RC=$rc"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": "/usr/bin:/bin",
            "CI_POLL_NO_SLEEP": "1",
            "CI_TIMEOUT_SECONDS": "60",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    rc = -1
    for line in result.stdout.splitlines():
        if line.startswith("RC="):
            rc = int(line.split("=", 1)[1])
            break
    return rc, result.stdout


def test_poll_ci_stable_failure_still_returns_one(tmp_path: Path) -> None:
    """A genuine failure (failure-with-no-pending on TWO consecutive polls)
    must still return 1 — the confirmation re-poll must not mask real
    failures."""
    rc, out = _run_poll_ci([_RESP_FAIL, _RESP_FAIL], tmp_path)
    assert rc == 1, f"stable failure must return 1, got {rc}\n{out}"


def test_poll_ci_transient_failure_then_pending_recovers(tmp_path: Path) -> None:
    """A transient failure-with-no-pending snapshot followed by the live run
    reappearing pending and then succeeding must converge to 0 — NOT a false
    explicit failure."""
    rc, out = _run_poll_ci(
        [_RESP_FAIL, _RESP_PENDING, _RESP_SUCCESS], tmp_path
    )
    assert rc == 0, (
        f"transient failure that recovers must return 0 (no false failure), "
        f"got {rc}\n{out}"
    )


def test_poll_ci_confirmation_logic_present() -> None:
    """Structural guard: the confirmation re-poll (counter + threshold gate +
    pending/in-progress resets) must stay wired into poll_ci."""
    text = FORGEJO_LIB.read_text()
    assert "failure_confirm_count" in text
    assert 'failure_confirm_polls="${FAILURE_CONFIRM_POLLS:-2}"' in text
    assert "failure_confirm_count -lt $failure_confirm_polls" in text
    # Reset on a pending sibling AND at the in-progress loop bottom — the two
    # resets that make the count require CONSECUTIVE failure observations.
    assert text.count("failure_confirm_count=0") >= 2


# --------------------------------------------------------------------
# json_bool_state — the tri-state reader the gate depends on.
#
# json_field cannot express these apart (it prints '' for null, '' for a
# missing key and '' on a parse failure), so the gate cannot use it to read
# `mergeable`.
# --------------------------------------------------------------------


def _json_bool_state(payload: str, field: str = "mergeable") -> str:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
            f"printf '%s' {payload!r} | json_bool_state '{field}'",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def test_json_bool_state_distinguishes_all_four_shapes() -> None:
    """true / false / null / absent must be four distinguishable answers.

    This is the whole point of the helper: `json_field` maps three of these
    to the same empty string, so a caller branching on it silently reads
    "not computed yet" as "no".
    """
    assert _json_bool_state('{"mergeable": true}') == "true"
    assert _json_bool_state('{"mergeable": false}') == "false"
    assert _json_bool_state('{"mergeable": null}') == "null"
    assert _json_bool_state('{"state": "open"}') == "absent"


def test_json_bool_state_reports_unreadable_rather_than_guessing() -> None:
    """A payload that is not JSON is `unreadable`, never `false`.

    Failing to parse and being told "no" are different facts too, and the
    caller is the only layer that can decide what to do about each.
    """
    assert _json_bool_state("not json at all") == "unreadable"


def test_json_bool_state_non_boolean_is_other() -> None:
    """A present-but-non-boolean value is `other`, not coerced to true/false."""
    assert _json_bool_state('{"mergeable": "clean"}') == "other"


def test_json_field_still_conflates_them_which_is_why_the_helper_exists() -> None:
    """Non-vacuity floor for the three tests above (L17).

    If `json_field` ever grew the same tri-state behavior, the assertions
    above would still pass while testing nothing anyone depends on. Pin the
    conflation that motivates the helper, so this file fails loudly if the
    premise changes rather than quietly measuring a distinction both
    functions make.
    """
    out = subprocess.run(
        [
            "bash",
            "-c",
            f"source '{FORGEJO_LIB}' >/dev/null 2>&1; "
            'for p in \'{"mergeable": null}\' \'{"state": "open"}\'; do '
            'printf "[%s]" "$(printf "%s" "$p" | json_field "mergeable")"; done',
        ],
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    assert out == "[][]", (
        f"json_field is expected to render BOTH null and absent as the empty "
        f"string — that conflation is what json_bool_state exists to fix. "
        f"Got {out!r}"
    )


# --------------------------------------------------------------------
# The declared terminal-state vocabulary (INV-rahib clauses (a)/(c)).
#
# "Merged or explicit failure" alone is too narrow: `queued_vpr`,
# `governance_pending` and `noop_empty_queue` are deliberate, documented,
# human-approved outcomes (WI-hajif). So convergence is a DECLARED CLOSED SET:
# every run must record a state drawn from this vocabulary, and `unknown` (the
# default) is the violation signal.
# --------------------------------------------------------------------

CONVERGED_MERGED = frozenset({"merged", "already_merged"})

CONVERGED_FAILURE = frozenset(
    {
        "auth_error",
        "failed_already_merged_check",
        "failed_flush_remote_unavailable",
        "failed_merge",
        "failed_not_merged",
        "failed_pr_lookup",
        "failed_protected_branch",
        "failed_tracker_sync_pending",
        # Exits that each name what they decided, so they record a
        # converged failure rather than the `unknown` initializer (WI-nazoj).
        "failed_branch_ownership",
        "failed_queue_not_linear",
        "failed_queue_unresolvable",
        "failed_repush",
        "flush_push_rejected",
        "push_rejected_diverged",
        # Set by ci_verdict_permits_merge and propagated via CI_VERDICT_STATE.
        "ci_failed",
        "ci_timeout",
        "ci_hung",
        "ci_unknown",
        # A non-tty caller that declared neither --detach nor --foreground is
        # refused before doing any work, because exit 0 means different things
        # in the two modes and guessing would conflate them (WI-hajak).
        "failed_mode_undeclared",
    }
)

# Deliberate non-merge outcomes. Converged by ruling, not by accident.
CONVERGED_DELIBERATE = frozenset(
    # `detached_watching` (WI-hajak) belongs here for the same reason
    # `queued_vpr` does: the run did exactly what it set out to do. It did not
    # merge, and it did not fail to merge — it handed the poll to a watcher
    # that outlives the caller's turn, and that watcher writes its own row.
    {"queued_vpr", "governance_pending", "noop_empty_queue", "detached_watching"}
)

# A FOURTH bucket, deliberately not folded into any of the three above. A run
# that was killed did not converge — but it did not fail to converge either,
# because it never got to decide. Folding it into CONVERGED_DELIBERATE would
# claim auto-pr chose this; folding it into CONVERGED_FAILURE would claim
# auto-pr failed; leaving it as `unknown` would report a run killed by its
# caller (often after its PR merged) as an INV-rahib violation. (WI-katap)
EXTERNALLY_TERMINATED = frozenset(
    {"terminated_sigterm", "terminated_sigint", "terminated_sighup"}
)

# Test seams that live in the production script. Declared rather than
# silently tolerated, so that their number is visible and does not grow
# unnoticed.
TEST_SEAM_STATES = frozenset(
    {"test_gh_flush_ok", "test_gh_push_ok", "test_desync_fallback_ok"}
)

NON_CONVERGENT = frozenset({"unknown"})

DECLARED_STATES = (
    CONVERGED_MERGED
    | CONVERGED_FAILURE
    | CONVERGED_DELIBERATE
    | EXTERNALLY_TERMINATED
    | TEST_SEAM_STATES
    | NON_CONVERGENT
)


def _emitted_states() -> set[str]:
    """Every terminal state auto-pr can actually record."""
    import re

    states = set(re.findall(r'_autopr_state_final="([a-z_]+)"', AUTO_PR.read_text()))
    # `_autopr_state_final="$CI_VERDICT_STATE"` forwards the CI verdict, whose
    # values are allocated in the forge library rather than in auto-pr.
    states |= set(
        re.findall(r'CI_VERDICT_STATE="([a-z_]+)"', FORGEJO_LIB.read_text())
    )
    return states


def test_every_emitted_terminal_state_is_declared() -> None:
    """No auto-pr exit may record a state outside the declared vocabulary.

    This is the executable half of the refined invariant. A new terminal
    state added without classifying it fails here, which is the point:
    it forces the state list to be looked at whenever it grows.
    """
    emitted = _emitted_states()
    # Non-vacuity floor (L17): a regex that stopped matching would make the
    # subset assertion below trivially true.
    assert len(emitted) >= 15, (
        f"extraction found only {len(emitted)} states — the regex has "
        f"probably stopped matching: {sorted(emitted)}"
    )
    undeclared = emitted - DECLARED_STATES
    assert not undeclared, (
        f"auto-pr can record terminal state(s) that no bucket declares: "
        f"{sorted(undeclared)}. Classify each as converged (merged / explicit "
        f"failure / deliberate non-merge) or as a violation before shipping."
    )


def test_declared_vocabulary_has_no_dead_entries() -> None:
    """Every declared state must actually be reachable in the scripts.

    The mirror of the test above, and the one that keeps the vocabulary from
    decaying into a wish-list: a declared state nobody emits is either a
    rename nobody finished or a claim about behavior that does not happen.
    `unknown` is exempt — it is the default initializer, not an emitted value.
    """
    emitted = _emitted_states()
    dead = (DECLARED_STATES - NON_CONVERGENT) - emitted
    assert not dead, (
        f"declared but never emitted: {sorted(dead)} — either the state was "
        f"renamed and the vocabulary was not updated, or it is aspirational."
    )


def test_unknown_is_the_default_and_therefore_the_violation_signal() -> None:
    """`unknown` must remain the initializer, unset by any deliberate exit.

    The refined invariant leans on this: an auto-pr run that ends without
    reaching one of its declared terminal states self-reports as `unknown`,
    which is exactly the non-converged "third state" the invariant forbids.
    If the default ever becomes a real state, non-convergence stops being
    detectable.
    """
    text = AUTO_PR.read_text()
    assert '_autopr_state_final="unknown"' in text
    assert text.count('_autopr_state_final="unknown"') == 1, (
        "`unknown` must be written in exactly one place (the initializer); "
        "a second write would make a real exit path indistinguishable from "
        "a run that fell off the end."
    )


# --------------------------------------------------------------------
# The post-merge boundary (INV-rahib, the "misleading status" clause).
#
# The invariant's second violation class is a run that reports failure for
# work that succeeded: the merge landed, but auto-pr exits non-zero.
#
# auto-pr runs under `set -e`, and commands still run AFTER
# `_autopr_state_final="merged"`. Two of them can abort:
#
#   git checkout "$base"   — refuses when a dirty file differs between the
#                            feature branch and the base. A modified tracker
#                            `.ops` file is exactly that, and is the normal
#                            state of this working tree.
#   read -p "Retry? …"     — returns 1 on EOF, which is every non-interactive
#                            invocation; cleanup_local therefore only prompts
#                            when stdin is a tty.
#
# The guard is a boundary, not per-command guards: once the merge has landed
# the outcome is decided, so the housekeeping after it runs through
# `_autopr_post_merge`, whose `if !` context makes bash ignore `set -e` for
# the whole callee body — including anything the callee itself calls.
#
# These tests drive the REAL extracted functions under PRODUCTION shell flags.
# That distinction is load-bearing: a harness missing the `-e` cannot observe
# this defect at all.
# --------------------------------------------------------------------


def _production_shell_flags() -> str:
    """The `set` line auto-pr actually runs under, read from auto-pr.

    Read from the script rather than hardcoded, so the harness cannot drift
    from production when the flags change (e.g. auto-pr's `-E` for its ERR
    trap). A harness carrying its own flags can miss bugs that only reproduce
    under production's — a function whose failure only aborts under `-e`
    stays green under `set -uo pipefail`.
    """
    for line in AUTO_PR.read_text(encoding="utf-8").splitlines()[:20]:
        if line.startswith("set -"):
            return line
    raise AssertionError("no `set -` line in the first 20 lines of auto-pr")


PROD_SHELL_FLAGS = _production_shell_flags()


def _extract_bash_func(name: str) -> str:
    """Pull one function out of auto-pr; sourcing the script runs dispatch."""
    text = AUTO_PR.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}$", text, re.S | re.M)
    assert m, f"{name}() not found in scripts/auto-pr"
    return m.group(0)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=30,
    )


def _build_post_merge_repo(tmp_path: Path, *, collide: bool) -> Path:
    """A repo sitting where auto-pr sits the instant after a merge lands.

    `collide=True` dirties a file that differs between the feature branch and
    the base, so the post-merge `git checkout` refuses — the tracker-`.ops`
    shape. `collide=False` leaves checkout clean and breaks the remote instead,
    so the failure lands on the `read -p` retry prompt.
    """
    repo = tmp_path / "work"
    server = tmp_path / "srv.git"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", "-b", "dev", str(server)],
                   check=True, timeout=30)
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True, timeout=30)
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"),
                 ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)
    (repo / "ops.txt").write_text("A\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "remote", "add", "origin", str(server))
    _git(repo, "push", "-q", "origin", "dev")
    _git(repo, "checkout", "-qb", "feat/x")
    (repo / "ops.txt").write_text("B\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")

    if collide:
        # Dirty, and differing between feat/x and dev -> checkout refuses.
        (repo / "ops.txt").write_text("C\n")
    else:
        # Checkout will succeed; make `git pull` fail so the retry prompt runs.
        _git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    return repo


def _run_post_merge_tail(repo: Path, *, wrapped: bool) -> tuple[int, str]:
    """Run do_pr's post-merge tail shape and report (exit_code, final_state).

    `wrapped=False` is the CONTROL: the same fixture with the call made bare,
    which must still abort. Without it a passing test cannot distinguish "the
    boundary holds" from "the fixture stopped inducing a failure".
    """
    body = _extract_bash_func("cleanup_local")
    if wrapped:
        body += "\n" + _extract_bash_func("_autopr_post_merge")
        call = '_autopr_post_merge cleanup_local "feat/x" "dev"'
    else:
        call = 'cleanup_local "feat/x" "dev"'
    script = (
        f"{PROD_SHELL_FLAGS}\n"
        f"REPO_ROOT={str(repo)!r}\n"
        f"cd {str(repo)!r}\n"
        f'_autopr_state_final="unknown"\n'
        f"trap '_ec=$?; echo \"SENTINEL $_autopr_state_final $_ec\"' EXIT\n"
        f"{body}\n"
        f'_autopr_state_final="merged"\n'
        f"{call}\n"
        f"exit 0\n"
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=90,
    )
    state = "NO-SENTINEL"
    for line in result.stdout.splitlines():
        if line.startswith("SENTINEL "):
            state = line.split()[1]
    return result.returncode, state


def test_control_bare_post_merge_call_really_does_abort(tmp_path: Path) -> None:
    """Non-vacuity floor: the fixture must break a BARE post-merge call.

    This is the arm that can fail: if the fixture stops inducing an abort,
    it goes red instead of letting the boundary test pass vacuously. Only
    the checkout-collision fixture is a control; the `read -p` fixture no
    longer aborts (cleanup_local's tty guard), and its property is pinned by
    `test_non_interactive_cleanup_skips_instead_of_aborting`.
    """
    repo = _build_post_merge_repo(tmp_path, collide=True)
    rc, _ = _run_post_merge_tail(repo, wrapped=False)
    assert rc != 0, (
        "the fixture no longer induces a post-merge failure, so the boundary "
        "test below would pass vacuously"
    )


def test_merged_run_exits_zero_despite_post_merge_failure(tmp_path: Path) -> None:
    """A merge that landed must report success, whatever housekeeping does.

    The fixture is the live abort site: a dirty file that differs between the
    feature branch and the base, which is what a modified tracker `.ops` file
    is on every run.
    """
    repo = _build_post_merge_repo(tmp_path, collide=True)
    rc, state = _run_post_merge_tail(repo, wrapped=True)
    assert (rc, state) == (0, "merged"), (
        f"a run whose merge landed exited {rc} with final_state={state!r}. "
        f"This is the 41-of-481 shape in .git/AUTOPR_HISTORY.jsonl: post-merge "
        f"housekeeping restated a decided outcome."
    )


def test_non_interactive_cleanup_skips_instead_of_aborting(tmp_path: Path) -> None:
    """With stdin closed, a failed pull must skip cleanly, not abort.

    `read` returns 1 at EOF, so under `set -e` an unguarded retry prompt is
    an abort at attempt 1 of 3. The boundary would catch that abort, but
    catching it is second best: the prompt is the thing that should not fire
    when nobody can answer it. Run bare (unwrapped) so the boundary cannot
    mask a regression.
    """
    repo = _build_post_merge_repo(tmp_path, collide=False)
    rc, state = _run_post_merge_tail(repo, wrapped=False)
    assert (rc, state) == (0, "merged"), (
        f"a bare cleanup_local with stdin closed exited {rc} (state={state!r}); "
        f"the retry prompt is still reachable without a tty"
    )


def test_no_bare_cleanup_local_call_survives_in_auto_pr() -> None:
    """Every `cleanup_local` invocation goes through the boundary.

    Structural complement to the behavioral tests: they prove the wrapper
    works, this proves nothing skips it. A new call site added without the
    wrapper fails here rather than surfacing later as ledger rows.
    """
    text = AUTO_PR.read_text(encoding="utf-8")
    bare = [
        line for line in text.splitlines()
        if re.match(r"^\s*cleanup_local\s+\"", line)
    ]
    assert not bare, (
        f"cleanup_local is invoked without _autopr_post_merge at: {bare}. "
        f"Under `set -euo pipefail` a failure there aborts the run after the "
        f"merge has already landed."
    )


def test_gov_cleanup_records_merged_before_it_does_housekeeping() -> None:
    """gov_cleanup's PR is confirmed merged by the API before cleanup runs.

    So `_autopr_state_final="merged"` must be set BEFORE `cleanup_local`;
    otherwise an abort in between loses the merged fact and the run
    self-reports as `unknown` — the other violation class.
    """
    # Strip comments first. The prose explaining this ordering names
    # cleanup_local, and an index over the raw body finds that mention before
    # the call — the assertion then fails on its own documentation.
    body = "\n".join(
        line for line in _extract_bash_func("gov_cleanup").splitlines()
        if not line.lstrip().startswith("#")
    )
    state_at = body.index('_autopr_state_final="merged"')
    cleanup_at = body.index("cleanup_local")
    assert state_at < cleanup_at, (
        "gov_cleanup sets its terminal state after housekeeping; a housekeeping "
        "abort then converges to `unknown` instead of `merged`."
    )


def test_cleanup_local_does_not_prompt_without_a_tty() -> None:
    """The retry prompt must be reached only when someone can answer it.

    `read -p` returns 1 at EOF, so under `set -e` an unguarded prompt aborts
    the 3-attempt retry loop at attempt 1 on every non-interactive run. Guarding the
    prompt on a tty makes the non-interactive path a deliberate skip rather
    than an abort that happens to be caught by the boundary.
    """
    body = _extract_bash_func("cleanup_local")
    assert "-t 0" in body, (
        "cleanup_local prompts unconditionally; with stdin closed that `read` "
        "is an abort, not a question."
    )
