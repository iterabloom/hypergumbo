# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/audit-autopr-convergence (WI-lapap).

The reader INV-rahib's convergence ledger never had. The ledger
(``.git/AUTOPR_HISTORY.jsonl``) was added so that "does auto-pr always end in a
declared terminal state" could be answered over a sequence of runs instead of
organically; it then accumulated 483 rows holding 41 merged-and-exited-1 runs
and 14 undeclared-terminal-state runs, and nothing read it for six weeks.

The property these tests care about most is **absent is not empty is not
clean**. This repository's recurring defect is a zero that means "never
modelled" being read as a zero that means "nothing wrong" — so an audit whose
ledger is missing, empty or unreadable must say *I cannot conclude*, never
*converged*. An instrument that reports all-clear when it read nothing is worse
than no instrument, because it manufactures the reassurance that stopped anyone
looking in the first place.

The ledger lives inside ``.git/``, so it is local to a clone and CI can never
see it. That is why this is a script the agent runs and a stop-hook nudge,
rather than a gate: there is no shared artifact to gate on.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS / "audit-autopr-convergence"


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "audit_autopr_convergence", str(SCRIPT_PATH)
    )
    spec = importlib.util.spec_from_loader("audit_autopr_convergence", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


aac = _load()


# --- fixtures --------------------------------------------------------------


def _row(state: str, exit_code: int, pr: int = 1, sha: str | None = "abc123") -> str:
    return json.dumps(
        {
            "exit_code": exit_code,
            "final_state": state,
            "merged_sha": sha,
            "pr_number": pr,
            "pr_url": f"https://example.invalid/pull/{pr}",
            "timestamp": f"2026-09-{(pr % 28) + 1:02d}T00:00:00Z",
        }
    )


def _ledger(tmp_path: Path, rows: list[str], name: str = "led.jsonl") -> Path:
    p = tmp_path / name
    p.write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    return p


# --- absent is not empty is not clean --------------------------------------


def test_missing_ledger_cannot_conclude_and_never_says_converged(tmp_path: Path) -> None:
    """The load-bearing test. A ledger that does not exist proves nothing."""
    rc, out = aac.run([str(tmp_path / "nope.jsonl")])
    assert rc == aac.EXIT_CANNOT_CONCLUDE, (
        f"a missing ledger returned rc={rc}; it must be distinguishable from "
        f"both a clean verdict ({aac.EXIT_OK}) and a violation ({aac.EXIT_VIOLATION})"
    )
    assert "converged" not in out.lower()
    assert "no ledger" in out.lower()


def test_empty_ledger_cannot_conclude(tmp_path: Path) -> None:
    """A ledger with no rows means auto-pr never ran here, not that it behaved.

    Separate from the missing case on purpose: the file existing tells you the
    machinery is wired, which is a different fact from it having produced
    evidence, and collapsing the two would repeat the defect one level in.
    """
    rc, out = aac.run([str(_ledger(tmp_path, []))])
    assert rc == aac.EXIT_CANNOT_CONCLUDE
    assert "converged" not in out.lower()
    assert "0 invocations" in out.lower() or "no invocations" in out.lower()


def test_unreadable_ledger_cannot_conclude(tmp_path: Path) -> None:
    """A directory where the ledger should be is an error, not an all-clear."""
    d = tmp_path / "led.jsonl"
    d.mkdir()
    rc, out = aac.run([str(d)])
    assert rc == aac.EXIT_CANNOT_CONCLUDE
    assert "converged" not in out.lower()


# --- the clean case must really be reachable (non-vacuity) -----------------


def test_clean_ledger_reports_converged(tmp_path: Path) -> None:
    """The arm that proves the others are not just 'always nonzero'.

    Without this, an audit hard-wired to exit 1 would pass every violation test
    above and be useless.
    """
    rows = [_row("merged", 0, pr=i) for i in range(1, 6)]
    rows.append(_row("ci_failed", 1, pr=6, sha=None))
    rows.append(_row("queued_vpr", 0, pr=7, sha=None))
    rc, out = aac.run([str(_ledger(tmp_path, rows))])
    assert rc == aac.EXIT_OK, f"a clean ledger returned rc={rc}:\n{out}"
    assert "converged" in out.lower()


# --- the two violation classes ---------------------------------------------


def test_merged_with_nonzero_exit_is_a_violation(tmp_path: Path) -> None:
    """Class 1: the run merged and reported failure (INV-rahib, 41 of 481)."""
    rows = [_row("merged", 0, pr=1), _row("merged", 1, pr=2)]
    rc, out = aac.run([str(_ledger(tmp_path, rows))])
    assert rc == aac.EXIT_VIOLATION
    assert "1" in out
    assert "merged" in out.lower() and "exit" in out.lower()


def test_already_merged_with_nonzero_exit_is_also_a_violation(tmp_path: Path) -> None:
    """`already_merged` is a converged-merged state too, so it carries the
    same obligation. Pinning it separately because auto-pr reaches it from a
    different path (_autopr_handle_already_merged) that had the same defect."""
    rows = [_row("already_merged", 1, pr=1)]
    rc, _ = aac.run([str(_ledger(tmp_path, rows))])
    assert rc == aac.EXIT_VIOLATION


def test_undeclared_terminal_state_is_a_violation(tmp_path: Path) -> None:
    """Class 2: `unknown` is the initializer, so reaching it means the run fell
    off the end (owner ruling 2026-08-03)."""
    rows = [_row("merged", 0, pr=1), _row("unknown", 1, pr=2, sha=None)]
    rc, out = aac.run([str(_ledger(tmp_path, rows))])
    assert rc == aac.EXIT_VIOLATION
    assert "unknown" in out.lower()


def test_undeclared_with_exit_zero_is_reported_as_the_worst_shape(
    tmp_path: Path,
) -> None:
    """exit 0 + no terminal state + no merge = success claimed for nothing.

    One such row exists in the live ledger. It must not be tallied silently
    alongside the exit-1 undeclared runs, because the two differ in what they
    told the caller.
    """
    rows = [_row("unknown", 0, pr=1, sha=None)]
    rc, out = aac.run([str(_ledger(tmp_path, rows))])
    assert rc == aac.EXIT_VIOLATION
    assert "exit 0" in out.lower() or "claimed success" in out.lower()


# --- a malformed row must shrink confidence, not the denominator -----------


def test_malformed_row_is_reported_not_skipped(tmp_path: Path) -> None:
    """A truncated line must not quietly reduce the population.

    Dropping it would make the audit's own denominator a function of file
    corruption, and a corrupted ledger would trend toward looking cleaner.
    """
    rows = [_row("merged", 0, pr=1), "{not json", _row("merged", 0, pr=2)]
    rc, out = aac.run([str(_ledger(tmp_path, rows))])
    assert rc == aac.EXIT_CANNOT_CONCLUDE, (
        "an unparseable row left the verdict at a confident value; a ledger "
        "that cannot be fully read cannot support an all-clear"
    )
    assert "unparseable" in out.lower() or "malformed" in out.lower()


def test_blank_lines_are_not_malformed(tmp_path: Path) -> None:
    """A trailing newline is not corruption; only real garbage is."""
    p = tmp_path / "led.jsonl"
    p.write_text(_row("merged", 0) + "\n\n", encoding="utf-8")
    rc, _ = aac.run([str(p)])
    assert rc == aac.EXIT_OK


# --- windowing --------------------------------------------------------------


def test_window_limits_to_the_most_recent_runs(tmp_path: Path) -> None:
    """`--window` is what keeps a permanent historical backlog from nagging.

    An append-only ledger never forgets, so a nudge over all history would fire
    forever and be tuned out — which is how an instrument stops being read, the
    exact failure this audit exists to end.
    """
    rows = [_row("merged", 1, pr=1)] + [_row("merged", 0, pr=i) for i in range(2, 12)]
    path = str(_ledger(tmp_path, rows))
    assert aac.run([path])[0] == aac.EXIT_VIOLATION, "full history must still see it"
    rc, out = aac.run([path, "--window", "5"])
    assert rc == aac.EXIT_OK, f"the old violation is outside the window:\n{out}"


def test_window_larger_than_the_ledger_is_not_an_error(tmp_path: Path) -> None:
    rows = [_row("merged", 0, pr=1)]
    rc, _ = aac.run([str(_ledger(tmp_path, rows)), "--window", "500"])
    assert rc == aac.EXIT_OK


# --- machine-readable output ------------------------------------------------


def test_json_output_carries_the_counts_and_the_verdict(tmp_path: Path) -> None:
    rows = [_row("merged", 0, pr=1), _row("merged", 1, pr=2), _row("unknown", 1, pr=3)]
    rc, out = aac.run([str(_ledger(tmp_path, rows)), "--json"])
    payload = json.loads(out)
    assert rc == aac.EXIT_VIOLATION
    assert payload["verdict"] == "violation"
    assert payload["total"] == 3
    assert payload["merged_nonzero_exit"] == 1
    assert payload["undeclared_state"] == 1
    assert payload["by_state"]["merged"]["0"] == 1


def test_json_output_for_a_missing_ledger_says_cannot_conclude(tmp_path: Path) -> None:
    """The JSON arm must carry the same absent-is-not-clean property as the
    text arm; a consumer reading only JSON must not be told `ok`."""
    rc, out = aac.run([str(tmp_path / "nope.jsonl"), "--json"])
    payload = json.loads(out)
    assert rc == aac.EXIT_CANNOT_CONCLUDE
    assert payload["verdict"] == "cannot_conclude"
    assert payload["total"] == 0


# --- the executable itself --------------------------------------------------


def test_script_runs_as_a_subprocess_and_exits_nonzero_on_violation(
    tmp_path: Path,
) -> None:
    """The functions above are the logic; this pins that the file is actually
    an executable entry point with the same exit contract."""
    path = _ledger(tmp_path, [_row("merged", 1, pr=1)])
    r = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(path)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "merged" in r.stdout.lower()


def test_script_default_path_is_the_repo_ledger() -> None:
    """With no argument the audit reads this clone's real ledger.

    Pinned because a default that silently pointed somewhere else would make
    every unattended invocation a no-op — the shape of the defect being fixed.
    """
    assert aac.default_ledger_path(REPO_ROOT) == REPO_ROOT / ".git" / "AUTOPR_HISTORY.jsonl"


# --- attribution: three states, not two (WI-nazoj) --------------------------


def _undeclared(pr: int, site, *, instrumented: bool = True) -> str:
    """An undeclared-terminal-state row.

    ``instrumented=False`` OMITS the key entirely, which is what every row
    written before WI-nazoj looks like. That is a different fact from a row
    whose ``abort_site`` is null, and the audit must not flatten them.
    """
    row = {
        "exit_code": 1,
        "final_state": "unknown",
        "merged_sha": None,
        "pr_number": pr,
        "pr_url": f"https://example.invalid/pull/{pr}",
        "timestamp": "2026-09-20T00:00:00Z",
    }
    if instrumented:
        row["abort_site"] = site
        row["abort_command"] = "git checkout dev" if site else None
    return json.dumps(row)


def test_an_undeclared_row_shows_where_it_died(tmp_path: Path) -> None:
    """The whole point of the attribution: a line number, not a shrug.

    Fourteen of these accumulated over five weeks with nothing to distinguish
    them from each other, so nobody could tell whether they shared a cause.
    """
    rc, out = aac.run([str(_ledger(tmp_path, [_undeclared(1, "auto-pr:2114")]))])
    assert rc == aac.EXIT_VIOLATION
    assert "auto-pr:2114" in out, out


def test_a_row_from_before_the_instrumentation_is_not_called_unattributed(
    tmp_path: Path,
) -> None:
    """ABSENT IS NOT EMPTY, at the ledger's own schema boundary.

    A row with no ``abort_site`` KEY was written by a version of auto-pr that
    could not record one. Reporting it as "abort site not recorded" would
    describe a limitation of the run rather than of the instrument, and would
    make the historical backlog look like fourteen live instrument failures.
    """
    rc, out = aac.run(
        [str(_ledger(tmp_path, [_undeclared(1, None, instrumented=False)]))]
    )
    assert rc == aac.EXIT_VIOLATION
    low = out.lower()
    assert "not instrumented" in low or "predates" in low, out
    assert "not recorded" not in low, (
        "a pre-instrumentation row was reported as though the trap had failed "
        "to fire:\n" + out
    )


def test_an_instrumented_row_with_no_site_says_the_trap_did_not_fire(
    tmp_path: Path,
) -> None:
    """The other half of the distinction. A `set -u` abort or a bare `exit`
    never reaches the ERR trap, so an instrumented run can still record no
    site — and that is a real, reportable gap rather than a missing feature."""
    rc, out = aac.run([str(_ledger(tmp_path, [_undeclared(1, None)]))])
    assert rc == aac.EXIT_VIOLATION
    assert "not recorded" in out.lower(), out


def test_json_output_tallies_the_abort_sites(tmp_path: Path) -> None:
    """Machine-readable clustering is what turns the remaining rows into a
    work list: two runs dying at the same line is one bug, not two."""
    rows = [
        _undeclared(1, "auto-pr:2114"),
        _undeclared(2, "auto-pr:2114"),
        _undeclared(3, "auto-pr:900"),
        _undeclared(4, None),
        _undeclared(5, None, instrumented=False),
    ]
    _, out = aac.run([str(_ledger(tmp_path, rows)), "--json"])
    payload = json.loads(out)
    assert payload["abort_sites"] == {"auto-pr:2114": 2, "auto-pr:900": 1}
    assert payload["undeclared_unattributed"] == 1
    assert payload["undeclared_uninstrumented"] == 1


# --- externally terminated is a fourth bucket, not a violation (WI-katap) ---
#
# Measured 2026-09-20: 22 of 84 post-instrumentation runs carried
# `final_state=unknown`, and 14 of the 15 PRs behind them had MERGED. The
# violation signal was firing on runs that succeeded, because a kill from the
# caller's `timeout` wrapper was indistinguishable from auto-pr falling off the
# end. auto-pr now names the signal; this is the reader half.


def _sig_row(state: str, exit_code: int, pr: int, terminated_by: str | None) -> str:
    row = json.loads(_row(state, exit_code, pr=pr, sha=None))
    row["abort_site"] = None
    row["terminated_by"] = terminated_by
    return json.dumps(row)


def test_a_terminated_run_is_not_counted_as_a_violation(tmp_path: Path) -> None:
    """The load-bearing behaviour: a kill is not a convergence failure.

    The run never got to decide, so it neither converged nor failed to.
    """
    mod = _load()
    rows = [
        _row("merged", 0, pr=1),
        _sig_row("terminated_sigterm", 143, 2, "SIGTERM"),
    ]
    code, out = mod.run([str(_ledger(tmp_path, rows))])
    assert code == mod.EXIT_OK, out
    assert "externally terminated" in out.lower(), (
        "a terminated run must still be DISCLOSED — it is not a violation, "
        "but it is not evidence of convergence either."
    )
    assert "1 invocation(s) that ran to a decision" in out, (
        "the converged verdict must name its own denominator, so an all-clear "
        f"cannot be read as covering runs that never got to decide. Got:\n{out}"
    )


def test_terminated_runs_are_reported_with_their_signal(tmp_path: Path) -> None:
    """SIGINT and SIGTERM are different facts and are tallied apart."""
    mod = _load()
    rows = [
        # A deciding run, so this exercises the TALLY rather than the
        # all-kills verdict (which its own test covers).
        _row("merged", 0, pr=9),
        _sig_row("terminated_sigterm", 143, 1, "SIGTERM"),
        _sig_row("terminated_sigterm", 143, 2, "SIGTERM"),
        _sig_row("terminated_sigint", 130, 3, "SIGINT"),
    ]
    code, out = mod.run([str(_ledger(tmp_path, rows))])
    assert code == mod.EXIT_OK, out
    assert "SIGTERMx2" in out and "SIGINTx1" in out, (
        f"signals must be tallied apart, not collapsed. Got:\n{out}"
    )
    assert "3 run(s)" in out


def test_an_undeclared_run_is_still_a_violation(tmp_path: Path) -> None:
    """Non-vacuity control: the change must not disarm the class it refines.

    Without this, a patch that simply stopped counting `unknown` would pass
    every test above while destroying the instrument.
    """
    mod = _load()
    rows = [_sig_row("unknown", 1, 1, None)]
    code, out = mod.run([str(_ledger(tmp_path, rows))])
    assert code == mod.EXIT_VIOLATION
    assert "Class 2" in out


def test_a_merged_run_that_was_also_signalled_stays_merged_and_is_disclosed(
    tmp_path: Path,
) -> None:
    """`terminated_by` is independent of `final_state`, so the reader is too.

    A run that merged and was then killed during housekeeping is NOT a
    violation — Class 1 is about a merge reported as a failure, and the exit
    code here is an honest signal death. But the kill is disclosed, because it
    is the fact Class 1's own investigation had to recover from a terminal log.
    """
    mod = _load()
    rows = [_sig_row("merged", 143, 1, "SIGTERM")]
    code, out = mod.run([str(_ledger(tmp_path, rows))])
    assert "SIGTERM" in out
    assert code == mod.EXIT_OK, out


def test_pre_instrumentation_rows_are_not_claimed_as_unsignalled(
    tmp_path: Path,
) -> None:
    """ABSENT is not EMPTY, one level further in — the audit's own house rule.

    A row written before the signal traps existed carries no `terminated_by`
    KEY. It must not be reported as "this run was not signalled", because the
    22 rows that prompted this work are exactly such rows and most of them WERE
    signalled. The audit has to say it cannot tell.
    """
    mod = _load()
    rows = [_row("unknown", 1, pr=1, sha=None)]  # no terminated_by key at all
    code, out = mod.run([str(_ledger(tmp_path, rows))])
    assert code == mod.EXIT_VIOLATION
    assert "cannot distinguish" in out.lower() or "predate" in out.lower(), (
        "the audit must disclose that pre-instrumentation undeclared rows "
        f"cannot be attributed to a kill or a fall-off-the-end. Got:\n{out}"
    )


def test_the_json_envelope_carries_the_fourth_bucket(tmp_path: Path) -> None:
    """A machine reader must see the split too, not just the text rendering."""
    mod = _load()
    rows = [
        _sig_row("terminated_sigterm", 143, 1, "SIGTERM"),
        _row("merged", 0, pr=2),
    ]
    code, out = mod.run([str(_ledger(tmp_path, rows)), "--json"])
    payload = json.loads(out)
    assert payload["externally_terminated"] == 1
    assert payload["verdict"] == "converged"
    assert payload["terminated_by"] == {"SIGTERM": 1}


def test_a_ledger_of_nothing_but_kills_cannot_conclude(tmp_path: Path) -> None:
    """If every run was killed, there is no evidence either way.

    The same rule the script already applies to an empty ledger, extended to a
    ledger that is empty OF DECISIONS. Reporting CONVERGED here would be the
    exact reassurance-manufacturing this audit exists to refuse.
    """
    mod = _load()
    rows = [
        _sig_row("terminated_sigterm", 143, 1, "SIGTERM"),
        _sig_row("terminated_sigint", 130, 2, "SIGINT"),
    ]
    code, out = mod.run([str(_ledger(tmp_path, rows))])
    assert code == mod.EXIT_CANNOT_CONCLUDE, out
    assert "CANNOT CONCLUDE" in out
