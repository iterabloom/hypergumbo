# SPDX-License-Identifier: AGPL-3.0-or-later
"""The test-only-reachability ratchet runs on the LIVE cron (INV-hokin).

WHAT WENT WRONG. WI-ratuv's ratchet (``scripts/check-test-only-reachability``)
was added on 2026-09-02 to ``.github/workflows/full-suite.yml`` only. That
workflow's schedule had been commented out on 2026-07-23 for the forge
migration, and GitHub Actions is disabled under Woodpecker CI, so the gate WI-ratuv
was closed on ran nowhere: no scheduled and no per-PR run ever executed it. The
live cron is ``.woodpecker/full-suite.yml``, whose port carried the two sibling
self-tree gates (self-tree-validation, self-claims) but not this one, and no test
pinned it. The self-claims gate had been lost the same way before it
(``test_ci_self_claims_gate_scope.py``).

WHY IT IS A STEP OF ``self-tree-validation`` AND NOT A STEP OF ITS OWN. The gate
reads a behaviour map of this repository, which costs ~13 minutes to build
(script header), and ``self-tree-validation`` already builds one at
``/tmp/self-tree.json``. Woodpecker steps run in separate containers that share
only the workspace, so ``/tmp`` does not carry across steps: the gate has to run
in the step that built the map, after it. It is NOT per-PR, deliberately (owner
ruling 2026-09-30; WI-ratuv's design, PR #717).

WHAT THESE TESTS PIN.

* REACH: the step that builds the map invokes the gate, after the build, on
  the map that build wrote. Without this every other assertion is vacuous.
* The step stays unconditional and cannot be set to ignore failure.
* BEHAVIOUR, not presence: the step's own commands, from the map build on, are
  executed under ``/bin/sh -e`` -- the shell and ``errexit`` Woodpecker's docker
  backend runs a step's commands with (the per-PR pipeline relies on the same
  ``set -e``; see its ``|| TEST_EXIT=$?``). ``python`` is a stub on ``PATH`` that
  records each call and exits with a chosen code. So the exit-code contract is
  run, not grepped: exit 1 (regression) reds the step, exit 2 (infrastructure:
  no baseline, empty map) warns and passes, as the script header specifies.
* NO MASKING between the two ratchets sharing the step. Under ``set -e`` a bare
  failing first gate would abort the step before the second one ran, so a red
  self-tree-validation would hide the reachability verdict (and the reverse).
  Both are run with their exit codes captured; the step fails if either failed.

NOT PINNED HERE: whether the Woodpecker cron actually fires, or the gate's own
logic (``packages/hypergumbo-core/tests/test_test_only_reachability.py``). The
``/bin/sh -e`` emulation is this file's model of the runner; it was not checked
against a live Woodpecker run from this test.
"""

# covers: .woodpecker/*.yml, scripts/check-test-only-reachability
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

# A HARD import: pyyaml is a declared dependency of hypergumbo-core and every
# container that runs tests/ installs it. importorskip could only ever turn this
# pin into a silent skip (see test_ci_self_claims_gate_scope.py).
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CRON = REPO_ROOT / ".woodpecker" / "full-suite.yml"

GATE_SCRIPT = "scripts/check-test-only-reachability"
MAP_BUILD = "-m hypergumbo_core run"

#: Stand-in for ``python``: logs its argv, exits with the code chosen for the
#: script it was asked to run. 97 = something the step runs that this model
#: does not know about -- it must surface, not pass.
_STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
  *check-test-only-reachability*) exit "$REACH_RC" ;;
  *check-self-tree-validation*) exit "$STV_RC" ;;
  *"-m hypergumbo_core run"*) exit "$MAP_RC" ;;
esac
exit 97
"""


def _steps() -> dict[str, dict]:
    data = yaml.safe_load(CRON.read_text())
    return {s["name"]: s for s in data["steps"]}


def _map_step() -> tuple[str, list[str]]:
    """(name, commands) of the step that builds the self-map."""
    hits = [
        (name, step["commands"]) for name, step in _steps().items()
        if any(MAP_BUILD in c for c in step.get("commands") or [])
    ]
    assert len(hits) == 1, f"expected exactly one map-building step, got {hits}"
    return hits[0]


def _tail_from_map_build(commands: list[str]) -> list[str]:
    start = next(i for i, c in enumerate(commands) if MAP_BUILD in c)
    return commands[start:]


def _out_path(command: str) -> str:
    argv = shlex.split(command)
    return argv[argv.index("--out") + 1]


def _run_step(tmp_path: Path, *, map_rc: int = 0, stv_rc: int = 0,
              reach_rc: int = 0) -> tuple[int, str, list[str]]:
    """Run the map step's commands from the map build on, as Woodpecker would."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "python"
    stub.write_text(_STUB)
    stub.chmod(0o755)
    log = tmp_path / "calls.log"
    log.touch()
    script = "\n".join(_tail_from_map_build(_map_step()[1])) + "\n"
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "MAP_RC": str(map_rc), "STV_RC": str(stv_rc), "REACH_RC": str(reach_rc),
    }
    proc = subprocess.run(
        ["/bin/sh", "-e", "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr, log.read_text().splitlines()


def _gate_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if GATE_SCRIPT in c]


def test_the_map_building_step_invokes_the_gate_on_its_own_map() -> None:
    """REACH. The gate runs in the step that built the map, after the build,
    and reads the file that build wrote (``/tmp`` does not cross steps)."""
    name, commands = _map_step()
    assert name == "self-tree-validation"
    tail = _tail_from_map_build(commands)
    map_out = _out_path(tail[0])
    gate = [c for c in tail[1:] if GATE_SCRIPT in c]
    assert gate, (
        f"{CRON.name}:{name} builds {map_out} but never runs {GATE_SCRIPT} "
        "after it -- the WI-ratuv gate runs nowhere (INV-hokin)"
    )
    assert f"--map {map_out}" in " ".join(gate), (
        f"{GATE_SCRIPT} must read the map this step built ({map_out}); "
        f"got: {gate}"
    )


def test_the_gate_step_is_unconditional_and_cannot_ignore_failure() -> None:
    step = _steps()[_map_step()[0]]
    assert not step.get("when"), (
        "the step running the test-only-reachability gate grew a when-clause; "
        "it must run on every cron firing"
    )
    assert step.get("failure") != "ignore", (
        "the gate step is `failure: ignore` -- a red ratchet would block nothing"
    )


def test_both_gates_run_and_pass_on_a_clean_tree(tmp_path: Path) -> None:
    rc, out, calls = _run_step(tmp_path)
    assert rc == 0, out
    assert len(_gate_calls(calls)) == 1, calls
    assert any("check-self-tree-validation" in c for c in calls), calls


def test_a_regression_reds_the_step(tmp_path: Path) -> None:
    """THE TEETH: exit 1 from the ratchet must fail the step."""
    rc, out, calls = _run_step(tmp_path, reach_rc=1)
    assert _gate_calls(calls), calls
    assert rc != 0, f"the ratchet exited 1 and the step still passed:\n{out}"


def test_an_infrastructure_exit_warns_and_passes(tmp_path: Path) -> None:
    """Exit 2 (no baseline / empty map) is not a regression (script header);
    it must warn visibly rather than pass silently or fail."""
    rc, out, calls = _run_step(tmp_path, reach_rc=2)
    assert _gate_calls(calls), calls
    assert rc == 0, out
    assert "test-only-reachability" in out and "skipped" in out, out


@pytest.mark.parametrize(("stv_rc", "reach_rc"), [(1, 0), (0, 1), (1, 1)])
def test_neither_ratchet_masks_the_other(tmp_path: Path, stv_rc: int,
                                         reach_rc: int) -> None:
    """A red sibling ratchet must not stop the other from running and
    reporting; the step fails if either does."""
    rc, out, calls = _run_step(tmp_path, stv_rc=stv_rc, reach_rc=reach_rc)
    assert any("check-self-tree-validation" in c for c in calls), calls
    assert _gate_calls(calls), (
        f"self-tree-validation exited {stv_rc} and the reachability ratchet "
        f"never ran:\n{out}"
    )
    assert rc != 0, out


def test_a_failed_map_build_fails_the_step(tmp_path: Path) -> None:
    """CONTROL for the runner model: if ``set -e`` were not in force here, a
    failed map build would fall through to the gates. It must stop the step."""
    rc, _, calls = _run_step(tmp_path, map_rc=1)
    assert rc != 0
    assert not _gate_calls(calls), calls
