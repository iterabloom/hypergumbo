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
    DEMOTION_EXTERNAL_BASE,
    DEMOTION_OVERRIDE,
    DEMOTION_PROTOCOL,
    DEMOTION_REGISTERED,
    DEMOTION_RUNTIME,
    GATED_KINDS,
    SET_ASIDE_UNTYPED,
    ProductionReferences,
    TestOnlySymbol,
    baseline_document,
    collect_production_references,
    compare_to_baseline,
    demotion_reason,
    find_test_only_symbols,
    gated_cohort,
    load_baseline,
    pass_contribution_problem,
    production_source_files,
    typed_method_call_share,
    typed_references,
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
    def test_methods_are_gated_only_on_a_map_with_receiver_typing(self) -> None:
        """Without types a method's identity is its SHORT name, so
        ``Store.add`` cannot be told from ``set.add``: gating it then would
        put unadjudicable rows in the baseline, the rubber stamp WI-ratuv's
        ruling forbids. Given a typed map (WI-mifag) the method is judged.
        """
        symbols = [
            TestOnlySymbol("a", "fn", "function", PROD),
            TestOnlySymbol("b", "C.m", "method", PROD),
        ]
        kept, census = gated_cohort(symbols, ProductionReferences())
        assert [s.name for s in kept] == ["fn"]
        assert census[SET_ASIDE_UNTYPED] == 1
        assert "method" in GATED_KINDS

        typed = typed_references([], [])
        kept, census = gated_cohort(symbols, ProductionReferences(), typed)
        assert {s.name for s in kept} == {"C.m", "fn"}
        assert SET_ASIDE_UNTYPED not in census

    def test_the_census_names_why_each_was_set_aside(self) -> None:
        symbols = [
            TestOnlySymbol("a", "cmd_x", "function", PROD),
            TestOnlySymbol("b", "kept", "function", PROD),
        ]
        refs = ProductionReferences(dispatch_bound={"cmd_x": {"cli.py"}})
        kept, census = gated_cohort(symbols, refs)
        assert [s.name for s in kept] == ["kept"]
        assert census[DEMOTION_DISPATCH] == 1


USE = "packages/p/src/p/use.py"
SET_ADD = "python:builtins.set:0-0:add:external_symbol"
PROTOCOL = "python:typing:0-0:Protocol:external_symbol"


def _typed_node(
    nid: str, name: str, path: str, kind: str, start: int, end: int,
    origin: tuple[str, ...] = ("python", "scip"),
) -> dict:
    return {"id": nid, "name": name, "path": path, "kind": kind,
            "origin": list(origin), "span": {"start_line": start, "end_line": end}}


def _edge(src: str, dst: str, etype: str = "calls", line: int = 0,
          origin: tuple[str, ...] = ("scip",)) -> dict:
    return {"src": src, "dst": dst, "type": etype, "line": line, "origin": list(origin)}


#: ``Store.add`` in PROD; ``user`` in USE (lines 1-20) is production code.
STORE_NODES = [
    _typed_node("store", "Store", PROD, "class", 1, 10),
    _typed_node("store_add", "Store.add", PROD, "method", 2, 3),
    _typed_node("user", "user", USE, "function", 1, 20),
]
STORE_ADD = TestOnlySymbol("store_add", "Store.add", "method", PROD)


class TestMethodsOnATypedMap:
    """WI-mifag: a method is judged by the TYPE its references resolve to.

    The bare-name demotion arm put ``set.add`` and ``Store.add`` in one bucket,
    so every ``.add`` anywhere kept ``Store.add`` out of the cohort. On a map
    the scip-python backend typed, an attribute site inside a production
    callable resolves through that callable's typed edges instead.
    """

    def test_a_site_typed_to_another_receiver_does_not_demote(self) -> None:
        """The item's own example: ``s.add(x)`` on a set is not ``Store.add``."""
        typed = typed_references(STORE_NODES, [_edge("user", SET_ADD, line=5)])
        refs = ProductionReferences(member_sites={"add": {(USE, 5)}})
        assert demotion_reason(STORE_ADD, refs, typed) is None

    def test_an_edge_missing_an_endpoint_is_ignored(self) -> None:
        typed = typed_references(STORE_NODES, [
            {"src": "user", "type": "calls", "origin": ["scip"]},
            {"dst": "store_add", "type": "implements", "origin": ["scip"]},
        ])
        assert typed.resolved == {} and typed.overrides == {}

    def test_a_site_typed_to_the_method_demotes(self) -> None:
        typed = typed_references(STORE_NODES, [_edge("user", "store_add", line=5)])
        refs = ProductionReferences(member_sites={"add": {(USE, 5)}})
        assert demotion_reason(STORE_ADD, refs, typed) == DEMOTION_CROSS_MODULE

    def test_an_untyped_site_demotes(self) -> None:
        """Pyright could not type the receiver (an ``Any``): it might be a
        Store, so the conservative reading wins -- a false NEGATIVE."""
        typed = typed_references(STORE_NODES, [])
        refs = ProductionReferences(member_sites={"add": {(USE, 5)}})
        assert demotion_reason(STORE_ADD, refs, typed) == DEMOTION_CROSS_MODULE

    def test_a_syntax_arm_edge_types_nothing(self) -> None:
        """Only the typed producer's edges count: the syntax arm's ``.add``
        resolution is the bare-name guess this replaces."""
        typed = typed_references(
            STORE_NODES, [_edge("user", SET_ADD, line=5, origin=("python",))],
        )
        refs = ProductionReferences(member_sites={"add": {(USE, 5)}})
        assert demotion_reason(STORE_ADD, refs, typed) == DEMOTION_CROSS_MODULE

    def test_a_site_outside_every_callable_demotes(self) -> None:
        """SCIP drops an occurrence with no enclosing definition (module top
        level), so such a site carries no type and is read conservatively."""
        typed = typed_references(STORE_NODES, [_edge("user", SET_ADD, line=5)])
        refs = ProductionReferences(member_sites={"add": {(USE, 40)}})
        assert demotion_reason(STORE_ADD, refs, typed) == DEMOTION_CROSS_MODULE

    def test_a_site_resolves_per_enclosing_callable_not_per_line(self) -> None:
        """The map keeps ONE edge per (src, dst, type), on its first line, so a
        second ``.add`` on a set in the same callable has no edge of its own.
        Resolution is therefore by the innermost typed encloser. Residual,
        stated: an untyped ``.add`` sharing a callable with a typed one reads
        as typed too."""
        typed = typed_references(STORE_NODES, [_edge("user", SET_ADD, line=5)])
        refs = ProductionReferences(member_sites={"add": {(USE, 5), (USE, 9)}})
        assert demotion_reason(STORE_ADD, refs, typed) is None

    def test_an_override_of_a_production_reached_method_demotes(self) -> None:
        """``DartAnalyzer.extract_symbols_from_file``: tests call it directly,
        production reaches it through ``self.extract_symbols_from_file`` in
        the base class -- virtual dispatch the typed call graph credits to the
        base."""
        nodes = [
            _typed_node("base_run", "Base.run", PROD, "method", 2, 3),
            _typed_node("sub_run", "Sub.run", PROD, "method", 12, 13),
            _typed_node("leaf_run", "Leaf.run", PROD, "method", 22, 23),
            _typed_node("user", "user", USE, "function", 1, 20),
        ]
        sub = TestOnlySymbol("sub_run", "Sub.run", "method", PROD)
        leaf = TestOnlySymbol("leaf_run", "Leaf.run", "method", PROD)
        overrides = [_edge("sub_run", "base_run", "implements"),
                     _edge("leaf_run", "sub_run", "implements")]
        reached = typed_references(nodes, overrides + [_edge("user", "base_run")])
        assert demotion_reason(sub, ProductionReferences(), reached) == DEMOTION_OVERRIDE
        assert demotion_reason(leaf, ProductionReferences(), reached) == DEMOTION_OVERRIDE
        unreached = typed_references(nodes, overrides)
        assert demotion_reason(sub, ProductionReferences(), unreached) is None

    def test_a_method_of_a_class_on_an_external_base_demotes(self) -> None:
        """Textual calls ``action_*`` by binding name, ``on_*`` by event, and
        ``render_line`` by override: the caller is the framework, outside the
        map. An in-repo ancestor on the external base counts the same."""
        nodes = [
            _typed_node("app", "TrackerApp", PROD, "class", 1, 50),
            _typed_node("act", "TrackerApp.action_yank", PROD, "method", 2, 3),
            _typed_node("child", "Child", PROD, "class", 60, 80),
            _typed_node("cm", "Child.on_click", PROD, "method", 61, 62),
        ]
        edges = [
            _edge("app", "python:textual.app:0-0:App:external_symbol", "extends",
                  origin=("python",)),
            _edge("child", "app", "extends", origin=("python",)),
        ]
        typed = typed_references(nodes, edges)
        for sid, name in [("act", "TrackerApp.action_yank"), ("cm", "Child.on_click")]:
            sym = TestOnlySymbol(sid, name, "method", PROD)
            assert demotion_reason(sym, ProductionReferences(), typed) == DEMOTION_EXTERNAL_BASE

    def test_a_protocol_or_abc_base_calls_nothing(self) -> None:
        """``typing.Protocol`` and ``abc.ABC`` declare a shape; neither runs a
        method. ``DuressHandler.filter_response`` -- a Protocol method nothing
        in production calls -- stays gated."""
        nodes = [
            _typed_node("p", "DuressHandler", PROD, "class", 1, 10),
            _typed_node("pm", "DuressHandler.filter_response", PROD, "method", 2, 3),
            _typed_node("a", "Strategy", PROD, "class", 20, 30),
            _typed_node("am", "Strategy.find", PROD, "method", 21, 22),
        ]
        edges = [
            _edge("p", PROTOCOL, "extends", origin=("python",)),
            _edge("a", "python:abc:0-0:ABC:external_symbol", "extends", origin=("python",)),
        ]
        typed = typed_references(nodes, edges)
        for sid, name in [("pm", "DuressHandler.filter_response"), ("am", "Strategy.find")]:
            sym = TestOnlySymbol(sid, name, "method", PROD)
            assert demotion_reason(sym, ProductionReferences(), typed) is None

    def test_a_method_named_like_a_reached_protocol_method_demotes(self) -> None:
        """``extractor.extract(...)`` types to ``DefUseExtractor.extract`` on
        the Protocol; ``CDefUseExtractor`` conforms structurally, with no
        edge saying so, so the name is all that links them."""
        nodes = [
            _typed_node("p", "DefUseExtractor", PROD, "class", 1, 10),
            _typed_node("pm", "DefUseExtractor.extract", PROD, "method", 2, 3),
            _typed_node("cm", "CDefUseExtractor.extract", PROD, "method", 12, 13),
            _typed_node("user", "user", USE, "function", 1, 20),
        ]
        edges = [_edge("p", PROTOCOL, "extends", origin=("python",))]
        sym = TestOnlySymbol("cm", "CDefUseExtractor.extract", "method", PROD)
        reached = typed_references(nodes, edges + [_edge("user", "pm")])
        assert demotion_reason(sym, ProductionReferences(), reached) == DEMOTION_PROTOCOL
        assert demotion_reason(sym, ProductionReferences(), typed_references(nodes, edges)) is None

    def test_a_test_caller_does_not_reach_a_protocol_method(self) -> None:
        nodes = [
            _typed_node("p", "Proto", PROD, "class", 1, 10),
            _typed_node("pm", "Proto.go", PROD, "method", 2, 3),
            _typed_node("t", "test_go", TEST, "function", 1, 5),
        ]
        edges = [_edge("p", PROTOCOL, "extends", origin=("python",)), _edge("t", "pm")]
        sym = TestOnlySymbol("x", "Impl.go", "method", PROD)
        assert demotion_reason(sym, ProductionReferences(), typed_references(nodes, edges)) is None

    def test_a_dunder_is_called_by_the_runtime(self) -> None:
        """``Pattern.__post_init__`` runs from the dataclass ``__init__``."""
        sym = TestOnlySymbol("x", "Pattern.__post_init__", "method", PROD)
        typed = typed_references([], [])
        assert demotion_reason(sym, ProductionReferences(), typed) == DEMOTION_RUNTIME

    def test_a_decorator_demotes_only_the_method_that_carries_it(self) -> None:
        """``@property def add`` on another class is not ``Store.add``'s."""
        typed = typed_references(STORE_NODES, [])
        other = ProductionReferences(decorated_methods={(PROD, "Bag.add"), (USE, "Store.add")})
        assert demotion_reason(STORE_ADD, other, typed) is None
        own = ProductionReferences(decorated_methods={(PROD, "Store.add")})
        assert demotion_reason(STORE_ADD, own, typed) == DEMOTION_REGISTERED

    def test_the_bare_name_indexes_do_not_vote_for_a_typed_method(self) -> None:
        """``loads`` / ``carries_decorator`` are keyed by bare name -- the
        ambiguity this arm replaces -- so a typed method ignores them."""
        refs = ProductionReferences(
            loads={"add": {USE}}, carries_decorator={"add": {USE}},
            dispatch_bound={"add": {USE}}, decorated={"add": {USE}},
        )
        assert demotion_reason(STORE_ADD, refs, typed_references(STORE_NODES, [])) is None

    def test_a_function_is_judged_as_before_on_a_typed_map(self) -> None:
        refs = ProductionReferences(loads={"helper": {USE}})
        sym = TestOnlySymbol("i", "helper", "function", PROD)
        assert demotion_reason(sym, refs, typed_references([], [])) == DEMOTION_CROSS_MODULE

    def test_the_census_names_the_method_arms(self) -> None:
        symbols = [
            STORE_ADD,
            TestOnlySymbol("d", "Store.__eq__", "method", PROD),
        ]
        typed = typed_references(STORE_NODES, [])
        kept, census = gated_cohort(symbols, ProductionReferences(), typed)
        assert [s.name for s in kept] == ["Store.add"]
        assert census == {DEMOTION_RUNTIME: 1}


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

    def test_methods_are_baselined_apart_from_functions(self, tmp_path: Path) -> None:
        """A map without receiver typing cannot judge methods, so a run on one
        must neither report the method keys as drained nor drop them on
        ``--write-baseline``. Keeping them in their own list is what lets it
        compare functions alone and carry the methods through untouched.
        """
        syms = [
            TestOnlySymbol("i", "fn", "function", PROD),
            TestOnlySymbol("j", "C.m", "method", PROD),
        ]
        doc = baseline_document(syms)
        assert doc["keys"] == [f"{PROD}::fn"]
        assert doc["method_keys"] == [f"{PROD}::C.m"]
        target = tmp_path / "b.json"
        target.write_text(json.dumps(doc), encoding="utf-8")
        assert load_baseline(target) == {f"{PROD}::fn", f"{PROD}::C.m"}
        assert load_baseline(target, methods=False) == {f"{PROD}::fn"}

        kept = baseline_document(syms[:1], keep_method_keys=[f"{PROD}::C.m"])
        assert kept["method_keys"] == [f"{PROD}::C.m"]


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

    def test_a_member_site_is_where_the_attribute_NAME_sits(self, tmp_path: Path) -> None:
        """SCIP's occurrence is the ``.add`` token, which on a chain split over
        lines is the expression's LAST line, not its first."""
        mod = tmp_path / "m.py"
        mod.write_text("y = (thing\n     .add)\n", encoding="utf-8")
        refs = collect_production_references([mod], tmp_path)
        assert refs.member_sites["add"] == {("m.py", 2)}

    def test_a_local_name_is_never_a_member_site(self, tmp_path: Path) -> None:
        """A method is reached through an attribute. A bare ``ancestors`` in a
        function body is a local -- 42 of them once demoted ``Store.ancestors``
        -- so only a CLASS-BODY name (``x = property(_get)``) is a member site.
        """
        mod = tmp_path / "m.py"
        mod.write_text(
            "def f():\n    ancestors = 1\n    return ancestors\n"
            "class C:\n    def _get(self):\n        return _get\n"
            "    x = property(_get)\n",
            encoding="utf-8",
        )
        refs = collect_production_references([mod], tmp_path)
        assert "ancestors" not in refs.member_sites
        assert refs.member_sites["_get"] == {("m.py", 7)}

    def test_a_nested_class_body_is_indexed_as_its_own(self, tmp_path: Path) -> None:
        mod = tmp_path / "m.py"
        mod.write_text(
            "class Outer:\n    class Inner:\n        alias = _hook\n",
            encoding="utf-8",
        )
        refs = collect_production_references([mod], tmp_path)
        assert refs.member_sites["_hook"] == {("m.py", 3)}

    def test_a_decorated_method_is_indexed_by_its_class(self, tmp_path: Path) -> None:
        mod = tmp_path / "m.py"
        mod.write_text(
            "class C:\n    @property\n    def add(self):\n        return 1\n"
            "    def plain(self):\n        return 2\n",
            encoding="utf-8",
        )
        refs = collect_production_references([mod], tmp_path)
        assert refs.decorated_methods == {("m.py", "C.add")}

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


class TestRepositoryScriptsAreProductionCallers:
    """WI-famub: a ``scripts/`` gate is a production caller the map cannot see.

    ``scripts/check-confidence-sites`` imports and calls four functions of
    ``confidence_sites``; the cron full-suite failed all four as test-only,
    because ``production_source_files`` globbed ``packages/*/src`` alone and
    repository scripts are extensionless. ``check_id_construction`` sat in the
    baseline for the same reason, and any future ``scripts/check-*`` backed by
    a core module would have tripped the gate on its first cron. Scripts VOTE
    as referencers; they are not themselves gated (``_is_production`` still
    admits only ``packages/*/src`` symbols to the cohort).
    """

    @staticmethod
    def _tree(tmp_path: Path) -> Path:
        prod = tmp_path / PROD
        prod.parent.mkdir(parents=True)
        prod.write_text("def scan_tree():\n    pass\n", encoding="utf-8")
        scripts = tmp_path / "scripts"
        (scripts / "lib").mkdir(parents=True)
        (scripts / "check-thing").write_text(
            "#!/usr/bin/env python3\nfrom p.mod import scan_tree\nscan_tree()\n",
            encoding="utf-8",
        )
        (scripts / "lib" / "helper.py").write_text(
            "x = from_a_module\n", encoding="utf-8",
        )
        (scripts / "run.sh").write_text(
            "#!/usr/bin/env bash\necho scan_tree\n", encoding="utf-8",
        )
        (scripts / "notes.txt").write_text("scan_tree in prose\n", encoding="utf-8")
        (scripts / "__pycache__").mkdir()
        (scripts / "__pycache__" / "helper.cpython-311.pyc").write_bytes(b"\x00\x01")
        return tmp_path

    def test_python_under_scripts_is_a_production_source(self, tmp_path: Path) -> None:
        """Extensionless with a Python shebang, or ``.py`` at any depth."""
        root = self._tree(tmp_path)
        rel = {str(f.relative_to(root)) for f in production_source_files(root)}
        assert rel == {PROD, "scripts/check-thing", "scripts/lib/helper.py"}

    def test_a_function_called_only_from_a_scripts_gate_is_not_test_only(
        self, tmp_path: Path,
    ) -> None:
        root = self._tree(tmp_path)
        refs = collect_production_references(production_source_files(root), root)
        symbol = TestOnlySymbol("s", "scan_tree", "function", PROD)

        assert refs.loads["scan_tree"] == {"scripts/check-thing"}
        assert demotion_reason(symbol, refs) == DEMOTION_CROSS_MODULE
        assert gated_cohort([symbol], refs) == ([], {DEMOTION_CROSS_MODULE: 1})

    def test_a_shell_script_is_not_a_python_source(self, tmp_path: Path) -> None:
        """``run.sh`` names ``scan_tree``; a shebang for another interpreter
        must not get it parsed, and prose in a text file must not vote."""
        root = self._tree(tmp_path)
        names = {f.name for f in production_source_files(root)}
        assert "run.sh" not in names
        assert "notes.txt" not in names

    def test_a_tree_without_scripts_is_just_the_packages(self, tmp_path: Path) -> None:
        prod = tmp_path / PROD
        prod.parent.mkdir(parents=True)
        prod.write_text("", encoding="utf-8")
        assert production_source_files(tmp_path) == [prod]

    def test_an_unreadable_script_is_left_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same conservative direction as the AST scan: fewer references,
        never a crash, for a gate that can delete work from a baseline."""
        root = self._tree(tmp_path)
        real_open = Path.open

        def open_unless_gate(self: Path, *args: object, **kwargs: object) -> object:
            if self.name == "check-thing":
                raise OSError("unreadable")
            return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", open_unless_gate)
        names = {f.name for f in production_source_files(root)}
        assert "check-thing" not in names
        assert "helper.py" in names


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

    def test_the_method_half_is_baselined_and_discriminates(self) -> None:
        """WI-mifag's adjudicated rows, asserted on this repository.

        ``Pattern.matches`` is the item's own example: it once scored 497
        production "references" off regex ``.matches``, and production in fact
        calls ``matches_all``. ``DuressHandler.filter_response`` is a Protocol
        hook nothing in production calls. The controls: Textual's ``action_*``
        handlers are dispatched by the framework, and ``CDefUseExtractor.
        extract`` is reached through the ``DefUseExtractor`` Protocol.
        """
        root = Path(__file__).resolve().parents[3]
        path = root / ".ci/test-only-reachability-baseline.json"
        methods = load_baseline(path) - load_baseline(path, methods=False)
        names = {k.rsplit("::", 1)[-1] for k in methods}
        assert {"Pattern.matches", "DuressHandler.filter_response"} <= names
        assert not {n for n in names if ".action_" in n}
        assert "CDefUseExtractor.extract" not in names

    def test_production_source_files_finds_the_shipped_tree(self) -> None:
        root = Path(__file__).resolve().parents[3]
        files = production_source_files(root)
        rel = [str(f.relative_to(root)) for f in files]
        assert len([r for r in rel if "/src/" in r]) > 100
        assert all(r.startswith(("packages/", "scripts/")) for r in rel)
        assert not any("/tests/" in r for r in rel)

    def test_the_scripts_gates_are_production_callers(self) -> None:
        """WI-famub's motivating examples, asserted on this repository.

        ``confidence_sites.scan_tree`` and ``check_id_construction.
        find_id_construction_drift`` have no caller under ``packages/*/src``:
        their production callers are ``scripts/check-confidence-sites`` and
        ``scripts/check-id-construction``. The cron failed the first as newly
        test-only (2026-10-16) and the baseline had carried the second.
        """
        root = Path(__file__).resolve().parents[3]
        files = production_source_files(root)
        rel = {str(f.relative_to(root)) for f in files}
        assert {"scripts/check-confidence-sites", "scripts/check-id-construction"} <= rel

        refs = collect_production_references(files, root)
        core = "packages/hypergumbo-core/src/hypergumbo_core"
        for name, module in [
            ("scan_tree", "confidence_sites"),
            ("find_id_construction_drift", "check_id_construction"),
        ]:
            symbol = TestOnlySymbol("x", name, "function", f"{core}/{module}.py")
            assert demotion_reason(symbol, refs) == DEMOTION_CROSS_MODULE, name



class TestTheMapCarriesReceiverTyping:
    """WI-losig: the CI self-map is built with the scip-python backend so a
    method reference resolves to its owning type (WI-mifag's prerequisite).
    An opt-in that silently falls back to the plain map would leave the gate
    reading a map the step claims is typed, so the gate can REQUIRE the pass."""

    def test_a_pass_that_ran_and_emitted_is_no_problem(self) -> None:
        data = {"analysis_runs": [{"pass": "scip_python", "nodes_emitted": 5}]}
        assert pass_contribution_problem(data, "scip_python") is None

    def test_a_skipped_pass_names_its_reason(self) -> None:
        data = {"limits": {"skipped_passes": [{
            "pass": "scip_python", "reason": "scip-python backend not enabled",
            "silence_reason": "backend_disabled",
        }]}, "analysis_runs": []}
        problem = pass_contribution_problem(data, "scip_python")
        assert problem is not None and "backend_disabled" in problem

    def test_a_missing_or_empty_pass_is_a_problem(self) -> None:
        assert "did not run" in str(pass_contribution_problem({}, "scip_python"))
        empty = {"analysis_runs": [{
            "pass": "scip_python", "nodes_emitted": 0, "silence_reason": "unreported",
        }]}
        assert "unreported" in str(pass_contribution_problem(empty, "scip_python"))

    def test_the_typed_share_counts_python_method_calls_carrying_the_origin(self) -> None:
        nodes = [
            {"id": "m1", "language": "python", "kind": "method"},
            {"id": "m2", "language": "python", "kind": "method"},
            {"id": "f", "language": "python", "kind": "function"},
            {"id": "jm", "language": "java", "kind": "method"},
        ]
        edges = [
            {"type": "calls", "dst": "m1", "origin": ["python", "scip"]},
            {"type": "calls", "dst": "m2", "origin": ["python"]},
            {"type": "calls", "dst": "f", "origin": ["scip"]},
            {"type": "calls", "dst": "jm", "origin": ["scip"]},
            {"type": "references", "dst": "m1", "origin": ["scip"]},
            {"type": "calls", "dst": "m1", "origin": "python"},
            {"type": "calls", "dst": "m2", "origin": "scip"},
            {"type": "calls", "dst": "m2"},
        ]
        assert typed_method_call_share(nodes, edges) == (2, 5)
