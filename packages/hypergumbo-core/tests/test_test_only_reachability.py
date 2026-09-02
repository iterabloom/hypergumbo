# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ratuv's gate: a production function reachable only from its own tests.

THE TESTS THAT MATTER MOST HERE ARE THE DEMOTION ONES, because every false
positive this gate has produced so far came from a reference mechanism the call
graph does not model, and each was found by reading a flagged row back against
source rather than by any assertion:

    cmd_slice              ``p_slice.set_defaults(func=cmd_slice)`` — a VALUE
                           BINDING, and cmd_slice is the entry point of
                           ``hypergumbo slice``
    link_decorator_dispatch  carries ``@register_linker(...)``; the first cut
                           indexed the DECORATOR's name and left the
                           REGISTERED function gated
    _install_rust_analyzer_with_bug06_gate
                           referenced as ``cli_mod._install_…`` in its OWN
                           module, which the first cut excluded

Each of those is pinned below, so a future simplification of the demotion arm
fails here instead of failing CI on a user-facing command.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.test_only_reachability import (
    DEMOTION_CROSS_MODULE,
    DEMOTION_DECORATOR,
    DEMOTION_DISPATCH,
    DEMOTION_REGISTERED,
    GATED_KINDS,
    ProductionReferences,
    TestOnlySymbol,
    baseline_document,
    collect_production_references,
    compare_to_baseline,
    demotion_reason,
    find_test_only_symbols,
    gated_cohort,
    load_baseline,
    production_source_files,
)

PROD = "packages/p/src/p/mod.py"
TEST = "packages/p/tests/test_mod.py"


def _node(nid: str, name: str, path: str, kind: str = "function") -> dict:
    return {"id": nid, "name": name, "path": path, "kind": kind}


def _call(src: str, dst: str) -> dict:
    return {"src": src, "dst": dst, "type": "calls"}


class TestTheCohortQuestion:
    """Callers, not seeds — the distinction the whole item turns on."""

    def test_a_function_called_only_from_tests_is_found(self) -> None:
        nodes = [_node("f", "target", PROD), _node("t", "test_it", TEST)]
        found = find_test_only_symbols(nodes, [_call("t", "f")])
        assert [s.name for s in found] == ["target"]

    def test_one_production_caller_is_enough_to_clear_it(self) -> None:
        nodes = [
            _node("f", "target", PROD),
            _node("t", "test_it", TEST),
            _node("p", "caller", PROD),
        ]
        found = find_test_only_symbols(
            nodes, [_call("t", "f"), _call("p", "f")],
        )
        assert found == []

    def test_a_symbol_with_NO_callers_is_not_in_this_cohort(self) -> None:
        """The load-bearing difference from ``dead-code-maybe``.

        A registry-dispatched analyzer method has no direct callers at all, so
        dynamic dispatch separates itself into the zero-caller population
        instead of drowning this one. That is measured, not assumed: under the
        seed-based question dispatch was 96.6% of the cohort.
        """
        found = find_test_only_symbols([_node("f", "target", PROD)], [])
        assert found == []

    def test_a_test_symbol_is_never_its_own_finding(self) -> None:
        nodes = [_node("a", "helper", TEST), _node("b", "test_it", TEST)]
        assert find_test_only_symbols(nodes, [_call("b", "a")]) == []

    def test_a_non_callable_node_is_never_in_the_cohort(self) -> None:
        """The map holds classes, modules and variables too.

        "Reachable only from tests" is a question about something that gets
        CALLED; a class node imported by a test is a different question, and
        letting one in would put un-adjudicable rows in a frozen baseline.
        """
        nodes = [
            _node("c", "Widget", PROD, kind="class"),
            _node("t", "test_it", TEST),
        ]
        assert find_test_only_symbols(nodes, [_call("t", "c")]) == []

    def test_a_support_file_UNDER_src_is_still_excluded(self) -> None:
        """``paths.is_test_file`` is the BROAD predicate and also flags
        ``fixtures/``, ``mocks/`` and ``testdata/`` — which can live under
        ``src/``. Both halves of the guard are needed: the path check alone
        would admit ``packages/p/src/p/testdata/helper.py`` as production.
        """
        nodes = [
            _node("f", "helper", "packages/p/src/p/testdata/helper.py"),
            _node("t", "test_it", TEST),
        ]
        assert find_test_only_symbols(nodes, [_call("t", "f")]) == []

    def test_non_production_paths_are_out_of_scope(self) -> None:
        nodes = [_node("f", "target", "scripts/thing.py"),
                 _node("t", "test_it", TEST)]
        assert find_test_only_symbols(nodes, [_call("t", "f")]) == []

    def test_a_non_call_edge_does_not_make_a_caller(self) -> None:
        nodes = [_node("f", "target", PROD), _node("t", "test_it", TEST)]
        edges = [{"src": "t", "dst": "f", "type": "imports"}]
        assert find_test_only_symbols(nodes, edges) == []

    def test_an_edge_from_an_unknown_node_is_ignored(self) -> None:
        """An edge whose src is not in the map cannot be classified.

        It must not read as a production caller (that would silently clear a
        real finding) and must not read as a test caller either.
        """
        nodes = [_node("f", "target", PROD)]
        assert find_test_only_symbols(nodes, [_call("ghost", "f")]) == []


class TestTheBaselineKeyIsStableUnderEdits:
    def test_the_key_omits_the_line_span(self) -> None:
        """An id-keyed baseline would churn on every edit that moves a line."""
        early = TestOnlySymbol("python:p.py:10-20:f:function", "f", "function", PROD)
        later = TestOnlySymbol("python:p.py:90-99:f:function", "f", "function", PROD)
        assert early.key == later.key
        assert "10-20" not in early.key


class TestDemotion:
    """Every case here was a live false positive before it was a test."""

    def test_a_dispatch_bound_function_is_demoted(self) -> None:
        """``set_defaults(func=cmd_slice)`` — the CLI entry-point shape."""
        refs = ProductionReferences(dispatch_bound={"cmd_slice": {"cli.py"}})
        sym = TestOnlySymbol("i", "cmd_slice", "function", PROD)
        assert demotion_reason(sym, refs) == DEMOTION_DISPATCH

    def test_a_function_CARRYING_a_decorator_is_demoted(self) -> None:
        """The bug that made this a separate index from ``decorated``.

        ``@register_linker`` put ``register_linker`` in ``decorated`` and left
        ``link_decorator_dispatch`` — the REGISTERED function, and the real
        production path — sitting in the gated cohort.
        """
        refs = ProductionReferences(
            decorated={"register_linker": {"linkers/x.py"}},
            carries_decorator={"link_decorator_dispatch": {"linkers/x.py"}},
        )
        sym = TestOnlySymbol("i", "link_decorator_dispatch", "function", PROD)
        assert demotion_reason(sym, refs) == DEMOTION_REGISTERED

    def test_a_function_USED_AS_a_decorator_is_demoted(self) -> None:
        """``register_linker`` itself was in the live cohort.

        Every ``@register_linker(...)`` is an APPLICATION, not a call edge, so
        the decorator's own definition looked test-only. Distinct from the
        case below it: this is the decorator, that is the decorated.
        """
        refs = ProductionReferences(
            decorated={"register_linker": {"linkers/a.py", "linkers/b.py"}},
        )
        sym = TestOnlySymbol("i", "register_linker", "function", PROD)
        assert demotion_reason(sym, refs) == DEMOTION_DECORATOR

    def test_a_reference_in_its_OWN_module_demotes(self) -> None:
        """``cli.py`` passes ``cli_mod._install_…`` as a value.

        A ``def`` is not a ``Load``, so the only way a module mentions its own
        function is a real reference. Excluding same-module hits — the first
        cut did — leaves a production value-binding flagged.
        """
        refs = ProductionReferences(loads={"helper": {PROD}})
        sym = TestOnlySymbol("i", "helper", "function", PROD)
        assert demotion_reason(sym, refs) == DEMOTION_CROSS_MODULE

    def test_an_unreferenced_function_is_not_demoted(self) -> None:
        """Non-vacuity floor: the arm must not demote everything."""
        sym = TestOnlySymbol("i", "orphan", "function", PROD)
        assert demotion_reason(sym, ProductionReferences()) is None

    def test_only_the_last_name_component_is_consulted(self) -> None:
        refs = ProductionReferences(loads={"add": {"other.py"}})
        sym = TestOnlySymbol("i", "Store.add", "method", PROD)
        assert demotion_reason(sym, refs) == DEMOTION_CROSS_MODULE


class TestGatedCohort:
    def test_methods_are_counted_but_never_gated(self) -> None:
        """Measured, not stylistic: a method's call-graph identity is its SHORT
        name, so ``Store.add`` cannot be told from ``set.add`` until receiver
        typing lands (INV-linub). Gating ~200 unadjudicable rows would make the
        baseline the rubber stamp this item's own ruling forbids.
        """
        symbols = [
            TestOnlySymbol("a", "fn", "function", PROD),
            TestOnlySymbol("b", "C.m", "method", PROD),
        ]
        kept, census = gated_cohort(symbols, ProductionReferences())
        assert [s.name for s in kept] == ["fn"]
        assert census["not_gated_kind"] == 1
        assert "method" not in GATED_KINDS

    def test_the_census_names_why_each_was_set_aside(self) -> None:
        symbols = [
            TestOnlySymbol("a", "cmd_x", "function", PROD),
            TestOnlySymbol("b", "kept", "function", PROD),
        ]
        refs = ProductionReferences(dispatch_bound={"cmd_x": {"cli.py"}})
        kept, census = gated_cohort(symbols, refs)
        assert [s.name for s in kept] == ["kept"]
        assert census[DEMOTION_DISPATCH] == 1


class TestTheRatchet:
    def test_a_new_key_is_reported(self) -> None:
        syms = [TestOnlySymbol("i", "fresh", "function", PROD)]
        new, gone = compare_to_baseline(syms, set())
        assert new == [f"{PROD}::fresh"]
        assert gone == []

    def test_a_drained_key_is_reported_so_it_can_be_removed(self) -> None:
        new, gone = compare_to_baseline([], {"a::b"})
        assert new == []
        assert gone == ["a::b"]

    def test_an_unchanged_cohort_is_clean(self) -> None:
        syms = [TestOnlySymbol("i", "known", "function", PROD)]
        assert compare_to_baseline(syms, {f"{PROD}::known"}) == ([], [])

    def test_a_missing_baseline_file_reads_empty(self, tmp_path: Path) -> None:
        assert load_baseline(tmp_path / "nope.json") == set()

    def test_a_written_baseline_round_trips(self, tmp_path: Path) -> None:
        syms = [TestOnlySymbol("i", "known", "function", PROD)]
        target = tmp_path / "b.json"
        target.write_text(json.dumps(baseline_document(syms)), encoding="utf-8")
        assert load_baseline(target) == {f"{PROD}::known"}

    def test_the_baseline_document_explains_its_own_contract(self) -> None:
        doc = baseline_document([])
        assert "SHRINK" in doc["_comment"]
        assert doc["keys"] == []


class TestAstScanning:
    """Parsed, never grepped — prose must not vote."""

    def test_a_name_in_a_comment_is_not_a_reference(self, tmp_path: Path) -> None:
        """The exact failure this replaces.

        A grep proxy once scored ``infer_summary`` as having one production
        reference; the reference was a COMMENT reading "``infer_summary`` still
        has zero production callers".
        """
        mod = tmp_path / "packages/p/src/p/m.py"
        mod.parent.mkdir(parents=True)
        mod.write_text(
            "# infer_summary still has zero production callers\n"
            "'''And infer_summary in a docstring too.'''\n",
            encoding="utf-8",
        )
        refs = collect_production_references([mod], tmp_path)
        assert "infer_summary" not in refs.loads

    def test_a_real_load_is_a_reference(self, tmp_path: Path) -> None:
        mod = tmp_path / "m.py"
        mod.write_text("x = helper\n", encoding="utf-8")
        refs = collect_production_references([mod], tmp_path)
        assert refs.loads["helper"] == {"m.py"}

    def test_an_attribute_access_is_a_reference(self, tmp_path: Path) -> None:
        mod = tmp_path / "m.py"
        mod.write_text("y = mod.helper\n", encoding="utf-8")
        refs = collect_production_references([mod], tmp_path)
        assert "helper" in refs.loads

    def test_a_decorated_function_indexes_BOTH_names(self, tmp_path: Path) -> None:
        mod = tmp_path / "m.py"
        mod.write_text(
            "@register_linker('x')\ndef link_thing(ctx):\n    pass\n",
            encoding="utf-8",
        )
        refs = collect_production_references([mod], tmp_path)
        assert "register_linker" in refs.decorated
        assert "link_thing" in refs.carries_decorator

    def test_a_bare_decorator_is_indexed_too(self, tmp_path: Path) -> None:
        mod = tmp_path / "m.py"
        mod.write_text("@cache\ndef f():\n    pass\n", encoding="utf-8")
        refs = collect_production_references([mod], tmp_path)
        assert "cache" in refs.decorated

    @pytest.mark.parametrize("kw", ["func", "handler", "callback", "default"])
    def test_dispatch_keywords_are_indexed(self, tmp_path: Path, kw: str) -> None:
        mod = tmp_path / "m.py"
        mod.write_text(f"p.set_defaults({kw}=cmd_slice)\n", encoding="utf-8")
        refs = collect_production_references([mod], tmp_path)
        assert refs.dispatch_bound["cmd_slice"] == {"m.py"}

    def test_a_non_bare_name_keyword_is_not_a_dispatch_binding(
        self, tmp_path: Path,
    ) -> None:
        """``func=obj.method`` is not a bare-name binding this can key on."""
        mod = tmp_path / "m.py"
        mod.write_text("p.set_defaults(func=obj.method)\n", encoding="utf-8")
        refs = collect_production_references([mod], tmp_path)
        assert refs.dispatch_bound == {}

    def test_an_unparseable_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """Conservative direction: a lint that can DELETE work from a baseline
        must see fewer references rather than crash mid-edit."""
        bad = tmp_path / "bad.py"
        bad.write_text("def (\n", encoding="utf-8")
        good = tmp_path / "good.py"
        good.write_text("x = helper\n", encoding="utf-8")
        refs = collect_production_references([bad, good], tmp_path)
        assert "helper" in refs.loads

    def test_a_missing_file_is_skipped(self, tmp_path: Path) -> None:
        refs = collect_production_references([tmp_path / "gone.py"], tmp_path)
        assert refs.loads == {}


class TestAgainstTheLiveTree:
    """The gate's own motivating examples, asserted on this repository."""

    def test_the_shipped_baseline_is_present_and_nonempty(self) -> None:
        """A baseline that silently vanished would make the gate pass forever."""
        root = Path(__file__).resolve().parents[3]
        keys = load_baseline(root / ".ci/test-only-reachability-baseline.json")
        assert len(keys) > 100

    def test_the_ADR_0017_family_is_in_the_baseline(self) -> None:
        """``infer_summary`` / ``is_field_tainted`` / ``select_ddg_targets``
        are the three members the SEED-based question missed. If a future
        change stops flagging them, this gate has lost the signal it exists
        for — and the seed-based version was refused for exactly that.
        """
        root = Path(__file__).resolve().parents[3]
        keys = load_baseline(root / ".ci/test-only-reachability-baseline.json")
        names = {k.rsplit("::", 1)[-1] for k in keys}
        assert {"infer_summary", "is_field_tainted",
                "select_ddg_targets"} <= names

    def test_the_control_is_NOT_in_the_baseline(self) -> None:
        """``load_function_summaries`` is the one family member genuinely fixed
        (PR #197 gave it production call sites). A gate that flagged it too
        would be flagging the family wholesale rather than discriminating.
        """
        root = Path(__file__).resolve().parents[3]
        keys = load_baseline(root / ".ci/test-only-reachability-baseline.json")
        names = {k.rsplit("::", 1)[-1] for k in keys}
        assert "load_function_summaries" not in names

    def test_registered_analyzers_are_NOT_in_the_baseline(self) -> None:
        """``analyze_scheme`` and its 124 peers carry ``@register_analyzer``.

        A 2026-08-30 measurement called this family "*** DEAD ***" on the
        strength of "nothing in production NAMES it" — true, and irrelevant,
        because the registration is a decorator on the line above the ``def``.
        Pinned so the claim cannot come back.
        """
        root = Path(__file__).resolve().parents[3]
        keys = load_baseline(root / ".ci/test-only-reachability-baseline.json")
        names = {k.rsplit("::", 1)[-1] for k in keys}
        assert not {n for n in names if n.startswith("analyze_")}

    def test_production_source_files_finds_the_shipped_tree(self) -> None:
        root = Path(__file__).resolve().parents[3]
        files = production_source_files(root)
        assert len(files) > 100
        assert all("/src/" in str(f) for f in files)
