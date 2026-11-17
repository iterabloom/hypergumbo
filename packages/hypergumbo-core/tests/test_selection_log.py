# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the shadow phase's durable evidence log.

WHAT THESE PROTECT is the honesty of a verdict that will later be used to
justify letting a selector DROP tests. Three ways this could quietly lie, one
class each:

  * counting raw misses as the criterion — a green run can miss 87 files and
    lose nothing, so that would reject the selector on its first ordinary day
  * counting observations that rest on nothing (cold index, all-new code) —
    they report every test as a miss AND advance the commit counter
  * treating "no failure data" as "nothing failed" — the difference between
    not looking and looking and finding nothing

The append-only property has its own test because the defect this module
exists to fix was exactly a file being overwritten every run.
"""
from __future__ import annotations

import json
from pathlib import Path

import gzip

from hypergumbo_core.selection_log import (
    DEFAULT_MIN_COMMITS,
    STALE_GRAPH_MIN_COMMITS,
    append_observation,
    append_run_record,
    append_stale_graph_observation,
    evaluate,
    evaluate_stale_graph,
    load_observations,
    sliceable_sources,
    sliced_test_files,
    stale_graph_row,
    store_snapshot,
)
from hypergumbo_core.selection_index import Selection
from hypergumbo_core.selection_shadow import compare


def _report(*, cov=("a.py::t1",), actual=("a.py",), failed=None,
            changed=("src.py",), unknown=()):
    return compare(
        Selection(
            tests=frozenset(cov), changed_blocks=frozenset(),
            new_blocks=frozenset(), unknown_paths=frozenset(unknown),
            unmeasured=frozenset(), missing_paths=frozenset(),
        ),
        actual_files=actual, changed_files=changed, failed=failed,
    )


def _row(**kw) -> dict:
    base = {"sha": "abc123", "informative": True, "trustworthy": True,
            "dangerous_misses": []}
    base.update(kw)
    return base


class TestAppendOnly:
    """The defect this module fixes: the shadow overwrote its output every run,
    so no evidence could ever accumulate."""

    def test_a_second_observation_does_not_replace_the_first(
        self, tmp_path,
    ) -> None:
        log = tmp_path / "obs.jsonl"
        append_observation(log, _report(), sha="aaa")
        append_observation(log, _report(), sha="bbb")
        assert [r["sha"] for r in load_observations(log)] == ["aaa", "bbb"]

    def test_a_missing_log_is_empty_not_an_error(self, tmp_path) -> None:
        assert load_observations(tmp_path / "nope.jsonl") == []

    def test_a_truncated_final_line_does_not_destroy_the_history(
        self, tmp_path,
    ) -> None:
        """An interrupted run can leave a half-written line. The whole history
        must survive it — this log is written by a tool that must never break
        the run it rides along on."""
        log = tmp_path / "obs.jsonl"
        append_observation(log, _report(), sha="aaa")
        with log.open("a") as fh:
            fh.write('{"sha": "bbb", "informa')
        assert [r["sha"] for r in load_observations(log)] == ["aaa"]

    def test_blank_lines_are_skipped(self, tmp_path) -> None:
        """An append that raced a truncation, or a hand-edited log, can leave a
        bare newline. It is not an observation and must not parse as one."""
        log = tmp_path / "obs.jsonl"
        append_observation(log, _report(), sha="aaa")
        with log.open("a") as fh:
            fh.write("\n   \n")
        append_observation(log, _report(), sha="bbb")
        assert [r["sha"] for r in load_observations(log)] == ["aaa", "bbb"]

    def test_no_failure_data_is_recorded_as_null_not_empty(
        self, tmp_path,
    ) -> None:
        """``[]`` would read as "looked, found nothing". ``null`` says "did not
        look", and the difference decides whether the run is evidence."""
        log = tmp_path / "obs.jsonl"
        append_observation(log, _report(failed=None), sha="aaa")
        assert json.loads(log.read_text())["dangerous_misses"] is None


class TestTheCriterionIsFailures:
    def test_many_misses_with_no_failures_is_not_disqualifying(self) -> None:
        """The normal green run. Counting raw misses would reject the selector
        immediately and permanently."""
        rows = [_row(sha=f"c{i}", missed_by_coverage=[f"f{j}.py" for j in range(87)])
                for i in range(DEFAULT_MIN_COMMITS)]
        ev = evaluate(rows)
        assert ev.verdict == "met"

    def test_one_dangerous_miss_disqualifies_regardless_of_volume(self) -> None:
        """A single confirmed miss that FAILED is not offset by any number of
        clean runs, so it is checked before the commit count."""
        rows = [_row(sha=f"c{i}") for i in range(DEFAULT_MIN_COMMITS)]
        rows.append(_row(sha="bad", dangerous_misses=["b.py::t9"]))
        ev = evaluate(rows)
        assert ev.verdict == "disqualified"
        assert ("bad", "b.py::t9") in ev.dangerous

    def test_too_few_commits_is_insufficient_not_met(self) -> None:
        ev = evaluate([_row(sha=f"c{i}") for i in range(3)])
        assert ev.verdict == "insufficient"

    def test_repeat_runs_on_one_commit_count_once(self) -> None:
        """Otherwise re-running smart-test 30 times on one commit would satisfy
        a criterion that is about breadth of change, not repetition."""
        ev = evaluate([_row(sha="same") for _ in range(DEFAULT_MIN_COMMITS)])
        assert ev.commits == 1
        assert ev.verdict == "insufficient"


class TestObservationsThatRestOnNothing:
    def test_a_collapsed_join_is_not_evidence_either(self) -> None:
        """OBSERVED: the shadow originally ran BEFORE the suite and read the
        PREVIOUS run's junit, so the join fell to 0% and the failure set
        belonged to a different commit. Such a row shows zero dangerous misses
        and would have supplied clean-looking evidence for free."""
        rows = [_row(sha=f"c{i}", trustworthy=False)
                for i in range(DEFAULT_MIN_COMMITS)]
        ev = evaluate(rows)
        assert ev.commits == 0
        assert ev.verdict == "insufficient"

    def test_uninformative_rows_do_not_advance_the_commit_count(self) -> None:
        """A cold index selects nothing and 'misses' everything. Letting those
        advance the counter would reach the threshold with no evidence."""
        rows = [_row(sha=f"c{i}", informative=False, trustworthy=False)
                for i in range(DEFAULT_MIN_COMMITS)]
        ev = evaluate(rows)
        assert ev.commits == 0
        assert ev.verdict == "insufficient"

    def test_the_informative_rate_is_reported(self) -> None:
        """If most observations are disqualified, "30 commits" is a far longer
        wait than it sounds, and that must be visible rather than discovered."""
        ev = evaluate([_row(sha="a"),
                       _row(sha="b", informative=False, trustworthy=False)])
        assert ev.informative_rate == 0.5
        assert "50%" in ev.summary()

    def test_rows_without_failure_data_are_counted_separately(self) -> None:
        """Not evidence for OR against — but if every row lands here the
        verdict is vacuous, so the count is surfaced."""
        ev = evaluate([_row(sha="a", dangerous_misses=None)])
        assert ev.unjudgeable == 1
        assert not ev.dangerous


class TestSummaryIsReadable:
    def test_it_names_the_verdict_and_the_offending_tests(self) -> None:
        ev = evaluate([_row(sha="deadbeefcafe", dangerous_misses=["b.py::t9"])])
        text = ev.summary()
        assert "DISQUALIFIED" in text
        assert "b.py::t9" in text
        assert "deadbeefcafe"[:12] in text

    def test_an_empty_log_does_not_divide_by_zero(self) -> None:
        ev = evaluate([])
        assert ev.informative_rate == 0.0
        assert ev.verdict == "insufficient"


def test_end_to_end_append_then_evaluate(tmp_path: Path) -> None:
    """The real path: build reports, append them, read them back, judge."""
    log = tmp_path / "obs.jsonl"
    append_observation(log, _report(failed=set()), sha="c1")
    append_observation(
        log,
        _report(cov=("a.py::t1",), actual=("a.py", "b.py"),
                failed={"b.py::t9"}),
        sha="c2")
    ev = evaluate(load_observations(log))
    assert ev.observations == 2
    assert ev.verdict == "disqualified", ev.summary()
    assert ev.dangerous == (("c2", "b.py::t9"),)


class TestSliceOutput:
    """Both walks are read by one rule; a rule that read them differently
    would manufacture a difference between identical answers."""

    def test_test_files_are_kept_and_made_repo_relative(self) -> None:
        lines = [
            "/repo/packages/core/tests/test_a.py",
            "packages/core/tests/BRANCHES_test_b.py",
            "/repo/packages/core/src/core/a.py",
            "/repo/packages/core/tests/helper.py",
            "",
            "/elsewhere/packages/x/tests/test_c.py",
        ]
        assert sliced_test_files(lines, "/repo/") == {
            "packages/core/tests/test_a.py",
            "packages/core/tests/BRANCHES_test_b.py",
            "/elsewhere/packages/x/tests/test_c.py",
        }

    def test_only_package_source_seeds_the_slice(self) -> None:
        assert sliceable_sources([
            "packages/core/src/core/a.py", "packages/core/tests/test_a.py",
            "scripts/smart-test", "docs/x.md", "packages/core/src/core/data.yaml",
        ]) == {"packages/core/src/core/a.py"}


class TestStaleGraphRow:
    def test_a_fresh_only_file_the_change_edited_is_not_a_loss(self) -> None:
        row = stale_graph_row(
            fresh=["t/test_a.py", "t/test_new.py", "t/test_lost.py"],
            stale=["t/test_a.py", "t/test_extra.py"],
            edited_tests=["t/test_new.py"], head="abc", at="now",
            changed_source=["p/src/b.py", "p/src/a.py"],
            graph={"survey": "s", "age_commits": 2},
            fresh_seconds=300.0, stale_seconds=5.0,
        )
        assert row["fresh_only"] == ["t/test_lost.py", "t/test_new.py"]
        assert row["fresh_only_unedited"] == ["t/test_lost.py"]
        assert row["stale_only"] == ["t/test_extra.py"]
        assert row["changed_source"] == ["p/src/a.py", "p/src/b.py"]
        assert row["graph"] == {"survey": "s", "age_commits": 2}
        assert (row["fresh_seconds"], row["stale_seconds"]) == (300.0, 5.0)

    def test_no_comparison_is_null_not_empty(self) -> None:
        """A stale walk that named nothing and a stale walk that never ran must
        not look alike: the second is no evidence at all."""
        none = stale_graph_row(fresh=["t/test_a.py"], stale=None, edited_tests=[],
                               head="abc", at="now", why_no_comparison="no older graph")
        empty = stale_graph_row(fresh=["t/test_a.py"], stale=[], edited_tests=[],
                                head="abc", at="now")
        assert none["stale"] is None and none["fresh_only_unedited"] is None
        assert none["why_no_comparison"] == "no older graph"
        assert empty["stale"] == [] and empty["fresh_only_unedited"] == ["t/test_a.py"]


class TestStaleGraphVerdict:
    def _row(self, head: str, lost=()) -> dict:
        return stale_graph_row(fresh=["t/test_a.py", *lost], stale=["t/test_a.py"],
                               edited_tests=[], head=head, at="now")

    def test_one_lost_test_file_disqualifies_whatever_else_agreed(self, tmp_path) -> None:
        log = tmp_path / "stale.jsonl"
        for n in range(STALE_GRAPH_MIN_COMMITS):
            append_stale_graph_observation(log, self._row(f"c{n}"))
        append_stale_graph_observation(log, self._row("bad", lost=["t/test_lost.py"]))
        ev = evaluate_stale_graph(load_observations(log))
        assert ev.verdict == "disqualified"
        assert ev.lost == (("bad", "t/test_lost.py"),)
        assert "bad" in ev.summary() and "t/test_lost.py" in ev.summary()

    def test_the_floor_counts_distinct_commits_that_were_compared(self) -> None:
        """Repeats on one commit and rows without a comparison add nothing."""
        rows = [self._row("same")] * 40
        rows += [stale_graph_row(fresh=["t/test_a.py"], stale=None, edited_tests=[],
                                 head=f"n{n}", at="now", why_no_comparison="x")
                 for n in range(40)]
        ev = evaluate_stale_graph(rows)
        assert (ev.observations, ev.compared, ev.commits) == (80, 40, 1)
        assert ev.verdict == "insufficient"

    def test_enough_agreeing_commits_meet_the_floor(self) -> None:
        rows = [self._row(f"c{n}") for n in range(STALE_GRAPH_MIN_COMMITS)]
        ev = evaluate_stale_graph(rows)
        assert ev.verdict == "met"
        assert "MET" in ev.summary()

    def test_a_compared_row_without_a_commit_is_not_a_commit(self) -> None:
        row = self._row("")
        ev = evaluate_stale_graph([row])
        assert (ev.compared, ev.commits) == (1, 0)


class TestRunRecords:
    def test_a_record_holds_the_run_as_it_was(self, tmp_path) -> None:
        log = tmp_path / "runs.jsonl"
        append_run_record(
            log, at="now", kind="explicit", head="abc", base="000",
            changed_files=["b.py", "a.py"], snapshot="d1g",
            ran_files={"t/test_b.py", "t/test_a.py"}, tests_ran=7,
            failed={"t/test_b.py::test_y", "t/test_a.py::test_x"}, exit_code=1,
        )
        (row,) = load_observations(log)
        assert row == {
            "at": "now", "kind": "explicit", "head": "abc", "base": "000",
            "changed_files": ["a.py", "b.py"], "snapshot": "d1g",
            "ran_files": ["t/test_a.py", "t/test_b.py"], "tests_ran": 7,
            "failed": ["t/test_a.py::test_x", "t/test_b.py::test_y"], "exit": 1,
        }

    def test_no_junit_is_null_not_nothing_failed(self, tmp_path) -> None:
        log = tmp_path / "runs.jsonl"
        append_run_record(log, at="now", kind="selected", head="abc", base="000",
                          changed_files=[], snapshot=None, ran_files=None,
                          tests_ran=None, failed=None, exit_code=None)
        append_run_record(log, at="now", kind="selected", head="abc", base="000",
                          changed_files=[], snapshot=None, ran_files=[],
                          tests_ran=0, failed=[], exit_code=5)
        blind, looked = load_observations(log)
        assert blind["failed"] is None and blind["ran_files"] is None
        assert looked["failed"] == [] and looked["ran_files"] == []

    def test_a_second_record_does_not_replace_the_first(self, tmp_path) -> None:
        log = tmp_path / "deep" / "runs.jsonl"
        for head in ("aaa", "bbb"):
            append_run_record(log, at="now", kind="full", head=head, base="000",
                              changed_files=[], snapshot=None, ran_files=[],
                              tests_ran=0, failed=[], exit_code=0)
        assert [r["head"] for r in load_observations(log)] == ["aaa", "bbb"]


class TestSnapshots:
    def test_a_snapshot_can_be_read_back_by_its_digest(self, tmp_path) -> None:
        patch = b"diff --git a/x b/x\n+new line\n"
        digest = store_snapshot(tmp_path / "snap", patch)
        assert len(digest) == 64
        with gzip.open(tmp_path / "snap" / f"{digest}.patch.gz", "rb") as fh:
            assert fh.read() == patch

    def test_the_same_change_is_stored_once(self, tmp_path) -> None:
        first = store_snapshot(tmp_path / "snap", b"same")
        stored = tmp_path / "snap" / f"{first}.patch.gz"
        stamp = stored.stat().st_mtime_ns
        assert store_snapshot(tmp_path / "snap", b"same") == first
        assert stored.stat().st_mtime_ns == stamp
        assert store_snapshot(tmp_path / "snap", b"other") != first
        assert len(list((tmp_path / "snap").iterdir())) == 2
