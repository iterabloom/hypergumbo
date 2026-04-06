# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the slice module (graph slicing for LLM context)."""
from typing import List


import pytest

from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.slice import (
    slice_graph,
    SliceQuery,
    find_entry_nodes,
    AmbiguousEntryError,
    rank_slice_nodes,
    SliceResult,
)


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    start_line: int = 1,
    end_line: int = 5,
    language: str = "python",
    meta: dict | None = None,
) -> Symbol:
    """Helper to create test symbols."""
    span = Span(start_line=start_line, end_line=end_line, start_col=0, end_col=10)
    sym_id = f"{language}:{path}:{start_line}-{end_line}:{name}:{kind}"
    return Symbol(
        id=sym_id,
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=span,
        origin=f"{language}-ast-v1",
        origin_run_id="uuid:test",
        meta=meta,
    )


def make_edge(
    src: Symbol,
    dst: Symbol,
    edge_type: str = "calls",
    confidence: float = 0.85,
) -> Edge:
    """Helper to create test edges."""
    return Edge.create(
        src=src.id,
        dst=dst.id,
        edge_type=edge_type,
        line=src.span.start_line,
        origin="python-ast-v1",
        origin_run_id="uuid:test",
        confidence=confidence,
    )


class TestFindEntryNodes:
    """Tests for finding entry nodes by various match criteria."""

    def test_find_by_exact_name(self) -> None:
        """Match entry by exact function name."""
        sym_a = make_symbol("login")
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_partial_name(self) -> None:
        """Match entry by partial name (contains)."""
        sym_a = make_symbol("user_login")
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_file_path(self) -> None:
        """Match entry by file path."""
        sym_a = make_symbol("func_a", path="src/auth.py")
        sym_b = make_symbol("func_b", path="src/db.py")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, "src/auth.py")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_path_suffix(self) -> None:
        """Match entry by path suffix (relative path matches absolute)."""
        # Nodes have absolute paths as stored in behavior map
        sym_a = make_symbol("func_a", path="/home/user/repo/src/auth.py")
        sym_b = make_symbol("func_b", path="/home/user/repo/src/db.py")
        nodes = [sym_a, sym_b]

        # User provides relative path
        matches = find_entry_nodes(nodes, "src/auth.py")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_path_suffix_nested(self) -> None:
        """Path suffix matching works for nested paths."""
        sym_a = make_symbol("main", path="/home/user/project/src/frontend/main.go")
        sym_b = make_symbol("main", path="/home/user/project/src/backend/main.go")
        nodes = [sym_a, sym_b]

        # Match specific nested path
        matches = find_entry_nodes(nodes, "frontend/main.go")

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_by_path_suffix_no_partial_filename(self) -> None:
        """Path suffix must match at directory boundary, not partial filename."""
        sym_a = make_symbol("handler", path="/home/user/repo/src/auth.py")
        sym_b = make_symbol("handler", path="/home/user/repo/src/unauth.py")
        nodes = [sym_a, sym_b]

        # "auth.py" should only match auth.py, not unauth.py
        matches = find_entry_nodes(nodes, "src/auth.py")

        assert len(matches) == 1
        assert "auth.py" in matches[0].path
        assert "unauth.py" not in matches[0].path

    def test_find_by_node_id(self) -> None:
        """Match entry by exact node ID."""
        sym_a = make_symbol("login", path="src/auth.py", start_line=10, end_line=20)
        sym_b = make_symbol("logout")
        nodes = [sym_a, sym_b]

        matches = find_entry_nodes(nodes, sym_a.id)

        assert len(matches) == 1
        assert matches[0].id == sym_a.id

    def test_find_multiple_matches(self) -> None:
        """Multiple nodes can match the same entry spec."""
        sym_a = make_symbol("handle_login")
        sym_b = make_symbol("login_user")
        sym_c = make_symbol("logout")
        nodes = [sym_a, sym_b, sym_c]

        matches = find_entry_nodes(nodes, "login")

        assert len(matches) == 2
        ids = {m.id for m in matches}
        assert sym_a.id in ids
        assert sym_b.id in ids

    def test_find_no_match(self) -> None:
        """Returns empty list when no match found."""
        sym_a = make_symbol("login")
        nodes = [sym_a]

        matches = find_entry_nodes(nodes, "nonexistent")

        assert matches == []

    def test_find_with_language_filter(self) -> None:
        """Language filter restricts matches to specified language."""
        py_main = make_symbol("main", path="src/app.py", language="python")
        js_main = make_symbol("main", path="src/index.js", language="javascript")
        nodes = [py_main, js_main]

        # Without filter: both match
        matches = find_entry_nodes(nodes, "main")
        assert len(matches) == 2

        # With python filter: only Python matches
        matches = find_entry_nodes(nodes, "main", language="python")
        assert len(matches) == 1
        assert matches[0].language == "python"

        # With javascript filter: only JS matches
        matches = find_entry_nodes(nodes, "main", language="javascript")
        assert len(matches) == 1
        assert matches[0].language == "javascript"


class TestAmbiguousEntryDetection:
    """Tests for detecting and reporting ambiguous entry points."""

    def test_raises_error_when_same_name_in_different_files(self) -> None:
        """Should raise AmbiguousEntryError when name matches symbols in different files."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        nodes = [py_ping, ts_ping]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="ping", max_hops=3, max_files=20)

        with pytest.raises(AmbiguousEntryError) as exc_info:
            slice_graph(nodes, edges, query)

        # Error message should include helpful information
        error = exc_info.value
        assert "ping" in str(error)
        assert len(error.candidates) == 2
        assert py_ping.id in [c.id for c in error.candidates]
        assert ts_ping.id in [c.id for c in error.candidates]

    def test_no_error_when_exact_id_used(self) -> None:
        """Should not raise error when entry is specified by exact node ID."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        nodes = [py_ping, ts_ping]
        edges: List[Edge] = []

        # Use exact ID - should work without ambiguity
        query = SliceQuery(entrypoint=py_ping.id, max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert py_ping.id in result.node_ids
        assert ts_ping.id not in result.node_ids

    def test_no_error_when_language_filter_used(self) -> None:
        """Language filter avoids ambiguity by restricting to one language."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        nodes = [py_ping, ts_ping]
        edges: List[Edge] = []

        # Use language filter - should not be ambiguous
        query = SliceQuery(entrypoint="ping", max_hops=3, max_files=20, language="python")
        result = slice_graph(nodes, edges, query)

        assert py_ping.id in result.node_ids
        assert ts_ping.id not in result.node_ids

        # Language should be in the query dict
        query_dict = query.to_dict()
        assert query_dict["language"] == "python"

    def test_no_error_when_same_name_same_file(self) -> None:
        """Multiple matches in same file are OK (e.g., overloads or nested)."""
        func1 = make_symbol("handler", path="src/app.py", start_line=1, end_line=5)
        func2 = make_symbol("handler", path="src/app.py", start_line=10, end_line=15)
        nodes = [func1, func2]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="handler", max_hops=3, max_files=20)
        # Should not raise - same file is fine
        result = slice_graph(nodes, edges, query)

        # Both should be in the result
        assert func1.id in result.node_ids
        assert func2.id in result.node_ids

    def test_error_message_includes_file_paths(self) -> None:
        """Error message should help user disambiguate by showing paths."""
        py_ping = make_symbol("ping", path="src/app.py", language="python")
        ts_ping = make_symbol("ping", path="web/client.ts", language="typescript")
        go_ping = make_symbol("ping", path="cmd/server.go", language="go")
        nodes = [py_ping, ts_ping, go_ping]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="ping", max_hops=3, max_files=20)

        with pytest.raises(AmbiguousEntryError) as exc_info:
            slice_graph(nodes, edges, query)

        error_msg = str(exc_info.value)
        assert "src/app.py" in error_msg
        assert "web/client.ts" in error_msg
        assert "cmd/server.go" in error_msg


class TestSliceGraph:
    """Tests for BFS graph slicing."""

    def test_slice_single_node_no_edges(self) -> None:
        """Slice from a node with no outgoing edges."""
        sym_a = make_symbol("isolated")
        nodes = [sym_a]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="isolated", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 1
        assert sym_a.id in result.node_ids
        assert len(result.edge_ids) == 0
        assert result.limits_hit == []

    def test_slice_follows_calls(self) -> None:
        """Slice follows call edges."""
        sym_a = make_symbol("caller", start_line=1, end_line=5)
        sym_b = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_a, sym_b, "calls")
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="caller", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert len(result.edge_ids) == 1
        assert edge.id in result.edge_ids

    def test_slice_follows_imports(self) -> None:
        """Slice follows import edges."""
        sym_a = make_symbol("main", path="src/main.py")
        sym_b = make_symbol("helper", path="src/utils.py")
        edge = make_edge(sym_a, sym_b, "imports")
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="main", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_b.id in result.node_ids

    def test_slice_respects_hop_limit(self) -> None:
        """Slice stops at max_hops depth."""
        # Create chain: a -> b -> c -> d
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)
        sym_c = make_symbol("c", start_line=5, end_line=6)
        sym_d = make_symbol("d", start_line=7, end_line=8)

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)
        edge_cd = make_edge(sym_c, sym_d)

        nodes = [sym_a, sym_b, sym_c, sym_d]
        edges = [edge_ab, edge_bc, edge_cd]

        query = SliceQuery(entrypoint="a", max_hops=2, max_files=20)
        result = slice_graph(nodes, edges, query)

        # With max_hops=2: a (hop 0) -> b (hop 1) -> c (hop 2), but NOT d
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_c.id in result.node_ids
        assert sym_d.id not in result.node_ids
        assert "hop_limit" in result.limits_hit

    def test_slice_respects_file_limit(self) -> None:
        """Slice stops when max_files is reached."""
        # Create nodes in different files
        sym_a = make_symbol("a", path="file1.py")
        sym_b = make_symbol("b", path="file2.py")
        sym_c = make_symbol("c", path="file3.py")

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_ab, edge_bc]

        query = SliceQuery(entrypoint="a", max_hops=10, max_files=2)
        result = slice_graph(nodes, edges, query)

        # Should only include nodes from 2 files
        files_in_result = {n.split(":")[1] for n in result.node_ids}
        assert len(files_in_result) <= 2
        assert "file_limit" in result.limits_hit

    def test_slice_filters_low_confidence(self) -> None:
        """Slice can exclude edges below confidence threshold."""
        sym_a = make_symbol("caller", start_line=1, end_line=2)
        sym_b = make_symbol("callee_high", start_line=3, end_line=4)
        sym_c = make_symbol("callee_low", start_line=5, end_line=6)

        edge_high = make_edge(sym_a, sym_b, confidence=0.90)
        edge_low = make_edge(sym_a, sym_c, confidence=0.40)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_high, edge_low]

        query = SliceQuery(
            entrypoint="caller",
            max_hops=3,
            max_files=20,
            min_confidence=0.50,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_c.id not in result.node_ids
        assert edge_high.id in result.edge_ids
        assert edge_low.id not in result.edge_ids

    def test_slice_excludes_tests(self) -> None:
        """Slice can exclude test files."""
        sym_a = make_symbol("main", path="src/main.py")
        sym_b = make_symbol("helper", path="src/utils.py")
        sym_test = make_symbol("test_main", path="tests/test_main.py")

        edge_to_helper = make_edge(sym_a, sym_b)
        edge_to_test = make_edge(sym_a, sym_test)

        nodes = [sym_a, sym_b, sym_test]
        edges = [edge_to_helper, edge_to_test]

        query = SliceQuery(
            entrypoint="main",
            max_hops=3,
            max_files=20,
            exclude_tests=True,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_test.id not in result.node_ids

    def test_slice_excludes_utility(self) -> None:
        """Forward slice skips utility files (docs, examples, scripts) when requested."""
        sym_a = make_symbol("main", path="src/app.py")
        sym_b = make_symbol("helper", path="src/utils.py")
        sym_docs = make_symbol("example_handler", path="docs_src/example.py")
        sym_scripts = make_symbol("deploy", path="scripts/deploy.py")

        edge_to_helper = make_edge(sym_a, sym_b)
        edge_to_docs = make_edge(sym_a, sym_docs)
        edge_to_scripts = make_edge(sym_a, sym_scripts)

        nodes = [sym_a, sym_b, sym_docs, sym_scripts]
        edges = [edge_to_helper, edge_to_docs, edge_to_scripts]

        query = SliceQuery(
            entrypoint="main",
            max_hops=3,
            max_files=20,
            exclude_utility=True,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_docs.id not in result.node_ids
        assert sym_scripts.id not in result.node_ids

    def test_slice_handles_cycles(self) -> None:
        """Slice handles cyclic references without infinite loop."""
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)

        edge_ab = make_edge(sym_a, sym_b)
        edge_ba = make_edge(sym_b, sym_a)

        nodes = [sym_a, sym_b]
        edges = [edge_ab, edge_ba]

        query = SliceQuery(entrypoint="a", max_hops=10, max_files=20)
        result = slice_graph(nodes, edges, query)

        # Should visit both nodes exactly once
        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids


class TestHubNodePruning:
    """Tests for hub node pruning during slice traversal.

    Hub nodes are high-degree nodes (e.g., utility functions imported by
    everything) that cause BFS expansion explosion. When hub_threshold is
    set, nodes whose out-degree exceeds the threshold are included in the
    slice result but NOT traversed through, preventing exponential growth.
    """

    def test_hub_node_not_traversed_when_threshold_exceeded(self) -> None:
        """Node with out-degree > hub_threshold at depth ≥ 2 is not expanded.

        Depth-1 nodes are exempt from hub pruning (to support the common
        main → run() orchestrator pattern), so the hub must be at depth 2+.
        """
        # Entry -> bridge -> hub -> {callee_1, callee_2, ..., callee_30}
        entry = make_symbol("entry", path="src/entry.py")
        bridge = make_symbol("init", path="src/init.py", start_line=1, end_line=5)
        hub = make_symbol("isNil", path="src/utils.py", start_line=10, end_line=15)
        callees = [
            make_symbol(f"callee_{i}", path=f"src/mod_{i}.py", start_line=1, end_line=5)
            for i in range(30)
        ]

        edges_list: List[Edge] = [make_edge(entry, bridge), make_edge(bridge, hub)]
        for callee in callees:
            edges_list.append(make_edge(hub, callee))

        all_nodes = [entry, bridge, hub] + callees

        # With hub_threshold=None: BFS expands through hub to all callees
        query_no_prune = SliceQuery(
            entrypoint="entry", max_hops=5, max_files=100,
            hub_threshold=None,
        )
        result_no_prune = slice_graph(all_nodes, edges_list, query_no_prune)
        assert len(result_no_prune.node_ids) == 33  # entry + bridge + hub + 30

        # With hub_threshold=20: hub (depth 2) is included but not expanded
        query_prune = SliceQuery(
            entrypoint="entry", max_hops=5, max_files=100,
            hub_threshold=20,
        )
        result_prune = slice_graph(all_nodes, edges_list, query_prune)

        # Entry, bridge, and hub should be included
        assert entry.id in result_prune.node_ids
        assert bridge.id in result_prune.node_ids
        assert hub.id in result_prune.node_ids
        # But the 30 callees should NOT be reached (hub at depth 2 was pruned)
        for callee in callees:
            assert callee.id not in result_prune.node_ids
        # Should report hub_pruned in limits_hit
        assert "hub_pruned" in result_prune.limits_hit

    def test_hub_below_threshold_is_traversed_normally(self) -> None:
        """Node with out-degree <= hub_threshold is expanded normally."""
        entry = make_symbol("entry", path="src/entry.py")
        normal = make_symbol("helper", path="src/helper.py", start_line=10, end_line=15)
        callee_a = make_symbol("a", path="src/a.py")
        callee_b = make_symbol("b", path="src/b.py")

        edges_list: List[Edge] = [
            make_edge(entry, normal),
            make_edge(normal, callee_a),
            make_edge(normal, callee_b),
        ]
        all_nodes = [entry, normal, callee_a, callee_b]

        query = SliceQuery(
            entrypoint="entry", max_hops=5, max_files=100,
            hub_threshold=20,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # All nodes should be visited (normal has out-degree 2, below threshold 20)
        assert len(result.node_ids) == 4
        assert callee_a.id in result.node_ids
        assert callee_b.id in result.node_ids

    def test_hub_threshold_none_disables_pruning(self) -> None:
        """Explicit hub_threshold=None disables pruning entirely."""
        entry = make_symbol("entry", path="src/entry.py")
        hub = make_symbol("hub", path="src/hub.py", start_line=10, end_line=15)
        callees = [
            make_symbol(f"c_{i}", path=f"src/c_{i}.py", start_line=1, end_line=5)
            for i in range(30)
        ]

        edges_list: List[Edge] = [make_edge(entry, hub)]
        for c in callees:
            edges_list.append(make_edge(hub, c))

        all_nodes = [entry, hub] + callees

        query = SliceQuery(
            entrypoint="entry", max_hops=5, max_files=100,
            hub_threshold=None,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # All 32 nodes visited (no pruning)
        assert len(result.node_ids) == 32
        assert "hub_pruned" not in result.limits_hit

    def test_entry_node_exempt_from_hub_pruning(self) -> None:
        """Entry node is always expanded even if it exceeds hub_threshold."""
        # Entry itself has 40 outgoing edges (exceeds threshold of 20)
        entry = make_symbol("entry", path="src/entry.py")
        callees = [
            make_symbol(f"callee_{i}", path=f"src/callee_{i}.py")
            for i in range(40)
        ]

        edges_list: List[Edge] = []
        for callee in callees:
            edges_list.append(make_edge(entry, callee))

        all_nodes = [entry] + callees

        query = SliceQuery(
            entrypoint="entry", max_hops=5, max_files=100,
            hub_threshold=20,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # Entry is exempt from pruning, so all 41 nodes should be visited
        assert len(result.node_ids) == 41
        assert entry.id in result.node_ids
        for callee in callees:
            assert callee.id in result.node_ids

    def test_default_hub_threshold_prunes_large_hubs(self) -> None:
        """Default hub_threshold=50 prunes nodes at depth ≥ 2 with >50 edges."""
        entry = make_symbol("entry", path="src/entry.py")
        bridge = make_symbol("setup", path="src/setup.py", start_line=1, end_line=5)
        hub = make_symbol("mega_hub", path="src/utils.py", start_line=10, end_line=15)
        callees = [
            make_symbol(f"callee_{i}", path=f"src/mod_{i}.py", start_line=1, end_line=5)
            for i in range(60)
        ]

        edges_list: List[Edge] = [make_edge(entry, bridge), make_edge(bridge, hub)]
        for callee in callees:
            edges_list.append(make_edge(hub, callee))

        all_nodes = [entry, bridge, hub] + callees

        # Default hub_threshold=50: hub at depth 2 has 60 edges (> 50), pruned
        query = SliceQuery(
            entrypoint="entry", max_hops=5, max_files=100,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # Entry, bridge, and hub included; hub's 60 callees NOT reached
        assert entry.id in result.node_ids
        assert bridge.id in result.node_ids
        assert hub.id in result.node_ids
        for callee in callees:
            assert callee.id not in result.node_ids
        assert "hub_pruned" in result.limits_hit

    def test_hub_threshold_uses_forward_degree_only(self) -> None:
        """Hub threshold counts outgoing edges, not incoming for forward slice."""
        # Node with many incoming edges but few outgoing should NOT be pruned
        target = make_symbol("target", path="src/target.py", start_line=10, end_line=15)
        callers = [
            make_symbol(f"caller_{i}", path=f"src/caller_{i}.py")
            for i in range(30)
        ]
        # Target calls only one function
        downstream = make_symbol("downstream", path="src/downstream.py")

        edges_list: List[Edge] = []
        for caller in callers:
            edges_list.append(make_edge(caller, target))
        edges_list.append(make_edge(target, downstream))
        # Add an entry that calls target
        entry = make_symbol("entry", path="src/entry.py")
        edges_list.append(make_edge(entry, target))

        all_nodes = [entry, target, downstream] + callers

        query = SliceQuery(
            entrypoint="entry", max_hops=5, max_files=100,
            hub_threshold=5,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # target has 1 outgoing edge (to downstream), way below threshold
        # So it should be traversed normally
        assert target.id in result.node_ids
        assert downstream.id in result.node_ids

    def test_reverse_slice_hub_uses_incoming_degree(self) -> None:
        """In reverse slice, hub threshold counts incoming edges at depth ≥ 2.

        Depth-1 nodes are exempt from hub pruning in both forward and
        reverse slices, so the hub must be at depth 2+ to be pruned.
        """
        # many callers -> hub -> bridge -> target_fn
        # Reverse slice from target_fn: bridge (depth 1) exempt,
        # hub (depth 2) has 30 incoming edges → pruned at threshold=20.
        entry2 = make_symbol("target_fn", path="src/target.py")
        bridge2 = make_symbol("handler", path="src/handler.py", start_line=5, end_line=10)
        hub2 = make_symbol("dispatcher", path="src/dispatch.py", start_line=10, end_line=15)
        callers2 = [
            make_symbol(f"src_{i}", path=f"src/src_{i}.py")
            for i in range(30)
        ]

        edges2: List[Edge] = [make_edge(bridge2, entry2), make_edge(hub2, bridge2)]
        for c in callers2:
            edges2.append(make_edge(c, hub2))

        all_nodes2 = [entry2, bridge2, hub2] + callers2

        query = SliceQuery(
            entrypoint="target_fn", max_hops=5, max_files=100,
            hub_threshold=20, reverse=True,
        )
        result = slice_graph(all_nodes2, edges2, query)

        assert entry2.id in result.node_ids
        assert bridge2.id in result.node_ids
        assert hub2.id in result.node_ids
        # The 30 callers of hub2 should NOT be expanded (hub at depth 2)
        for c in callers2:
            assert c.id not in result.node_ids
        assert "hub_pruned" in result.limits_hit


class TestSliceResult:
    """Tests for SliceResult structure and feature ID generation."""

    def test_feature_id_is_deterministic(self) -> None:
        """Same query produces same feature ID."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)

        result1 = slice_graph(nodes, edges, query)
        result2 = slice_graph(nodes, edges, query)

        assert result1.feature_id == result2.feature_id

    def test_feature_id_changes_with_query(self) -> None:
        """Different queries produce different feature IDs."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query1 = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)
        query2 = SliceQuery(entrypoint="entry", max_hops=5, max_files=20)

        result1 = slice_graph(nodes, edges, query1)
        result2 = slice_graph(nodes, edges, query2)

        assert result1.feature_id != result2.feature_id

    def test_to_dict_produces_valid_feature(self) -> None:
        """SliceResult.to_dict produces spec-compliant feature structure."""
        sym_a = make_symbol("entry")
        sym_b = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_a, sym_b)
        nodes = [sym_a, sym_b]
        edges = [edge]

        query = SliceQuery(entrypoint="entry", max_hops=3, max_files=20)
        result = slice_graph(nodes, edges, query)
        feature = result.to_dict()

        assert "id" in feature
        assert feature["id"].startswith("sha256:")
        assert feature["name"] == "entry"
        assert "entry_nodes" in feature
        assert "node_ids" in feature
        assert "edge_ids" in feature
        assert "query" in feature
        assert feature["query"]["method"] == "bfs"
        assert feature["query"]["entrypoint"] == "entry"
        assert feature["query"]["hops"] == 3
        assert feature["query"]["max_files"] == 20
        assert "limits_hit" in feature


class TestSliceQuery:
    """Tests for SliceQuery dataclass."""

    def test_query_defaults(self) -> None:
        """Query has sensible defaults."""
        query = SliceQuery(entrypoint="foo")

        assert query.max_hops is None
        assert query.max_files == 100
        assert query.min_confidence == 0.0
        assert query.exclude_tests is False
        assert query.method == "bfs"
        assert query.hub_threshold == 50

    def test_query_to_dict(self) -> None:
        """Query serializes to dict for feature output."""
        query = SliceQuery(
            entrypoint="foo",
            max_hops=5,
            max_files=10,
            exclude_tests=True,
        )

        d = query.to_dict()

        assert d["method"] == "bfs"
        assert d["entrypoint"] == "foo"
        assert d["hops"] == 5
        assert d["max_files"] == 10
        assert d["exclude_tests"] is True

    def test_query_to_dict_with_hub_threshold(self) -> None:
        """Hub threshold is included in dict when set."""
        query = SliceQuery(entrypoint="bar", hub_threshold=25)
        d = query.to_dict()
        assert d["hub_threshold"] == 25

    def test_query_to_dict_omits_hub_threshold_when_explicit_none(self) -> None:
        """Hub threshold is omitted from dict when explicitly set to None."""
        query = SliceQuery(entrypoint="bar", hub_threshold=None)
        d = query.to_dict()
        assert "hub_threshold" not in d

    def test_query_to_dict_includes_default_hub_threshold(self) -> None:
        """Default hub_threshold (50) is included in dict."""
        query = SliceQuery(entrypoint="bar")
        d = query.to_dict()
        assert d["hub_threshold"] == 50


class TestIsTestFile:
    """Tests for test file detection patterns."""

    def test_test_underscore_prefix(self) -> None:
        """Detect test_ prefix in path."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("test_main.py")

    def test_tests_dir_prefix(self) -> None:
        """Detect tests/ directory prefix."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("tests/main.py")

    def test_underscore_test_suffix(self) -> None:
        """Detect _test.py suffix."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("main_test.py")
        assert is_test_file("main_test.js")
        assert is_test_file("main_test.ts")

    def test_dot_test_suffix(self) -> None:
        """Detect .test.py suffix."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("main.test.py")
        assert is_test_file("main.test.js")
        assert is_test_file("main.test.ts")

    def test_spec_patterns(self) -> None:
        """Detect spec patterns."""
        from hypergumbo_core.paths import is_test_file
        # Root-level spec/ is a test directory (Ruby RSpec convention)
        assert is_test_file("spec/main.py")
        # Nested spec/ is NOT a test directory (production interface defs)
        assert not is_test_file("src/spec/main.py")
        # Filename patterns still work regardless of directory
        assert is_test_file("main_spec.py")
        assert is_test_file("main.spec.js")

    def test_not_test_file(self) -> None:
        """Non-test files return False."""
        from hypergumbo_core.paths import is_test_file
        assert not is_test_file("src/main.py")
        assert not is_test_file("utils.py")

    def test_go_test_suffix(self) -> None:
        """Detect Go _test.go suffix."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("main_test.go")
        assert is_test_file("pkg/handlers/user_test.go")

    def test_mock_filename_patterns(self) -> None:
        """Detect *_mock.* and mock_*.* filename patterns."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("user_mock.go")
        assert is_test_file("src/mock_service.py")

    def test_fake_filename_patterns(self) -> None:
        """Detect *_fake.* and fake_*.* filename patterns."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("handler_fake.ts")
        assert is_test_file("pkg/fake_client.go")

    def test_mock_directories(self) -> None:
        """Detect files in fakes/, mocks/, fixtures/, testdata/, testutils/ directories."""
        from hypergumbo_core.paths import is_test_file
        assert is_test_file("pkg/fakes/handler.go")
        assert is_test_file("src/mocks/service.ts")
        assert is_test_file("tests/fixtures/data.json")
        assert is_test_file("pkg/testdata/sample.txt")
        assert is_test_file("internal/testutils/helpers.go")

    def test_compound_directory_names(self) -> None:
        """Detect files in directories ending with 'fakes' or 'mocks'."""
        from hypergumbo_core.paths import is_test_file
        # These hit endswith("fakes") and endswith("mocks") specifically
        assert is_test_file("pkg/rtc/transport/transportfakes/handler.go")
        assert is_test_file("internal/servicemocks/client.go")


class TestSliceEdgeCases:
    """Edge case tests for slice functionality."""

    def test_entry_node_is_test_file_excluded(self) -> None:
        """Entry node in test file is excluded when exclude_tests=True."""
        sym = make_symbol("test_main", path="tests/test_main.py")
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="test_main", max_hops=3, exclude_tests=True)
        result = slice_graph(nodes, edges, query)

        # Entry node should be excluded, so no nodes in result
        assert len(result.node_ids) == 0

    def test_entry_node_with_test_annotation_excluded(self) -> None:
        """Entry node with #[test] annotation excluded when exclude_tests=True.

        Rust test functions live in production files (src/lib.rs) but are
        annotated with #[test]. The exclude_tests filter should catch them
        via is_test_node even though the file path is not a test path.
        """
        test_meta = {"decorators": [{"name": "test", "args": [], "kwargs": {}}]}
        sym = make_symbol(
            "test_it_works", path="src/lib.rs", language="rust", meta=test_meta
        )
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="test_it_works", max_hops=3, exclude_tests=True)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 0

    def test_bfs_skips_test_annotated_neighbor(self) -> None:
        """BFS excludes neighbor with #[test] annotation even in non-test file.

        Forward slice from main() should NOT follow edge to test_add() when
        test_add has #[test] and exclude_tests=True.
        """
        main = make_symbol("main", path="src/main.rs", language="rust")
        test_meta = {"decorators": [{"name": "test", "args": [], "kwargs": {}}]}
        test_add = make_symbol(
            "test_add", path="src/lib.rs", language="rust",
            start_line=10, end_line=15, meta=test_meta,
        )
        real_fn = make_symbol(
            "add", path="src/lib.rs", language="rust",
            start_line=1, end_line=5,
        )
        nodes = [main, test_add, real_fn]
        edges = [make_edge(main, test_add), make_edge(main, real_fn)]

        query = SliceQuery(entrypoint="main", max_hops=3, exclude_tests=True)
        result = slice_graph(nodes, edges, query)

        # main and add should be in the result, but test_add excluded
        assert main.id in result.node_ids
        assert real_fn.id in result.node_ids
        assert test_add.id not in result.node_ids

    def test_entry_node_is_utility_file_excluded(self) -> None:
        """Entry node in utility file is excluded when exclude_utility=True."""
        sym = make_symbol("example_main", path="docs_src/example.py")
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="example_main", max_hops=3, exclude_utility=True)
        result = slice_graph(nodes, edges, query)

        # Entry node should be excluded, so no nodes in result
        assert len(result.node_ids) == 0

    def test_edge_to_nonexistent_node(self) -> None:
        """Edge pointing to non-existent node is skipped."""
        sym_a = make_symbol("caller")
        # Create edge to non-existent node
        edge = Edge.create(
            src=sym_a.id,
            dst="python:nonexistent.py:1-5:missing:function",
            edge_type="calls",
            line=1,
        )

        nodes = [sym_a]
        edges = [edge]

        query = SliceQuery(entrypoint="caller", max_hops=3)
        result = slice_graph(nodes, edges, query)

        # Should only have the source node
        assert len(result.node_ids) == 1
        assert sym_a.id in result.node_ids
        # Edge should not be included since dst doesn't exist
        assert len(result.edge_ids) == 0

    def test_no_matching_entry(self) -> None:
        """Slice with no matching entry returns empty result."""
        sym = make_symbol("real_function")
        nodes = [sym]
        edges: List[Edge] = []

        query = SliceQuery(entrypoint="nonexistent", max_hops=3)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 0
        assert len(result.edge_ids) == 0
        assert result.entry_nodes == []

    def test_slice_includes_file_level_imports(self) -> None:
        """Slice from function should include import edges from the containing file.

        Import edges source from file nodes (e.g., python:path:1-1:file:file),
        not function nodes. When slicing from a function, we should include
        the import edges from that function's file.
        """
        # Create a function node in main.py
        func = make_symbol("main", path="src/main.py")

        # Create the file node for main.py (this is what import edges source from)
        file_node = Symbol(
            id="python:src/main.py:1-1:file:file",
            name="file",
            kind="file",
            language="python",
            path="src/main.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Create a module node (the import target)
        module_node = Symbol(
            id="python:os:0-0:module:module",
            name="module",
            kind="module",
            language="python",
            path="os",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Create an import edge from file node to module
        import_edge = Edge.create(
            src=file_node.id,
            dst=module_node.id,
            edge_type="imports",
            line=1,
            evidence_type="ast_import",
            confidence=0.95,
        )

        nodes = [func, file_node, module_node]
        edges = [import_edge]

        # Slice from the function, not the file
        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph(nodes, edges, query)

        # The function should be included
        assert func.id in result.node_ids

        # The import edge from the file should also be included
        assert import_edge.id in result.edge_ids, (
            "Import edges from the containing file should be included in the slice"
        )

    def test_slice_includes_imports_from_multiple_files(self) -> None:
        """Slice should include import edges from all visited files."""
        # main.py: main() calls helper()
        main_func = make_symbol("main", path="src/main.py")
        main_file = Symbol(
            id="python:src/main.py:1-1:file:file",
            name="file",
            kind="file",
            language="python",
            path="src/main.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # utils.py: helper()
        helper_func = make_symbol("helper", path="src/utils.py")
        utils_file = Symbol(
            id="python:src/utils.py:1-1:file:file",
            name="file",
            kind="file",
            language="python",
            path="src/utils.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Module nodes
        os_module = Symbol(
            id="python:os:0-0:module:module",
            name="module",
            kind="module",
            language="python",
            path="os",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )
        json_module = Symbol(
            id="python:json:0-0:module:module",
            name="module",
            kind="module",
            language="python",
            path="json",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            origin="python-ast-v1",
            origin_run_id="uuid:test",
        )

        # Edges
        call_edge = make_edge(main_func, helper_func, "calls")
        import_os = Edge.create(
            src=main_file.id,
            dst=os_module.id,
            edge_type="imports",
            line=1,
            evidence_type="ast_import",
            confidence=0.95,
        )
        import_json = Edge.create(
            src=utils_file.id,
            dst=json_module.id,
            edge_type="imports",
            line=1,
            evidence_type="ast_import",
            confidence=0.95,
        )

        nodes = [main_func, main_file, helper_func, utils_file, os_module, json_module]
        edges = [call_edge, import_os, import_json]

        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph(nodes, edges, query)

        # Both function nodes should be visited
        assert main_func.id in result.node_ids
        assert helper_func.id in result.node_ids

        # Import edges from both files should be included
        assert import_os.id in result.edge_ids, "Import from main.py should be included"
        assert import_json.id in result.edge_ids, "Import from utils.py should be included"


class TestReverseSlice:
    """Tests for reverse slice - finding callers of a function."""

    def test_reverse_slice_query_has_reverse_flag(self) -> None:
        """SliceQuery should support a reverse flag."""
        query = SliceQuery(entrypoint="foo", reverse=True)
        assert query.reverse is True

    def test_reverse_slice_query_defaults_false(self) -> None:
        """SliceQuery.reverse should default to False."""
        query = SliceQuery(entrypoint="foo")
        assert query.reverse is False

    def test_reverse_slice_finds_callers(self) -> None:
        """Reverse slice should find functions that call the entry point."""
        # caller -> callee (entry)
        sym_caller = make_symbol("caller", start_line=1, end_line=5)
        sym_callee = make_symbol("callee", start_line=10, end_line=15)
        edge = make_edge(sym_caller, sym_callee, "calls")

        nodes = [sym_caller, sym_callee]
        edges = [edge]

        # Slice from callee in REVERSE - should find caller
        query = SliceQuery(entrypoint="callee", max_hops=3, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert sym_callee.id in result.node_ids
        assert sym_caller.id in result.node_ids
        assert edge.id in result.edge_ids

    def test_reverse_slice_multi_hop(self) -> None:
        """Reverse slice should traverse multiple hops backward."""
        # a -> b -> c (entry)
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)
        sym_c = make_symbol("c", start_line=5, end_line=6)

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_ab, edge_bc]

        # Slice from c in reverse - should find b, then a
        query = SliceQuery(entrypoint="c", max_hops=3, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert sym_c.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_a.id in result.node_ids
        assert edge_bc.id in result.edge_ids
        assert edge_ab.id in result.edge_ids

    def test_reverse_slice_respects_hop_limit(self) -> None:
        """Reverse slice should respect max_hops limit."""
        # a -> b -> c -> d (entry)
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)
        sym_c = make_symbol("c", start_line=5, end_line=6)
        sym_d = make_symbol("d", start_line=7, end_line=8)

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)
        edge_cd = make_edge(sym_c, sym_d)

        nodes = [sym_a, sym_b, sym_c, sym_d]
        edges = [edge_ab, edge_bc, edge_cd]

        # From d, max_hops=2: d -> c -> b (NOT a)
        query = SliceQuery(entrypoint="d", max_hops=2, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert sym_d.id in result.node_ids
        assert sym_c.id in result.node_ids
        assert sym_b.id in result.node_ids
        assert sym_a.id not in result.node_ids
        assert "hop_limit" in result.limits_hit

    def test_reverse_slice_respects_file_limit(self) -> None:
        """Reverse slice should respect max_files limit."""
        sym_a = make_symbol("a", path="file1.py")
        sym_b = make_symbol("b", path="file2.py")
        sym_c = make_symbol("c", path="file3.py")

        edge_ab = make_edge(sym_a, sym_b)
        edge_bc = make_edge(sym_b, sym_c)

        nodes = [sym_a, sym_b, sym_c]
        edges = [edge_ab, edge_bc]

        query = SliceQuery(entrypoint="c", max_hops=10, max_files=2, reverse=True)
        result = slice_graph(nodes, edges, query)

        files_in_result = {n.split(":")[1] for n in result.node_ids}
        assert len(files_in_result) <= 2
        assert "file_limit" in result.limits_hit

    def test_reverse_slice_excludes_tests(self) -> None:
        """Reverse slice should exclude test files when requested."""
        sym_main = make_symbol("main", path="src/main.py")
        sym_test = make_symbol("test_main", path="tests/test_main.py")

        # test_main calls main - in reverse from main, should NOT find test
        edge = make_edge(sym_test, sym_main, "calls")

        nodes = [sym_main, sym_test]
        edges = [edge]

        query = SliceQuery(entrypoint="main", max_hops=3, reverse=True, exclude_tests=True)
        result = slice_graph(nodes, edges, query)

        assert sym_main.id in result.node_ids
        assert sym_test.id not in result.node_ids

    def test_reverse_slice_excludes_utility(self) -> None:
        """Reverse slice should exclude utility files (docs, examples) when requested."""
        sym_main = make_symbol("create_item", path="src/app.py")
        sym_docs = make_symbol("example_call", path="docs_src/example.py")
        sym_scripts = make_symbol("deploy_call", path="scripts/deploy.py")

        # Both doc and script call main - in reverse, should NOT find them
        edge_docs = make_edge(sym_docs, sym_main, "calls")
        edge_scripts = make_edge(sym_scripts, sym_main, "calls")

        nodes = [sym_main, sym_docs, sym_scripts]
        edges = [edge_docs, edge_scripts]

        query = SliceQuery(entrypoint="create_item", max_hops=3, reverse=True, exclude_utility=True)
        result = slice_graph(nodes, edges, query)

        assert sym_main.id in result.node_ids
        assert sym_docs.id not in result.node_ids
        assert sym_scripts.id not in result.node_ids

    def test_reverse_slice_filters_low_confidence(self) -> None:
        """Reverse slice should filter edges below confidence threshold."""
        sym_entry = make_symbol("entry", start_line=1, end_line=2)
        sym_high = make_symbol("caller_high", start_line=3, end_line=4)
        sym_low = make_symbol("caller_low", start_line=5, end_line=6)

        edge_high = make_edge(sym_high, sym_entry, confidence=0.90)
        edge_low = make_edge(sym_low, sym_entry, confidence=0.40)

        nodes = [sym_entry, sym_high, sym_low]
        edges = [edge_high, edge_low]

        query = SliceQuery(
            entrypoint="entry",
            max_hops=3,
            min_confidence=0.50,
            reverse=True,
        )
        result = slice_graph(nodes, edges, query)

        assert sym_entry.id in result.node_ids
        assert sym_high.id in result.node_ids
        assert sym_low.id not in result.node_ids

    def test_reverse_slice_handles_cycles(self) -> None:
        """Reverse slice should handle cycles without infinite loop."""
        sym_a = make_symbol("a", start_line=1, end_line=2)
        sym_b = make_symbol("b", start_line=3, end_line=4)

        edge_ab = make_edge(sym_a, sym_b)
        edge_ba = make_edge(sym_b, sym_a)

        nodes = [sym_a, sym_b]
        edges = [edge_ab, edge_ba]

        query = SliceQuery(entrypoint="a", max_hops=10, reverse=True)
        result = slice_graph(nodes, edges, query)

        assert len(result.node_ids) == 2
        assert sym_a.id in result.node_ids
        assert sym_b.id in result.node_ids

    def test_reverse_slice_to_dict_includes_reverse(self) -> None:
        """SliceQuery.to_dict should include reverse flag."""
        query = SliceQuery(entrypoint="foo", reverse=True)
        d = query.to_dict()
        assert d["reverse"] is True

    def test_reverse_slice_different_feature_id(self) -> None:
        """Reverse and forward slices should have different feature IDs."""
        sym_a = make_symbol("entry")
        nodes = [sym_a]
        edges: List[Edge] = []

        query_forward = SliceQuery(entrypoint="entry", reverse=False)
        query_reverse = SliceQuery(entrypoint="entry", reverse=True)

        result_forward = slice_graph(nodes, edges, query_forward)
        result_reverse = slice_graph(nodes, edges, query_reverse)

        assert result_forward.feature_id != result_reverse.feature_id


class TestRankSliceNodes:
    """Tests for rank_slice_nodes function."""

    def test_empty_slice_returns_sorted_ids(self) -> None:
        """Empty slice returns sorted node IDs."""
        # Create a slice result with node IDs but no matching Symbol objects
        result = SliceResult(
            entry_nodes=[],
            node_ids={"node_c", "node_a", "node_b"},
            edge_ids=set(),
            query=SliceQuery(entrypoint="foo"),
        )

        # Pass empty nodes list so slice_nodes will be empty
        ranked = rank_slice_nodes(result, [], [])

        # Should return sorted node IDs as fallback
        assert ranked == ["node_a", "node_b", "node_c"]

    def test_first_party_priority_false(self) -> None:
        """Raw centrality used when first_party_priority=False."""
        # Create symbols with different tiers
        first_party = make_symbol("my_func", path="src/main.py")
        first_party.supply_chain_tier = 1
        external = make_symbol("lodash", path="node_modules/lodash.js")
        external.supply_chain_tier = 3
        caller = make_symbol("caller", path="src/other.py")
        caller.supply_chain_tier = 1

        # Edge from caller to external
        edge = Edge.create(caller.id, external.id, "calls", 10, confidence=0.9)

        # Create a slice result with these nodes
        result = SliceResult(
            entry_nodes=[caller.id],
            node_ids={caller.id, external.id, first_party.id},
            edge_ids={edge.id},
            query=SliceQuery(entrypoint="caller"),
        )

        ranked = rank_slice_nodes(
            result,
            [first_party, external, caller],
            [edge],
            first_party_priority=False
        )

        # Without tier weighting, external should rank high (has incoming edge)
        # and first_party should rank lower (no incoming edges)
        external_rank = ranked.index(external.id)
        first_party_rank = ranked.index(first_party.id)
        assert external_rank < first_party_rank  # external ranks higher

    def test_test_weight_none_no_change(self) -> None:
        """When test_weight is None, test files not downweighted."""
        test_sym = make_symbol("test_func", path="tests/test_main.py")
        prod_sym = make_symbol("prod_func", path="src/main.py")
        caller = make_symbol("caller", path="src/other.py")

        # Both test and prod are called by caller equally
        edge1 = Edge.create(caller.id, test_sym.id, "calls", 5, confidence=0.9)
        edge2 = Edge.create(caller.id, prod_sym.id, "calls", 6, confidence=0.9)

        result = SliceResult(
            entry_nodes=[caller.id],
            node_ids={caller.id, test_sym.id, prod_sym.id},
            edge_ids={edge1.id, edge2.id},
            query=SliceQuery(entrypoint="caller"),
        )

        # With test_weight=None, test files not downweighted
        ranked = rank_slice_nodes(
            result,
            [test_sym, prod_sym, caller],
            [edge1, edge2],
            first_party_priority=False,
            test_weight=None,
        )

        # Both have same centrality (1 incoming each), order by name
        assert test_sym.id in ranked
        assert prod_sym.id in ranked

    def test_test_weight_downweights_test_files(self) -> None:
        """When test_weight is set, test files are downweighted in ranking."""
        test_sym = make_symbol("test_func", path="tests/test_main.py")
        prod_sym = make_symbol("prod_func", path="src/main.py")
        caller1 = make_symbol("caller1", path="src/a.py")
        caller2 = make_symbol("caller2", path="src/b.py")

        # Test file has more incoming edges (2 vs 1)
        edges = [
            Edge.create(caller1.id, test_sym.id, "calls", 5, confidence=0.9),
            Edge.create(caller2.id, test_sym.id, "calls", 6, confidence=0.9),
            Edge.create(caller1.id, prod_sym.id, "calls", 7, confidence=0.9),
        ]

        result = SliceResult(
            entry_nodes=[caller1.id],
            node_ids={caller1.id, caller2.id, test_sym.id, prod_sym.id},
            edge_ids={e.id for e in edges},
            query=SliceQuery(entrypoint="caller"),
        )

        # With test_weight=0.3, test file centrality reduced significantly
        ranked = rank_slice_nodes(
            result,
            [test_sym, prod_sym, caller1, caller2],
            edges,
            first_party_priority=False,
            test_weight=0.3,
        )

        # Prod should rank higher than test despite fewer incoming edges
        # test raw centrality: 1.0 (2 edges, max), weighted: 0.3
        # prod raw centrality: 0.5 (1 edge), weighted: 0.5
        prod_rank = ranked.index(prod_sym.id)
        test_rank = ranked.index(test_sym.id)
        assert prod_rank < test_rank  # prod ranks higher

class TestReverseSliceClassExpansion:
    """Tests for reverse slice auto-expanding class entries to member methods.

    When a reverse slice entry point resolves to a class/interface node,
    the slicer should automatically expand the starting set to include
    all member methods (via 'contains' edges). This way, querying
    "what calls OwnerRepository?" returns callers of findById, etc.
    """

    def test_class_entry_expands_to_member_methods(self) -> None:
        """Reverse slice from class should find callers of its methods."""
        # Class with two methods
        repo_class = make_symbol(
            "OwnerRepository", kind="interface",
            path="src/repo.java", start_line=1, end_line=30, language="java",
        )
        find_method = make_symbol(
            "OwnerRepository.findById", kind="method",
            path="src/repo.java", start_line=10, end_line=15, language="java",
        )
        search_method = make_symbol(
            "OwnerRepository.search", kind="method",
            path="src/repo.java", start_line=20, end_line=25, language="java",
        )

        # Caller of findById
        controller = make_symbol(
            "Controller.show", kind="method",
            path="src/ctrl.java", start_line=1, end_line=10, language="java",
        )

        # Contains edges: class -> methods
        contains1 = make_edge(repo_class, find_method, "contains")
        contains2 = make_edge(repo_class, search_method, "contains")
        # Calls edge: controller -> findById
        calls1 = make_edge(controller, find_method, "calls")

        nodes = [repo_class, find_method, search_method, controller]
        edges = [contains1, contains2, calls1]

        # Reverse slice from class name should find the controller
        query = SliceQuery(
            entrypoint="OwnerRepository", max_hops=3, reverse=True,
        )
        result = slice_graph(nodes, edges, query)

        # Should include: class, both methods, and the controller caller
        assert repo_class.id in result.node_ids
        assert find_method.id in result.node_ids
        assert search_method.id in result.node_ids
        assert controller.id in result.node_ids
        assert calls1.id in result.edge_ids

    def test_class_entry_expansion_multi_hop(self) -> None:
        """Reverse slice from class should follow multiple hops from methods."""
        # Class with one method
        svc_class = make_symbol(
            "UserService", kind="class",
            path="src/svc.py", start_line=1, end_line=30, language="python",
        )
        get_user = make_symbol(
            "UserService.get_user", kind="method",
            path="src/svc.py", start_line=10, end_line=15, language="python",
        )
        # Caller chain: handler -> controller -> get_user
        controller = make_symbol(
            "Controller.show", kind="method",
            path="src/ctrl.py", start_line=1, end_line=10, language="python",
        )
        handler = make_symbol(
            "Handler.process", kind="method",
            path="src/handler.py", start_line=1, end_line=10, language="python",
        )

        contains = make_edge(svc_class, get_user, "contains")
        call1 = make_edge(controller, get_user, "calls")
        call2 = make_edge(handler, controller, "calls")

        nodes = [svc_class, get_user, controller, handler]
        edges = [contains, call1, call2]

        query = SliceQuery(
            entrypoint="UserService", max_hops=3, reverse=True,
        )
        result = slice_graph(nodes, edges, query)

        # Should reach handler via: class -> get_user -> controller -> handler
        assert handler.id in result.node_ids
        assert call2.id in result.edge_ids

    def test_method_entry_not_affected(self) -> None:
        """Method-level entries should NOT trigger class expansion."""
        repo_class = make_symbol(
            "OwnerRepository", kind="interface",
            path="src/repo.java", start_line=1, end_line=30, language="java",
        )
        find_method = make_symbol(
            "OwnerRepository.findById", kind="method",
            path="src/repo.java", start_line=10, end_line=15, language="java",
        )
        search_method = make_symbol(
            "OwnerRepository.search", kind="method",
            path="src/repo.java", start_line=20, end_line=25, language="java",
        )
        # Caller of search only
        caller = make_symbol(
            "Service.doSearch", kind="method",
            path="src/svc.java", start_line=1, end_line=5, language="java",
        )

        contains1 = make_edge(repo_class, find_method, "contains")
        contains2 = make_edge(repo_class, search_method, "contains")
        call = make_edge(caller, search_method, "calls")

        nodes = [repo_class, find_method, search_method, caller]
        edges = [contains1, contains2, call]

        # Reverse slice from findById method directly - should NOT find caller
        # of search (expansion only for class-level entries)
        query = SliceQuery(
            entrypoint="OwnerRepository.findById", max_hops=3, reverse=True,
        )
        result = slice_graph(nodes, edges, query)

        assert find_method.id in result.node_ids
        assert caller.id not in result.node_ids

    def test_forward_slice_from_class_expands_to_methods(self) -> None:
        """Forward slice from class expands to member methods via class expansion."""
        svc_class = make_symbol(
            "UserService", kind="class",
            path="src/svc.py", start_line=1, end_line=30, language="python",
        )
        get_user = make_symbol(
            "UserService.get_user", kind="method",
            path="src/svc.py", start_line=10, end_line=15, language="python",
        )
        # get_user calls helper
        helper = make_symbol(
            "helper.validate", kind="function",
            path="src/helper.py", start_line=1, end_line=5, language="python",
        )

        contains = make_edge(svc_class, get_user, "contains")
        call = make_edge(get_user, helper, "calls")

        nodes = [svc_class, get_user, helper]
        edges = [contains, call]

        # Forward slice from class — class expansion seeds get_user,
        # then BFS follows calls to helper
        query = SliceQuery(
            entrypoint="UserService", max_hops=3, reverse=False,
        )
        result = slice_graph(nodes, edges, query)

        # Class entry and its expanded method both in slice
        assert svc_class.id in result.node_ids
        assert get_user.id in result.node_ids
        # Method's dependency reachable via calls edge
        assert helper.id in result.node_ids

    def test_expansion_respects_exclude_tests(self) -> None:
        """Class expansion should exclude test methods when requested."""
        svc_class = make_symbol(
            "Service", kind="class",
            path="src/svc.py", start_line=1, end_line=30, language="python",
        )
        method = make_symbol(
            "Service.do_work", kind="method",
            path="src/svc.py", start_line=10, end_line=15, language="python",
        )
        # Caller from test file
        test_caller = make_symbol(
            "test_service", kind="function",
            path="tests/test_svc.py", start_line=1, end_line=5, language="python",
        )

        contains = make_edge(svc_class, method, "contains")
        call = make_edge(test_caller, method, "calls")

        nodes = [svc_class, method, test_caller]
        edges = [contains, call]

        query = SliceQuery(
            entrypoint="Service", max_hops=3, reverse=True,
            exclude_tests=True,
        )
        result = slice_graph(nodes, edges, query)

        # Method should be expanded from class
        assert method.id in result.node_ids
        # Test caller should be excluded
        assert test_caller.id not in result.node_ids

    def test_expansion_skips_non_contains_edges(self) -> None:
        """Class expansion should only follow 'contains' edges, not others."""
        svc_class = make_symbol(
            "ParentService", kind="class",
            path="src/parent.py", start_line=1, end_line=30, language="python",
        )
        child_class = make_symbol(
            "ChildService", kind="class",
            path="src/child.py", start_line=1, end_line=30, language="python",
        )
        method = make_symbol(
            "ParentService.do_work", kind="method",
            path="src/parent.py", start_line=10, end_line=15, language="python",
        )
        caller = make_symbol(
            "handler", kind="function",
            path="src/handler.py", start_line=1, end_line=5, language="python",
        )

        # Contains edge: class -> method
        contains = make_edge(svc_class, method, "contains")
        # Extends edge: class -> child (should not be followed for expansion)
        extends = make_edge(svc_class, child_class, "extends")
        # Caller of method
        call = make_edge(caller, method, "calls")

        nodes = [svc_class, child_class, method, caller]
        edges = [contains, extends, call]

        query = SliceQuery(
            entrypoint="ParentService", max_hops=3, reverse=True,
        )
        result = slice_graph(nodes, edges, query)

        # Method and caller should be found (via contains expansion)
        assert method.id in result.node_ids
        assert caller.id in result.node_ids
        # Child class should NOT be expanded (extends edge, not contains)
        # It may or may not be in the result depending on BFS traversal,
        # but the key check is that expansion only follows contains edges.

    def test_expansion_excludes_utility_members(self) -> None:
        """Class expansion should exclude utility file members when requested."""
        svc_class = make_symbol(
            "Service", kind="class",
            path="src/svc.py", start_line=1, end_line=30, language="python",
        )
        # Method in utility/examples directory
        example_method = make_symbol(
            "Service.example", kind="method",
            path="examples/demo.py", start_line=1, end_line=5, language="python",
        )
        # Normal method
        normal_method = make_symbol(
            "Service.do_work", kind="method",
            path="src/svc.py", start_line=10, end_line=15, language="python",
        )
        caller = make_symbol(
            "handler", kind="function",
            path="src/handler.py", start_line=1, end_line=5, language="python",
        )

        contains1 = make_edge(svc_class, example_method, "contains")
        contains2 = make_edge(svc_class, normal_method, "contains")
        call = make_edge(caller, normal_method, "calls")

        nodes = [svc_class, example_method, normal_method, caller]
        edges = [contains1, contains2, call]

        query = SliceQuery(
            entrypoint="Service", max_hops=3, reverse=True,
            exclude_utility=True,
        )
        result = slice_graph(nodes, edges, query)

        # Normal method and caller should be found
        assert normal_method.id in result.node_ids
        assert caller.id in result.node_ids
        # Utility member should be excluded
        assert example_method.id not in result.node_ids

    def test_expansion_skips_test_member(self) -> None:
        """Class expansion should skip test file members when exclude_tests."""
        svc_class = make_symbol(
            "Service", kind="class",
            path="src/svc.py", start_line=1, end_line=30, language="python",
        )
        # A method defined in a test file (unusual but possible)
        test_member = make_symbol(
            "Service.test_helper", kind="method",
            path="tests/test_svc.py", start_line=1, end_line=5, language="python",
        )

        contains = make_edge(svc_class, test_member, "contains")

        nodes = [svc_class, test_member]
        edges = [contains]

        query = SliceQuery(
            entrypoint="Service", max_hops=3, reverse=True,
            exclude_tests=True,
        )
        result = slice_graph(nodes, edges, query)

        # Class is the entry so it's always included
        assert svc_class.id in result.node_ids
        # Test member should be excluded from expansion
        assert test_member.id not in result.node_ids


    def test_test_weight_useful_for_reverse_slice(self) -> None:
        """Test weight is useful for reverse slicing to prioritize prod callers."""
        # Target function that is called by both test and prod
        target = make_symbol("core_func", path="src/core.py")
        test_caller = make_symbol("test_core", path="tests/test_core.py")
        prod_caller = make_symbol("api_handler", path="src/api.py")

        edges = [
            Edge.create(test_caller.id, target.id, "calls", 10, confidence=0.9),
            Edge.create(prod_caller.id, target.id, "calls", 20, confidence=0.9),
        ]

        # In a reverse slice from target, both callers would be in the result
        result = SliceResult(
            entry_nodes=[target.id],
            node_ids={target.id, test_caller.id, prod_caller.id},
            edge_ids={e.id for e in edges},
            query=SliceQuery(entrypoint="core_func", reverse=True),
        )

        ranked = rank_slice_nodes(
            result,
            [target, test_caller, prod_caller],
            edges,
            first_party_priority=True,
            test_weight=0.5,
        )

        # Production caller should rank higher than test caller
        prod_rank = ranked.index(prod_caller.id)
        test_rank = ranked.index(test_caller.id)
        assert prod_rank < test_rank


class TestForwardSliceInheritanceEdges:
    """Forward slices should NOT traverse structural edges, but DO follow dispatches_to.

    Structural edges (extends, implements, contains) cause BFS explosion:
    - extends/implements: VoiceController → ApplicationController fans out
      to ALL sibling controllers
    - contains: reaching a class fans out to ALL member methods

    dispatches_to edges ARE followed in forward slices — they represent
    intentional registry dispatch (run_all_analyzers → each analyzer).
    Hub pruning exempts dispatches_to from its edge count so dispatch
    sites with many handlers aren't pruned.
    """

    def test_forward_slice_skips_extends_edge(self) -> None:
        """Forward slice should not follow extends edges to parent class."""
        child = make_symbol(
            "VoiceController", kind="class",
            path="src/voice.py", start_line=1, end_line=50, language="python",
        )
        parent = make_symbol(
            "ApplicationController", kind="class",
            path="src/base.py", start_line=1, end_line=30, language="python",
        )
        # A sibling class that also extends the parent
        sibling = make_symbol(
            "UserController", kind="class",
            path="src/user.py", start_line=1, end_line=40, language="python",
        )
        # Direct dependency of child (via calls)
        service = make_symbol(
            "VoiceService", kind="class",
            path="src/voice_svc.py", start_line=1, end_line=30, language="python",
        )

        edges = [
            make_edge(child, parent, "extends"),          # child -> parent
            make_edge(sibling, parent, "extends"),        # sibling -> parent
            make_edge(child, service, "calls"),           # child -> service
        ]

        query = SliceQuery(entrypoint="VoiceController", max_hops=3)
        result = slice_graph(
            [child, parent, sibling, service], edges, query,
        )

        # Service should be reachable (via calls edge)
        assert service.id in result.node_ids
        # Parent should NOT be reachable (extends edge skipped in forward)
        assert parent.id not in result.node_ids
        # Sibling should NOT be reachable
        assert sibling.id not in result.node_ids

    def test_forward_slice_skips_implements_edge(self) -> None:
        """Forward slice should not follow implements edges to interface."""
        struct = make_symbol(
            "MyServer", kind="struct",
            path="src/server.go", start_line=1, end_line=50, language="go",
        )
        iface = make_symbol(
            "Handler", kind="interface",
            path="src/handler.go", start_line=1, end_line=10, language="go",
        )
        # Another struct implementing same interface
        other_impl = make_symbol(
            "OtherServer", kind="struct",
            path="src/other.go", start_line=1, end_line=30, language="go",
        )
        # Direct dependency
        db = make_symbol(
            "Database", kind="struct",
            path="src/db.go", start_line=1, end_line=20, language="go",
        )

        edges = [
            make_edge(struct, iface, "implements"),       # struct -> interface
            make_edge(other_impl, iface, "implements"),   # other -> interface
            make_edge(struct, db, "calls"),               # struct -> db
        ]

        query = SliceQuery(entrypoint="MyServer", max_hops=3)
        result = slice_graph(
            [struct, iface, other_impl, db], edges, query,
        )

        # DB should be reachable (via calls)
        assert db.id in result.node_ids
        # Interface should NOT be reachable (implements edge skipped)
        assert iface.id not in result.node_ids
        # Other impl should NOT be reachable
        assert other_impl.id not in result.node_ids

    def test_reverse_slice_still_follows_extends(self) -> None:
        """Reverse slice should still follow extends edges."""
        parent = make_symbol(
            "BaseModel", kind="class",
            path="src/base.py", start_line=1, end_line=30, language="python",
        )
        child = make_symbol(
            "UserModel", kind="class",
            path="src/user.py", start_line=1, end_line=40, language="python",
        )

        edges = [
            make_edge(child, parent, "extends"),
        ]

        query = SliceQuery(entrypoint="BaseModel", max_hops=3, reverse=True)
        result = slice_graph([parent, child], edges, query)

        # In reverse slice, extends edge (child -> parent) should be followed
        # backwards: from parent, find child as a source
        assert child.id in result.node_ids

    def test_reverse_slice_still_follows_implements(self) -> None:
        """Reverse slice should still follow implements edges."""
        iface = make_symbol(
            "Repository", kind="interface",
            path="src/repo.go", start_line=1, end_line=10, language="go",
        )
        impl = make_symbol(
            "SqlRepo", kind="struct",
            path="src/sql_repo.go", start_line=1, end_line=50, language="go",
        )

        edges = [
            make_edge(impl, iface, "implements"),
        ]

        query = SliceQuery(entrypoint="Repository", max_hops=3, reverse=True)
        result = slice_graph([iface, impl], edges, query)

        # In reverse slice, implements edge should be followed backwards
        assert impl.id in result.node_ids

    def test_forward_slice_ancestor_explosion_prevented(self) -> None:
        """Forward slice should not explode through shared ancestor.

        This tests the specific scenario reported in bakeoff assessments:
        VoiceController extends ApplicationController, which is also extended
        by 3 other controllers. Without the fix, forward slicing from
        VoiceController would traverse to ApplicationController and then
        discover all sibling controllers through the reverse direction of
        their extends edges — producing a massively inflated slice.
        """
        app_ctrl = make_symbol(
            "ApplicationController", kind="class",
            path="src/base_ctrl.rb", start_line=1, end_line=50, language="ruby",
        )
        voice_ctrl = make_symbol(
            "VoiceController", kind="class",
            path="src/voice_ctrl.rb", start_line=1, end_line=30, language="ruby",
        )
        user_ctrl = make_symbol(
            "UserController", kind="class",
            path="src/user_ctrl.rb", start_line=1, end_line=30, language="ruby",
        )
        admin_ctrl = make_symbol(
            "AdminController", kind="class",
            path="src/admin_ctrl.rb", start_line=1, end_line=30, language="ruby",
        )
        # VoiceController's actual dependency
        voice_svc = make_symbol(
            "VoiceService", kind="class",
            path="src/voice_svc.rb", start_line=1, end_line=40, language="ruby",
        )
        # ApplicationController's dependency (should NOT be in slice)
        auth_svc = make_symbol(
            "AuthService", kind="class",
            path="src/auth_svc.rb", start_line=1, end_line=30, language="ruby",
        )

        edges = [
            make_edge(voice_ctrl, app_ctrl, "extends"),
            make_edge(user_ctrl, app_ctrl, "extends"),
            make_edge(admin_ctrl, app_ctrl, "extends"),
            make_edge(voice_ctrl, voice_svc, "calls"),
            make_edge(app_ctrl, auth_svc, "calls"),  # parent's dependency
        ]

        query = SliceQuery(entrypoint="VoiceController", max_hops=3)
        result = slice_graph(
            [app_ctrl, voice_ctrl, user_ctrl, admin_ctrl, voice_svc, auth_svc],
            edges, query,
        )

        # VoiceController's direct dependency should be found
        assert voice_svc.id in result.node_ids
        # Shared ancestor should NOT be reached
        assert app_ctrl.id not in result.node_ids
        # Sibling controllers should NOT be reached
        assert user_ctrl.id not in result.node_ids
        assert admin_ctrl.id not in result.node_ids
        # Parent's dependency should NOT be reached
        assert auth_svc.id not in result.node_ids

    def test_forward_slice_follows_dispatches_to_edge(self) -> None:
        """Forward slice follows dispatches_to edges to implementations.

        dispatches_to edges represent polymorphic dispatch: InterfaceMethod
        dispatches to concrete implementations at runtime. Forward slices
        should traverse these so the slice doesn't dead-end at interface
        call sites. Hub_threshold handles fan-out for interfaces with
        many implementations.
        """
        # Caller that invokes the interface method
        caller = make_symbol(
            "main", kind="function",
            path="src/main.java", start_line=1, end_line=20, language="java",
        )
        # Interface method
        iface_create = make_symbol(
            "OutputFile.create", kind="method",
            path="src/output.java", start_line=1, end_line=5, language="java",
        )
        # Two implementations
        s3_create = make_symbol(
            "S3FileIO.create", kind="method",
            path="src/s3.java", start_line=1, end_line=20, language="java",
        )
        gcs_create = make_symbol(
            "GCSFileIO.create", kind="method",
            path="src/gcs.java", start_line=1, end_line=20, language="java",
        )

        edges = [
            # caller calls the interface method
            make_edge(caller, iface_create, "calls"),
            # interface dispatches_to both implementations
            make_edge(iface_create, s3_create, "dispatches_to"),
            make_edge(iface_create, gcs_create, "dispatches_to"),
        ]

        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph(
            [caller, iface_create, s3_create, gcs_create], edges, query,
        )

        # Interface method reachable via calls edge
        assert iface_create.id in result.node_ids
        # Both implementations reachable via dispatches_to
        assert s3_create.id in result.node_ids, (
            "Forward slice should follow dispatches_to to reach implementations"
        )
        assert gcs_create.id in result.node_ids

    def test_reverse_slice_still_follows_dispatches_to(self) -> None:
        """Reverse slice should follow dispatches_to edges.

        dispatches_to goes FROM interface TO implementation.  A reverse slice
        from an implementation follows the edge backwards and discovers the
        interface method (useful for understanding which abstraction this
        implementation serves).
        """
        iface_method = make_symbol(
            "OutputFile.create", kind="method",
            path="src/output.java", start_line=1, end_line=5, language="java",
        )
        impl = make_symbol(
            "S3FileIO.create", kind="method",
            path="src/s3.java", start_line=1, end_line=20, language="java",
        )

        edges = [
            make_edge(iface_method, impl, "dispatches_to"),
        ]

        # Reverse slice from implementation finds interface via dispatches_to
        query = SliceQuery(
            entrypoint="S3FileIO.create", max_hops=3, reverse=True,
        )
        result = slice_graph(
            [iface_method, impl], edges, query,
        )

        # Interface method should be found (dispatches_to reversed)
        assert iface_method.id in result.node_ids

    def test_dispatches_to_exempt_from_hub_pruning(self) -> None:
        """Hub pruning should not count dispatches_to edges toward threshold.

        Registry dispatch sites like run_all_analyzers fan out to many
        handlers via dispatches_to edges. This is intentional architectural
        fan-out, not noisy utility calls. If these edges count toward
        hub_threshold, the dispatch site gets pruned at depth >= 2 and
        the slice misses all registered handlers.
        """
        # entry -> bridge -> dispatch_site -> {handler_0..59} via dispatches_to
        entry = make_symbol("main", path="src/main.py")
        bridge = make_symbol("run", path="src/run.py", start_line=1, end_line=5)
        dispatch = make_symbol(
            "run_all_analyzers", path="src/registry.py",
            start_line=10, end_line=20,
        )
        handlers = [
            make_symbol(
                f"analyze_{i}", path=f"src/lang_{i}.py",
                start_line=1, end_line=5,
            )
            for i in range(60)
        ]

        edges_list: List[Edge] = [
            make_edge(entry, bridge),
            make_edge(bridge, dispatch),
        ]
        for handler in handlers:
            edges_list.append(
                make_edge(dispatch, handler, edge_type="dispatches_to")
            )

        all_nodes = [entry, bridge, dispatch] + handlers

        # dispatch_site is at depth 2 with 60 outgoing dispatches_to edges.
        # With hub_threshold=50, naive counting would prune it.
        # But dispatches_to edges should be exempt from hub counting.
        query = SliceQuery(
            entrypoint="main", max_hops=5, max_files=200,
            hub_threshold=50,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # All handlers should be reachable despite exceeding hub_threshold
        assert dispatch.id in result.node_ids
        reachable_handlers = [h for h in handlers if h.id in result.node_ids]
        assert len(reachable_handlers) == 60, (
            f"Expected 60 handlers reachable via dispatches_to, got {len(reachable_handlers)}. "
            f"Hub pruning should not count dispatches_to edges."
        )

    def test_mixed_calls_and_dispatches_to_below_threshold(self) -> None:
        """Hub pruning counts only non-dispatch edges toward threshold.

        A node with 10 calls edges + 60 dispatches_to edges should NOT be
        pruned when hub_threshold=50 (only 10 non-dispatch edges < 50).
        """
        entry = make_symbol("main", path="src/main.py")
        bridge = make_symbol("run", path="src/run.py", start_line=1, end_line=5)
        mixed_node = make_symbol(
            "orchestrator", path="src/orchestrator.py",
            start_line=10, end_line=20,
        )
        call_targets = [
            make_symbol(
                f"util_{i}", path=f"src/util_{i}.py",
                start_line=1, end_line=5,
            )
            for i in range(10)
        ]
        dispatch_targets = [
            make_symbol(
                f"handler_{i}", path=f"src/handler_{i}.py",
                start_line=1, end_line=5,
            )
            for i in range(60)
        ]

        edges_list: List[Edge] = [
            make_edge(entry, bridge),
            make_edge(bridge, mixed_node),
        ]
        for t in call_targets:
            edges_list.append(make_edge(mixed_node, t, edge_type="calls"))
        for t in dispatch_targets:
            edges_list.append(
                make_edge(mixed_node, t, edge_type="dispatches_to")
            )

        all_nodes = [entry, bridge, mixed_node] + call_targets + dispatch_targets

        query = SliceQuery(
            entrypoint="main", max_hops=5, max_files=200,
            hub_threshold=50,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # All targets reachable — only 10 non-dispatch edges < 50
        reachable_calls = [t for t in call_targets if t.id in result.node_ids]
        reachable_dispatch = [t for t in dispatch_targets if t.id in result.node_ids]
        assert len(reachable_calls) == 10
        assert len(reachable_dispatch) == 60

    def test_hub_pruned_node_still_follows_dispatches_to(self) -> None:
        """When a node IS hub-pruned (calls > threshold), dispatches_to still followed.

        A node with 55 calls edges + 5 dispatches_to edges should be
        hub-pruned for calls (55 > 50), but its dispatches_to targets
        should still be reachable.
        """
        entry = make_symbol("main", path="src/main.py")
        bridge = make_symbol("run", path="src/run.py", start_line=1, end_line=5)
        hub = make_symbol(
            "big_orchestrator", path="src/big.py",
            start_line=10, end_line=20,
        )
        call_targets = [
            make_symbol(
                f"callee_{i}", path=f"src/callee_{i}.py",
                start_line=1, end_line=5,
            )
            for i in range(55)
        ]
        dispatch_targets = [
            make_symbol(
                f"dispatch_{i}", path=f"src/dispatch_{i}.py",
                start_line=1, end_line=5,
            )
            for i in range(5)
        ]

        edges_list: List[Edge] = [
            make_edge(entry, bridge),
            make_edge(bridge, hub),
        ]
        for t in call_targets:
            edges_list.append(make_edge(hub, t, edge_type="calls"))
        for t in dispatch_targets:
            edges_list.append(
                make_edge(hub, t, edge_type="dispatches_to")
            )

        all_nodes = [entry, bridge, hub] + call_targets + dispatch_targets

        query = SliceQuery(
            entrypoint="main", max_hops=5, max_files=200,
            hub_threshold=50,
        )
        result = slice_graph(all_nodes, edges_list, query)

        # Hub should be hub-pruned (55 calls > 50)
        assert "hub_pruned" in result.limits_hit
        # Calls targets should NOT be reachable (hub-pruned)
        reachable_calls = [t for t in call_targets if t.id in result.node_ids]
        assert len(reachable_calls) == 0
        # But dispatches_to targets SHOULD still be reachable
        reachable_dispatch = [t for t in dispatch_targets if t.id in result.node_ids]
        assert len(reachable_dispatch) == 5


class TestForwardSliceContainsEdges:
    """Forward slices should NOT traverse 'contains' edges.

    When forward BFS reaches a class node (e.g., through a route edge or
    entrypoint expansion), following 'contains' edges would fan out to ALL
    methods of that class — even sibling methods unrelated to the slice.

    For example, forward-slicing from Owner.getPet should NOT pull in
    Owner.getAddress, Owner.setCity, etc. just because they are all
    contained in the Owner class.

    When the *entry point* is a container type (class/interface/etc.),
    forward slice class expansion automatically seeds the BFS with member
    methods, so they are still reachable as starting points.
    """

    def test_forward_slice_skips_contains_edge(self) -> None:
        """Forward slice should not follow 'contains' edges to sibling methods."""
        owner_class = make_symbol(
            "Owner", kind="class",
            path="src/owner.py", start_line=1, end_line=100, language="python",
        )
        get_pet = make_symbol(
            "Owner.getPet", kind="method",
            path="src/owner.py", start_line=10, end_line=20, language="python",
        )
        get_address = make_symbol(
            "Owner.getAddress", kind="method",
            path="src/owner.py", start_line=30, end_line=40, language="python",
        )
        set_city = make_symbol(
            "Owner.setCity", kind="method",
            path="src/owner.py", start_line=50, end_line=60, language="python",
        )
        # A real dependency of getPet (via calls)
        pet_repo = make_symbol(
            "PetRepository.findById", kind="method",
            path="src/pet_repo.py", start_line=1, end_line=10, language="python",
        )

        edges = [
            make_edge(owner_class, get_pet, "contains"),
            make_edge(owner_class, get_address, "contains"),
            make_edge(owner_class, set_city, "contains"),
            make_edge(get_pet, pet_repo, "calls"),
        ]

        query = SliceQuery(entrypoint="Owner.getPet", max_hops=3)
        result = slice_graph(
            [owner_class, get_pet, get_address, set_city, pet_repo], edges, query,
        )

        # getPet's direct dependency should be reachable
        assert pet_repo.id in result.node_ids
        # Owner class should NOT be pulled in (no edge from getPet -> Owner)
        assert owner_class.id not in result.node_ids
        # Sibling methods should NOT be pulled in
        assert get_address.id not in result.node_ids
        assert set_city.id not in result.node_ids

    def test_forward_slice_class_reaches_method_then_no_sibling_explosion(self) -> None:
        """Even if BFS reaches a class via calls, it should not fan out via contains."""
        # A controller that calls a method on a service class
        controller = make_symbol(
            "PetController", kind="class",
            path="src/ctrl.py", start_line=1, end_line=50, language="python",
        )
        # The service class (reached through some edge)
        service_class = make_symbol(
            "PetService", kind="class",
            path="src/svc.py", start_line=1, end_line=80, language="python",
        )
        svc_create = make_symbol(
            "PetService.create", kind="method",
            path="src/svc.py", start_line=10, end_line=30, language="python",
        )
        svc_delete = make_symbol(
            "PetService.delete", kind="method",
            path="src/svc.py", start_line=40, end_line=60, language="python",
        )
        svc_list = make_symbol(
            "PetService.list", kind="method",
            path="src/svc.py", start_line=65, end_line=75, language="python",
        )

        edges = [
            # Controller calls create on the service
            make_edge(controller, svc_create, "calls"),
            # Service class contains all its methods
            make_edge(service_class, svc_create, "contains"),
            make_edge(service_class, svc_delete, "contains"),
            make_edge(service_class, svc_list, "contains"),
            # Controller also has a calls edge to service class (e.g., import)
            make_edge(controller, service_class, "calls"),
        ]

        query = SliceQuery(entrypoint="PetController", max_hops=3)
        result = slice_graph(
            [controller, service_class, svc_create, svc_delete, svc_list],
            edges, query,
        )

        # svc_create should be reachable (via direct calls edge)
        assert svc_create.id in result.node_ids
        # service_class is reachable (via calls edge from controller)
        assert service_class.id in result.node_ids
        # But sibling methods should NOT be reached (contains edge skipped)
        assert svc_delete.id not in result.node_ids
        assert svc_list.id not in result.node_ids

    def test_forward_slice_class_entry_expands_to_methods(self) -> None:
        """When entry point is a class, forward slice should expand to member methods."""
        owner_class = make_symbol(
            "Owner", kind="class",
            path="src/owner.py", start_line=1, end_line=100, language="python",
        )
        get_pet = make_symbol(
            "Owner.getPet", kind="method",
            path="src/owner.py", start_line=10, end_line=20, language="python",
        )
        get_address = make_symbol(
            "Owner.getAddress", kind="method",
            path="src/owner.py", start_line=30, end_line=40, language="python",
        )
        # Dependency of getPet
        pet_repo = make_symbol(
            "PetRepository.findById", kind="method",
            path="src/pet_repo.py", start_line=1, end_line=10, language="python",
        )

        edges = [
            make_edge(owner_class, get_pet, "contains"),
            make_edge(owner_class, get_address, "contains"),
            make_edge(get_pet, pet_repo, "calls"),
        ]

        # Entry is the class itself
        query = SliceQuery(entrypoint="Owner", max_hops=3)
        result = slice_graph(
            [owner_class, get_pet, get_address, pet_repo], edges, query,
        )

        # Both methods should be in the slice (expanded from class entry)
        assert get_pet.id in result.node_ids
        assert get_address.id in result.node_ids
        # getPet's dependency should be reachable
        assert pet_repo.id in result.node_ids

    def test_reverse_slice_still_follows_contains(self) -> None:
        """Reverse slice should still follow contains edges."""
        owner_class = make_symbol(
            "Owner", kind="class",
            path="src/owner.py", start_line=1, end_line=100, language="python",
        )
        get_pet = make_symbol(
            "Owner.getPet", kind="method",
            path="src/owner.py", start_line=10, end_line=20, language="python",
        )
        caller = make_symbol(
            "show_owner", kind="function",
            path="src/views.py", start_line=1, end_line=10, language="python",
        )

        edges = [
            make_edge(owner_class, get_pet, "contains"),
            make_edge(caller, get_pet, "calls"),
        ]

        # Reverse slice from getPet should find caller (via calls edge)
        # but NOT the class (contains edges are excluded from reverse BFS
        # to prevent false positives from class-level callers).
        query = SliceQuery(entrypoint="Owner.getPet", max_hops=3, reverse=True)
        result = slice_graph(
            [owner_class, get_pet, caller], edges, query,
        )

        # caller should be found (calls edge reversed)
        assert caller.id in result.node_ids
        # owner_class should NOT be found — contains edges are excluded
        # from reverse BFS to prevent fan-out to unrelated class callers
        assert owner_class.id not in result.node_ids

    def test_reverse_slice_contains_does_not_cause_false_positives(self) -> None:
        """Reverse slice should not reach unrelated callers via contains edges.

        Scenario: method M is contained in class C. Another function F calls C
        (the class itself) but never calls M. Without filtering contains edges,
        reverse slice from M would go: M → C (via contains) → F (via calls C).
        F is a false positive — it doesn't call M.
        """
        owner_class = make_symbol(
            "Owner", kind="class",
            path="src/owner.py", start_line=1, end_line=100, language="python",
        )
        get_pet = make_symbol(
            "Owner.getPet", kind="method",
            path="src/owner.py", start_line=10, end_line=20, language="python",
        )
        # Calls getPet directly — should be in reverse slice
        direct_caller = make_symbol(
            "show_owner", kind="function",
            path="src/views.py", start_line=1, end_line=10, language="python",
        )
        # Calls the Owner class (instantiation) but NOT getPet — false positive
        class_instantiator = make_symbol(
            "create_owner", kind="function",
            path="src/factory.py", start_line=1, end_line=10, language="python",
        )

        edges = [
            make_edge(owner_class, get_pet, "contains"),
            make_edge(direct_caller, get_pet, "calls"),
            make_edge(class_instantiator, owner_class, "calls"),
        ]

        query = SliceQuery(entrypoint="Owner.getPet", max_hops=3, reverse=True)
        result = slice_graph(
            [owner_class, get_pet, direct_caller, class_instantiator],
            edges, query,
        )

        # direct_caller should be found (directly calls getPet)
        assert direct_caller.id in result.node_ids
        # class_instantiator should NOT be found (only calls the class, not the method)
        assert class_instantiator.id not in result.node_ids


class TestExcludeImports:
    """Tests for --exclude-imports flag on slice queries.

    Import edges represent file-level package dependencies rather than
    function-level call relationships. In large Go/Python codebases,
    import edges can constitute 60%+ of slice output, drowning out the
    call edges that developers actually need for refactoring analysis.
    """

    def test_exclude_imports_removes_import_edges(self) -> None:
        """Import edges are excluded from both traversal and output."""
        main_fn = make_symbol("main", path="src/main.py")
        helper = make_symbol("helper", path="src/utils.py", start_line=10)
        imported = make_symbol("lib", path="lib/core.py", start_line=20)

        call_edge = make_edge(main_fn, helper, "calls")
        import_edge = make_edge(main_fn, imported, "imports")

        query = SliceQuery(
            entrypoint="main", max_hops=3, max_files=20,
            exclude_imports=True,
        )
        result = slice_graph([main_fn, helper, imported], [call_edge, import_edge], query)

        # Call edge should be followed
        assert helper.id in result.node_ids
        assert call_edge.id in result.edge_ids

        # Import edge should NOT be followed
        assert imported.id not in result.node_ids
        assert import_edge.id not in result.edge_ids

    def test_exclude_imports_skips_file_level_imports(self) -> None:
        """File-level import edges (from file nodes) are also excluded."""
        func = make_symbol("process", path="src/app.py")
        dep = make_symbol("dep", path="lib/dep.py", start_line=10)
        file_node = make_symbol("file", path="src/app.py", kind="file", start_line=1, end_line=1)

        call_edge = make_edge(func, dep, "calls")
        file_import = make_edge(file_node, dep, "imports")

        query = SliceQuery(
            entrypoint="process", max_hops=3, max_files=20,
            exclude_imports=True,
        )
        result = slice_graph([func, dep, file_node], [call_edge, file_import], query)

        # dep is reachable via call edge
        assert dep.id in result.node_ids
        assert call_edge.id in result.edge_ids
        # But file import edge should NOT be in output
        assert file_import.id not in result.edge_ids

    def test_imports_included_by_default(self) -> None:
        """Default behavior (no exclude_imports) includes imports."""
        main_fn = make_symbol("main", path="src/main.py")
        imported = make_symbol("lib", path="lib/core.py", start_line=10)
        import_edge = make_edge(main_fn, imported, "imports")

        query = SliceQuery(entrypoint="main", max_hops=3, max_files=20)
        result = slice_graph([main_fn, imported], [import_edge], query)

        # Import should be followed by default
        assert imported.id in result.node_ids

    def test_exclude_imports_serialized_in_query(self) -> None:
        """exclude_imports flag appears in query dict."""
        query = SliceQuery(entrypoint="main", exclude_imports=True)
        d = query.to_dict()
        assert d["exclude_imports"] is True

    def test_exclude_imports_not_serialized_when_false(self) -> None:
        """exclude_imports flag omitted from query dict when False."""
        query = SliceQuery(entrypoint="main")
        d = query.to_dict()
        assert "exclude_imports" not in d


class TestSliceNodeDepths:
    """Tests for node_depths tracking in slice results."""

    def test_entry_nodes_depth_zero(self) -> None:
        """Entry nodes should have depth 0."""
        entry = make_symbol("main")
        callee = make_symbol("helper", start_line=10)
        edge = make_edge(entry, callee)

        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph([entry, callee], [edge], query)

        assert result.node_depths[entry.id] == 0

    def test_direct_callees_depth_one(self) -> None:
        """Direct callees should have depth 1."""
        entry = make_symbol("main")
        callee = make_symbol("helper", start_line=10)
        edge = make_edge(entry, callee)

        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph([entry, callee], [edge], query)

        assert result.node_depths[callee.id] == 1

    def test_chain_depths_increment(self) -> None:
        """Depths should increment along a call chain: 0 -> 1 -> 2."""
        a = make_symbol("a")
        b = make_symbol("b", start_line=10)
        c = make_symbol("c", start_line=20)
        edges = [make_edge(a, b), make_edge(b, c)]

        query = SliceQuery(entrypoint="a", max_hops=3)
        result = slice_graph([a, b, c], edges, query)

        assert result.node_depths[a.id] == 0
        assert result.node_depths[b.id] == 1
        assert result.node_depths[c.id] == 2

    def test_node_depths_in_to_dict(self) -> None:
        """node_depths should appear in to_dict() output when non-empty."""
        entry = make_symbol("main")
        callee = make_symbol("helper", start_line=10)
        edge = make_edge(entry, callee)

        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph([entry, callee], [edge], query)
        d = result.to_dict()

        assert "node_depths" in d
        assert d["node_depths"][entry.id] == 0
        assert d["node_depths"][callee.id] == 1

    def test_node_depths_omitted_when_empty(self) -> None:
        """node_depths should be omitted from to_dict() when empty (no matches)."""
        query = SliceQuery(entrypoint="nonexistent", max_hops=3)
        result = slice_graph([], [], query)
        d = result.to_dict()

        assert "node_depths" not in d

    def test_reverse_slice_tracks_depths(self) -> None:
        """Reverse slicing should also track node depths."""
        callee = make_symbol("target")
        caller = make_symbol("caller", start_line=10)
        edge = make_edge(caller, callee)

        query = SliceQuery(entrypoint="target", max_hops=3, reverse=True)
        result = slice_graph([callee, caller], [edge], query)

        assert result.node_depths[callee.id] == 0
        assert result.node_depths[caller.id] == 1

    def test_class_expansion_members_depth_zero(self) -> None:
        """Class-expanded member methods should have depth 0."""
        cls = make_symbol("MyClass", kind="class")
        method = make_symbol("MyClass.do_thing", kind="method", start_line=10)
        callee = make_symbol("helper", start_line=20)
        contains_edge = make_edge(cls, method, edge_type="contains")
        call_edge = make_edge(method, callee)

        query = SliceQuery(entrypoint="MyClass", max_hops=3)
        result = slice_graph([cls, method, callee], [contains_edge, call_edge], query)

        assert result.node_depths[cls.id] == 0
        assert result.node_depths[method.id] == 0
        assert result.node_depths[callee.id] == 1


class TestPassThroughSyntheticNodes:
    """Tests for pass-through filtering of synthetic routing nodes.

    Synthetic nodes like event_publisher and event_subscriber are created by
    linkers to represent IPC channels. They are useful for traversal (connecting
    producers to consumers) but inflate slice output with nodes that don't
    correspond to real code the developer can read or modify.

    The pass_through_kinds parameter controls which node kinds are traversed
    through during BFS but excluded from the final SliceResult.node_ids.
    """

    def test_event_publisher_excluded_from_node_ids(self) -> None:
        """event_publisher nodes are traversed but not in output.

        Uses the real edge pattern from the event sourcing linker:
        enclosing --(uses)--> event_publisher --(event_publishes)-->
        event_subscriber --(event_subscribes)--> handler
        """
        func_a = make_symbol("emit_order", path="src/orders.py")
        pub = make_symbol(
            "event:order.created", path="src/orders.py",
            kind="event_publisher", start_line=10,
        )
        sub = make_symbol(
            "event:order.created", path="src/handlers.py",
            kind="event_subscriber", start_line=1,
        )
        handler = make_symbol("on_order_created", path="src/handlers.py", start_line=5)

        edges = [
            make_edge(func_a, pub, edge_type="uses"),
            make_edge(pub, sub, edge_type="event_publishes"),
            make_edge(sub, handler, edge_type="event_subscribes"),
        ]

        query = SliceQuery(entrypoint="emit_order", max_hops=5)
        result = slice_graph([func_a, pub, sub, handler], edges, query)

        # Handler should be reachable through the synthetic nodes
        assert handler.id in result.node_ids
        # Synthetic nodes should NOT be in node_ids
        assert pub.id not in result.node_ids
        assert sub.id not in result.node_ids
        # Entry node should still be there
        assert func_a.id in result.node_ids

    def test_pass_through_still_traverses(self) -> None:
        """Nodes behind synthetic routing are reachable despite filtering."""
        producer = make_symbol("send_event", path="src/producer.py")
        pub = make_symbol(
            "event:user.signup", path="src/producer.py",
            kind="event_publisher", start_line=10,
        )
        sub = make_symbol(
            "event:user.signup", path="src/consumer.py",
            kind="event_subscriber", start_line=1,
        )
        consumer = make_symbol("handle_signup", path="src/consumer.py", start_line=5)
        downstream = make_symbol("send_welcome", path="src/consumer.py", start_line=15)

        edges = [
            make_edge(producer, pub, edge_type="uses"),
            make_edge(pub, sub, edge_type="event_publishes"),
            make_edge(sub, consumer, edge_type="event_subscribes"),
            make_edge(consumer, downstream, edge_type="calls"),
        ]

        query = SliceQuery(entrypoint="send_event", max_hops=5)
        result = slice_graph(
            [producer, pub, sub, consumer, downstream], edges, query,
        )

        # Both real consumer nodes reachable
        assert consumer.id in result.node_ids
        assert downstream.id in result.node_ids
        # Synthetic nodes filtered out
        assert pub.id not in result.node_ids
        assert sub.id not in result.node_ids

    def test_edges_to_synthetic_nodes_excluded(self) -> None:
        """Edges referencing filtered synthetic nodes are excluded."""
        func_a = make_symbol("emit_order", path="src/orders.py")
        pub = make_symbol(
            "event:order.created", path="src/orders.py",
            kind="event_publisher", start_line=10,
        )
        sub = make_symbol(
            "event:order.created", path="src/handlers.py",
            kind="event_subscriber", start_line=1,
        )
        handler = make_symbol("on_order_created", path="src/handlers.py", start_line=5)

        uses_edge = make_edge(func_a, pub, edge_type="uses")
        publishes_edge = make_edge(pub, sub, edge_type="event_publishes")
        subscribes_edge = make_edge(sub, handler, edge_type="event_subscribes")

        query = SliceQuery(entrypoint="emit_order", max_hops=5)
        result = slice_graph(
            [func_a, pub, sub, handler],
            [uses_edge, publishes_edge, subscribes_edge],
            query,
        )

        # Edges touching synthetic nodes should not be in edge_ids
        assert uses_edge.id not in result.edge_ids
        assert publishes_edge.id not in result.edge_ids
        assert subscribes_edge.id not in result.edge_ids

    def test_pass_through_kinds_empty_keeps_all(self) -> None:
        """When pass_through_kinds is empty, synthetic nodes stay in output."""
        func_a = make_symbol("emit_order", path="src/orders.py")
        pub = make_symbol(
            "event:order.created", path="src/orders.py",
            kind="event_publisher", start_line=10,
        )
        sub = make_symbol(
            "event:order.created", path="src/handlers.py",
            kind="event_subscriber", start_line=1,
        )
        handler = make_symbol("on_order_created", path="src/handlers.py", start_line=5)

        edges = [
            make_edge(func_a, pub, edge_type="uses"),
            make_edge(pub, sub, edge_type="event_publishes"),
            make_edge(sub, handler, edge_type="event_subscribes"),
        ]

        query = SliceQuery(
            entrypoint="emit_order", max_hops=5,
            pass_through_kinds=frozenset(),
        )
        result = slice_graph([func_a, pub, sub, handler], edges, query)

        # With empty pass_through_kinds, synthetic nodes ARE included
        assert pub.id in result.node_ids
        assert sub.id in result.node_ids
        assert handler.id in result.node_ids

    def test_pass_through_in_reverse_slice(self) -> None:
        """Reverse slice also filters pass-through synthetic nodes."""
        producer = make_symbol("send_event", path="src/producer.py")
        pub = make_symbol(
            "event:user.signup", path="src/producer.py",
            kind="event_publisher", start_line=10,
        )
        sub = make_symbol(
            "event:user.signup", path="src/consumer.py",
            kind="event_subscriber", start_line=1,
        )
        consumer = make_symbol("handle_signup", path="src/consumer.py", start_line=5)

        edges = [
            make_edge(producer, pub, edge_type="uses"),
            make_edge(pub, sub, edge_type="event_publishes"),
            make_edge(sub, consumer, edge_type="event_subscribes"),
        ]

        # Reverse from consumer: should find producer through synthetic nodes
        query = SliceQuery(
            entrypoint="handle_signup", max_hops=5, reverse=True,
        )
        result = slice_graph([producer, pub, sub, consumer], edges, query)

        assert producer.id in result.node_ids
        assert consumer.id in result.node_ids
        # Synthetic nodes filtered
        assert pub.id not in result.node_ids
        assert sub.id not in result.node_ids

    def test_pass_through_node_depths_excluded(self) -> None:
        """Filtered synthetic nodes should not appear in node_depths."""
        func_a = make_symbol("emit_order", path="src/orders.py")
        pub = make_symbol(
            "event:order.created", path="src/orders.py",
            kind="event_publisher", start_line=10,
        )
        sub = make_symbol(
            "event:order.created", path="src/handlers.py",
            kind="event_subscriber", start_line=1,
        )
        handler = make_symbol("on_order_created", path="src/handlers.py", start_line=5)

        edges = [
            make_edge(func_a, pub, edge_type="uses"),
            make_edge(pub, sub, edge_type="event_publishes"),
            make_edge(sub, handler, edge_type="event_subscribes"),
        ]

        query = SliceQuery(entrypoint="emit_order", max_hops=5)
        result = slice_graph([func_a, pub, sub, handler], edges, query)

        # Synthetic nodes should not be in node_depths
        assert pub.id not in result.node_depths
        assert sub.id not in result.node_depths
        # Handler should have a depth (it's reachable)
        assert handler.id in result.node_depths

    def test_custom_pass_through_kinds(self) -> None:
        """Custom pass_through_kinds can include additional synthetic types."""
        func_a = make_symbol("call_grpc", path="src/client.py")
        stub = make_symbol(
            "grpc:OrderService", path="src/client.py",
            kind="grpc_stub", start_line=10,
        )
        server = make_symbol(
            "grpc:OrderService", path="src/server.py",
            kind="grpc_server", start_line=1,
        )
        impl = make_symbol("create_order", path="src/server.py", start_line=5)

        edges = [
            make_edge(func_a, stub, edge_type="uses"),
            make_edge(stub, server, edge_type="grpc"),
            make_edge(server, impl, edge_type="implements_rpc"),
        ]

        # Include grpc_stub and grpc_server in pass-through
        query = SliceQuery(
            entrypoint="call_grpc", max_hops=5,
            pass_through_kinds=frozenset({
                "event_publisher", "event_subscriber",
                "grpc_stub", "grpc_server",
            }),
        )
        result = slice_graph([func_a, stub, server, impl], edges, query)

        assert func_a.id in result.node_ids
        assert impl.id in result.node_ids
        assert stub.id not in result.node_ids
        assert server.id not in result.node_ids


# ==================== DATAFLOW SLICE TESTS (ADR-0015) ====================


class TestDataflowSlice:
    """Tests for the --dataflow flag on slice_graph."""

    def test_dataflow_follows_write_to_read(self) -> None:
        """Dataflow mode should follow write→read edges."""
        writer = make_symbol("writer", path="src/a.py")
        reader = make_symbol("reader", path="src/b.py")
        edge = Edge.create(
            src=writer.id, dst=reader.id, edge_type="data_flows_to", line=10,
            access_mode="write", dest_access_mode="read", channel="config.key",
        )
        query = SliceQuery(entrypoint="writer", dataflow=True, max_hops=3)
        result = slice_graph([writer, reader], [edge], query)
        assert writer.id in result.node_ids
        assert reader.id in result.node_ids

    def test_dataflow_follows_mutate(self) -> None:
        """Dataflow mode should follow mutate edges (mutate implies write)."""
        mutator = make_symbol("mutator", path="src/a.py")
        reader = make_symbol("reader", path="src/b.py")
        edge = Edge.create(
            src=mutator.id, dst=reader.id, edge_type="calls", line=10,
            access_mode="mutate",
        )
        query = SliceQuery(entrypoint="mutator", dataflow=True, max_hops=3)
        result = slice_graph([mutator, reader], [edge], query)
        assert reader.id in result.node_ids

    def test_dataflow_skips_read_edges(self) -> None:
        """Dataflow mode should skip read-only edges."""
        func_a = make_symbol("func_a", path="src/a.py")
        func_b = make_symbol("func_b", path="src/b.py")
        edge = Edge.create(
            src=func_a.id, dst=func_b.id, edge_type="calls", line=10,
            access_mode="read",
        )
        query = SliceQuery(entrypoint="func_a", dataflow=True, max_hops=3)
        result = slice_graph([func_a, func_b], [edge], query)
        assert func_a.id in result.node_ids
        assert func_b.id not in result.node_ids

    def test_dataflow_skips_delete_edges(self) -> None:
        """Dataflow mode should skip delete edges."""
        func_a = make_symbol("func_a", path="src/a.py")
        func_b = make_symbol("func_b", path="src/b.py")
        edge = Edge.create(
            src=func_a.id, dst=func_b.id, edge_type="calls", line=10,
            access_mode="delete",
        )
        query = SliceQuery(entrypoint="func_a", dataflow=True, max_hops=3)
        result = slice_graph([func_a, func_b], [edge], query)
        assert func_b.id not in result.node_ids

    def test_dataflow_follows_unannotated_edges(self) -> None:
        """Edges without access_mode should still be followed (graceful degradation)."""
        func_a = make_symbol("func_a", path="src/a.py")
        func_b = make_symbol("func_b", path="src/b.py")
        edge = Edge.create(
            src=func_a.id, dst=func_b.id, edge_type="calls", line=10,
        )
        query = SliceQuery(entrypoint="func_a", dataflow=True, max_hops=3)
        result = slice_graph([func_a, func_b], [edge], query)
        # Unannotated edge should still be followed
        assert func_b.id in result.node_ids

    def test_dataflow_false_follows_all(self) -> None:
        """When dataflow=False, read edges should still be followed normally."""
        func_a = make_symbol("func_a", path="src/a.py")
        func_b = make_symbol("func_b", path="src/b.py")
        edge = Edge.create(
            src=func_a.id, dst=func_b.id, edge_type="calls", line=10,
            access_mode="read",
        )
        query = SliceQuery(entrypoint="func_a", dataflow=False, max_hops=3)
        result = slice_graph([func_a, func_b], [edge], query)
        assert func_b.id in result.node_ids

    def test_query_to_dict_includes_dataflow(self) -> None:
        """SliceQuery.to_dict should include dataflow when True."""
        query = SliceQuery(entrypoint="x", dataflow=True)
        d = query.to_dict()
        assert d["dataflow"] is True

    def test_query_to_dict_omits_dataflow_when_false(self) -> None:
        """SliceQuery.to_dict should omit dataflow when False."""
        query = SliceQuery(entrypoint="x", dataflow=False)
        d = query.to_dict()
        assert "dataflow" not in d

    def test_reverse_dataflow_follows_read_edges(self) -> None:
        """Reverse dataflow mode should follow read edges (find readers)."""
        writer = make_symbol("writer", path="src/a.py")
        reader = make_symbol("reader", path="src/b.py")
        # Edge from reader to writer (reader reads from writer's output)
        edge = Edge.create(
            src=reader.id, dst=writer.id, edge_type="calls", line=10,
            access_mode="read",
        )
        query = SliceQuery(entrypoint="writer", dataflow=True, reverse=True, max_hops=3)
        result = slice_graph([writer, reader], [edge], query)
        assert writer.id in result.node_ids
        assert reader.id in result.node_ids

    def test_reverse_dataflow_skips_write_edges(self) -> None:
        """Reverse dataflow mode should skip write edges."""
        writer = make_symbol("writer", path="src/a.py")
        other_writer = make_symbol("other_writer", path="src/b.py")
        # Edge from other_writer to writer (both writing, not a read dependency)
        edge = Edge.create(
            src=other_writer.id, dst=writer.id, edge_type="calls", line=10,
            access_mode="write",
        )
        query = SliceQuery(entrypoint="writer", dataflow=True, reverse=True, max_hops=3)
        result = slice_graph([writer, other_writer], [edge], query)
        assert writer.id in result.node_ids
        assert other_writer.id not in result.node_ids

    def test_reverse_dataflow_follows_unannotated(self) -> None:
        """Reverse dataflow should follow unannotated edges."""
        func_a = make_symbol("func_a", path="src/a.py")
        func_b = make_symbol("func_b", path="src/b.py")
        edge = Edge.create(
            src=func_b.id, dst=func_a.id, edge_type="calls", line=10,
        )
        query = SliceQuery(entrypoint="func_a", dataflow=True, reverse=True, max_hops=3)
        result = slice_graph([func_a, func_b], [edge], query)
        assert func_b.id in result.node_ids


class TestCrossLinkerIntegration:
    """Verify slice BFS traverses edges from multiple linkers in a single trace.

    Validates INV-jisit: when multiple linkers (Tauri IPC, Yjs CRDT, WebSocket,
    event sourcing) are active in the same project, a single forward slice must
    produce a connected subgraph that crosses 3+ boundary types.

    The test simulates a PlazaFlow-like polyglot project:
      TS frontend → Tauri IPC → Rust handler → Yjs CRDT → WebSocket relay
                                              → event sourcing → audit handler
    """

    @pytest.fixture()
    def polyglot_graph(self) -> tuple[list[Symbol], list[Edge]]:
        """Build a graph spanning 4 linker boundary types.

        Chain: user_action (TS) --ipc_calls--> handle_action (Rust)
               --calls--> sync_doc (Rust) --crdt_publishes--> crdt_sub
               --crdt_publishes--> relay_update (Rust)
               --websocket_message--> ws_receiver (TS)

        Branch: handle_action --uses--> event_pub --event_publishes--> event_sub
                --event_subscribes--> audit_handler (Rust)
        """
        # Frontend (TypeScript)
        user_action = make_symbol(
            "userAction", path="src/app.ts", language="typescript",
        )
        ws_receiver = make_symbol(
            "onWsMessage", path="src/ws.ts", language="typescript",
            start_line=20, end_line=30,
        )

        # Backend (Rust)
        handle_action = make_symbol(
            "handle_action", path="src-tauri/commands.rs",
            language="rust", start_line=10, end_line=20,
        )
        sync_doc = make_symbol(
            "sync_doc", path="src-tauri/crdt.rs",
            language="rust", start_line=1, end_line=10,
        )
        relay_update = make_symbol(
            "relay_update", path="src-tauri/ws.rs",
            language="rust", start_line=1, end_line=10,
        )

        # Synthetic CRDT nodes (pass-through)
        crdt_pub = make_symbol(
            "crdt:doc.content", path="src-tauri/crdt.rs",
            kind="crdt_publisher", language="rust",
            start_line=30, end_line=31,
        )
        crdt_sub = make_symbol(
            "crdt:doc.content", path="src-tauri/ws.rs",
            kind="crdt_subscriber", language="rust",
            start_line=30, end_line=31,
        )

        # Synthetic event sourcing nodes (pass-through)
        event_pub = make_symbol(
            "event:action.completed", path="src-tauri/commands.rs",
            kind="event_publisher", language="rust",
            start_line=40, end_line=41,
        )
        event_sub = make_symbol(
            "event:action.completed", path="src-tauri/audit.rs",
            kind="event_subscriber", language="rust",
            start_line=1, end_line=2,
        )
        audit_handler = make_symbol(
            "on_action_completed", path="src-tauri/audit.rs",
            language="rust", start_line=5, end_line=15,
        )

        symbols = [
            user_action, handle_action, sync_doc, crdt_pub, crdt_sub,
            relay_update, ws_receiver, event_pub, event_sub, audit_handler,
        ]
        edges = [
            # Tauri IPC boundary (TS → Rust)
            make_edge(user_action, handle_action, edge_type="ipc_calls"),
            # Internal call
            make_edge(handle_action, sync_doc, edge_type="calls"),
            # Yjs CRDT boundary (pub/sub)
            make_edge(sync_doc, crdt_pub, edge_type="uses"),
            make_edge(crdt_pub, crdt_sub, edge_type="crdt_publishes"),
            make_edge(crdt_sub, relay_update, edge_type="crdt_publishes"),
            # WebSocket boundary (Rust → TS)
            make_edge(relay_update, ws_receiver, edge_type="websocket_message"),
            # Event sourcing boundary (branching from handle_action)
            make_edge(handle_action, event_pub, edge_type="uses"),
            make_edge(event_pub, event_sub, edge_type="event_publishes"),
            make_edge(event_sub, audit_handler, edge_type="event_subscribes"),
        ]
        return symbols, edges

    def test_forward_slice_crosses_four_boundaries(
        self, polyglot_graph: tuple[list[Symbol], list[Edge]],
    ) -> None:
        """Forward slice from TS frontend reaches all endpoints via 4 linker types."""
        symbols, edges = polyglot_graph
        query = SliceQuery(
            entrypoint="userAction", max_hops=10, max_files=50,
            pass_through_kinds=frozenset({
                "event_publisher", "event_subscriber",
                "crdt_publisher", "crdt_subscriber",
            }),
        )
        result = slice_graph(symbols, edges, query)

        # Must reach endpoints across all 4 boundary types
        real_names = {
            s.name for s in symbols
            if s.id in result.node_ids
            and s.kind not in (
                "event_publisher", "event_subscriber",
                "crdt_publisher", "crdt_subscriber",
            )
        }
        assert "userAction" in real_names, "entry point missing"
        assert "handle_action" in real_names, "Tauri IPC target missing"
        assert "sync_doc" in real_names, "CRDT source missing"
        assert "relay_update" in real_names, "CRDT→WS bridge missing"
        assert "onWsMessage" in real_names, "WebSocket target missing"
        assert "on_action_completed" in real_names, "event sourcing target missing"

        # Verify synthetic nodes are filtered (pass-through)
        synthetic_ids = {
            s.id for s in symbols
            if s.kind in (
                "event_publisher", "event_subscriber",
                "crdt_publisher", "crdt_subscriber",
            )
        }
        assert not synthetic_ids & result.node_ids, \
            "synthetic pass-through nodes should be filtered from result"

    def test_forward_slice_edge_types_span_boundaries(
        self, polyglot_graph: tuple[list[Symbol], list[Edge]],
    ) -> None:
        """Verify the slice traverses edges from 4+ distinct linker domains.

        Pass-through filtering removes synthetic nodes and their edges from
        the output. The BFS still traverses these edges during exploration.
        We verify traversal by checking that nodes beyond each boundary type
        are reachable.
        """
        symbols, edges = polyglot_graph
        query = SliceQuery(
            entrypoint="userAction", max_hops=10, max_files=50,
            pass_through_kinds=frozenset({
                "event_publisher", "event_subscriber",
                "crdt_publisher", "crdt_subscriber",
            }),
        )
        result = slice_graph(symbols, edges, query)

        # Retained edges should include non-synthetic types
        edge_types_retained = {
            e.edge_type for e in edges if e.id in result.edge_ids
        }
        # ipc_calls and websocket_message connect real nodes directly
        assert "ipc_calls" in edge_types_retained, \
            "IPC boundary edge should be retained (connects real nodes)"
        assert "websocket_message" in edge_types_retained, \
            "WebSocket boundary edge should be retained (connects real nodes)"

        # crdt_publishes and event_publishes connect synthetic nodes, so they
        # are filtered out. But we verify traversal happened by checking that
        # nodes beyond those boundaries are reachable.
        reachable = {
            s.name for s in symbols if s.id in result.node_ids
        }
        # relay_update is only reachable via CRDT pass-through chain
        assert "relay_update" in reachable, \
            "CRDT boundary traversal failed: relay_update unreachable"
        # on_action_completed is only reachable via event pass-through chain
        assert "on_action_completed" in reachable, \
            "Event sourcing boundary traversal failed: handler unreachable"

    def test_reverse_slice_from_ws_receiver(
        self, polyglot_graph: tuple[list[Symbol], list[Edge]],
    ) -> None:
        """Reverse slice from WS receiver traces back through CRDT and IPC."""
        symbols, edges = polyglot_graph
        query = SliceQuery(
            entrypoint="onWsMessage", max_hops=10, max_files=50, reverse=True,
            pass_through_kinds=frozenset({
                "crdt_publisher", "crdt_subscriber",
            }),
        )
        result = slice_graph(symbols, edges, query)

        real_names = {
            s.name for s in symbols
            if s.id in result.node_ids
            and s.kind not in ("crdt_publisher", "crdt_subscriber")
        }
        assert "onWsMessage" in real_names, "entry point missing"
        assert "relay_update" in real_names, "WS source missing"
        assert "sync_doc" in real_names, "CRDT source missing"
        assert "handle_action" in real_names, "Tauri handler missing"
        assert "userAction" in real_names, "TS frontend missing"
        # Event branch should NOT appear (not on path to ws_receiver)
        assert "audit_handler" not in real_names, \
            "event branch should not appear in reverse slice to WS"

    def test_reverse_slice_from_audit_handler(
        self, polyglot_graph: tuple[list[Symbol], list[Edge]],
    ) -> None:
        """Reverse slice from audit handler traces back through events and IPC."""
        symbols, edges = polyglot_graph
        query = SliceQuery(
            entrypoint="on_action_completed", max_hops=10, max_files=50,
            reverse=True,
            pass_through_kinds=frozenset({
                "event_publisher", "event_subscriber",
            }),
        )
        result = slice_graph(symbols, edges, query)

        real_names = {
            s.name for s in symbols
            if s.id in result.node_ids
            and s.kind not in ("event_publisher", "event_subscriber")
        }
        assert "on_action_completed" in real_names
        assert "handle_action" in real_names, "Tauri handler missing"
        assert "userAction" in real_names, "TS frontend missing"
        # WS branch should NOT appear
        assert "onWsMessage" not in real_names, \
            "WS branch should not appear in reverse slice to audit"

    def test_max_files_limits_cross_boundary_reach(
        self, polyglot_graph: tuple[list[Symbol], list[Edge]],
    ) -> None:
        """max_files limits prune the slice but still preserve BFS ordering.

        With max_files=4 (7 unique files in graph), the slice correctly
        includes files in BFS order from the entry point. Deeper boundary
        crossings may be pruned — this is expected behavior, not a bug.
        The important invariant is that the BFS traversal itself does not
        skip edge types; it's the file budget that limits reach.
        """
        symbols, edges = polyglot_graph
        query = SliceQuery(
            entrypoint="userAction", max_hops=10, max_files=4,
            pass_through_kinds=frozenset({
                "event_publisher", "event_subscriber",
                "crdt_publisher", "crdt_subscriber",
            }),
        )
        result = slice_graph(symbols, edges, query)

        # Entry point and at least 1 cross-boundary target must be reached
        assert any(
            s.name == "userAction" for s in symbols if s.id in result.node_ids
        ), "entry point missing even with tight file budget"
        # At least some boundary crossing should survive
        edge_types_in_slice = {
            e.edge_type for e in edges if e.id in result.edge_ids
        }
        boundary_types = {
            "ipc_calls", "crdt_publishes", "websocket_message",
            "event_publishes", "event_subscribes",
        }
        boundaries_present = edge_types_in_slice & boundary_types
        assert len(boundaries_present) >= 1, (
            f"Even with tight file budget, at least 1 boundary crossing "
            f"should survive. Got: {edge_types_in_slice}"
        )
        # File limit should be hit
        assert "file_limit" in result.limits_hit
