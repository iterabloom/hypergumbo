# SPDX-License-Identifier: AGPL-3.0-or-later
"""With no terminal, auto-pr refuses to start until the caller declares a mode.

WHY (WI-hajak follow-up)
------------------------
WI-katap made an externally-killed run visible; WI-hajak gave it `--detach` so
it need not be killed at all. Neither stops the caller FORGETTING. That is not
hypothetical: the session that discovered `nohup` reverted to `timeout 590`
within the same session, and 22 of 84 recorded runs died that way.

The obvious fix — make `--detach` the default when stdout is not a tty — was
considered and REJECTED on evidence. `scripts/prepare-release` does:

    if ./scripts/auto-pr; then
        echo "✓ Release commit merged to dev via auto-pr"

That reads exit 0 as *merged*, and AGENTS.md has an agent run `prepare-release`,
so it is non-tty. Auto-detaching would make it return 0 immediately having
merged nothing and then announce a merge that did not happen. Exit 0 would come
to carry two facts — "merged" and "handed off, outcome unknown" — which is the
exact conflation WI-katap and WI-hajak just spent two changes removing, re-made
one level up at the exit code.

So the mode is not guessed. It is DECLARED, and an undeclared non-tty run is
refused before it does anything. This is the same discipline `--tracker-id`
already applies when it hard-errors rather than silently treating the next
positional as an ID: refuse rather than guess.

WHY AT THE TOP OF do_pr, not at the poll. The harm only materialises at the CI
poll, so gating there would be more narrowly targeted — and would refuse only
AFTER pushing a branch and creating a PR. A refusal that leaves side effects
behind is a worse trade than one that costs a re-run, so the gate sits before
any work happens.
"""

from __future__ import annotations

import json
import os
import pty
import subprocess
from pathlib import Path

from test_autopr_result_sentinel import _init_fake_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


def _run(fake: Path, *args: str, tty: bool = False):
    env = dict(os.environ)
    for k in ("FORGEJO_USER", "FORGEJO_TOKEN", "AUTO_PR_SIMULATE_OUTAGE"):
        env.pop(k, None)
    env["FORGEJO_USER"] = "u"
    env["FORGEJO_TOKEN"] = "t"
    env["AUTOPR_RESULT_FILE"] = str(fake / ".git" / "AUTOPR_LAST_RESULT.json")
    env["AUTOPR_HISTORY_FILE"] = str(fake / ".git" / "AUTOPR_HISTORY.jsonl")
    if not tty:
        return subprocess.run(
            ["bash", str(AUTO_PR), *args], cwd=str(fake), env=env,
            capture_output=True, text=True, timeout=120,
        )
    # A real pty, because `[[ -t 1 ]]` cannot be faked from the outside and a
    # control that lied about it would let an always-refuse implementation pass.
    primary, secondary = pty.openpty()
    try:
        proc = subprocess.run(
            ["bash", str(AUTO_PR), *args], cwd=str(fake), env=env,
            stdout=secondary, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            text=True, timeout=120,
        )
    finally:
        os.close(secondary)
        os.close(primary)
    return proc


def _sentinel(fake: Path) -> dict | None:
    p = fake / ".git" / "AUTOPR_LAST_RESULT.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_an_undeclared_run_without_a_terminal_is_refused(tmp_path: Path) -> None:
    """The load-bearing behaviour."""
    fake = _init_fake_repo(tmp_path)
    proc = _run(fake)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    assert "--detach" in out and "--foreground" in out, (
        f"the refusal must name BOTH ways out, or it is a riddle. Got:\n{out}"
    )
    payload = _sentinel(fake)
    assert payload is not None
    assert payload["final_state"] == "failed_mode_undeclared"


def test_the_refusal_happens_before_any_push(tmp_path: Path) -> None:
    """A refusal that left a branch and an open PR behind would be worse than
    the problem it prevents."""
    fake = _init_fake_repo(tmp_path)
    proc = _run(fake)
    assert proc.returncode == 1
    out = proc.stdout + proc.stderr
    assert "Pushing" not in out and "Created PR" not in out, (
        f"auto-pr did work before refusing:\n{out}"
    )


def test_foreground_declared_gets_past_the_gate(tmp_path: Path) -> None:
    """Declaring foreground is honoured — the caller said it can block.

    Run from ``dev``. The gate sits at auto-pr:1955 and the protected-branch
    refusal at :2001, so a declared run gets PAST the gate and then stops
    immediately, on a check that touches no network. That ordering is the
    whole fixture: the first draft ran from a feature branch, sailed past the
    gate into a real push, and timed out after 120s in CI while passing
    locally in a second — a test that depended on the network failing FAST,
    which is not a property any environment owes it.
    """
    fake = _init_fake_repo(tmp_path, branch="dev")
    proc = _run(fake, "--foreground")
    payload = _sentinel(fake)
    assert payload is not None
    assert payload["final_state"] == "failed_protected_branch", (
        "a declared foreground run must reach the protected-branch check, "
        f"i.e. get past the gate. Got {payload['final_state']!r}"
    )


def test_a_terminal_needs_no_declaration(tmp_path: Path) -> None:
    """Non-vacuity control (L17), and the reason this is a tty check at all.

    Without this, an implementation that refused unconditionally would pass
    every other test in this file. A human at a terminal is watching and can
    Ctrl-C, so there is nothing to declare.
    """
    fake = _init_fake_repo(tmp_path, branch="dev")
    _run(fake, tty=True)
    payload = _sentinel(fake)
    assert payload is not None
    assert payload["final_state"] == "failed_protected_branch", (
        "a run WITH a terminal was refused; the gate is not reading the tty. "
        f"Got {payload['final_state']!r}"
    )


def test_detach_and_foreground_together_are_refused(tmp_path: Path) -> None:
    """They are contradictory instructions, so guessing which one wins is the
    defect this whole family of fixes is about."""
    fake = _init_fake_repo(tmp_path)
    proc = _run(fake, "--detach", "--foreground")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "mutually exclusive" in out.lower(), out


def test_the_detached_child_declares_foreground_for_itself() -> None:
    """Load-bearing: without this, --detach is broken by its own gate.

    The child's stdout is the log file, so it has no tty either. If the parent
    did not declare the child's mode, the watcher would be refused the instant
    it started and `--detach` would fail closed — with the failure visible only
    inside a log nobody reads.

    It must be PREPENDED: the flag loop breaks at the first non-flag, so a
    trailing `--foreground` after a subcommand like `flush` is never parsed.
    """
    text = AUTO_PR.read_text(encoding="utf-8")
    start = text.index("_autopr_detach_and_exit() {")
    body = text[start : text.index("\n}", start)]
    assert "--foreground" in body, (
        "the detached child is not told to run in the foreground; it will be "
        "refused by the mode gate the moment it starts"
    )
    assert '_AUTOPR_CHILD_ARGS=(--foreground' in body, (
        "--foreground must be PREPENDED to the child argv, not appended: the "
        "flag loop stops at the first non-flag, so a trailing flag after a "
        "subcommand is never parsed"
    )


def test_prepare_release_declares_its_mode() -> None:
    """The caller that made auto-detach unsafe must itself be explicit.

    `prepare-release` reads auto-pr's exit 0 as "merged" and is run by an
    agent, hence non-tty. It has to opt into blocking or it would now be
    refused — and it genuinely does want to block.
    """
    text = (REPO_ROOT / "scripts" / "prepare-release").read_text(encoding="utf-8")
    assert "./scripts/auto-pr --foreground" in text, (
        "prepare-release still invokes auto-pr without declaring a mode"
    )


def test_both_flags_are_documented() -> None:
    text = AUTO_PR.read_text(encoding="utf-8")
    assert 'echo "  --foreground' in text, "--foreground missing from --help"
