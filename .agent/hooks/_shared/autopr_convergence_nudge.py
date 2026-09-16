#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compute the auto-pr convergence nudge for the stop hook (WI-lapap).

``scripts/audit-autopr-convergence`` reads INV-rahib's convergence ledger. This
module is the thing that makes anyone run it. Without an invocation point the
audit is just a second instrument nobody consults, which is the defect WI-lapap
names — the ledger itself already went six weeks unread while holding 55
violations.

Invoked as a subprocess by ``stop_logic.sh``; prints the nudge markdown, or
nothing, to stdout. Same contract as ``awaits_bakeoff_nudge.py``: any
unexpected failure is swallowed and no nudge is printed, so this can never
block the stop hook.

WHY A WINDOW AND NOT ALL HISTORY
--------------------------------
The ledger is append-only and never forgets, so a nudge over all history would
fire on a backlog that cannot be retired and would be tuned out within a week.
A windowed nudge is self-clearing: a violation ages out as clean runs
accumulate, so when it *does* fire it means "auto-pr misbehaved recently",
which is a claim worth interrupting for. The deliberate audit (the script with
no ``--window``) still sees everything.

WHY "CANNOT CONCLUDE" IS SILENT HERE
------------------------------------
The audit's three-valued verdict matters at the command line, where a human or
agent asked a question and deserves an honest "I could not read the evidence".
It does not belong in a stop-hook nudge: a fresh clone has no ledger, and
nagging every session about a file auto-pr has not written yet would train the
reader to skip the section — the exact failure this exists to prevent. Silence
here means "nothing to report", and the script is where you go to ask.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dep in CI
    yaml = None

DEFAULT_WINDOW = 25


def load_window(config_path: Path) -> int:
    """Read the nudge window from the tracker config.

    Missing file, missing yaml, malformed document or absent key all mean "use
    the default" rather than "fail" — a calibration knob must not be able to
    break the hook that reads it.
    """
    if yaml is None or not config_path.exists():  # pragma: no cover - import guard
        return DEFAULT_WINDOW
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        value = data["stop_hook"]["autopr_convergence_nudge"]["window"]
        return int(value)
    except Exception:
        return DEFAULT_WINDOW


def audit(repo_root: Path, window: int, audit_script: Path | None = None) -> dict | None:
    """Run the audit and return its JSON payload, or ``None`` if unavailable."""
    script = audit_script or (repo_root / "scripts" / "audit-autopr-convergence")
    if not script.exists():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--window", str(window), "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(repo_root),
        )
        return json.loads(proc.stdout)
    except Exception:
        return None


def compute_line(payload: dict | None, window: int) -> str:
    """One-sentence form, for the session-start hook.

    Session-start is the invocation point that matters, because the STOP hook
    does not reach this module at all when autonomous mode is OFF: the vendor
    adapter (``.agent/hooks/claude-code/stop.sh``) approves and exits before it
    sources ``stop_logic.sh``. OFF is a normal working mode for this repo, so a
    stop-hook-only wire-up would be an instrument that does not run in the
    configuration it was written for — which is the WI-lapap defect one level
    up. Session-start fires in every mode.
    """
    if not payload or payload.get("verdict") != "violation":
        return ""
    parts = []
    if payload.get("merged_nonzero_exit"):
        parts.append(f"{payload['merged_nonzero_exit']} merged then exited nonzero")
    if payload.get("undeclared_state"):
        parts.append(
            f"{payload['undeclared_state']} ended with no declared terminal state"
        )
    return (
        f"auto-pr did not converge in the last {window} invocation(s): "
        f"{'; '.join(parts)}. Run `./scripts/audit-autopr-convergence` for the "
        f"table and the offending rows, including the abort site of any "
        f"undeclared run (INV-rahib)."
    )


def compute_nudge(payload: dict | None, window: int) -> str:
    """Return the nudge markdown, or ``''`` when there is nothing to say."""
    if not payload or payload.get("verdict") != "violation":
        return ""

    merged_nonzero = payload.get("merged_nonzero_exit", 0)
    undeclared = payload.get("undeclared_state", 0)
    undeclared_zero = payload.get("undeclared_state_exit_zero", 0)

    lines = [
        "\n\n---\n",
        "## AUTO-PR DID NOT CONVERGE\n",
        f"In the last {window} `auto-pr` invocation(s) "
        f"(`.git/AUTOPR_HISTORY.jsonl`):\n",
    ]
    if merged_nonzero:
        lines.append(
            f"- **{merged_nonzero} merged, then exited nonzero** — the work "
            f"landed and the run reported failure.\n"
        )
    if undeclared:
        detail = (
            f", {undeclared_zero} of them exiting 0 (success claimed with no "
            f"terminal state recorded)"
            if undeclared_zero
            else ""
        )
        lines.append(
            f"- **{undeclared} ended with no declared terminal state**{detail} "
            f"— `unknown` is the initializer, so the run fell off the end.\n"
        )
    lines.append(
        "\nRun `./scripts/audit-autopr-convergence` for the full table and the "
        "offending rows. An undeclared run names the abort site it died on, or "
        "reports that the site was not recorded — which is itself the signal to "
        "look by hand, since a `set -u` abort and a bare `exit` never reach the "
        "ERR trap. This is INV-rahib.\n"
    )
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute the auto-pr convergence nudge for the stop hook."
    )
    parser.add_argument("repo_root", help="Absolute path to the repo root")
    parser.add_argument("--window", type=int, default=None, help="Override the window.")
    parser.add_argument("--audit-script", default=None, help="Override (testing).")
    parser.add_argument(
        "--line", action="store_true",
        help="One-sentence form for the session-start hook (default: markdown).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    window = (
        args.window
        if args.window is not None
        else load_window(repo_root / ".agent" / "tracker" / "config.yaml")
    )
    script = Path(args.audit_script) if args.audit_script else None
    payload = audit(repo_root, window, script)
    render = compute_line if args.line else compute_nudge
    sys.stdout.write(render(payload, window))
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
