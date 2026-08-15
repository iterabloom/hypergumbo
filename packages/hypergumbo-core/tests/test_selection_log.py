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

from hypergumbo_core.selection_log import (
    DEFAULT_MIN_COMMITS,
    append_observation,
    evaluate,
    load_observations,
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
