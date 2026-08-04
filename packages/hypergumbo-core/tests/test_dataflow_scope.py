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
    dataflow_scope_dict,
    render_dataflow_scope_text,
)
from hypergumbo_core.taint import load_builtin_taint_catalog


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

    def test_javascript_is_uncovered_despite_a_large_catalog(self) -> None:
        catalog = load_builtin_taint_catalog()
        (js,) = compute_dataflow_scope(catalog, ["javascript"])
        assert not js.cfg_mapping
        assert not js.dataflow_capable
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

        ADR-0017 §3a is confirm-only: the walk raises confidence and never
        removes a flow, so EVERY reported flow was included by call-graph
        reachability. Reading ``analysis_method == "ddg"`` as "this flow's
        inclusion was decided by data flow" is the misreading INV-sadah was
        filed for, and it has been made twice in this repository. Emitting the
        fact gives the claim an executable re-evaluation trigger (R16): when
        §3a gains refutation, this value has to change or the test fails.
        """
        out = dataflow_scope_dict([_row(language="python")], {"ddg": 3})
        assert out["inclusion_decided_by"] == INCLUSION_DECIDED_BY
        assert INCLUSION_DECIDED_BY == "call_graph_reachability"

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
