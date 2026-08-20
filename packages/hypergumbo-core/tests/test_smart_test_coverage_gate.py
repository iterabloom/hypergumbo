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


def test_baseline_anchors_on_merge_base_with_authoritative_dev() -> None:
    """1a/1d: the merge-base anchor is the authoritative remote's dev.

    THIS TEST USED TO ASSERT THE OPPOSITE (WI-kasin) — it required
    ``get_baseline`` to keep a failover branch re-anchoring onto a second
    remote. That was right while the failover existed; WI-hajif step 1 retired
    it, and the retirement's own recurrence gate forbids the string this test
    demanded. Both failed on clean dev and neither was selected by any ordinary
    PR, so the contradiction sat in the suite unnoticed.

    What survives the retirement is the anchoring property itself, which is
    what 1a/1d was actually about: the baseline is the branch's own fork point
    against the authoritative dev, not a stale marker. The sibling test above
    pins the negative half (no ``last-green-sha.txt`` consumption); this pins
    the positive half.
    """
    body = _get_baseline_body(_text())
    assert "git merge-base HEAD" in body, (
        "get_baseline no longer computes a merge-base; without it the baseline "
        "is not the branch's own fork point and the changed set inflates with "
        "already-merged files."
    )
    assert "origin/dev" in body, (
        "get_baseline does not anchor on origin/dev, the only authoritative "
        "remote since WI-hajif retired the failover."
    )


def test_exit_code_is_sourced_from_pytest_not_re_derived() -> None:
    """1b, superseded by INV-vilag: the run's verdict comes from pytest itself.

    WI-kalub Step 1b hardened an affirmative-green re-derivation clause (``N
    passed`` && ! ``N failed`` -> 0) so its ``failed`` match could not be
    satisfied by the always-present ``N xfailed`` token. INV-vilag removed that
    clause outright: it could only ever de-escalate to success, which is exactly
    how a collection-error run (``N passed`` + ``N errors``, no ``FAILED``) was
    reported green. The 1b property is now satisfied STRUCTURALLY — there is no
    ``failed``-matching green clause left to mis-word-bound — so this guard pins
    the stronger successor property instead of the clause it protected.
    """
    text = _text()
    assert '! grep -q "failed"' not in text, (
        "smart-test reintroduced the bare `! grep -q \"failed\"`, which matches the "
        "always-present '4 xfailed' summary token (WI-kalub Step 1b)."
    )
    cap = [
        ln for ln in text.splitlines()
        if "pytest " in ln
        and '"$PYTEST_LOG" 2>&1' in ln
        and not ln.lstrip().startswith("#")
    ]
    assert cap, "could not find the captured pytest invocation (writes $PYTEST_LOG)"
    for ln in cap:
        assert "|| exit_code=$?" in ln, (
            "the captured pytest run does not preserve pytest's OWN exit status. "
            "With `|| true`, PIPESTATUS reflects the `true` and the real status is "
            "discarded, so a collection-error run (pytest exits 2, prints `N errors` "
            "and no `FAILED`) reconstructs as green and the caller then computes a "
            "coverage verdict over modules that never imported (INV-vilag). Capture "
            f"it with `|| exit_code=$?`. Line:\n  {ln}"
        )
    # Scope to CODE lines: the fix's own explanatory comment names PIPESTATUS.
    code_pipestatus = [
        ln for ln in text.splitlines()
        if "PIPESTATUS" in ln and not ln.lstrip().startswith("#")
    ]
    assert not code_pipestatus, (
        "smart-test derives the pytest verdict from PIPESTATUS again. After "
        "`cmd || true` PIPESTATUS reports the `true`, so it is always 0 — the "
        "masking that made INV-vilag invisible. Use `|| exit_code=$?`. Line(s):\n  "
        + "\n  ".join(code_pipestatus)
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


# ---------------------------------------------------------------------------
# INV-vilag: run_pytest must not report green when modules fail to COLLECT.
#
# The preceding guards are source-level (smart-test is bash and contributes no
# pytest coverage, so that is the file's established convention). These two are
# BEHAVIORAL: they sed-extract the real `summarize_pytest_output` + `run_pytest`
# out of the shipped script and drive them against a stub `pytest`, so they
# exercise the actual code rather than pinning its text.
#
# The defect: `pytest … > "$PYTEST_LOG" 2>&1 || true` makes PIPESTATUS[0] always
# 0, so the re-derivation started from 0 and the affirmative-green clause
# (`N passed` && ! `N failed`) could only ever HOLD it at 0 — a collection-error
# run satisfies neither failure clause, because pytest reports `ERROR`/`N errors`
# and not `FAILED`. run_pytest returned 0, the caller's `TEST_EXIT -eq 0` gate
# then ran the scoped coverage check WITHOUT the modules that never imported, and
# smart-test exited 0 printing "✅ … 11756 passed, 3 errors".
# ---------------------------------------------------------------------------

_STUB_PYTEST = """#!/usr/bin/env bash
cat <<'LOG'
{log_body}
LOG
exit {rc}
"""

_DRIVER = """set -euo pipefail
export PATH="$PWD/bin:$PATH"
RAW_OUTPUT=false
SMART_TEST_MODE=targeted
TARGETED_TEST_COUNT=1
TOTAL_TEST_COUNT=1
TARGETED_SOURCE_COUNT=1
TOTAL_SOURCE_COUNT=1
CHANGED_SOURCE_FILES=""
BASE_BRANCH=dev
MANIFEST_DIR="$PWD/.ci"
PYTEST_LOG="$MANIFEST_DIR/pytest-output.log"
JUNIT_XML="$MANIFEST_DIR/pytest-results.xml"
COV_PATHS=()
mkdir -p "$MANIFEST_DIR"
source ./fns.sh
set +e
run_pytest "sometests/"
echo "RUN_PYTEST_RETURNED=$?"
"""


def _drive_run_pytest(tmp_path: Path, log_body: str, stub_rc: int) -> tuple[int, str]:
    """Run the REAL run_pytest from scripts/smart-test against a stub pytest.

    Returns ``(return_code_of_run_pytest, combined_output)``.
    """
    import shutil
    import subprocess

    bash = shutil.which("bash")
    assert bash, "bash is required to drive smart-test's own functions"

    (tmp_path / "bin").mkdir()
    stub = tmp_path / "bin" / "pytest"
    stub.write_text(_STUB_PYTEST.format(log_body=log_body, rc=stub_rc))
    stub.chmod(0o755)

    text = _text()
    lines = text.splitlines()

    def _extract(name: str) -> str:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{name}() {{"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
        return "\n".join(lines[start:end + 1])

    (tmp_path / "fns.sh").write_text(
        _extract("summarize_pytest_output") + "\n" + _extract("run_pytest") + "\n"
    )
    (tmp_path / "drive.sh").write_text(_DRIVER)

    proc = subprocess.run(
        [bash, "drive.sh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    marker = "RUN_PYTEST_RETURNED="
    assert marker in out, f"driver did not reach the end; output:\n{out}"
    rc = int(out.rsplit(marker, 1)[1].split()[0])
    return rc, out


def test_run_pytest_reports_failure_when_modules_fail_to_collect(
    tmp_path: Path,
) -> None:
    """INV-vilag: a collection-error run must NOT be reported as green.

    pytest exits 2 and prints ``N passed, N errors`` with no ``FAILED`` line. If
    run_pytest returns 0 here, the caller's ``TEST_EXIT -eq 0`` gate computes the
    scoped coverage verdict over a run whose erroring modules never imported —
    the coverage claim is made about tests that did not execute.
    """
    rc, out = _drive_run_pytest(
        tmp_path,
        log_body=(
            "ERROR packages/hypergumbo-lang-mainstream/tests/BRANCHES_test_ini.py\n"
            "ERROR packages/hypergumbo-lang-mainstream/tests/BRANCHES_test_properties.py\n"
            "=========== 11756 passed, 2 errors in 214.44s ==========="
        ),
        stub_rc=2,
    )
    assert rc != 0, (
        "run_pytest returned 0 for a run in which test modules failed to COLLECT "
        "(pytest exited 2). The exit code must come from pytest itself, not from a "
        "log re-derivation whose affirmative-green clause matches `N passed` while "
        "`N errors` satisfies neither failure clause (INV-vilag).\n" + out
    )
    assert "✅" not in out, (
        "run_pytest printed a green checkmark for a collection-error run; the "
        "summary line's error count is the signal, not the checkmark.\n" + out
    )


def test_run_pytest_still_reports_green_on_a_clean_run(tmp_path: Path) -> None:
    """Positive control for the guard above.

    Without this, a harness that always returned non-zero would make the
    INV-vilag test pass while proving nothing (pt.39b: establish a positive
    control before believing a null result).
    """
    rc, out = _drive_run_pytest(
        tmp_path,
        log_body="=========== 11756 passed, 2 xfailed in 214.44s ===========",
        stub_rc=0,
    )
    assert rc == 0, f"run_pytest must still return 0 on a genuinely green run\n{out}"
    assert "✅" in out, f"a genuinely green run should still print the ✅\n{out}"
