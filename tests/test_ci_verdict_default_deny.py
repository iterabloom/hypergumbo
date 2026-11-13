# SPDX-License-Identifier: AGPL-3.0-or-later
"""A CI verdict may PERMIT a merge only by being zero (WI-ditav / WI-ninar).

Two properties keep a PR from merging while CI is pending: no consumer may
treat an unrecognised verdict as permission, and the hung-runner verdict must
not fire on a healthy pipeline. Broken together, they compose into exactly
that merge.

DEFAULT-DENY VERDICTS (WI-ditav). A consumer of ``poll_ci`` that decides
whether to merge by enumerating the codes that BLOCK::

    poll_ci "$HEAD_SHA" || poll_result=$?
    if   [[ $poll_result -eq 1 ]]; then exit 1
    elif [[ $poll_result -eq 2 ]]; then exit 2
    fi
    ... falls through to the merge ...

fails open the moment ``poll_ci`` allocates a new code: exit 3 (hung runner)
falls straight through to the merge in exactly this shape. A guard that
branches on ``rc == 0`` and treats everything else as refusal does not have
the hazard (L54), so every merge decision routes through
``ci_verdict_permits_merge``, which permits only 0.

DISPATCH DETECTION (WI-ninar). ``poll_ci`` returns 3 when no pipeline was
dispatched within ``CI_STALE_PENDING_SECONDS``. "Dispatched" means a status
context EXISTS, not that one has gone non-pending: Woodpecker posts exactly
ONE aggregate context, which stays ``pending`` for the entire pipeline, so a
"has anything gone non-pending?" predicate is never satisfied and declares
every pipeline that outlives the threshold (300 s default) a hung runner.

The tests below are behavioural, not textual. ``poll_ci`` is driven with
``api_get`` overridden in the sourced shell, so the decision is exercised
against real payloads with no forge -- which is what distinguishes the two
dispatch predicates (same inputs, different verdict).
"""

# covers: scripts/lib/forgejo-api.sh
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGE_LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"


def _bash(snippet: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a snippet with the forge library sourced."""
    return subprocess.run(
        ["bash", "-c", f"source '{FORGE_LIB}' >/dev/null 2>&1\n{snippet}"],
        capture_output=True, text=True, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# WI-ditav: the verdict chokepoint
# ---------------------------------------------------------------------------

def test_zero_is_the_only_permitting_verdict() -> None:
    """Exit 0 permits; nothing else does."""
    assert _bash("ci_verdict_permits_merge 0").returncode == 0


@pytest.mark.parametrize("code", [1, 2, 3])
def test_known_failure_verdicts_refuse(code: int) -> None:
    """The three allocated non-zero codes each refuse, preserving their code."""
    result = _bash(f"ci_verdict_permits_merge {code}")
    assert result.returncode == code, (
        f"verdict {code} must refuse and preserve its exit code; got "
        f"{result.returncode}"
    )


@pytest.mark.parametrize("code", [4, 7, 42])
def test_unallocated_verdicts_refuse(code: int) -> None:
    """A code nobody has allocated yet must still refuse.

    This is the property an enumerate-the-blocking-codes guard lacks: a guard
    that lists what blocks it fails open the moment the producer learns a new
    verdict.
    """
    result = _bash(f"ci_verdict_permits_merge {code}")
    assert result.returncode != 0, (
        f"an unallocated verdict ({code}) was treated as PERMISSION to merge"
    )
    assert "unrecognised" in result.stdout.lower(), (
        f"an unknown verdict must say so rather than refusing silently:\n{result.stdout}"
    )


def test_non_numeric_verdict_refuses() -> None:
    """A malformed verdict is a refusal, not a crash and not a permit."""
    result = _bash("ci_verdict_permits_merge ''; echo RC=$?")
    assert "RC=0" not in result.stdout, (
        f"an empty verdict was treated as permission:\n{result.stdout}"
    )


def test_verdict_state_is_exported_for_callers() -> None:
    """Callers set _autopr_state_final from the verdict; the names must exist."""
    for code, expected in ((1, "ci_failed"), (2, "ci_timeout"), (3, "ci_hung")):
        out = _bash(
            f"ci_verdict_permits_merge {code} >/dev/null; echo \"$CI_VERDICT_STATE\"",
        ).stdout.strip()
        assert out == expected, f"verdict {code} → CI_VERDICT_STATE={out!r}"


# ---------------------------------------------------------------------------
# WI-ninar: the dispatch test
# ---------------------------------------------------------------------------

_STUB = """
api_get() {{ API_RESPONSE='{payload}'; return 0; }}
CI_POLL_NO_SLEEP=1 CI_STALE_PENDING_SECONDS=0 CI_TIMEOUT_SECONDS={timeout} \
  poll_ci deadbeef >/dev/null 2>&1
echo "RC=$?"
"""

_ONE_PENDING_AGGREGATE = (
    '{"state":"pending","statuses":['
    '{"context":"ci/woodpecker/pr/woodpecker","state":"pending"}]}'
)
_NO_CONTEXT_AT_ALL = '{"state":"pending","statuses":[]}'
_SUCCESS = (
    '{"state":"success","statuses":['
    '{"context":"ci/woodpecker/pr/woodpecker","state":"success"}]}'
)


def test_a_pending_aggregate_context_is_not_a_hung_runner() -> None:
    """The WI-ninar case, in the exact shape Woodpecker produces.

    One aggregate context, pending, with the stale threshold already elapsed.
    A "has anything gone non-pending?" predicate answers no and returns 3
    immediately. The correct predicate ("does a context EXIST?") answers yes:
    Woodpecker posts that context when it creates the run, so its presence is
    proof of dispatch. The run should then be allowed to keep going and time out
    normally (2), never be declared hung.
    """
    out = _bash(
        _STUB.format(payload=_ONE_PENDING_AGGREGATE, timeout=1), timeout=120,
    ).stdout
    assert "RC=3" not in out, (
        "a pending aggregate context was declared a HUNG RUNNER. On Woodpecker "
        "that fires on every pipeline outliving the threshold, because the "
        "single context stays pending for the whole run."
    )
    assert "RC=2" in out, f"expected a normal poll timeout (2), got: {out}"


def test_no_context_at_all_is_still_a_dispatch_failure() -> None:
    """Nothing posted still means nothing started.

    Without this the dispatch predicate would be indistinguishable from
    deleting the check.
    """
    out = _bash(
        _STUB.format(payload=_NO_CONTEXT_AT_ALL, timeout=1), timeout=120,
    ).stdout
    assert "RC=3" in out, (
        f"an empty status list past the threshold must report exit 3; got {out}"
    )


# ---------------------------------------------------------------------------
# Recurrence guard: the call sites must keep routing through the chokepoint
# ---------------------------------------------------------------------------

MERGE_PR = REPO_ROOT / "scripts" / "merge-pr"
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


def test_helper_has_exactly_one_definition() -> None:
    """One fact, one home. A second copy is drift waiting to happen."""
    defs = [
        p for p in (FORGE_LIB, MERGE_PR, AUTO_PR)
        if "ci_verdict_permits_merge()" in p.read_text()
    ]
    assert defs == [FORGE_LIB], (
        f"ci_verdict_permits_merge must be defined once, in the forge library; "
        f"found definitions in {[p.name for p in defs]}"
    )


def test_merge_pr_has_no_hand_rolled_verdict_gate() -> None:
    """merge-pr's ONLY verdict decision must be the chokepoint.

    Scope is deliberate (L23). Applied to auto-pr, the rule "a comparison
    against a non-zero code is only legitimate as a retry guard" gives a FALSE
    POSITIVE on auto-pr's exit-2 recovery branch, which compares against 2 to
    decide whether to close-and-repush and then RE-POLLS -- a recovery
    construct, not a merge gate, and textually indistinguishable from one at
    line level. A rule that needs a per-site exemption list is a rule that
    gets exempted into uselessness, so it is not applied there.

    merge-pr has exactly one poll site, no retry logic and no recovery branch,
    so in that file any comparison of a poll result against a code IS the merge
    gate -- precisely the construct that lets a PR merge with CI pending.
    auto-pr is covered by the call-count guard below instead.
    """
    text = MERGE_PR.read_text()
    offenders = [
        f"{n}: {ln.strip()}"
        for n, ln in enumerate(text.splitlines(), 1)
        if "poll_result -eq" in ln and not ln.strip().startswith("#")
    ]
    assert not offenders, (
        "merge-pr is gating a merge on a hand-rolled verdict comparison again. "
        "That shape enumerates the codes that BLOCK and therefore fails open "
        "for every code added later:\n  " + "\n  ".join(offenders)
    )


def test_both_scripts_call_the_chokepoint() -> None:
    """Non-vacuity floor for the test above.

    Without this, deleting every poll_result comparison AND every guard would
    satisfy the invariant while removing the protection entirely.
    """
    for path, minimum in ((MERGE_PR, 1), (AUTO_PR, 3)):
        n = path.read_text().count("ci_verdict_permits_merge ")
        assert n >= minimum, (
            f"{path.name} calls ci_verdict_permits_merge {n} time(s); expected "
            f"at least {minimum}. A merge decision has stopped routing through "
            "the chokepoint."
        )


def test_success_still_passes() -> None:
    """Non-vacuity floor (L17): the harness can produce a PASS, so a refusal
    above is a real verdict rather than the stub failing to drive poll_ci."""
    out = _bash(_STUB.format(payload=_SUCCESS, timeout=5), timeout=120).stdout
    assert "RC=0" in out, f"the stub never reaches a passing verdict: {out}"
