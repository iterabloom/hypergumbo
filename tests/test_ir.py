"""Tests for the internal representation (IR) layer."""
from pathlib import Path

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol, UsageContext
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
