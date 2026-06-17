# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the internal representation (IR) layer."""
from pathlib import Path

from hypergumbo_core.ir import (
    VALID_ACCESS_MODES,
    AnalysisRun, Edge, ExternalRef, Span, Symbol, UsageContext, create_boundary_nodes,
    _default_config_fingerprint, compute_config_fingerprint,
    format_legacy_dst, is_external_boundary, validate_symbol_id_format,
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
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    # run_signature should be deterministic based on pass+version+config
    assert hasattr(run, "run_signature")
    assert run.run_signature is not None
    assert run.run_signature.startswith("sha256:")


def test_analysis_run_has_toolchain() -> None:
    """AnalysisRun should have toolchain dict with runtime info."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    assert hasattr(run, "toolchain")
    assert isinstance(run.toolchain, dict)
    # For Python analyzer, should have python version
    assert "name" in run.toolchain
    assert "version" in run.toolchain


def test_analysis_run_has_config_fingerprint() -> None:
    """AnalysisRun should have config_fingerprint for cache invalidation."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    assert hasattr(run, "config_fingerprint")
    assert run.config_fingerprint is not None
    assert run.config_fingerprint.startswith("sha256:")


def test_analysis_run_direct_construction_gets_config_fingerprint() -> None:
    """INV-kotiz: direct AnalysisRun() must auto-default config_fingerprint."""
    run = AnalysisRun(
        execution_id="uuid:test",
        pass_id="thrift",
        version="0.1.0",
        files_analyzed=0,
        duration_ms=100,
    )
    assert run.config_fingerprint != "", (
        "Direct AnalysisRun() must not leave config_fingerprint empty"
    )
    assert run.config_fingerprint.startswith("sha256:")


def test_compute_config_fingerprint_deterministic_and_keysorted() -> None:
    """compute_config_fingerprint is the shared producer-identity primitive:
    sorted-keys deterministic, sha256:<16hex> shape, distinct for distinct
    inputs, and never equal to the empty-config default for a non-empty dict."""
    a = compute_config_fingerprint({"b": 1, "a": 2})
    b = compute_config_fingerprint({"a": 2, "b": 1})
    assert a == b  # key order does not matter
    assert a.startswith("sha256:") and len(a) == len("sha256:") + 16
    assert a != compute_config_fingerprint({"a": 2, "b": 3})
    assert a != _default_config_fingerprint()


def test_analysis_run_explicit_config_fingerprint_preserved() -> None:
    """INV-kotiz: explicit config_fingerprint must not be overwritten."""
    run = AnalysisRun(
        execution_id="uuid:test",
        pass_id="custom",
        version="0.1.0",
        config_fingerprint="sha256:custom123",
    )
    assert run.config_fingerprint == "sha256:custom123"


def test_analysis_run_has_repo_fingerprint() -> None:
    """AnalysisRun should have repo_fingerprint for cache keying."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    assert hasattr(run, "repo_fingerprint")
    # Can be None if not set, but field must exist


def test_analysis_run_has_skipped_passes() -> None:
    """AnalysisRun should have skipped_passes list."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    assert hasattr(run, "skipped_passes")
    assert isinstance(run.skipped_passes, list)


def test_analysis_run_has_warnings() -> None:
    """AnalysisRun should have warnings list."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    assert hasattr(run, "warnings")
    assert isinstance(run.warnings, list)


def test_analysis_run_has_failed_files() -> None:
    """AnalysisRun should have a failed_files list (INV-buhur)."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    assert hasattr(run, "failed_files")
    assert isinstance(run.failed_files, list)
    assert run.failed_files == []


def test_analysis_run_record_failed_file_appends_entry() -> None:
    """record_failed_file appends a {path, reason} dict to failed_files (INV-buhur)."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")

    run.record_failed_file("broken.py", "SyntaxError: invalid syntax (line 42)")

    assert len(run.failed_files) == 1
    assert run.failed_files[0] == {
        "path": "broken.py",
        "reason": "SyntaxError: invalid syntax (line 42)",
    }


def test_analysis_run_to_dict_includes_failed_files() -> None:
    """AnalysisRun.to_dict should include failed_files in serialized output."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")
    run.record_failed_file("a.py", "SyntaxError")
    run.record_failed_file("b.py", "UnicodeDecodeError")

    d = run.to_dict()

    assert "failed_files" in d
    assert len(d["failed_files"]) == 2
    assert d["failed_files"][0]["path"] == "a.py"
    assert d["failed_files"][1]["path"] == "b.py"


def test_analysis_run_to_dict_includes_new_fields() -> None:
    """AnalysisRun.to_dict should include all spec fields."""
    run = AnalysisRun.create(pass_id="python", version="0.5.0")
    d = run.to_dict()

    assert "run_signature" in d
    assert "toolchain" in d
    assert "config_fingerprint" in d
    assert "repo_fingerprint" in d
    assert "skipped_passes" in d
    assert "warnings" in d


def test_symbol_has_qualified_name() -> None:
    """Symbol should have qualified_name field for fully qualified name (ADR-0032)."""
    span = Span(start_line=1, end_line=2, start_col=0, end_col=10)
    symbol = Symbol(
        id="python:test.py:1-2:greet:function",
        name="greet",
        kind="function",
        language="python",
        path="test.py",
        span=span,
        qualified_name="mymodule.greet",
    )

    assert symbol.qualified_name == "mymodule.greet"


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
        qualified_name="mymodule.greet",
        fingerprint="sha256:abc123",
        quality={"score": 0.95, "reason": "AST-based definition"},
    )
    d = symbol.to_dict()

    assert "qualified_name" in d
    assert "fingerprint" in d
    assert "quality" in d


def test_edge_has_edge_key() -> None:
    """Edge should have edge_key for canonical identity."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
    )
    edge2 = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=20,

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
    )
    edge2 = Edge.create(
        src="ruby:a.rb:1-2:Foo#bar:method",
        dst="ruby:b.rb:3-4:Baz#qux:method",
        edge_type="calls",
        line=20,

        origin="test", origin_run_id="test",
    )
    edge3 = Edge.create(
        src="ruby:a.rb:1-2:Foo#bar:method",
        dst="ruby:c.rb:5-6:Other#func:method",
        edge_type="calls",
        line=15,

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
    )
    self_loop = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:a.py:1-2:foo:function",
        edge_type="calls",
        line=20,

        origin="test", origin_run_id="test",
    )

    result = deduplicate_edges([normal, self_loop], remove_self_loops=True)
    assert len(result) == 1
    assert result[0].id == normal.id


def test_deduplicate_edges_handles_none_edge_key() -> None:
    """Edges with edge_key=None must not collapse into a single edge.

    Regression test: Edge( origin="test", origin_run_id="test") constructor (not Edge.create( origin="test", origin_run_id="test")) defaults
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

        origin="test", origin_run_id="test",
    )
    edge_b = Edge(
        id="edge:route2->handler2",
        src="go:server.go:11-11:POST /users:route",
        dst="go:server.go:40-50:createUser:method",
        edge_type="routes_to",
        line=11,

        origin="test", origin_run_id="test",
    )
    edge_c = Edge(
        id="edge:dockerfile->stage",
        src="docker:Dockerfile:1-1:stage1:stage",
        dst="docker:Dockerfile:10-10:stage2:stage",
        edge_type="depends_on",
        line=1,

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
    )
    edge_a_dup = Edge(
        id="edge:route1->handler1:line15",
        src="go:server.go:10-10:GET /users:route",
        dst="go:server.go:20-30:listUsers:method",
        edge_type="routes_to",
        line=15,

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
    )
    imports_edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="imports",
        line=10,

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
    )
    edge.quality = {"score": 0.85, "reason": "Direct AST call"}

    assert edge.quality["score"] == 0.85
    assert edge.quality["reason"] == "Direct AST call"


def test_edge_quality_auto_derived_high_confidence() -> None:
    """WI-lonoz / Phase 6 PR2: Edge.quality is auto-derived at construction
    when the producer doesn't set it. confidence >= 0.95 gets the
    ``high_confidence_direct`` reason tag."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        origin="test", origin_run_id="test",
        confidence=0.97,
    )
    assert edge.quality == {"score": 0.97, "reason": "high_confidence_direct"}


def test_edge_quality_auto_derived_resolved_call_site() -> None:
    """Confidence in [0.8, 0.95) with is_resolved=True gets
    ``resolved_call_site``."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        origin="test", origin_run_id="test",
        confidence=0.85,
        is_resolved=True,
    )
    assert edge.quality["reason"] == "resolved_call_site"


def test_edge_quality_auto_derived_low_confidence() -> None:
    """Confidence < 0.5 gets ``low_confidence_fallback``."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        origin="test", origin_run_id="test",
        confidence=0.3,
    )
    assert edge.quality["reason"] == "low_confidence_fallback"


def test_edge_quality_auto_derived_derived_from() -> None:
    """Mid-confidence with derived_from populated gets
    ``derived_from_linker_evidence``."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        origin="test", origin_run_id="test",
        confidence=0.7,
        derived_from=["python:c.py:1-1:src:function"],
    )
    assert edge.quality["reason"] == "derived_from_linker_evidence"


def test_edge_quality_auto_derived_medium_confidence() -> None:
    """Mid-confidence (~0.7), unresolved, no derived_from — gets
    ``medium_confidence``."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        origin="test", origin_run_id="test",
        confidence=0.7,
        is_resolved=False,
    )
    assert edge.quality["reason"] == "medium_confidence"


def test_edge_quality_producer_set_wins(tmp_path: Path) -> None:
    """When the producer pre-populates ``quality``, the auto-derivation
    does NOT overwrite it. The producer's value wins."""
    from hypergumbo_core.ir import Edge as _Edge
    edge = _Edge(
        id="edge:1", src="a", dst="b", edge_type="calls", line=1,
        edge_key="edgekey:sha256:0123",
        confidence=0.85,
        origin=["test"],
        origin_run_id="test",
        evidence_type="ast_call_direct",
        quality={"score": 0.99, "reason": "producer-set"},
    )
    assert edge.quality == {"score": 0.99, "reason": "producer-set"}


def test_edge_has_evidence_lang() -> None:
    """Edge should have evidence_lang in meta."""
    edge = Edge.create(
        src="python:a.py:1-2:foo:function",
        dst="python:b.py:3-4:bar:function",
        edge_type="calls",
        line=5,
        evidence_lang="python",

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

            origin="test", origin_run_id="test",
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

            origin="test", origin_run_id="test",
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

        origin="test", origin_run_id="test",
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

        origin_run_id="test",
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
        "origin": "python",
        "origin_run_id": "uuid:12345",
        "stable_id": "stable:123",
        "qualified_name": "api.process_request",
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
    assert symbol.origin == ["python"]
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
    assert symbol.origin == []
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


def test_symbol_is_exported_default() -> None:
    """WI-zimum: Symbol.is_exported defaults to False."""
    sym = Symbol(
        id="python:src/lib.py:1-2:helper:function",
        name="helper",
        kind="function",
        language="python",
        path="src/lib.py",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
    )
    assert sym.is_exported is False


def test_symbol_to_dict_includes_is_exported() -> None:
    """WI-zimum: Symbol.to_dict embeds is_exported under supply_chain."""
    sym = Symbol(
        id="go:pkg/api.go:1-2:PublicFn:function",
        name="PublicFn",
        kind="function",
        language="go",
        path="pkg/api.go",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        is_exported=True,
    )
    d = sym.to_dict()
    assert d["supply_chain"]["is_exported"] is True


def test_symbol_roundtrip_preserves_is_exported() -> None:
    """WI-zimum: to_dict → from_dict round-trips is_exported."""
    sym = Symbol(
        id="rust:src/lib.rs:1-2:public_fn:function",
        name="public_fn",
        kind="function",
        language="rust",
        path="src/lib.rs",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=0),
        is_exported=True,
    )
    restored = Symbol.from_dict(sym.to_dict())
    assert restored.is_exported is True


def test_symbol_from_dict_is_exported_default_false() -> None:
    """WI-zimum: from_dict treats missing is_exported as False (back-compat)."""
    d = {
        "id": "python:src/lib.py:1-2:foo:function",
        "name": "foo",
        "kind": "function",
        "language": "python",
        "path": "src/lib.py",
        "supply_chain": {"tier": 1, "reason": "first_party"},
    }
    restored = Symbol.from_dict(d)
    assert restored.is_exported is False


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
        "origin": "python",
        "origin_run_id": "uuid:12345",
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
    assert edge.origin == ["python"]
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
        e = Edge.create(src=s1.id, dst=s2.id, edge_type="calls", line=1, origin="test", origin_run_id="test")
        result, remap = create_boundary_nodes([s1, s2], [e])
        assert result == []
        assert remap == {}

    def test_dangling_dst_creates_boundary(self):
        """Edges pointing to nonexistent dst get boundary nodes.

        WI-fozoh: non-file-kind dangling ids preserve their full path
        slot in the canonical id (so semantically distinct externals
        like ``urllib.request.urlopen`` vs ``urllib.parse.urlopen`` stay
        separate boundary nodes).
        """
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="go:fmt:0-0:Errorf:unresolved",
            edge_type="calls", line=5,

            origin="test", origin_run_id="test",
        )
        result, remap = create_boundary_nodes([s1], [e])
        assert len(result) == 1
        node = result[0]
        # Non-file kind: canonical id == original (no rewrite needed).
        assert node.id == "go:fmt:0-0:Errorf:unresolved"
        assert node.kind == "external_symbol"
        assert node.language == "go"
        assert node.name == "Errorf"
        assert node.supply_chain_tier == 3
        assert node.meta["external_boundary"] is True
        # Stable identity is populated for cross-run grouping (WI-fozoh).
        assert node.stable_id is not None
        assert node.display_label == "go:fmt:Errorf:unresolved"
        # Canonical id == original id, so remap is empty (no rewrite needed).
        assert remap == {}

    def test_stamps_origin_and_origin_run_id(self):
        """synthetic:F1: boundary external_symbol nodes carry a non-empty
        ``origin`` (the ``boundary_external_symbol_synthesis`` mechanism) and the
        provided ``origin_run_id``, so the node->AnalysisRun JOIN resolves.
        Previously both were empty (origin=[], origin_run_id='')."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="go:fmt:0-0:Errorf:unresolved",
            edge_type="calls", line=5, origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes(
            [s1], [e], origin_run_id="uuid:boundary-run",
        )
        assert len(result) == 1
        node = result[0]
        assert node.kind == "external_symbol"
        assert node.origin == ["boundary_external_symbol_synthesis"]
        assert node.origin_run_id == "uuid:boundary-run"

    def test_origin_run_id_defaults_empty_but_origin_is_stamped(self):
        """``origin_run_id`` defaults to '' (legacy call form), but ``origin`` is
        always the synthesis mechanism so node-side provenance is never empty."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="go:fmt:0-0:Errorf:unresolved",
            edge_type="calls", line=5, origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes([s1], [e])
        assert result[0].origin == ["boundary_external_symbol_synthesis"]
        assert result[0].origin_run_id == ""

    def test_boundary_synthesis_mechanism_is_a_known_pass_id(self):
        """The boundary synthesis mechanism must be a registered pass-id so both
        ``Symbol.origin`` and the synthetic ``AnalysisRun.pass_id`` pass the
        axis-conformance check (``all_known_pass_ids``)."""
        from hypergumbo_core.catalog import all_known_pass_ids

        assert "boundary_external_symbol_synthesis" in all_known_pass_ids()

    def test_dangling_src_creates_boundary(self):
        """Edges with nonexistent src also get boundary nodes."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src="external:lib:0-0:helper:unresolved", dst=s1.id,
            edge_type="calls", line=1,

            origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes([s1], [e])
        assert len(result) == 1
        # Non-file kind: canonical id == original.
        assert result[0].id == "external:lib:0-0:helper:unresolved"

    def test_multiple_dangling_deduped(self):
        """Multiple edges to the same dangling target create only one node."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        s2 = self._make_symbol("python:b.py:1-1:bar:function")
        dangling_id = "go:fmt:0-0:Println:unresolved"
        e1 = Edge.create(src=s1.id, dst=dangling_id, edge_type="calls", line=1, origin="test", origin_run_id="test")
        e2 = Edge.create(src=s2.id, dst=dangling_id, edge_type="calls", line=2, origin="test", origin_run_id="test")
        result, _ = create_boundary_nodes([s1, s2], [e1, e2])
        assert len(result) == 1
        assert result[0].id == dangling_id

    def test_distinct_modules_with_same_name_stay_distinct(self):
        """WI-fozoh: ``urllib.request.urlopen`` and ``urllib.parse.urlopen``
        are different functions in Python; they must NOT collapse to a
        single boundary just because their ``name`` and ``kind`` match.
        Non-file kinds keep the path slot in their dedupe key.
        """
        s1 = self._make_symbol("python:a.py:1-1:f:function")
        e1 = Edge.create(
            src=s1.id, dst="python:urllib.request:0-0:urlopen:unresolved",
            edge_type="calls", line=1,

            origin="test", origin_run_id="test",
        )
        e2 = Edge.create(
            src=s1.id, dst="python:urllib.parse:0-0:urlopen:unresolved",
            edge_type="calls", line=2,

            origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes([s1], [e1, e2])
        assert len(result) == 2
        ids = {n.id for n in result}
        assert "python:urllib.request:0-0:urlopen:unresolved" in ids
        assert "python:urllib.parse:0-0:urlopen:unresolved" in ids

    def test_boundary_node_path_is_external(self):
        """Boundary nodes have path '<external>'."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="lua:?:0-0:ngx.log:unresolved",
            edge_type="calls", line=1,

            origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes([s1], [e])
        assert result[0].path == "<external>"

    def test_boundary_node_zero_span(self):
        """Boundary nodes have zero span."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="rust:std:0-0:println:unresolved",
            edge_type="calls", line=1,

            origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes([s1], [e])
        assert result[0].span.start_line == 0
        assert result[0].span.end_line == 0

    def test_go_import_format_parsed(self):
        """Go import edges (go:{path}:0-0:package:package) are handled."""
        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/pkg/errors:0-0:package:package",
            edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes([s1], [e])
        assert len(result) == 1
        assert result[0].language == "go"
        assert result[0].name == "package"

    def test_sorted_output_deterministic(self):
        """Boundary nodes are returned in sorted order for reproducibility."""
        s1 = self._make_symbol("python:a.py:1-1:foo:function")
        e1 = Edge.create(src=s1.id, dst="z:z:0-0:z:unresolved", edge_type="calls", line=1, origin="test", origin_run_id="test")
        e2 = Edge.create(src=s1.id, dst="a:a:0-0:a:unresolved", edge_type="calls", line=2, origin="test", origin_run_id="test")
        result, _ = create_boundary_nodes([s1], [e1, e2])
        assert len(result) == 2
        assert result[0].id < result[1].id

    def test_file_kind_collapses_per_language(self):
        """WI-fozoh: ``kind="file"`` pseudo-IDs collapse per language.

        On hypergumbo self-analysis, every Python file's import-edge src
        is a ``make_file_id``-style id with a different per-reference
        filesystem path but the same ``name="file"``, ``kind="file"``.
        These all collapse to one canonical "file" boundary per language.
        """
        # Two real Python file pseudo-IDs (the symptom: 732 of these on
        # hypergumbo self-analysis).
        e1 = Edge.create(
            src="python:packages/foo/A.py:1-1:file:file",
            dst="python:click:0-0:click:unresolved",
            edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )
        e2 = Edge.create(
            src="python:packages/bar/B.py:1-1:file:file",
            dst="python:click:0-0:click:unresolved",
            edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )
        result, remap = create_boundary_nodes([], [e1, e2])
        # Two boundary nodes: 1 collapsed "file" boundary covering both
        # source files, plus 1 click boundary from the shared dst.
        ids = {n.id for n in result}
        canonical_file = "python:<external>:0-0:file:file"
        assert canonical_file in ids
        assert "python:click:0-0:click:unresolved" in ids
        # Both distinct file-id srcs remap to the canonical "file" id.
        assert remap["python:packages/foo/A.py:1-1:file:file"] == canonical_file
        assert remap["python:packages/bar/B.py:1-1:file:file"] == canonical_file
        # The click dst is non-file kind: canonical id == original, no remap.
        assert "python:click:0-0:click:unresolved" not in remap

    def test_tier_min_selection_picks_tier2_when_any_member_matches(self):
        """WI-fozoh: tier-min selection — if any member of the collapsed
        group classifies as tier-2, the canonical node is tier-2.

        Two file pseudo-IDs collapse, with different per-reference path
        slots. The manifest classifies neither file path (because they
        ARE filesystem paths, not module names), but for the file-id
        collapse case the path slot is uninformative anyway. This test
        therefore exercises a scenario where the collapsed group
        receives a path slot that DOES match the manifest — uses Go
        package-style ids where the path slot is the import path.
        """
        from hypergumbo_core.supply_chain import DependencyManifest

        # Two go package boundary ids with the SAME path (same external).
        # They have full identity preservation (kind="package"); manifest
        # match works on the path slot. No collapse here, just stable_id
        # identity verification.
        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/go-kit/log:0-0:package:package",
            edge_type="imports", line=3,

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "github.com/go-kit/log": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 2
        # stable_id is populated regardless of manifest match (WI-fozoh).
        assert result[0].stable_id is not None
        assert result[0].display_label is not None

    def test_manifest_classifies_direct_dep_as_tier2(self):
        """Boundary nodes for direct deps get tier 2 when manifest provided."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/go-kit/log:0-0:package:package",
            edge_type="imports", line=3,

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "github.com/go-kit/log": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
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

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "github.com/beorn7/perp": {"direct": False},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 3

    def test_manifest_classifies_go_stdlib_as_tier3(self):
        """Go stdlib boundary nodes remain tier 3 with manifest."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:encoding/json:0-0:Marshal:unresolved",
            edge_type="calls", line=5,

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "github.com/foo/bar": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 3

    def test_no_manifest_backward_compat(self):
        """Without manifest, all boundary nodes get tier 3 (existing behavior)."""
        s1 = self._make_symbol("go:main.go:1-1:main:function")
        e = Edge.create(
            src=s1.id, dst="go:github.com/go-kit/log:0-0:package:package",
            edge_type="imports", line=3,

            origin="test", origin_run_id="test",
        )
        result, _ = create_boundary_nodes([s1], [e])
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

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "github.com/go-kit/log": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 2

    def test_manifest_non_go_language_unaffected(self):
        """Languages without manifest support stay tier 3 (the lua language
        has no manifest parser; even if a passed manifest happened to
        match by string prefix, classification should fall through).
        """
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("lua:a.lua:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="lua:redis:0-0:get:unresolved",
            edge_type="calls", line=5,

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "github.com/go-kit/log": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        # lua not in the allow-list: manifest doesn't apply, stays tier 3
        assert result[0].supply_chain_tier == 3

    def test_manifest_classifies_java_direct_dep_as_tier2(self):
        """Java boundary nodes classified as tier 2 with manifest."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("java:App.java:1-1:main:function")
        e = Edge.create(
            src=s1.id,
            dst="java:com.fasterxml.jackson.core.JsonParser:0-0:parse:unresolved",
            edge_type="calls", line=5,

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "com.fasterxml.jackson.core": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 2
        assert "direct dependency" in result[0].supply_chain_reason

    def test_manifest_classifies_kotlin_direct_dep_as_tier2(self):
        """Kotlin boundary nodes classified as tier 2 with manifest."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("kotlin:App.kt:1-1:main:function")
        e = Edge.create(
            src=s1.id,
            dst="kotlin:io.ktor.server.core:0-0:embeddedServer:unresolved",
            edge_type="calls", line=5,

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "io.ktor": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 2

    def test_manifest_java_unknown_import_stays_tier3(self):
        """Java import not in manifest stays tier 3."""
        from hypergumbo_core.supply_chain import DependencyManifest

        s1 = self._make_symbol("java:App.java:1-1:main:function")
        e = Edge.create(
            src=s1.id,
            dst="java:com.unknown.lib.Foo:0-0:bar:unresolved",
            edge_type="calls", line=5,

            origin="test", origin_run_id="test",
        )
        manifest = DependencyManifest(entries={
            "com.fasterxml.jackson.core": {"direct": True},
        })
        result, _ = create_boundary_nodes([s1], [e], dependency_manifest=manifest)
        assert len(result) == 1
        assert result[0].supply_chain_tier == 3


class TestApplyExternalIdRemap:
    """Tests for apply_external_id_remap (WI-fozoh).

    The remap returned by ``create_boundary_nodes`` collapses N
    per-reference dangling ids into one canonical boundary id. Edges
    pointing at the original ids must be rewritten to the canonical id,
    deduped on collision, and have their original src path slots
    captured into ``meta.referring_paths`` so per-file attribution
    survives src-side dedupe.
    """

    def _make_real(self, sym_id: str) -> Symbol:
        return Symbol(
            id=sym_id, name="x", kind="function", language="python",
            path="x.py", span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )

    def test_empty_remap_returns_input_unchanged(self):
        from hypergumbo_core.ir import apply_external_id_remap

        s1 = self._make_real("python:a.py:1-1:foo:function")
        e = Edge.create(src=s1.id, dst="other", edge_type="calls", line=1, origin="test", origin_run_id="test")
        result = apply_external_id_remap([e], {})
        assert result == [e]

    def test_dst_remap_rewrites_dst(self):
        from hypergumbo_core.ir import apply_external_id_remap

        s1 = self._make_real("python:a.py:1-1:foo:function")
        e = Edge.create(
            src=s1.id, dst="python:click:0-0:click:unresolved",
            edge_type="calls", line=5,

            origin="test", origin_run_id="test",
        )
        canonical = "python:<external>:0-0:click:unresolved"
        result = apply_external_id_remap(
            [e], {"python:click:0-0:click:unresolved": canonical},
        )
        assert len(result) == 1
        assert result[0].dst == canonical

    def test_src_remap_captures_referring_path(self):
        """When src is collapsed, the original src's path slot lands in
        ``meta.referring_paths`` on the surviving edge."""
        from hypergumbo_core.ir import apply_external_id_remap

        e = Edge.create(
            src="python:packages/foo/A.py:1-1:file:file",
            dst="python:click:0-0:click:unresolved",
            edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )
        canonical_file = "python:<external>:0-0:file:file"
        canonical_click = "python:<external>:0-0:click:unresolved"
        remap = {
            "python:packages/foo/A.py:1-1:file:file": canonical_file,
            "python:click:0-0:click:unresolved": canonical_click,
        }
        result = apply_external_id_remap([e], remap)
        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("referring_paths") == ["packages/foo/A.py"]

    def test_collision_dedupes_and_unions_paths(self):
        """Two edges with the same canonical (src, dst, edge_type) collapse
        to one; their original src paths union into referring_paths."""
        from hypergumbo_core.ir import apply_external_id_remap

        e1 = Edge.create(
            src="python:packages/foo/A.py:1-1:file:file",
            dst="python:click:0-0:click:unresolved",
            edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )
        e2 = Edge.create(
            src="python:packages/bar/B.py:1-1:file:file",
            dst="python:click:0-0:click:unresolved",
            edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )
        canonical_file = "python:<external>:0-0:file:file"
        canonical_click = "python:<external>:0-0:click:unresolved"
        remap = {
            "python:packages/foo/A.py:1-1:file:file": canonical_file,
            "python:packages/bar/B.py:1-1:file:file": canonical_file,
            "python:click:0-0:click:unresolved": canonical_click,
        }
        result = apply_external_id_remap([e1, e2], remap)
        # Only one edge survives — they collapsed.
        assert len(result) == 1
        paths = result[0].meta.get("referring_paths")
        assert set(paths) == {"packages/foo/A.py", "packages/bar/B.py"}

    def test_collision_caps_referring_paths_at_50(self):
        """No more than _REFERRING_PATHS_CAP entries are kept on collisions."""
        from hypergumbo_core.ir import _REFERRING_PATHS_CAP, apply_external_id_remap

        canonical_file = "python:<external>:0-0:file:file"
        canonical_click = "python:<external>:0-0:click:unresolved"
        remap = {"python:click:0-0:click:unresolved": canonical_click}
        edges: list[Edge] = []
        # Generate 75 distinct file srcs all importing click — should cap at 50.
        for i in range(75):
            src_id = f"python:packages/p{i}.py:1-1:file:file"
            remap[src_id] = canonical_file
            edges.append(Edge.create(
                src=src_id,
                dst="python:click:0-0:click:unresolved",
                edge_type="imports", line=1,

                origin="test", origin_run_id="test",
            ))
        result = apply_external_id_remap(edges, remap)
        assert len(result) == 1
        paths = result[0].meta.get("referring_paths")
        assert len(paths) == _REFERRING_PATHS_CAP

    def test_collision_dedupes_repeated_paths(self):
        """Two edges from the SAME origin file (e.g. two `import` lines in
        one module) shouldn't double-list their src path."""
        from hypergumbo_core.ir import apply_external_id_remap

        e1 = Edge.create(
            src="python:packages/foo/A.py:1-1:file:file",
            dst="python:click:0-0:click:unresolved",
            edge_type="imports", line=1,

            origin="test", origin_run_id="test",
        )
        e2 = Edge.create(
            src="python:packages/foo/A.py:1-1:file:file",
            dst="python:click:0-0:click:unresolved",
            edge_type="imports", line=2,

            origin="test", origin_run_id="test",
        )
        canonical_file = "python:<external>:0-0:file:file"
        canonical_click = "python:<external>:0-0:click:unresolved"
        remap = {
            "python:packages/foo/A.py:1-1:file:file": canonical_file,
            "python:click:0-0:click:unresolved": canonical_click,
        }
        result = apply_external_id_remap([e1, e2], remap)
        assert len(result) == 1
        paths = result[0].meta.get("referring_paths")
        assert paths == ["packages/foo/A.py"]


class TestIsExternalBoundary:
    """Tests for is_external_boundary helper (PR1 of stop-stripping plan)."""

    def _real_symbol(self) -> Symbol:
        return Symbol(
            id="python:foo.py:1-1:foo:function",
            name="foo", kind="function", language="python",
            path="foo.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )

    def _boundary_symbol(self) -> Symbol:
        return Symbol(
            id="python:urllib.request:0-0:urlopen:unresolved",
            name="urlopen", kind="external_symbol", language="python",
            path="<external>",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            meta={"external_boundary": True},
            supply_chain_tier=3,
        )

    def test_returns_true_for_boundary_symbol(self) -> None:
        assert is_external_boundary(self._boundary_symbol()) is True

    def test_returns_false_for_real_symbol(self) -> None:
        assert is_external_boundary(self._real_symbol()) is False

    def test_returns_true_for_boundary_dict(self) -> None:
        # Dict shape — Symbol.to_dict() roundtrip via behavior_map JSON.
        d = self._boundary_symbol().to_dict()
        assert is_external_boundary(d) is True

    def test_returns_false_for_real_dict(self) -> None:
        d = self._real_symbol().to_dict()
        assert is_external_boundary(d) is False

    def test_returns_false_when_meta_missing(self) -> None:
        # Symbol with no meta dict at all.
        s = Symbol(
            id="python:x.py:1-1:x:function",
            name="x", kind="function", language="python",
            path="x.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            meta=None,
        )
        assert is_external_boundary(s) is False

    def test_returns_false_for_dict_without_meta(self) -> None:
        # Dict with no meta key.
        assert is_external_boundary({"id": "x", "kind": "function"}) is False

    def test_returns_false_when_meta_external_boundary_falsy(self) -> None:
        # Defensive: explicit False or missing key both mean "not boundary".
        s = self._real_symbol()
        s.meta = {"external_boundary": False}
        assert is_external_boundary(s) is False
        s.meta = {"other_key": True}
        assert is_external_boundary(s) is False


# ==================== VALIDATE_SYMBOL_ID_FORMAT TESTS ====================
# WI-davan: enforce the dual-shape spec from docs/hypergumbo-spec.md §6.


def test_validate_accepts_file_path_shape_with_real_span() -> None:
    assert validate_symbol_id_format(
        "python:src/app.py:10-25:get_users:function"
    ) is None


def test_validate_accepts_file_path_shape_with_whole_file_sentinel() -> None:
    # `1-1:file:file` is the canonical whole-file pseudo-id.
    assert validate_symbol_id_format(
        "python:packages/hypergumbo-core/src/hypergumbo_core/cli.py:1-1:file:file"
    ) is None


def test_validate_accepts_file_path_shape_with_hyphens_in_path() -> None:
    # Hyphens in directory names (`hypergumbo-core/`) are real on-disk
    # path segments, not module identifiers — the file-path shape must
    # preserve them.
    assert validate_symbol_id_format(
        "python:packages/hypergumbo-core/src/hypergumbo_core/foo.py:5-12:bar:function"
    ) is None


def test_validate_accepts_module_hint_shape() -> None:
    assert validate_symbol_id_format(
        "python:hypergumbo_core.taxonomy:0-0:LANGUAGE_ALIASES:symbol"
    ) is None


def test_validate_accepts_module_hint_with_sentinel_slot2() -> None:
    # `external` and `unresolved` are sentinel slot-2 values used as
    # fallbacks when no module hint is recoverable. They are not the
    # WI-davan bug.
    assert validate_symbol_id_format(
        "python:external:0-0:requests:unresolved"
    ) is None
    assert validate_symbol_id_format(
        "python:unresolved:0-0:foo.bar:unresolved"
    ) is None


def test_validate_accepts_dart_io_with_colon_in_slot2() -> None:
    # Slot 2 may contain colons (e.g. `dart:io`); the parse uses the
    # trailing span/name/kind triple as the boundary.
    assert validate_symbol_id_format(
        "dart:dart:io:0-0:module:module"
    ) is None


def test_validate_rejects_packages_segment_in_module_hint() -> None:
    # WI-davan canonical: `packages.<pkg>.src.<mod>` shape leaking
    # into a module-hint slot.
    err = validate_symbol_id_format(
        "python:packages.hypergumbo-core.src.hypergumbo_core.taxonomy:0-0:LANGUAGE_ALIASES:symbol"
    )
    assert err is not None
    assert "packages." in err
    assert "WI-davan" in err


def test_validate_rejects_src_dot_segment_in_module_hint() -> None:
    err = validate_symbol_id_format(
        "python:foo.src.bar:0-0:Quux:symbol"
    )
    assert err is not None
    assert ".src." in err


def test_validate_rejects_python_module_hint_with_hyphen() -> None:
    # Python identifiers cannot contain hyphens. A 0-0-span Python id
    # whose slot 2 has a hyphen is the path-stringified-as-module bug.
    err = validate_symbol_id_format(
        "python:my-package.foo:0-0:Bar:symbol"
    )
    assert err is not None
    assert "hyphen" in err


def test_validate_does_not_constrain_non_python_module_hint_hyphens() -> None:
    # Other languages may legitimately have hyphens in module hints
    # (e.g. npm package names). Only Python identifier rules are enforced.
    assert validate_symbol_id_format(
        "javascript:my-package:0-0:doThing:export"
    ) is None


def test_validate_passes_through_short_ids() -> None:
    # Ids with fewer than 5 colon-separated parts are out of scope —
    # this validator is narrowly the WI-davan bug class.
    assert validate_symbol_id_format("garbage") is None
    assert validate_symbol_id_format("a:b:c:d") is None


def test_validate_passes_through_file_path_shape_with_packages_dot_substring() -> None:
    # In the file-path shape, slot 2 is a literal path; the substring
    # 'packages.' might incidentally appear (e.g. inside a directory
    # name). The validator does not check file-path-shape ids at all.
    # This test documents that contract.
    assert validate_symbol_id_format(
        "python:weird/packages.like.dir/foo.py:1-2:f:function"
    ) is None


def test_python_analyzer_emits_only_well_formed_ids_on_monorepo(
    tmp_path: Path,
) -> None:
    """Property test: ``analyze_python`` on a synthetic
    ``packages/<pkg>/src/<mod>/`` monorepo emits no symbols or edges
    whose IDs violate :func:`validate_symbol_id_format`.

    This is the structural enforcement the lab notebook proposed for
    WI-davan — replacing tag-borne validation with an in-CI invariant
    check. Catches future regressions in ``_detect_source_roots``,
    ``_module_name_from_path``, or any analyzer that derives a
    module-hint qualifier from a file path under a monorepo layout.
    """
    pkg_a = tmp_path / "packages" / "pkg-a" / "src" / "pkg_a"
    pkg_a.mkdir(parents=True)
    (pkg_a / "__init__.py").write_text("")
    (pkg_a / "constants.py").write_text("X = 1\nY = 2\n")
    (pkg_a / "core.py").write_text("def helper():\n    return 1\n")

    pkg_b = tmp_path / "packages" / "pkg-b" / "src" / "pkg_b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "consumer.py").write_text(
        "from pkg_a.constants import X\n"
        "from pkg_a.core import helper\n"
        "def use():\n    return X + helper()\n"
    )

    result = analyze_python(tmp_path)

    violations = []
    for symbol in result.symbols:
        err = validate_symbol_id_format(symbol.id)
        if err is not None:
            violations.append(f"symbol: {err}")
    for edge in result.edges:
        for endpoint_role, endpoint_id in (("src", edge.src), ("dst", edge.dst)):
            err = validate_symbol_id_format(endpoint_id)
            if err is not None:
                violations.append(f"edge.{endpoint_role}: {err}")

    assert not violations, (
        f"analyze_python emitted {len(violations)} ill-formed IDs on a "
        f"packages/<pkg>/src/<mod>/ monorepo (WI-davan regression). First "
        f"few:\n  " + "\n  ".join(violations[:5])
    )


# ---------------------------------------------------------------------------
# WI-tihup: ExternalRef + Edge.dst_ref sibling-field tests (PR1 foundation)
# ---------------------------------------------------------------------------


def test_external_ref_constructs_and_is_frozen() -> None:
    """ExternalRef holds (lang, module_path, name) and is hashable."""
    a = ExternalRef(lang="python", module_path="urllib.request", name="urlopen")
    b = ExternalRef(lang="python", module_path="urllib.request", name="urlopen")
    c = ExternalRef(lang="python", module_path="urllib.request", name="Request")
    assert a == b
    assert a != c
    # Hashable (frozen dataclass).
    assert {a, b, c} == {a, c}


def test_format_legacy_dst_uniform_5seg_shape() -> None:
    """``format_legacy_dst`` produces the 5-seg ``{lang}:{module}:0-0:{name}:unresolved`` shape.

    This is the helper PR2's per-analyzer retrofits route their legacy
    dst string through so the format is uniform across languages —
    no more Rust 6-seg outlier or Java class-embedded-in-module variant.
    """
    py_ref = ExternalRef(lang="python", module_path="urllib.request", name="urlopen")
    assert format_legacy_dst(py_ref) == "python:urllib.request:0-0:urlopen:unresolved"
    rust_ref = ExternalRef(lang="rust", module_path="std::fs", name="read_to_string")
    assert format_legacy_dst(rust_ref) == "rust:std::fs:0-0:read_to_string:unresolved"


def test_external_ref_to_dict_round_trip() -> None:
    """to_dict / from_dict preserve all three fields."""
    ref = ExternalRef(lang="rust", module_path="std::fs", name="read_to_string")
    d = ref.to_dict()
    assert d == {"lang": "rust", "module_path": "std::fs", "name": "read_to_string"}
    assert ExternalRef.from_dict(d) == ref


def test_edge_dst_ref_defaults_to_none() -> None:
    """Edge.dst_ref defaults to None when not provided (~90% case)."""
    e = Edge.create(src="s", dst="d", edge_type="calls", line=1, origin="test", origin_run_id="test")
    assert e.dst_ref is None
    # Serialized form omits the key when None (additive schema, back-compat).
    assert "dst_ref" not in e.to_dict()


def test_edge_dst_ref_serializes_when_present() -> None:
    """Populated dst_ref serializes as nested dict."""
    ref = ExternalRef(lang="python", module_path="urllib.request", name="urlopen")
    e = Edge.create(src="s", dst="d", edge_type="calls", line=1, dst_ref=ref, origin="test", origin_run_id="test")
    d = e.to_dict()
    assert d["dst_ref"] == {
        "lang": "python",
        "module_path": "urllib.request",
        "name": "urlopen",
    }
    # Round-trip via from_dict reconstructs the ExternalRef.
    e2 = Edge.from_dict(d)
    assert e2.dst_ref == ref


def test_edge_from_dict_handles_missing_dst_ref_key() -> None:
    """Pre-0.7.2 cached JSON without 'dst_ref' key deserializes with dst_ref=None.

    The defensive ``d.get("dst_ref")`` keeps old behavior maps loadable
    after the additive schema bump. This is the same backward-compat
    pattern ADR-0028 used for ``is_resolved``.
    """
    legacy_dict = {
        "id": "edge:sha256:abc",
        "src": "s",
        "dst": "d",
        "type": "calls",
        "line": 1,
        "meta": {"evidence_type": "ast_call_direct"},
        # Note: no 'dst_ref' key — represents a behavior map dumped before
        # SCHEMA_VERSION 0.7.2.
    }
    e = Edge.from_dict(legacy_dict)
    assert e.dst_ref is None


def test_edge_from_dict_handles_null_dst_ref() -> None:
    """Explicit dst_ref=None in JSON deserializes to dst_ref=None."""
    d = {
        "id": "edge:sha256:abc",
        "src": "s", "dst": "d", "type": "calls", "line": 1,
        "meta": {"evidence_type": "ast_call_direct"},
        "dst_ref": None,
    }
    e = Edge.from_dict(d)
    assert e.dst_ref is None


def test_py_analyzer_populates_dst_ref_on_polyglot_imports(tmp_path: Path) -> None:
    """PR1 reference adoption: Python emits ExternalRef on import + call edges.

    The polyglot fixture's Python source exercises the 4 import shapes
    WI-zigah Level 1 fixed; PR1 of WI-tihup requires each emitted
    external-target edge to carry a structured ExternalRef alongside the
    legacy dst string. This is the worked example for the 7 PR2
    retrofits.

    Uses the full pipeline (run_behavior_map) because call-edge emission
    depends on global symbol context that the standalone ``analyze_python``
    entry point doesn't synthesize from a single file.
    """
    import json

    from hypergumbo_core.cli import run_behavior_map

    src = (
        "import urllib.request\n"
        "from urllib.request import urlopen\n"
        "\n"
        "def f(url):\n"
        "    urllib.request.urlopen(url)\n"
        "    urlopen(url)\n"
    )
    (tmp_path / "poly.py").write_text(src)
    out = tmp_path / "out.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out, include_sketch_precomputed=False,
    )
    data = json.loads(out.read_text())
    refs = [
        (e["dst_ref"]["lang"], e["dst_ref"]["module_path"], e["dst_ref"]["name"])
        for e in data["edges"]
        if e.get("dst_ref") is not None
    ]
    # At least one external-target edge must carry a populated dst_ref
    # pointing at urllib.request.urlopen — the canonical WI-zigah Case A.
    assert (
        "python",
        "urllib.request",
        "urlopen",
    ) in refs, f"Expected an ExternalRef to urllib.request:urlopen, got {refs!r}"


# --- WI-gapin: origin_run_signature removal ---


class TestOriginRunSignatureRemoved:
    """WI-gapin: origin_run_signature was dead weight — never stamped by any producer."""

    def test_symbol_has_no_origin_run_signature_field(self) -> None:
        """Symbol dataclass must not have an origin_run_signature field."""
        assert not hasattr(Symbol, "origin_run_signature"), (
            "Symbol still has origin_run_signature — remove the dead field (WI-gapin)"
        )

    def test_edge_has_no_origin_run_signature_field(self) -> None:
        """Edge dataclass must not have an origin_run_signature field."""
        assert not hasattr(Edge, "origin_run_signature"), (
            "Edge still has origin_run_signature — remove the dead field (WI-gapin)"
        )

    def test_symbol_from_dict_ignores_legacy_origin_run_signature(self) -> None:
        """Old JSON with origin_run_signature must deserialize without error."""
        d = {
            "id": "python:a.py:1-5:f:function",
            "name": "f",
            "kind": "function",
            "language": "python",
            "path": "a.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            "origin": "python",
            "origin_run_id": "uuid:123",
            "origin_run_signature": "sha256:deadbeef",
        }
        sym = Symbol.from_dict(d)
        assert sym.name == "f"
        assert not hasattr(sym, "origin_run_signature")

    def test_edge_from_dict_ignores_legacy_origin_run_signature(self) -> None:
        """Old JSON with origin_run_signature must deserialize without error."""
        d = {
            "id": "edge:1",
            "src": "python:a.py:1-5:f:function",
            "dst": "python:b.py:1-5:g:function",
            "type": "calls",
            "line": 3,
            "origin": "python",
            "origin_run_id": "uuid:123",
            "origin_run_signature": "sha256:deadbeef",
        }
        edge = Edge.from_dict(d)
        assert edge.src == "python:a.py:1-5:f:function"
        assert not hasattr(edge, "origin_run_signature")

    def test_symbol_to_dict_omits_origin_run_signature(self) -> None:
        """Symbol.to_dict() output must not contain origin_run_signature."""
        span = Span(start_line=1, end_line=5, start_col=0, end_col=10)
        sym = Symbol(
            id="python:a.py:1-5:f:function",
            name="f",
            kind="function",
            language="python",
            path="a.py",
            span=span,
            origin="python",
            origin_run_id="uuid:123",
        )
        assert "origin_run_signature" not in sym.to_dict()

    def test_edge_to_dict_omits_origin_run_signature(self) -> None:
        """Edge.to_dict() output must not contain origin_run_signature."""
        edge = Edge(
            id="edge:1",
            src="python:a.py:1-5:f:function",
            dst="python:b.py:1-5:g:function",
            edge_type="calls",
            line=3,
            origin=["python"],
            origin_run_id="uuid:123",
        )
        assert "origin_run_signature" not in edge.to_dict()
