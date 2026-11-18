# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local test run's wall time, said against its target.

WHY THIS EXISTS. The work on test selection has one aim a person can feel: a
local ``smart-test`` run short enough to wait for. The owner's target is 60
seconds of wall time (ruling of 2026-11-17). It is REPORTED by every run and
gates nothing: a number nobody is shown does not get smaller, and a gate on
wall time would fail a run for the machine being busy.

WHAT A RUN SAYS. One line. Under the target: the total and its parts. Over
it: by how much, the parts largest first, and the slowest single test, which
bounds a run's wall time whatever the number of workers (one test of 67 s
sat in most selections until it was split).

THE PARTS are what ``smart-test`` can time around its own steps: the type
gate, the analysis or the walk of a stored graph, the tests. What is left of
the total is given as ``the rest`` and not guessed at (change detection,
narrowing, writing the manifest). The clock stops when the tests end: the
coverage gate and the fold into the selection index come after this line is
printed, and the line says "to the end of the tests".

UNITS. Wall seconds for the run and its parts. For the tests the line adds
their summed duration from the junit report ("test-seconds"), which exceeds
their wall time by about the number of workers.

PURE BY DESIGN, except :func:`junit_times`, which reads one junit file.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 -- our own junit report
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

#: The local target, in wall seconds (owner ruling, 2026-11-17).
TARGET_SECONDS = 60


@dataclass(frozen=True)
class SuiteTimes:
    """What a junit report says about how long the tests took."""

    count: int
    #: Summed duration of every test case.
    seconds: float
    #: ``(id, seconds)`` of the longest one; None when the report has no case.
    slowest: Optional[tuple[str, float]]


def junit_times(junit_path: Path) -> Optional[SuiteTimes]:
    """The test durations in a junit report; None when it cannot be read
    ("no report" must not read as "no tests")."""
    try:
        # Written moments earlier by this run's own pytest; never fetched.
        root = ET.parse(junit_path).getroot()  # noqa: S314  # nosec B314
    except (OSError, ET.ParseError):
        return None
    count, total = 0, 0.0
    slowest: Optional[tuple[str, float]] = None
    for case in root.iter("testcase"):
        try:
            seconds = float(case.get("time") or 0)
        except ValueError:
            seconds = 0.0
        count += 1
        total += seconds
        if slowest is None or seconds > slowest[1]:
            slowest = (f"{case.get('classname') or '?'}::{case.get('name') or '?'}", seconds)
    return SuiteTimes(count=count, seconds=total, slowest=slowest)


def report(*, total: float, parts: Mapping[str, float], tests: Optional[SuiteTimes],
           target: float = TARGET_SECONDS) -> str:
    """The line a run prints (module docstring, WHAT A RUN SAYS).

    ``parts`` maps a part's name to its wall seconds; what they leave of
    ``total`` is reported as ``the rest``.
    """
    known = {name: seconds for name, seconds in parts.items() if seconds > 0}
    rest = total - sum(known.values())
    if rest >= 1:
        known["the rest"] = rest
    ordered = sorted(known.items(), key=lambda item: -item[1])
    said = ", ".join(f"{name} {seconds:.0f} s" for name, seconds in ordered) or "nothing timed"
    if tests is None:
        about = "no junit report"
    else:
        about = f"{tests.count} tests, {tests.seconds:.0f} test-seconds"
    head = f"smart-test: {total:.0f} s to the end of the tests"
    if total <= target:
        return f"{head} (local target {target:.0f} s): {said} ({about})"
    line = (f"{head}, {total - target:.0f} s OVER the {target:.0f} s local target. "
            f"Largest first: {said} ({about})")
    if tests is not None and tests.slowest is not None:
        name, seconds = tests.slowest
        line += f". Slowest test: {seconds:.1f} s {name}"
    return line
