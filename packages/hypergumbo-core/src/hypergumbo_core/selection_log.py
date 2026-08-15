# SPDX-License-Identifier: AGPL-3.0-or-later
"""Durable evidence log for the coverage-selection shadow phase.

WHY THIS EXISTS AS A SEPARATE THING. The shadow shipped writing a single JSON
file that every run overwrote. The phase was designed to accrue evidence for
free from ordinary work, and as built it accrued nothing: each observation
destroyed the last, so the exit criterion could never be evaluated no matter
how long it ran.

The evidence is a FORWARD-ONLY TIME SERIES. An observation depends on what a
particular commit changed, what the index knew at that moment, and what failed.
None of that is reconstructable afterwards, so every run without a log is a
permanently lost data point — which is why this is append-only and why it was
worth interrupting the phase order to build.

THE CRITERION IS NOT A COUNT OF MISSES. A run can miss 87 files and lose
nothing, because none of them failed; that is the normal case. The only
disqualifying result is a test that ran, FAILED, and sits in a file coverage
would not have selected. Counting raw misses would reject the selector on its
first ordinary green run.

OBSERVATIONS THAT REST ON NOTHING ARE EXCLUDED, NOT COUNTED AS PASSES. A cold
index, or a commit whose changed files are all new code, selects nothing and
therefore "misses" everything. Admitting those would both flood the miss column
with phantoms and inflate the commit count toward the threshold without
evidence behind it. :attr:`Evidence.informative_rate` is reported for exactly
that reason: if most observations are disqualified, "30 commits" is a much
longer wait than it sounds, and that should be visible from the counter rather
than discovered weeks later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hypergumbo_core.selection_shadow import ShadowReport

#: Commits of admissible evidence before the criterion can be judged. Not a
#: statistical threshold — a floor, chosen so a handful of lucky green runs
#: cannot be mistaken for a safety result.
DEFAULT_MIN_COMMITS = 30


def append_observation(
    log_path: Path, report: ShadowReport, *, sha: str,
) -> None:
    """Append one observation as a JSON line. Never rewrites earlier lines."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    dangerous = report.dangerous_misses
    row = {
        "sha": sha,
        "informative": report.informative,
        "trustworthy": report.trustworthy,
        "join_rate": report.join_rate,
        "coverage_files": sorted(report.coverage_files),
        "actual_files": sorted(report.actual_files),
        "missed_by_coverage": sorted(report.missed_by_coverage),
        "extra_from_coverage": sorted(report.extra_from_coverage),
        # null (not []) when no junit was available, so "did not look" stays
        # distinguishable from "nothing failed".
        "dangerous_misses": None if dangerous is None else sorted(dangerous),
        "new_blocks": report.new_blocks,
        "unmeasured": report.unmeasured,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def load_observations(log_path: Path) -> list[dict[str, object]]:
    """Every observation, oldest first. A missing log is empty, not an error.

    Malformed lines are SKIPPED rather than fatal: this log is appended to by a
    tool that must never break the test run it rides along on, so a truncated
    final line (an interrupted run) must not make the whole history unreadable.
    """
    path = Path(log_path)
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@dataclass(frozen=True)
class Evidence:
    """The aggregate the phase is judged on."""

    observations: int
    informative: int
    commits: int
    dangerous: tuple[tuple[str, str], ...]
    unjudgeable: int

    @property
    def informative_rate(self) -> float:
        return self.informative / self.observations if self.observations else 0.0

    @property
    def verdict(self) -> str:
        """One of ``disqualified`` / ``insufficient`` / ``met``.

        ``disqualified`` outranks everything: one confirmed dangerous miss is
        not offset by any quantity of clean runs, so it is checked first and
        the commit count is irrelevant to it.
        """
        if self.dangerous:
            return "disqualified"
        if self.commits < DEFAULT_MIN_COMMITS:
            return "insufficient"
        return "met"

    def summary(self) -> str:
        lines = [
            f"  observations        : {self.observations}",
            f"  informative         : {self.informative} "
            f"({self.informative_rate:.0%})",
            f"  distinct commits    : {self.commits} / {DEFAULT_MIN_COMMITS}",
            f"  no failure data     : {self.unjudgeable}",
            f"  DANGEROUS MISSES    : {len(self.dangerous)}",
            f"  verdict             : {self.verdict.upper()}",
        ]
        for sha, test in self.dangerous[:5]:
            lines.append(f"      {sha[:12]}  {test}")
        return "\n".join(lines)


def evaluate(observations: Iterable[dict[str, object]]) -> Evidence:
    """Aggregate observations into the phase's verdict.

    Only INFORMATIVE observations count toward the commit total. An observation
    from a cold index reports every test as a miss and would otherwise advance
    the counter while contributing nothing.
    """
    rows = list(observations)
    # TRUSTWORTHY, not merely informative: a row whose join collapsed compared
    # the index against a test list that does not match it, so its miss set is
    # meaningless in BOTH directions. Admitting it would let a broken join
    # supply clean-looking evidence.
    informative = [r for r in rows if r.get("trustworthy")]
    dangerous: list[tuple[str, str]] = []
    unjudgeable = 0
    for row in informative:
        misses = row.get("dangerous_misses")
        if misses is None:
            unjudgeable += 1
            continue
        # Narrowed rather than trusted: this log is plain JSON on disk and a
        # malformed row must degrade to "skip", not to a crash in the reporter.
        if isinstance(misses, list):
            sha = str(row.get("sha", "?"))
            dangerous.extend((sha, str(t)) for t in misses)
    return Evidence(
        observations=len(rows),
        informative=len(informative),
        commits=len({r.get("sha") for r in informative if r.get("sha")}),
        dangerous=tuple(dangerous),
        unjudgeable=unjudgeable,
    )
