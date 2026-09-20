# SPDX-License-Identifier: AGPL-3.0-or-later
"""A run that is KILLED must say so, instead of reporting the violation signal.

WHY THIS EXISTS (WI-katap)
--------------------------
INV-rahib's residual class is the run whose ``final_state`` still holds its
``unknown`` initializer. Owner ruling 2026-08-03 makes that *the* violation
signal: it is never written by a deliberate exit, so reaching it means the run
fell off the end.

Measured on the ledger 2026-09-20, that class was 22 of 84 post-instrumentation
runs (26%), up from 14 of 485 (2.9%) before. None of the 22 carried an
``abort_site`` — WI-nazoj's ERR trap had never once attributed an abort. Its
own docstring names the three causes it cannot see, and a surviving run log
(PR #1091) identified which one::

    CONVERGENCE VIOLATION (INV-rahib / WI-nazoj)
       auto-pr is exiting 1 with no declared terminal state.
       Abort site: not recorded. ...
    Terminated

``Terminated`` is the shell reporting SIGTERM. The source is the caller: the
agent wraps auto-pr in ``timeout 590`` because its harness caps one foreground
call at 600s, and auto-pr's own poll budget (2400s default, plus a soft-retry
of 300s that is not optional) cannot fit inside that. 14 of the 15 PRs behind
those rows are merged — the violation signal was firing on runs that succeeded.

THE DEFECT IS A CONFLATION, NOT A CRASH
---------------------------------------
``final_state=unknown`` was carrying two facts:

* the run reached its end without deciding — the convergence bug the invariant
  is about;
* the run was **killed before it could decide** — not a convergence property at
  all, because the run never got to converge.

That is this repository's recurring absent-versus-empty defect, one level up
from where the audit script already guards it: ``json_field`` rendered a JSON
null and a missing key identically (fixed by ``json_bool_state``), and
``silence_reason`` separates *not applicable* from *cannot determine*. Here the
criterion "zero rows carrying ``final_state`` unknown over a window" was
UNSATISFIABLE while a legitimate external kill reported as its own violation.

Underneath it was a plain structural gap: auto-pr trapped ``EXIT`` and ``ERR``
and **no signals at all**, so a killed run had no code path that could name
what happened to it.

TWO FACTS, TWO FIELDS
---------------------
``terminated_by`` is recorded *independently* of ``final_state``, and is not
redundant with it. A run that had already declared ``merged`` and is then
SIGTERMed while cleaning up keeps ``merged`` — the run decided, and the kill
only interrupted its housekeeping — but still records ``terminated_by``. That
second fact is precisely what INV-rahib's Class 1 investigation (merged, then
exit nonzero) had to reconstruct from a surviving terminal log, because nothing
recorded it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from test_autopr_nazoj_undeclared_state import (
    _extract_func,
    _extract_state_initializers,
    production_shell_flags,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


# ---------------------------------------------------------------------------
# Harness — the real trap machinery, a synthetic body
# ---------------------------------------------------------------------------


def _write_action(tmp_path: Path, body: str) -> tuple[Path, Path]:
    """Build a script carrying auto-pr's REAL trap installation and finalize.

    Real: the production ``set`` line, the real state initializers, the real
    ``_autopr_record_abort`` / ``_autopr_on_signal`` / ``_autopr_write_sentinel``
    / ``_autopr_finalize``, and the real ``_autopr_install_traps``. Synthetic:
    only the body, so a test can choose when to be killed.

    Installing the traps via the production helper rather than by hand is the
    point of that helper existing: a test that re-typed the three ``trap``
    lines could not observe a signal trap going missing from an action path.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    script = tmp_path / "act.sh"
    script.write_text(
        f"{production_shell_flags()}\n"
        f'REPO_ROOT="{repo}"\n'
        f'AUTOPR_RESULT_FILE="{repo}/.git/AUTOPR_LAST_RESULT.json"\n'
        f'AUTOPR_HISTORY_FILE="{repo}/.git/AUTOPR_HISTORY.jsonl"\n'
        f"{_extract_state_initializers()}\n"
        f"{_extract_func('_autopr_record_abort')}\n"
        f"{_extract_func('_autopr_on_signal')}\n"
        f"{_extract_func('_autopr_write_sentinel')}\n"
        f"{_extract_func('_autopr_finalize')}\n"
        f"{_extract_func('_autopr_install_traps')}\n"
        "action() {\n"
        "\t_autopr_install_traps\n"
        f"{body}\n"
        "}\n"
        "echo READY\n"
        "action\n",
        encoding="utf-8",
    )
    return script, repo


def _run_and_signal(
    tmp_path: Path, body: str, sig: int = signal.SIGTERM
) -> tuple[int, dict | None]:
    """Start the action, wait until it is really running, then signal it."""
    script, repo = _write_action(tmp_path, body)
    proc = subprocess.Popen(
        ["bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(repo),
        env={**os.environ, "LC_ALL": "C"},
    )
    # Wait for the script to reach the action rather than sleeping a guess:
    # signalling before the traps are installed would test nothing and would
    # do it intermittently, which is worse than failing.
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "READY"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (repo / ".git" / "armed").exists():
            break
        time.sleep(0.01)
    else:  # pragma: no cover - only on a pathologically slow machine
        proc.kill()
        pytest.fail("action never armed")
    proc.send_signal(sig)
    proc.wait(timeout=30)
    sentinel = repo / ".git" / "AUTOPR_LAST_RESULT.json"
    payload = json.loads(sentinel.read_text()) if sentinel.exists() else None
    return proc.returncode, payload


# The body arms a marker the harness waits on, then blocks the way production
# blocks: a LOOP OF SHORT SLEEPS, which is what the CI poll actually does.
#
# This is not a cosmetic choice. Bash does not run a trap while it is waiting
# on a foreground child — it defers until that child exits. A fixture that
# slept once for 60s would therefore sit on the signal for 60s and read as
# "the trap never fired", when production (polling on `sleep 10`) sees the
# trap within one tick. The first draft of this file made exactly that mistake
# and hung the SIGINT case.
_ARM_AND_BLOCK = (
    '\ttouch "$REPO_ROOT/.git/armed"\n'
    "\tfor _ in $(seq 600); do sleep 0.1; done\n"
)


# ---------------------------------------------------------------------------
# The load-bearing behavior
# ---------------------------------------------------------------------------


def test_a_sigtermed_run_declares_that_it_was_terminated(tmp_path: Path) -> None:
    """The defect, inverted: a killed run must not report the violation signal."""
    code, payload = _run_and_signal(tmp_path, _ARM_AND_BLOCK)
    assert payload is not None
    assert payload["final_state"] == "terminated_sigterm", (
        "a run killed mid-poll recorded the `unknown` initializer, which owner "
        "ruling 2026-08-03 makes INV-rahib's violation signal — so an external "
        "kill was indistinguishable from auto-pr falling off the end."
    )
    assert payload["terminated_by"] == "SIGTERM"
    assert code == 128 + signal.SIGTERM, (
        "the exit code must stay a conventional signal death so `timeout`, a "
        "supervisor and the shell all still see what happened."
    )


def test_each_signal_is_named_rather_than_collapsed(tmp_path: Path) -> None:
    """SIGINT is a human at a keyboard; SIGTERM is usually a supervisor.

    Collapsing them into one `terminated` state would re-commit the defect this
    file exists to fix, one level smaller: only the reader can act on the
    difference between "I pressed Ctrl-C" and "my timeout wrapper fired".
    """
    code, payload = _run_and_signal(tmp_path, _ARM_AND_BLOCK, sig=signal.SIGINT)
    assert payload is not None
    assert payload["final_state"] == "terminated_sigint"
    assert payload["terminated_by"] == "SIGINT"
    assert code == 128 + signal.SIGINT


def test_a_hangup_is_named_too(tmp_path: Path) -> None:
    """SIGHUP is what a detached run gets when its terminal goes away."""
    code, payload = _run_and_signal(tmp_path, _ARM_AND_BLOCK, sig=signal.SIGHUP)
    assert payload is not None
    assert payload["final_state"] == "terminated_sighup"
    assert payload["terminated_by"] == "SIGHUP"
    assert code == 128 + signal.SIGHUP


def test_a_signal_never_overwrites_a_state_the_run_already_declared(
    tmp_path: Path,
) -> None:
    """The guard, and the reason `terminated_by` is a SEPARATE field.

    INV-rahib's Class 1 was runs that merged and then exited nonzero because a
    bare command aborted during post-merge housekeeping. A kill arriving in the
    same window must not now rewrite the outcome to `terminated_sigterm`: the
    merge landed, the run decided, and only the cleanup was interrupted. But
    the kill is still a fact worth keeping, so it lands in its own field.
    """
    body = '\t_autopr_state_final="merged"\n' + _ARM_AND_BLOCK
    code, payload = _run_and_signal(tmp_path, body)
    assert payload is not None
    assert payload["final_state"] == "merged", (
        "a signal during post-merge cleanup rewrote a landed merge into a "
        "termination — the Class 1 defect with the sign flipped."
    )
    assert payload["terminated_by"] == "SIGTERM", (
        "the kill must still be recorded; it is the fact Class 1's "
        "investigation had to reconstruct from a terminal log."
    )
    assert code == 128 + signal.SIGTERM


def test_an_unsignalled_run_records_no_terminator(tmp_path: Path) -> None:
    """Non-vacuity control (L17).

    Without this, every assertion above would still pass if `terminated_by`
    were hardcoded to a signal name, and `final_state` were hardcoded to
    `terminated_sigterm`. It also pins the third value of the field: null means
    "this run was not signalled", which a reader must not confuse with the
    absent key on a row written before this instrumentation existed.
    """
    script, repo = _write_action(tmp_path, '\t_autopr_state_final="merged"\n')
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60,
        cwd=str(repo), env={**os.environ, "LC_ALL": "C"},
    )
    payload = json.loads((repo / ".git" / "AUTOPR_LAST_RESULT.json").read_text())
    assert proc.returncode == 0
    assert payload["final_state"] == "merged"
    assert payload["terminated_by"] is None


def test_the_ledger_row_carries_the_termination_too(tmp_path: Path) -> None:
    """The sentinel is one run; the ledger is the population INV-rahib asks about.

    A fact recorded only in the overwrite-in-place sentinel cannot be measured
    over a window, which is the whole reason the ledger exists (WI-lapap).
    """
    script, repo = _write_action(tmp_path, _ARM_AND_BLOCK)
    proc = subprocess.Popen(
        ["bash", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=str(repo), env={**os.environ, "LC_ALL": "C"},
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "READY"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (repo / ".git" / "armed").exists():
            break
        time.sleep(0.01)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30)
    rows = [
        json.loads(line)
        for line in (repo / ".git" / "AUTOPR_HISTORY.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["final_state"] == "terminated_sigterm"
    assert rows[0]["terminated_by"] == "SIGTERM"


# ---------------------------------------------------------------------------
# Structural — the traps must reach every action path
# ---------------------------------------------------------------------------


def test_every_action_path_installs_traps_through_the_one_helper() -> None:
    """A boundary, not N guards — the lesson the post-merge fix already paid for.

    `_autopr_post_merge` was introduced as a boundary rather than three guards
    precisely because a structural test found a FOURTH call site that reading
    the code by hand had missed. The same argument applies here: an action
    subcommand that installs `_autopr_finalize` by hand would silently have no
    signal traps, and the only symptom would be a row in a ledger nobody reads.
    """
    text = AUTO_PR.read_text(encoding="utf-8")
    # The helper's OWN trap lines are the legitimate ones, so exclude its body
    # rather than the string that names it — an earlier draft filtered on the
    # helper's name appearing in the line, which no trap line inside it
    # contains, so the helper reported itself as a violation.
    helper = _extract_func("_autopr_install_traps")
    outside = text.replace(helper, "")
    bare = [
        line.strip()
        for line in outside.splitlines()
        if line.strip().startswith("trap ")
        and ("_autopr_finalize" in line or "_autopr_record_abort" in line)
    ]
    assert not bare, (
        f"action path(s) install auto-pr's traps by hand: {bare}. Route them "
        f"through _autopr_install_traps so a signal trap cannot go missing "
        f"from one path while the others have it."
    )
    assert text.count("_autopr_install_traps() {") == 1
    # Non-vacuity: the helper must actually be used, or the assertion above is
    # satisfied by a script that installs no traps at all.
    assert text.count("\t_autopr_install_traps") >= 3, (
        "expected every action subcommand to call the helper"
    )


def test_the_signal_traps_are_installed_for_all_three_signals() -> None:
    """Read from production: the helper must cover TERM, INT and HUP."""
    helper = _extract_func("_autopr_install_traps")
    for sig in ("TERM", "INT", "HUP"):
        assert f"' {sig}" in helper or f" {sig}\n" in helper or f" {sig} " in helper, (
            f"_autopr_install_traps does not trap SIG{sig}: {helper}"
        )
