# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the INV-fahub receiver-blind magnet detector.

The detector is the single language-agnostic definition of "a receiver-blind
method magnet edge" shared by the spec_validator whole-graph check, the
per-analyzer magnet-gate tests, and the real-repro survey harness. These tests
lock every branch of the qualification predicate and both input shapes (live
dataclass-like objects and deserialized survey-JSON dicts), so a consumer can
trust the same verdict regardless of shape.
"""
from types import SimpleNamespace

from hypergumbo_core.receiver_blind_magnets import (
    demote_harmful_magnets,
    find_receiver_blind_magnets,
    owner_of,
)


# ---- owner_of -------------------------------------------------------------

def test_owner_of_none_and_empty():
    assert owner_of(None) is None
    assert owner_of("") is None


def test_owner_of_bare_free_function_has_no_owner():
    assert owner_of("helper") is None


def test_owner_of_each_separator():
    assert owner_of("GlobSet::len") == "GlobSet"       # Rust / C++
    assert owner_of("Json.to") == "Json"               # Go / most
    assert owner_of("Foo#bar") == "Foo"                # ruby-ish
    # nested — rsplit keeps the full owner path
    assert owner_of("a.b.c") == "a.b"


def test_owner_of_normalizes_generics():
    # a template/generic class folds to its base owner so a this/self call
    # inside it doesn't read as cross-class
    assert owner_of("AP4_Array<T>::method") == "AP4_Array"
    assert owner_of("Vec<T>::push") == "Vec"
    assert owner_of("Foo<Bar<T>>::x") == "Foo"            # nested generics
    assert owner_of("Foo<Bar::Baz>::x") == "Foo"          # :: inside the param
    # operator tails with a bare '<' don't crash; owner still resolves
    assert owner_of("Foo::operator<<") == "Foo"


def test_same_class_generic_call_not_flagged():
    # AP4_Array<T>::ctor calling a method of AP4_Array is a same-class this call
    nodes = [
        _node("AP4_Array<T>::ctor", "AP4_Array<T>::ctor", "method"),
        _node("AP4_Array::ItemCount", "AP4_Array::ItemCount", "method"),
    ]
    edges = [_edge(src="AP4_Array<T>::ctor", dst="AP4_Array::ItemCount")]
    assert find_receiver_blind_magnets(nodes, edges) == []


# ---- fixture helpers ------------------------------------------------------

def _node(nid, name, kind):
    return SimpleNamespace(id=nid, name=name, kind=kind, qualified_name=None)


def _edge(**kw):
    kw.setdefault("edge_type", "calls")
    kw.setdefault("is_resolved", True)
    kw.setdefault("evidence_type", "ast_call")
    kw.setdefault("confidence", 0.85)
    kw.setdefault("meta", {})
    return SimpleNamespace(**kw)


def _graph_with_magnet(**edge_overrides):
    """A minimal graph: caller A.run -> B.method via an untyped receiver."""
    nodes = [
        _node("A.run", "A.run", "method"),
        _node("B.method", "B.method", "method"),
    ]
    edge = _edge(src="A.run", dst="B.method", **edge_overrides)
    return nodes, [edge]


# ---- the qualification predicate -----------------------------------------

def test_genuine_cross_owner_magnet_is_flagged():
    nodes, edges = _graph_with_magnet()
    assert find_receiver_blind_magnets(nodes, edges) == edges


def test_dispatches_to_edge_type_also_flagged():
    nodes, edges = _graph_with_magnet(edge_type="dispatches_to")
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


def test_non_call_edge_skipped():
    nodes, edges = _graph_with_magnet(edge_type="imports")
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_unresolved_edge_skipped():
    nodes, edges = _graph_with_magnet(is_resolved=False)
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_receiver_aware_evidence_type_skipped():
    # typed binds / recoveries carry a receiver-aware evidence_type
    nodes, edges = _graph_with_magnet(evidence_type="ast_call_type_inferred")
    assert find_receiver_blind_magnets(nodes, edges) == []
    nodes, edges = _graph_with_magnet(evidence_type="ast_call_inherited")
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_receiver_marker_in_meta_skipped():
    nodes, edges = _graph_with_magnet(meta={"receiver": "typed_field"})
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_qualified_receiver_marker_skipped():
    # A scoped/static ``Type::method()`` call names the target type explicitly
    # (receiver="qualified"), so it is NOT receiver-blind — excluded even though
    # it resolves to a cross-owner method-kind symbol (the associated-function
    # false positive this marker exists to suppress).
    nodes, edges = _graph_with_magnet(meta={"receiver": "qualified"})
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_external_receiver_markers_skipped():
    for marker in ("external", "constant_external", "stdlib"):
        nodes, edges = _graph_with_magnet(meta={"receiver": marker})
        assert find_receiver_blind_magnets(nodes, edges) == [], marker


def test_bare_and_generic_receiver_still_flagged_cross_owner():
    # 'bare' (implicit-this/self) and 'generic' (receiver present but
    # unclassified) are receiver-blind: a cross-owner bind carrying them is still
    # a magnet. (The legitimate same-class implicit-this case is excluded by the
    # owner check, not by the marker — see test_same_owner_implicit_this_skipped.)
    for marker in ("bare", "generic"):
        nodes, edges = _graph_with_magnet(meta={"receiver": marker})
        assert len(find_receiver_blind_magnets(nodes, edges)) == 1, marker


def test_resolution_quality_typed_receiver_skipped():
    nodes, edges = _graph_with_magnet(meta={"resolution_quality": "typed_receiver"})
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_resolution_quality_ambiguous_is_not_a_receiver_marker():
    # "ambiguous" means the resolver gave up on the receiver — still a magnet.
    nodes, edges = _graph_with_magnet(meta={"resolution_quality": "ambiguous"})
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


def test_route_dispatch_edge_skipped():
    # A framework route registration (``mux.HandleFunc("/x", dr.handler)``) binds
    # a handler BY REFERENCE to the real method — a correctly-resolved dispatch,
    # not a receiver-blind method-CALL magnet. It only trips the owner check
    # because the call-site receiver var (``dr``) reads as an "owner" different
    # from the handler's class. ``meta.dispatch_kind == "route"`` marks it out of
    # scope. (Real repro: Go alertmanager ``deprecationHandler`` <- 8 routes.)
    nodes, edges = _graph_with_magnet(meta={"dispatch_kind": "route"})
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_route_handler_name_marker_skipped():
    # The same route-registration signal via the ``handler_name`` marker (some
    # route linkers stamp the handler ref without a dispatch_kind).
    nodes, edges = _graph_with_magnet(meta={"handler_name": "dr.deprecationHandler"})
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_confidence_below_threshold_skipped():
    nodes, edges = _graph_with_magnet(confidence=0.42)
    assert find_receiver_blind_magnets(nodes, edges) == []
    # ...but caught when the floor is dropped (fixture-gate mode)
    assert len(find_receiver_blind_magnets(nodes, edges, min_confidence=0.0)) == 1


def test_confidence_none_skipped():
    nodes, edges = _graph_with_magnet(confidence=None)
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_dst_not_a_method_skipped():
    nodes = [_node("A.run", "A.run", "method"), _node("B.thing", "B.thing", "function")]
    edges = [_edge(src="A.run", dst="B.thing")]
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_dst_absent_from_node_set_skipped():
    nodes = [_node("A.run", "A.run", "method")]
    edges = [_edge(src="A.run", dst="Z.gone")]
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_same_owner_implicit_this_skipped():
    nodes = [_node("B.run", "B.run", "method"), _node("B.method", "B.method", "method")]
    edges = [_edge(src="B.run", dst="B.method")]
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_free_function_caller_cross_owner_flagged():
    # a free function (no owner) calling x.method() -> B.method is receiver-blind
    nodes = [_node("helper", "helper", "function"), _node("B.method", "B.method", "method")]
    edges = [_edge(src="helper", dst="B.method")]
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


def test_src_absent_from_node_set_still_flagged():
    # src id resolves to no node -> src_owner is None -> cross-owner -> flagged
    nodes = [_node("B.method", "B.method", "method")]
    edges = [_edge(src="ghost", dst="B.method")]
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


def test_node_without_id_is_ignored():
    nodes = [SimpleNamespace(id=None, name="junk", kind="method"),
             _node("A.run", "A.run", "method"), _node("B.method", "B.method", "method")]
    edges = [_edge(src="A.run", dst="B.method")]
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


# ---- survey-JSON dict shape (evidence_type nested under meta, type key) ----

def test_dict_shape_survey_json():
    nodes = [
        {"id": "A.run", "name": "A.run", "kind": "method"},
        {"id": "B.method", "name": "B.method", "kind": "method"},
    ]
    edges = [{
        "type": "calls", "src": "A.run", "dst": "B.method",
        "is_resolved": True, "confidence": 0.85,
        "meta": {"evidence_type": "ast_call", "evidence_lang": "rust"},
    }]
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


def test_dict_shape_qualified_name_fallback_for_owner():
    # name lacks a separator; owner comes from qualified_name
    nodes = [
        {"id": "s", "name": "run", "qualified_name": "A.run", "kind": "method"},
        {"id": "d", "name": "method", "qualified_name": "B.method", "kind": "method"},
    ]
    edges = [{"type": "calls", "src": "s", "dst": "d", "is_resolved": True,
              "confidence": 0.85, "meta": {"evidence_type": "ast_call"}}]
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


# ---- demote_harmful_magnets (INV-fahub Phase-B gate) ----------------------
#
# The finalize demotion gates only the two CLEANLY-harmful magnet sub-classes
# (owner ruling): a production->test-helper misbind, and a stdlib-INTERFACE
# method shadow. It redirects the offending edge's dst to an external:unresolved
# id (finalize's ADR-0037 verdict then derives is_resolved=False) and stamps
# resolution_quality='ambiguous'. It KEEPS correct-but-unprovable binds (the
# snake_case trait-method funnel — Rust ``x.next()`` — is ADR-0012 scope).

def _pnode(nid, name, kind, path, language="go"):
    return SimpleNamespace(
        id=nid, name=name, kind=kind, qualified_name=None,
        path=path, language=language,
    )


def _prod_to(dst_name, dst_path, *, src_path="app/main.go", language="go"):
    """A resolved cross-owner untyped-receiver magnet: App.run -> <dst>."""
    nodes = [
        _pnode("src", "App.run", "method", src_path, language),
        _pnode("dst", dst_name, "method", dst_path, language),
    ]
    edges = [_edge(src="src", dst="dst")]
    return nodes, edges


def test_demote_production_to_test_helper_magnet():
    # App.run (production) -> Collector.Add whose def lives in test/testutils/:
    # a production->test-helper misbind. Demoted.
    nodes, edges = _prod_to("Collector.Add", "test/testutils/collector.go")
    demoted = demote_harmful_magnets(nodes, edges)
    assert demoted == edges
    assert edges[0].dst == "go:external:0-0:Add:unresolved"
    assert edges[0].meta.get("resolution_quality") == "ambiguous"
    # and it is no longer a magnet by the detector (dst left the node set)
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_demote_stdlib_interface_method_shadow():
    # x.Parse() -> a local Template.Parse: Parse is a stdlib-interface method
    # name (PascalCase), overwhelmingly a stdlib shadow on an untyped receiver.
    nodes, edges = _prod_to("Template.Parse", "template/template.go")
    demoted = demote_harmful_magnets(nodes, edges)
    assert demoted == edges
    assert edges[0].dst == "go:external:0-0:Parse:unresolved"


def test_keep_snake_case_trait_method_bind():
    # Rust ``x.next()`` -> Red::next is the correct-but-unprovable trait funnel
    # (ADR-0012 scope). snake_case ``next`` is NOT a stdlib-INTERFACE name, and
    # the def is not in a test-helper dir, so it is KEPT (still a magnet).
    nodes, edges = _prod_to(
        "Red::next", "src/source/noise.rs", src_path="src/lib.rs", language="rust"
    )
    demoted = demote_harmful_magnets(nodes, edges)
    assert demoted == []
    assert edges[0].dst == "dst"  # untouched
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


def test_keep_production_to_production_builder_bind():
    # App.run -> Args.append (both in app source, ``append`` not a stdlib-
    # interface name): a correct-but-unprovable builder bind. KEPT.
    nodes, edges = _prod_to("Args.append", "modules/addonlib/Args.scala",
                            src_path="modules/addonlib/Builder.scala", language="scala")
    assert demote_harmful_magnets(nodes, edges) == []
    assert edges[0].dst == "dst"


def test_keep_test_to_test_helper_bind():
    # A test file calling a test helper is legitimate — NOT a production
    # misbind. KEPT even though the dst is a test-helper.
    nodes, edges = _prod_to("Collector.Add", "test/testutils/collector.go",
                            src_path="cluster/cluster_test.go")
    assert demote_harmful_magnets(nodes, edges) == []


def test_demote_uses_src_language_for_external_id():
    # the external:unresolved id is built with the caller's language slot
    nodes, edges = _prod_to("Handler.Close", "internal/h.go", language="go")
    demote_harmful_magnets(nodes, edges)
    assert edges[0].dst.startswith("go:external:0-0:Close:")


def test_demote_empty_graph_is_noop():
    assert demote_harmful_magnets([], []) == []


def test_demote_bare_name_target_not_stdlib_kept():
    # A dst whose name has no owner separator (a free function-shaped name)
    # yields a bare method short-name; unless it is a stdlib-interface name it
    # is kept. Exercises _method_short_name's no-separator branch.
    nodes, edges = _prod_to("standalone", "app/util.go")
    assert demote_harmful_magnets(nodes, edges) == []


def test_demote_bare_stdlib_name_target_demoted():
    # ...and a bare dst name that IS a stdlib-interface method (Close) demotes.
    nodes, edges = _prod_to("Close", "app/util.go")
    demoted = demote_harmful_magnets(nodes, edges)
    assert len(demoted) == 1
    assert edges[0].dst == "go:external:0-0:Close:unresolved"


def test_demote_nameless_method_target_is_kept():
    # Defensive mirror of owner_of: a method-kind dst with no name yields a None
    # short-name — not a stdlib-interface name, and with no path not a
    # test-helper — so the magnet is kept (not demoted).
    nodes = [
        _pnode("s", "App.run", "method", "app/main.go"),
        SimpleNamespace(id="d", name="", kind="method", qualified_name=None,
                        path="", language="go"),
    ]
    edges = [_edge(src="s", dst="d")]
    assert demote_harmful_magnets(nodes, edges) == []


def test_demote_survey_json_dict_shape():
    # The demotion also runs on deserialized survey-JSON dicts (the real-repro
    # harness), mutating dict keys instead of dataclass attrs.
    nodes = [
        {"id": "s", "name": "App.run", "kind": "method", "path": "app/main.go",
         "language": "go"},
        {"id": "d", "name": "Collector.Add", "kind": "method",
         "path": "test/testutils/collector.go", "language": "go"},
    ]
    edges = [{"type": "calls", "src": "s", "dst": "d", "is_resolved": True,
              "confidence": 0.85, "meta": {"evidence_type": "ast_call"}}]
    demoted = demote_harmful_magnets(nodes, edges)
    assert len(demoted) == 1
    assert edges[0]["dst"] == "go:external:0-0:Add:unresolved"
    assert edges[0]["meta"]["resolution_quality"] == "ambiguous"
