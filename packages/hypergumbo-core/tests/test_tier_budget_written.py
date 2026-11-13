# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tiered survey file is within its token budget ON DISK (INV-nukuk).

A tier is written with ``indent=2, sort_keys=True``
(``safety_zones.user_out_open_json_dump``). The budget used to be checked
against ``json.dumps(map)`` with default separators, a form about a quarter
smaller than the file, so a tier could pass the check and exceed its budget
once written. And the check could only remove nodes: a map whose other
sections alone exceed the budget was written over budget and said nothing.

Every size assertion here is on the bytes of a file written by the real
writer, never on an estimate of them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core import compact
from hypergumbo_core.compact import format_tiered_behavior_map
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.safety_zones import user_out_open_json_dump
from hypergumbo_core.selection.token_budget import (
    CHARS_PER_TOKEN,
    estimate_json_tokens,
    estimate_written_json_tokens,
    written_json_text,
)


def _symbol(name: str, path: str = "src/main.py") -> Symbol:
    return Symbol(
        id=f"python:{path}:1-10:{name}:function", name=name, kind="function",
        language="python", path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
    )


def _edge(src: Symbol, dst: Symbol) -> Edge:
    return Edge(id=f"edge:{src.id}->{dst.id}", src=src.id, dst=dst.id,
                edge_type="calls", line=1, confidence=0.9, origin="test", origin_run_id="test")


def _dense_map(nodes: int = 60, fan_out: int = 8, **extra: Any) -> tuple[dict, list, list]:
    symbols = [_symbol(f"func_{i}") for i in range(nodes)]
    edges = [_edge(symbols[i], symbols[(i + j) % nodes])
             for i in range(nodes) for j in range(1, fan_out + 1)]
    behavior_map = {
        "nodes": [s.to_dict() for s in symbols],
        "edges": [e.to_dict() for e in edges],
        "entrypoints": [{"symbol_id": s.id, "kind": "main_function", "confidence": 0.9}
                        for s in symbols[:5]],
        **extra,
    }
    return behavior_map, symbols, edges


def _tokens_on_disk(tiered: dict, tmp_path: Path) -> int:
    out = tmp_path / "survey.tier.json"
    user_out_open_json_dump(out, tiered)
    return out.stat().st_size // CHARS_PER_TOKEN


# ── the written form has one definition ──────────────────────────────────────

def test_written_json_text_is_what_the_writer_writes(tmp_path: Path) -> None:
    """If the writer's layout changes and this helper does not, every budget
    check is measuring a file that is not written."""
    data = {"b": [1, {"y": None, "x": "é"}], "a": {"k": [[], {}]}, "c": "text"}
    out = tmp_path / "x.json"
    user_out_open_json_dump(out, data)
    assert out.read_text() == written_json_text(data)
    assert estimate_written_json_tokens(data) == out.stat().st_size // CHARS_PER_TOKEN


def test_the_written_form_is_larger_than_the_default_dump() -> None:
    """Reach: the two forms differ, so a check on one says nothing of the other."""
    behavior_map, _symbols, _edges = _dense_map()
    assert estimate_written_json_tokens(behavior_map) > estimate_json_tokens(behavior_map)


# ── a tier fits its budget as written ────────────────────────────────────────

@pytest.mark.parametrize("budget", [4000, 8000, 16000])
def test_a_tier_file_is_within_its_budget_on_disk(tmp_path: Path, budget: int) -> None:
    behavior_map, symbols, edges = _dense_map()
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    # Reach: the budget binds (nodes were left out) and the tier is not degenerate.
    assert 1 < len(tiered["nodes"]) < len(symbols)
    assert _tokens_on_disk(tiered, tmp_path) <= budget


def test_a_budget_the_whole_map_fits_removes_nothing(tmp_path: Path) -> None:
    behavior_map, symbols, edges = _dense_map(nodes=6, fan_out=2)
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, 64000)
    assert len(tiered["nodes"]) == len(symbols)
    assert _tokens_on_disk(tiered, tmp_path) <= 64000


# ── a tier says what it reached ──────────────────────────────────────────────

@pytest.mark.parametrize("budget", [4000, 16000, 64000])
def test_a_tier_records_the_size_it_was_written_at(tmp_path: Path, budget: int) -> None:
    behavior_map, symbols, edges = _dense_map()
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert tiered["tier_tokens"] == budget
    assert tiered["tier_tokens_used"] == _tokens_on_disk(tiered, tmp_path)


def test_a_budget_the_fixed_sections_exceed_is_reported_not_hidden(tmp_path: Path) -> None:
    """The shrink can only remove nodes. What does not depend on the
    selection and cannot be grouped or bounded (here one long string) can
    exceed the budget on its own; the file is then over budget, and it must
    say so in a field a reader can check."""
    fixed = {"partial_results_reason": "x" * 20000}
    behavior_map, symbols, edges = _dense_map(limits=fixed)
    budget = 4000
    assert estimate_written_json_tokens({"limits": fixed}) > budget  # reach
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert len(tiered["nodes"]) == 1
    on_disk = _tokens_on_disk(tiered, tmp_path)
    assert on_disk > budget
    assert tiered["tier_tokens_used"] == on_disk
    assert tiered["tier_tokens_used"] > tiered["tier_tokens"]


# ── the finished map is re-checked ───────────────────────────────────────────

def test_a_finished_map_larger_than_its_probe_is_searched_again(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The summary is re-derived when a selection is finished and can be
    larger than the copy the probes measured with. The budget is checked on
    the finished map, and the search resumes from it."""
    real = compact.recompute_view_summary

    def bulky(view_map: dict, *args: Any, **kwargs: Any) -> None:
        real(view_map, *args, **kwargs)
        view_map["nodes_summary"]["padding"] = "x" * 6000

    behavior_map, symbols, edges = _dense_map()
    budget = 8000
    plain = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    monkeypatch.setattr(compact, "recompute_view_summary", bulky)
    padded = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert 1 < len(padded["nodes"]) < len(plain["nodes"])  # reach: it had to give nodes up
    assert _tokens_on_disk(padded, tmp_path) <= budget
    assert padded["tier_tokens_used"] == _tokens_on_disk(padded, tmp_path)


def test_the_arrays_agree_with_each_other_after_the_shrink() -> None:
    behavior_map, symbols, edges = _dense_map()
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, 4000)
    ids = {n["id"] for n in tiered["nodes"]}
    assert all(e["src"] in ids and e["dst"] in ids for e in tiered["edges"])
    assert all(ep["symbol_id"] in ids for ep in tiered["entrypoints"])
    assert tiered["nodes_summary"]["included"]["count"] == len(ids)
    assert json.loads(written_json_text(tiered)) == tiered


# ── what a tier carries besides nodes (WI-lavum) ────────────────────────────

def _feature_for(symbols: list, edges: list) -> dict:
    """A feature that references every node and edge, as a slice from a hub
    entry point does on a real repository."""
    return {
        "id": "sha256:feature", "name": symbols[0].id,
        "entry_nodes": [symbols[0].id],
        "node_ids": [s.id for s in symbols],
        "edge_ids": [e.id for e in edges],
        "node_depths": {s.id: i for i, s in enumerate(symbols)},
        "node_tiers": {s.id: 1 for s in symbols},
        "query": {"entrypoint": symbols[0].id, "max_hops": 3, "exclude_tests": True},
        "limits_hit": [],
    }


def test_a_tier_does_not_leave_budget_unused_while_leaving_nodes_out(tmp_path: Path) -> None:
    """The shrink measures against the features of the selection it STARTED
    with. On a map whose features are large it removed nodes the finished
    file had room for: killbill's 16k tier kept one node with 2.2k tokens
    unused. A tier that left nodes out must have used its budget."""
    behavior_map, symbols, edges = _dense_map(nodes=120, fan_out=3)
    behavior_map["features"] = [
        {**_feature_for(symbols, edges), "id": f"sha256:feature{i}"} for i in range(6)]
    budget = 16000
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert len(tiered["nodes"]) < len(symbols)  # reach: the budget binds
    on_disk = _tokens_on_disk(tiered, tmp_path)
    assert on_disk <= budget
    assert on_disk >= 0.85 * budget, (on_disk, len(tiered["nodes"]))


def test_tier_edges_entry_points_and_features_are_slim(tmp_path: Path) -> None:
    behavior_map, symbols, edges = _dense_map()
    behavior_map["features"] = [_feature_for(symbols, edges)]
    for entry in behavior_map["entrypoints"]:
        entry.update(label="main()", rank_score=1.0, meta={"id": "entrypoint:x"})
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, 16000)
    assert tiered["edges"] and tiered["entrypoints"] and tiered["features"]  # reach
    for edge in tiered["edges"]:
        assert set(edge) <= {"id", "src", "dst", "type", "line", "confidence", "meta"}
        assert set(edge.get("meta", {})) <= {"evidence_type"}
    for entry in tiered["entrypoints"]:
        assert set(entry) == {"symbol_id", "kind", "label", "confidence"}
    for feature in tiered["features"]:
        assert not {"query", "node_depths", "node_tiers"} & set(feature)


def test_a_slim_tier_still_points_only_at_what_it_holds() -> None:
    """The pointer invariant (INV-titid) survives the slimming: a feature's
    node and edge ids resolve inside the tier."""
    behavior_map, symbols, edges = _dense_map()
    behavior_map["features"] = [_feature_for(symbols, edges)]
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, 8000)
    node_ids = {n["id"] for n in tiered["nodes"]}
    edge_ids = {e["id"] for e in tiered["edges"]}
    (feature,) = tiered["features"]
    assert feature["node_ids"] and set(feature["node_ids"]) <= node_ids
    assert set(feature["edge_ids"]) <= edge_ids
    assert set(feature["entry_nodes"]) <= node_ids


def test_the_source_map_is_not_modified_by_making_a_tier() -> None:
    behavior_map, symbols, edges = _dense_map(
        limits={"skipped_passes": [{"pass": f"p{i}", "reason": "no files matched"}
                                   for i in range(30)]})
    before = json.dumps(behavior_map, sort_keys=True)
    format_tiered_behavior_map(behavior_map, symbols, edges, 4000)
    assert json.dumps(behavior_map, sort_keys=True) == before


def test_records_that_differ_only_by_pass_no_longer_crowd_out_the_nodes(tmp_path: Path) -> None:
    """The fixture that used to exceed a 4k budget by its ``limits`` alone:
    400 skipped-pass records identical but for the pass name. Grouped and
    bounded, the tier fits and carries nodes."""
    fixed = {"skipped_passes": [{"pass": f"analyzer-{i}", "reason": "no files matched"}
                                for i in range(400)]}
    behavior_map, symbols, edges = _dense_map(limits=fixed)
    budget = 4000
    assert estimate_written_json_tokens({"limits": fixed}) > budget  # reach
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, budget)
    assert len(tiered["nodes"]) > 1
    assert _tokens_on_disk(tiered, tmp_path) <= budget
    (group,) = tiered["limits"]["skipped_passes_by_reason"]
    assert group["reason"] == "no files matched"
    assert group["passes_total"] == 400 and len(group["passes"]) == 2


def test_an_entry_point_leaves_the_tier_with_its_node() -> None:
    """Every node here is an entry point, so the nodes left out take entry
    points with them: what the tier lists are exactly the ones it holds."""
    behavior_map, symbols, edges = _dense_map()
    behavior_map["entrypoints"] = [
        {"symbol_id": s.id, "kind": "main_function", "confidence": 0.9} for s in symbols]
    tiered = format_tiered_behavior_map(behavior_map, symbols, edges, 4000)
    kept = {n["id"] for n in tiered["nodes"]}
    assert 1 < len(kept) < len(symbols)  # reach: nodes, and so entry points, were left out
    assert {ep["symbol_id"] for ep in tiered["entrypoints"]} == kept
