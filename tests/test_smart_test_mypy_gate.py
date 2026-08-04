# SPDX-License-Identifier: AGPL-3.0-or-later
"""The mypy strict ratchet must gate testing, locally and in CI.

WHY THIS FILE EXISTS. INV-zogud's ratchet is shrink-only and was documented in
AGENTS.md and the Wave-5 notebooks as "BLOCKING, instrument-pinned to mypy
2.1.x". On the pipeline that actually gates merges it was neither.

``auto-pr`` polls exactly one CI context — ``ci/woodpecker/pr/woodpecker`` —
and merges on it. That Woodpecker step ran ``--mode=warning`` *and* carried
``failure: ignore``, so the ratchet could not fail a pipeline by either route.
Nothing ran mypy locally at all: not ``.githooks/pre-commit`` (tracker schema,
five axis gates, test manifest), not ``.githooks/pre-push`` (protected-branch
block only), not ``scripts/auto-pr``, not the documented pre-commit checklist.

Measured consequence: dev drifted **672 → 682** across sixty commits touching
24 source files. One of the ten new errors reported a *live* ``isinstance``
guard as "always true", and the comment three lines above that guard explains
exactly why it exists — draining it the obvious way deletes it. That is the
shape that once took the whole-tree ratchet to a CLEAN 832 → 789 on a rewrite
deleting 43 live guards.

WHAT THESE TESTS PIN, AND WHY EACH IS NOT REDUNDANT. Three edits were needed
and **two of them are silent alone**: with ``failure: ignore`` still set,
``--mode=strict`` exits 1 and Woodpecker passes the step anyway, so flipping
the mode by itself reads as a fix and gates nothing. A test that checked only
the mode would have gone green over exactly that. So the mode, the
failure-policy and the version pin are each asserted separately.

The gate's *message* is pinned too, and deliberately. Its job is to close the
"not my change" exit — the response that turns a two-line drift into a
ten-error one — and a message that quietly loses that clause has lost the
thing it was written for.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"
WOODPECKER = REPO_ROOT / ".woodpecker" / "woodpecker.yml"
GHA_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RATCHET = REPO_ROOT / "scripts" / "check-mypy-ratchet"


def _mypy_step() -> dict:
    """The Woodpecker step that runs the ratchet, found by what it RUNS.

    Located by its command rather than by name, so renaming the step cannot
    quietly move it out from under this test.
    """
    doc = yaml.safe_load(WOODPECKER.read_text(encoding="utf-8"))
    for step in doc["steps"]:
        if any(
            "check-mypy-ratchet" in str(cmd)
            for cmd in (step.get("commands") or [])
        ):
            return step
    raise AssertionError(
        "no Woodpecker step runs check-mypy-ratchet — the gating pipeline "
        "does not check the ratchet at all"
    )


class TestEveryInvocationIsStrict:
    """Warning mode exits 0 on a regression, so it is not a gate."""

    def test_woodpecker_runs_strict(self) -> None:
        commands = " ".join(str(c) for c in _mypy_step()["commands"])
        assert "check-mypy-ratchet --mode=strict" in commands
        assert "--mode=warning" not in commands

    def test_github_actions_runs_strict(self) -> None:
        text = GHA_CI.read_text(encoding="utf-8")
        assert "check-mypy-ratchet --mode=strict" in text
        assert "check-mypy-ratchet --mode=warning" not in text

    def test_smart_test_runs_strict(self) -> None:
        text = SMART_TEST.read_text(encoding="utf-8")
        assert "check-mypy-ratchet\" --mode=strict" in text.replace("'", '"')

    def test_no_invocation_anywhere_uses_warning_mode(self) -> None:
        """The whole-tree assertion, so a fourth call site cannot be lax.

        Scoped to lines that invoke THE RATCHET, on purpose. Two earlier
        drafts of this test were wrong in opposite directions and both are
        worth recording:

        * grepping the raw text for ``--mode=warning`` failed on all three
          files, because each one *documents* why warning mode is wrong — the
          forbidden string appears in the prose explaining the prohibition;
        * dropping comments and grepping the remainder still failed, because
          ``check-schema-coverage --mode=warning`` is a DIFFERENT tool that is
          legitimately advisory. That draft would have forbidden correct
          configuration, which is worse than not testing at all.

        So the unit is the invocation, not the file: find every executable
        line that runs ``check-mypy-ratchet`` and assert each one is strict.
        """
        offenders = []
        for path in (SMART_TEST, WOODPECKER, GHA_CI):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if "check-mypy-ratchet" not in line:
                    continue
                if "--mode=" in line and "--mode=strict" not in line:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}: {line.strip()}"
                    )
        assert not offenders, (
            f"these invoke the ratchet in a non-strict mode, which cannot "
            f"gate: {offenders}"
        )


class TestWoodpeckerActuallyFails:
    """--mode=strict is inert while the step is allowed to fail."""

    def test_mypy_step_does_not_ignore_failure(self) -> None:
        """The silent half. With this set, strict mode gates nothing.

        Woodpecker's ``failure: ignore`` is the direct analogue of GitHub's
        ``continue-on-error``: the step reports red and the pipeline passes.
        A reviewer reading only the ``--mode=strict`` flag would conclude the
        ratchet blocks.
        """
        step = _mypy_step()
        assert step.get("failure") != "ignore", (
            "the ratchet step is marked `failure: ignore`, so --mode=strict "
            "exits 1 and the pipeline passes anyway"
        )

    def test_github_actions_mypy_job_does_not_continue_on_error(self) -> None:
        doc = yaml.safe_load(GHA_CI.read_text(encoding="utf-8"))
        job = doc["jobs"]["mypy"]
        assert job.get("continue-on-error") in (None, False)


class TestInstrumentIsPinned:
    """A baseline's counts are only comparable against the mypy that made them."""

    @pytest.mark.parametrize("path", [WOODPECKER, GHA_CI])
    def test_mypy_is_pinned_to_the_baseline_minor(self, path: Path) -> None:
        """``~=2.1`` allows 2.2/2.3; ``~=2.1.0`` does not.

        Under the loose spec a fresh install can resolve a mypy the baseline
        was not measured with, at which point the ratchet refuses to compare
        at all — and under the old warning mode that refusal was silent too.
        """
        code = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        loose = re.findall(r'mypy~=2\.1(?![.\d])', code)
        assert not loose, (
            f"{path.name} pins mypy loosely ({loose}); the baseline records a "
            f"specific version and the ratchet cannot compare across versions"
        )
        assert "mypy~=2.1.0" in code

    def test_baseline_records_the_instrument(self) -> None:
        import json
        baseline = json.loads(
            (REPO_ROOT / ".ci" / "mypy-strict-baseline.json").read_text(
                encoding="utf-8",
            ),
        )
        assert baseline.get("mypy_version"), (
            "the baseline records no mypy version, so nothing can detect that "
            "the counts under it were measured by a different instrument"
        )


class TestSmartTestGate:
    """The local half: the ratchet runs first, and a breach stops testing."""

    def test_gate_precedes_the_pytest_invocation(self) -> None:
        """"First thing that runs" is the requirement, so assert the ORDER.

        A gate placed after the slice or after pytest would still be a gate
        and would still be useless: the point is not to spend twenty minutes
        of test time on a tree whose type contracts have already regressed.
        """
        text = SMART_TEST.read_text(encoding="utf-8")
        gate_at = text.index("check-mypy-ratchet")
        # The real pytest invocation, not the arg-parsing that precedes it.
        run_at = text.index("PYTEST_LOG=")
        assert gate_at < run_at, (
            "the mypy gate must run before smart-test does any work"
        )

    def test_a_violation_exits_without_running_pytest(self) -> None:
        text = SMART_TEST.read_text(encoding="utf-8")
        block = text[text.index("MYPY_GATE_RC=$?"):]
        assert "exit 1" in block

    def test_the_message_closes_the_not_my_fault_exit(self) -> None:
        """The clause this gate exists for.

        Ten errors survived sixty commits because each agent in turn could
        observe a breach it had not caused and decline to own it. A message
        that loses this clause has lost its point, so it is pinned rather
        than left to editorial drift.
        """
        text = SMART_TEST.read_text(encoding="utf-8")
        assert "EVEN IF YOU DID NOT CAUSE IT" in text
        assert '"Not my change" is not an exit' in text

    def test_the_message_forbids_the_two_known_bypasses(self) -> None:
        """Direct pytest, and the escape hatch this gate itself introduces.

        Honest limit, recorded here rather than implied: the gate lives in
        smart-test, which is what the ``pytest`` alias invokes, so it covers
        the normal path. A direct ``python -m pytest`` goes around it. Text is
        the enforcement there, not a mechanism — which is exactly why the text
        is asserted.
        """
        text = SMART_TEST.read_text(encoding="utf-8")
        assert "python -m pytest" in text
        assert "HG_SKIP_MYPY_GATE" in text

    def test_the_message_forbids_raising_the_baseline(self) -> None:
        """L33: a shrink-only baseline may only ratchet DOWN.

        Raising it is the fastest way to make this gate green and the worst,
        because it ratifies the drift and destroys the record of what the
        surface used to be.
        """
        text = SMART_TEST.read_text(encoding="utf-8")
        assert "mypy-strict-baseline.json" in text
        assert "may only ratchet" in text

    def test_infrastructure_failure_does_not_block(self) -> None:
        """Exit 2 is not a regression, per the ratchet's own contract.

        A missing toolchain must not make the repository untestable. This is
        the ratchet's documented rule, not a carve-out invented here — its
        docstring reads "2 — infrastructure error ... Never blocks: an infra
        failure is not a type regression."
        """
        # Whitespace-tolerant: the sentence wraps across lines in the
        # docstring, so a literal "Never blocks" substring does not match.
        assert re.search(
            r"Never\s+blocks", RATCHET.read_text(encoding="utf-8"),
        ), "the ratchet no longer documents exit 2 as non-blocking"
        text = SMART_TEST.read_text(encoding="utf-8")
        assert "MYPY_GATE_RC -eq 2" in text
        block = text[text.index("MYPY_GATE_RC -eq 2"):]
        assert "continuing" in block.split("fi")[0]


class TestGateFiresForReal:
    """Behavioural: run the script against a baseline it must fail."""

    def test_smart_test_refuses_to_run_tests_on_a_regression(
        self, tmp_path: Path,
    ) -> None:
        """A doctored baseline of all-zeros is a guaranteed regression.

        This runs the REAL ``scripts/smart-test`` and asserts it exits
        non-zero with the block banner and without reaching pytest — the
        component-level assertions above cannot show that the wiring holds
        end to end (L13: a component correct at unit level can be inert).
        """
        baseline = tmp_path / "zero-baseline.json"
        baseline.write_text('{"total": 0, "by_code": {}}', encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c",
             f'cd {REPO_ROOT} && '
             f'scripts/check-mypy-ratchet --mode=strict --baseline {baseline}'],
            capture_output=True, text=True, timeout=900,
        )
        # Positive control for the fixture: the doctored baseline really does
        # make the ratchet fail. Without this, a gate test could pass because
        # the ratchet never failed at all.
        assert result.returncode == 1, (
            f"the all-zero baseline did not produce a regression "
            f"(rc={result.returncode}); the fixture is not exercising the gate"
        )
        assert "REGRESSION" in result.stdout + result.stderr
