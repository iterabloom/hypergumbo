# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for shadow-mode comparison.

THE POINT OF SHADOW MODE is to accumulate evidence for free, so the thing these
tests protect is the HONESTY of that evidence rather than its convenience. Two
properties carry almost all the weight:

  * the two directions stay separate. A net delta would let a miss (dangerous)
    be cancelled by an extra (harmless), which is the one summary that must
    never be produced.
  * a failed join is announced. Every join in this project that could fail
    silently, did — pytest-cov's ``|setup`` suffix matched 0 of 20,437 contexts.
    A report whose join collapsed shows zero misses and reads as a clean pass.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.selection_index import Selection
from hypergumbo_core.selection_shadow import (
    ShadowReport,
    compare,
    failed_tests_from_junit,
    ran_tests_from_junit,
)

_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2">
<testcase classname="pkg.tests.test_x.Klass" name="test_one" time="0.1"/>
<testcase classname="pkg.tests.test_x" name="test_two" time="0.1"/>
<testcase classname="nowhere.test_gone" name="test_three" time="0.1"/>
</testsuite></testsuites>
"""


def _sel(tests, **kw) -> Selection:
    return Selection(
        tests=frozenset(tests),
        changed_blocks=frozenset(kw.get("changed", ())),
        new_blocks=frozenset(kw.get("new", ())),
        unknown_paths=frozenset(kw.get("unknown", ())),
        unmeasured=frozenset(kw.get("unmeasured", ())),
        missing_paths=frozenset(kw.get("missing", ())),
    )


class TestJunitNodeIds:
    """junit gives a DOTTED classname and no file attribute, so the module /
    class boundary has to be recovered by finding the file that exists."""

    def _tree(self, tmp_path: Path) -> Path:
        d = tmp_path / "pkg" / "tests"
        d.mkdir(parents=True)
        (d / "test_x.py").write_text("")
        (tmp_path / "junit.xml").write_text(_JUNIT)
        return tmp_path

    def test_a_class_nested_test_becomes_a_full_node_id(self, tmp_path) -> None:
        root = self._tree(tmp_path)
        got = ran_tests_from_junit(root / "junit.xml", root)
        assert "pkg/tests/test_x.py::Klass::test_one" in got

    def test_a_module_level_test_has_no_class_segment(self, tmp_path) -> None:
        """The boundary is real: ``pkg.tests.test_x`` is entirely module, so
        inserting a phantom class segment would produce an id that joins with
        nothing."""
        root = self._tree(tmp_path)
        got = ran_tests_from_junit(root / "junit.xml", root)
        assert "pkg/tests/test_x.py::test_two" in got

    def test_an_unresolvable_classname_is_dropped_not_invented(
        self, tmp_path,
    ) -> None:
        """``nowhere/test_gone.py`` does not exist. Guessing a path would
        attach a test to a file that is not there and quietly poison the join.
        """
        root = self._tree(tmp_path)
        got = ran_tests_from_junit(root / "junit.xml", root)
        assert not any("test_three" in g for g in got)
        assert len(got) == 2


class TestTheTwoDirectionsStaySeparate:
    def test_a_test_that_ran_but_coverage_would_miss_is_reported(self) -> None:
        rep = compare(_sel({"a.py::t1"}), actual_files={"a.py", "b.py"})
        assert rep.missed_by_coverage == {"b.py"}

    def test_a_test_coverage_adds_is_reported_separately(self) -> None:
        rep = compare(_sel({"a.py::t1", "c.py::t9"}), actual_files={"a.py"})
        assert rep.extra_from_coverage == {"c.py"}

    def test_a_miss_and_an_extra_do_not_cancel(self) -> None:
        """The single most important property here. If these were netted, one
        harmless extra would hide one dangerous miss and the exit criterion for
        the whole phase would be unmeasurable."""
        rep = compare(_sel({"c.py::t9"}), actual_files={"b.py"})
        assert rep.missed_by_coverage == {"b.py"}
        assert rep.extra_from_coverage == {"c.py"}

    def test_agreement_produces_both_directions_empty(self) -> None:
        rep = compare(_sel({"a.py::t1", "a.py::t2"}), actual_files={"a.py"})
        assert not rep.missed_by_coverage and not rep.extra_from_coverage


class TestGranularity:
    def test_the_comparison_is_file_to_file(self) -> None:
        """smart-test selects FILES. Comparing node ids against files would
        flatter coverage by construction."""
        rep = compare(_sel({"a.py::t1", "a.py::t2", "a.py::t3"}),
                      actual_files={"a.py"})
        assert rep.coverage_files == {"a.py"}
        assert not rep.extra_from_coverage

    def test_the_node_level_count_is_still_reported_as_the_potential(
        self,
    ) -> None:
        rep = compare(_sel({"a.py::t1", "a.py::t2", "a.py::t3"}),
                      actual_files={"a.py"})
        assert len(rep.coverage_tests) == 3


class TestJoinRateGate:
    """A collapsed join yields zero misses and looks like a perfect result."""

    def test_a_failed_join_is_flagged_untrustworthy(self) -> None:
        rep = compare(_sel({"a.py::t1", "a.py::t2"}),
                      actual_files={"a.py"},
                      known_tests={"totally/other.py::t1"})
        assert rep.join_rate == 0.0
        assert not rep.trustworthy
        assert "JOIN FAILED" in rep.summary()

    def test_a_good_join_is_trustworthy(self) -> None:
        rep = compare(_sel({"a.py::t1"}), actual_files={"a.py"},
                      known_tests={"a.py::t1", "a.py::t2"})
        assert rep.join_rate == 1.0
        assert rep.trustworthy
        assert "JOIN FAILED" not in rep.summary()

    def test_no_control_supplied_is_not_a_failed_control(self) -> None:
        """``None`` means "not asked". Reporting 0.0 would make every
        un-instrumented call look like a broken join and train the reader to
        ignore the warning."""
        rep = compare(_sel({"a.py::t1"}), actual_files={"a.py"})
        assert rep.join_rate == 1.0 and rep.trustworthy

    def test_an_empty_selection_is_not_a_failed_join(self) -> None:
        """Nothing changed, so nothing was selected — that is the common case
        and must not raise a division by zero or cry wolf."""
        rep = compare(_sel(set()), actual_files={"a.py"}, known_tests={"a.py::t1"})
        assert rep.trustworthy


class TestPassThrough:
    def test_the_cases_coverage_cannot_speak_to_are_carried(self) -> None:
        """new/unknown/missing/unmeasured are the reasons a small ``tests`` set
        might be untrustworthy; dropping them would make the report look
        cleaner than the evidence supports."""
        rep = compare(
            _sel({"a.py::t1"}, new=[("a.py", "gamma")], unknown=["z.py"],
                 missing=["gone.py"], unmeasured=["t_sub"]),
            actual_files={"a.py"},
        )
        assert rep.new_blocks == 1
        assert rep.unknown_paths == {"z.py"}
        assert rep.missing_paths == {"gone.py"}
        assert rep.unmeasured == 1

    def test_the_summary_names_both_directions(self) -> None:
        text = compare(_sel({"a.py::t1"}), actual_files={"b.py"}).summary()
        assert "MISSED_BY_COVERAGE" in text and "EXTRA_FROM_COVERAGE" in text

    def test_report_is_immutable(self) -> None:
        """Observations get accumulated across commits; a mutable one invites
        a caller to 'correct' a miss out of the record."""
        rep = compare(_sel({"a.py::t1"}), actual_files={"a.py"})
        assert isinstance(rep, ShadowReport)
        try:
            rep.join_rate = 0.0  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("ShadowReport must be frozen")


class TestAColdIndexIsNotEvidence:
    """OBSERVED on the first real end-to-end run: 8 of 8 changed files unknown
    to the index, 0 tests selected, 87 reported as MISSED_BY_COVERAGE — and the
    report called itself trustworthy because the join rate was vacuously 1.0.

    Phase 1's exit criterion counts misses, so admitting phantom ones from a
    cold index corrupts exactly the evidence the shadow exists to collect.
    """

    def test_all_changed_files_unknown_is_not_informative(self) -> None:
        rep = compare(
            _sel(set(), unknown=["a.py", "b.py"]),
            actual_files={"t1.py", "t2.py"},
            changed_files=["a.py", "b.py"],
        )
        assert rep.missed_by_coverage == {"t1.py", "t2.py"}
        assert not rep.informative
        assert not rep.trustworthy
        assert "INDEX COLD" in rep.summary()

    def test_one_known_file_is_enough_to_be_informative(self) -> None:
        """The partner. A partially-cold index still says something real about
        the file it knows, so it must not be discarded wholesale."""
        rep = compare(
            _sel({"t1.py::x"}, unknown=["b.py"]),
            actual_files={"t1.py"},
            changed_files=["a.py", "b.py"],
        )
        assert rep.informative and rep.trustworthy

    def test_no_changed_files_is_informative_not_cold(self) -> None:
        """Nothing changed is a real, correct answer of "run nothing", not an
        absence of knowledge."""
        rep = compare(_sel(set()), actual_files=set(), changed_files=[])
        assert rep.informative

    def test_a_cold_index_still_reports_a_failed_join_distinctly(self) -> None:
        """The two failure modes must stay distinguishable in the summary, or
        a reader cannot tell "seed the index" from "fix the join"."""
        rep = compare(_sel({"a.py::t"}), actual_files={"a.py"},
                      known_tests={"other.py::t"}, changed_files=["a.py"])
        assert "JOIN FAILED" in rep.summary()


_JUNIT_MIXED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="pkg.tests.test_x" name="test_ok" time="0.1"/>
<testcase classname="pkg.tests.test_x" name="test_broke" time="0.1">
  <failure message="assert 1 == 2">E assert 1 == 2</failure>
</testcase>
<testcase classname="pkg.tests.test_y" name="test_blew_up" time="0.1">
  <error message="fixture error">E RuntimeError</error>
</testcase>
<testcase classname="pkg.tests.test_y" name="test_skipped" time="0.0">
  <skipped message="no reason"/>
</testcase>
</testsuite></testsuites>
"""


class TestFailedTests:
    """The exit criterion is "a miss that ALSO FAILED", so the failure set is
    the load-bearing half of the evidence — and the shadow did not record it
    at all when Phase 1 shipped.
    """

    def _tree(self, tmp_path: Path) -> Path:
        for name in ("test_x.py", "test_y.py"):
            d = tmp_path / "pkg" / "tests"
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text("")
        (tmp_path / "junit.xml").write_text(_JUNIT_MIXED)
        return tmp_path

    def test_assertion_failures_are_captured(self, tmp_path) -> None:
        root = self._tree(tmp_path)
        got = failed_tests_from_junit(root / "junit.xml", root)
        assert "pkg/tests/test_x.py::test_broke" in got

    def test_errors_count_as_failures(self, tmp_path) -> None:
        """A collection or fixture error is a test that did not pass. Counting
        only <failure> would let the INV-vilag shape — green verdict on modules
        that never imported — back in through the evidence."""
        root = self._tree(tmp_path)
        assert "pkg/tests/test_y.py::test_blew_up" in failed_tests_from_junit(
            root / "junit.xml", root)

    def test_skips_and_passes_are_not_failures(self, tmp_path) -> None:
        root = self._tree(tmp_path)
        got = failed_tests_from_junit(root / "junit.xml", root)
        assert not any(n.endswith(("test_ok", "test_skipped")) for n in got)
        assert len(got) == 2


class TestDangerousMisses:
    """The ONLY disqualifying result: coverage would not have selected a test
    that ran and failed. Everything else in the report is diagnostics."""

    def test_a_failed_test_in_an_unselected_file_is_dangerous(self) -> None:
        rep = compare(_sel({"a.py::t1"}), actual_files={"a.py", "b.py"},
                      failed={"b.py::t9"})
        assert rep.dangerous_misses == {"b.py::t9"}

    def test_a_failed_test_in_a_SELECTED_file_is_not_dangerous(self) -> None:
        """Coverage would have run it, so it caught nothing that coverage
        would have lost. Without this the metric counts ordinary red suites."""
        rep = compare(_sel({"a.py::t1"}), actual_files={"a.py"},
                      failed={"a.py::t1"})
        assert rep.dangerous_misses == frozenset()

    def test_a_green_run_has_no_dangerous_misses_however_many_are_missed(
        self,
    ) -> None:
        """87 missed files mean nothing if none of them failed — which is the
        normal case and why raw miss COUNTS are not the criterion."""
        rep = compare(_sel(set()), actual_files={f"f{i}.py" for i in range(87)},
                      failed=set())
        assert len(rep.missed_by_coverage) == 87
        assert rep.dangerous_misses == frozenset()

    def test_no_failure_data_is_not_zero_failures(self) -> None:
        """``None`` means the junit was unavailable. Reporting an empty set
        would let a run with no failure data count as clean evidence."""
        rep = compare(_sel({"a.py::t1"}), actual_files={"a.py", "b.py"})
        assert rep.dangerous_misses is None

    def test_a_failure_in_an_unresolvable_file_is_dropped_not_invented(
        self, tmp_path,
    ) -> None:
        """Same guard as the ran-tests path, and it matters more here: a
        fabricated path would produce a phantom node id that joins with
        nothing, so a REAL failure would be silently reclassified as a
        dangerous miss against a file that does not exist."""
        (tmp_path / "junit.xml").write_text(
            '<testsuites><testsuite name="pytest">'
            '<testcase classname="nowhere.test_ghost" name="test_x">'
            '<failure message="boom">E boom</failure></testcase>'
            '</testsuite></testsuites>')
        assert failed_tests_from_junit(tmp_path / "junit.xml", tmp_path) == set()
