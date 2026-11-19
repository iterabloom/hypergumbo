# SPDX-License-Identifier: AGPL-3.0-or-later
"""The test budget, as a REPORT: what a cut could save and what it would cost.

WHAT IT IS FOR. PRs run every selected test (owner ruling on decision 9).
Whether a budget may later cut the slowest of them is the owner's call, and
two of its rulings (the coverage claim a PR makes, decision 10, and the spot
check, decision 11) are not made. This module computes the numbers such a cut
would be judged on, and acts on none of them (WI-tunoz). The CLI is
``scripts/coverage-select budget-report``; it reads one commit's test
manifest, so it reports a PR exactly as its CI saw it, merged or not.

* THE RANKING. The tests of the run that ran a changed statement, cheapest
  covering test first: a greedy cover of the changed statements, each step
  the test that covers the most still-uncovered statements per second (ties:
  the cheaper, then the id). Its curve (tests, seconds, statements covered)
  is what any time budget would be read against. Seconds are the mean of the
  timing store's runs; a test with no timing is priced at the median of those
  with one and counted, so a curve built on guesses says how many.

* THE AT-RISK STATEMENTS. A statement of the shipped source is at risk when
  every test the index records running it passes through the change (ran a
  changed statement): cut those tests and nothing measures it. Computed per
  block from the statement records, as bitmaps: the statements tests through
  the change ran, minus those any other test ran. A pair with no statement
  record counts as running nothing, so the at-risk set is never smaller than
  the records support. Only blocks of files the caller counts are counted
  (the CLI: ``packages/*/src`` of this checkout, the measured figure's own
  scope).

* THE INFERRED WHOLE-TREE COVERAGE. The last full run's measured figure (the
  published verdict: statements, missing), minus the at-risk statements that
  this run did not re-run (no test of the run ran them). With every selected
  test run, a statement drops out only when the tests that measure it were
  left out of the run (narrowing, the by-statement selector). Printed beside
  the measured figure; where they differ, the not-re-run statements say why.
  Beside it, the figure a CUT would leave: had only the ranking's tests run,
  the at-risk statements none of them runs (a cut that keeps the ranking
  keeps every changed statement covered, and still loses these).

WHAT IT DOES NOT SAY. Inferred coverage is not "the tests pass": it says
nothing of a test that runs a changed statement and would be cut. The
ranking's evidence does not transfer from one-function changes to PRs
(16 of 37 replayed PRs need 109 test-seconds or more on changed statements
alone). The statement records are as old as each test's last folded run. And
the index numbers statements as ``block_hash`` does (one per statement of a
block), while coverage.py counts lines: the two counts are close, not equal,
so the inferred figure is the measured one less a count in a slightly
different unit. A changed block whose base version the index no longer holds
(neither its stored digest nor a kept one) has no records to rank: the report
counts those blocks rather than guessing.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping, Optional

#: A changed statement: (absolute path, block name, ordinal at the base).
Statement = tuple[str, str, int]


def load_timings(path: Path) -> dict[str, float]:
    """Node id -> mean seconds over its recorded runs. Missing or unreadable
    file: no timings."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, float] = {}
    for key, info in data.items():
        runs = [float(r.get("seconds", 0.0)) for r in (info or {}).get("runs", [])]
        if runs:
            out[key] = sum(runs) / len(runs)
    return out


@dataclass(frozen=True)
class Step:
    test: str
    seconds: float
    covered: int
    cumulative_seconds: float
    cumulative_covered: int


@dataclass(frozen=True)
class Ranking:
    steps: tuple[Step, ...]
    statements: int
    #: Tests priced at the median for want of a timing.
    unpriced: int


def rank_cheapest_cover(covers: Mapping[str, frozenset[Statement]],
                        seconds: Mapping[str, float]) -> Ranking:
    """Greedy cover of every statement in ``covers`` (test -> the changed
    statements it ran), most new statements per second first."""
    known = [seconds[t] for t in covers if t in seconds]
    fallback = statistics.median(known) if known else 1.0
    price = {t: seconds.get(t, fallback) for t in covers}
    left: set[Statement] = set().union(*covers.values()) if covers else set()
    total = len(left)
    pending = {t: set(s) for t, s in covers.items() if s}
    steps: list[Step] = []
    spent, done = 0.0, 0
    # Every statement still in ``left`` belongs to a test still pending (a
    # picked test takes all of its statements out), so the pick gains >= 1.
    while left:
        best = min(pending, key=lambda t: (-len(pending[t] & left) / max(price[t], 1e-3),
                                           price[t], t))
        gain = len(pending.pop(best) & left)
        left -= covers[best]
        spent += price[best]
        done += gain
        steps.append(Step(best, price[best], gain, spent, done))
    return Ranking(tuple(steps), total, sum(1 for t in covers if t not in seconds))


@dataclass
class AtRisk:
    """At-risk statements, and those of them no test of the run ran."""

    statements: int = 0
    not_rerun: int = 0
    by_file: dict[str, tuple[int, int]] = field(default_factory=dict)


def _int(bitmap: Optional[bytes]) -> int:
    return int.from_bytes(bitmap, "little") if bitmap else 0


def at_risk(conn: sqlite3.Connection, through: Iterable[str], ran: Iterable[str],
            counts: Callable[[str], bool] = lambda path: True) -> AtRisk:
    """Per block of a counted file reached by a test in ``through``: the
    statements only tests in ``through`` ran (at risk), and of those, the ones
    no test in ``ran`` ran (not re-run). Test ids as the index spells them."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS budget_through (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM temp.budget_through")
    conn.executemany("INSERT OR IGNORE INTO temp.budget_through (id) SELECT id FROM test "
                     "WHERE test_id = ?", ((t,) for t in set(through)))
    ran_ids = {row[0] for t in set(ran)
               for row in conn.execute("SELECT id FROM test WHERE test_id = ?", (t,))}
    through_ids = {row[0] for row in conn.execute("SELECT id FROM temp.budget_through")}
    rows = conn.execute(
        "SELECT f.path, tb.block, tb.test, tb.statements FROM test_block tb "
        "JOIN block b ON b.id = tb.block JOIN file f ON f.id = b.file "
        "WHERE tb.block IN (SELECT DISTINCT block FROM test_block "
        "WHERE test IN (SELECT id FROM temp.budget_through)) ORDER BY tb.block")
    result = AtRisk()
    current: Optional[int] = None
    path = ""
    inside = outside = rerun = 0

    def close() -> None:
        risky = inside & ~outside
        if risky and counts(path):
            missed = risky & ~rerun
            n, m = bin(risky).count("1"), bin(missed).count("1")
            result.statements += n
            result.not_rerun += m
            a, b = result.by_file.get(path, (0, 0))
            result.by_file[path] = (a + n, b + m)

    for file_path, block, test, bitmap in rows:
        if block != current:
            if current is not None:
                close()
            current, path, inside, outside, rerun = block, str(file_path), 0, 0, 0
        bits = _int(bitmap)
        if test in through_ids:
            inside |= bits
        else:
            outside |= bits
        if test in ran_ids:
            rerun |= bits
    if current is not None:
        close()
    return result


def inferred_coverage(statements: int, missing: int, not_rerun: int) -> Optional[float]:
    """The measured figure minus the statements not re-run, in percent."""
    if statements <= 0:
        return None
    return 100.0 * (statements - missing - not_rerun) / statements


@dataclass(frozen=True)
class Changed:
    """Test -> the changed statements it ran, and the changed blocks whose
    base version the index holds no record of."""

    covers: Mapping[str, frozenset[Statement]]
    unrecorded: int


def changed_statements(conn: sqlite3.Connection,
                       versions: Mapping[str, tuple[Optional[str], Optional[str]]]) -> Changed:
    """For each file (absolute path, as the index spells it -> (base text,
    head text); None = absent): the changed statements, numbered as the base
    version numbers them, that each test ran, for every changed block whose
    base version the index holds. New blocks and import-time changes have no
    test to rank (nothing recorded ran them); a file that does not parse on
    either side is skipped."""
    from .selection_index import statement_records_at
    from .statement_diff import Kind, statement_changes

    covers: dict[str, set[Statement]] = {}
    unrecorded = 0
    for path, (base, head) in versions.items():
        try:
            changes = statement_changes(path, base, head)
        except SyntaxError:
            continue
        for name, change in changes.items():
            if change.kind in (Kind.PROSE, Kind.NEW, Kind.NEW_METHOD) or change.import_time:
                continue
            records = statement_records_at(conn, path, name, change.old_digest)
            if records is None:
                unrecorded += 1
                continue
            wanted = change.ran if change.kind is Kind.STATEMENTS else None
            for test, ran in records.items():
                for ordinal in ran or ():
                    if wanted is None or ordinal in wanted:
                        covers.setdefault(test, set()).add((path, name, ordinal))
    return Changed({t: frozenset(s) for t, s in covers.items()}, unrecorded)


@dataclass(frozen=True)
class Manifest:
    baseline: Optional[str]
    files: tuple[str, ...]
    deselected: frozenset[str]


def parse_manifest(text: str) -> Manifest:
    """A smart-test manifest (``.ci/affected-tests.txt``): its baseline, the
    selected test files and the ``#- <id>`` tests the by-statement selector
    deselected."""
    baseline: Optional[str] = None
    files: list[str] = []
    deselected: set[str] = set()
    selected = False
    for line in text.splitlines():
        if line.startswith("# Baseline: "):
            baseline = line.split(": ", 1)[1].strip() or None
        elif line.startswith("#- "):
            deselected.add(line[3:].strip())
        elif line.startswith("# === "):
            selected = line.strip() == "# === SELECTED_TESTS ==="
        elif selected and line.strip() and not line.startswith("#"):
            files.append(line.strip())
    return Manifest(baseline, tuple(files), frozenset(deselected))


def tests_of_files(conn: sqlite3.Connection, files: Iterable[str]) -> frozenset[str]:
    """The index's tests in the given test files (node id = ``file::...``)."""
    wanted = set(files)
    return frozenset(t for (t,) in conn.execute("SELECT test_id FROM test")
                     if t.split("::", 1)[0] in wanted)


def relative(path: str, root: Path) -> str:
    prefix = f"{root}/"
    return path[len(prefix):] if path.startswith(prefix) else path


def is_package_source(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return len(parts) > 3 and parts[0] == "packages" and parts[2] == "src" and rel.endswith(".py")


def _by_file(risk: AtRisk, root: Path) -> list[str]:
    """The files with statements not re-run, most first (ten at most)."""
    return [f"  {m:5} not re-run of {n:5}  {relative(path, root)}"
            for path, (n, m) in sorted(risk.by_file.items(), key=lambda kv: -kv[1][1])[:10]
            if m]


def render(ranking: Ranking, risk: AtRisk, measured: Optional[Mapping[str, object]], root: Path,
           selected: int, selected_seconds: float, ran: int, unrecorded: int = 0,
           cut: Optional[AtRisk] = None) -> str:
    out = [f"selected: {selected} indexed test(s), {selected_seconds:.1f} test-seconds by the "
           f"timing store; ran: {ran}"]
    if ranking.statements:
        out.append(f"ranking: {len(ranking.steps)} test(s) cover the {ranking.statements} changed "
                   f"statement(s) the tests of the run ran"
                   + (f"; {ranking.unpriced} priced at the median for want of a timing"
                      if ranking.unpriced else ""))
        for step in ranking.steps[:10]:
            out.append(f"  {step.cumulative_seconds:8.2f} s  {step.cumulative_covered:5}/"
                       f"{ranking.statements}  +{step.covered:<4} {step.test} ({step.seconds:.2f} s)")
        if len(ranking.steps) > 10:
            out.append(f"  ... {len(ranking.steps) - 10} more")
    else:
        out.append("ranking: no changed statement any test of the run ran")
    if unrecorded:
        out.append(f"  {unrecorded} changed block(s) not ranked: the index holds no record of "
                   f"their base version")
    out.append(f"at risk: {risk.statements} statement(s) only tests through the change run; "
               f"{risk.not_rerun} of them not re-run by this run")
    out.extend(_by_file(risk, root))
    stmts = int(str(measured.get("statements") or 0)) if measured else 0
    if measured and stmts:
        missing = int(str(measured.get("missing") or 0))
        inferred = inferred_coverage(stmts, missing, risk.not_rerun)
        out.append(f"coverage: measured {100.0 * (stmts - missing) / stmts:.2f}% "
                   f"({stmts - missing}/{stmts}) at the last full run; inferred after this "
                   f"change {inferred:.2f}% ({stmts - missing - risk.not_rerun}/{stmts})"
                   + ("; the difference is the statements not re-run above"
                      if risk.not_rerun else ""))
        if cut is not None and ranking.steps:
            out.append(f"a cut to the ranking's {len(ranking.steps)} test(s) "
                       f"({ranking.steps[-1].cumulative_seconds:.2f} s): {cut.not_rerun} at-risk "
                       f"statement(s) not re-run; inferred "
                       f"{inferred_coverage(stmts, missing, cut.not_rerun):.2f}% "
                       f"({stmts - missing - cut.not_rerun}/{stmts})")
            out.extend(_by_file(cut, root))
    else:
        out.append("coverage: no published full-run figure to infer from")
    return "\n".join(out)
