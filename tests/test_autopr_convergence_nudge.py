# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the auto-pr convergence stop-hook nudge (WI-lapap).

The audit script answers the question; this module is what makes anyone ask it.
So the properties worth pinning are about *when it speaks*:

* it speaks on a violation, naming which class, because a nudge that says only
  "something is wrong" sends the reader somewhere else to find out what;
* it is silent on a converged window, or the section becomes wallpaper;
* it is silent when the ledger cannot be read, because a fresh clone has no
  ledger and nagging about a file auto-pr has not written yet trains the reader
  to skip the section — which is precisely how the ledger went unread;
* it never raises, because the stop hook must not be blocked by its own
  advisory.

The last two look similar and are not: silence-on-cannot-conclude is a
deliberate difference from the SCRIPT, which reports that state loudly. The
command line is where you asked a question and deserve an honest "I could not
read the evidence"; the stop hook is where an unanswerable question is noise.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NUDGE_PATH = REPO_ROOT / ".agent" / "hooks" / "_shared" / "autopr_convergence_nudge.py"
AUDIT_PATH = REPO_ROOT / "scripts" / "audit-autopr-convergence"
STOP_LOGIC = REPO_ROOT / ".agent" / "hooks" / "_shared" / "stop_logic.sh"


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "autopr_convergence_nudge", str(NUDGE_PATH)
    )
    spec = importlib.util.spec_from_loader("autopr_convergence_nudge", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


nudge = _load()


def _payload(**kw) -> dict:
    base = {
        "verdict": "converged",
        "total": 10,
        "merged_nonzero_exit": 0,
        "undeclared_state": 0,
        "undeclared_state_exit_zero": 0,
    }
    base.update(kw)
    return base


# --- when it speaks ---------------------------------------------------------


def test_speaks_on_merged_with_nonzero_exit() -> None:
    out = nudge.compute_nudge(
        _payload(verdict="violation", merged_nonzero_exit=3), 25
    )
    assert "AUTO-PR DID NOT CONVERGE" in out
    assert "3 merged, then exited nonzero" in out
    assert "audit-autopr-convergence" in out


def test_speaks_on_undeclared_state_and_names_the_exit_zero_subset() -> None:
    """The exit-0 subset is the worst shape, so it must not be folded into a
    single count that reads as fourteen identical failures."""
    out = nudge.compute_nudge(
        _payload(
            verdict="violation", undeclared_state=4, undeclared_state_exit_zero=1
        ),
        25,
    )
    assert "4 ended with no declared terminal state" in out
    assert "1 of them exiting 0" in out


def test_omits_the_exit_zero_clause_when_there_are_none() -> None:
    out = nudge.compute_nudge(_payload(verdict="violation", undeclared_state=2), 25)
    assert "2 ended with no declared terminal state" in out
    assert "exiting 0" not in out


def test_names_the_window_it_judged() -> None:
    """A count without its denominator is unreadable: '3 bad runs' out of how
    many, over what period?"""
    out = nudge.compute_nudge(_payload(verdict="violation", merged_nonzero_exit=3), 7)
    assert "last 7" in out


# --- when it stays quiet ----------------------------------------------------


def test_silent_on_a_converged_window() -> None:
    assert nudge.compute_nudge(_payload(verdict="converged"), 25) == ""


def test_silent_when_the_ledger_cannot_be_read() -> None:
    """A fresh clone has no ledger. Nagging about that every session is how a
    section stops being read."""
    assert nudge.compute_nudge(_payload(verdict="cannot_conclude", total=0), 25) == ""


def test_silent_on_a_missing_payload() -> None:
    assert nudge.compute_nudge(None, 25) == ""


# --- it cannot break the hook ----------------------------------------------


def test_audit_returns_none_when_the_script_is_absent(tmp_path: Path) -> None:
    assert nudge.audit(tmp_path, 25, audit_script=tmp_path / "nope") is None


def test_audit_returns_none_when_the_script_fails(tmp_path: Path) -> None:
    """A crashing audit must degrade to silence, not to a traceback in the
    stop hook."""
    broken = tmp_path / "broken"
    broken.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    assert nudge.audit(tmp_path, 25, audit_script=broken) is None


def test_window_config_falls_back_to_the_default(tmp_path: Path) -> None:
    assert nudge.load_window(tmp_path / "absent.yaml") == nudge.DEFAULT_WINDOW
    malformed = tmp_path / "bad.yaml"
    malformed.write_text("stop_hook: [this is not a mapping]\n", encoding="utf-8")
    assert nudge.load_window(malformed) == nudge.DEFAULT_WINDOW


def test_window_config_is_read_when_present(tmp_path: Path) -> None:
    """The knob is read where PyYAML exists, and falls back where it does not.

    Both arms are the documented contract, not an escape hatch: the module
    states that a missing PyYAML means "use the default", and the CI job that
    exercises this file (`forge-arms`) installs pytest and nothing else on
    purpose — it tests the forge scripts in a minimal environment. Asserting
    only the reads-the-knob arm made this test a claim about the test runner's
    dependency set rather than about the function. The reads-the-knob arm is
    still exercised for real in the main pytest job, which has PyYAML.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "stop_hook:\n  autopr_convergence_nudge:\n    window: 9\n", encoding="utf-8"
    )
    expected = nudge.DEFAULT_WINDOW if nudge.yaml is None else 9
    assert nudge.load_window(cfg) == expected


# --- end to end, against the real audit script ------------------------------


def test_end_to_end_against_the_real_audit(tmp_path: Path) -> None:
    """Drive the real audit script through the real nudge.

    The unit tests above feed `compute_nudge` a hand-built payload, which would
    keep passing if the audit's JSON key names drifted. This is the arm that
    fails when the two halves stop agreeing.
    """
    ledger = tmp_path / "led.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps(
                {
                    "exit_code": 1, "final_state": "merged", "merged_sha": "a",
                    "pr_number": i, "pr_url": "u", "timestamp": "2026-09-01T00:00:00Z",
                }
            )
            for i in range(3)
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(AUDIT_PATH), str(ledger), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    payload = json.loads(proc.stdout)
    out = nudge.compute_nudge(payload, 25)
    assert "3 merged, then exited nonzero" in out, (
        f"the nudge could not read the audit's own payload — the two halves "
        f"have drifted. Payload was: {payload}"
    )


# --- the wire-up ------------------------------------------------------------


def test_stop_logic_invokes_the_nudge() -> None:
    """Structural: the module must actually be called by the stop hook.

    Without this the nudge is a third instrument nobody runs, stacked on the
    audit nobody ran, on the ledger nobody read.
    """
    text = STOP_LOGIC.read_text(encoding="utf-8")
    assert "autopr_convergence_nudge.py" in text, (
        "stop_logic.sh does not invoke the convergence nudge; the audit has no "
        "reader and WI-lapap is not closed by shipping it."
    )


# --- the session-start wire-up, and why stop-only is not enough -------------

SESSION_START_LOGIC = REPO_ROOT / ".agent" / "hooks" / "_shared" / "session_start_logic.sh"
VENDOR_STOP = REPO_ROOT / ".agent" / "hooks" / "claude-code" / "stop.sh"


def test_stop_hook_does_not_run_in_off_mode() -> None:
    """The premise behind the session-start wire-up, pinned as a fact.

    `stop.sh` approves and exits when AUTONOMOUS_MODE is OFF/FALSE, BEFORE it
    sources stop_logic.sh — so the stop-hook nudge cannot fire in OFF mode. OFF
    is a normal working mode for this repo. If this assumption ever stops
    holding, this test fails and whoever is reading can simplify the
    session-start path deliberately rather than discovering the redundancy by
    accident.
    """
    text = VENDOR_STOP.read_text(encoding="utf-8")
    off_branch = text.index('"$MODE" == "OFF"')
    source_line = text.index("_shared/stop_logic.sh")
    assert off_branch < source_line, (
        "stop.sh now sources stop_logic.sh before the OFF short-circuit; the "
        "stop nudge reaches OFF-mode sessions and the session-start wire-up "
        "may be redundant."
    )


def test_session_start_invokes_the_nudge_in_every_mode_branch() -> None:
    """Every branch that reaches a prompt must carry the convergence check.

    The cadence helper above it is called from each branch for the same
    reason — a nudge attached to only one mode-state is a nudge that silently
    does not apply to the mode you happen to be in.
    """
    text = SESSION_START_LOGIC.read_text(encoding="utf-8")
    cadence_calls = text.count("\n    _append_concept_audit_cadence") + text.count(
        "\n        _append_concept_audit_cadence"
    ) + text.count("\n_append_concept_audit_cadence")
    conv_calls = text.count("\n    _append_autopr_convergence") + text.count(
        "\n        _append_autopr_convergence"
    ) + text.count("\n_append_autopr_convergence")
    assert conv_calls >= 4, f"only {conv_calls} call site(s) found"
    assert conv_calls == cadence_calls, (
        f"the convergence nudge is wired into {conv_calls} branch(es) but the "
        f"cadence nudge into {cadence_calls}; one mode-state is missing a check"
    )


def test_line_form_is_one_sentence_and_names_the_command() -> None:
    """Session-start messages are read as prose, not markdown sections."""
    out = nudge.compute_line(
        _payload(verdict="violation", merged_nonzero_exit=2, undeclared_state=1), 25
    )
    assert "\n" not in out
    assert "audit-autopr-convergence" in out
    assert "2 merged then exited nonzero" in out
    assert "1 ended with no declared terminal state" in out


def test_line_form_is_silent_when_there_is_nothing_to_say() -> None:
    assert nudge.compute_line(_payload(verdict="converged"), 25) == ""
    assert nudge.compute_line(None, 25) == ""
    assert nudge.compute_line(_payload(verdict="cannot_conclude"), 25) == ""
