# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the published data-flow coverage scope (INV-karud clause a3).

The clause requires that "the scope of data-flow coverage may not be left to
assumption". These tests pin two things that are easy to get silently wrong:

* the capability bits are read from PRODUCTION registries, not re-derived here
  (L53 — when a production classification exists for the thing you are
  counting, counting it yourself IS the bug); and
* the registry is populated at the moment the scope is computed. The def/use
  extractors register as an *import side effect*, so a scope computed before
  that import reports every language incapable — a clean, plausible, entirely
  wrong table. ``test_production_path_reports_the_capable_languages`` is the
  non-vacuity floor for exactly that failure (L17).
"""
import pytest

from hypergumbo_core.dataflow_scope import (
    COVERAGE_GRANULARITY,
    INCLUSION_DECIDED_BY,
    LanguageDataflowScope,
    compute_dataflow_scope,
    count_walk_verdicts,
    dataflow_scope_dict,
    render_dataflow_scope_text,
)
from hypergumbo_core.taint import (
    WALK_VERDICTS,
    TaintFlowFinding,
    load_builtin_taint_catalog,
)


def _finding(verdict: str, values: tuple = ()) -> TaintFlowFinding:
    """A real ``TaintFlowFinding``, not a stand-in.

    L53: when a production classification exists for the thing you are
    counting, counting it yourself IS the bug. ``__post_init__`` derives
    ``walk_verdict_values`` from the scalar when the tuple is empty, and that
    derivation is precisely what the mixed-row test is about -- a hand-rolled
    stub would let the test pass while production disagreed.
    """
    return TaintFlowFinding(
        taint_label="untrusted_input",
        source_symbol="s", source_primitive="p",
        sink_symbol="k", sink_primitive="q",
        sink_zone="z", sanitized=False,
        confidence="approximate", analysis_method="ddg_mixed",
        walk_verdict=verdict, walk_verdict_values=values,
    )


def _row(**kw) -> LanguageDataflowScope:
    base = {
        "language": "x",
        "catalog_sources": 1,
        "catalog_sinks": 1,
        "catalog_sanitizers": 0,
        "cfg_mapping": True,
        "atomic_statement": True,
        "def_use_extractor": True,
        "ddg_spec": True,
    }
    base.update(kw)
    return LanguageDataflowScope(**base)


class TestCapability:
    def test_all_four_bits_are_required(self) -> None:
        assert _row().dataflow_capable

    @pytest.mark.parametrize(
        "missing",
        ["cfg_mapping", "atomic_statement", "def_use_extractor", "ddg_spec"],
    )
    def test_any_missing_bit_disqualifies(self, missing: str) -> None:
        """Each bit is independently sufficient to keep the walk inert.

        This is not defensive parametrisation. Rust shipped an extractor in
        March and produced zero DDG edges for months against THREE of these
        four simultaneously; a table that treated any one of them as optional
        would have reported it capable the whole time.
        """
        row = _row(**{missing: False})
        assert not row.dataflow_capable
        assert missing in row.blockers

    def test_a_capable_language_lists_no_blockers(self) -> None:
        assert _row().blockers == ()


class TestComputeFromProduction:
    def test_production_path_reports_the_capable_languages(self) -> None:
        """NON-VACUITY FLOOR. The four registered languages must read capable.

        If this fails with everything incapable, the def/use registry was
        empty when the scope was computed — the scope function is responsible
        for ensuring registration, and this is the only test that can tell.
        """
        catalog = load_builtin_taint_catalog()
        rows = compute_dataflow_scope(
            catalog, ["go", "java", "javascript", "python", "rust", "typescript"],
        )
        by_lang = {r.language: r for r in rows}
        for lang in ("go", "python", "rust", "typescript"):
            assert by_lang[lang].def_use_extractor, lang
            assert by_lang[lang].ddg_spec, lang
            assert by_lang[lang].dataflow_capable, lang

    def test_java_has_a_cfg_mapping_but_no_dataflow(self) -> None:
        """java is the one language that is half-wired, and the table says so.

        It ships ``cfg_nodes/java.yaml`` — so a reader who checked only for a
        CFG mapping would call it covered — while declaring no
        ``atomic_statement`` and registering no def/use extractor. Its 69
        sinks are the largest ineligible block behind a language that looks
        supported.
        """
        catalog = load_builtin_taint_catalog()
        (java,) = compute_dataflow_scope(catalog, ["java"])
        assert java.cfg_mapping
        assert not java.atomic_statement
        assert not java.def_use_extractor
        assert not java.dataflow_capable
        assert java.catalog_sinks > 0

    def test_javascript_is_covered_via_the_shared_typescript_grammar(
        self,
    ) -> None:
        """WI-nonad wired JavaScript; this used to assert the opposite.

        The inversion is the point, so it is recorded rather than quietly
        rewritten: JavaScript carries the larger catalog of the pair (>50 sinks
        against TypeScript's zero) and was the only unwired language whose CFG
        mapping and def/use extractor already existed under another key.

        ``cfg_mapping`` is asserted true THROUGH ``compute_dataflow_scope``,
        which reads ``load_cfg_mapping`` — so this also pins that the
        ``_CFG_MAPPING_ALIASES`` indirection reaches this consumer, not just
        the loader's own callers.

        Coverage here means the machinery is reachable for ``.js``. It does not
        assert any taint verdict changed; that is an A/B, not a unit test.
        """
        catalog = load_builtin_taint_catalog()
        (js,) = compute_dataflow_scope(catalog, ["javascript"])
        assert js.cfg_mapping
        assert js.atomic_statement
        assert js.def_use_extractor
        assert js.dataflow_capable
        assert js.catalog_sinks > 50

    def test_counts_come_from_the_catalog(self) -> None:
        catalog = load_builtin_taint_catalog()
        (py,) = compute_dataflow_scope(catalog, ["python"])
        assert py.catalog_sinks == len(catalog.sinks_for_language("python"))
        assert py.catalog_sources == len(catalog.sources_for_language("python"))
        assert py.catalog_sanitizers == len(
            catalog.sanitizers_for_language("python"),
        )

    def test_rows_are_sorted_and_unique(self) -> None:
        catalog = load_builtin_taint_catalog()
        rows = compute_dataflow_scope(catalog, ["python", "go", "go"])
        assert [r.language for r in rows] == ["go", "python"]

    def test_empty_language_set_yields_no_rows(self) -> None:
        assert compute_dataflow_scope(load_builtin_taint_catalog(), []) == []


class TestEmittedShape:
    def test_dict_states_what_decides_inclusion(self) -> None:
        """The a2 fact must be machine-readable, not left to prose.

        **THIS TEST HAS ALREADY FIRED ONCE, WHICH IS THE POINT OF IT.** It used
        to assert ``call_graph_reachability`` on the grounds that §3a was
        confirm-only, and it says so here rather than being quietly rewritten:
        WI-kabif granted §3a removal authority on 2026-09-02, and this test —
        the R16 trigger — caught two production constants and a rendered
        disclosure that would otherwise have gone on publishing a false claim
        to consumers.

        Re-pointed, not relaxed. The value now names BOTH halves because both
        are true, and the asymmetry is asserted separately below: a flow is
        still INCLUDED by reachability and the walk can only SUBTRACT. Reading
        ``analysis_method == "ddg"`` as "this flow's inclusion was decided by
        data flow" is therefore still the INV-sadah misreading — a surviving
        ``ddg`` flow was included by reachability and merely corroborated.
        """
        out = dataflow_scope_dict([_row(language="python")], {"ddg": 3})
        assert out["inclusion_decided_by"] == INCLUSION_DECIDED_BY
        assert INCLUSION_DECIDED_BY == (
            "call_graph_reachability_minus_ddg_refutation"
        )
        # The half that did NOT change: reachability still decides inclusion,
        # and the walk still adds nothing. A future edit that lets the walk
        # MINT a flow has to come back through here.
        assert INCLUSION_DECIDED_BY.startswith("call_graph_reachability")
        assert "minus" in INCLUSION_DECIDED_BY

    def test_dict_states_what_capability_does_NOT_claim(self) -> None:
        """Capability is per language; it is not per-function coverage.

        ``cfg_nodes/go.yaml`` self-documents that ``if err := do(); err != nil``
        initializers are invisible to def/use, so Go reads ``dataflow_capable``
        while holding functions the walk cannot see into. A reader who took the
        bit for coverage would be making exactly the assumption clause (a3)
        forbids, so the granularity is emitted rather than left implicit.

        Like ``inclusion_decided_by`` this is a declared constant with a test
        on it, which is what stops the claim outliving its truth: when
        WI-joluk's per-function coverage gate lands, this must become
        ``function`` or the assertion fails (R16).
        """
        out = dataflow_scope_dict([_row(language="go")], {"ddg": 1})
        assert out["coverage_granularity"] == COVERAGE_GRANULARITY
        assert COVERAGE_GRANULARITY == "language"

    def test_dict_carries_rows_and_findings(self) -> None:
        out = dataflow_scope_dict(
            [_row(language="python"), _row(language="javascript",
                                           cfg_mapping=False)],
            {"structural": 2, "ddg": 1},
        )
        assert [r["language"] for r in out["languages"]] == [
            "python", "javascript",
        ]
        assert out["languages"][0]["dataflow_capable"] is True
        assert out["languages"][1]["dataflow_capable"] is False
        assert out["languages"][1]["blockers"] == ["cfg_mapping"]
        assert out["findings_by_analysis_method"] == {"structural": 2, "ddg": 1}
        assert out["findings_total"] == 3

    def test_dict_is_json_serialisable(self) -> None:
        import json
        json.dumps(dataflow_scope_dict([_row()], {"ddg": 1}))

    def test_text_names_each_language_and_its_verdict(self) -> None:
        lines = render_dataflow_scope_text(
            [_row(language="python"),
             _row(language="javascript", cfg_mapping=False,
                  atomic_statement=False, def_use_extractor=False,
                  ddg_spec=False, catalog_sinks=83)],
            {"structural": 1},
        )
        body = "\n".join(lines)
        assert "python" in body
        assert "javascript" in body
        assert "83" in body
        assert "cfg_mapping" in body
        assert "call-graph reachability" in body
        # The text says "wired", never "adjudicable" — the latter reads as a
        # completeness claim the block is not making, and the caveat naming
        # the Go initializer gap must travel with it.
        assert "wired" in body
        assert "adjudicable" not in body
        assert "NOT that every function" in body

    def test_text_reports_zero_findings_without_dividing_by_zero(self) -> None:
        lines = render_dataflow_scope_text([_row()], {})
        assert any("0" in line for line in lines)

    def test_text_is_empty_when_nothing_was_analyzed(self) -> None:
        """No taint-capable language means no scope to publish, not a blank header."""
        assert render_dataflow_scope_text([], {}) == []


class TestWalkVerdictDisclosure:
    """INV-busis / ADR (c): make ``flows_removed_by_walk: 0`` readable.

    This module's founding argument is that "0 precise findings" is unreadable
    without scope, because "looked everywhere and found nothing" and "was
    structurally incapable of looking" have opposite consequences for a
    security reader on identical evidence. That argument was never applied to
    this module's OWN removal count. ``flows_removed_by_walk: 0`` sits beside a
    ``findings_by_analysis_method`` rollup in which ``ddg_mixed`` collapses
    THREE walk verdicts -- ``taint.py`` records a production split of 0
    ``unconfirmed`` / 14 ``escaped`` / 139 ``not_attempted`` -- so a reader
    cannot tell a walk that adjudicated and refuted nothing from a walk that
    never got to look. Only ``unconfirmed`` can remove a flow.

    The owner retired the removal GOAL on 2026-09-09 (INV-busis option (c)):
    escapes will not be chased until refutation fires at scale. Retiring a goal
    without disclosing its consequence is how a limitation becomes a silent
    one, so the breakdown ships as the user-visible half of that decision.
    """

    def test_a_unanimous_row_is_counted_under_its_verdict(self) -> None:
        counts = count_walk_verdicts([
            _finding("escaped"), _finding("escaped"), _finding("confirmed"),
        ])
        assert counts["escaped"] == 2
        assert counts["confirmed"] == 1

    def test_every_verdict_key_is_always_present(self) -> None:
        """Zeros are emitted, so an absent key never reads as 'not applicable'.

        The module's own convention, stated for ``sanitizer_scope``: "a
        disclosure that appears only when it has something to say teaches a
        consumer to treat its absence as 'not applicable' rather than 'zero'".
        """
        counts = count_walk_verdicts([])
        # EXACT, not a subset: adding a verdict to taint's vocabulary without
        # teaching this breakdown about it would silently dump the new cell
        # into ``unrecorded``, and the disclosure would read as complete while
        # hiding it. That is the R16 pattern this module already uses for
        # ``INCLUSION_DECIDED_BY`` -- a declared claim with a test on it, so it
        # cannot quietly outlive its truth.
        assert set(counts) == set(WALK_VERDICTS) | {"mixed", "unrecorded"}
        assert set(counts.values()) == {0}

    def test_a_MIXED_row_is_not_attributed_to_its_first_member(self) -> None:
        """The whole reason ``walk_verdict_values`` exists (INV-muhij A).

        On a collapsed row the scalar is ``grp[0]``'s, and measured on beads
        63.9% of groups holding a ``sink_before_source`` member are NOT
        unanimous. Counting the scalar would publish a clean, plausible,
        entirely wrong breakdown -- and would do it in the direction that
        flatters the tool, since a row standing for one confirmed and three
        escaped members would read as fully adjudicated.
        """
        row = _finding("confirmed", values=("confirmed", "escaped"))
        counts = count_walk_verdicts([row])
        assert counts["mixed"] == 1
        assert counts["confirmed"] == 0, (
            "a mixed row was attributed to grp[0]'s scalar"
        )

    def test_a_row_with_no_recorded_verdict_is_counted_not_dropped(self) -> None:
        """``""`` is a real cell (a finding deserialized from an older map).

        Dropping it would make the breakdown fail to sum to the finding total,
        which is the one arithmetic a reader can check.
        """
        counts = count_walk_verdicts([_finding("")])
        assert counts["unrecorded"] == 1

    def test_the_breakdown_sums_to_the_number_of_findings(self) -> None:
        findings = [
            _finding("confirmed"), _finding("escaped"), _finding(""),
            _finding("unconfirmed", values=("unconfirmed", "confirmed")),
        ]
        assert sum(count_walk_verdicts(findings).values()) == len(findings)

    def test_dict_publishes_the_breakdown(self) -> None:
        out = dataflow_scope_dict(
            [_row(language="python")], {"ddg_mixed": 2},
            walk_verdicts=count_walk_verdicts([
                _finding("escaped"), _finding("not_attempted"),
            ]),
        )
        assert out["walk_verdicts"]["escaped"] == 1
        assert out["walk_verdicts"]["not_attempted"] == 1
        assert out["walk_verdicts"]["unconfirmed"] == 0

    def test_dict_publishes_the_breakdown_even_when_not_supplied(self) -> None:
        """Shape stability: the key is present on every run, like its siblings."""
        out = dataflow_scope_dict([_row()], {"ddg": 1})
        assert "walk_verdicts" in out
        assert out["walk_verdicts"]["confirmed"] == 0

    def test_text_says_only_unconfirmed_can_remove(self) -> None:
        """The text reader gets it too -- a json-only disclosure is half shipped."""
        lines = render_dataflow_scope_text(
            [_row(language="python")], {"ddg_mixed": 140},
            flows_removed_by_walk=0,
            walk_verdicts=count_walk_verdicts(
                [_finding("escaped")] * 14 + [_finding("not_attempted")] * 139
            ),
        )
        blob = "\n".join(lines)
        assert "unconfirmed" in blob
        assert "escaped 14" in blob
        assert "not_attempted 139" in blob
        # The point of the line: 0 removals beside 139 not_attempted is NOT
        # evidence the walk looked and found nothing.
        assert "did not get to look" in blob

    def test_text_still_renders_with_no_breakdown_supplied(self) -> None:
        lines = render_dataflow_scope_text([_row()], {"ddg": 1})
        assert any("Flows REMOVED" in line for line in lines)
