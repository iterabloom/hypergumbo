# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the internal representation (IR) layer."""
from pathlib import Path

from hypergumbo_core.ir import (
    VALID_ACCESS_MODES,
    AnalysisRun, Edge, Span, Symbol, UsageContext, create_boundary_nodes,
)
from hypergumbo_lang_mainstream.py import analyze_python


def test_symbol_has_required_fields() -> None:
    """Symbol dataclass should have all required fields."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
    )

    assert symbol.id == "python:test.py:1-2:greet:function"
    assert symbol.name == "greet"
    assert symbol.kind == "function"
    assert symbol.language == "python"
    assert symbol.path == "test.py"
    assert symbol.line == 1  # property for backwards compat
    assert symbol.end_line == 2  # property for backwards compat
    assert symbol.span.start_col == 0
    assert symbol.span.end_col == 10


def test_analyze_python_returns_symbols(tmp_path: Path) -> None:
    """analyze_python should return AnalysisResult with Symbol objects."""
    py_file = tmp_path / "hello.py"
    py_file.write_text("def greet():\n    pass\n")

    result = analyze_python(tmp_path)

    assert len(result.symbols) == 1
    assert isinstance(result.symbols[0], Symbol)
    assert result.symbols[0].name == "greet"
    assert result.symbols[0].kind == "function"


def test_symbol_id_format(tmp_path: Path) -> None:
    """Symbol id should follow the spec format: {lang}:{file}:{start}-{end}:{name}:{kind}."""
    py_file = tmp_path / "models.py"
    py_file.write_text("class User:\n    pass\n")

    result = analyze_python(tmp_path)

    assert len(result.symbols) == 1
    symbol = result.symbols[0]
    # ID should contain all components
    assert symbol.language in symbol.id
    assert "models.py" in symbol.id
    assert symbol.name in symbol.id
    assert symbol.kind in symbol.id


# ==================== NEW SPEC FIELDS TESTS ====================


def test_analysis_run_has_run_signature() -> None:
    """AnalysisRun should have run_signature field for deterministic fingerprint."""
    run = AnalysisRun.create(pass_id="python-ast-v1", version="0.5.0")

    # run_signature should be deterministic based on pass+version+config
    assert hasattr(run, "run_signature")
    assert run.run_signature is not None
    assert run.run_signature.startswith("sha256:")


def test_analysis_run_has_toolchain() -> None:
    """AnalysisRun should have toolchain dict with runtime info."""
    run = AnalysisRun.create(pass_id="python-ast-v1", version="0.5.0")

    assert hasattr(run, "toolchain")
    assert isinstance(run.toolchain, dict)
    # For Python analyzer, should have python version
    assert "name" in run.toolchain
    assert "version" in run.toolchain


def test_analysis_run_has_config_fingerprint() -> None:
    """AnalysisRun should have config_fingerprint for cache invalidation."""
    run = AnalysisRun.create(pass_id="python-ast-v1", version="0.5.0")

    assert hasattr(run, "config_fingerprint")
    assert run.config_fingerprint is not None
    assert run.config_fingerprint.startswith("sha256:")


def test_analysis_run_has_repo_fingerprint() -> None:
    """AnalysisRun should have repo_fingerprint for cache keying."""
    run = AnalysisRun.create(pass_id="python-ast-v1", version="0.5.0")

    assert hasattr(run, "repo_fingerprint")
    # Can be None if not set, but field must exist


def test_analysis_run_has_skipped_passes() -> None:
    """AnalysisRun should have skipped_passes list."""
    run = AnalysisRun.create(pass_id="python-ast-v1", version="0.5.0")

    assert hasattr(run, "skipped_passes")
    assert isinstance(run.skipped_passes, list)


def test_analysis_run_has_warnings() -> None:
    """AnalysisRun should have warnings list."""
    run = AnalysisRun.create(pass_id="python-ast-v1", version="0.5.0")

    assert hasattr(run, "warnings")
    assert isinstance(run.warnings, list)


def test_analysis_run_to_dict_includes_new_fields() -> None:
    """AnalysisRun.to_dict should include all spec fields."""
    run = AnalysisRun.create(pass_id="python-ast-v1", version="0.5.0")
    d = run.to_dict()

    assert "run_signature" in d
    assert "toolchain" in d
    assert "config_fingerprint" in d
    assert "repo_fingerprint" in d
    assert "skipped_passes" in d
    assert "warnings" in d


def test_symbol_has_canonical_name() -> None:
    """Symbol should have canonical_name field for fully qualified name."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
        canonical_name="mymodule.greet",
    )

    assert symbol.canonical_name == "mymodule.greet"


def test_symbol_has_fingerprint() -> None:
    """Symbol should have fingerprint field for content hash."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
        fingerprint="sha256:abc123",
    )

    assert symbol.fingerprint == "sha256:abc123"


def test_symbol_has_quality() -> None:
    """Symbol should have quality field with score and reason."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
        quality={"score": 0.95, "reason": "AST-based definition"},
    )

    assert symbol.quality["score"] == 0.95
    assert symbol.quality["reason"] == "AST-based definition"


def test_symbol_to_dict_includes_new_fields() -> None:
    """Symbol.to_dict should include all spec fields."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
        canonical_name="mymodule.greet",
        fingerprint="sha256:abc123",
        quality={"score": 0.95, "reason": "AST-based definition"},
    )
    d = symbol.to_dict()

    assert "canonical_name" in d
    assert "fingerprint" in d
    assert "quality" in d


def test_edge_has_edge_key() -> None:
    """Edge should have edge_key for canonical identity."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
    )

    assert hasattr(edge, "edge_key")
    assert edge.edge_key is not None
    assert edge.edge_key.startswith("edgekey:sha256:")


def test_edge_id_unique_per_line() -> None:
    """Edge IDs must be unique - different lines should produce different IDs.

    This is INV-005: Edge IDs must be unique because they serve as primary keys.
    Multiple calls from the same function to the same target at different lines
    should each get a unique edge ID.
    """
    edge1 = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=10,
    )
    edge2 = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=20,
    )

    # Different lines MUST produce different IDs
    assert edge1.id != edge2.id, "Edge IDs must be unique per call site"

    # But edge_key should be the same (for deduplication purposes)
    assert edge1.edge_key == edge2.edge_key


def test_deduplicate_edges_collapses_same_key() -> None:
    """deduplicate_edges keeps first edge per edge_key, discards rest.

    Multiple call sites from the same function to the same target produce
    edges with different IDs (line-sensitive) but identical edge_keys
    (line-insensitive).  For a call graph, one edge per (src, dst, type)
    is the correct model.
    """
    from hypergumbo_core.ir import deduplicate_edges

    edge1 = Edge.create(
        src="ruby:a.rb:1-2:Foo#bar:method",
        dst="ruby:b.rb:3-4:Baz#qux:method",
        edge_type="calls",
        line=10,
    )
    edge2 = Edge.create(
        src="ruby:a.rb:1-2:Foo#bar:method",
        dst="ruby:b.rb:3-4:Baz#qux:method",
        edge_type="calls",
        line=20,
    )
    edge3 = Edge.create(
        src="ruby:a.rb:1-2:Foo#bar:method",
        dst="ruby:c.rb:5-6:Other#func:method",
        edge_type="calls",
        line=15,
    )

    result = deduplicate_edges([edge1, edge2, edge3])

    # Two unique relationships, first occurrence kept for duplicates
    assert len(result) == 2
    assert result[0].id == edge1.id  # First occurrence kept
    assert result[1].id == edge3.id  # Different relationship kept


def test_deduplicate_edges_removes_self_loops() -> None:
    """deduplicate_edges with remove_self_loops=True drops src==dst edges."""
    from hypergumbo_core.ir import deduplicate_edges

    normal = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=10,
    )
    self_loop = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:a.py:1-2:foo:function",
        edge_type="calls",
        line=20,
    )

    result = deduplicate_edges([normal, self_loop], remove_self_loops=True)
    assert len(result) == 1
    assert result[0].id == normal.id


def test_deduplicate_edges_handles_none_edge_key() -> None:
    """Edges with edge_key=None must not collapse into a single edge.

    Regression test: Edge() constructor (not Edge.create()) defaults
    edge_key to None.  When multiple such edges existed, only the first
    survived deduplication because None was treated as a valid dedup key.
    This silently dropped ALL routes_to edges from the route-handler linker.
    """
    from hypergumbo_core.ir import deduplicate_edges

    # Simulate edges created by the route_handler linker (using Edge constructor)
    edge_a = Edge(
        id="edge:route1->handler1",
        src="go:server.go:10-10:GET /users:route",
        dst="go:server.go:20-30:listUsers:method",
        edge_type="routes_to",
        line=10,
        # edge_key deliberately not set (None) — matches real bug
    )
    edge_b = Edge(
        id="edge:route2->handler2",
        src="go:server.go:11-11:POST /users:route",
        dst="go:server.go:40-50:createUser:method",
        edge_type="routes_to",
        line=11,
    )
    edge_c = Edge(
        id="edge:dockerfile->stage",
        src="docker:Dockerfile:1-1:stage1:stage",
        dst="docker:Dockerfile:10-10:stage2:stage",
        edge_type="depends_on",
        line=1,
    )

    result = deduplicate_edges([edge_a, edge_b, edge_c])

    # All three edges are unique relationships — all must survive
    assert len(result) == 3, (
        f"Expected 3 unique edges but got {len(result)}; "
        f"None edge_key must not cause false deduplication"
    )


def test_deduplicate_edges_none_key_still_deduplicates_true_duplicates() -> None:
    """True duplicates (same src+dst+type) with None edge_key are still collapsed."""
    from hypergumbo_core.ir import deduplicate_edges

    edge_a = Edge(
        id="edge:route1->handler1:line10",
        src="go:server.go:10-10:GET /users:route",
        dst="go:server.go:20-30:listUsers:method",
        edge_type="routes_to",
        line=10,
    )
    edge_a_dup = Edge(
        id="edge:route1->handler1:line15",
        src="go:server.go:10-10:GET /users:route",
        dst="go:server.go:20-30:listUsers:method",
        edge_type="routes_to",
        line=15,
    )

    result = deduplicate_edges([edge_a, edge_a_dup])

    # Same src+dst+type → should collapse to 1 even with None edge_key
    assert len(result) == 1
    assert result[0].id == edge_a.id


def test_deduplicate_edges_preserves_different_types() -> None:
    """Edges with same src/dst but different edge_types are distinct."""
    from hypergumbo_core.ir import deduplicate_edges

    calls_edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=10,
    )
    imports_edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="imports",
        line=10,
    )

    result = deduplicate_edges([calls_edge, imports_edge])
    assert len(result) == 2


def test_edge_has_quality() -> None:
    """Edge should have quality field with score and reason."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
    )
    edge.quality = {"score": 0.85, "reason": "Direct AST call"}

    assert edge.quality["score"] == 0.85
    assert edge.quality["reason"] == "Direct AST call"


def test_edge_has_evidence_lang() -> None:
    """Edge should have evidence_lang in meta."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        evidence_lang="python",
    )

    assert edge.evidence_lang == "python"


def test_edge_has_evidence_spans() -> None:
    """Edge should have evidence_spans in meta."""
    evidence_spans = [{"file": "a.py", "span": {"start_line": 5, "end_line": 5}}]
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        evidence_spans=evidence_spans,
    )

    assert edge.evidence_spans == evidence_spans


def test_edge_to_dict_includes_new_fields() -> None:
    """Edge.to_dict should include all spec fields."""
    evidence_spans = [{"file": "a.py", "span": {"start_line": 5, "end_line": 5}}]
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        evidence_lang="python",
        evidence_spans=evidence_spans,
    )
    edge.quality = {"score": 0.85, "reason": "Direct AST call"}
    d = edge.to_dict()

    assert "edge_key" in d
    assert "quality" in d
    assert "evidence_lang" in d["meta"]
    assert "evidence_spans" in d["meta"]


def test_edge_with_custom_meta() -> None:
    """Edge.to_dict should merge custom meta fields."""
    edge = Edge.create(
        src="ipc:sender.ts:10:send:my-channel",
        dst="ipc:receiver.ts:20:receive:my-channel",
        edge_type="message_send",
        line=10,
    )
    edge.meta = {"channel": "my-channel"}
    d = edge.to_dict()

    assert d["meta"]["evidence_type"] == "ast_call_direct"
    assert d["meta"]["channel"] == "my-channel"


# ==================== DATAFLOW ACCESS MODE TESTS (ADR-0015) ====================


def test_valid_access_modes_vocabulary() -> None:
    """VALID_ACCESS_MODES should contain the four defined modes."""
    assert VALID_ACCESS_MODES == frozenset({"read", "write", "mutate", "delete"})


def test_edge_create_access_mode_kwargs() -> None:
    """Edge.create should accept access_mode, dest_access_mode, channel kwargs."""
    edge = Edge.create(
        src="py:src/a.py:10:writer:function",
        dst="py:src/b.py:20:reader:function",
        edge_type="data_flows_to",
        line=10,
        access_mode="write",
        dest_access_mode="read",
        channel="awareness.cursor",
    )
    assert edge.meta is not None
    assert edge.meta["access_mode"] == "write"
    assert edge.meta["dest_access_mode"] == "read"
    assert edge.meta["channel"] == "awareness.cursor"


def test_edge_create_access_mode_merges_with_existing_meta() -> None:
    """access_mode kwargs should merge with explicitly passed meta."""
    edge = Edge.create(
        src="py:src/a.py:10:writer:function",
        dst="py:src/b.py:20:reader:function",
        edge_type="event_publishes",
        line=10,
        meta={"topic": "user.created"},
        access_mode="write",
        dest_access_mode="read",
        channel="user.created",
    )
    assert edge.meta is not None
    assert edge.meta["topic"] == "user.created"
    assert edge.meta["access_mode"] == "write"
    assert edge.meta["dest_access_mode"] == "read"
    assert edge.meta["channel"] == "user.created"


def test_edge_create_access_mode_none_omitted() -> None:
    """When access_mode kwargs are None, they should not appear in meta."""
    edge = Edge.create(
        src="py:src/a.py:10:f:function",
        dst="py:src/b.py:20:g:function",
        edge_type="calls",
        line=10,
    )
    # meta should be None when no meta or access_mode kwargs are passed
    assert edge.meta is None


def test_edge_create_partial_access_mode() -> None:
    """Only non-None access_mode kwargs should appear in meta."""
    edge = Edge.create(
        src="py:src/a.py:10:f:function",
        dst="py:src/b.py:20:g:function",
        edge_type="calls",
        line=10,
        access_mode="write",
    )
    assert edge.meta is not None
    assert edge.meta["access_mode"] == "write"
    assert "dest_access_mode" not in edge.meta
    assert "channel" not in edge.meta


def test_edge_create_invalid_access_mode_raises() -> None:
    """Edge.create should reject invalid access_mode values."""
    import pytest
    with pytest.raises(ValueError, match="access_mode"):
        Edge.create(
            src="py:src/a.py:10:f:function",
            dst="py:src/b.py:20:g:function",
            edge_type="calls",
            line=10,
            access_mode="bogus",
        )


def test_edge_create_invalid_dest_access_mode_raises() -> None:
    """Edge.create should reject invalid dest_access_mode values."""
    import pytest
    with pytest.raises(ValueError, match="dest_access_mode"):
        Edge.create(
            src="py:src/a.py:10:f:function",
            dst="py:src/b.py:20:g:function",
            edge_type="calls",
            line=10,
            dest_access_mode="bogus",
        )


def test_edge_access_mode_survives_to_dict() -> None:
    """access_mode fields in meta should appear in to_dict output."""
    edge = Edge.create(
        src="py:src/a.py:10:f:function",
        dst="py:src/b.py:20:g:function",
        edge_type="data_flows_to",
        line=10,
        access_mode="write",
        dest_access_mode="read",
        channel="config.db_url",
    )
    d = edge.to_dict()
    assert d["meta"]["access_mode"] == "write"
    assert d["meta"]["dest_access_mode"] == "read"
    assert d["meta"]["channel"] == "config.db_url"


def test_edge_create_backward_compatible() -> None:
    """Existing Edge.create calls without access_mode kwargs should work unchanged."""
    edge = Edge.create(
        src="py:src/a.py:10:f:function",
        dst="py:src/b.py:20:g:function",
        edge_type="calls",
        line=10,
        origin="python_analyzer",
        meta={"some_key": "some_value"},
    )
    assert edge.meta == {"some_key": "some_value"}
    assert "access_mode" not in edge.meta


# ==================== SUPPLY CHAIN FIELDS TESTS ====================


def test_symbol_has_supply_chain_fields() -> None:
    """Symbol should have supply_chain_tier and supply_chain_reason fields."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:src/test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="src/test.py",
        span=span,
        supply_chain_tier=1,
        supply_chain_reason="matches ^src/",
    )

    assert symbol.supply_chain_tier == 1
    assert symbol.supply_chain_reason == "matches ^src/"


def test_symbol_supply_chain_defaults() -> None:
    """Symbol supply_chain_tier should default to 1 (first_party)."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
    )

    assert symbol.supply_chain_tier == 1
    assert symbol.supply_chain_reason == ""


def test_symbol_to_dict_includes_supply_chain() -> None:
    """Symbol.to_dict should include supply_chain object with tier, tier_name, reason."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:src/test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="src/test.py",
        span=span,
        supply_chain_tier=1,
        supply_chain_reason="matches ^src/",
    )
    d = symbol.to_dict()

    assert "supply_chain" in d
    assert d["supply_chain"]["tier"] == 1
    assert d["supply_chain"]["tier_name"] == "first_party"
    assert d["supply_chain"]["reason"] == "matches ^src/"


def test_symbol_to_dict_supply_chain_all_tiers() -> None:
    """Symbol.to_dict should produce correct tier_name for all tiers."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)

    # Test each tier
    tier_names = {
        1: "first_party",
        2: "internal_dep",
        3: "external_dep",
        4: "derived",
    }

    for tier, tier_name in tier_names.items():
        symbol = Symbol(
            id="python:test.py:1-2:greet:function",
            name="greet",
            kind="function",
            language="python",
            path="test.py",
            span=span,
            supply_chain_tier=tier,
            supply_chain_reason=f"test reason for tier {tier}",
        )
        d = symbol.to_dict()

        assert d["supply_chain"]["tier"] == tier
        assert d["supply_chain"]["tier_name"] == tier_name
        assert d["supply_chain"]["reason"] == f"test reason for tier {tier}"


# ==================== MODIFIERS FIELD TESTS ====================


def test_symbol_has_modifiers_field() -> None:
    """Symbol should have modifiers field for semantic attributes."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="java:Test.java:1-2:doWork:method",
        name="doWork",
        kind="method",
        language="java",
        path="Test.java",
        span=span,
        modifiers=["native", "public", "static"],
    )

    assert symbol.modifiers == ["native", "public", "static"]


def test_symbol_modifiers_defaults_to_empty_list() -> None:
    """Symbol modifiers should default to empty list."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
    )

    assert symbol.modifiers == []


def test_symbol_to_dict_includes_modifiers() -> None:
    """Symbol.to_dict should include modifiers field."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="java:Test.java:1-2:doWork:method",
        name="doWork",
        kind="method",
        language="java",
        path="Test.java",
        span=span,
        modifiers=["native", "public"],
    )
    d = symbol.to_dict()

    assert "modifiers" in d
    assert d["modifiers"] == ["native", "public"]


# ==================== USAGE CONTEXT TESTS ====================


def test_usage_context_create() -> None:
    """UsageContext.create should auto-generate ID."""
    span = Span(start_line=5, end_line=5, start_col=0, end_col=50)
    ctx = UsageContext.create(
        kind="call",
        context_name="path",
        position="args[1]",
        path="urls.py",
        span=span,
        symbol_ref="python:views.py:10-15:list_users:function",
        metadata={"args": ["/users/", "views.list_users"]},
    )

    assert ctx.id.startswith("usage:sha256:")
    assert ctx.kind == "call"
    assert ctx.context_name == "path"
    assert ctx.position == "args[1]"
    assert ctx.path == "urls.py"
    assert ctx.symbol_ref == "python:views.py:10-15:list_users:function"
    assert ctx.metadata == {"args": ["/users/", "views.list_users"]}


def test_usage_context_create_inline_handler() -> None:
    """UsageContext.create should allow None symbol_ref for inline handlers."""
    span = Span(start_line=10, end_line=12, start_col=0, end_col=30)
    ctx = UsageContext.create(
        kind="call",
        context_name="app.get",
        position="args[1]",
        path="server.js",
        span=span,
        symbol_ref=None,  # Inline lambda handler
        metadata={"args": ["/api", "<lambda>"]},
    )

    assert ctx.symbol_ref is None
    assert ctx.kind == "call"


def test_usage_context_create_defaults() -> None:
    """UsageContext.create should provide defaults for optional fields."""
    span = Span(start_line=1, end_line=1, start_col=0, end_col=20)
    ctx = UsageContext.create(
        kind="export",
        context_name="module.exports",
        position="default",
        path="index.js",
        span=span,
    )

    assert ctx.symbol_ref is None
    assert ctx.metadata == {}


def test_usage_context_to_dict() -> None:
    """UsageContext.to_dict should serialize all fields."""
    span = Span(start_line=5, end_line=5, start_col=0, end_col=50)
    ctx = UsageContext.create(
        kind="call",
        context_name="path",
        position="args[1]",
        path="urls.py",
        span=span,
        symbol_ref="python:views.py:10-15:list_users:function",
        metadata={"args": ["/users/", "views.list_users"]},
    )
    d = ctx.to_dict()

    assert "id" in d
    assert d["kind"] == "call"
    assert d["context_name"] == "path"
    assert d["symbol_ref"] == "python:views.py:10-15:list_users:function"
    assert d["position"] == "args[1]"
    assert d["metadata"] == {"args": ["/users/", "views.list_users"]}
    assert d["path"] == "urls.py"
    assert "span" in d
    assert d["span"]["start_line"] == 5


def test_usage_context_id_is_deterministic() -> None:
    """Same inputs should produce the same UsageContext ID."""
    span = Span(start_line=5, end_line=5, start_col=0, end_col=50)

    ctx1 = UsageContext.create(
        kind="call",
        context_name="path",
        position="args[1]",
        path="urls.py",
        span=span,
    )
    ctx2 = UsageContext.create(
        kind="call",
        context_name="path",
        position="args[1]",
        path="urls.py",
        span=span,
    )

    assert ctx1.id == ctx2.id


def test_usage_context_id_differs_for_different_inputs() -> None:
    """Different inputs should produce different UsageContext IDs."""
    span = Span(start_line=5, end_line=5, start_col=0, end_col=50)

    ctx1 = UsageContext.create(
        kind="call",
        context_name="path",
        position="args[1]",
        path="urls.py",
        span=span,
    )
    ctx2 = UsageContext.create(
        kind="call",
        context_name="re_path",  # Different context_name
        position="args[1]",
        path="urls.py",
        span=span,
    )

    assert ctx1.id != ctx2.id


def test_usage_context_all_kinds() -> None:
    """UsageContext should accept all valid kind values."""
    span = Span(start_line=1, end_line=1, start_col=0, end_col=10)

    for kind in ["call", "data_value", "export", "macro"]:
        ctx = UsageContext.create(
            kind=kind,  # type: ignore[arg-type]
            context_name="test",
            position="test",
            path="test.py",
            span=span,
        )
        assert ctx.kind == kind


# ==================== FROM_DICT TESTS ====================


def test_span_from_dict() -> None:
    """Span.from_dict should reconstruct Span from dict."""
    d = {"start_line": 10, "end_line": 20, "start_col": 5, "end_col": 50}
    span = Span.from_dict(d)

    assert span.start_line == 10
    assert span.end_line == 20
    assert span.start_col == 5
    assert span.end_col == 50


def test_span_from_dict_with_defaults() -> None:
    """Span.from_dict should use defaults for missing fields."""
    span = Span.from_dict({})

    assert span.start_line == 0
    assert span.end_line == 0
    assert span.start_col == 0
    assert span.end_col == 0


def test_symbol_from_dict() -> None:
    """Symbol.from_dict should reconstruct Symbol from dict."""
    d = {
        "id": "python:src/api.py:10-20:process_request:function",
        "name": "process_request",
        "kind": "function",
        "language": "python",
        "path": "src/api.py",
        "span": {"start_line": 10, "end_line": 20, "start_col": 0, "end_col": 30},
        "origin": "python-ast-v1",
        "origin_run_id": "uuid:12345",
        "origin_run_signature": "sha256:abcdef",
        "stable_id": "stable:123",
        "canonical_name": "api.process_request",
        "supply_chain": {"tier": 1, "reason": "first_party"},
        "cyclomatic_complexity": 5,
        "lines_of_code": 10,
        "signature": "(request: Request) -> Response",
        "modifiers": ["async", "public"],
    }

    symbol = Symbol.from_dict(d)

    assert symbol.id == "python:src/api.py:10-20:process_request:function"
    assert symbol.name == "process_request"
    assert symbol.kind == "function"
    assert symbol.language == "python"
    assert symbol.path == "src/api.py"
    assert symbol.span.start_line == 10
    assert symbol.span.end_line == 20
    assert symbol.origin == "python-ast-v1"
    assert symbol.supply_chain_tier == 1
    assert symbol.supply_chain_reason == "first_party"
    assert symbol.cyclomatic_complexity == 5
    assert symbol.lines_of_code == 10
    assert symbol.signature == "(request: Request) -> Response"
    assert symbol.modifiers == ["async", "public"]


def test_symbol_from_dict_with_defaults() -> None:
    """Symbol.from_dict should use defaults for optional fields."""
    d = {
        "id": "python:test.py:1-5:foo:function",
        "name": "foo",
        "kind": "function",
        "language": "python",
        "path": "test.py",
    }

    symbol = Symbol.from_dict(d)

    assert symbol.id == "python:test.py:1-5:foo:function"
    assert symbol.name == "foo"
    assert symbol.origin == ""
    assert symbol.supply_chain_tier == 1  # Default
    assert symbol.modifiers == []


def test_symbol_roundtrip_preserves_tier() -> None:
    """to_dict → from_dict must preserve non-default supply_chain_tier.

    Regression test: _node_from_dict (removed) read d.get("supply_chain_tier")
    but to_dict() nests it under d["supply_chain"]["tier"], so non-default
    tiers were silently lost during deserialization.
    """
    sym = Symbol(
        id="python:vendor/lib.py:1-10:function:parse",
        name="parse",
        kind="function",
        language="python",
        path="vendor/lib.py",
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        supply_chain_tier=3,
        supply_chain_reason="vendored",
    )
    d = sym.to_dict()
    restored = Symbol.from_dict(d)
    assert restored.supply_chain_tier == 3
    assert restored.supply_chain_reason == "vendored"


def test_symbol_is_test_file_default() -> None:
    """WI-rigun: Symbol.is_test_file defaults to False."""
    sym = Symbol(
        id="python:src/app.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="src/app.py",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
    )
    assert sym.is_test_file is False


def test_symbol_to_dict_includes_is_test_file() -> None:
    """WI-rigun: Symbol.to_dict embeds is_test_file under supply_chain."""
    sym = Symbol(
        id="go:pkg/handler_test.go:1-2:TestHandler:function",
        name="TestHandler",
        kind="function",
        language="go",
        path="pkg/handler_test.go",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        supply_chain_tier=2,
        supply_chain_reason="test file matches _test.go$",
        is_test_file=True,
    )
    d = sym.to_dict()
    assert d["supply_chain"]["is_test_file"] is True
    assert d["supply_chain"]["tier"] == 2


def test_symbol_roundtrip_preserves_is_test_file() -> None:
    """WI-rigun: to_dict → from_dict round-trips is_test_file."""
    sym = Symbol(
        id="go:pkg/handler_test.go:1-2:TestHandler:function",
        name="TestHandler",
        kind="function",
        language="go",
        path="pkg/handler_test.go",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        supply_chain_tier=2,
        supply_chain_reason="test file matches _test.go$",
        is_test_file=True,
    )
    restored = Symbol.from_dict(sym.to_dict())
    assert restored.is_test_file is True
    assert restored.supply_chain_tier == 2
    assert restored.supply_chain_reason == "test file matches _test.go$"


def test_symbol_from_dict_is_test_file_default_false() -> None:
    """WI-rigun: from_dict treats missing is_test_file as False (back-compat)."""
    d = {
        "id": "python:src/lib.py:1-2:foo:function",
        "name": "foo",
        "kind": "function",
        "language": "python",
        "path": "src/lib.py",
        "supply_chain": {"tier": 1, "reason": "first_party"},
    }
    restored = Symbol.from_dict(d)
    assert restored.is_test_file is False


def test_edge_from_dict() -> None:
    """Edge.from_dict should reconstruct Edge from dict."""
    d = {
        "id": "edge:sha256:abcdef123456",
        "edge_key": "edgekey:sha256:123456",
        "src": "python:a.py:1-5:caller:function",
        "dst": "python:b.py:10-15:callee:function",
        "type": "calls",
        "line": 3,
        "confidence": 0.95,
        "origin": "python-ast-v1",
        "origin_run_id": "uuid:12345",
        "origin_run_signature": "sha256:abcdef",
        "quality": {"score": 0.9, "reason": "direct call"},
        "meta": {
            "evidence_type": "ast_call_direct",
            "evidence_lang": "python",
        },
    }

    edge = Edge.from_dict(d)

    assert edge.id == "edge:sha256:abcdef123456"
    assert edge.edge_key == "edgekey:sha256:123456"
    assert edge.src == "python:a.py:1-5:caller:function"
    assert edge.dst == "python:b.py:10-15:callee:function"
    assert edge.edge_type == "calls"
    assert edge.line == 3
    assert edge.confidence == 0.95
    assert edge.origin == "python-ast-v1"
    assert edge.evidence_type == "ast_call_direct"
    assert edge.evidence_lang == "python"


def test_edge_from_dict_with_defaults() -> None:
    """Edge.from_dict should use defaults for optional fields."""
    d = {
        "src": "python:a.py:1-5:caller:function",
        "dst": "python:b.py:10-15:callee:function",
        "type": "calls",
        "line": 5,
    }

    edge = Edge.from_dict(d)

    assert edge.src == "python:a.py:1-5:caller:function"
    assert edge.dst == "python:b.py:10-15:callee:function"
    assert edge.edge_type == "calls"
    assert edge.line == 5
    assert edge.id == ""  # Default
    assert edge.confidence == 0.85  # Default
    assert edge.evidence_type == "ast_call_direct"  # Default


class TestCreateBoundaryNodes:
    """Tests for create_boundary_nodes (WI-sikur / INV-miniz)."""

    def _make_symbol(self, sym_id: str) -> Symbol:
        return Symbol(
            id=sym_id, name="x", kind="function", language="python",
            path="x.py", span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )

    def test_no_dangling_edges_returns_empty(self):
        """When all edges resolve, no boundary nodes are created."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        s2 = self._make_symbol("python:b.py:1-1:bar:function")
        e = Edge.create(src=s1.id, dst=s2.id, edge_type="calls", line=1)
        result = create_boundary_nodes([s1, s2], [e])
        assert result == []

    def test_dangling_dst_creates_boundary(self):
        """Edges pointing to nonexistent dst get boundary nodes."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="go:fmt:0-0:Errorf:unresolved",
            edge_type="calls", line=5,
        )
        result = create_boundary_nodes([s1], [e])
        assert len(result) == 1
        node = result[0]
        assert node.id == "go:fmt:0-0:Errorf:unresolved"
        assert node.kind == "external_symbol"
        assert node.language == "go"
        assert node.name == "Errorf"
        assert node.supply_chain_tier == 3
        assert node.meta["external_boundary"] is True

    def test_dangling_src_creates_boundary(self):
        """Edges with nonexistent src also get boundary nodes."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src="external:lib:0-0:helper:unresolved", dst=s1.id,
            edge_type="calls", line=1,
        )
        result = create_boundary_nodes([s1], [e])
        assert len(result) == 1
        assert result[0].id == "external:lib:0-0:helper:unresolved"

    def test_multiple_dangling_deduped(self):
        """Multiple edges to the same dangling target create only one node."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        s2 = self._make_symbol("python:b.py:1-1:bar:function")
        dangling_id = "go:fmt:0-0:Println:unresolved"
        e1 = Edge.create(src=s1.id, dst=dangling_id, edge_type="calls", line=1)
        e2 = Edge.create(src=s2.id, dst=dangling_id, edge_type="calls", line=2)
        result = create_boundary_nodes([s1, s2], [e1, e2])
        assert len(result) == 1
        assert result[0].id == dangling_id

    def test_boundary_node_path_is_external(self):
        """Boundary nodes have path '<external>'."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="lua:?:0-0:ngx.log:unresolved",
            edge_type="calls", line=1,
        )
        result = create_boundary_nodes([s1], [e])
        assert result[0].path == "<external>"

    def test_boundary_node_zero_span(self):
        """Boundary nodes have zero span."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="rust:std:0-0:println:unresolved",
            edge_type="calls", line=1,
        )
        result = create_boundary_nodes([s1], [e])
        assert result[0].span.start_line == 0
        assert result[0].span.end_line == 0

    def test_go_import_format_parsed(self):
        """Go import edges (go:{path}:0-0:package:package) are handled."""
        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/pkg/errors:0-0:package:package",
            edge_type="imports", line=1,
        )
        result = create_boundary_nodes([s1], [e])
        assert len(result) == 1
        assert result[0].language == "go"
        assert result[0].name == "package"

    def test_sorted_output_deterministic(self):
        """Boundary nodes are returned in sorted order for reproducibility."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e1 = Edge.create(src=s1.id, dst="z:z:0-0:z:unresolved", edge_type="calls", line=1)
        e2 = Edge.create(src=s1.id, dst="a:a:0-0:a:unresolved", edge_type="calls", line=2)
        result = create_boundary_nodes([s1], [e1, e2])
        assert len(result) == 2
        assert result[0].id < result[1].id

    def test_manifest_classifies_direct_dep_as_tier2(self):
        """Boundary nodes for direct deps get tier 2 when manifest provided."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/go-kit/log:0-0:package:package",
            edge_type="imports", line=3,
        )
        manifest = DependencyManifest(entries={
            "github.com/go-kit/log": {"direct": True},
        })
        result = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 2
        assert "direct dependency" in result[0].supply_chain_reason

    def test_manifest_classifies_indirect_dep_as_tier3(self):
        """Boundary nodes for indirect deps remain tier 3."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/beorn7/perp:0-0:package:package",
            edge_type="imports", line=3,
        )
        manifest = DependencyManifest(entries={
            "github.com/beorn7/perp": {"direct": False},
        })
        result = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 3

    def test_manifest_classifies_go_stdlib_as_tier3(self):
        """Go stdlib boundary nodes remain tier 3 with manifest."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:encoding/json:0-0:Marshal:unresolved",
            edge_type="calls", line=5,
        )
        manifest = DependencyManifest(entries={
            "github.com/foo/bar": {"direct": True},
        })
        result = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 3

    def test_no_manifest_backward_compat(self):
        """Without manifest, all boundary nodes get tier 3 (existing behavior)."""
        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/go-kit/log:0-0:package:package",
            edge_type="imports", line=3,
        )
        result = create_boundary_nodes([s1], [e])
        assert len(result) == 1
        assert result[0].supply_chain_tier == 3

    def test_manifest_subpackage_prefix_match(self):
        """Import of subpackage matches module path prefix in manifest."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id,
            dst="go:github.com/go-kit/log/level:0-0:package:package",
            edge_type="imports", line=3,
        )
        manifest = DependencyManifest(entries={
            "github.com/go-kit/log": {"direct": True},
        })
        result = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 2

    def test_manifest_non_go_language_unaffected(self):
        """Non-Go boundary nodes are not reclassified by manifest."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="python:requests:0-0:get:unresolved",
            edge_type="calls", line=5,
        )
        manifest = DependencyManifest(entries={
            "github.com/go-kit/log": {"direct": True},
        })
        result = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        # Non-Go: manifest doesn't apply, stays tier 3
        assert result[0].supply_chain_tier == 3
