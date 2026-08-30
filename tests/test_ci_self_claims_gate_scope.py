# SPDX-License-Identifier: AGPL-3.0-or-later
"""The self-claim drift gate runs on the twice-daily cron, and only there.

THIS FILE HAS PINNED TWO OPPOSITE DECISIONS, so it carries both.

ROUND 1 (INV-bobor). The gate lived only on the cron. A self-claim regression --
``runtime-cli-no-host-fs`` going ``confirmed_with_caveats`` -> ``violated`` --
sat on green ``dev`` for 63 commits. DETECTION WAS NEVER THE FAILURE: the cron
arm named it exactly, at the 2026-08-19 01:00 firing. Delivery failed, three
ways:

* **Masking.** ``full-suite.yml`` publishes ONE aggregate status for all six of
  its steps. At the 2026-08-20 firing that status was ``failure`` while this
  gate PASSED -- the red came from a flaky tracker TUI test inside
  ``test-all-packages``. A red aggregate says nothing about the gate.
* **Addressing.** A cron status lands on whichever commit was tip at cron time,
  never ``HEAD`` by the time anyone looks, and ``scripts/ci-debug`` rendered
  only ``HEAD``.
* **Error is not failure.** A pipeline that dies inside the gate is
  indistinguishable downstream from one whose gate passed.

So the gate was moved INTO the per-PR pipeline, where ``auto-pr`` refuses to
merge on it and detection is bounded at one commit by construction.

ROUND 2 (owner decision, 2026-08-30). The per-PR arm is removed; the cron arm is
now the only one. Two things changed, and only one of them is an opinion:

* **The price the move rested on went stale by ~3.7x.** Round 1 measured the
  gate at 3m49s, running concurrently with a 935s pytest step -- i.e. free. It
  now takes ~14 minutes and finishes LAST, making it the critical path of every
  code PR. ``verify-claims --minimal`` was added in the same change and takes
  30.4% off the analysis (1450s -> 1009s cold, byte-identical output), which
  reduces but does not remove that.
* **One of the three delivery failures is fixed.** ``ci-debug cron-status``
  (INV-bozid remedy (b)) reads the latest cron verdict off whatever commit
  carries it, so ADDRESSING is closed.

WHAT REMAINS UNFIXED, AND WAS ACCEPTED RATHER THAN SOLVED: masking and
error-is-not-failure. Both follow from the single aggregate status, so a
self-claim regression now reads as "full-suite is red" and needs a log fetch to
attribute. The judgement was that a log fetch is cheap WHEN THE AGGREGATE IS
USUALLY GREEN -- the failure mode is a sibling going chronically red, at which
point "full-suite is red" carries no information and nobody reads the log. That
is precisely what produced the 63 commits. The risk is lower than in August (the
flaky TUI test behind the 2026-08-20 masking was fixed the same day as this
change) but it is not gone.

THE TRIPWIRE, stated so it can be acted on rather than rediscovered: if
``full-suite`` starts sitting red, give this gate its own workflow file and
therefore its own commit-status context (INV-bozid remedy (a), verified viable;
it must reuse the EXISTING ``full-suite`` cron name, because crons are
configured in repo settings and not in these files).

Detection is now bounded by the cron cadence -- up to ~12 hours -- not by one
commit. These tests pin what is left: that the surviving arm exists, runs
unconditionally, and actually invokes the gate.

NO TEST HERE ASSERTS THE PER-PR ARM'S ABSENCE. Pinning a removal would block
whoever re-adds it, and Round 1 says that is a decision a future measurement may
well justify again.
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
CRON = REPO_ROOT / ".woodpecker" / "full-suite.yml"

GATE = "self-claims-gate"


def _steps(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text())
    return {s["name"]: s for s in data["steps"]}


def test_cron_arm_exists_and_declares_commands():
    """NON-VACUITY FLOOR, and it carries more weight than it used to.

    While the per-PR arm existed this was one of two defences. It is now the
    only one, so a silent disappearance here is a silent loss of the gate
    entirely -- there is no second arm to notice.
    """
    steps = _steps(CRON)
    assert GATE in steps, f"{GATE} is absent from the only pipeline that runs it"
    assert steps[GATE].get("commands"), f"{GATE} declares no commands"


def test_cron_arm_stays_unconditional():
    """The sole arm must not grow a path filter.

    A ``when:`` clause here would reintroduce Round 1's defect in its original
    form: a claim verdict moves on ANY analyzer change, so a path-scoped trigger
    cannot bound detection. It runs against ``dev`` itself rather than a PR merge
    result, and it is now the only thing that does.
    """
    step = _steps(CRON)[GATE]
    assert not step.get("when"), (
        "the full-suite self-claims arm grew a when-clause; it must run on every "
        "cron firing, unconditionally -- it is the only arm left"
    )


def test_cron_arm_actually_invokes_the_gate():
    """The step must run check-self-claims, not merely exist.

    ``test_cron_arm_exists_and_declares_commands`` passes for a step whose
    commands install dependencies and stop. With one arm remaining, "the step is
    present" and "the gate runs" have to be asserted separately.
    """
    commands = " ".join(_steps(CRON)[GATE]["commands"])
    assert "scripts/check-self-claims" in commands, (
        f"the {GATE} step no longer invokes scripts/check-self-claims"
    )
