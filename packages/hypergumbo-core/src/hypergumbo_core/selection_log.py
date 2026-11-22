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


#: Owner ruling, 2026-11-17: the by-statement selector may select once this
#: many runs had a failed test it could have left out, and it left none out.
STATEMENT_BAR = 30

#: The selector's own code, and the code that writes a row about it. A run
#: whose change touches one of these was answered by a selector other than
#: the merged one (a half-written one, or a mutation control of this very
#: code), so its row is no evidence about the merged one. The first row ever
#: to show a failed test left out was such a row: written two minutes before
#: the commit that defined the field, by a formula that commit does not have.
SELECTOR_FILES = frozenset({
    "packages/hypergumbo-core/src/hypergumbo_core/statement_select.py",
    "packages/hypergumbo-core/src/hypergumbo_core/statement_diff.py",
    "packages/hypergumbo-core/src/hypergumbo_core/selection_index.py",
    "packages/hypergumbo-core/src/hypergumbo_core/selection_log.py",
    "packages/hypergumbo-core/src/hypergumbo_core/selection_shadow.py",
    "packages/hypergumbo-core/src/hypergumbo_core/block_hash.py",
    "scripts/coverage-select",
})


@dataclass(frozen=True)
class StatementBar:
    """What the run records say about the by-statement selector so far."""

    rows: int
    #: Rows with no answer to count, and why (the reason -> how many).
    uncounted: Mapping[str, int]
    #: Rows that hold the selector's answer and the run's failed tests.
    recorded: int
    #: Runs, by run kind, in which a FAILED test reached a function the
    #: selector answered by statement and the selector had a record of every
    #: changed function. Counted once per state of the tree (base, head,
    #: uncommitted change): the same state run twice is one piece of evidence.
    informative: Mapping[str, int]
    #: The informative runs in which the selector also LEFT TESTS OUT (it
    #: selected fewer tests than reached the changed functions). Only these
    #: test it: where it selects every test that reached the function there is
    #: nothing it could have missed. In the first planted-fault campaign 25
    #: runs were informative and 9 of them were of this kind. THE BAR IS
    #: COUNTED ON THESE.
    left_out: Mapping[str, int]
    #: ``(when, run kind, head, the failed tests left out)`` for every row in
    #: which the selector left out a failed test that reached a changed function.
    misses: tuple[tuple[str, str, str, tuple[str, ...]], ...]
    #: ``(failed tests, rows)`` set aside because the test's own FILE is one
    #: the change edits (:func:`evaluate_statement_bar` says why).
    edited: tuple[int, int] = (0, 0)

    @property
    def verdict(self) -> str:
        """``missed`` / ``not met`` / ``met``. One miss is not offset by any
        number of clean runs (owner ruling: it stays off and is filed)."""
        if self.misses:
            return "missed"
        if sum(self.left_out.values()) < STATEMENT_BAR:
            return "not met"
        return "met"

    def summary(self) -> str:
        lines = [
            f"  run records               : {self.rows}",
            f"  hold the selector's answer : {self.recorded}",
        ]
        lines += [f"      not counted, {why}: {n}" for why, n in sorted(self.uncounted.items())]
        lines.append(f"  a failed test was at risk : {sum(self.informative.values())}")
        lines += [f"      {kind}: {n}" for kind, n in sorted(self.informative.items())]
        lines.append(f"  AND THE SELECTOR LEFT TESTS OUT : "
                     f"{sum(self.left_out.values())} / {STATEMENT_BAR}")
        lines += [f"      {kind}: {n}" for kind, n in sorted(self.left_out.items())]
        lines.append(f"  RUNS WITH A FAILED TEST LEFT OUT : {len(self.misses)}")
        for at, kind, head, tests in self.misses[:5]:
            lines.append(f"      {at} {kind} {head[:12]}  {', '.join(tests[:3])}")
        if self.edited[0]:
            lines.append(f"  failed tests of a changed test file, set aside : "
                         f"{self.edited[0]} in {self.edited[1]} run(s)")
        lines.append(f"  verdict                   : {self.verdict.upper()}")
        lines.append("  not seen by this count    : a failed test the index does not pair with "
                     "the changed function (shared work: a cache, a shared fixture, a subprocess)")
        return "\n".join(lines)


def evaluate_statement_bar(records: Iterable[Mapping[str, object]]) -> StatementBar:
    """Count the by-statement selector's bar from the run records.

    WHAT THIS COUNT CANNOT SEE. A failed test the index does not pair with
    the changed function at all is not in ``failed_not_selected``: work done
    once and shared (a memoised function, a module-scoped fixture, a
    subprocess) leaves most of its dependents unpaired, and a planted-fault
    campaign found 13 such failing tests in 3 of 93 runs while this count
    read "met". A clean count here is necessary for selecting test by test,
    and it is not sufficient.

    A row written before the selector recorded which tests it had narrowed
    (no ``failed_reaching_a_narrowed_block``) cannot be informative: it does
    not say whether the selector narrowed anything. It can still be a miss,
    and a miss in any row counts, except a row whose change was to the
    selector's own code (:data:`SELECTOR_FILES`): that row was not written by
    the selector being judged.

    A FAILED TEST WHOSE OWN FILE THE CHANGE EDITS IS SET ASIDE, in both
    counts. The row's ``failed_not_selected`` is the selector's answer alone.
    What acts on that answer (:mod:`statement_narrow`) never deselects a test
    of a changed test file: the caller runs such a file whole, because the
    index's record of the test is of the test as it was before the edit. So
    the test could not have been left out of a run, and its failure says
    nothing about the selector either way (a test edited to assert new
    behaviour fails until the source follows, whatever statements the old
    test ran). The one row that ever read ``missed`` on this repository's
    records (2026-11-19) was that. ``edited`` counts what was set aside, and
    the summary prints it, so the exclusion is never silent.
    """
    rows = list(records)
    uncounted: dict[str, int] = {}
    informative: dict[str, set[tuple[object, object, object]]] = {}
    narrowed: dict[str, set[tuple[object, object, object]]] = {}
    misses: list[tuple[str, str, str, tuple[str, ...]]] = []
    recorded = 0
    edited_tests = edited_rows = 0

    def skip(why: str) -> None:
        uncounted[why] = uncounted.get(why, 0) + 1

    for row in rows:
        facts = row.get("by_statement")
        if not isinstance(facts, Mapping):
            skip("the selector was not asked")
            continue
        if "error" in facts:
            skip("the selector failed")
            continue
        changed = row.get("changed_files")
        if isinstance(changed, list) and SELECTOR_FILES.intersection(changed):
            skip("the change was to the selector itself")
            continue
        if row.get("failed") is None:
            skip("the run left no junit report")
            continue
        recorded += 1
        kind = str(row.get("kind"))
        edited_files = frozenset(changed) if isinstance(changed, list) else frozenset()
        left_out, aside_left = _not_of(facts.get("failed_not_selected"), edited_files)
        if left_out:
            misses.append((str(row.get("at")), kind, str(row.get("head")), tuple(left_out)))
        named = facts.get("failed_reaching_a_narrowed_block")
        at_risk, aside_risk = _not_of(named, edited_files)
        aside = aside_left | aside_risk
        if aside:
            edited_tests += len(aside)
            edited_rows += 1
        if named is None:
            skip("written before narrowed tests were recorded")
        elif at_risk and not facts.get("unknown_blocks"):
            state = (row.get("base"), row.get("head"), row.get("snapshot"))
            informative.setdefault(kind, set()).add(state)
            selected, reached = facts.get("tests"), facts.get("by_block")
            if isinstance(selected, int) and isinstance(reached, int) and selected < reached:
                narrowed.setdefault(kind, set()).add(state)
    return StatementBar(
        rows=len(rows), uncounted=uncounted, recorded=recorded,
        informative={kind: len(states) for kind, states in informative.items()},
        left_out={kind: len(states) for kind, states in narrowed.items()},
        misses=tuple(misses), edited=(edited_tests, edited_rows))


def _not_of(tests: object, files: frozenset[object]) -> tuple[list[str], frozenset[str]]:
    """``tests`` (a row's list of node ids, or anything else: none) split into
    those whose file is not one of ``files`` and those whose file is."""
    ids = [str(test) for test in tests] if isinstance(tests, list) else []
    aside = frozenset(test for test in ids if test.split("::")[0] in files)
    return [test for test in ids if test not in aside], aside
