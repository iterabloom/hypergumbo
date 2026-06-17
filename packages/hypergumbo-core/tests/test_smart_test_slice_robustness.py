# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-tolil: smart-test must not silently swallow a reverse-slice failure.

``scripts/smart-test`` runs under ``set -euo pipefail``. The reverse-slice
invocation (``hypergumbo slice --files …``) computes the affected-test set;
its result feeds a deliberate fallback (``run_full_suite "slice command
failed"``). But if that invocation runs as a *bare* command, a non-zero slice
exit makes ``set -e`` abort the whole script **at the slice line**, before
``SLICE_EXIT`` is captured and before the fallback can run — a silent swallow:
the agent (or CI) sees a non-zero exit with no output and no
``.ci/pytest-output.log``, having never run a single test.

These are *static source guards* — ``smart-test`` is bash, so it contributes
no pytest coverage and has no behavioral harness; the repo's convention for
that case is a source-level invariant test (cf.
``test_edge_provenance_invariant.py``). They pin the two structural
properties whose absence reintroduces the swallow / the failover over-select:

1. the slice invocation that feeds ``SLICE_EXIT`` must capture its exit
   (``|| SLICE_EXIT=…``) rather than run bare under ``set -e``; and
2. baseline detection must be failover-aware (anchor on the authoritative
   remote), or under permanent failover the stale ``origin/dev`` merge-base
   forces a whole-tree slice every run.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"


def _smart_test_lines() -> list[str]:
    return SMART_TEST.read_text().splitlines()


def test_smart_test_present() -> None:
    """Liveness floor: the script exists where the guards expect it."""
    assert SMART_TEST.is_file(), f"smart-test not found at {SMART_TEST}"


def test_slice_invocation_captures_exit_not_bare() -> None:
    """The reverse-slice invocation must capture its exit code so the
    ``set -e`` shell does not abort before the slice-failure fallback runs
    (WI-tolil defect 1: the silent swallow)."""
    # The line that actually RUNS the slice and writes SLICE_OUTPUT — not the
    # ``SLICE_CMD="…"`` string assignment used only for diagnostics.
    invocations = [
        ln for ln in _smart_test_lines()
        if "slice --files" in ln
        and "$SLICE_OUTPUT" in ln
        and "SLICE_CMD=" not in ln
        and not ln.lstrip().startswith("#")
    ]
    assert invocations, (
        "could not find the reverse-slice invocation in smart-test; the guard "
        "needs updating if the slice call moved/renamed"
    )
    for ln in invocations:
        assert re.search(r"\|\|\s*SLICE_EXIT=", ln) or "|| true" in ln, (
            "slice invocation runs bare under `set -e` — a non-zero slice exit "
            "would abort smart-test before the `run_full_suite \"slice command "
            f"failed\"` fallback. Capture the exit (`|| SLICE_EXIT=$?`). Line:\n  {ln}"
        )


def test_baseline_detection_is_failover_aware() -> None:
    """Baseline detection must consult the failover marker so the merge-base
    anchors on the authoritative remote (WI-tolil defect 2). Under permanent
    failover, ``origin/dev`` is stale and the merge-base would otherwise span
    the whole tree, forcing a slow/failure-prone whole-repo slice every run."""
    text = SMART_TEST.read_text()
    assert "CI_FAILOVER_ACTIVE" in text, (
        "smart-test baseline detection is not failover-aware: it does not "
        "consult .git/CI_FAILOVER_ACTIVE, so under failover it anchors the "
        "merge-base on a stale origin/dev and slices the whole tree."
    )
