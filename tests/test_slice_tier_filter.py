"""Tests for slice tier filtering (--max-tier flag).

Tests that BFS traversal respects supply chain tier boundaries,
stopping at nodes with tier > max_tier.
"""

from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.slice import slice_graph, SliceQuery
from hypergumbo.cli import build_parser


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
