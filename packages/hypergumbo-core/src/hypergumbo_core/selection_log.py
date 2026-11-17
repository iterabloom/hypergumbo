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
therefore "misses" everything; a run whose test-id join mostly failed
(``join_rate < 0.5``) is excluded too. Admitting those would both flood the
miss column with phantoms and inflate the commit count toward the threshold
without evidence behind it. :attr:`Evidence.informative_rate` is reported for
exactly that reason: if most observations are disqualified, "30 commits" is a
much longer wait than it sounds, and that should be visible from the counter
rather than discovered weeks later.

TWO MORE LOGS LIVE HERE, each append-only for the same reason and each kept in
its own file so that neither can change the verdict above.

THE STALE-GRAPH LOG answers one question: would the reverse slice have named
the same test files from a graph recorded at an OLDER tree? Analysing the whole
repository takes minutes and walking a stored graph takes seconds, so the
answer decides whether selection can stop paying for a fresh analysis. Each
time smart-test computes a fresh slice it also asks what the graphs STORED
before it would have selected for the same tree (``coverage-select
stale-record``; the stored graphs and the selection are
:mod:`~hypergumbo_core.graph_rows` and :mod:`~hypergumbo_core.graph_select`)
and stores both answers (:func:`stale_graph_row`). The one result that counts
against a stored graph is a test file the fresh walk names, the stored
graph's selection lacks, and the change did not itself edit: smart-test
always runs an edited test file, so a new or edited one that an older graph
cannot know is not a loss. A run with no stored graph to compare is stored
with the reason and counts for nothing. Rows written before 2026-11-17
compared a walk of an older SURVEY seeded with the change's source files
only; their ``graph`` holds a ``survey`` path where later rows hold an
``id``.

THE RUN RECORDS are what a selector that does not exist yet will be judged
on. The shadow log above stores only the misses of ONE selector, so a new
selector cannot be replayed against it. A run record stores the facts
themselves: the commit, the baseline, which files differed, which test files
ran, and the id of every test that did not pass (:func:`append_run_record`).
A run on an uncommitted tree also stores the change (:func:`store_snapshot`),
because a tree that was never committed cannot be recovered afterwards and a
selector can only be replayed against a tree it can read.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from hypergumbo_core.selection_shadow import ShadowReport

#: Commits of admissible evidence before the criterion can be judged. Not a
#: statistical threshold — a floor, chosen so a handful of lucky green runs
#: cannot be mistaken for a safety result.
DEFAULT_MIN_COMMITS = 30


def _append(log_path: Path, row: dict[str, object]) -> None:
    """Append one row as a JSON line. Never rewrites earlier lines."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def append_observation(
    log_path: Path, report: ShadowReport, *, sha: str,
) -> None:
    """Append one selection-shadow observation."""
    dangerous = report.dangerous_misses
    row: dict[str, object] = {
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
    _append(log_path, row)


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


#: Distinct commits on which the stale walk must have been compared with the
#: fresh one, with no unedited test file missing, before a stored graph may
#: select. The same floor as the coverage shadow, for the same reason.
STALE_GRAPH_MIN_COMMITS = 30


#: A test file as the reverse slice's consumer recognises one. The same
#: pattern smart-test applies to the fresh slice's output, so both walks are
#: read the same way.
_SLICED_TEST = re.compile(r"packages/.*/tests/(test_|BRANCHES_test_).*\.py$")
_SLICED_SOURCE = re.compile(r"^packages/.*/src/.*\.py$")


def sliced_test_files(lines: Iterable[str], repo_root: str) -> frozenset[str]:
    """The test files among a slice's output lines, repo-relative. The slice
    may print absolute paths; a path outside ``repo_root`` is kept as it is."""
    prefix = repo_root.rstrip("/") + "/"
    out = set()
    for line in lines:
        path = line.strip()
        if _SLICED_TEST.search(path):
            out.add(path[len(prefix):] if path.startswith(prefix) else path)
    return frozenset(out)


def sliceable_sources(changed_files: Iterable[str]) -> frozenset[str]:
    """The changed files the reverse slice is seeded with: package source."""
    return frozenset(f for f in changed_files if _SLICED_SOURCE.match(f))


def stale_graph_row(
    *,
    fresh: Iterable[str],
    stale: Optional[Iterable[str]],
    edited_tests: Iterable[str],
    head: str,
    at: str,
    changed_source: Iterable[str] = (),
    graph: Optional[dict[str, object]] = None,
    why_no_comparison: Optional[str] = None,
    fresh_seconds: Optional[float] = None,
    stale_seconds: Optional[float] = None,
) -> dict[str, object]:
    """One stale-graph observation.

    ``stale=None`` means no comparison was made (``why_no_comparison`` says
    why), which is not the same as a stale walk that named nothing.
    ``fresh_only_unedited`` is the result that counts: test files the fresh
    walk names, the stale walk lacks, and the change did not edit.
    """
    fresh_set = frozenset(fresh)
    row: dict[str, object] = {
        "at": at,
        "head": head,
        "changed_source": sorted(changed_source),
        "fresh": sorted(fresh_set),
        "graph": graph,
        "why_no_comparison": why_no_comparison,
        "fresh_seconds": fresh_seconds,
        "stale_seconds": stale_seconds,
        "stale": None,
        "fresh_only": None,
        "fresh_only_unedited": None,
        "stale_only": None,
    }
    if stale is not None:
        stale_set = frozenset(stale)
        fresh_only = fresh_set - stale_set
        row["stale"] = sorted(stale_set)
        row["fresh_only"] = sorted(fresh_only)
        row["fresh_only_unedited"] = sorted(fresh_only - frozenset(edited_tests))
        row["stale_only"] = sorted(stale_set - fresh_set)
    return row


def append_stale_graph_observation(log_path: Path, row: dict[str, object]) -> None:
    """Append one :func:`stale_graph_row`. Never rewrites earlier lines."""
    _append(log_path, row)


@dataclass(frozen=True)
class StaleGraphEvidence:
    """What the stale-graph log says so far."""

    observations: int
    compared: int
    commits: int
    #: (commit, test file) for every unedited test file a stale walk lacked.
    lost: tuple[tuple[str, str], ...]

    @property
    def verdict(self) -> str:
        """``disqualified`` / ``insufficient`` / ``met``, as for the coverage
        shadow: one lost test file is not offset by any number of agreeing runs."""
        if self.lost:
            return "disqualified"
        if self.commits < STALE_GRAPH_MIN_COMMITS:
            return "insufficient"
        return "met"

    def summary(self) -> str:
        lines = [
            f"  observations        : {self.observations}",
            f"  compared            : {self.compared}",
            f"  distinct commits    : {self.commits} / {STALE_GRAPH_MIN_COMMITS}",
            f"  TEST FILES LOST     : {len(self.lost)}",
            f"  verdict             : {self.verdict.upper()}",
        ]
        for head, test in self.lost[:5]:
            lines.append(f"      {head[:12]}  {test}")
        return "\n".join(lines)


def evaluate_stale_graph(observations: Iterable[dict[str, object]]) -> StaleGraphEvidence:
    """Aggregate the stale-graph log.

    Only rows that hold a comparison count, and they count once per commit: a
    walk repeated on one commit is one piece of evidence, not several.
    """
    rows = list(observations)
    compared = 0
    commits: set[object] = set()
    lost: list[tuple[str, str]] = []
    for row in rows:
        missing = row.get("fresh_only_unedited")
        if not isinstance(missing, list):
            continue
        compared += 1
        if row.get("head"):
            commits.add(row["head"])
        lost.extend((str(row.get("head", "?")), str(t)) for t in missing)
    return StaleGraphEvidence(
        observations=len(rows), compared=compared, commits=len(commits),
        lost=tuple(lost),
    )


def store_snapshot(snapshot_dir: Path, patch: bytes) -> str:
    """Store ``patch`` gzip-compressed under its own SHA-256 and return that
    digest. The same change stored twice is one file."""
    digest = hashlib.sha256(patch).hexdigest()
    snapshot_dir = Path(snapshot_dir)
    target = snapshot_dir / f"{digest}.patch.gz"
    if not target.exists():
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(target, "wb") as fh:
            fh.write(patch)
    return digest


def append_run_record(
    log_path: Path,
    *,
    at: str,
    kind: str,
    head: str,
    base: str,
    changed_files: Iterable[str],
    snapshot: Optional[str],
    ran_files: Optional[Iterable[str]],
    tests_ran: Optional[int],
    failed: Optional[Iterable[str]],
    exit_code: Optional[int],
    by_statement: Optional[Mapping[str, object]] = None,
) -> None:
    """Append what one test run was and how it ended.

    ``kind`` is how the tests were chosen (``selected`` by smart-test, or
    ``explicit`` paths from the caller). ``snapshot`` is the
    :func:`store_snapshot` digest of the uncommitted change, ``None`` when the
    tree was exactly ``head``. ``ran_files``, ``tests_ran`` and ``failed`` are
    ``None`` when the run left no junit report: "did not look" must stay
    distinguishable from "nothing ran" and "nothing failed".

    ``by_statement`` is what the by-statement selector would have selected
    for this change, as the index stood BEFORE this run was folded into it,
    and which of the failed tests it would have left out
    (``coverage-select run-record`` says what the keys hold). ``None`` when it
    was not asked: no changed package source, or no index.
    """
    _append(log_path, {
        "at": at,
        "kind": kind,
        "head": head,
        "base": base,
        "changed_files": sorted(changed_files),
        "snapshot": snapshot,
        "ran_files": None if ran_files is None else sorted(ran_files),
        "tests_ran": tests_ran,
        "failed": None if failed is None else sorted(failed),
        "exit": exit_code,
        "by_statement": None if by_statement is None else dict(by_statement),
    })
