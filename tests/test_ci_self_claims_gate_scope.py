# SPDX-License-Identifier: AGPL-3.0-or-later
"""The self-claim drift gate runs on the twice-daily cron, and only there.

WHERE IT RUNS, AND THE TRADE-OFF. The gate (``self-claims-gate`` in
``.woodpecker/full-suite.yml``) is not in the per-PR pipeline: it is slow
enough to become the critical path of every code PR, and
``verify-claims --minimal`` reduces that cost without removing it. The price
is that detection is bounded by the cron cadence -- up to ~12 hours -- rather
than by one commit.

A cron-only gate has three delivery hazards; left unaddressed, any of them
lets a self-claim regression (e.g. ``runtime-cli-no-host-fs`` going
``confirmed_with_caveats`` -> ``violated``) sit on green ``dev`` for many
commits even though the cron named it (history: INV-bobor):

* **Masking.** ``full-suite.yml`` publishes ONE aggregate status for all its
  steps. A red aggregate -- from a flaky test in ``test-all-packages``, say --
  says nothing about this gate, and a red gate reads only as "full-suite is
  red". Once a sibling step is chronically red, nobody reads the log.
* **Addressing.** A cron status lands on whichever commit was tip at cron time,
  never ``HEAD`` by the time anyone looks.
* **Error is not failure.** A pipeline that dies inside the gate must not read
  as one whose gate passed.

``scripts/ci-debug cron-status`` (and ``status``) handle all three from the
reader's side: they read the latest cron verdict off whatever commit carries
it, and render every step's own verdict beneath the aggregate, read off the
Woodpecker pipeline API -- ``OK`` / ``FAIL`` / ``ERR`` / ``SKIP``
(test_ci_debug_cron_status.py pins it). If this gate ever needs its OWN GitHub
check context, the route is a per-gate workflow-file split (INV-bozid remedy
(a)); it must reuse the EXISTING ``full-suite`` cron name, because crons are
configured in repo settings and not in these files.

These tests pin the surviving arm: that it exists, runs unconditionally, and
actually invokes the gate.

NO TEST HERE ASSERTS THE PER-PR ARM'S ABSENCE. Pinning a removal would block
whoever re-adds it, and per-commit detection is a trade-off a future
measurement may well justify again.
"""

# covers: .woodpecker/*.yml, scripts/check-self-claims
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
    """NON-VACUITY FLOOR.

    The cron arm is the only one, so a silent disappearance here is a silent
    loss of the gate entirely -- there is no second arm to notice.
    """
    steps = _steps(CRON)
    assert GATE in steps, f"{GATE} is absent from the only pipeline that runs it"
    assert steps[GATE].get("commands"), f"{GATE} declares no commands"


def test_cron_arm_stays_unconditional():
    """The sole arm must not grow a path filter.

    A ``when:`` clause here would leave regressions undetected: a claim
    verdict moves on ANY analyzer change, so a path-scoped trigger cannot
    bound detection. It runs against ``dev`` itself rather than a PR merge
    result, and it is the only thing that does.
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
