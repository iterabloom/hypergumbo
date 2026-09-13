# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the pass-silence-reason axis (T6 / INV-bikaj).

The axis exists to split an undifferentiated population: a pass that emitted
no edges is one of "saw no input", "saw input and found nothing", or "never
really ran", and before this field a reader could not tell which.
"""
import pytest

from hypergumbo_core.ir import AnalysisRun
from hypergumbo_core.multi_value_field_axis import _known_axes
from hypergumbo_core.pass_silence import (
    BACKEND_DISABLED,
    DEPENDENCY_UNAVAILABLE,
    NO_CANDIDATE_CONSTRUCT,
    NO_CANDIDATE_FILES,
    PASS_CRASHED,
    PREREQUISITE_ABSENT,
    SILENCE_REASONS,
    UNREPORTED,
    all_pass_silence_reason_names,
    derive_silence_reason,
)


class TestRegistry:
    def test_all_names_is_the_closed_vocabulary(self):
        assert all_pass_silence_reason_names() == SILENCE_REASONS
        assert all_pass_silence_reason_names() == frozenset({
            NO_CANDIDATE_FILES,
            NO_CANDIDATE_CONSTRUCT,
            DEPENDENCY_UNAVAILABLE,
            BACKEND_DISABLED,
            PREREQUISITE_ABSENT,
            PASS_CRASHED,
            UNREPORTED,
        })

    def test_empty_string_is_not_a_member(self):
        """'' means NOT APPLICABLE and is deliberately outside the vocabulary."""
        assert "" not in all_pass_silence_reason_names()

    def test_axis_is_wired_into_the_linter(self):
        axes = _known_axes()
        assert "pass-silence-reason" in axes
        assert axes["pass-silence-reason"]() == SILENCE_REASONS


class TestDeriveSilenceReason:
    def test_a_pass_that_emitted_edges_is_not_silent(self):
        assert derive_silence_reason(
            files_analyzed=10, nodes_emitted=0, edges_emitted=3) == ""

    def test_a_pass_that_emitted_only_nodes_is_not_silent(self):
        """A node-only pass is not silent at all — 26 of the 48 in the sizing."""
        assert derive_silence_reason(
            files_analyzed=10, nodes_emitted=5, edges_emitted=0) == ""

    def test_zero_files_is_no_candidate_files(self):
        assert derive_silence_reason(
            files_analyzed=0, nodes_emitted=0, edges_emitted=0
        ) == NO_CANDIDATE_FILES

    def test_saw_files_and_said_nothing_is_unreported_not_no_candidate_files(self):
        """THE population T6 exists to explain. Claiming 'no candidate files'
        here would be the field lying about exactly those pass-runs."""
        assert derive_silence_reason(
            files_analyzed=120, nodes_emitted=0, edges_emitted=0
        ) == UNREPORTED

    @pytest.mark.parametrize("files", [1, 2, 991])
    def test_any_nonzero_file_count_reaches_unreported(self, files):
        assert derive_silence_reason(
            files_analyzed=files, nodes_emitted=0, edges_emitted=0
        ) == UNREPORTED

    def test_every_derived_value_is_in_the_vocabulary(self):
        for files in (0, 1, 50):
            got = derive_silence_reason(
                files_analyzed=files, nodes_emitted=0, edges_emitted=0)
            assert got in all_pass_silence_reason_names()


class TestAnalysisRunField:
    def test_defaults_to_empty(self):
        run = AnalysisRun(execution_id="e", pass_id="p", version="1")
        assert run.silence_reason == ""

    def test_to_dict_omits_when_empty(self):
        """INV-virik: absence reads as 'nothing to report', not a misleading
        always-present empty value on every one of the many runs."""
        run = AnalysisRun(execution_id="e", pass_id="p", version="1")
        assert "silence_reason" not in run.to_dict()

    def test_to_dict_includes_when_set(self):
        run = AnalysisRun(execution_id="e", pass_id="p", version="1")
        run.silence_reason = NO_CANDIDATE_FILES
        assert run.to_dict()["silence_reason"] == NO_CANDIDATE_FILES


class TestAnalyzerChokepointStamping:
    """The analyzer chokepoint (all_analyzers.collect_analyzer_result)."""

    def _result(self, *, files, symbols, edges):
        from hypergumbo_core.ir import PASS_VERSION, AnalysisRun

        run = AnalysisRun.create(pass_id="demo", version=PASS_VERSION)
        run.files_analyzed = files
        return type("R", (), {
            "run": run, "symbols": symbols, "edges": edges,
            "usage_contexts": [], "skipped": False, "skip_reason": "",
        })()

    def _collect(self, result):
        from hypergumbo_core.analyze.all_analyzers import collect_analyzer_result
        from hypergumbo_core.limits import Limits

        runs: list = []
        collect_analyzer_result(
            result, runs, [], [], [], Limits(), analyzer_name="demo")
        return runs

    def test_productive_analyzer_is_not_stamped(self):
        from hypergumbo_core.ir import Symbol

        sym = Symbol(id="s", name="n", kind="function", language="python",
                     path="f.py", span=(1, 1))
        runs = self._collect(self._result(files=3, symbols=[sym], edges=[]))
        assert runs[0].get("silence_reason", "") == ""

    def test_analyzer_with_no_files_is_no_candidate_files(self):
        runs = self._collect(self._result(files=0, symbols=[], edges=[]))
        assert runs[0]["silence_reason"] == NO_CANDIDATE_FILES

    def test_analyzer_that_read_files_and_said_nothing_is_unreported(self):
        runs = self._collect(self._result(files=42, symbols=[], edges=[]))
        assert runs[0]["silence_reason"] == UNREPORTED


class TestLinkerChokepointStamping:
    """The linker chokepoint (_run_linker_with_cache) — every linker
    invocation flows through it, so it is the one locus that sees them all."""

    def _run(self, *, files, symbols, edges):
        import types

        from hypergumbo_core.ir import PASS_VERSION, AnalysisRun
        from hypergumbo_core.linkers.registry import (
            LinkerResult,
            _run_linker_with_cache,
        )

        run = AnalysisRun.create(pass_id="demo-linker", version=PASS_VERSION)
        run.files_analyzed = files
        ctx = types.SimpleNamespace(parsed_trees={})
        result = _run_linker_with_cache(
            lambda _c: LinkerResult(symbols=symbols, edges=edges, run=run), ctx)
        return result.run

    def test_productive_linker_is_not_stamped(self):
        from hypergumbo_core.ir import Edge

        edge = Edge(id="e", src="a", dst="b", edge_type="calls", line=1,
                    origin="demo-linker", origin_run_id="uuid:test")
        assert self._run(files=9, symbols=[], edges=[edge]).silence_reason == ""

    def test_linker_with_no_files_is_no_candidate_files(self):
        assert self._run(
            files=0, symbols=[], edges=[]).silence_reason == NO_CANDIDATE_FILES

    def test_silent_linker_that_read_files_is_unreported(self):
        """The 22 truly-silent pass-runs from the sizing land HERE, and they
        must NOT be labelled no_candidate_files — they saw files."""
        assert self._run(
            files=339, symbols=[], edges=[]).silence_reason == UNREPORTED

    def test_a_linker_without_a_run_does_not_crash_the_wrapper(self):
        import types

        from hypergumbo_core.linkers.registry import (
            LinkerResult,
            _run_linker_with_cache,
        )

        ctx = types.SimpleNamespace(parsed_trees={})
        result = _run_linker_with_cache(lambda _c: LinkerResult(run=None), ctx)
        assert result.run is None


class TestSummariseSilence:
    """The CONSUMER side. A disclosure nothing reads is not one — the field
    shipped stamped but unread, and this is the half that makes it observable."""

    def _run(self, reason, **kw):
        d = {"pass": kw.get("name", "p"), "files_analyzed": 1,
             "nodes_emitted": 0, "edges_emitted": 0}
        if reason:
            d["silence_reason"] = reason
        return d

    def test_empty_input_is_empty_summary(self):
        from hypergumbo_core.pass_silence import summarize_silence
        assert summarize_silence([]) == {}

    def test_counts_by_reason(self):
        from hypergumbo_core.pass_silence import summarize_silence
        runs = [self._run(NO_CANDIDATE_FILES), self._run(NO_CANDIDATE_FILES),
                self._run(UNREPORTED)]
        assert summarize_silence(runs) == {NO_CANDIDATE_FILES: 2, UNREPORTED: 1}

    def test_productive_runs_are_not_counted(self):
        """A run with no silence_reason key emitted something — NOT APPLICABLE,
        not an unknown reason."""
        from hypergumbo_core.pass_silence import summarize_silence
        assert summarize_silence([self._run(None), self._run(None)]) == {}

    def test_unknown_reason_is_kept_not_dropped(self):
        """A value outside the vocabulary must not vanish silently — dropping it
        would make a drifted producer look like a clean run."""
        from hypergumbo_core.pass_silence import summarize_silence
        assert summarize_silence([self._run("something_new")]) == {"something_new": 1}


class TestFormatSilenceSummary:
    def test_returns_none_when_nothing_was_silent(self):
        """Silent on a clean corpus, mirroring spec_validator.emit_stderr_summary."""
        from hypergumbo_core.pass_silence import format_silence_summary
        assert format_silence_summary({}, total_passes=10) is None

    def test_line_names_the_totals_and_the_remaining_work(self):
        from hypergumbo_core.pass_silence import format_silence_summary
        line = format_silence_summary(
            {NO_CANDIDATE_FILES: 52, UNREPORTED: 5}, total_passes=78)
        assert line is not None
        assert "57 of 78" in line          # silent / total
        assert "no_candidate_files=52" in line
        assert "unreported=5" in line
        assert "silence_reason" in line    # tells the reader where to look
        assert "\n" not in line            # one line, like the [warn] convention

    def test_reasons_are_ordered_most_common_first(self):
        from hypergumbo_core.pass_silence import format_silence_summary
        line = format_silence_summary(
            {UNREPORTED: 2, NO_CANDIDATE_FILES: 9}, total_passes=20)
        assert line.index("no_candidate_files") < line.index("unreported")

    def test_ties_break_alphabetically_so_the_line_is_deterministic(self):
        from hypergumbo_core.pass_silence import format_silence_summary
        a = format_silence_summary({UNREPORTED: 3, NO_CANDIDATE_FILES: 3}, total_passes=9)
        b = format_silence_summary({NO_CANDIDATE_FILES: 3, UNREPORTED: 3}, total_passes=9)
        assert a == b


class TestEmitSilenceSummary:
    def test_writes_one_line_to_stderr(self, capsys):
        from hypergumbo_core.pass_silence import emit_silence_summary
        runs = [{"pass": "x", "silence_reason": UNREPORTED},
                {"pass": "y", "silence_reason": NO_CANDIDATE_FILES},
                {"pass": "z"}]
        emit_silence_summary(runs)
        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert "2 of 3" in err
        assert "unreported=1" in err

    def test_silent_when_every_pass_emitted(self, capsys):
        from hypergumbo_core.pass_silence import emit_silence_summary
        emit_silence_summary([{"pass": "x"}, {"pass": "y"}])
        assert capsys.readouterr().err == ""

    def test_silent_on_no_passes_at_all(self, capsys):
        from hypergumbo_core.pass_silence import emit_silence_summary
        emit_silence_summary([])
        assert capsys.readouterr().err == ""
