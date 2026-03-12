# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for slice tier filtering (--max-tier flag).

Tests that BFS traversal respects supply chain tier boundaries,
stopping at nodes with tier > max_tier.
"""

from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.slice import slice_graph, SliceQuery
from hypergumbo_core.cli import build_parser


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    start_line: int = 1,
    supply_chain_tier: int = 1,
) -> Symbol:
    """Helper to create test symbols with tier."""
    span = Span(start_line=start_line, end_line=start_line + 5, start_col=0, end_col=10)
    sym_id = f"python:{path}:{start_line}-{start_line + 5}:{name}:{kind}"
    sym = Symbol(
        id=sym_id,
        name=name,
        kind=kind,
        language="python",
        path=path,
        span=span,
        origin="python-ast-v1",
        origin_run_id="uuid:test",
    )
    sym.supply_chain_tier = supply_chain_tier
    return sym


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


class TestSliceTierFilterParser:
    """Test --max-tier argument parsing for slice command."""

    def test_slice_has_max_tier_argument(self):
        """Slice command should accept --max-tier."""
        parser = build_parser()
        args = parser.parse_args(["slice", ".", "--entry", "main", "--max-tier", "1"])
        assert args.max_tier == 1

    def test_slice_max_tier_default_is_none(self):
        """Default max-tier should be None (no filtering)."""
        parser = build_parser()
        args = parser.parse_args(["slice", ".", "--entry", "main"])
        assert args.max_tier is None


class TestSliceTierFilterBehavior:
    """Test tier-based filtering during BFS traversal."""

    def test_no_tier_filter_includes_all(self):
        """Without max_tier, all nodes are included."""
        # Entry point (tier 1) -> external dep (tier 3) -> another external (tier 3)
        entry = make_symbol("main", path="src/app.py", supply_chain_tier=1)
        external1 = make_symbol("lodash", path="node_modules/lodash/index.js", supply_chain_tier=3)
        external2 = make_symbol("underscore", path="node_modules/underscore/index.js", supply_chain_tier=3)

        edge1 = make_edge(entry, external1)
        edge2 = make_edge(external1, external2)

        query = SliceQuery(entrypoint="main", max_hops=3)
        result = slice_graph([entry, external1, external2], [edge1, edge2], query)

        # Should include all nodes
        assert entry.id in result.node_ids
        assert external1.id in result.node_ids
        assert external2.id in result.node_ids

    def test_tier_1_stops_at_boundary(self):
        """max_tier=1 stops at first-party boundary."""
        entry = make_symbol("main", path="src/app.py", supply_chain_tier=1)
        first_party = make_symbol("helper", path="src/utils.py", supply_chain_tier=1)
        external = make_symbol("lodash", path="node_modules/lodash/index.js", supply_chain_tier=3)

        edge1 = make_edge(entry, first_party)
        edge2 = make_edge(first_party, external)

        query = SliceQuery(entrypoint="main", max_hops=5, max_tier=1)
        result = slice_graph([entry, first_party, external], [edge1, edge2], query)

        # Should include first-party nodes
        assert entry.id in result.node_ids
        assert first_party.id in result.node_ids
        # Should NOT include external dep
        assert external.id not in result.node_ids
        # Should report tier_limit hit
        assert "tier_limit" in result.limits_hit

    def test_tier_2_includes_internal_deps(self):
        """max_tier=2 includes first-party and internal deps."""
        entry = make_symbol("main", path="src/app.py", supply_chain_tier=1)
        example = make_symbol("demo", path="examples/demo.py", supply_chain_tier=2)
        external = make_symbol("lodash", path="node_modules/lodash/index.js", supply_chain_tier=3)

        edge1 = make_edge(entry, example)
        edge2 = make_edge(example, external)

        query = SliceQuery(entrypoint="main", max_hops=5, max_tier=2)
        result = slice_graph([entry, example, external], [edge1, edge2], query)

        # Should include tier 1 and 2
        assert entry.id in result.node_ids
        assert example.id in result.node_ids
        # Should NOT include tier 3
        assert external.id not in result.node_ids

    def test_tier_3_excludes_derived(self):
        """max_tier=3 includes all except derived artifacts."""
        entry = make_symbol("main", path="src/app.py", supply_chain_tier=1)
        external = make_symbol("lodash", path="node_modules/lodash/index.js", supply_chain_tier=3)
        derived = make_symbol("bundle", path="dist/bundle.js", supply_chain_tier=4)

        edge1 = make_edge(entry, external)
        edge2 = make_edge(external, derived)

        query = SliceQuery(entrypoint="main", max_hops=5, max_tier=3)
        result = slice_graph([entry, external, derived], [edge1, edge2], query)

        # Should include tier 1 and 3
        assert entry.id in result.node_ids
        assert external.id in result.node_ids
        # Should NOT include tier 4 (derived)
        assert derived.id not in result.node_ids

    def test_tier_filter_in_query_to_dict(self):
        """SliceQuery.to_dict should include max_tier."""
        query = SliceQuery(entrypoint="main", max_tier=1)
        result = query.to_dict()
        assert result["max_tier"] == 1

    def test_tier_filter_none_excluded_from_dict(self):
        """SliceQuery.to_dict should not include max_tier if None."""
        query = SliceQuery(entrypoint="main")
        result = query.to_dict()
        assert "max_tier" not in result or result.get("max_tier") is None


class TestSliceTierFilterReverse:
    """Test tier filtering in reverse slice mode."""

    def test_reverse_tier_filter(self):
        """Reverse slice should also respect tier limits."""
        external = make_symbol("lodash", path="node_modules/lodash/index.js", supply_chain_tier=3)
        first_party = make_symbol("helper", path="src/utils.py", supply_chain_tier=1)
        entry = make_symbol("main", path="src/app.py", supply_chain_tier=1)

        # external calls first_party calls entry
        edge1 = make_edge(external, first_party)
        edge2 = make_edge(first_party, entry)

        # Reverse slice from entry: what calls entry?
        query = SliceQuery(entrypoint="main", max_hops=5, max_tier=1, reverse=True)
        result = slice_graph([external, first_party, entry], [edge1, edge2], query)

        # Should include entry and first_party (both tier 1)
        assert entry.id in result.node_ids
        assert first_party.id in result.node_ids
        # Should NOT include external (tier 3)
        assert external.id not in result.node_ids


class TestSliceHubPruningDepthExempt:
    """Test that depth-1 nodes are exempt from hub pruning.

    In the common "main → run()" orchestrator pattern, the run() function
    has many outgoing edges (it sets up the entire application). If run()
    is hub-pruned, the slice from main is nearly empty. Exempting depth-1
    nodes from hub pruning ensures the entry point's immediate dependencies
    are always traversed, giving a useful slice even when those dependencies
    are high-degree orchestrators.
    """

    def test_depth1_hub_node_traversed(self):
        """A hub node at depth 1 should be traversed, not pruned."""
        # main → run (hub, 60 outgoing edges) → many callees
        entry = make_symbol("main", path="cmd/main.go", kind="function")
        run = make_symbol("run", path="cmd/main.go", kind="function", start_line=100)

        # Create 60 callees of run (exceeds default hub_threshold=50)
        callees = []
        for i in range(60):
            callees.append(make_symbol(
                f"setup_{i}", path=f"pkg/setup_{i}.go", kind="function",
            ))

        edges = [make_edge(entry, run)]
        for callee in callees:
            edges.append(make_edge(run, callee))

        all_symbols = [entry, run] + callees
        query = SliceQuery(
            entrypoint="main", max_hops=2, hub_threshold=50,
        )
        result = slice_graph(all_symbols, edges, query)

        # run should be included AND traversed (depth 1 exempt from pruning)
        assert run.id in result.node_ids
        # All 60 callees should be included (traversal through run)
        for callee in callees:
            assert callee.id in result.node_ids, (
                f"Callee {callee.name} should be in slice — "
                f"depth-1 hub node 'run' should not be pruned"
            )

    def test_depth2_hub_node_still_pruned(self):
        """A hub node at depth 2 should still be pruned."""
        entry = make_symbol("main", path="cmd/main.go", kind="function")
        run = make_symbol("run", path="cmd/main.go", kind="function", start_line=100)
        orchestrator = make_symbol(
            "orchestrator", path="pkg/orch.go", kind="function",
        )

        # orchestrator has 60 outgoing edges (hub)
        leaf_nodes = []
        for i in range(60):
            leaf_nodes.append(make_symbol(
                f"leaf_{i}", path=f"pkg/leaf_{i}.go", kind="function",
            ))

        edges = [
            make_edge(entry, run),
            make_edge(run, orchestrator),
        ]
        for leaf in leaf_nodes:
            edges.append(make_edge(orchestrator, leaf))

        all_symbols = [entry, run, orchestrator] + leaf_nodes
        query = SliceQuery(
            entrypoint="main", max_hops=3, hub_threshold=50,
        )
        result = slice_graph(all_symbols, edges, query)

        # orchestrator (depth 2) should be included but NOT traversed
        assert orchestrator.id in result.node_ids
        # Its leaves should NOT be in the slice (hub pruned at depth 2)
        included_leaves = [l for l in leaf_nodes if l.id in result.node_ids]
        assert len(included_leaves) == 0, (
            f"Expected 0 leaves (depth-2 hub pruned), got {len(included_leaves)}"
        )
        assert "hub_pruned" in result.limits_hit

    def test_reverse_depth1_hub_exempt(self):
        """In reverse slice, depth-1 hubs should also be exempt."""
        target = make_symbol("handler", path="api/handler.go", kind="function")
        dispatcher = make_symbol(
            "dispatcher", path="api/dispatch.go", kind="function", start_line=50,
        )

        # dispatcher has 60 incoming edges (many things call it)
        # But we're doing reverse slice, so relevant_edges are "edges_to"
        callers = []
        for i in range(60):
            callers.append(make_symbol(
                f"caller_{i}", path=f"cmd/caller_{i}.go", kind="function",
            ))

        edges = [make_edge(dispatcher, target)]
        for caller in callers:
            edges.append(make_edge(caller, dispatcher))

        all_symbols = [target, dispatcher] + callers
        query = SliceQuery(
            entrypoint="handler", max_hops=2, hub_threshold=50, reverse=True,
        )
        result = slice_graph(all_symbols, edges, query)

        # dispatcher (depth 1 in reverse) should be traversed
        assert dispatcher.id in result.node_ids
        # All callers should be included
        for caller in callers:
            assert caller.id in result.node_ids, (
                f"Caller {caller.name} should be in reverse slice"
            )


class TestSliceNodeTierPropagation:
    """Tests for supply_chain tier propagation into slice results (WI-fonif).

    SliceResult.node_tiers maps each node ID to its supply_chain_tier
    integer, enabling tier-based filtering in downstream tooling without
    requiring the full behavior map.
    """

    def test_node_tiers_populated_for_all_nodes(self):
        """Every node in the slice gets a tier entry."""
        entry = make_symbol("main", path="src/app.py", supply_chain_tier=1)
        dep = make_symbol("helper", path="src/utils.py", supply_chain_tier=1)
        ext = make_symbol("lodash", path="node_modules/lodash.js",
                          supply_chain_tier=3)

        edges = [make_edge(entry, dep), make_edge(dep, ext)]
        query = SliceQuery(entrypoint="main", max_hops=5)
        result = slice_graph([entry, dep, ext], edges, query)

        assert entry.id in result.node_tiers
        assert dep.id in result.node_tiers
        assert ext.id in result.node_tiers

    def test_node_tiers_reflect_symbol_tier(self):
        """Tier values match the supply_chain_tier from the Symbol."""
        t1 = make_symbol("main", path="src/app.py", supply_chain_tier=1)
        t2 = make_symbol("internal", path="lib/pkg.py", supply_chain_tier=2)
        t3 = make_symbol("ext", path="vendor/ext.py", supply_chain_tier=3)

        edges = [make_edge(t1, t2), make_edge(t2, t3)]
        query = SliceQuery(entrypoint="main", max_hops=5)
        result = slice_graph([t1, t2, t3], edges, query)

        assert result.node_tiers[t1.id] == 1
        assert result.node_tiers[t2.id] == 2
        assert result.node_tiers[t3.id] == 3

    def test_node_tiers_in_to_dict(self):
        """node_tiers appears in serialized output."""
        entry = make_symbol("main", path="src/app.py", supply_chain_tier=1)
        dep = make_symbol("dep", path="vendor/dep.py", supply_chain_tier=3)

        edges = [make_edge(entry, dep)]
        query = SliceQuery(entrypoint="main", max_hops=5)
        result = slice_graph([entry, dep], edges, query)

        d = result.to_dict()
        assert "node_tiers" in d
        assert d["node_tiers"][entry.id] == 1
        assert d["node_tiers"][dep.id] == 3

    def test_node_tiers_empty_when_no_nodes(self):
        """Empty slice has empty node_tiers."""
        # Entry excluded by test filter → empty slice
        entry = make_symbol("test_main", path="tests/test_main.py",
                            supply_chain_tier=1)
        query = SliceQuery(entrypoint="test_main", exclude_tests=True)
        result = slice_graph([entry], [], query)

        assert result.node_tiers == {}
        # to_dict should omit empty node_tiers
        d = result.to_dict()
        assert "node_tiers" not in d
