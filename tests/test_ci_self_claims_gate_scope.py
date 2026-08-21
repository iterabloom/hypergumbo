# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-bobor: a self-claim regression must reach CI within a bounded number of commits.

WHAT ACTUALLY HAPPENED, and why this file pins the trigger rather than adding a
scheduled arm. The runtime-cli-no-host-fs verdict regressed from
``confirmed_with_caveats`` to ``violated`` and sat on green ``dev`` for 63
commits. The filed root cause said the gate was path-triggered *only*. It was
not: an unconditional arm has lived in ``.woodpecker/full-suite.yml`` since
097ab4e05a (2026-08-16 03:31), forty-two minutes before the window opened, and
the fetched log for the 2026-08-19 01:00 cron firing shows it naming the
regression exactly::

    check-self-claims-drift: SELF-CLAIM DRIFT
      REGRESSED: runtime-cli-no-host-fs [verdict]: "confirmed_with_caveats" -> "violated"

Detection was never the failure. Verdict DELIVERY was, in three ways that a
second scheduled arm cannot fix:

* **Masking.** The cron publishes one aggregate status,
  ``ci/woodpecker/cron/full-suite``. At the 2026-08-20 13:00 firing that status
  was ``failure`` while this gate *passed* in the same run -- the red came from
  one flaky tracker TUI test inside ``test-all-packages``. A red aggregate
  therefore says nothing about the gate.
* **Addressing.** A cron status lands on whichever commit was tip at cron time,
  never ``HEAD`` by the time anyone looks, and ``scripts/ci-debug`` renders only
  ``HEAD``'s statuses. The instrument that answers "is dev green?" is
  structurally unable to see a cron verdict.
* **Error is not failure.** The run at ``e6cb77fa15`` that the item calls the
  gate's last is truncated mid-analysis with no verdict line, matching its
  ``error`` status. A pipeline that dies inside the gate is indistinguishable
  downstream from one whose gate passed.

So the fix is to put the verdict where the merge path already reads it. ``auto-pr``
polls exactly one context, ``ci/woodpecker/pr/woodpecker``; a gate inside that
pipeline bounds detection at ONE commit by construction and depends on no signal
delivery at all.

The trade this reverses was deliberate and is documented in
``woodpecker.yml`` -- analyzer ``.py`` changes rode the cron cadence because
"gating every core PR on a self-analysis was judged too heavy", priced there at
"~10 minutes". Measured on dev 6e290fcdb8 the gate is **3m49s** wall-clock, and
in CI it shares ``build-grammars`` with, and runs concurrently with, a pytest
step that took 935s in the same pipeline. The decision was made against a number
2.6x too high; these tests pin the corrected scope so it cannot silently narrow
again.

The final test pins a claim ``woodpecker.yml`` makes in prose and nothing
enforced -- that both arms run the same install so they "cannot disagree on
identical trees". Two copies of one command block is the shape this project
keeps being bitten by (LIVE.md rule 8), so it gets an executable trigger.
"""
from __future__ import annotations

from pathlib import Path

# A HARD import, deliberately, not pytest.importorskip. pyyaml is a declared
# dependency of hypergumbo-core and every CI container that runs tests installs
# that package, so yaml is always present -- which means importorskip could only
# ever produce a SILENT SKIP, turning this gate vacuous in the one place it
# matters. A missing yaml should be a loud collection error.
import yaml

REPO_ROOT = Path(__file__).parent.parent
PER_PR = REPO_ROOT / ".woodpecker" / "woodpecker.yml"
CRON = REPO_ROOT / ".woodpecker" / "full-suite.yml"

GATE = "self-claims-gate"


def _steps(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text())
    return {s["name"]: s for s in data["steps"]}


def _include_globs(step: dict) -> list[str]:
    """Flatten a step's ``when`` clauses down to their path include globs."""
    globs: list[str] = []
    for clause in step.get("when") or []:
        path_clause = clause.get("path")
        if isinstance(path_clause, dict):
            globs.extend(path_clause.get("include") or [])
    return globs


def test_per_pr_gate_exists_and_is_conditioned():
    """Non-vacuity floor: the other tests are meaningless if the step vanished."""
    steps = _steps(PER_PR)
    assert GATE in steps, f"{GATE} is absent from the pipeline auto-pr polls"
    assert steps[GATE].get("commands"), f"{GATE} declares no commands"


def test_per_pr_gate_fires_on_any_python_source_change():
    """The gate's subject is the whole analyzed tree, so its trigger must be too.

    ``verify-claims`` analyses every package under ``packages/*/src``. A verdict
    can therefore move on any Python change, not only on a catalogue edit -- which
    is exactly how the runtime-cli-no-host-fs regression entered. Anything
    narrower than ``**/*.py`` reopens INV-bobor.
    """
    step = _steps(PER_PR)[GATE]
    globs = _include_globs(step)
    assert "**/*.py" in globs, (
        "the per-PR self-claims gate does not fire on Python source changes; "
        f"its include list is {globs!r}. A claim verdict moves on analyzer "
        "changes, so a claim-surface-only trigger cannot bound detection."
    )


def test_cron_arm_stays_unconditional():
    """The second line of defence must survive the per-PR widening.

    The cron arm is what caught the regression on 2026-08-19. Widening the per-PR
    trigger is not a reason to drop it -- it is the only arm that runs against
    ``dev`` itself rather than against a PR merge result.
    """
    step = _steps(CRON)[GATE]
    assert not step.get("when"), (
        "the full-suite self-claims arm grew a when-clause; it must run on every "
        "cron firing, unconditionally"
    )


def test_both_arms_run_the_same_commands():
    """woodpecker.yml asserts the arms "cannot disagree on identical trees".

    Nothing enforced that. Two hand-maintained copies of one command block drift,
    and the drift shows up as two arms reporting different verdicts on the same
    tree -- which is unfalsifiable from either arm alone.
    """
    assert _steps(PER_PR)[GATE]["commands"] == _steps(CRON)[GATE]["commands"], (
        "the per-PR and cron self-claims arms run different command blocks, so "
        "they can analyse different language sets and disagree on one tree"
    )
