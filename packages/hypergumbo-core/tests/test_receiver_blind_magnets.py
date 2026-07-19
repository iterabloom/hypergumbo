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


def test_resolution_quality_typed_receiver_skipped():
    nodes, edges = _graph_with_magnet(meta={"resolution_quality": "typed_receiver"})
    assert find_receiver_blind_magnets(nodes, edges) == []


def test_resolution_quality_ambiguous_is_not_a_receiver_marker():
    # "ambiguous" means the resolver gave up on the receiver — still a magnet.
    nodes, edges = _graph_with_magnet(meta={"resolution_quality": "ambiguous"})
    assert len(find_receiver_blind_magnets(nodes, edges)) == 1


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
