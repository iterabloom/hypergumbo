# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the pass-silence-reason axis (T6 / INV-bikaj).

The axis exists to split an undifferentiated population: a pass that emitted
no edges is one of "saw no input", "saw input and found nothing", or "never
really ran", and before this field a reader could not tell which.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun
from hypergumbo_core.multi_value_field_axis import _known_axes
from hypergumbo_core.pass_silence import (
    census_silence,
    emit_unaccounted_warning,
    format_unaccounted_warning,
    BACKEND_DISABLED,
    CANDIDATES_UNRESOLVED,
    DEPENDENCY_UNAVAILABLE,
    NO_CANDIDATE_CONSTRUCT,
    NO_CANDIDATE_FILES,
    PASS_CRASHED,
    PREREQUISITE_ABSENT,
    SILENCE_REASONS,
    UNREPORTED,
    all_pass_silence_reason_names,
    derive_silence_reason,
    emit_skip_summary,
    format_skip_summary,
    prerequisite_absent_clauses,
    summarize_skip_reasons,
)


class TestRegistry:
    def test_all_names_is_the_closed_vocabulary(self):
        assert all_pass_silence_reason_names() == SILENCE_REASONS
        assert all_pass_silence_reason_names() == frozenset({
            NO_CANDIDATE_FILES,
            NO_CANDIDATE_CONSTRUCT,
            CANDIDATES_UNRESOLVED,
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


class TestSilenceReasonForCandidates:
    """The PRODUCER helper. Both of its returns are positive claims.

    It used to return ``""`` on the non-empty branch — asserting nothing while
    holding the answer. That was the INV-bikaj complaint one level down, inside
    the axis built to cure it: a producer that computes a fact and discards it.
    """

    def test_no_candidates_claims_the_construct_is_absent(self):
        from hypergumbo_core.pass_silence import silence_reason_for_candidates

        assert silence_reason_for_candidates([]) == NO_CANDIDATE_CONSTRUCT

    def test_candidates_found_claims_they_went_unresolved(self):
        """Found candidates, carried none through => CANDIDATES_UNRESOLVED.

        Re-pointed, not deleted: this test previously pinned the ``""`` return
        and is the reason the change is visible. The pass found its construct;
        what failed is the RESOLUTION. Saying nothing here sent the run to
        ``unreported`` -- "did not say why" -- about a pass that could say why.
        """
        from hypergumbo_core.pass_silence import silence_reason_for_candidates

        assert silence_reason_for_candidates(["a candidate"]) == CANDIDATES_UNRESOLVED

    def test_the_two_returns_are_opposite_claims(self):
        """Neither branch is silence-about-silence; they disagree on purpose."""
        from hypergumbo_core.pass_silence import silence_reason_for_candidates

        assert (silence_reason_for_candidates([])
                != silence_reason_for_candidates([1]))
        assert "" not in (silence_reason_for_candidates([]),
                          silence_reason_for_candidates([1]))

    def test_accepts_any_sized_collection(self):
        from hypergumbo_core.pass_silence import silence_reason_for_candidates

        for empty, full in ((set(), {1}), ((), (1,)), ({}, {"k": 1}), ([], [1])):
            assert silence_reason_for_candidates(empty) == NO_CANDIDATE_CONSTRUCT
            assert silence_reason_for_candidates(full) == CANDIDATES_UNRESOLVED


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

    def _result(self, *, files, symbols, edges, reason=""):
        from hypergumbo_core.ir import PASS_VERSION, AnalysisRun

        run = AnalysisRun.create(pass_id="demo", version=PASS_VERSION)
        run.files_analyzed = files
        run.silence_reason = reason
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

    def test_body_supplied_reason_survives_the_stamp(self):
        """WI-finij question A: only a BODY can know it looked and found nothing.

        The orchestrator refuses to infer NO_CANDIDATE_CONSTRUCT, and rightly —
        inventing a reason on the producer's behalf is a fabricated disclosure.
        But the stamp assigned unconditionally, so a body that DID know was
        overwritten with the orchestrator's weaker guess. The declared value was
        therefore unreachable: a vocabulary entry with no possible producer.
        """
        runs = self._collect(self._result(
            files=42, symbols=[], edges=[], reason=NO_CANDIDATE_CONSTRUCT))
        assert runs[0]["silence_reason"] == NO_CANDIDATE_CONSTRUCT

    def test_body_reason_survives_even_a_zero_file_count(self):
        """A body that spoke outranks the orchestrator's NO_CANDIDATE_FILES."""
        runs = self._collect(self._result(
            files=0, symbols=[], edges=[], reason=NO_CANDIDATE_CONSTRUCT))
        assert runs[0]["silence_reason"] == NO_CANDIDATE_CONSTRUCT

    def test_candidates_unresolved_survives_to_the_output(self):
        """The value has a producer AND reaches the serialized run."""
        runs = self._collect(self._result(
            files=42, symbols=[], edges=[], reason=CANDIDATES_UNRESOLVED))
        assert runs[0]["silence_reason"] == CANDIDATES_UNRESOLVED

    def test_a_pass_that_emitted_is_never_silent_whatever_the_body_said(self):
        """`""` is NOT APPLICABLE and it is not the body's to override.

        A reason explains why a pass produced NOTHING. A pass that emitted has
        no silence to explain, so a stale or mistaken body value is cleared
        rather than published as a contradiction.
        """
        from hypergumbo_core.ir import Symbol

        sym = Symbol(id="s", name="n", kind="function", language="python",
                     path="f.py", span=(1, 1))
        runs = self._collect(self._result(
            files=3, symbols=[sym], edges=[], reason=NO_CANDIDATE_CONSTRUCT))
        assert runs[0].get("silence_reason", "") == ""


class TestLinkerChokepointStamping:
    """The linker chokepoint (_run_linker_with_cache) — every linker
    invocation flows through it, so it is the one locus that sees them all."""

    def _run(
        self, *, files, symbols, edges, reason="",
        # S107 false-positives on any "pass*" name; this is a pass identifier,
        # not a password (same carve-out ir.py's AnalysisRun.create carries).
        pass_id="demo-linker",  # noqa: S107
        skipped_pass_codes=None,
    ):
        from pathlib import Path

        from hypergumbo_core.ir import PASS_VERSION, AnalysisRun
        from hypergumbo_core.linkers.registry import (
            LinkerContext,
            LinkerResult,
            _run_linker_with_cache,
        )

        # The chokepoint resolves the running linker's depends_on out of
        # _LINKER_REGISTRY, which is populated by @register_linker IMPORT
        # SIDE-EFFECT. Without these the registry is empty, every lookup
        # misses, and every assertion below would pass for the wrong reason --
        # the same vacuous-control shape WI-jijor found in the catalog tests.
        import hypergumbo_core.linkers.pyffi
        import hypergumbo_core.linkers.tauri_ipc

        run = AnalysisRun.create(pass_id="demo-linker", version=PASS_VERSION)
        run.files_analyzed = files
        run.silence_reason = reason
        # A REAL LinkerContext, not a SimpleNamespace stand-in: the chokepoint
        # now reads two more fields off it, and a hand-rolled double is exactly
        # the kind of fake that passes while the production shape drifts.
        ctx = LinkerContext(
            repo_root=Path("."),
            linker_pass_id=pass_id,
            skipped_pass_codes=dict(skipped_pass_codes or {}),
        )
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

    def test_body_supplied_reason_survives_the_stamp(self):
        """Same guard at the linker chokepoint — both sites clobbered."""
        assert self._run(files=339, symbols=[], edges=[],
                         reason=NO_CANDIDATE_CONSTRUCT
                         ).silence_reason == NO_CANDIDATE_CONSTRUCT

    def test_body_reason_survives_even_a_zero_file_count(self):
        assert self._run(files=0, symbols=[], edges=[],
                         reason=NO_CANDIDATE_CONSTRUCT
                         ).silence_reason == NO_CANDIDATE_CONSTRUCT

    def test_a_linker_that_emitted_is_never_silent_whatever_the_body_said(self):
        from hypergumbo_core.ir import Edge

        edge = Edge(id="e", src="a", dst="b", edge_type="calls", line=1,
                    origin="demo-linker", origin_run_id="uuid:test")
        assert self._run(files=9, symbols=[], edges=[edge],
                         reason=NO_CANDIDATE_CONSTRUCT).silence_reason == ""

    def test_blocked_prerequisite_replaces_the_DERIVED_reason(self):
        """WI-dabup: the headline. no_candidate_files here is a FALSE claim.

        tauri-ipc-linker declares [["javascript"], ["rust"]]. On a JS+Rust repo
        whose Rust grammar is missing it reads zero files and the orchestrator
        derives no_candidate_files -- "nothing to find, and no ordering or
        declaration mechanism would change it". Installing the grammar changes
        it. prerequisite_absent is the true answer and it is actionable.
        """
        assert self._run(
            files=0, symbols=[], edges=[],
            pass_id="tauri-ipc-linker",
            skipped_pass_codes={"rust": DEPENDENCY_UNAVAILABLE},
        ).silence_reason == PREREQUISITE_ABSENT

    def test_prerequisite_absent_does_NOT_override_a_BODY_claim(self):
        """The #1008 guard is preserved, and this is not a formality.

        Measured on the same repository, pyffi-linker is silent with the SAME
        blocked conjunct and its body truthfully claims no_candidate_construct:
        it scanned the Python side and there are genuinely no FFI call sites,
        so the missing Rust grammar is irrelevant to ITS silence. Overruling it
        would replace a true claim with a plausible one.
        """
        assert self._run(
            files=12, symbols=[], edges=[], reason=NO_CANDIDATE_CONSTRUCT,
            pass_id="pyffi-linker",
            skipped_pass_codes={"c": NO_CANDIDATE_FILES,
                                "cpp": NO_CANDIDATE_FILES,
                                "rust": DEPENDENCY_UNAVAILABLE},
        ).silence_reason == NO_CANDIDATE_CONSTRUCT

    def test_a_prerequisite_absent_for_a_FILE_reason_is_not_stamped(self):
        """State A stays State A. The repo simply has no Rust."""
        assert self._run(
            files=0, symbols=[], edges=[],
            pass_id="tauri-ipc-linker",
            skipped_pass_codes={"rust": NO_CANDIDATE_FILES},
        ).silence_reason == NO_CANDIDATE_FILES

    def test_a_productive_linker_is_never_stamped_prerequisite_absent(self):
        """It emitted. There is no silence to explain, blocked or not."""
        from hypergumbo_core.ir import Edge

        edge = Edge(id="e", src="a", dst="b", edge_type="calls", line=1,
                    origin="demo-linker", origin_run_id="uuid:test")
        assert self._run(
            files=4, symbols=[], edges=[edge],
            pass_id="tauri-ipc-linker",
            skipped_pass_codes={"rust": DEPENDENCY_UNAVAILABLE},
        ).silence_reason == ""

    def test_unknown_pass_id_falls_through_to_the_derived_reason(self):
        """The by-name run_linker path sets no pass id; no declaration, no stamp."""
        assert self._run(
            files=0, symbols=[], edges=[],
            pass_id="",
            skipped_pass_codes={"rust": DEPENDENCY_UNAVAILABLE},
        ).silence_reason == NO_CANDIDATE_FILES

    def test_no_skip_map_falls_through(self):
        """A caller with no Limits sink cannot be blocked by what it cannot see."""
        assert self._run(
            files=0, symbols=[], edges=[], pass_id="tauri-ipc-linker",
        ).silence_reason == NO_CANDIDATE_FILES

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


class TestSummarizeSkipReasons:
    """WI-dukoh / ADR-0056 W2: skipped_passes gets a structured code AND a reader.

    ``skipped_passes`` has been serialized since the beginning and **nothing
    has ever summarized it**. Landing a structured code with no reader would
    repeat the defect this whole arc is about (LIVE.md §1.7).
    """

    def test_empty_gives_empty_counts(self) -> None:
        assert summarize_skip_reasons([]) == {}

    def test_counts_by_code(self) -> None:
        entries = [
            {"pass": "a", "reason": "no files matched",
             "silence_reason": NO_CANDIDATE_FILES},
            {"pass": "b", "reason": "no files matched",
             "silence_reason": NO_CANDIDATE_FILES},
            {"pass": "c", "reason": "rust-analyzer backend not enabled",
             "silence_reason": BACKEND_DISABLED},
        ]
        assert summarize_skip_reasons(entries) == {
            NO_CANDIDATE_FILES: 2, BACKEND_DISABLED: 1,
        }

    def test_entry_without_a_code_counts_as_unreported(self) -> None:
        """ABSENT is not EMPTY and it is not ``no_candidate_files`` either.

        A producer that did not classify its own skip has NOT said "the repo
        lacks the files" — it has said nothing. Bucketing a missing code under
        the 98.94%-common value would manufacture the majority answer for
        every unconverted producer, which is the absent-versus-empty
        substitution this axis exists to cure.
        """
        assert summarize_skip_reasons([{"pass": "a", "reason": "?"}]) == {
            UNREPORTED: 1,
        }

    def test_value_outside_the_vocabulary_is_counted_under_its_own_name(self) -> None:
        """A drifted producer must not be laundered into a clean count."""
        assert summarize_skip_reasons(
            [{"pass": "a", "silence_reason": "invented_value"}],
        ) == {"invented_value": 1}


class TestFormatSkipSummary:
    def test_none_when_nothing_was_skipped(self) -> None:
        assert format_skip_summary({}, total_passes=10) is None

    def test_orders_most_common_first_then_alphabetically(self) -> None:
        line = format_skip_summary(
            {BACKEND_DISABLED: 1, NO_CANDIDATE_FILES: 5, DEPENDENCY_UNAVAILABLE: 1},
            total_passes=20,
        )
        assert line is not None
        assert line.index("no_candidate_files=5") < line.index("backend_disabled=1")
        assert line.index("backend_disabled=1") < line.index("dependency_unavailable=1")

    def test_names_the_field_to_read_for_detail(self) -> None:
        line = format_skip_summary({NO_CANDIDATE_FILES: 1}, total_passes=2)
        assert line is not None
        assert "limits.skipped_passes" in line


class TestEmitSkipSummary:
    def test_silent_when_nothing_skipped(self, capsys) -> None:
        emit_skip_summary([])
        assert capsys.readouterr().err == ""

    def test_one_line_to_stderr(self, capsys) -> None:
        emit_skip_summary([
            {"pass": "a", "reason": "x", "silence_reason": DEPENDENCY_UNAVAILABLE},
        ])
        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert "dependency_unavailable=1" in err


class TestPrerequisiteAbsentClauses:
    """WI-dabup / ADR-0056 W3: the producer `prerequisite_absent` never had.

    The discriminator is NOT "is the conjunct satisfied" — 98.94% of skip
    records are `no files matched`, so an unsatisfied conjunct nearly always
    means the repository lacks that language, which is State A and a correct
    no-op. Stamping THAT as an ordering defect would be the axis's founding sin
    committed inside the axis built to cure it. The discriminator is "was the
    missing literal skipped for a FILE reason or a TOOLCHAIN reason", which is
    exactly what W2's structured code made askable.
    """

    def test_no_declarations_gives_nothing(self) -> None:
        assert prerequisite_absent_clauses([], {}) == []

    def test_satisfied_clause_is_not_reported(self) -> None:
        """A literal that RAN satisfies the clause, whatever it produced."""
        assert prerequisite_absent_clauses(
            [["rust"]], {"c": "no_candidate_files"},
        ) == []

    def test_clause_missing_for_a_FILE_reason_is_not_reported(self) -> None:
        """State A. The repo has no Rust; nothing is out of order.

        This is the case that makes the naive "unsatisfied conjunct" rule
        wrong, and it is 98.94% of all skip records.
        """
        assert prerequisite_absent_clauses(
            [["rust"]], {"rust": NO_CANDIDATE_FILES},
        ) == []

    def test_clause_missing_for_a_TOOLCHAIN_reason_IS_reported(self) -> None:
        """State C. The repo HAS Rust; the grammar is not installed."""
        assert prerequisite_absent_clauses(
            [["rust"]], {"rust": DEPENDENCY_UNAVAILABLE},
        ) == [["rust"]]

    def test_backend_disabled_also_counts_as_a_toolchain_reason(self) -> None:
        assert prerequisite_absent_clauses(
            [["rust"]], {"rust": BACKEND_DISABLED},
        ) == [["rust"]]

    def test_crashed_prerequisite_counts(self) -> None:
        assert prerequisite_absent_clauses(
            [["go"]], {"go": PASS_CRASHED},
        ) == [["go"]]

    def test_unclassified_prerequisite_counts(self) -> None:
        """`unreported` is not `no_candidate_files`.

        A producer that did not classify itself has NOT said the repo lacked
        the files, so this cannot be waved through as State A. Reporting it is
        the conservative direction: it surfaces a pass whose prerequisite went
        missing for a reason nobody recorded.
        """
        assert prerequisite_absent_clauses(
            [["rust"]], {"rust": UNREPORTED},
        ) == [["rust"]]

    def test_or_clause_needs_EVERY_literal_missing(self) -> None:
        """One surviving member satisfies the disjunction."""
        assert prerequisite_absent_clauses(
            [["c", "cpp", "rust"]], {"c": NO_CANDIDATE_FILES,
                                     "rust": DEPENDENCY_UNAVAILABLE},
        ) == []

    def test_or_clause_with_one_toolchain_member_among_file_members(self) -> None:
        """All missing, and at least one for a toolchain reason -> State C.

        The mixed case is the realistic one: a bridge linker wants c OR cpp OR
        rust, the repo has no C at all, and the Rust grammar is missing. The
        pass could have worked and did not.
        """
        assert prerequisite_absent_clauses(
            [["c", "cpp", "rust"]],
            {"c": NO_CANDIDATE_FILES, "cpp": NO_CANDIDATE_FILES,
             "rust": DEPENDENCY_UNAVAILABLE},
        ) == [["c", "cpp", "rust"]]

    def test_all_members_missing_for_file_reasons_is_still_State_A(self) -> None:
        assert prerequisite_absent_clauses(
            [["c", "cpp"]],
            {"c": NO_CANDIDATE_FILES, "cpp": NO_CANDIDATE_FILES},
        ) == []

    def test_only_the_offending_conjunct_is_returned(self) -> None:
        """A satisfied conjunct alongside a blocked one is not reported."""
        assert prerequisite_absent_clauses(
            [["javascript"], ["rust"]],
            {"rust": DEPENDENCY_UNAVAILABLE},
        ) == [["rust"]]

    def test_empty_clause_is_skipped(self) -> None:
        """A vacuous conjunct blocks nothing and must not report."""
        assert prerequisite_absent_clauses([[]], {"rust": PASS_CRASHED}) == []

    def test_a_linker_literal_is_never_blocking(self) -> None:
        """Only ANALYZER passes appear in skipped_passes.

        The spec is explicit that non-analyzer passes are not enumerated there
        ("a linker with no applicable targets is a correct no-op, not a pass
        that did not run"), so a clause naming a LINKER can never be seen as
        missing. That reads as satisfied, which is the conservative direction:
        it under-reports rather than inventing an ordering defect from a
        population this channel does not cover.
        """
        assert prerequisite_absent_clauses(
            [["inheritance-linker"]], {"rust": DEPENDENCY_UNAVAILABLE},
        ) == []


class TestSilenceCensus:
    """WI-mamiv: the two hosts have never had a reader that unions them.

    Measured on component-model-demo: **130 of 162 passes carry
    ``no_candidate_files``** — 48 through ``AnalysisRun.silence_reason`` and 82
    through ``limits.skipped_passes[].silence_reason``. Which host a pass
    lands in is decided by ``taxonomy.LANGUAGE_EXTENSIONS`` membership and by
    whether the analyzer returns a bare ``AnalysisResult()`` or a run with
    ``files_analyzed=0`` — producer registration and implementation detail,
    not a property of the silence.

    A consumer asking "which passes had no input?" therefore has to know about
    the WI-jadig pre-filter to get a right answer, and the two stderr lines
    report ``64 of 79`` and ``83 of 162`` — different denominators over
    overlapping populations, whose sum is not the answer.
    """

    def _runs(self, *pairs):
        return [{"pass": p, "silence_reason": r} for p, r in pairs]

    def _skips(self, *pairs):
        return [{"pass": p, "silence_reason": c} for p, c in pairs]

    def test_it_unions_the_two_hosts(self) -> None:
        census = census_silence(
            self._runs(("apex", NO_CANDIDATE_FILES), ("twig", NO_CANDIDATE_FILES)),
            self._skips(("java", NO_CANDIDATE_FILES), ("ruby", DEPENDENCY_UNAVAILABLE)),
        )
        assert census.by_pass == {
            "apex": NO_CANDIDATE_FILES,
            "twig": NO_CANDIDATE_FILES,
            "java": NO_CANDIDATE_FILES,
            "ruby": DEPENDENCY_UNAVAILABLE,
        }
        assert census.count_of(NO_CANDIDATE_FILES) == 3

    def test_it_records_which_host_each_answer_came_from(self) -> None:
        """The host is still a fact worth keeping — it just isn't the ANSWER."""
        census = census_silence(
            self._runs(("apex", NO_CANDIDATE_FILES)),
            self._skips(("java", NO_CANDIDATE_FILES)),
        )
        assert census.hosts == {
            "apex": "analysis_runs", "java": "skipped_passes",
        }

    def test_a_pass_that_emitted_is_not_in_the_census(self) -> None:
        """``""`` is NOT APPLICABLE, not an unknown reason (ADR-0056)."""
        census = census_silence(self._runs(("python", "")), [])
        assert census.by_pass == {}

    def test_a_skip_with_no_code_reads_as_unreported(self) -> None:
        """Matching summarize_skip_reasons: absence on the skip host means the
        producer did not classify itself, never the 98.94%-common value."""
        census = census_silence([], [{"pass": "lean"}])
        assert census.by_pass == {"lean": UNREPORTED}

    def test_a_catalog_pass_in_NEITHER_host_is_unaccounted(self) -> None:
        """The third outcome, which the audit missed and WI-didag found.

        ``rust_analyzer``'s success path returned output with no run, so it
        appeared in neither list — the only one of 118 registered analyzers
        for which that was true. A union built over ``runs + skips`` alone
        cannot see it, which is exactly why this helper takes the catalog.
        """
        census = census_silence(
            self._runs(("python", "")),
            self._skips(("java", NO_CANDIDATE_FILES)),
            catalog_pass_ids=("python", "java", "rust_analyzer"),
        )
        assert census.unaccounted == ("rust_analyzer",)

    def test_no_catalog_means_no_unaccounted_claim(self) -> None:
        """ABSENT != EMPTY: without the catalog the helper cannot know, and
        must not report an empty tuple as if it had checked."""
        census = census_silence(self._runs(("python", "")), [])
        assert census.unaccounted is None


class TestUnaccountedReaderIsWiredUp:
    """An instrument with no reader is the same organic criterion in a costume.

    ``unaccounted`` is only worth computing if something prints it in the mode
    the tool actually runs. This pins the wire-up, not just the function —
    WI-didag sat unnoticed for a month precisely because nothing read the
    signal that would have shown it.
    """

    def test_the_warning_names_the_offending_passes(self) -> None:
        census = census_silence(
            [{"pass": "python", "silence_reason": ""}],
            [{"pass": "java", "silence_reason": NO_CANDIDATE_FILES}],
            catalog_pass_ids=("python", "java", "rust_analyzer"),
        )
        line = format_unaccounted_warning(census)
        assert line is not None
        assert "rust_analyzer" in line

    def test_silent_when_every_catalogue_pass_is_accounted_for(self) -> None:
        census = census_silence(
            [], [{"pass": "java", "silence_reason": NO_CANDIDATE_FILES}],
            catalog_pass_ids=("java",),
        )
        assert format_unaccounted_warning(census) is None

    def test_silent_when_it_could_not_check(self) -> None:
        """``None`` unaccounted must print nothing — it is CANNOT-DETERMINE,
        and a line claiming all-clear would be the absent-vs-empty defect."""
        census = census_silence([], [])
        assert census.unaccounted is None
        assert format_unaccounted_warning(census) is None

    def test_the_cli_calls_the_reader(self) -> None:
        """The wire-up itself, by AST — a grep would pass on a docstring."""
        import ast
        import pathlib
        src = pathlib.Path(
            "packages/hypergumbo-core/src/hypergumbo_core/cli.py"
        ).read_text()
        called = {
            node.func.id
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "emit_unaccounted_warning" in called, (
            "cli.py must CALL emit_unaccounted_warning; importing it is not "
            "a reader"
        )

    def test_a_pass_that_EMITTED_is_accounted_for(self) -> None:
        """Presence is not the same question as silence, and the first draft
        of ``census_silence`` got this wrong: it built ``unaccounted`` from the
        passes that had a REASON, so a pass that emitted — which has an
        analysis_runs row and correctly no reason — was reported as missing
        from the catalog. That is the same absent-vs-empty conflation this
        module exists to stop, committed inside the helper written to expose
        it."""
        census = census_silence(
            [{"pass": "python", "silence_reason": ""}],
            [],
            catalog_pass_ids=("python",),
        )
        assert census.by_pass == {}, "it emitted — no silence to explain"
        assert census.unaccounted == (), "but it IS accounted for"

    def test_a_row_with_no_pass_id_is_skipped_on_both_hosts(self) -> None:
        """A malformed row must not poison the census.

        Neither host's rows are schema-guaranteed to carry ``pass`` — the
        skipped_passes entries are a bare ``List[Dict[str, str]]`` built at
        four separate producer sites — so an id-less row is dropped rather
        than keyed under ``""``, which would collide every malformed row onto
        one phantom pass.
        """
        census = census_silence(
            [{"silence_reason": NO_CANDIDATE_FILES}],
            [{"silence_reason": NO_CANDIDATE_FILES}],
        )
        assert census.by_pass == {}
        assert census.hosts == {}

    def test_an_id_less_row_does_not_mask_a_catalogue_gap(self) -> None:
        """The dangerous version of the above: if an id-less row counted as
        'present', it could silently account for a pass that never ran."""
        census = census_silence(
            [{"silence_reason": NO_CANDIDATE_FILES}], [],
            catalog_pass_ids=("rust_analyzer",),
        )
        assert census.unaccounted == ("rust_analyzer",)

    def test_emit_writes_the_line_to_stderr(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        emit_unaccounted_warning(census_silence(
            [], [], catalog_pass_ids=("rust_analyzer",),
        ))
        assert "rust_analyzer" in capsys.readouterr().err

    def test_emit_is_silent_when_nothing_is_due(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        emit_unaccounted_warning(census_silence([], []))
        assert capsys.readouterr().err == ""


class TestOneFactOneName:
    """WI-mamiv verdict A: both hosts spell the axis field ``silence_reason``.

    The axis was reachable under two names — ``AnalysisRun.silence_reason`` and
    ``limits.skipped_passes[].skip_reason_code`` — and which name a consumer
    saw was decided by taxonomy membership and the analyzer's return style,
    never by anything about the silence. The ROUTING is unchanged and is still
    not a function of the fact; what changed is that asking the question no
    longer requires knowing which host will answer.
    """

    def test_a_skip_entry_carries_silence_reason_and_not_the_old_key(
        self, tmp_path: Path
    ) -> None:
        """Production path — the real dispatcher on a real (empty) repo."""
        from hypergumbo_core.analyze.all_analyzers import run_all_analyzers
        from hypergumbo_core.profile import detect_profile

        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        profile = detect_profile(tmp_path, count_loc=True).to_dict()
        _runs, _syms, _edges, _ucs, limits, _cap, _dep = run_all_analyzers(
            tmp_path, profile=profile
        )

        assert limits.skipped_passes, "fixture should skip most of the catalogue"
        for entry in limits.skipped_passes:
            assert "silence_reason" in entry, (
                f"skip entry for {entry.get('pass')!r} carries no silence_reason"
            )
            assert "skip_reason_code" not in entry, (
                f"skip entry for {entry.get('pass')!r} still carries the old "
                "skip_reason_code key; the axis is back to two names"
            )

    def test_both_hosts_use_the_same_serialized_key(self) -> None:
        """The AnalysisRun half spells it the same way, so a union is on one key."""
        from hypergumbo_core.ir import PASS_VERSION, make_pass_id

        run = AnalysisRun.create(pass_id=make_pass_id("probe"), version=PASS_VERSION)
        run.silence_reason = NO_CANDIDATE_FILES
        assert run.to_dict()["silence_reason"] == NO_CANDIDATE_FILES

        skip_entry = {
            "pass": "probe", "reason": "no files matched",
            "silence_reason": NO_CANDIDATE_FILES,
        }
        shared = set(run.to_dict()) & set(skip_entry)
        assert "silence_reason" in shared, (
            "the two hosts must agree on the key name for census_silence to "
            "union them without a per-host alias"
        )
