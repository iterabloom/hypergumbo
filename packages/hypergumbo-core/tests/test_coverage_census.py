# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the per-test coverage census and trajectory search.

THE COST MODEL IS WHAT THESE MOSTLY PIN, because it is the part that would be
silently wrong. A trajectory built on naive per-test durations optimises a cost
function that does not exist: measured on this repo,
``test_route_detected[annotation]`` records 286s and its fifteen siblings 0.0s,
because a module-scoped fixture runs the full pipeline seventeen times and
pytest bills the whole setup to whichever test triggered it. Drop the
"expensive" one, keep the "cheap" ones, and you pay the 286s anyway. Every
assertion about ``fixed`` / ``marginal`` below exists to stop that.

The greedy tests use hand-built indices with known optimal answers, because a
search whose correctness is only checked against its own output is not checked.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.coverage_census import (
    CostModel,
    CoverageIndex,
    PhaseDuration,
    build_cost_model,
    canonical_lines,
    greedy_trajectory,
    jaccard,
    load_index_from_coverage,
    parse_durations,
    redundancy_report,
    residual_lines,
)

#: Deliberately shaped: a multi-line ``if (`` whose continuation lines the
#: tracer reports and the parser attributes back to the ``if``; module-level
#: statements that only ever run at import; and a function nothing calls.
_SAMPLE = '''\
CONST = 1


def used(x):
    # a bare comment
    if (
        x > 0
        and x < 10
    ):
        return "yes"
    return "no"


def never_run():
    return "never"
'''

_UNTOUCHED = '''\
ORPHAN = 2
'''

#: Generates the DB in a SUBPROCESS on purpose. Starting a second ``Coverage``
#: inside the pytest-cov session swaps the trace function under the outer
#: measurement, which would make this repo's own 100% gate flaky. The subprocess
#: only builds the artifact; the code under test parses it in-process and is
#: measured normally.
_GENERATE = '''\
import sys
import coverage

db, root, extra = sys.argv[1], sys.argv[2], sys.argv[3] == "yes"
cov = coverage.Coverage(data_file=db, source=[root])
cov.start()
sys.path.insert(0, root)
import sample_mod                      # import-time -> the EMPTY context
if extra:
    import untouched_mod               # imported, never called into
cov.switch_context("test_used|run")
sample_mod.used(5)
cov.stop()
cov.save()
'''


def _build(tmp_path: Path, *, extra: bool) -> Path:
    (tmp_path / "sample_mod.py").write_text(_SAMPLE)
    if extra:
        (tmp_path / "untouched_mod.py").write_text(_UNTOUCHED)
    db = tmp_path / "census.coverage"
    subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_GENERATE),
         str(db), str(tmp_path), "yes" if extra else "no"],
        check=True, capture_output=True, text=True,
    )
    return db


def _lineno(path: Path, needle: str) -> int:
    """1-indexed line of the unique source line containing ``needle``."""
    hits = [i for i, line in enumerate(path.read_text().splitlines(), 1)
            if needle in line]
    assert len(hits) == 1, f"{needle!r} matched {hits}"
    return hits[0]


@pytest.fixture
def census_db(tmp_path: Path):
    return _build(tmp_path, extra=False), tmp_path / "sample_mod.py"


@pytest.fixture
def census_db_two_files(tmp_path: Path):
    db = _build(tmp_path, extra=True)
    return db, tmp_path / "sample_mod.py", tmp_path / "untouched_mod.py"


class TestParseDurations:
    """pytest's ``--durations=0 --durations-min=0`` report, three phases a test."""

    REPORT = """============================= slowest durations ==============================
286.40s setup    packages/core/tests/test_route.py::test_route_detected[annotation]
0.01s call     packages/core/tests/test_route.py::test_route_detected[annotation]
0.00s teardown packages/core/tests/test_route.py::test_route_detected[annotation]
0.00s setup    packages/core/tests/test_route.py::test_route_detected[django]
0.02s call     packages/core/tests/test_route.py::test_route_detected[django]
=========================== short test summary info ==========================
"""

    def test_phases_are_split_per_test(self) -> None:
        got = parse_durations(self.REPORT.splitlines())
        anno = got["packages/core/tests/test_route.py::test_route_detected[annotation]"]
        assert (anno.setup, anno.call, anno.teardown) == (286.40, 0.01, 0.00)

    def test_a_missing_phase_is_zero_not_absent(self) -> None:
        """django has no teardown line; it must not vanish or raise."""
        got = parse_durations(self.REPORT.splitlines())
        dj = got["packages/core/tests/test_route.py::test_route_detected[django]"]
        assert dj.teardown == 0.0
        assert dj.total == pytest.approx(0.02)

    def test_non_duration_lines_are_ignored(self) -> None:
        """A whole captured run log is a valid input, not just the report."""
        noisy = ["....... [ 42%]", "collected 9 items", *self.REPORT.splitlines()]
        assert len(parse_durations(noisy)) == 2

    def test_the_group_is_the_module(self) -> None:
        c = PhaseDuration("packages/core/tests/test_route.py::TestX::test_y[p]")
        assert c.group == "packages/core/tests/test_route.py"


class TestFixtureAwareCost:
    """THE measured defect: one test is billed for the whole module's fixture."""

    @staticmethod
    def _route_shaped() -> list[PhaseDuration]:
        """One test pays a 286s module fixture; fifteen siblings pay nothing."""
        return [
            PhaseDuration("m.py::t0", setup=286.0, call=0.01),
            *[PhaseDuration(f"m.py::t{i}", setup=0.0, call=0.01) for i in range(1, 16)],
        ]

    def test_the_one_time_fixture_is_not_charged_to_one_test(self) -> None:
        model = build_cost_model(self._route_shaped())
        assert model.marginal["m.py::t0"] < 1.0, (
            "the 286s module fixture is still billed to the test that "
            "triggered it, so greedy would drop that test and keep its "
            "siblings — which depend on the very fixture it was charged for"
        )

    def test_the_fixture_cost_is_charged_to_the_group(self) -> None:
        model = build_cost_model(self._route_shaped())
        assert model.fixed["m.py"] == pytest.approx(286.0)

    def test_running_any_sibling_pays_the_fixture_once(self) -> None:
        """The property that makes the model honest: you cannot dodge setup
        by picking only the cheap-looking tests."""
        model = build_cost_model(self._route_shaped())
        one = model.cost_of(["m.py::t7"])
        two = model.cost_of(["m.py::t7", "m.py::t9"])
        assert one == pytest.approx(286.01, abs=0.05)
        assert two == pytest.approx(286.02, abs=0.05), (
            "the second test from the same module must add only its marginal "
            "cost — the fixture is already paid"
        )

    def test_uniform_setup_produces_no_fixed_cost(self) -> None:
        """The common case must not be distorted by this machinery.

        A module whose tests all pay the same small setup has no one-time
        build, so ``fixed`` is zero and costs behave exactly like independent
        per-test durations.
        """
        model = build_cost_model([
            PhaseDuration(f"u.py::t{i}", setup=0.5, call=1.0) for i in range(5)
        ])
        assert model.fixed["u.py"] == 0.0
        assert model.marginal["u.py::t0"] == pytest.approx(1.5)

    def test_costs_from_different_modules_are_charged_separately(self) -> None:
        model = build_cost_model([
            PhaseDuration("a.py::t0", setup=10.0, call=0.1),
            PhaseDuration("a.py::t1", setup=0.0, call=0.1),
            PhaseDuration("b.py::t0", setup=4.0, call=0.1),
            PhaseDuration("b.py::t1", setup=0.0, call=0.1),
        ])
        assert model.cost_of(["a.py::t0"]) == pytest.approx(10.1, abs=0.05)
        assert model.cost_of(["a.py::t0", "b.py::t0"]) == pytest.approx(
            14.2, abs=0.05,
        )


class TestStripPhase:
    """pytest-cov records one context PER PHASE; nothing else spells it that way.

    Measured on a full census run of this repo: 19,103 ``|run`` contexts,
    1,210 ``|setup``, 124 ``|teardown``. Joining those raw against pytest's
    ``--durations`` report (which keys on the bare node id) matched **0 of
    20,437**. After folding, 19,129 of 19,129. A census that skipped this step
    would have produced a complete-looking table with every duration missing.
    """

    @pytest.mark.parametrize("phase", ["setup", "run", "teardown"])
    def test_each_phase_folds_to_the_bare_node_id(self, phase: str) -> None:
        from hypergumbo_core.coverage_census import strip_phase

        assert strip_phase(f"pkg/tests/t.py::TestX::test_y[p]|{phase}") == (
            "pkg/tests/t.py::TestX::test_y[p]"
        )

    def test_a_context_without_a_phase_is_unchanged(self) -> None:
        from hypergumbo_core.coverage_census import strip_phase

        assert strip_phase("pkg/tests/t.py::test_y") == "pkg/tests/t.py::test_y"

    def test_only_the_last_bar_is_treated_as_the_phase(self) -> None:
        """A parametrised id may legitimately contain ``|`` in its argument."""
        from hypergumbo_core.coverage_census import strip_phase

        assert strip_phase("t.py::test_x[a|b]|run") == "t.py::test_x[a|b]"

    def test_phases_of_one_test_merge_into_one_index_entry(self) -> None:
        """The defect this prevents: setup and body counted as two tests.

        Fixture-executed lines belong to the test that needed the fixture, so
        a test's coverage is the union across its phases. Indexed raw, the
        same test appears twice with disjoint line sets and every unique-line
        count is wrong.
        """
        from hypergumbo_core.coverage_census import strip_phase

        idx = CoverageIndex()
        idx.add(strip_phase("t.py::test_a|setup"), "f.py", [1, 2])
        idx.add(strip_phase("t.py::test_a|run"), "f.py", [3])
        assert sorted(idx.lines_of) == ["t.py::test_a"]
        assert idx.lines_of["t.py::test_a"].bit_count() == 3


class TestCoverageIndex:
    @staticmethod
    def _index() -> CoverageIndex:
        idx = CoverageIndex()
        idx.add("t_a", "f.py", [1, 2, 3])
        idx.add("t_b", "f.py", [3, 4])
        idx.add("t_c", "g.py", [1])
        return idx

    def test_lines_are_interned_per_file_not_globally(self) -> None:
        """``f.py:1`` and ``g.py:1`` are different lines.

        Interning on the line number alone would silently merge them and
        overstate coverage — the whole census would read as denser than it is.
        """
        assert self._index().total_lines == 5

    def test_universe_is_every_line_any_test_covers(self) -> None:
        assert self._index().universe().bit_count() == 5

    def test_unique_lines_counts_sole_executors(self) -> None:
        uniq = self._index().unique_lines()
        assert uniq["t_a"] == 2   # f.py:1, f.py:2  (f.py:3 shared with t_b)
        assert uniq["t_b"] == 1   # f.py:4
        assert uniq["t_c"] == 1   # g.py:1

    def test_a_fully_shadowed_test_has_zero_unique_lines(self) -> None:
        idx = CoverageIndex()
        idx.add("wide", "f.py", [1, 2, 3])
        idx.add("subset", "f.py", [2])
        assert idx.unique_lines()["subset"] == 0
        assert idx.unique_lines()["wide"] == 2


class TestGreedyTrajectory:
    """Checked against hand-built cases with known answers."""

    def test_it_prefers_coverage_per_second_not_raw_coverage(self) -> None:
        """The whole point of cost-weighting.

        ``slow`` covers more lines but costs 100x; ``fast`` is the better
        first pick and a coverage-only greedy would get this wrong.
        """
        idx = CoverageIndex()
        idx.add("slow", "f.py", range(1, 11))
        idx.add("fast", "f.py", range(11, 19))
        model = CostModel(
            marginal={"slow": 100.0, "fast": 1.0},
            group_of={"slow": "a.py", "fast": "b.py"},
        )
        traj = greedy_trajectory(idx, model)
        assert traj[0].test_id == "fast"

    def test_marginal_gain_shrinks_as_lines_are_covered(self) -> None:
        """Submodularity, asserted on the GAIN rather than on the pick.

        CORRECTED IN PLACE, carrying the measurement that refuted it: this
        first asserted ``"redundant" not in picked``, which is the wrong rule.
        ``redundant`` covers 5 lines for 0.1s (50/s) and ``wide`` covers 10 for
        1.0s (10/s), so picking the dense cheap one FIRST is cost-weighting
        working exactly as intended — a coverage-only greedy is what would
        prefer ``wide``. What submodularity actually promises is that ``wide``'s
        gain then DROPS from 10 to 5, and that is what is pinned here.
        """
        idx = CoverageIndex()
        idx.add("wide", "f.py", range(1, 11))
        idx.add("redundant", "f.py", range(1, 6))
        model = CostModel(
            marginal={"wide": 1.0, "redundant": 0.1},
            group_of={"wide": "a.py", "redundant": "a.py"},
        )
        traj = greedy_trajectory(idx, model)
        assert [s.test_id for s in traj] == ["redundant", "wide"]
        assert traj[0].new_lines == 5
        assert traj[1].new_lines == 5, (
            "wide covers ten lines but five were already taken; a gain that "
            "did not shrink means the search is double-counting coverage"
        )

    def test_a_fully_shadowed_test_is_never_picked(self) -> None:
        """Zero marginal gain means zero reason to pay for it, at any price.

        The ratios are set so ``wide`` genuinely wins the first pick — 10
        lines / 0.1s = 100/s against ``shadowed``'s 5 / 1.0s = 5/s. An earlier
        version of this test made ``shadowed`` the cheaper one and then
        asserted it would not be picked, which greedy rightly ignored: a test
        is only "shadowed" relative to what is ALREADY covered, so the
        construction has to put the shadowing test first on merit.
        """
        idx = CoverageIndex()
        idx.add("wide", "f.py", range(1, 11))
        idx.add("shadowed", "f.py", range(1, 6))
        model = CostModel(
            marginal={"wide": 0.1, "shadowed": 1.0},
            group_of={"wide": "a.py", "shadowed": "a.py"},
        )
        traj = greedy_trajectory(idx, model, target=1.0)
        assert [s.test_id for s in traj] == ["wide"]
        assert traj[0].cumulative_lines == 10

    def test_it_stops_at_the_target_rather_than_covering_everything(self) -> None:
        idx = CoverageIndex()
        for i in range(10):
            idx.add(f"t{i}", "f.py", [i])
        model = CostModel(
            marginal={f"t{i}": 1.0 for i in range(10)},
            group_of={f"t{i}": "a.py" for i in range(10)},
        )
        traj = greedy_trajectory(idx, model, target=0.5)
        assert len(traj) == 5
        assert traj[-1].cumulative_lines == 5

    def test_cumulative_seconds_charge_the_group_fixture_once(self) -> None:
        idx = CoverageIndex()
        idx.add("t0", "f.py", [1])
        idx.add("t1", "f.py", [2])
        model = CostModel(
            marginal={"t0": 1.0, "t1": 1.0},
            fixed={"m.py": 50.0},
            group_of={"t0": "m.py", "t1": "m.py"},
        )
        traj = greedy_trajectory(idx, model)
        assert traj[-1].cumulative_seconds == pytest.approx(52.0)

    def test_epsilon_zero_is_deterministic(self) -> None:
        idx = CoverageIndex()
        for i in range(20):
            idx.add(f"t{i}", "f.py", [i])
        model = CostModel(
            marginal={f"t{i}": 1.0 for i in range(20)},
            group_of={f"t{i}": "a.py" for i in range(20)},
        )
        a = [s.test_id for s in greedy_trajectory(idx, model, seed=1)]
        b = [s.test_id for s in greedy_trajectory(idx, model, seed=2)]
        assert a == b, "epsilon=0 must ignore the seed entirely"

    def test_the_same_seed_reproduces_the_same_trajectory(self) -> None:
        """Reproducibility is load-bearing: a logged seed is worthless if the
        selection also depends on per-process string hash order."""
        idx = CoverageIndex()
        for i in range(20):
            idx.add(f"t{i}", "f.py", [i])
        model = CostModel(
            marginal={f"t{i}": 1.0 for i in range(20)},
            group_of={f"t{i}": "a.py" for i in range(20)},
        )
        a = [s.test_id for s in greedy_trajectory(idx, model, epsilon=0.5, seed=7)]
        b = [s.test_id for s in greedy_trajectory(idx, model, epsilon=0.5, seed=7)]
        assert a == b

    def test_different_seeds_diverge_when_ties_exist(self) -> None:
        """Twenty interchangeable tests: the ordering is arbitrary, which is
        exactly the free variation epsilon is meant to harvest."""
        idx = CoverageIndex()
        for i in range(20):
            idx.add(f"t{i}", "f.py", [i])
        model = CostModel(
            marginal={f"t{i}": 1.0 for i in range(20)},
            group_of={f"t{i}": "a.py" for i in range(20)},
        )
        seen = {
            tuple(s.test_id for s in
                  greedy_trajectory(idx, model, epsilon=0.5, seed=s, target=0.5))
            for s in range(8)
        }
        assert len(seen) > 1

    def test_an_empty_index_yields_an_empty_trajectory(self) -> None:
        assert greedy_trajectory(CoverageIndex(), CostModel()) == []


class TestResidual:
    def test_residual_is_what_the_chosen_set_misses(self) -> None:
        idx = CoverageIndex()
        idx.add("a", "f.py", [1, 2])
        idx.add("b", "f.py", [3])
        assert residual_lines(idx, ["a"]).bit_count() == 1
        assert residual_lines(idx, ["a", "b"]).bit_count() == 0

    def test_an_unknown_test_contributes_nothing(self) -> None:
        idx = CoverageIndex()
        idx.add("a", "f.py", [1])
        assert residual_lines(idx, ["ghost"]).bit_count() == 1


class TestJaccard:
    @pytest.mark.parametrize("a,b,want", [
        (0b111, 0b111, 1.0),
        (0b110, 0b011, 1 / 3),
        (0b100, 0b001, 0.0),
        (0, 0, 1.0),
    ])
    def test_similarity(self, a: int, b: int, want: float) -> None:
        assert jaccard(a, b) == pytest.approx(want)


class TestCanonicalLines:
    """The raw tracer line is NOT always the line the coverage gate scores.

    A multi-line statement (``if (`` spanning four lines) is traced on its INNER
    lines and attributed by coverage's parser back to the statement's first
    line. Intersecting raw traced lines with the statement set — the census's
    original remedy — therefore DISCARDS the coverage of every such statement.

    Measured on the real census DB before this landed: 144 statements across 53
    files that coverage reports covered were absent from the index, concentrated
    in the largest analyzers (py.py 17, go.py 15, java.py 12). Translating first
    closes it to 0.
    """

    def test_a_continuation_line_maps_to_the_statement_start(
        self, census_db,
    ) -> None:
        db, sample = census_db
        canon = canonical_lines(db)[str(sample)]
        cont = _lineno(sample, "and x < 10")
        head = _lineno(sample, "if (")
        assert canon[cont] == head, (
            "the continuation line of a multi-line `if (` must score against "
            "the statement's first line, or its coverage is silently dropped"
        )

    def test_a_line_that_is_not_a_statement_is_dropped(
        self, census_db,
    ) -> None:
        """The control for the test above.

        If translation mapped everything through, the index would gain lines
        the coverage gate does not count and the two denominators would
        disagree in the other direction.
        """
        db, sample = census_db
        canon = canonical_lines(db)[str(sample)]
        assert _lineno(sample, "# a bare comment") not in canon


class TestImportTimeAttribution:
    """Module-level code executes at IMPORT, under no test context at all.

    Measured on the real census: 9,192 statements — 14.5% of the coverage
    gate's denominator — are executed only under the empty context. Left out,
    the index optimises over 85.2% of what CI actually scores.

    The rule that recovers them is an implication, not an approximation: if a
    test executes any line in file F, then F was necessarily imported, so F's
    import-time lines are covered whenever that test runs — for ANY subset.
    """

    def test_import_time_lines_are_credited_to_a_test_that_touches_the_file(
        self, census_db,
    ) -> None:
        db, sample = census_db
        index = load_index_from_coverage(db)
        const = _lineno(sample, "CONST = 1")
        line_id = index.line_ids[(str(sample), const)]
        assert index.lines_of["test_used"] & (1 << line_id), (
            "CONST = 1 runs at import; a test executing code in this file "
            "cannot run without it, so it must be credited"
        )

    def test_import_lines_of_an_untouched_file_are_NOT_credited(
        self, census_db_two_files,
    ) -> None:
        """The control, and the one a careless fix breaks.

        Crediting every import-time line to every test would make the recovered
        14.5% unconditionally free, which is exactly the false claim the
        original blind spot hid. The credit is conditional on TOUCHING the file.
        """
        db, _sample, untouched = census_db_two_files
        index = load_index_from_coverage(db)
        assert not any(
            path == str(untouched) for path, _ in index.line_ids
        ), "a file no test executes must contribute nothing to any test's mask"

    def test_unattributable_import_lines_are_reported_not_discarded(
        self, census_db_two_files,
    ) -> None:
        """The residual is 25 lines over 7 files on the real census, 6 of them
        package ``__init__.py``. Small, but it is a real gap in what the index
        can see and it belongs in the data structure rather than in a comment.
        """
        db, _sample, untouched = census_db_two_files
        index = load_index_from_coverage(db)
        assert str(untouched) in index.unattributed_lines
        assert index.unattributed_lines[str(untouched)]


class TestLoadIndexEndToEnd:
    def test_a_multiline_statement_is_covered_by_the_test_that_ran_it(
        self, census_db,
    ) -> None:
        """The two fixes meeting: translation feeding the index."""
        db, sample = census_db
        index = load_index_from_coverage(db)
        head = _lineno(sample, "if (")
        line_id = index.line_ids[(str(sample), head)]
        assert index.lines_of["test_used"] & (1 << line_id)

    def test_a_test_is_not_credited_with_a_line_it_never_ran(
        self, census_db,
    ) -> None:
        """The control. Without it, "everything is covered" is equally
        consistent with a fix and with an index that ORs everything together."""
        db, sample = census_db
        index = load_index_from_coverage(db)
        only_other = _lineno(sample, 'return "never"')
        line_id = index.line_ids.get((str(sample), only_other))
        assert line_id is None or not (index.lines_of["test_used"] & (1 << line_id))


def _idx(**tests: set[int]) -> CoverageIndex:
    index = CoverageIndex()
    for name, lines in tests.items():
        index.add(name, "m.py", sorted(lines))
    return index


class TestLazyGreedyReevaluation:
    """The CELF machinery itself — stale-gain re-push, exhausted candidates,
    and the epsilon cut-off. These branches carry the algorithm's correctness
    guarantee and none of them had a test.

    Every expected pick below is DERIVED, not observed. Two earlier tests in
    this file asserted a pick without computing gain-per-second; both times
    greedy was right and the test was wrong.
    """

    def test_a_stale_entry_beaten_by_the_next_is_repushed_not_taken(
        self,
    ) -> None:
        """A={1,2,3,4}/1s  B={3,4,5}/1s  C={6,7}/0.8s

        step 1  A 4/1=4.0   B 3/1=3.0   C 2/0.8=2.5   -> A
        step 2  B is STALE at 3.0 but now yields only {5} = 1/1 = 1.0, which is
                below C's stale 2.5, so it must go BACK on the heap and C wins.
                Taking the stale top here would pick B and cost the guarantee.
        step 3  B 1/1 -> B
        """
        index = _idx(A={1, 2, 3, 4}, B={3, 4, 5}, C={6, 7})
        model = CostModel(marginal={"A": 1.0, "B": 1.0, "C": 0.8})
        steps = greedy_trajectory(index, model)
        assert [s.test_id for s in steps] == ["A", "C", "B"]
        assert [s.new_lines for s in steps] == [4, 2, 1]

    def test_an_entry_with_no_remaining_gain_is_dropped(self) -> None:
        """A={1,2,3,4}/1s  B={1,2,3}/1s  C={5,6}/1s

        step 1  A 4.0 > B 3.0 > C 2.0 -> A
        step 2  B is fully subsumed: new == 0, so it is discarded rather than
                re-pushed (re-pushing a zero-gain entry loops forever).
        """
        index = _idx(A={1, 2, 3, 4}, B={1, 2, 3}, C={5, 6})
        model = CostModel(marginal={"A": 1.0, "B": 1.0, "C": 1.0})
        steps = greedy_trajectory(index, model)
        assert [s.test_id for s in steps] == ["A", "C"]

    def test_epsilon_admits_the_tie_and_cuts_off_below_it(self) -> None:
        """A={1,2,3,4}/1s  B={5,6,7,8}/1s  C={9}/1s, epsilon=0.1

        A and B both score 4.0 and are admitted as near-ties. C scores 1.0,
        which is below 4.0*(1-0.1)=3.6, so collection STOPS there and C goes
        back on the heap — the cut-off is what keeps epsilon sampling from
        degenerating into a uniform random pick over everything.
        """
        index = _idx(A={1, 2, 3, 4}, B={5, 6, 7, 8}, C={9})
        model = CostModel(marginal={"A": 1.0, "B": 1.0, "C": 1.0})
        first = {
            greedy_trajectory(index, model, epsilon=0.1, seed=s)[0].test_id
            for s in range(12)
        }
        assert first == {"A", "B"}, (
            "epsilon must sample among the 4.0 ties and never reach C at 1.0"
        )

    def test_it_terminates_when_the_target_is_unreachable(self) -> None:
        """A line no test executes cannot be covered, and greedy must STOP
        rather than spin.

        This is exactly the shape :attr:`CoverageIndex.unattributed_lines`
        describes — import-time lines in a file no test touches. Built directly
        here because the loader now routes those OUT of ``line_ids``, so the
        only way to reach this branch is to construct the index by hand.

        TWO tests, not one, and that is load-bearing. Line 2 is unreachable, so
        goal=2 is never met and the outer loop keeps going:

            iter 1  heap=[A,B]  A yields {1} -> picked, covered={1}
            iter 2  heap=[B]    B now yields NOTHING, so it is discarded and
                                the inner loop exhausts the heap with no
                                candidate -> the `if not candidates` exit.

        With a single test the heap is empty by iteration 2 and the ``while
        heap`` guard exits FIRST, so that exit is never reached. The one-test
        version of this test passed while leaving the branch uncovered — the
        assertion was right and the construction did not exercise the mechanism.
        """
        index = CoverageIndex(
            line_ids={("m.py", 1): 0, ("m.py", 2): 1},
            lines_of={"A": 0b01, "B": 0b01},
        )
        steps = greedy_trajectory(
            index, CostModel(marginal={"A": 1.0, "B": 1.0}),
        )
        assert [s.test_id for s in steps] == ["A"]
        assert steps[-1].cumulative_lines == 1


class TestUnmeasuredTestsAreNotRedundant:
    """A test with NO coverage rows is ABSENT from the data, not zero — and
    the difference is the whole safety property.

    Measured on this suite: 3,614 tests (15.9%) produce no rows at all.
    2,818 are hypergumbo-tracker, which is not in ``COV_PATHS`` at all; 435
    have an IMPORT-TIME subject (a registry or frozen dataclass built at
    import, so nothing executes during the test); 255 run their code in a
    subprocess; 106 test a file under ``scripts/``, which is outside
    ``packages/*/src``.

    Because the index derives its population FROM the rows, those tests were
    invisible — which made them look like the 18,181 "coverage-redundant"
    tail. They are not redundant; they are unobserved. And the import-time
    group is the dangerous one: ``test_specs_are_frozen`` and
    ``test_registry_has_no_duplicate_names`` validate exactly the lines this
    module credits to OTHER tests, so dropping them moves the coverage gate
    not at all while deleting an invariant guard.
    """

    def test_a_known_test_with_no_rows_is_recorded_as_unmeasured(
        self, census_db,
    ) -> None:
        db, _sample = census_db
        index = load_index_from_coverage(
            db, known_tests=["test_used", "test_no_rows_at_all"],
        )
        assert index.unmeasured_tests == frozenset({"test_no_rows_at_all"})
        assert "test_no_rows_at_all" not in index.lines_of

    def test_without_a_test_list_nothing_is_claimed(self, census_db) -> None:
        """The control. The DB alone cannot reveal a test that left no trace,
        so the index must not invent one — an empty set here means "not asked",
        and :func:`redundancy_report` says so rather than implying zero."""
        db, _sample = census_db
        assert load_index_from_coverage(db).unmeasured_tests == frozenset()

    def test_the_redundant_share_excludes_what_was_never_observed(
        self, census_db,
    ) -> None:
        """The denominator is the bug. Counting unmeasured tests as redundant
        is how 76% of the observable suite got reported as 79% of the whole."""
        db, _sample = census_db
        index = load_index_from_coverage(
            db, known_tests=["test_used", "ghost_a", "ghost_b"],
        )
        rep = redundancy_report(index, chosen=["test_used"])
        assert rep.measurable == 1
        assert rep.unmeasured == 2
        assert rep.coverage_redundant == 0
        assert rep.redundant_share == 0.0, (
            "two unobserved tests must not read as redundant"
        )

    def test_unmeasured_tests_are_never_skip_candidates(
        self, census_db,
    ) -> None:
        """The property that actually prevents the harm."""
        db, _sample = census_db
        index = load_index_from_coverage(
            db, known_tests=["test_used", "ghost_a"],
        )
        rep = redundancy_report(index, chosen=["test_used"])
        assert "ghost_a" not in rep.skip_candidates
        assert rep.skip_candidates == frozenset()
