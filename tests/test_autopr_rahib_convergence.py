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
    # Deliberately loose. This previously pinned the literal source line
    # `mergeable=$(echo "$API_RESPONSE" | json_field "mergeable")`, which
    # asserts an IMPLEMENTATION TEXT rather than a behavior — and that exact
    # line was the site of the INV-rahib Surface 1 defect, so the assertion
    # broke precisely when the bug was fixed and pointed the fixer at keeping
    # it. The behavioral `_run_gate` cases below prove the gate consults
    # mergeability; this only needs to keep the dependency from being deleted.
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

    Scoped deliberately to the explicit `false`. The docstring here used to
    read "false (or absent)" while only ever feeding `false`; absent and null
    are now covered by their own cases below, because they are NOT the same
    fact and the gate's handling of them differs in consequence.
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
    Before the fix this returned 1 and emitted nothing at all — the gate did
    not merely decide wrongly, it decided invisibly.
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
# Surface 3 (2026-06-17) — poll_ci confirmation re-poll under concurrency.
#
# A single failure snapshot during concurrent CI activity can be transiently
# inconsistent (e.g. the hung-run retry's PR-close cancels its in-flight run,
# whose error statuses shadow the live run's still-pending status for the same
# context). The fix requires the failure-with-no-pending signal to be STABLE
# across FAILURE_CONFIRM_POLLS consecutive polls before poll_ci returns 1.
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
    """INV-rahib incident shape: a transient failure-with-no-pending snapshot
    followed by the live run reappearing pending and then succeeding must
    converge to 0 — NOT a false explicit failure (the regression this fixes)."""
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
# missing key and '' on a parse failure), which is the root of Surface 1.
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
# The declared terminal-state vocabulary (INV-rahib refined clause (a)/(c)).
#
# The invariant's original wording — "merged or explicit failure, no third
# state" — cannot be satisfied by any conforming implementation: `queued_vpr`
# is a deliberate, documented, human-approved outcome (WI-hajif Ruling 2),
# and so are `governance_pending` and `noop_empty_queue`. Owner ruling
# 2026-08-03 replaced it with a DECLARED CLOSED SET: every run must record a
# state drawn from this vocabulary, and `unknown` (the default) is the
# violation signal.
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
        "flush_push_rejected",
        "push_rejected_diverged",
        # Set by ci_verdict_permits_merge and propagated via CI_VERDICT_STATE.
        "ci_failed",
        "ci_timeout",
        "ci_hung",
        "ci_unknown",
    }
)

# Deliberate non-merge outcomes. Converged by ruling, not by accident.
CONVERGED_DELIBERATE = frozenset(
    {"queued_vpr", "governance_pending", "noop_empty_queue"}
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
    state added without classifying it fails here, which is the point: the
    2026-06-17 validation criterion went unevaluated for 579 commits partly
    because nothing ever forced the state list to be looked at.
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
    and that is exactly the "third state" the original wording was reaching
    for. If the default ever becomes a real state, non-convergence stops
    being detectable.
    """
    text = AUTO_PR.read_text()
    assert '_autopr_state_final="unknown"' in text
    assert text.count('_autopr_state_final="unknown"') == 1, (
        "`unknown` must be written in exactly one place (the initializer); "
        "a second write would make a real exit path indistinguishable from "
        "a run that fell off the end."
    )
