# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kalub: static source guards for smart-test's baseline scope + coverage gate.

``scripts/smart-test`` is bash, so it contributes no pytest coverage and has no
behavioral harness; the repo convention (cf. ``test_smart_test_slice_robustness``)
is a source-level invariant test. These guards pin the four Step-1 properties of
the WI-kalub fix, each of whose regression reintroduces a concrete failure:

1. **Branch-own baseline (1a/1d).** The per-PR baseline is ``merge-base(HEAD,
   <authoritative-dev>)`` — the branch's own changes — NOT the ``last-green-sha``
   marker. Anchoring on the (routinely 50-commits-stale) marker inflates the
   changed set with already-merged files whose full test sets the affected slice
   does not select, which is the root of both the false-red scoped-coverage
   failures AND the ~whole-suite-every-PR runtime bloat. Whole-codebase coverage
   is enforced separately by full-suite teeth + nightly, not by this per-PR gate.

2. **Word-bounded 'failed' match (1b).** The pass/fail re-derivation must not let
   ``grep -q "failed"`` match the always-present ``4 xfailed`` summary line (which
   silently defeats the green re-confirmation). Use ``[0-9]+ failed``.

3. **'No data' is not a coverage failure (1c).** coverage.py exits 1 with "No data
   to report" when the affected slice didn't exercise a changed source file (e.g.
   subprocess-only); that is NOT a <100% regression (exit 2). The scoped gate must
   distinguish them and not false-red on no-data.

4. **Verdict written to the captured log (1e).** The scoped-coverage verdict +
   summary print to smart-test stdout, but ``.ci/pytest-output.log`` (the file the
   ADR/handoff tell you to read) receives only raw pytest output. The verdict must
   also be written into ``$PYTEST_LOG`` so the captured log carries the green
   signal.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"


def _text() -> str:
    return SMART_TEST.read_text()


def _get_baseline_body(text: str) -> str:
    """Return the body of the ``get_baseline()`` bash function (between its
    opening ``{`` line and the first column-0 ``}``)."""
    lines = text.splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("get_baseline()")),
        None,
    )
    assert start is not None, "could not find get_baseline() in smart-test"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] == "}"),
        None,
    )
    assert end is not None, "could not find the close of get_baseline()"
    return "\n".join(lines[start:end + 1])


def test_smart_test_present() -> None:
    assert SMART_TEST.is_file(), f"smart-test not found at {SMART_TEST}"


def test_baseline_uses_merge_base_not_stale_marker() -> None:
    """1a/1d: baseline is the merge-base with the authoritative dev, and the
    stale last-green-sha marker is NOT consumed as the baseline."""
    body = _get_baseline_body(_text())
    assert "git merge-base HEAD" in body, (
        "get_baseline must resolve the baseline via `git merge-base HEAD "
        "<authoritative-dev>` (branch-own scope). Found no merge-base in the "
        f"function body:\n{body}"
    )
    # The consumption signal is a read of the marker FILE (git show
    # …:last-green-sha.txt); prose that merely *names* the marker to explain why
    # we no longer use it is fine (and desirable documentation).
    assert "last-green-sha.txt" not in body, (
        "get_baseline still consumes the last-green-sha marker as the baseline "
        "(reads …:last-green-sha.txt). Under a routinely-stale marker this "
        "inflates the per-PR changed set with already-merged files, causing "
        "false-red scoped-coverage failures and ~whole-suite runtime. Use "
        "merge-base(HEAD, authoritative-dev); whole-codebase coverage is enforced "
        "by full-suite teeth + nightly (WI-kalub Step 1a/1d)."
    )


def test_baseline_detection_still_failover_aware() -> None:
    """1a/1d must PRESERVE failover-awareness: the merge-base anchor is the
    authoritative remote's dev (selfh under failover), not the stale origin/dev."""
    body = _get_baseline_body(_text())
    assert "CI_FAILOVER_ACTIVE" in body, (
        "get_baseline dropped failover detection; under permanent failover the "
        "merge-base would anchor on a stale origin/dev and over-select the whole "
        "tree. Keep the CI_FAILOVER_ACTIVE branch selecting selfh/dev."
    )


def test_failed_grep_is_word_bounded() -> None:
    """1b: the pass/fail re-derivation must not match 'xfailed' when testing for
    'failed'."""
    text = _text()
    assert '! grep -q "failed"' not in text, (
        "smart-test still uses the bare `! grep -q \"failed\"`, which matches the "
        "always-present '4 xfailed' summary and defeats the green re-confirmation. "
        "Use `! grep -qE \"[0-9]+ failed\"` (WI-kalub Step 1b)."
    )
    assert re.search(r'grep -qE "\[0-9\]\+ failed"', text), (
        "expected a word/count-bounded failed match (`grep -qE \"[0-9]+ failed\"`) "
        "in the pass/fail re-derivation."
    )


def test_scoped_coverage_gate_treats_no_data_as_non_fatal() -> None:
    """1c: the scoped gate must special-case coverage.py's 'No data to report'
    (exit 1) and NOT treat it as a <100% failure (exit 2)."""
    text = _text()
    assert "No data to report" in text, (
        "the scoped coverage gate does not special-case coverage.py's 'No data to "
        "report' (exit 1). It would false-red when the affected slice didn't "
        "exercise a changed source file (e.g. subprocess-only). Distinguish it "
        "from a real <100% failure (WI-kalub Step 1c)."
    )


def test_scoped_coverage_verdict_written_to_pytest_log() -> None:
    """1e: the scoped-coverage verdict must be appended to $PYTEST_LOG so the
    captured .ci/pytest-output.log contains the green signal."""
    text = _text()
    assert re.search(r'(tee -a|>>)\s*"?\$PYTEST_LOG"?', text), (
        "the scoped-coverage verdict is not written into $PYTEST_LOG; the captured "
        "log (.ci/pytest-output.log) — the file the ADR/handoff tell you to read — "
        "will not contain the green signal (WI-kalub Step 1e)."
    )


def test_captured_pytest_run_neutralizes_forced_color() -> None:
    """The captured (non-raw) pytest run must neutralize FORCE_COLOR/COLORTERM so
    the log written to $PYTEST_LOG is plain text.

    Under a FORCE_COLOR dev shell, pytest colorizes even a redirected file. The
    summary line then starts with an ANSI escape, so summarize's anchored
    ``^=+ …`` result-line grep (and the ``^FAILED`` re-derivation grep) fail to
    match; the unguarded result-line grep aborts smart-test under
    ``set -e + pipefail`` with a false exit 1 on a *green* run (the WI-kalub
    dev-loop symptom — CI is unaffected, it has no FORCE_COLOR). Neutralizing the
    color-forcing env for the captured run aligns the local dev loop with CI."""
    cap = [
        ln for ln in _text().splitlines()
        if "pytest " in ln
        and '"$PYTEST_LOG" 2>&1' in ln
        and not ln.lstrip().startswith("#")
    ]
    assert cap, "could not find the captured pytest invocation (writes $PYTEST_LOG)"
    for ln in cap:
        assert "env -u FORCE_COLOR" in ln, (
            "the captured pytest run does not neutralize FORCE_COLOR, so under a "
            "FORCE_COLOR dev shell $PYTEST_LOG is ANSI-colorized and summarize's "
            "anchored greps abort smart-test (false exit 1 on green). Prefix the "
            f"run with `env -u FORCE_COLOR -u COLORTERM` (WI-kalub). Line:\n  {ln}"
        )


def test_summarize_result_grep_cannot_abort() -> None:
    """Defense in depth: summarize's result-line grep must be guarded (``|| true``)
    so a non-matching log (e.g. still colorized) can never abort smart-test under
    ``set -e + pipefail`` — the exit code must reflect the tests, not a summary
    formatting mismatch."""
    grep_lines = [ln for ln in _text().splitlines() if "result_line=$(grep" in ln]
    assert grep_lines, "could not find summarize's result_line grep"
    for ln in grep_lines:
        assert "|| true" in ln, (
            "summarize's result_line grep lacks a `|| true` guard; a non-matching "
            "log aborts smart-test under set -e+pipefail before it can report the "
            f"result (WI-kalub). Line:\n  {ln}"
        )
