# SPDX-License-Identifier: AGPL-3.0-or-later
"""The self-claims COVERAGE guard runs per PR (INV-jojog).

``test_ci_self_claims_gate_scope.py`` records that the DRIFT gate's per-PR arm
was removed on 2026-08-30 for cost, leaving detection to the twice-daily cron.
Four of the five reds since were one shape -- a shipped call into a module the
I/O catalogue cannot classify -- and that shape is decidable from the python
analyzer's edges alone, in ~30 s. This pins the cheap arm that decides it:

* the step exists in the per-PR pipeline and actually invokes the script;
* its path filter covers everything that can move the answer -- shipped
  source (the catalogue is under ``packages/*/src``), the self overlay, the
  claims file, the script itself. A filter that missed one of these would
  reintroduce the gap on exactly the change that opens it;
* it installs the analyzer package the guard needs, and cannot be set to
  ignore failure.

NOT PINNED HERE: the guard's own logic
(``packages/hypergumbo-core/tests/test_claims_coverage_guard.py``), nor the
cron arm, which ``test_ci_self_claims_gate_scope.py`` keeps.
"""

# covers: .woodpecker/woodpecker.yml, scripts/check-claims-coverage
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
PER_PR = REPO_ROOT / ".woodpecker" / "woodpecker.yml"
STEP = "claims-coverage"
SCRIPT = "scripts/check-claims-coverage"


def _step() -> dict:
    data = yaml.safe_load(PER_PR.read_text())
    steps = {s["name"]: s for s in data["steps"]}
    assert STEP in steps, f"{STEP} is absent from the per-PR pipeline"
    return steps[STEP]


def _included_paths() -> list[str]:
    paths: list[str] = []
    for clause in _step().get("when", []):
        paths.extend((clause.get("path") or {}).get("include", []))
    return paths


def test_the_step_invokes_the_guard() -> None:
    commands = " ".join(_step()["commands"])
    assert SCRIPT in commands


def test_the_step_installs_the_analyzer_the_guard_runs() -> None:
    """The guard reports exit 2 without the python analyzer; a step that
    installed only core would pass forever by never asking the question."""
    commands = " ".join(_step()["commands"])
    assert "packages/hypergumbo-lang-mainstream" in commands
    assert "packages/hypergumbo-core" in commands


def test_the_path_filter_covers_everything_that_moves_the_answer() -> None:
    included = _included_paths()
    for required in (
        "packages/*/src/**",
        "docs/io-primitives-overlays/**",
        "docs/hypergumbo.claims.yaml",
        SCRIPT,
    ):
        assert required in included, f"{STEP} would not run on a change to {required}"


def test_the_step_cannot_ignore_failure() -> None:
    step = _step()
    assert not step.get("failure"), "the guard must red the pipeline, not warn"


def test_the_script_is_executable_and_declares_its_exit_codes() -> None:
    script = REPO_ROOT / SCRIPT
    assert script.is_file()
    assert script.stat().st_mode & 0o111, f"{SCRIPT} is not executable"
    text = script.read_text()
    for marker in ("0   qualifiable", "1   WITHHELD", "2   infrastructure"):
        assert marker in text
