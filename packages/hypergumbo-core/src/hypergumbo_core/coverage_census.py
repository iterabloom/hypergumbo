# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-test coverage census and test-trajectory search.

WHAT THIS ANSWERS. The full suite costs ~17 wall-clock minutes and ~90 minutes
of serial work on six cores, and nobody knows how much of that work is
REDUNDANT. Aggregate coverage says the suite reaches 100%; it says nothing
about who contributed what. This module builds the inverted index
(``line -> tests that execute it``) from a context-enabled coverage run and
searches for cheap test subsets that still reach a coverage target.

THE COST MODEL IS THE WHOLE BALL GAME, so it is stated here rather than buried.
Per-test durations are NOT independent, and taking them at face value produces
a trajectory that optimises a cost function which does not exist. Measured on
this repo: ``test_route_detected[annotation]`` is recorded at 286s and its
fifteen sibling cases at 0.0s — because a ``scope="module"`` fixture runs the
full pipeline over seventeen fixture directories and pytest bills the whole
setup to whichever test happened to trigger it. Across runs the same test
records ``[0.0, 0.0, 129.0, 358.0]``; a test that takes 0.0s on one run and
358s on the next is not a slow test.

A naive model reads that as fifteen infinitely-efficient tests and one terrible
one, drops the expensive one, keeps the cheap ones — and then pays the 286s
anyway, because the fixture is exactly what the cheap ones depend on.

So cost is split in two, using pytest's own ``--durations`` phase breakdown:

    marginal(test)  = call + teardown + per_test_setup
    fixed(group)    = one-time setup a group pays once, if ANY of its tests run

where ``per_test_setup`` is the MEDIAN setup within the group (the recurring
part every test pays) and ``fixed`` is the excess above it (the one-time build).
A trajectory's cost is then::

    sum(marginal(t) for t in chosen) + sum(fixed(g) for g in groups_touched)

GROUPING IS BY MODULE, WHICH IS AN APPROXIMATION AND HAS A NAMED FAILURE MODE.
Module-scoped fixtures are the measured problem here and module grouping
captures them exactly. A SESSION-scoped fixture shared across modules is
attributed to whichever module paid it first, so its cost is
under-counted for the second module onward. Function-scoped setup is recurring
and lands in the median, which is correct. Recorded rather than silently
assumed: if session-scoped heavy fixtures appear later, this model needs a
fixture-level grouping key, which pytest does not expose in ``--durations``.

WHY GREEDY, AND WHY IT IS PRINCIPLED RATHER THAN MERELY INTUITIVE. "Lines
covered by a set of tests" is monotone submodular — adding a test never reduces
coverage, and a test's marginal gain shrinks as others are chosen. That is the
condition under which the cost-weighted greedy has a guarantee: within
``ln(n)`` of optimal for full cover, ``(1 - 1/e)`` for max-coverage-under-budget.
Set cover is NP-hard, so this is close to the best available.

Submodularity also makes it TRACTABLE. Recomputing all ~22,700 marginal gains
at each of ~22,700 steps is ~500M set operations. Because gains only ever
DECREASE, a stale gain is an upper bound, so the lazy-greedy (CELF) variant
holds a priority queue of stale gains, pops the top, recomputes only that one,
and accepts it when it still beats the next stale entry. In practice this is
near-linear.

GREEDY IS NOT OPTIMAL, WHICH IS WHY RANDOMISED TRAJECTORIES ARE NOT MERELY A
ROTATION TRICK. A perturbed run can beat the deterministic one — randomised
restart is standard practice for exactly this reason — so ``epsilon`` sampling
serves two purposes: diversity of the residual (for a rotating coverage
target) and a shot at a better cover than greedy finds alone.

WHAT THE INDEX CANNOT TELL YOU, stated because acting on it would be the
expensive mistake. It records EXECUTION, not ASSERTION. A line run by ten tests
that assert nothing about it reads as covered ten times and protected zero. A
test with zero unique lines is removable without moving the coverage gate,
which is strictly weaker than removable safely.

AND IT CANNOT SEE EVERY TEST. The population comes from the coverage rows, so a
test that produced none is ABSENT rather than zero — 3,614 of them here, 15.9%
of the suite. Left implicit they read as "not needed"; see
:func:`load_index_from_coverage` for the four causes and
:class:`RedundancyReport` for the denominator that keeps them out of the
redundancy claim. In this repo the two near-misses
of the week both passed every unit test and were caught only end-to-end, so a
cost-ranked skip list would preferentially drop precisely the tests that catch
its characteristic bugs. Output here is a shortlist for human review, never a
delete list.
"""
from __future__ import annotations

import heapq
import random
import re
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

#: ``12.34s setup    path/to/test.py::Class::test[param]``
_DURATION_LINE = re.compile(
    r"^(?P<seconds>[0-9]+\.[0-9]+)s\s+(?P<phase>setup|call|teardown)\s+(?P<test>\S.*)$"
)

PHASES = ("setup", "call", "teardown")


@dataclass(frozen=True)
class PhaseDuration:
    """Phase-split duration for one test, before fixture attribution."""

    test_id: str
    setup: float = 0.0
    call: float = 0.0
    teardown: float = 0.0

    @property
    def total(self) -> float:
        return self.setup + self.call + self.teardown

    @property
    def group(self) -> str:
        """The module the test belongs to — the fixture-cost grouping key."""
        return self.test_id.split("::", 1)[0]


def parse_durations(lines: Iterable[str]) -> dict[str, PhaseDuration]:
    """Parse a pytest ``--durations=0 --durations-min=0`` report.

    Only the three phase lines are recognised; every other line in the report
    (headers, the summary, progress dots) is ignored rather than rejected, so
    this can be handed a whole captured run log.
    """
    acc: dict[str, dict[str, float]] = {}
    for line in lines:
        m = _DURATION_LINE.match(line.strip())
        if m is None:
            continue
        acc.setdefault(m["test"], {})[m["phase"]] = float(m["seconds"])
    return {
        test: PhaseDuration(
            test_id=test,
            setup=phases.get("setup", 0.0),
            call=phases.get("call", 0.0),
            teardown=phases.get("teardown", 0.0),
        )
        for test, phases in acc.items()
    }


@dataclass
class CostModel:
    """Fixture-aware costs: what a trajectory actually pays.

    See the module docstring for why per-test totals are not usable directly.
    """

    marginal: dict[str, float] = field(default_factory=dict)
    fixed: dict[str, float] = field(default_factory=dict)
    group_of: dict[str, str] = field(default_factory=dict)

    def cost_of(self, chosen: Iterable[str]) -> float:
        """Total seconds for a trajectory, charging each group's fixed cost once."""
        chosen = list(chosen)
        groups = {self.group_of[t] for t in chosen if t in self.group_of}
        return (
            sum(self.marginal.get(t, 0.0) for t in chosen)
            + sum(self.fixed.get(g, 0.0) for g in groups)
        )


def build_cost_model(costs: Iterable[PhaseDuration]) -> CostModel:
    """Split recurring per-test setup from one-time per-group fixture setup.

    The median setup within a group is the part every test in it pays; the
    excess above the median, taken at the maximum, is the one-time build. A
    group whose setups are uniform therefore has ``fixed == 0`` and behaves
    exactly like independent per-test costs, which is the common case and must
    not be distorted by this machinery.
    """
    by_group: dict[str, list[PhaseDuration]] = {}
    for c in costs:
        by_group.setdefault(c.group, []).append(c)

    model = CostModel()
    for group, members in by_group.items():
        setups = [m.setup for m in members]
        recurring = statistics.median(setups)
        model.fixed[group] = max(0.0, max(setups) - recurring)
        for m in members:
            model.group_of[m.test_id] = group
            model.marginal[m.test_id] = m.call + m.teardown + recurring
    return model


@dataclass
class CoverageIndex:
    """``line -> tests`` and ``test -> lines``, as dense bitsets.

    Lines are interned to consecutive integers so a test's coverage is one
    Python ``int`` used as a bitset: union is ``|``, size is ``bit_count()``,
    both implemented in C over machine words. At ~63K lines that is ~8 KB per
    test, which keeps a full 22K-test index in a few hundred MB.
    """

    line_ids: dict[tuple[str, int], int] = field(default_factory=dict)
    lines_of: dict[str, int] = field(default_factory=dict)

    #: Import-time statements in files NO test executes a line in, so no test
    #: can be credited with them (see :func:`load_index_from_coverage`). Held
    #: here rather than dropped because they are part of the coverage GATE's
    #: denominator while being outside anything the trajectory search can
    #: select — a caveat on every subset this module proposes, not a detail.
    #: Measured on this repo: 25 lines over 7 files, 6 of them package
    #: ``__init__.py``.
    unattributed_lines: dict[str, frozenset[int]] = field(default_factory=dict)

    #: Tests KNOWN to exist that the coverage data has no row for at all — see
    #: :func:`load_index_from_coverage`. Empty means "not asked", NOT "none":
    #: the database alone cannot reveal a test that left no trace, so this is
    #: only populated when a test list is supplied.
    unmeasured_tests: frozenset[str] = frozenset()

    @property
    def total_lines(self) -> int:
        return len(self.line_ids)

    def _intern(self, path: str, lineno: int) -> int:
        key = (path, lineno)
        got = self.line_ids.get(key)
        if got is None:
            got = len(self.line_ids)
            self.line_ids[key] = got
        return got

    def add(self, test_id: str, path: str, linenos: Iterable[int]) -> None:
        mask = self.lines_of.get(test_id, 0)
        for lineno in linenos:
            mask |= 1 << self._intern(path, lineno)
        self.lines_of[test_id] = mask

    def universe(self) -> int:
        """Bitset of every line any test covered."""
        u = 0
        for mask in self.lines_of.values():
            u |= mask
        return u

    def unique_lines(self) -> dict[str, int]:
        """Per test, the count of lines NO other test executes.

        A test with a non-zero count is the sole executor of something, which
        is the cheapest available guard against dropping the only test that
        reaches a code path. It is NOT evidence that the test asserts anything
        about that path — see the module docstring.
        """
        seen_once = 0
        seen_more = 0
        for mask in self.lines_of.values():
            seen_more |= seen_once & mask
            seen_once |= mask
        exclusive = seen_once & ~seen_more
        return {t: (mask & exclusive).bit_count()
                for t, mask in self.lines_of.items()}


def strip_phase(context: str) -> str:
    """``…::test_x|run`` -> ``…::test_x``.

    pytest-cov records a SEPARATE context per test phase, suffixed ``|setup``,
    ``|run`` or ``|teardown``. Measured on a full run of this repo: 19,103
    ``|run``, 1,210 ``|setup``, 124 ``|teardown``. Two consequences, and the
    first is a defect this function exists to prevent:

    * indexing the raw string treats ``t|setup`` and ``t|run`` as two
      different tests, so a test's coverage is split across phantom entries
      and every unique-line count is wrong;
    * nothing else in the toolchain uses that spelling — pytest's
      ``--durations`` report and ``test_timings.json`` both key on the bare
      node id — so a raw join silently matches NOTHING. Measured before this
      existed: 0 of 20,437 contexts joined; 19,129 of 19,129 after.

    The empty-context case (``''``, meaning contexts were never collected) is
    the caller's to reject; it is not a phase and is returned unchanged.
    """
    return context.rsplit("|", 1)[0] if "|" in context else context


def canonical_lines(db_path: Path) -> dict[str, dict[int, int]]:
    """Per measured file, ``traced line -> the STATEMENT line it scores as``.

    TWO CORRECTIONS LIVE HERE. The census got the first right and the second
    wrong, and the second silently ate coverage.

    (1) ``line_bits`` stores the raw line numbers the tracer saw, which is a
    SUPERSET of the statements coverage reports on: continuation lines, some
    ``else:`` clauses and decorator lines execute without being counted.
    Measured on this repo's census DB — 97,767 distinct executed
    ``(file, line)`` pairs against a reported statement total of 63,603. Left
    unfiltered the denominator is ~23% larger than the coverage GATE's, so "95%
    covered" here would mean something different from "95% covered" in CI.

    (2) But FILTERING ALONE IS THE WRONG REMEDY, which is what the census
    originally did. A multi-line statement is traced on its INNER lines and
    attributed by the parser back to the statement's first line, so intersecting
    raw lines with the statement set discards the coverage of every one of them.
    Measured: 144 statements across 53 files that coverage reports COVERED were
    absent from the index — py.py 17, go.py 15, java.py 12, concentrated in the
    largest analyzers because that is where the multi-line conditions are. One
    instance, ``py.py:1218``::

        if (            <- the statement coverage scores
            x > 0       <- the lines the tracer reports
            and x < 10
        ):

    Translating FIRST and filtering SECOND closes the gap to zero.

    The statement set comes from ``analysis2`` rather than ``parser.statements``
    because only the former applies the config's ``exclude_list`` — without it
    every ``# pragma: no cover`` line would re-enter the denominator as
    permanently uncoverable. The parser is used solely for its multi-line map,
    which is a physical property of the source and exclusion-independent.

    The map is materialised per file over the whole line range rather than
    calling ``first_line`` in the hot loop: it is ``lru_cache``'d at maxsize=1000
    upstream and py.py alone traces 3,166 distinct lines, so the cache would
    thrash on every file.

    THE DB IS A SNAPSHOT AND THIS READS TODAY'S SOURCE. Line numbers in
    ``line_bits`` were recorded against the tree as it was when the census ran;
    the parse here happens against the tree as it is now. Editing a measured
    file between the two silently shifts its statements. Observed while
    building this: the statement total moved 63,603 -> 63,628 across a few
    hours purely from edits to this module and its tests. A census is only
    interpretable against the tree that produced it — re-run it after any
    change to measured source, and treat a stale DB as a different question.
    """
    import coverage
    from coverage.exceptions import CoverageException
    from coverage.parser import PythonParser

    cov = coverage.Coverage(data_file=str(db_path))
    cov.load()
    out: dict[str, dict[int, int]] = {}
    for path in cov.get_data().measured_files():
        try:
            _fname, statements, _excluded, _missing, _fmt = cov.analysis2(path)
            parser = PythonParser(filename=path)
            parser.parse_source()
        # NoSource (file deleted since the run) and NotPython both derive from
        # CoverageException; a source file that no longer parses raises
        # SyntaxError. Narrow rather than bare, so a genuine bug in the loader
        # surfaces instead of silently dropping a file from the denominator.
        except (CoverageException, SyntaxError):  # pragma: no cover - removed/unparsable source
            continue
        keep = set(statements)
        n_lines = len(parser.text.splitlines())
        canon = {}
        for raw in range(1, n_lines + 1):
            first = parser.first_line(raw)
            if first in keep:
                canon[raw] = first
        out[path] = canon
    return out


def load_index_from_coverage(
    db_path: Path, known_tests: Optional[Iterable[str]] = None,
) -> CoverageIndex:
    """Read a context-enabled coverage database into the inverted index.

    Reads the ``line_bits`` schema directly rather than through
    ``CoverageData.contexts_by_lineno``: that API is per-file and would reopen
    and re-query for each of several hundred files, where one join over the
    whole database is a single pass. The ``numbits`` blob is a little-endian
    bitmap of line numbers, which is exactly the representation this index
    wants, so it is reinterpreted rather than expanded into a list.

    Phase contexts are FOLDED into their test (see :func:`strip_phase`), so a
    test's coverage is the union of what its setup, body and teardown executed
    — which is what "running this test costs you these lines" means.

    Traced lines are canonicalised to the statement they score against (see
    :func:`canonical_lines`) so this index measures the same quantity the
    coverage gate does.

    IMPORT-TIME CODE IS THE OTHER HALF, and leaving it out was a 14.5% blind
    spot. Module bodies, class definitions, decorators and constants execute
    once at collection, under NO test context — measured here, 9,192 statements,
    14.5% of the gate's denominator, attributed to the empty context and
    therefore invisible to a search that only reads test contexts. A subset
    chosen against 85.2% of the gate cannot be trusted against the gate.

    The rule that recovers them is an IMPLICATION, not an approximation: if a
    test executes any line in file F then F was necessarily imported, so F's
    import-time lines are covered whenever that test runs — under any subset.
    So each file's import-time lines are credited to every test that touches
    that file, which recovers 9,167 of the 9,192 (99.7%).

    WHAT IT DOES NOT RECOVER, and why that is not papered over: a file whose
    lines NO test ever executes has nobody to credit. Its import happens only
    because some test module imports it, which the coverage data does not
    record. Those land in :attr:`CoverageIndex.unattributed_lines` — 25 lines
    over 7 files here — rather than being silently counted as free. Attributing
    them would need a ``pytest_collectstart`` hook calling
    ``Coverage.switch_context()`` during collection, and a fresh census.

    ``known_tests`` CLOSES THE OTHER DIRECTION, and it is the one that could
    delete a real test. This index derives its population FROM the coverage
    rows, so a test that produced no rows is ABSENT rather than zero — and an
    absent test is indistinguishable, downstream, from one greedy declined to
    pick. Measured on this suite, 3,614 tests (15.9%) produce no rows at all:

        2,818  hypergumbo-tracker  -- the package is not in COV_PATHS
          435  import-time subject -- asserts on a registry or frozen
                                      dataclass built at import, so nothing
                                      executes while the test runs
          255  subprocess          -- the code runs in another process
          106  tests a scripts/ file -- outside packages/*/src

    Supplying the test list records them in
    :attr:`CoverageIndex.unmeasured_tests`, which :func:`redundancy_report`
    then reports separately and never counts as redundant. The import-time
    group is why this matters rather than being bookkeeping: those tests
    validate exactly the import-time lines credited above to OTHER tests, so
    dropping ``test_specs_are_frozen`` or
    ``test_registry_has_no_duplicate_names`` moves the coverage gate not at
    all while removing an invariant guard.
    """
    index = CoverageIndex()
    canon = canonical_lines(db_path)
    import_lines: dict[str, set[int]] = {}
    touched: dict[str, set[str]] = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT c.context, f.path, lb.numbits "
            "FROM line_bits lb "
            "JOIN context c ON c.id = lb.context_id "
            "JOIN file f ON f.id = lb.file_id "
        )
        for context, path, numbits in rows:
            per_file = canon.get(path)
            linenos = _numbits_to_linenos(numbits)
            if per_file is not None:
                linenos = (per_file[n] for n in linenos if n in per_file)
            if context == "":
                import_lines.setdefault(path, set()).update(linenos)
            else:
                test = strip_phase(context)
                index.add(test, path, linenos)
                touched.setdefault(path, set()).add(test)
    finally:
        con.close()

    for path, lines in import_lines.items():
        tests = touched.get(path)
        if not tests:
            index.unattributed_lines[path] = frozenset(lines)
            continue
        for test in tests:
            index.add(test, path, lines)

    if known_tests is not None:
        index.unmeasured_tests = frozenset(known_tests) - set(index.lines_of)
    return index


@dataclass(frozen=True)
class RedundancyReport:
    """What a trajectory does and does not license you to skip.

    THE DENOMINATOR IS THE BUG THIS TYPE EXISTS TO PREVENT. Reporting
    ``(suite - chosen) / suite`` counts every test the census could not see as
    one it did not need: on this suite that turned 76% of the OBSERVABLE tests
    into "79% of the suite is redundant", quietly absorbing 3,614 unobserved
    tests into the claim.

    ``skip_candidates`` is deliberately narrow and is still NOT a delete list.
    It excludes unmeasured tests structurally, and what remains has only been
    shown not to move the coverage GATE — the index records EXECUTION, not
    ASSERTION, so a test that runs a line while asserting nothing about it is
    indistinguishable here from the one that guards it.
    """

    measurable: int
    unmeasured: int
    chosen: int
    skip_candidates: frozenset[str]

    @property
    def coverage_redundant(self) -> int:
        return len(self.skip_candidates)

    @property
    def redundant_share(self) -> float:
        """Share of the OBSERVABLE population, never of the whole suite."""
        return self.coverage_redundant / self.measurable if self.measurable else 0.0


def redundancy_report(
    index: CoverageIndex, chosen: Iterable[str],
) -> RedundancyReport:
    """Split a trajectory's complement into observable and unobserved."""
    picked = set(chosen)
    measurable = set(index.lines_of)
    return RedundancyReport(
        measurable=len(measurable),
        unmeasured=len(index.unmeasured_tests),
        chosen=len(picked),
        skip_candidates=frozenset(measurable - picked),
    )


def _numbits_to_linenos(numbits: bytes) -> Iterator[int]:
    """Expand coverage.py's ``numbits`` blob into line numbers."""
    for byte_index, byte in enumerate(numbits):
        for bit in range(8):
            if byte & (1 << bit):
                yield byte_index * 8 + bit


@dataclass(frozen=True)
class Step:
    """One pick in a trajectory."""

    test_id: str
    new_lines: int
    seconds: float
    cumulative_lines: int
    cumulative_seconds: float


def greedy_trajectory(
    index: CoverageIndex,
    model: CostModel,
    *,
    target: float = 1.0,
    epsilon: float = 0.0,
    seed: Optional[int] = None,
) -> list[Step]:
    """Cost-weighted greedy (CELF), optionally sampling among near-ties.

    ``epsilon`` admits any candidate whose gain-per-second is within
    ``(1 - epsilon)`` of the best, and picks among them uniformly at random.
    At ``epsilon == 0`` this is deterministic greedy. Near-ties are where the
    free variation lives — dozens of tests with all-but-identical marginal
    gains, whose ordering is arbitrary — so harvesting them costs essentially
    nothing, unlike a temperature that perturbs every pick including the ones
    that matter.

    DETERMINISM IS LOAD-BEARING and easy to lose: Python randomises string
    hashing per process, so iterating a ``set``/``dict`` of test ids yields a
    different order each run and the same seed would produce a different
    trajectory. Every collection here is sorted before sampling.
    """
    # Not cryptographic: this samples among near-ties to diversify trajectories,
    # and is seeded precisely so a trajectory is reproducible.
    rng = random.Random(seed)  # noqa: S311  # nosec B311
    universe = index.universe()
    goal = int(index.total_lines * target)

    covered = 0
    chosen: list[Step] = []
    charged_groups: set[str] = set()

    def gain_and_cost(test_id: str, covered_mask: int) -> tuple[int, float]:
        new = (index.lines_of[test_id] & ~covered_mask).bit_count()
        cost = model.marginal.get(test_id, 0.0)
        group = model.group_of.get(test_id)
        if group is not None and group not in charged_groups:
            cost += model.fixed.get(group, 0.0)
        return new, max(cost, 1e-9)

    heap: list[tuple[float, str]] = []
    for test_id in sorted(index.lines_of):
        new, cost = gain_and_cost(test_id, 0)
        if new:
            heapq.heappush(heap, (-new / cost, test_id))

    while heap and covered.bit_count() < goal:
        candidates: list[tuple[float, str, int, float]] = []
        best_ratio: Optional[float] = None
        # Re-evaluate stale entries until the top is provably current, then
        # collect every candidate within epsilon of it.
        while heap:
            neg_ratio, test_id = heapq.heappop(heap)
            new, cost = gain_and_cost(test_id, covered)
            if new == 0:
                continue
            ratio = new / cost
            if heap and ratio < -heap[0][0] - 1e-12:
                heapq.heappush(heap, (-ratio, test_id))
                continue
            if best_ratio is None:
                best_ratio = ratio
            if ratio < best_ratio * (1.0 - epsilon) - 1e-12:
                heapq.heappush(heap, (-ratio, test_id))
                break
            candidates.append((ratio, test_id, new, cost))
            if epsilon <= 0.0:
                break
        if not candidates:
            break
        pick = candidates[0] if epsilon <= 0.0 else rng.choice(
            sorted(candidates, key=lambda c: c[1])
        )
        for cand in candidates:
            if cand is not pick:
                heapq.heappush(heap, (-cand[0], cand[1]))
        _ratio, test_id, new, cost = pick
        covered |= index.lines_of[test_id]
        group = model.group_of.get(test_id)
        if group is not None:
            charged_groups.add(group)
        chosen.append(Step(
            test_id=test_id,
            new_lines=new,
            seconds=cost,
            cumulative_lines=covered.bit_count(),
            cumulative_seconds=(chosen[-1].cumulative_seconds if chosen else 0.0) + cost,
        ))
    del universe
    return chosen


def residual_lines(index: CoverageIndex, chosen: Sequence[str]) -> int:
    """Bitset of lines the universe covers that ``chosen`` does not."""
    covered = 0
    for test_id in chosen:
        covered |= index.lines_of.get(test_id, 0)
    return index.universe() & ~covered


def jaccard(a: int, b: int) -> float:
    """Similarity of two line bitsets. 1.0 identical, 0.0 disjoint."""
    union = (a | b).bit_count()
    if union == 0:
        return 1.0
    return (a & b).bit_count() / union
