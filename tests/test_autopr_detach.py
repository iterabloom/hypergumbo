# SPDX-License-Identifier: AGPL-3.0-or-later
"""`auto-pr --detach`: hand the CI poll to a watcher that outlives the turn (WI-hajak).

WHY
---
WI-katap made an externally-killed run VISIBLE — it records `terminated_sigterm`
rather than INV-rahib's `unknown` violation signal. It did nothing about the
cause. The arithmetic is unchanged and unfixable by tuning: `auto-pr` commits to
2400s of CI polling plus a 300s soft-retry that is not optional, while an agent
harness caps one foreground shell call at 600s. 22 of 84 post-instrumentation
runs were killed by the caller's own `timeout` wrapper, and 14 of the 15 PRs
behind them merged anyway — every one finished by hand afterwards.

`nohup ./scripts/auto-pr &` already works and is now written into AGENTS.md. It
is still a workaround: the caller has to know to do it, has to invent a log
path, and gets no declared state back. The prior session rediscovered it and
reverted to `timeout 590` within the same session.

THE SHAPE
---------
The parent detaches BEFORE doing any work and re-execs itself without
`--detach`. That is what keeps `do_pr` — 600 lines with the poll, the soft
retry, Scenario B and the merge cascade threaded through it — completely
untouched: the child is an ordinary `auto-pr` run, not a resumed half of one.
The push is idempotent for exactly this reason (an existing open PR for the
branch is reused, not duplicated), which is already exercised in production.

So the parent's only job is to hand off, and `detached_watching` is a
deliberate non-merge outcome in the sense `queued_vpr` already is: the run did
what it set out to do.

ABSENT IS NOT EMPTY, AGAIN
--------------------------
A handle whose watcher is gone is NOT the same as a handle whose watcher is
still polling, and neither is the same as no handle at all. If `wait` collapsed
them it would re-commit — one level further out — the exact defect WI-katap
just closed inside the ledger. A watcher that died mid-poll (machine sleep,
session teardown, OOM) leaves a handle that looks identical to a live one
unless someone checks the pid, so `wait` checks it and says `watcher_vanished`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from test_autopr_nazoj_undeclared_state import (
    _extract_func,
    _extract_state_initializers,
    production_shell_flags,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"


def _harness(tmp_path: Path, child_body: str, driver: str) -> tuple[Path, Path]:
    """A script that is BOTH the parent and — when re-execed — the child.

    `_autopr_detach_and_exit` spawns `"$0"`, so pointing $0 at this harness is
    what lets the real production function be exercised without a forge: the
    re-exec lands back in this same file and takes the child branch.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    script = tmp_path / "act.sh"
    script.write_text(
        # The shebang is load-bearing, not boilerplate. `_autopr_detach_and_exit`
        # re-execs "$0" directly, so without one the kernel hands the child to
        # /bin/sh and it dies on `set -o pipefail` — which looks identical to
        # "the spawn never happened". Production's $0 always carries one.
        "#!/usr/bin/env bash\n"
        f"{production_shell_flags()}\n"
        # json_field lives in the forge library, and do_wait reads the handle
        # with it. Sourcing the real one (rather than stubbing) keeps the
        # harness honest about the null/missing/unparseable conflation the
        # production guard exists to refuse.
        f'AUTOPR_SOURCE_ONLY=1 source "{REPO_ROOT}/scripts/lib/forgejo-api.sh"\n'
        f'REPO_ROOT="{repo}"\n'
        f'AUTOPR_RESULT_FILE="{repo}/.git/AUTOPR_LAST_RESULT.json"\n'
        f'AUTOPR_HISTORY_FILE="{repo}/.git/AUTOPR_HISTORY.jsonl"\n'
        # The marker may not be $1: the detach helper PREPENDS --foreground to
        # the child argv (the child has no tty either and would otherwise be
        # refused by the mode gate), so match anywhere in "$*".
        "if [[ \" $* \" == *\" --child-marker \"* ]]; then\n"
        f"{child_body}\n"
        "\texit 0\n"
        "fi\n"
        f"{_extract_state_initializers()}\n"
        f"{_extract_func('_autopr_record_abort')}\n"
        f"{_extract_func('_autopr_on_signal')}\n"
        f"{_extract_func('_autopr_write_sentinel')}\n"
        f"{_extract_func('_autopr_finalize')}\n"
        f"{_extract_func('_autopr_install_traps')}\n"
        f"{_extract_func('_autopr_detach_and_exit')}\n"
        f"{_extract_func('do_wait')}\n"
        "action() {\n"
        "\t_autopr_install_traps\n"
        f"{driver}\n"
        "}\n"
        "action\n",
        encoding="utf-8",
    )
    # `_autopr_detach_and_exit` re-execs "$0" directly, as production does
    # where $0 is always an executable script. A non-executable harness would
    # fail the exec and look exactly like "the spawn was never attempted".
    script.chmod(0o755)
    return script, repo


def _run(script: Path, repo: Path, args: list[str] | None = None, timeout: int = 60):
    return subprocess.run(
        ["bash", str(script), *(args or [])],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(repo), env={**os.environ, "LC_ALL": "C"},
    )


_SPAWN = (
    '\t_AUTOPR_CHILD_ARGS=(--child-marker alpha)\n'
    '\t_autopr_detach_and_exit\n'
)


# ---------------------------------------------------------------------------
# The hand-off
# ---------------------------------------------------------------------------


def test_the_parent_returns_immediately_and_the_child_outlives_it(
    tmp_path: Path,
) -> None:
    """The whole point: the caller gets its shell back inside its budget.

    The child sleeps first, so a marker that appears at all proves it kept
    running after the parent had already exited — which is what `setsid`
    buys and what a plain background job would not survive.
    """
    child = '\tsleep 2\n\techo "$*" > "$REPO_ROOT/.git/child-ran"\n'
    script, repo = _harness(tmp_path, child, _SPAWN)
    started = time.monotonic()
    proc = _run(script, repo)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert elapsed < 2, (
        f"the parent blocked for {elapsed:.1f}s — it must hand off, not wait."
    )
    marker = repo / ".git" / "child-ran"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.exists(), "the detached child did not survive the parent"
    assert "--child-marker alpha" in marker.read_text()


def test_the_child_never_inherits_detach(tmp_path: Path) -> None:
    """Load-bearing: a child that re-detached would fork-bomb the machine.

    This is the one failure mode of this design that is not merely wrong but
    destructive, so it is pinned on the argv the parent actually builds rather
    than on a comment promising the flag was stripped.
    """
    child = '\techo "$*" > "$REPO_ROOT/.git/child-ran"\n'
    script, repo = _harness(tmp_path, child, _SPAWN)
    _run(script, repo)
    marker = repo / ".git" / "child-ran"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.exists()
    assert "--detach" not in marker.read_text()
    # And the production parser must build the child argv without it.
    text = AUTO_PR.read_text(encoding="utf-8")
    assert "_AUTOPR_CHILD_ARGS" in text
    detach_arm = text[text.index("\t--detach)"):]
    detach_arm = detach_arm[: detach_arm.index(";;")]
    assert "_AUTOPR_CHILD_ARGS+=" not in detach_arm, (
        "the --detach arm must NOT append itself to the child argv"
    )


def test_detach_records_a_declared_terminal_state(tmp_path: Path) -> None:
    """The parent converged — it handed off — so it says so."""
    script, repo = _harness(tmp_path, '\ttrue\n', _SPAWN)
    _run(script, repo)
    payload = json.loads((repo / ".git" / "AUTOPR_LAST_RESULT.json").read_text())
    assert payload["final_state"] == "detached_watching"
    assert payload["exit_code"] == 0


def test_detach_writes_a_handle_naming_its_watcher(tmp_path: Path) -> None:
    """`wait` needs a pid to check and a log to show; both come from here."""
    script, repo = _harness(tmp_path, '\tsleep 3\n', _SPAWN)
    _run(script, repo)
    handle = json.loads((repo / ".git" / "AUTOPR_WATCH.json").read_text())
    assert isinstance(handle["watcher_pid"], int) and handle["watcher_pid"] > 0
    assert handle["log"].endswith(".log")
    assert handle["started_at"].endswith("Z")
    assert Path(handle["log"]).exists(), "the log must exist before the parent exits"


def test_detach_leaves_the_gate_up_for_the_watcher(tmp_path: Path) -> None:
    """PR_PENDING belongs to the run that is still working, not to the parent.

    The parent's finalize removes the gate by default; if it did so here the
    next `auto-pr` would start while a watcher was still mid-merge.
    """
    script, repo = _harness(tmp_path, '\tsleep 3\n', _SPAWN)
    _run(script, repo)
    assert (repo / ".git" / "PR_PENDING").exists()


# ---------------------------------------------------------------------------
# wait — three outcomes, never collapsed
# ---------------------------------------------------------------------------


def test_wait_returns_the_watchers_outcome_when_it_finished(tmp_path: Path) -> None:
    script, repo = _harness(tmp_path, '\ttrue\n', '\tdo_wait 30\n')
    (repo / ".git" / "AUTOPR_WATCH.json").write_text(json.dumps({
        "watcher_pid": 999999, "log": str(repo / ".git" / "w.log"),
        "started_at": "2026-09-20T00:00:00Z",
    }))
    (repo / ".git" / "w.log").write_text("done\n")
    (repo / ".git" / "AUTOPR_LAST_RESULT.json").write_text(json.dumps({
        "final_state": "merged", "exit_code": 0, "pr_number": 7,
        "timestamp": "2026-09-20T01:00:00Z",
    }))
    proc = _run(script, repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "merged" in proc.stdout


def test_wait_says_still_running_distinctly(tmp_path: Path) -> None:
    """A watcher still polling is not a failure and must not read as one."""
    script, repo = _harness(tmp_path, '\ttrue\n', '\tdo_wait 1\n')
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        (repo / ".git" / "AUTOPR_WATCH.json").write_text(json.dumps({
            "watcher_pid": sleeper.pid, "log": str(repo / ".git" / "w.log"),
            "started_at": "2026-09-20T00:00:00Z",
        }))
        (repo / ".git" / "w.log").write_text("polling\n")
        proc = _run(script, repo)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "still" in proc.stdout.lower()
    finally:
        sleeper.kill()
        sleeper.wait()


def test_wait_reports_a_vanished_watcher_rather_than_success(
    tmp_path: Path,
) -> None:
    """ABSENT is not EMPTY, one level out from the ledger.

    A dead pid with no result from this watcher is a watcher that died
    mid-poll. Reporting it as finished would hand the caller a merge that
    never happened; reporting it as still-running would hang them forever.
    """
    script, repo = _harness(tmp_path, '\ttrue\n', '\tdo_wait 1\n')
    (repo / ".git" / "AUTOPR_WATCH.json").write_text(json.dumps({
        "watcher_pid": 999999, "log": str(repo / ".git" / "w.log"),
        "started_at": "2026-09-20T00:00:00Z",
    }))
    (repo / ".git" / "w.log").write_text("polling\n")
    proc = _run(script, repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "vanished" in (proc.stdout + proc.stderr).lower()


def test_wait_with_no_handle_says_so(tmp_path: Path) -> None:
    """Nothing to wait for is its own answer, not an error about a missing file."""
    script, repo = _harness(tmp_path, '\ttrue\n', '\tdo_wait 1\n')
    proc = _run(script, repo)
    assert proc.returncode == 1
    assert "no detached" in (proc.stdout + proc.stderr).lower()


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def test_detach_is_documented_in_help() -> None:
    text = AUTO_PR.read_text(encoding="utf-8")
    assert "--detach" in text
    assert re.search(r'echo "\s*--detach', text), "--detach missing from --help"
    assert re.search(r'echo "\s*wait', text), "wait missing from --help"
