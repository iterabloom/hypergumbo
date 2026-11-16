# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tier form of every part of a survey that is not a node (WI-lavum).

The rules are in ``tier_projection``'s docstring. Each test pins one rule
and, where a rule cuts, that the cut is visible and the count is true.
"""

from __future__ import annotations

import copy

import pytest

from hypergumbo_core.tier_projection import (
    SELECTION_SECTIONS,
    bound_lists,
    compact_edge,
    compact_entrypoint,
    compact_feature,
    group_records,
    list_cap,
    project_fixed_sections,
)

SKIPPED = [
    {"pass": "ruby", "reason": "no files matched", "silence_reason": "no_candidate_files"},
    {"pass": "haskell", "reason": "no files matched", "silence_reason": "no_candidate_files"},
    {"pass": "scip-python", "reason": "backend not enabled", "silence_reason": "backend_disabled"},
    {"pass": "ada", "reason": "no files matched", "silence_reason": "no_candidate_files"},
]


# ── grouping ────────────────────────────────────────────────────────────────

def test_records_equal_but_for_one_key_become_one_record() -> None:
    assert group_records(SKIPPED, "pass", "passes") == [
        {"reason": "no files matched", "silence_reason": "no_candidate_files",
         "passes": ["ada", "haskell", "ruby"]},
        {"reason": "backend not enabled", "silence_reason": "backend_disabled",
         "passes": ["scip-python"]},
    ]


def test_grouping_loses_nothing() -> None:
    """Every input record can be rebuilt from the groups."""
    rebuilt = [
        {**{k: v for k, v in group.items() if k != "passes"}, "pass": name}
        for group in group_records(SKIPPED, "pass", "passes") for name in group["passes"]
    ]
    key = lambda r: sorted(r.items())  # noqa: E731
    assert sorted(rebuilt, key=key) == sorted(SKIPPED, key=key)


def test_grouping_is_independent_of_record_and_key_order() -> None:
    shuffled = [dict(reversed(list(r.items()))) for r in reversed(SKIPPED)]
    assert group_records(shuffled, "pass", "passes") == group_records(SKIPPED, "pass", "passes")


def test_a_record_without_the_varying_key_is_its_own_group() -> None:
    records = [{"reason": "crashed"}, {"pass": "x", "reason": "crashed"}]
    assert group_records(records, "pass", "passes") == [{"reason": "crashed", "passes": ["x"]}]
    assert group_records([{"reason": "crashed"}], "pass", "passes") == [
        {"reason": "crashed", "passes": []}]


def test_groups_of_one_size_are_ordered_by_their_fields() -> None:
    records = [{"pass": "a", "reason": "zz"}, {"pass": "b", "reason": "aa"}]
    assert [g["reason"] for g in group_records(records, "pass", "passes")] == ["aa", "zz"]


# ── bounding ────────────────────────────────────────────────────────────────

def test_a_long_list_is_cut_and_says_how_long_it_was() -> None:
    assert bound_lists({"paths": list("abcdef")}, 2) == {
        "paths": ["a", "b"], "paths_total": 6}


def test_a_list_within_the_cap_is_untouched_and_carries_no_total() -> None:
    """The total's presence means "cut"; a reader must not see it otherwise."""
    assert bound_lists({"paths": ["a", "b"]}, 2) == {"paths": ["a", "b"]}
    assert bound_lists({"paths": []}, 2) == {"paths": []}


def test_bounding_reaches_every_depth() -> None:
    value = {"a": {"b": [{"c": list(range(9))}, {"c": [1]}, {"c": []}]}}
    assert bound_lists(value, 2) == {
        "a": {"b": [{"c": [0, 1], "c_total": 9}, {"c": [1]}], "b_total": 3}}


def test_bounding_copies_and_leaves_the_source_alone() -> None:
    value = {"paths": list("abcdef"), "n": {"m": [1, 2, 3]}}
    before = copy.deepcopy(value)
    bound_lists(value, 2)
    assert value == before


def test_a_list_of_sentences_is_kept_whole() -> None:
    """Caveats are statements, not an enumeration that grows with the
    repository: a tier must not drop four of six to save forty tokens."""
    caveats = ["dynamic imports (importlib, __import__)", "eval() and exec() calls",
               "runtime code generation", "monkey-patching", "metaprogramming patterns"]
    assert bound_lists({"not_captured": caveats}, 2) == {"not_captured": caveats}


def test_a_list_is_sentences_only_when_at_least_half_its_entries_have_a_space() -> None:
    mostly_paths = ["src/a.py", "src/b.py", "my docs/c.md"]
    assert bound_lists({"paths": mostly_paths}, 2) == {
        "paths": ["src/a.py", "src/b.py"], "paths_total": 3}
    half = ["one two", "three four", "a.py", "b.py"]
    assert bound_lists({"notes": half}, 2) == {"notes": half}
    mixed_types = ["one two", "three four", 5]
    assert bound_lists({"notes": mixed_types}, 2) == {
        "notes": ["one two", "three four"], "notes_total": 3}


def test_an_existing_total_is_not_overwritten() -> None:
    assert bound_lists({"paths": list("abc"), "paths_total": 99}, 2) == {
        "paths": ["a", "b"], "paths_total": 99}


def test_a_list_inside_a_list_is_left_whole() -> None:
    assert bound_lists({"rows": [[1, 2, 3], [4, 5, 6]]}, 2) == {"rows": [[1, 2, 3], [4, 5, 6]]}
    assert bound_lists(7, 2) == 7


@pytest.mark.parametrize(("budget", "cap"), [(1000, 2), (4000, 2), (16000, 8), (40000, 20), (64000, 20)])
def test_the_cap_follows_the_budget_alone(budget: int, cap: int) -> None:
    assert list_cap(budget) == cap


# ── the fixed sections of a map ─────────────────────────────────────────────

def _map() -> dict:
    return {
        "nodes": [{"id": f"n{i}"} for i in range(30)],
        "edges": [{"id": f"e{i}"} for i in range(30)],
        "entrypoints": [{"symbol_id": f"n{i}"} for i in range(30)],
        "features": [{"id": f"f{i}", "node_ids": [f"n{j}" for j in range(30)]} for i in range(30)],
        "nodes_summary": {"omitted": {"top_words": [{"word": str(i)} for i in range(30)]}},
        "features_summary": {"total": 30},
        "metrics": {"languages": {}},
        "limits": {
            "skipped_passes": list(SKIPPED),
            "failed_files": [{"analyzer": "cpp", "path": f"src/f{i}.h", "reason": "partial_parse"}
                             for i in range(9)],
            "tier_filtered_files": [f"vendor/v{i}.c" for i in range(40)],
            "not_captured": ["eval() and exec() calls", "dynamic imports", "runtime code generation"],
        },
        "profile": {"framework_evidence": {"spring": [f"java:A{i}" for i in range(50)]}},
        "a_section_added_later": {"things": list(range(100))},
        "schema_version": "0.21.0",
    }


def test_the_two_limits_lists_are_grouped_and_the_ungrouped_keys_are_absent() -> None:
    tiered = _map()
    project_fixed_sections(tiered, 16000)
    limits = tiered["limits"]
    assert "skipped_passes" not in limits and "failed_files" not in limits
    assert [g["passes"] for g in limits["skipped_passes_by_reason"]] == [
        ["ada", "haskell", "ruby"], ["scip-python"]]
    (group,) = limits["failed_files_by_reason"]
    assert group["analyzer"] == "cpp" and group["reason"] == "partial_parse"
    assert group["paths_total"] == 9 and len(group["paths"]) == 8


def test_every_repository_sized_list_is_bounded_with_its_true_length() -> None:
    tiered = _map()
    project_fixed_sections(tiered, 4000)
    assert tiered["limits"]["tier_filtered_files"] == ["vendor/v0.c", "vendor/v1.c"]
    assert tiered["limits"]["tier_filtered_files_total"] == 40
    assert tiered["profile"]["framework_evidence"] == {
        "spring": ["java:A0", "java:A1"], "spring_total": 50}
    assert len(tiered["limits"]["not_captured"]) == 3  # sentences: whole, above the cap
    assert "not_captured_total" not in tiered["limits"]


def test_a_section_this_module_never_heard_of_is_bounded_too() -> None:
    tiered = _map()
    project_fixed_sections(tiered, 4000)
    assert tiered["a_section_added_later"] == {"things": [0, 1], "things_total": 100}


def test_the_sections_the_selection_owns_are_not_cut() -> None:
    tiered = _map()
    before = copy.deepcopy({k: tiered[k] for k in SELECTION_SECTIONS})
    project_fixed_sections(tiered, 4000)
    assert {k: tiered[k] for k in SELECTION_SECTIONS} == before
    assert set(before) == SELECTION_SECTIONS  # reach: the fixture has every one


def test_the_source_limits_are_not_modified() -> None:
    source = _map()
    tiered = dict(source)  # a tier is a shallow copy of the survey
    project_fixed_sections(tiered, 4000)
    assert "skipped_passes" in source["limits"]
    assert len(source["limits"]["tier_filtered_files"]) == 40


def test_a_map_with_no_limits_is_projected_without_one() -> None:
    tiered = {"nodes": [], "profile": {"x": [1, 2, 3]}}
    project_fixed_sections(tiered, 4000)
    assert tiered == {"nodes": [], "profile": {"x": [1, 2], "x_total": 3}}


# ── records ─────────────────────────────────────────────────────────────────

EDGE = {
    "id": "edge:sha256:aa", "src": "a", "dst": "b", "type": "calls", "line": 7,
    "confidence": 0.85, "confidence_source": "emitter_constant", "edge_key": "edgekey:1",
    "is_resolved": True, "origin": ["python-ast-v1"], "origin_run_id": "uuid:1",
    "rank_score": 0.4, "derived_from": ["a", "b"],
    "meta": {"evidence_type": "ast_call_direct", "call_lines": [7, 9]},
}


def test_an_edge_keeps_what_a_graph_reader_needs() -> None:
    assert compact_edge(EDGE) == {
        "id": "edge:sha256:aa", "src": "a", "dst": "b", "type": "calls", "line": 7,
        "confidence": 0.85, "meta": {"evidence_type": "ast_call_direct"}}


def test_an_edge_with_no_evidence_type_gets_no_meta() -> None:
    for meta in ({}, None, {"call_lines": [1]}):
        assert "meta" not in compact_edge({**EDGE, "meta": meta})
    assert compact_edge({"src": "a", "dst": "b"}) == {"src": "a", "dst": "b"}


def test_an_entry_point_keeps_its_identity_and_confidence() -> None:
    entry = {"symbol_id": "a", "kind": "main_function", "label": "Python main()",
             "confidence": 0.8, "rank_score": 1.0, "meta": {"id": "entrypoint:1"}}
    assert compact_entrypoint(entry) == {
        "symbol_id": "a", "kind": "main_function", "label": "Python main()", "confidence": 0.8}


def test_a_feature_keeps_its_pointers_and_drops_its_maps() -> None:
    feature = {"id": "sha256:f", "name": "a", "entry_nodes": ["a"], "node_ids": ["a", "b"],
               "edge_ids": ["edge:sha256:aa"], "limits_hit": [],
               "node_depths": {"a": 0, "b": 1}, "node_tiers": {"a": 1, "b": 1},
               "query": {"entrypoint": "a", "max_hops": 3}}
    assert compact_feature(feature) == {
        "id": "sha256:f", "name": "a", "entry_nodes": ["a"], "node_ids": ["a", "b"],
        "edge_ids": ["edge:sha256:aa"]}
    assert compact_feature({**feature, "limits_hit": ["hop_limit"]})["limits_hit"] == ["hop_limit"]
