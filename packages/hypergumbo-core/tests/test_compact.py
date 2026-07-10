# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for compact output mode.

This module tests the coverage-based truncation and bag-of-words
summarization for LLM-friendly output.
"""
import pytest

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.ir import Symbol, Edge, Span
from hypergumbo_core.compact import (
    tokenize_name,
    extract_path_pattern,
    compute_word_frequencies,
    compute_path_frequencies,
    compute_kind_distribution,
    compute_tier_distribution,
    select_by_coverage,
    format_compact_behavior_map,
    CompactConfig,
    IncludedSummary,
    OmittedSummary,
    CompactResult,
    STOP_WORDS,
    MIN_WORD_LENGTH,
    # Tiered output functions
    parse_tier_spec,
    estimate_node_tokens,
    estimate_behavior_map_tokens,
    select_by_tokens,
    format_tiered_behavior_map,
    recompute_view_summary,
    generate_tier_filename,
    DEFAULT_TIERS,
    CHARS_PER_TOKEN,
    # Filtering constants
    EXCLUDED_KINDS,
    _is_test_path,
    _is_example_path,
    EXAMPLE_PATH_PATTERNS,
)


def make_symbol(
    name: str,
    path: str = "src/main.py",
    kind: str = "function",
    language: str = "python",
    tier: int = 1,
) -> Symbol:
    """Helper to create test symbols."""
    sym = Symbol(
        id=f"{language}:{path}:1-10:{kind}:{name}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
    )
    sym.supply_chain_tier = tier
    sym.supply_chain_reason = f"tier_{tier}"
    return sym


def make_edge(src_id: str, dst_id: str, edge_type: str = "calls") -> Edge:
    """Helper to create test edges."""
    return Edge(
        id=f"edge:{src_id}->{dst_id}",
        src=src_id,
        dst=dst_id,
        edge_type=edge_type,
        line=1,
        confidence=0.9,

        origin="test", origin_run_id="test",
    )


class TestTokenizeName:
    """Tests for tokenize_name function."""

    def test_snake_case(self):
        """Snake case names are split correctly."""
        tokens = tokenize_name("get_user_by_id")
        assert "user" in tokens
        # "get" is a stop word

    def test_camel_case(self):
        """CamelCase names are split correctly."""
        tokens = tokenize_name("getUserById")
        assert "user" in tokens

    def test_pascal_case(self):
        """PascalCase names are split correctly."""
        tokens = tokenize_name("UserController")
        assert "user" in tokens
        assert "controller" in tokens

    def test_mixed_case(self):
        """Mixed case with underscores is handled."""
        tokens = tokenize_name("HTTP_request_handler")
        assert "http" in tokens
        assert "request" in tokens
        assert "handler" in tokens

    def test_stop_words_filtered(self):
        """Stop words are filtered out."""
        tokens = tokenize_name("get_the_value")
        assert "get" not in tokens
        assert "the" not in tokens
        assert "value" in tokens

    def test_short_tokens_filtered(self):
        """Tokens shorter than MIN_WORD_LENGTH are filtered."""
        tokens = tokenize_name("a_b_foo")
        assert "foo" in tokens
        # "a" and "b" are too short

    def test_numeric_suffix(self):
        """Handles numeric suffixes."""
        tokens = tokenize_name("handler_v2")
        assert "handler" in tokens


class TestExtractPathPattern:
    """Tests for extract_path_pattern function."""

    def test_test_directory(self):
        """Test directories are detected."""
        assert extract_path_pattern("tests/test_main.py") == "tests/"
        assert extract_path_pattern("test/unit/foo.py") == "test/"
        assert extract_path_pattern("src/__tests__/foo.js") == "__tests__/"

    def test_vendor_directory(self):
        """Vendor directories are detected."""
        assert extract_path_pattern("vendor/lodash/index.js") == "vendor/"
        assert extract_path_pattern("node_modules/react/index.js") == "node_modules/"

    def test_build_directory(self):
        """Build directories are detected."""
        assert extract_path_pattern("dist/bundle.js") == "dist/"
        assert extract_path_pattern("build/output.js") == "build/"

    def test_minified_files(self):
        """Minified files are detected."""
        assert extract_path_pattern("src/app.min.js") == "*.min.*"
        assert extract_path_pattern("dist/bundle.min.css") == "*.min.*"

    def test_bundled_files(self):
        """Bundled files are detected."""
        assert extract_path_pattern("dist/app.bundle.js") == "*.bundle.*"

    def test_regular_path(self):
        """Regular paths return first directory."""
        assert extract_path_pattern("src/utils/helpers.py") == "src/"
        assert extract_path_pattern("lib/core.js") == "lib/"

    def test_single_file(self):
        """Single file with no directory."""
        assert extract_path_pattern("main.py") == "main.py"


class TestComputeWordFrequencies:
    """Tests for compute_word_frequencies function."""

    def test_empty_symbols(self):
        """Empty input returns empty counter."""
        result = compute_word_frequencies([])
        assert len(result) == 0

    def test_word_counts(self):
        """Words are counted correctly."""
        symbols = [
            make_symbol("get_user"),
            make_symbol("update_user"),
            make_symbol("delete_user"),
        ]
        result = compute_word_frequencies(symbols)
        assert result["user"] == 3
        assert result["update"] == 1
        assert result["delete"] == 1


class TestComputePathFrequencies:
    """Tests for compute_path_frequencies function."""

    def test_empty_symbols(self):
        """Empty input returns empty counter."""
        result = compute_path_frequencies([])
        assert len(result) == 0

    def test_path_counts(self):
        """Path patterns are counted correctly."""
        symbols = [
            make_symbol("foo", path="tests/test_foo.py"),
            make_symbol("bar", path="tests/test_bar.py"),
            make_symbol("baz", path="src/main.py"),
        ]
        result = compute_path_frequencies(symbols)
        assert result["tests/"] == 2
        assert result["src/"] == 1


class TestComputeKindDistribution:
    """Tests for compute_kind_distribution function."""

    def test_empty_symbols(self):
        """Empty input returns empty dict."""
        result = compute_kind_distribution([])
        assert len(result) == 0

    def test_kind_counts(self):
        """Kinds are counted correctly."""
        symbols = [
            make_symbol("foo", kind="function"),
            make_symbol("bar", kind="function"),
            make_symbol("Baz", kind="class"),
        ]
        result = compute_kind_distribution(symbols)
        assert result["function"] == 2
        assert result["class"] == 1


class TestComputeTierDistribution:
    """Tests for compute_tier_distribution function."""

    def test_empty_symbols(self):
        """Empty input returns empty dict."""
        result = compute_tier_distribution([])
        assert len(result) == 0

    def test_tier_counts(self):
        """Tiers are counted correctly."""
        symbols = [
            make_symbol("foo", tier=1),
            make_symbol("bar", tier=1),
            make_symbol("baz", tier=3),
        ]
        result = compute_tier_distribution(symbols)
        assert result[1] == 2
        assert result[3] == 1


class TestSelectByCoverage:
    """Tests for select_by_coverage function."""

    def test_empty_symbols(self):
        """Empty input returns empty result."""
        config = CompactConfig()
        result = select_by_coverage([], [], config)

        assert result.included.count == 0
        assert result.omitted.count == 0
        assert result.included.coverage == 1.0

    def test_all_included_small_set(self):
        """Small sets are fully included (min_symbols)."""
        symbols = [make_symbol(f"sym_{i}") for i in range(5)]
        config = CompactConfig(min_symbols=10)

        result = select_by_coverage(symbols, [], config)

        assert result.included.count == 5
        assert result.omitted.count == 0

    def test_coverage_based_selection(self):
        """Symbols selected by coverage threshold."""
        # Create symbols where one has high centrality
        core = make_symbol("core")
        helper1 = make_symbol("helper1")
        helper2 = make_symbol("helper2")

        # Core is called by both helpers
        edges = [
            make_edge(helper1.id, core.id),
            make_edge(helper2.id, core.id),
        ]

        config = CompactConfig(
            target_coverage=0.5,
            min_symbols=1,
            max_symbols=100,
        )

        result = select_by_coverage([core, helper1, helper2], edges, config)

        # Core has highest centrality, should be included first
        assert core in result.included.symbols

    def test_max_symbols_respected(self):
        """Max symbols limit is respected."""
        symbols = [make_symbol(f"sym_{i}") for i in range(100)]
        config = CompactConfig(max_symbols=10, min_symbols=1)

        result = select_by_coverage(symbols, [], config)

        assert result.included.count <= 10

    def test_omitted_summary_has_words(self):
        """Omitted summary includes word frequencies."""
        # Create enough symbols to ensure some are omitted
        symbols = [
            make_symbol("test_foo"),
            make_symbol("test_bar"),
            make_symbol("test_baz"),
            make_symbol("important_core"),  # This one will be included
        ]

        # Make important_core have highest centrality
        edges = [
            make_edge(symbols[0].id, symbols[3].id),
            make_edge(symbols[1].id, symbols[3].id),
            make_edge(symbols[2].id, symbols[3].id),
        ]

        config = CompactConfig(
            target_coverage=0.9,
            min_symbols=1,
            max_symbols=2,
        )

        result = select_by_coverage(symbols, edges, config)

        # Check that omitted summary has word frequencies
        if result.omitted.count > 0:
            assert len(result.omitted.top_words) >= 0  # May have words

    def test_language_proportional_disabled(self):
        """language_proportional=False uses original sorting."""
        symbols = [make_symbol(f"sym_{i}") for i in range(20)]
        config = CompactConfig(
            language_proportional=False,
            max_symbols=10,
            min_symbols=1,
        )

        result = select_by_coverage(symbols, [], config)

        # Should still select symbols, just without language stratification
        assert result.included.count <= 10

    def test_max_symbols_breaks_loop(self):
        """Max symbols limit breaks the selection loop."""
        # Create many symbols to ensure we hit max before coverage
        symbols = [make_symbol(f"sym_{i}") for i in range(200)]
        config = CompactConfig(
            target_coverage=0.99,  # Very high coverage
            max_symbols=5,  # But strict max limit
            min_symbols=1,
        )

        result = select_by_coverage(symbols, [], config)

        # Should stop at max_symbols even though coverage not met
        assert result.included.count == 5

    def test_utility_dampener_demotes_logger(self):
        """select_by_coverage applies apply_utility_symbol_weights (WI-lidum).

        A high-centrality `Logger.error` symbol with many callers should
        not displace a less-central first-party domain function once
        utility-name dampening (0.1x) is applied.
        """
        # Long span so trivial_sink doesn't also fire and confound the test
        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        logger_sym = Symbol(
            id="logger", name="Logger.error", kind="method", language="python",
            path="src/observability/logger.py", span=long_span,
        )
        logger_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="process_payment", kind="function", language="python",
            path="src/payments/processor.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "logger") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        config = CompactConfig(
            language_proportional=False, max_symbols=1, min_symbols=1,
            target_coverage=0.0,
        )
        result = select_by_coverage([logger_sym, domain_sym], edges, config)
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids, (
            "Expected process_payment to outrank Logger.error after utility "
            f"dampening; got included={included_ids}"
        )

    def test_trivial_sink_dampener_demotes_short_pure_sinks(self):
        """select_by_coverage applies apply_trivial_sink_weights (WI-lidum).

        A short-bodied pure sink (out_degree=0, loc<=20) with high in-degree
        gets multiplied by 0.1x and should fall behind a longer-bodied
        domain function with lower in-degree.
        """
        short_span = Span(start_line=1, end_line=5, start_col=0, end_col=0)
        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        sink_sym = Symbol(
            id="sink", name="get_status", kind="function", language="python",
            path="src/util/status.py", span=short_span,
        )
        sink_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="reconcile_ledger", kind="function",
            language="python", path="src/finance/reconcile.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "sink") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        config = CompactConfig(
            language_proportional=False, max_symbols=1, min_symbols=1,
            target_coverage=0.0,
        )
        result = select_by_coverage([sink_sym, domain_sym], edges, config)
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids, (
            "Expected reconcile_ledger to outrank get_status after trivial-"
            f"sink dampening; got included={included_ids}"
        )

    def test_generated_dampener_demotes_openapi_models(self):
        """select_by_coverage applies apply_generated_code_weights (WI-lidum).

        kserve's V1beta1 OpenAPI model classes — flagged is_generated_file —
        accounted for 22 of the top-100 select_by_coverage entries before
        this dampener was applied. This test pins the fix.
        """
        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        generated_sym = Symbol(
            id="generated", name="V1beta1InferenceService", kind="class",
            language="python",
            path="kserve/models/v1beta1_inference_service.py",
            span=long_span,
        )
        generated_sym.supply_chain_tier = 1
        generated_sym.is_generated_file = True
        domain_sym = Symbol(
            id="domain", name="InferenceService", kind="class", language="python",
            path="kserve/api/inference_service.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "generated") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        config = CompactConfig(
            language_proportional=False, max_symbols=1, min_symbols=1,
            target_coverage=0.0,
        )
        result = select_by_coverage([generated_sym, domain_sym], edges, config)
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids, (
            "Expected InferenceService to outrank V1beta1InferenceService "
            f"after generated dampening; got included={included_ids}"
        )

    def test_file_kind_dampener_suppresses_file_symbols(self):
        """select_by_coverage applies apply_file_kind_weights (WI-lidum).

        kind="file" symbols (synthesized one per analyzed source file by
        the orchestrator post-process) accumulate in-degree from each
        file's import count and would otherwise displace real functions.
        Dampener multiplies them by 0.0 (full suppression).
        """
        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        file_sym = Symbol(
            id="file_sym", name="cmd/main.go", kind="file", language="go",
            path="cmd/main.go", span=long_span,
        )
        file_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="ServeRequest", kind="function", language="go",
            path="server/server.go", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "file_sym") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        config = CompactConfig(
            language_proportional=False, max_symbols=1, min_symbols=1,
            target_coverage=0.0,
        )
        result = select_by_coverage([file_sym, domain_sym], edges, config)
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids, (
            "Expected ServeRequest to outrank kind=file symbol after "
            f"file-kind suppression; got included={included_ids}"
        )

    def test_centrality_params_match_rank_symbols(self):
        """select_by_coverage's compute_centrality call passes rank_symbols'
        tuned parameters (WI-dohaf): hub_threshold=100, within_file_weight=0.3,
        max_per_file_in=5, edge_type_weights=DEFAULT_EDGE_TYPE_WEIGHTS.

        Tests max_per_file_in=5: target_A gets 30 callers all from the same
        file (capped to 5), target_B gets 6 callers from 6 distinct files
        (uncapped, total 6). Without the parameter, A wins on raw in-degree;
        with the parameter, B wins.
        """
        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        target_a = Symbol(
            id="target_a", name="hot_helper", kind="function", language="python",
            path="src/helper.py", span=long_span,
        )
        target_a.supply_chain_tier = 1
        target_b = Symbol(
            id="target_b", name="distributed_callee", kind="function",
            language="python", path="src/callee.py", span=long_span,
        )
        target_b.supply_chain_tier = 1
        a_callers = []
        for i in range(30):
            c = Symbol(
                id=f"a_caller{i}", name=f"call_a_{i}", kind="function",
                language="python", path="src/single_caller.py",
                span=long_span,
            )
            c.supply_chain_tier = 1
            a_callers.append(c)
        b_callers = []
        for i in range(6):
            c = Symbol(
                id=f"b_caller{i}", name=f"call_b_{i}", kind="function",
                language="python", path=f"src/file_b_{i}.py",
                span=long_span,
            )
            c.supply_chain_tier = 1
            b_callers.append(c)
        edges = [
            make_edge(c.id, "target_a") for c in a_callers
        ] + [
            make_edge(c.id, "target_b") for c in b_callers
        ]
        config = CompactConfig(
            language_proportional=False, max_symbols=1, min_symbols=1,
            target_coverage=0.0,
        )
        result = select_by_coverage(
            [target_a, target_b] + a_callers + b_callers, edges, config,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "target_b" in included_ids and "target_a" not in included_ids, (
            "Expected distributed_callee to outrank hot_helper after "
            f"max_per_file_in=5 capping; got included={included_ids}"
        )

    def test_noise_dampener_demotes_migrations(self):
        """select_by_coverage applies apply_noise_weights (WI-lidum).

        django's ProjectState / ModelState from db/migrations/ accounted
        for 8 of the top-100 select_by_coverage entries before this
        dampener was applied.
        """
        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        migration_sym = Symbol(
            id="migration", name="ModelState", kind="class", language="python",
            path="django/db/migrations/state.py", span=long_span,
        )
        migration_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="DomainModel", kind="class", language="python",
            path="app/domain.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "migration") for i in range(15)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        config = CompactConfig(
            language_proportional=False, max_symbols=1, min_symbols=1,
            target_coverage=0.0,
        )
        result = select_by_coverage([migration_sym, domain_sym], edges, config)
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids, (
            "Expected DomainModel to outrank ModelState after noise dampening; "
            f"got included={included_ids}"
        )


class TestCompactConfig:
    """Tests for CompactConfig dataclass."""

    def test_defaults(self):
        """Default values are set correctly."""
        config = CompactConfig()
        assert config.target_coverage == 0.8
        assert config.max_symbols == 100
        assert config.min_symbols == 10
        assert config.top_words_count == 10
        assert config.top_paths_count == 5
        assert config.first_party_priority is True

    def test_custom_values(self):
        """Custom values can be set."""
        config = CompactConfig(
            target_coverage=0.9,
            max_symbols=50,
        )
        assert config.target_coverage == 0.9
        assert config.max_symbols == 50


class TestIncludedSummary:
    """Tests for IncludedSummary dataclass."""

    def test_to_dict(self):
        """Serialization works correctly."""
        sym = make_symbol("foo")
        summary = IncludedSummary(
            count=1,
            centrality_sum=0.5,
            coverage=0.8,
            symbols=[sym],
        )

        d = summary.to_dict()

        assert d["count"] == 1
        assert d["centrality_sum"] == 0.5
        assert d["coverage"] == 0.8
        assert "symbols" not in d  # Symbols not serialized in summary


class TestOmittedSummary:
    """Tests for OmittedSummary dataclass."""

    def test_to_dict(self):
        """Serialization works correctly."""
        summary = OmittedSummary(
            count=100,
            centrality_sum=0.2,
            max_centrality=0.05,
            top_words=[("test", 50), ("mock", 30)],
            top_paths=[("tests/", 80)],
            kinds={"function": 80, "class": 20},
            tiers={1: 50, 3: 50},
        )

        d = summary.to_dict()

        assert d["count"] == 100
        assert d["centrality_sum"] == 0.2
        assert d["max_centrality"] == 0.05
        assert d["top_words"] == [
            {"word": "test", "count": 50},
            {"word": "mock", "count": 30},
        ]
        assert d["top_paths"] == [{"pattern": "tests/", "count": 80}]
        assert d["kinds"] == {"function": 80, "class": 20}
        assert d["tiers"] == {"1": 50, "3": 50}  # Keys are stringified


class TestCompactResult:
    """Tests for CompactResult dataclass."""

    def test_to_dict(self):
        """Full result serialization works."""
        result = CompactResult(
            included=IncludedSummary(
                count=10, centrality_sum=0.8, coverage=0.8, symbols=[]
            ),
            omitted=OmittedSummary(
                count=90, centrality_sum=0.2, max_centrality=0.02,
                top_words=[], top_paths=[], kinds={}, tiers={}
            ),
        )

        d = result.to_dict()

        assert "included" in d
        assert "omitted" in d
        assert d["included"]["count"] == 10
        assert d["omitted"]["count"] == 90


class TestFormatCompactBehaviorMap:
    """Tests for format_compact_behavior_map function."""

    def test_basic_formatting(self):
        """Basic behavior map formatting works."""
        symbols = [
            make_symbol("core"),
            make_symbol("helper"),
        ]
        edges = [make_edge(symbols[1].id, symbols[0].id)]

        behavior_map = {
            "schema_version": SCHEMA_VERSION,
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
        }

        config = CompactConfig(min_symbols=1, max_symbols=1)
        result = format_compact_behavior_map(behavior_map, symbols, edges, config)

        assert result["view"] == "compact"
        assert "nodes_summary" in result
        assert len(result["nodes"]) <= 1

    def test_edges_filtered(self):
        """Only edges where BOTH endpoints are included are kept."""
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")

        # Edge a->b (both in included set when max=2)
        # Edge b->c (c will be omitted, so this edge should be dropped)
        edge_ab = make_edge(sym_a.id, sym_b.id)
        edge_bc = make_edge(sym_b.id, sym_c.id)

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [edge_ab.to_dict(), edge_bc.to_dict()],
            "entrypoints": [],
        }

        config = CompactConfig(min_symbols=2, max_symbols=2)
        result = format_compact_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], [edge_ab, edge_bc], config,
            force_include_entrypoints=False,
        )

        # Edges should only exist where BOTH endpoints are in included set
        included_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["src"] in included_ids and edge["dst"] in included_ids

    def test_output_closure_preserves_parallel_induced_edges(self):
        """WI-hakom output-closure: compact edges == the induced subgraph,
        including parallel edges, in the connectivity-aware path.

        For every source edge whose both endpoints survive into the compact
        view, that exact edge (by id) must appear in the compact edge array —
        no induced edge, parallel or otherwise, may be silently dropped.
        """
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")
        symbols = [sym_a, sym_b, sym_c]

        # Two parallel A->B edges (distinct types/ids) + A->C for connectivity.
        e_calls = make_edge(sym_a.id, sym_b.id, edge_type="calls")
        e_calls.id = "edge:a->b:calls"
        e_refs = make_edge(sym_a.id, sym_b.id, edge_type="references")
        e_refs.id = "edge:a->b:references"
        e_ac = make_edge(sym_a.id, sym_c.id, edge_type="calls")
        edges = [e_calls, e_refs, e_ac]

        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        config = CompactConfig(min_symbols=3, max_symbols=3)
        result = format_compact_behavior_map(
            behavior_map, symbols, edges, config,
            connectivity_aware=True, force_include_entrypoints=False,
        )

        included_ids = {n["id"] for n in result["nodes"]}
        # The parallel pair must actually be in scope for this to test anything.
        assert sym_a.id in included_ids and sym_b.id in included_ids

        # Output closure: compact edges == induced subgraph (both directions).
        expected = {
            e["id"] for e in behavior_map["edges"]
            if e["src"] in included_ids and e["dst"] in included_ids
            and e["src"] != e["dst"]
        }
        got = {e["id"] for e in result["edges"]}
        assert expected == got, f"induced edges dropped: {expected - got}"

    def test_strips_heavy_keys_but_keeps_provenance_and_quality(self):
        """WI-judun: compact drops the heavy, view-irrelevant blocks but KEEPS
        the finalize provenance/quality signals.

        ``usage_contexts`` is spec-mandated stripped from compact/tiered views
        (spec §usage_contexts) and ``sketch_precomputed`` is an internal cache
        artifact (spec §707) — both are dropped. But ``analysis_runs``
        (provenance) and ``validation_report`` (the finalize quality signal)
        are deliberately PRESERVED through the compact projection per
        ADR-0033/ADR-0043 — unlike the more aggressive tiered view, which drops
        them too. Applies to BOTH the coverage and connectivity-aware branches.
        """
        symbols = [make_symbol("core"), make_symbol("helper")]
        edges = [make_edge(symbols[1].id, symbols[0].id)]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
            "analysis_runs": [{"analyzer": "py", "files": 3, "symbols": 9}],
            "usage_contexts": [{"symbol_id": "x", "kind": "call"}],
            "sketch_precomputed": {"config_info": "x" * 500},
            "validation_report": {"violations": [], "checks": 12},
        }
        config = CompactConfig(min_symbols=2, max_symbols=2)

        for connectivity_aware in (False, True):
            result = format_compact_behavior_map(
                behavior_map, symbols, edges, config,
                connectivity_aware=connectivity_aware,
                force_include_entrypoints=False,
            )
            for stripped in ("usage_contexts", "sketch_precomputed"):
                assert stripped not in result, (
                    f"compact (connectivity_aware={connectivity_aware}) should "
                    f"strip {stripped} to save tokens"
                )
            for kept in ("analysis_runs", "validation_report"):
                assert kept in result, (
                    f"compact (connectivity_aware={connectivity_aware}) must "
                    f"preserve {kept} (ADR-0043 provenance/quality signal)"
                )

    def test_metrics_describe_projection_not_source(self):
        """WI-pizat: compact recomputes its metrics block from the PROJECTED
        arrays instead of echoing the source (full-repo) totals.

        ``analysis_incomplete`` is left untouched — per spec §726 it is an
        analyzer-scope flag (early termination / errors / resource limits),
        NOT a view-truncation signal. Applies to both selection branches.
        """
        symbols = [make_symbol(f"s{i}") for i in range(8)]
        edges = [
            make_edge(symbols[1].id, symbols[0].id),
            make_edge(symbols[2].id, symbols[0].id),
        ]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
            "analysis_incomplete": False,
            "metrics": {
                "total_nodes": 9999,
                "total_edges": 9999,
                "total_files": 999,
                "by_supply_chain_tier": {
                    "first_party": {
                        "nodes": 9999, "edges": 9999, "edges_incident": 9999,
                    },
                },
            },
        }
        config = CompactConfig(min_symbols=3, max_symbols=3)

        for connectivity_aware in (False, True):
            result = format_compact_behavior_map(
                behavior_map, symbols, edges, config,
                connectivity_aware=connectivity_aware,
                force_include_entrypoints=False,
            )
            m = result["metrics"]
            # metrics describe the projected arrays, not the 9999 source totals
            assert m["total_nodes"] == len(result["nodes"]) < 9999
            assert m["total_edges"] == len(result["edges"]) < 9999
            # analyzer-scope flag untouched by view truncation (spec §726)
            assert result["analysis_incomplete"] is False

    def test_per_node_centrality_edge_count_and_summary_companions(self):
        """WI-zotam + WI-kulan: compact annotates each node with its centrality,
        reports included_edges_count in BOTH selection modes, and emits
        entrypoints_summary / features_summary companions for the truncated
        arrays. Invariant assertions, robust to selection nondeterminism.
        """
        symbols = [make_symbol(f"s{i}") for i in range(8)]
        edges = [
            make_edge(symbols[1].id, symbols[0].id),
            make_edge(symbols[2].id, symbols[0].id),
        ]
        entrypoints = [
            {"symbol_id": s.id, "kind": "function", "confidence": 0.9}
            for s in symbols
        ]
        features = [
            {"id": "feat1", "entry_nodes": [symbols[7].id],
             "node_ids": [symbols[7].id], "edge_ids": []},
        ]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": entrypoints,
            "features": features,
        }
        config = CompactConfig(min_symbols=3, max_symbols=3)

        for connectivity_aware in (False, True):
            result = format_compact_behavior_map(
                behavior_map, symbols, edges, config,
                connectivity_aware=connectivity_aware,
                force_include_entrypoints=False,
            )
            # WI-zotam (a): every retained node carries a centrality score
            for n in result["nodes"]:
                assert isinstance(n["centrality"], float)
            # WI-zotam (b): included_edges_count present in BOTH modes == array
            assert (
                result["nodes_summary"]["included_edges_count"]
                == len(result["edges"])
            )
            # WI-kulan: companion summaries mirror nodes_summary's shape and
            # reconcile with the emitted/source arrays
            for skey, akey in (("entrypoints_summary", "entrypoints"),
                               ("features_summary", "features")):
                summ = result[skey]
                assert summ["included"]["count"] == len(result[akey])
                assert summ["omitted"]["count"] == (
                    len(behavior_map[akey]) - len(result[akey])
                )
            # entrypoints truncation is actually exercised (8 -> <=3 nodes)
            assert result["entrypoints_summary"]["omitted"]["count"] > 0

    def test_default_selection_containment_monotonic_multilang(self):
        """WI-kolal: with the centrality-ranked default (connectivity_aware=False),
        a smaller --max-symbols budget selects a SUBSET of a larger budget's
        selection — including across languages. The language_proportional budget
        allocation must not let a language's slice SHRINK as the global budget
        grows (which would break nodes(B1) ⊆ nodes(B2))."""
        # UNEQUAL language sizes + budgets that are NOT multiples of the language
        # count, so int(budget * proportion) truncates and the remainder is
        # redistributed — the exact case that can make a language's slice shrink
        # as the global budget grows.
        sizes = {"python": ("py", 8), "javascript": ("js", 4),
                 "go": ("go", 3), "rust": ("rs", 2), "c": ("c", 1)}
        symbols = []
        for lang, (ext, n) in sizes.items():
            for i in range(n):
                symbols.append(make_symbol(
                    f"{lang}_{i}", path=f"src/{lang}/{i}.{ext}", language=lang
                ))
        edges = [
            make_edge(symbols[1].id, symbols[0].id),
            make_edge(symbols[9].id, symbols[8].id),
            make_edge(symbols[13].id, symbols[12].id),
        ]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }
        prev_ids = None
        for budget in range(2, len(symbols) + 1):
            # Matches the compact CLI default: a GLOBAL centrality-ranked prefix
            # (language_proportional disabled), which is monotonic by construction
            # — unlike the language-stratified allocation, whose remainder
            # redistribution can be non-monotonic (WI-kolal).
            config = CompactConfig(
                min_symbols=1, max_symbols=budget, language_proportional=False,
            )
            result = format_compact_behavior_map(
                behavior_map, symbols, edges, config,
                connectivity_aware=False, force_include_entrypoints=False,
            )
            ids = {n["id"] for n in result["nodes"]}
            if prev_ids is not None:
                assert prev_ids <= ids, (
                    f"containment violated at budget {budget}: "
                    f"dropped {prev_ids - ids}"
                )
            prev_ids = ids

    def test_output_closure_all_projected_views(self):
        """INV-fanur (G4): every compact projected view (both selection modes)
        is self-consistent — edges reference only included nodes, entrypoints
        and features reference only included nodes/edges, and every summary
        count matches its array length. The output-closure guardrail for the
        whole projection-finalize block.
        """
        symbols = [make_symbol(f"s{i}") for i in range(12)]
        edges = [make_edge(symbols[i + 1].id, symbols[0].id) for i in range(5)]
        entrypoints = [
            {"symbol_id": s.id, "kind": "function", "confidence": 0.9}
            for s in symbols
        ]
        features = [
            {"id": "f_hub", "entry_nodes": [symbols[0].id],
             "node_ids": [s.id for s in symbols[:6]],
             "edge_ids": [e.id for e in edges]},
            {"id": "f_orphan", "entry_nodes": [symbols[11].id],
             "node_ids": [symbols[11].id], "edge_ids": []},
        ]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": entrypoints,
            "features": features,
        }
        config = CompactConfig(min_symbols=3, max_symbols=4)

        for connectivity_aware in (False, True):
            view = format_compact_behavior_map(
                behavior_map, symbols, edges, config,
                connectivity_aware=connectivity_aware,
                force_include_entrypoints=False,
            )
            node_ids = {n["id"] for n in view["nodes"]}
            edge_ids = {e["id"] for e in view["edges"]}

            # edges ⊆ nodes (both endpoints included)
            for e in view["edges"]:
                assert e["src"] in node_ids and e["dst"] in node_ids

            # entrypoints reference only included nodes
            for ep in view["entrypoints"]:
                assert ep["symbol_id"] in node_ids

            # features reference only included nodes/edges (INV-titid / F38.C4)
            for feat in view["features"]:
                for nid in feat.get("node_ids", []):
                    assert nid in node_ids
                for eid in feat.get("edge_ids", []):
                    assert eid in edge_ids
                assert any(n in node_ids for n in feat.get("entry_nodes", []))

            # every summary count reconciles with its emitted array
            ns = view["nodes_summary"]
            assert ns["included"]["count"] == len(view["nodes"])
            assert ns["included_edges_count"] == len(view["edges"])
            assert (view["entrypoints_summary"]["included"]["count"]
                    == len(view["entrypoints"]))
            assert (view["features_summary"]["included"]["count"]
                    == len(view["features"]))

    def test_connectivity_selection_deterministic_across_hash_seeds(self):
        """WI-nivuj: connectivity selection is PYTHONHASHSEED-independent.

        The seed/frontier iteration was over sets, so a connectivity-score TIE
        resolved to an arbitrary (hash-seed-dependent) node. This runs an
        identical selection with a deliberate tie under several hash seeds and
        asserts the output is identical. A subprocess test (the fix's sorted()
        lines are covered by the many in-process select_by_connectivity tests);
        it is what makes the determinism reliably testable — an in-process test
        cannot vary PYTHONHASHSEED, which is fixed per interpreter.
        """
        import os
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent('''
            from hypergumbo_core.compact import select_by_connectivity
            from hypergumbo_core.ir import Symbol, Edge, Span

            def sym(name):
                return Symbol(
                    id=f"python:src/m.py:1-2:function:{name}", name=name,
                    kind="function", language="python", path="src/m.py",
                    span=Span(start_line=1, end_line=2,
                              start_col=0, end_col=0),
                )

            # seed S; A..E are symmetric frontier nodes (each connects only to
            # S) -> identical connectivity scores -> a tie the budget can't fit.
            s = sym("seed")
            others = [sym(n) for n in ("aaa", "bbb", "ccc", "ddd", "eee")]
            edges = [
                Edge(id=f"e{i}", src=o.id, dst=s.id, edge_type="calls",
                     line=1, confidence=0.9, origin="t", origin_run_id="t")
                for i, o in enumerate(others)
            ]
            r = select_by_connectivity(
                [s, *others], edges, {s.id}, max_additional=2,
            )
            print(",".join(x.id for x in r.included.symbols))
        ''')

        outputs = set()
        for seed in ("0", "1", "2", "3", "7", "13"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            res = subprocess.run(
                [sys.executable, "-c", script], env=env,
                capture_output=True, text=True, check=True,
            )
            outputs.add(res.stdout.strip())
        assert len(outputs) == 1, (
            f"connectivity selection non-deterministic across hash seeds: "
            f"{outputs}"
        )


class TestStopWords:
    """Tests for stop words constant."""

    def test_common_stop_words(self):
        """Common stop words are included."""
        assert "get" in STOP_WORDS
        assert "set" in STOP_WORDS
        assert "the" in STOP_WORDS
        assert "self" in STOP_WORDS


class TestMinWordLength:
    """Tests for MIN_WORD_LENGTH constant."""

    def test_min_length(self):
        """Minimum word length is reasonable."""
        assert MIN_WORD_LENGTH >= 2
        assert MIN_WORD_LENGTH <= 4


class TestFirstPartyPriorityFalse:
    """Tests for first_party_priority=False in compact mode."""

    def test_no_tier_weighting(self):
        """Raw centrality used when first_party_priority=False."""
        first_party = make_symbol("my_func", tier=1)
        external = make_symbol("lodash", tier=3)
        caller = make_symbol("caller")

        # External has higher centrality
        edges = [make_edge(caller.id, external.id)]

        config = CompactConfig(
            first_party_priority=False,
            min_symbols=1,
            max_symbols=2,
        )

        result = select_by_coverage([first_party, external, caller], edges, config)

        # Without tier weighting, external should be included (has incoming edge)
        included_names = {s.name for s in result.included.symbols}
        assert "lodash" in included_names


# ============================================================================
# Tiered output tests
# ============================================================================


class TestParseTierSpec:
    """Tests for parse_tier_spec function."""

    def test_parse_k_suffix(self):
        """Parse specs with 'k' suffix."""
        assert parse_tier_spec("4k") == 4000
        assert parse_tier_spec("16k") == 16000
        assert parse_tier_spec("64k") == 64000

    def test_parse_uppercase_k(self):
        """Parse specs with uppercase 'K' suffix."""
        assert parse_tier_spec("4K") == 4000
        assert parse_tier_spec("16K") == 16000

    def test_parse_decimal_k(self):
        """Parse specs with decimal values."""
        assert parse_tier_spec("1.5k") == 1500
        assert parse_tier_spec("2.5k") == 2500

    def test_parse_raw_number(self):
        """Parse raw number specs."""
        assert parse_tier_spec("4000") == 4000
        assert parse_tier_spec("16000") == 16000

    def test_parse_with_whitespace(self):
        """Parse specs with leading/trailing whitespace."""
        assert parse_tier_spec("  4k  ") == 4000
        assert parse_tier_spec("\t16k\n") == 16000

    def test_invalid_spec_raises(self):
        """Invalid specs raise ValueError."""
        with pytest.raises(ValueError):
            parse_tier_spec("invalid")


class TestEstimateNodeTokens:
    """Tests for estimate_node_tokens function."""

    def test_basic_node(self):
        """Basic node token estimation."""
        node_dict = {
            "id": "python:src/main.py:1-10:function:main",
            "name": "main",
            "kind": "function",
            "language": "python",
            "path": "src/main.py",
        }
        tokens = estimate_node_tokens(node_dict)
        # Should be roughly len(json) / CHARS_PER_TOKEN
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_larger_node_more_tokens(self):
        """Larger nodes should have more tokens."""
        small_node = {"id": "a", "name": "x"}
        large_node = {
            "id": "python:src/very/long/path/to/file.py:1-100:function:very_long_function_name",
            "name": "very_long_function_name",
            "kind": "function",
            "language": "python",
            "path": "src/very/long/path/to/file.py",
            "meta": {"route_path": "/api/v1/users/{id}/profile"},
        }
        assert estimate_node_tokens(large_node) > estimate_node_tokens(small_node)


class TestEstimateBehaviorMapTokens:
    """Tests for estimate_behavior_map_tokens function."""

    def test_basic_behavior_map(self):
        """Basic behavior map token estimation."""
        behavior_map = {
            "schema_version": SCHEMA_VERSION,
            "nodes": [{"id": "a", "name": "foo"}],
            "edges": [],
        }
        tokens = estimate_behavior_map_tokens(behavior_map)
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_empty_behavior_map(self):
        """Empty behavior map has minimal tokens."""
        behavior_map = {}
        tokens = estimate_behavior_map_tokens(behavior_map)
        # Should be very small (just "{}")
        assert tokens < 5


class TestSelectByTokens:
    """Tests for select_by_tokens function."""

    def test_empty_symbols(self):
        """Empty input returns empty result."""
        result = select_by_tokens([], [], target_tokens=4000)
        assert result.included.count == 0
        assert result.omitted.count == 0
        assert result.included.coverage == 1.0

    def test_fits_within_budget(self):
        """Small symbol set fits within budget."""
        symbols = [make_symbol(f"sym_{i}") for i in range(5)]
        result = select_by_tokens(symbols, [], target_tokens=100000)
        # With large budget, all should fit
        assert result.included.count == 5
        assert result.omitted.count == 0

    def test_respects_token_limit(self):
        """Large symbol sets are truncated to fit budget."""
        # Create many symbols
        symbols = [make_symbol(f"symbol_with_longer_name_{i}") for i in range(100)]
        edges = []

        # Use a small token budget
        result = select_by_tokens(symbols, edges, target_tokens=1000)

        # Should include fewer than all symbols
        assert result.included.count < 100
        assert result.omitted.count > 0

    def test_omitted_has_summary(self):
        """Omitted summary is populated."""
        symbols = [make_symbol(f"test_func_{i}") for i in range(50)]
        result = select_by_tokens(symbols, [], target_tokens=500)

        if result.omitted.count > 0:
            # Should have summary info
            assert isinstance(result.omitted.top_words, list)
            assert isinstance(result.omitted.top_paths, list)
            assert isinstance(result.omitted.kinds, dict)

    def test_first_party_priority_true(self):
        """First party symbols prioritized when flag is True."""
        first_party = make_symbol("my_core", tier=1)
        external = make_symbol("external_dep", tier=3)
        caller = make_symbol("caller")

        # External has more edges
        edges = [make_edge(caller.id, external.id)]

        # Use larger budget to ensure symbols fit
        result = select_by_tokens(
            [first_party, external, caller], edges,
            target_tokens=2000,
            first_party_priority=True,
        )

        # With tier weighting, first party should get priority
        included_names = {s.name for s in result.included.symbols}
        assert "my_core" in included_names

    def test_first_party_priority_false(self):
        """Raw centrality used when first_party_priority=False."""
        first_party = make_symbol("my_core", tier=1)
        external = make_symbol("external_dep", tier=3)
        caller = make_symbol("caller")

        # External has more edges
        edges = [make_edge(caller.id, external.id)]

        # Use larger budget to ensure symbols fit
        result = select_by_tokens(
            [first_party, external, caller], edges,
            target_tokens=2000,
            first_party_priority=False,
        )

        # Without tier weighting, external with edges should be included
        included_names = {s.name for s in result.included.symbols}
        assert "external_dep" in included_names

    def test_language_proportional_disabled(self):
        """language_proportional=False uses original sorting."""
        symbols = [make_symbol(f"sym_{i}") for i in range(20)]
        result = select_by_tokens(
            symbols, [],
            target_tokens=4000,
            language_proportional=False,
        )

        # Should still select symbols, just without language stratification
        assert result.included.count > 0


class TestFormatTieredBehaviorMap:
    """Tests for format_tiered_behavior_map function."""

    def test_basic_formatting(self):
        """Basic tiered behavior map formatting."""
        symbols = [make_symbol("core"), make_symbol("helper")]
        edges = [make_edge(symbols[1].id, symbols[0].id)]

        behavior_map = {
            "schema_version": SCHEMA_VERSION,
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
        }

        result = format_tiered_behavior_map(
            behavior_map, symbols, edges, target_tokens=4000
        )

        assert result["view"] == "tiered"
        assert result["tier_tokens"] == 4000
        assert "nodes_summary" in result
        assert isinstance(result["nodes"], list)

    def test_tier_tokens_in_output(self):
        """Output includes tier_tokens field."""
        symbols = [make_symbol("foo")]
        behavior_map = {"nodes": [s.to_dict() for s in symbols], "edges": []}

        result = format_tiered_behavior_map(behavior_map, symbols, [], 16000)
        assert result["tier_tokens"] == 16000

    def test_edges_filtered(self):
        """Only edges where BOTH endpoints are included are kept."""
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")

        edge_ab = make_edge(sym_a.id, sym_b.id)
        edge_bc = make_edge(sym_b.id, sym_c.id)

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [edge_ab.to_dict(), edge_bc.to_dict()],
            "entrypoints": [],
        }

        # Small budget to force truncation
        result = format_tiered_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], [edge_ab, edge_bc],
            target_tokens=500,
            force_include_entrypoints=False,
        )

        # Edges should only exist where BOTH endpoints are in included set
        included_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["src"] in included_ids and edge["dst"] in included_ids

    def test_nodes_summary_reconciled_after_shrink(self):
        """INV-pazur: after the post-selection shrink loop prunes nodes/edges to fit the
        budget, nodes_summary must report the POST-shrink reality — included.count ==
        len(nodes) and included_edges_count == len(edges) — not the pre-shrink selection.

        Two oversized signatures make each node cost ~3000 tokens, far above the loop's
        250-token/node estimate, so the assembled tier blows the 4k budget and the shrink
        loop fires, evicting a node. Before the fix nodes_summary kept the pre-shrink
        counts (2 nodes / 1 edge) while the on-disk arrays held 1 node / 0 edges.
        """
        big = "x" * 12000  # ~3000 tokens once serialized -> forces the shrink loop
        a = make_symbol("alpha", path="src/a.py")
        b = make_symbol("beta", path="src/b.py")
        a.signature = big
        b.signature = big
        edges = [make_edge(a.id, b.id)]
        behavior_map = {
            "nodes": [a.to_dict(), b.to_dict()],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        result = format_tiered_behavior_map(
            behavior_map, [a, b], edges, target_tokens=4000,
            force_include_entrypoints=False,
        )

        # Precondition: the shrink loop actually fired (two oversized nodes cannot both
        # fit in 4k). If this ever fails the test is no longer exercising the bug.
        assert len(result["nodes"]) < 2, "shrink loop must prune at least one node"

        summary = result["nodes_summary"]
        assert summary["included"]["count"] == len(result["nodes"])
        assert summary["included_edges_count"] == len(result["edges"])
        # Evicted nodes migrate into omitted over the eligible population (here: 2 symbols).
        assert summary["included"]["count"] + summary["omitted"]["count"] == 2


class TestRecomputeViewSummary:
    """Pure-helper tests for recompute_view_summary (the INV-pazur reconciler).

    These construct view_map dicts + Symbol populations + centrality directly (no analyzer),
    so they fully cover the helper in hypergumbo-core isolation.
    """

    def test_counts_match_arrays_and_omitted_migrates(self):
        # Population of 4; the on-disk view holds 2 nodes / 1 edge (as if post-shrink).
        pop = [make_symbol(f"f{i}", path=f"src/{i}.py") for i in range(4)]
        centrality = {s.id: 0.25 for s in pop}
        kept = pop[:2]
        view_map = {
            "nodes": [s.to_dict() for s in kept],
            "edges": [make_edge(kept[0].id, kept[1].id).to_dict()],
        }

        recompute_view_summary(view_map, pop, centrality, emit_edge_count=True)

        s = view_map["nodes_summary"]
        # INV-pazur: counts read straight off the on-disk arrays.
        assert s["included"]["count"] == len(view_map["nodes"]) == 2
        assert s["included_edges_count"] == len(view_map["edges"]) == 1
        # Pruned nodes migrate into omitted; included + omitted partition the population.
        assert s["omitted"]["count"] == 2
        assert s["included"]["count"] + s["omitted"]["count"] == len(pop)
        # included_centrality 2*0.25=0.5; total 4*0.25=1.0; coverage 0.5.
        assert s["included"]["centrality_sum"] == 0.5
        assert s["included"]["coverage"] == 0.5

    def test_emit_edge_count_false_omits_key(self):
        # The coverage-shaped summary (CompactResult) carries no included_edges_count.
        pop = [make_symbol("a", path="src/a.py")]
        view_map = {"nodes": [pop[0].to_dict()], "edges": []}

        recompute_view_summary(view_map, pop, {pop[0].id: 1.0}, emit_edge_count=False)

        assert "included_edges_count" not in view_map["nodes_summary"]

    def test_all_selected_empty_omitted(self):
        # Omitted empty -> max(..., default=0.0) default path and empty distributions.
        pop = [make_symbol("a", path="src/a.py"), make_symbol("b", path="src/b.py")]
        view_map = {"nodes": [s.to_dict() for s in pop], "edges": []}

        recompute_view_summary(view_map, pop, {s.id: 0.5 for s in pop}, emit_edge_count=True)

        s = view_map["nodes_summary"]
        assert s["omitted"]["count"] == 0
        assert s["omitted"]["max_centrality"] == 0.0
        assert s["omitted"]["top_words"] == []
        assert s["included"]["count"] == 2

    def test_all_zero_centrality_no_zero_division(self):
        # total_centrality `or 1.0` guard -> coverage = 0/1 = 0.0, no ZeroDivisionError.
        pop = [make_symbol("a", path="src/a.py"), make_symbol("b", path="src/b.py")]
        view_map = {"nodes": [pop[0].to_dict()], "edges": []}

        recompute_view_summary(view_map, pop, {s.id: 0.0 for s in pop}, emit_edge_count=True)

        s = view_map["nodes_summary"]
        assert s["included"]["coverage"] == 0.0
        assert s["included"]["centrality_sum"] == 0.0

    def test_empty_nodes_degenerate(self):
        # Degenerate: everything pruned -> included.count 0, omitted = whole population.
        pop = [make_symbol("a", path="src/a.py")]
        view_map = {"nodes": [], "edges": []}

        recompute_view_summary(view_map, pop, {pop[0].id: 1.0}, emit_edge_count=True)

        s = view_map["nodes_summary"]
        assert s["included"]["count"] == 0
        assert s["included"]["centrality_sum"] == 0.0
        assert s["omitted"]["count"] == 1
        assert s["included_edges_count"] == 0


class TestGenerateTierFilename:
    """Tests for generate_tier_filename function."""

    def test_basic_json(self):
        """Generate filename for JSON file."""
        assert generate_tier_filename("hypergumbo.results.json", "4k") == \
            "hypergumbo.results.4k.json"

    def test_different_tiers(self):
        """Generate filenames for different tiers."""
        base = "output.json"
        assert generate_tier_filename(base, "4k") == "output.4k.json"
        assert generate_tier_filename(base, "16k") == "output.16k.json"
        assert generate_tier_filename(base, "64k") == "output.64k.json"

    def test_nested_path(self):
        """Handle nested paths correctly."""
        assert generate_tier_filename("/path/to/results.json", "4k") == \
            "/path/to/results.4k.json"

    def test_multiple_dots(self):
        """Handle filenames with multiple dots."""
        assert generate_tier_filename("my.results.json", "16k") == \
            "my.results.16k.json"


class TestDefaultTiers:
    """Tests for DEFAULT_TIERS constant."""

    def test_default_tiers_exist(self):
        """Default tiers are defined."""
        assert len(DEFAULT_TIERS) >= 3

    def test_default_tiers_parseable(self):
        """All default tiers can be parsed."""
        for tier in DEFAULT_TIERS:
            tokens = parse_tier_spec(tier)
            assert tokens > 0

    def test_default_tiers_ascending(self):
        """Default tiers are in ascending order."""
        parsed = [parse_tier_spec(t) for t in DEFAULT_TIERS]
        assert parsed == sorted(parsed)


class TestCharsPerToken:
    """Tests for CHARS_PER_TOKEN constant."""

    def test_reasonable_value(self):
        """CHARS_PER_TOKEN is a reasonable approximation."""
        # Typical values are 3-5 chars per token
        assert CHARS_PER_TOKEN >= 3
        assert CHARS_PER_TOKEN <= 6


class TestExcludedKinds:
    """Tests for EXCLUDED_KINDS constant."""

    def test_dependency_excluded(self):
        """Dependency kinds are excluded. Post-Phase-4b: dev-dependency
        is the ``dependency`` canonical + ``meta['dependency_scope']``
        rather than a separate kind."""
        assert "dependency" in EXCLUDED_KINDS

    def test_file_excluded(self):
        """File-level nodes are excluded."""
        assert "file" in EXCLUDED_KINDS

    def test_code_kinds_not_excluded(self):
        """Code kinds are not excluded."""
        assert "function" not in EXCLUDED_KINDS
        assert "method" not in EXCLUDED_KINDS
        assert "class" not in EXCLUDED_KINDS


class TestIsTestPath:
    """Tests for _is_test_path function."""

    def test_tests_directory(self):
        """tests/ directory is detected."""
        assert _is_test_path("/home/project/tests/test_foo.py")
        assert _is_test_path("src/tests/unit/test_bar.py")

    def test_test_directory(self):
        """test/ directory is detected."""
        assert _is_test_path("/home/project/test/foo_test.go")

    def test_dunder_tests(self):
        """__tests__/ directory is detected (Jest style)."""
        assert _is_test_path("src/__tests__/Component.test.tsx")

    def test_go_test_files(self):
        """Go test files are detected."""
        assert _is_test_path("pkg/handler_test.go")
        assert _is_test_path("internal/service_test.go")

    def test_ts_spec_files(self):
        """TypeScript spec files are detected."""
        assert _is_test_path("src/utils.spec.ts")
        assert _is_test_path("components/Button.spec.tsx")

    def test_js_test_files(self):
        """JavaScript test files are detected."""
        assert _is_test_path("src/utils.test.js")
        assert _is_test_path("lib/helper.test.jsx")

    def test_python_test_files(self):
        """Python test files are detected."""
        assert _is_test_path("tests/test_cli.py")
        assert _is_test_path("src/test_utils.py")

    def test_dts_test_files(self):
        """TypeScript definition test files are detected."""
        assert _is_test_path("types/component.test-d.ts")
        assert _is_test_path("dts-test/foo.test-d.tsx")

    def test_java_integration_test_source_set(self):
        """Gradle/Maven integration test source set detected."""
        assert _is_test_path(
            "aws/src/integration/java/org/apache/iceberg/aws/glue/TestGlueCatalogTable.java"
        )
        assert _is_test_path(
            "core/src/integration/java/org/apache/TestBase.java"
        )

    def test_production_files_not_detected(self):
        """Production files are not detected as tests."""
        assert not _is_test_path("src/app.py")
        assert not _is_test_path("lib/utils.ts")
        assert not _is_test_path("pkg/handler.go")
        assert not _is_test_path("components/Button.tsx")

    def test_integration_directory_not_test(self):
        """Non-src integration directories are not test paths."""
        # Only /src/integration/ is a test convention, not arbitrary /integration/
        assert not _is_test_path("services/integration/handler.go")
        assert not _is_test_path("api/integration/client.py")


class TestIsExamplePath:
    """Tests for _is_example_path function."""

    def test_examples_directory(self):
        """examples/ directory is detected."""
        assert _is_example_path("/home/project/examples/basic.py")
        assert _is_example_path("src/examples/demo.ts")

    def test_example_singular(self):
        """example/ directory is detected."""
        assert _is_example_path("/home/project/example/basic.py")

    def test_demos_directory(self):
        """demos/ directory is detected."""
        assert _is_example_path("/home/project/demos/showcase.py")
        assert _is_example_path("src/demos/feature.ts")

    def test_demo_singular(self):
        """demo/ directory is detected."""
        assert _is_example_path("/home/project/demo/showcase.py")

    def test_samples_directory(self):
        """samples/ directory is detected."""
        assert _is_example_path("/home/project/samples/basic.py")

    def test_sample_singular(self):
        """sample/ directory is detected."""
        assert _is_example_path("/home/project/sample/basic.py")

    def test_playground_directory(self):
        """playground/ directory is detected."""
        assert _is_example_path("src/playground/experiment.ts")

    def test_tutorials_directory(self):
        """tutorials/ directory is detected."""
        assert _is_example_path("/home/project/tutorials/getting_started.py")
        assert _is_example_path("docs/tutorial/step1.py")

    def test_production_files_not_detected(self):
        """Production files are not detected as examples."""
        assert not _is_example_path("src/app.py")
        assert not _is_example_path("lib/utils.ts")
        assert not _is_example_path("pkg/handler.go")
        assert not _is_example_path("components/Button.tsx")

    def test_case_insensitive(self):
        """Detection is case insensitive."""
        assert _is_example_path("/home/project/Examples/basic.py")
        assert _is_example_path("/home/project/EXAMPLES/demo.ts")


class TestExamplePathPatterns:
    """Tests for EXAMPLE_PATH_PATTERNS constant."""

    def test_expected_patterns(self):
        """Expected patterns are in the constant."""
        assert "/examples/" in EXAMPLE_PATH_PATTERNS
        assert "/example/" in EXAMPLE_PATH_PATTERNS
        assert "/demos/" in EXAMPLE_PATH_PATTERNS
        assert "/demo/" in EXAMPLE_PATH_PATTERNS
        assert "/samples/" in EXAMPLE_PATH_PATTERNS
        assert "/playground/" in EXAMPLE_PATH_PATTERNS


class TestSelectByTokensFiltering:
    """Tests for filtering in select_by_tokens."""

    def test_excludes_dependency_kinds(self):
        """Dependency kinds are excluded from selection."""
        dep = make_symbol("lodash", kind="dependency")
        func = make_symbol("myFunc", kind="function")

        # Both have edges to make them central
        caller = make_symbol("caller")
        edges = [
            make_edge(caller.id, dep.id),
            make_edge(caller.id, func.id),
        ]

        result = select_by_tokens([dep, func, caller], edges, target_tokens=5000)

        # Function should be included, dependency should not
        included_kinds = {s.kind for s in result.included.symbols}
        assert "function" in included_kinds
        assert "dependency" not in included_kinds

    def test_excludes_test_paths(self):
        """Symbols from test files are excluded."""
        test_sym = make_symbol("test_helper", path="tests/test_utils.py")
        prod_sym = make_symbol("real_func", path="src/utils.py")

        edges = []

        result = select_by_tokens([test_sym, prod_sym], edges, target_tokens=5000)

        # Production symbol should be included, test should not
        included_paths = {s.path for s in result.included.symbols}
        assert any("src/" in p for p in included_paths)
        assert not any("tests/" in p for p in included_paths)

    def test_exclude_tests_can_be_disabled(self):
        """exclude_tests=False includes test symbols."""
        test_sym = make_symbol("test_helper", path="tests/test_utils.py")
        prod_sym = make_symbol("real_func", path="src/utils.py")

        result = select_by_tokens(
            [test_sym, prod_sym], [],
            target_tokens=5000,
            exclude_tests=False,
        )

        # Both should be included
        included_names = {s.name for s in result.included.symbols}
        assert "test_helper" in included_names
        assert "real_func" in included_names

    def test_exclude_non_code_can_be_disabled(self):
        """exclude_non_code=False includes dependency kinds."""
        dep = make_symbol("lodash", kind="dependency")
        func = make_symbol("myFunc", kind="function")

        result = select_by_tokens(
            [dep, func], [],
            target_tokens=5000,
            exclude_non_code=False,
        )

        # Both should be included
        included_kinds = {s.kind for s in result.included.symbols}
        assert "dependency" in included_kinds
        assert "function" in included_kinds

    def test_omitted_includes_filtered_symbols(self):
        """Filtered symbols count toward omitted summary."""
        dep = make_symbol("lodash", kind="dependency")
        test_sym = make_symbol("test_helper", path="tests/test_utils.py")
        prod_sym = make_symbol("real_func", path="src/utils.py")

        result = select_by_tokens([dep, test_sym, prod_sym], [], target_tokens=5000)

        # Omitted should include both filtered symbols
        assert result.omitted.count >= 2

    def test_deduplicates_names_by_default(self):
        """Duplicate symbol names are excluded by default."""
        # Create multiple symbols with the same name from different files
        push1 = make_symbol("push", path="src/array.ts")
        push2 = make_symbol("push", path="src/collection.ts")
        push3 = make_symbol("push", path="src/stack.ts")
        unique = make_symbol("pop", path="src/array.ts")

        result = select_by_tokens(
            [push1, push2, push3, unique], [],
            target_tokens=10000,
        )

        # Only one "push" should be included
        included_names = [s.name for s in result.included.symbols]
        assert included_names.count("push") == 1
        assert "pop" in included_names

    def test_deduplication_prefers_higher_centrality(self):
        """Deduplication keeps the symbol with higher centrality."""
        # Create duplicates where one has more edges
        push_important = make_symbol("push", path="src/core.ts")
        push_minor = make_symbol("push", path="src/util.ts")
        caller = make_symbol("caller")

        # Make push_important have higher centrality
        edges = [make_edge(caller.id, push_important.id)]

        result = select_by_tokens(
            [push_important, push_minor, caller], edges,
            target_tokens=10000,
        )

        # The important push should be included
        included_paths = {s.path for s in result.included.symbols if s.name == "push"}
        assert "src/core.ts" in included_paths
        assert "src/util.ts" not in included_paths

    def test_deduplicate_names_can_be_disabled(self):
        """deduplicate_names=False includes all symbols."""
        push1 = make_symbol("push", path="src/array.ts")
        push2 = make_symbol("push", path="src/collection.ts")

        result = select_by_tokens(
            [push1, push2], [],
            target_tokens=10000,
            deduplicate_names=False,
        )

        # Both should be included
        included_names = [s.name for s in result.included.symbols]
        assert included_names.count("push") == 2

    def test_deduplication_counts_skipped_as_omitted(self):
        """Deduplicated symbols count toward omitted."""
        push1 = make_symbol("push", path="src/array.ts")
        push2 = make_symbol("push", path="src/collection.ts")
        push3 = make_symbol("push", path="src/stack.ts")

        result = select_by_tokens([push1, push2, push3], [], target_tokens=10000)

        # One included, two omitted
        assert result.included.count == 1
        assert result.omitted.count == 2

    def test_excludes_example_paths(self):
        """Symbols from example directories are excluded."""
        example_sym = make_symbol("demo_handler", path="/project/examples/basic/handler.py")
        prod_sym = make_symbol("real_handler", path="src/handlers.py")

        result = select_by_tokens([example_sym, prod_sym], [], target_tokens=5000)

        # Production symbol should be included, example should not
        included_paths = {s.path for s in result.included.symbols}
        assert any("src/" in p for p in included_paths)
        assert not any("/examples/" in p for p in included_paths)

    def test_exclude_examples_can_be_disabled(self):
        """exclude_examples=False includes example symbols."""
        example_sym = make_symbol("demo_handler", path="/project/examples/basic/handler.py")
        prod_sym = make_symbol("real_handler", path="src/handlers.py")

        result = select_by_tokens(
            [example_sym, prod_sym], [],
            target_tokens=5000,
            exclude_examples=False,
        )

        # Both should be included
        included_names = {s.name for s in result.included.symbols}
        assert "demo_handler" in included_names
        assert "real_handler" in included_names

    def test_omitted_includes_example_symbols(self):
        """Example symbols count toward omitted summary."""
        example_sym = make_symbol("demo_handler", path="/project/examples/basic/handler.py")
        prod_sym = make_symbol("real_handler", path="src/handlers.py")

        result = select_by_tokens([example_sym, prod_sym], [], target_tokens=5000)

        # Omitted should include filtered example symbol
        assert result.omitted.count >= 1



# ============================================================================
# Tests for induced subgraph fixes (edge AND filter, entrypoint filtering)
# ============================================================================


class TestInducedSubgraphEdgeFilter:
    """Tests for the edge filter using AND (both endpoints must exist)."""

    def test_edges_require_both_endpoints(self):
        """Only edges where BOTH src and dst exist are kept."""
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")

        # a->b, b->c, a->c
        edge_ab = make_edge(sym_a.id, sym_b.id)
        edge_bc = make_edge(sym_b.id, sym_c.id)
        edge_ac = make_edge(sym_a.id, sym_c.id)

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [edge_ab.to_dict(), edge_bc.to_dict(), edge_ac.to_dict()],
            "entrypoints": [],
        }

        # Only include a and b (not c)
        config = CompactConfig(min_symbols=2, max_symbols=2)
        result = format_compact_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], [edge_ab, edge_bc, edge_ac], config,
            force_include_entrypoints=False,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # Only edge a->b should be kept (both endpoints exist)
        # Edges b->c and a->c should NOT be kept (c doesn't exist)
        for edge in result["edges"]:
            assert edge["src"] in included_ids, f"Edge src {edge['src']} not in included nodes"
            assert edge["dst"] in included_ids, f"Edge dst {edge['dst']} not in included nodes"

    def test_no_dangling_edges_in_compact(self):
        """Compact output should have no dangling edge references."""
        # Create a hub-spoke pattern where hub has many callers
        hub = make_symbol("hub")
        spokes = [make_symbol(f"spoke_{i}") for i in range(10)]
        edges = [make_edge(spoke.id, hub.id) for spoke in spokes]

        behavior_map = {
            "nodes": [hub.to_dict()] + [s.to_dict() for s in spokes],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        # Only include hub (max 1 symbol)
        config = CompactConfig(min_symbols=1, max_symbols=1)
        result = format_compact_behavior_map(
            behavior_map, [hub] + spokes, edges, config,
            force_include_entrypoints=False,
        )

        # With only hub included, NO edges should exist
        # (all edges are spoke->hub, but spokes aren't included)
        assert len(result["edges"]) == 0, "Should have no edges when only one endpoint exists"


class TestEntrypointFiltering:
    """Tests for entrypoint filtering to only resolvable symbol_ids."""

    def test_entrypoints_filtered_to_included_nodes(self):
        """Only entrypoints with symbol_id in included nodes are kept."""
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [],
            "entrypoints": [
                {"symbol_id": sym_a.id, "kind": "http_route", "confidence": 0.9},
                {"symbol_id": sym_b.id, "kind": "cli_command", "confidence": 0.9},
                {"symbol_id": sym_c.id, "kind": "main_function", "confidence": 0.8},
            ],
        }

        # Only include a and b (not c)
        config = CompactConfig(min_symbols=2, max_symbols=2)
        result = format_compact_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], [], config,
            force_include_entrypoints=False,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # Only entrypoints for included nodes should remain
        for ep in result["entrypoints"]:
            assert ep["symbol_id"] in included_ids, \
                f"Entrypoint {ep['symbol_id']} references non-existent node"

    def test_no_dangling_entrypoints(self):
        """Compact output should have no entrypoints referencing missing nodes."""
        # Create many entrypoints, but compact to few nodes
        symbols = [make_symbol(f"sym_{i}") for i in range(20)]
        entrypoints = [
            {"symbol_id": s.id, "kind": "main_function", "confidence": 0.8}
            for s in symbols
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [],
            "entrypoints": entrypoints,
        }

        # Only include 5 symbols
        config = CompactConfig(min_symbols=1, max_symbols=5)
        result = format_compact_behavior_map(
            behavior_map, symbols, [], config,
            force_include_entrypoints=False,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # All remaining entrypoints should reference existing nodes
        assert len(result["entrypoints"]) <= len(included_ids)
        for ep in result["entrypoints"]:
            assert ep["symbol_id"] in included_ids


class TestFeatureReprojection:
    """Tests for feature re-projection onto the compacted graph (INV-titid).

    The compact pass selects a budget-limited subset of nodes/edges. Feature
    slices in the source behavior map carry full-graph node/edge references;
    without re-projection the great majority of those references dangle in the
    compact output (the feature claims to describe a slice of the compact
    graph but points at pruned content). These tests pin the twin contract to
    INV-tarol's slice fix: a compact feature must be self-contained — every id
    it references exists in the compact's own nodes/edges — and a feature whose
    anchor was pruned is dropped, mirroring entrypoint filtering.
    """

    @pytest.mark.parametrize("connectivity_aware", [False, True])
    def test_feature_refs_have_no_dangling_after_compaction(
        self, connectivity_aware
    ):
        """No feature ref points at content absent from the compact output."""
        symbols = [make_symbol(f"sym_{i}") for i in range(10)]
        edges = [
            make_edge(symbols[i].id, symbols[i + 1].id) for i in range(4)
        ]
        feature = {
            "id": "sha256:feat0",
            "name": "feat0",
            "entry_nodes": [symbols[0].id],
            "node_ids": [s.id for s in symbols[:5]],
            "edge_ids": [e.id for e in edges],
            "node_depths": {s.id: i for i, s in enumerate(symbols[:5])},
            "node_tiers": {s.id: 1 for s in symbols[:5]},
        }
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [
                {"symbol_id": symbols[0].id, "kind": "main_function",
                 "confidence": 0.9}
            ],
            "features": [feature],
        }
        config = CompactConfig(min_symbols=1, max_symbols=3)
        result = format_compact_behavior_map(
            behavior_map, symbols, edges, config,
            connectivity_aware=connectivity_aware,
        )

        node_ids = {n["id"] for n in result["nodes"]}
        edge_ids = {e["id"] for e in result["edges"]}
        # The feature is anchored at a force-included entrypoint, so it
        # survives -- but every reference it carries must be present.
        assert result["features"], "feature anchored on included node was dropped"
        for feat in result["features"]:
            for nid in feat.get("entry_nodes", []):
                assert nid in node_ids, f"dangling entry_node {nid}"
            for nid in feat.get("node_ids", []):
                assert nid in node_ids, f"dangling node_id {nid}"
            for eid in feat.get("edge_ids", []):
                assert eid in edge_ids, f"dangling edge_id {eid}"
            for nid in feat.get("node_depths", {}):
                assert nid in node_ids, f"dangling node_depths key {nid}"
            for nid in feat.get("node_tiers", {}):
                assert nid in node_ids, f"dangling node_tiers key {nid}"

    def test_feature_dropped_when_all_entry_nodes_pruned(self):
        """A feature whose every entry node was pruned is removed entirely."""
        symbols = [make_symbol(f"sym_{i}") for i in range(5)]
        ghost_id = "python:gone.py:1-10:function:ghost"
        feature = {
            "id": "sha256:ghost",
            "name": "ghost",
            "entry_nodes": [ghost_id],
            "node_ids": [ghost_id],
            "edge_ids": [],
        }
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [],
            "entrypoints": [],
            "features": [feature],
        }
        config = CompactConfig(min_symbols=1, max_symbols=3)
        result = format_compact_behavior_map(
            behavior_map, symbols, [], config,
            force_include_entrypoints=False,
        )

        assert all(f["name"] != "ghost" for f in result["features"]), \
            "feature with no surviving entry node should be dropped"

    def test_feature_kept_when_entry_node_survives_and_refs_filtered(self):
        """A surviving feature keeps in-set refs and drops pruned ones."""
        anchor = make_symbol("anchor")
        other = make_symbol("other")
        edge_ao = make_edge(anchor.id, other.id)
        ghost_id = "python:gone.py:1-10:function:ghost"
        ghost_edge_id = f"edge:{ghost_id}->{anchor.id}"
        feature = {
            "id": "sha256:anchored",
            "name": "anchored",
            "entry_nodes": [anchor.id],
            "node_ids": [anchor.id, other.id, ghost_id],
            "edge_ids": [edge_ao.id, ghost_edge_id],
        }
        behavior_map = {
            "nodes": [anchor.to_dict(), other.to_dict()],
            "edges": [edge_ao.to_dict()],
            "entrypoints": [
                {"symbol_id": anchor.id, "kind": "main_function",
                 "confidence": 0.9},
                {"symbol_id": other.id, "kind": "main_function",
                 "confidence": 0.9},
            ],
            "features": [feature],
        }
        # max_symbols=4 leaves room for both force-included entrypoints
        # (the forced cap is max_symbols//2 == 2).
        config = CompactConfig(min_symbols=1, max_symbols=4)
        result = format_compact_behavior_map(
            behavior_map, [anchor, other], [edge_ao], config,
        )

        kept = [f for f in result["features"] if f["name"] == "anchored"]
        assert kept, "feature anchored on an included node should survive"
        feat = kept[0]
        # Phantom refs are gone; the real edge between two included nodes
        # is retained.
        assert ghost_id not in feat["node_ids"]
        assert ghost_edge_id not in feat["edge_ids"]
        assert anchor.id in feat["entry_nodes"]
        assert edge_ao.id in feat["edge_ids"]


class TestForceIncludeEntrypoints:
    """Tests for force-including entrypoints in selection."""

    def test_entrypoints_force_included(self):
        """Entrypoint symbols are always included when force_include_entrypoints=True."""
        # Create symbols where entrypoint has low centrality
        entrypoint_sym = make_symbol("main")
        high_centrality_sym = make_symbol("utils")
        caller = make_symbol("caller")

        # Make utils have higher centrality
        edges = [make_edge(caller.id, high_centrality_sym.id)]

        behavior_map = {
            "nodes": [s.to_dict() for s in [entrypoint_sym, high_centrality_sym, caller]],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [
                {"symbol_id": entrypoint_sym.id, "kind": "main_function", "confidence": 0.8},
            ],
        }

        # With max_symbols=1 and force_include=True, entrypoint should be included
        config = CompactConfig(min_symbols=1, max_symbols=1)
        result = format_compact_behavior_map(
            behavior_map,
            [entrypoint_sym, high_centrality_sym, caller],
            edges,
            config,
            force_include_entrypoints=True,
        )

        included_ids = {n["id"] for n in result["nodes"]}
        assert entrypoint_sym.id in included_ids, "Entrypoint should be force-included"

    def test_entrypoints_not_force_included_when_disabled(self):
        """Entrypoints follow normal selection when force_include_entrypoints=False."""
        # Create symbols where entrypoint has low centrality
        entrypoint_sym = make_symbol("main")
        high_centrality_sym = make_symbol("utils")
        caller = make_symbol("caller")

        # Make utils have higher centrality
        edges = [make_edge(caller.id, high_centrality_sym.id)]

        behavior_map = {
            "nodes": [s.to_dict() for s in [entrypoint_sym, high_centrality_sym, caller]],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [
                {"symbol_id": entrypoint_sym.id, "kind": "main_function", "confidence": 0.8},
            ],
        }

        # With max_symbols=1 and force_include=False, high centrality sym should win
        config = CompactConfig(min_symbols=1, max_symbols=1)
        result = format_compact_behavior_map(
            behavior_map,
            [entrypoint_sym, high_centrality_sym, caller],
            edges,
            config,
            force_include_entrypoints=False,
        )

        included_ids = {n["id"] for n in result["nodes"]}
        # High centrality symbol should be selected over low-centrality entrypoint
        assert high_centrality_sym.id in included_ids

    def test_all_entrypoints_preserved_when_within_budget(self):
        """All entrypoints are included if budget allows."""
        # Create 5 entrypoint symbols
        entrypoint_syms = [make_symbol(f"main_{i}") for i in range(5)]
        other_syms = [make_symbol(f"helper_{i}") for i in range(10)]

        behavior_map = {
            "nodes": [s.to_dict() for s in entrypoint_syms + other_syms],
            "edges": [],
            "entrypoints": [
                {"symbol_id": s.id, "kind": "main_function", "confidence": 0.8}
                for s in entrypoint_syms
            ],
        }

        # Budget of 10 should include all 5 entrypoints + 5 others
        config = CompactConfig(min_symbols=10, max_symbols=10)
        result = format_compact_behavior_map(
            behavior_map,
            entrypoint_syms + other_syms,
            [],
            config,
            force_include_entrypoints=True,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # All entrypoints should be included
        for ep_sym in entrypoint_syms:
            assert ep_sym.id in included_ids, f"Entrypoint {ep_sym.name} should be included"

    def test_entrypoints_capped_when_exceeding_budget(self):
        """When entrypoints far exceed max_symbols, they are capped aggressively
        to leave room for bridge nodes."""
        # Create 20 entrypoint symbols (simulating many main() functions)
        entrypoint_syms = [make_symbol(f"main_{i}") for i in range(20)]
        helper_syms = [make_symbol(f"helper_{i}") for i in range(30)]

        # Set confidence so main_0 through main_2 have highest confidence
        behavior_map = {
            "nodes": [s.to_dict() for s in entrypoint_syms + helper_syms],
            "edges": [],
            "entrypoints": [
                {"symbol_id": s.id, "kind": "main_function", "confidence": 0.9 - i * 0.01}
                for i, s in enumerate(entrypoint_syms)
            ],
        }

        # With max_symbols=10 and 20 entrypoints (> max_symbols), adaptive cap
        # kicks in: max_symbols // 3 = 3 forced entrypoints.
        config = CompactConfig(min_symbols=10, max_symbols=10)
        result = format_compact_behavior_map(
            behavior_map,
            entrypoint_syms + helper_syms,
            [],
            config,
            force_include_entrypoints=True,
        )

        included_ids = {n["id"] for n in result["nodes"]}
        entrypoints_included = [s for s in entrypoint_syms if s.id in included_ids]

        # Should have capped entrypoints to 3 (max_symbols // 3) since
        # 20 entrypoints > max_symbols (10)
        assert len(entrypoints_included) <= 5, (
            f"Expected at most 5 entrypoints, got {len(entrypoints_included)}"
        )

        # The highest-confidence entrypoints should be included (main_0 through main_2)
        for i in range(3):
            assert entrypoint_syms[i].id in included_ids, (
                f"Entrypoint main_{i} (high confidence) should be included"
            )


class TestSelectByCoverageForceInclude:
    """Tests for force_include_ids parameter in select_by_coverage."""

    def test_force_include_ids_respected(self):
        """Symbols in force_include_ids are always included."""
        symbols = [make_symbol(f"sym_{i}") for i in range(10)]
        force_ids = {symbols[7].id, symbols[9].id}  # Force include last two

        config = CompactConfig(min_symbols=2, max_symbols=5)
        result = select_by_coverage(symbols, [], config, force_include_ids=force_ids)

        included_ids = {s.id for s in result.included.symbols}

        # Force-included symbols should be present
        assert symbols[7].id in included_ids
        assert symbols[9].id in included_ids

    def test_force_include_fills_remaining_budget(self):
        """After force-including, remaining budget is filled with centrality-ranked symbols."""
        low_centrality = make_symbol("low")
        high_centrality = make_symbol("high")
        caller = make_symbol("caller")

        # Make high have higher centrality
        edges = [make_edge(caller.id, high_centrality.id)]

        # Force include low, then fill with high-centrality
        force_ids = {low_centrality.id}
        config = CompactConfig(min_symbols=2, max_symbols=3)
        result = select_by_coverage(
            [low_centrality, high_centrality, caller], edges, config,
            force_include_ids=force_ids,
        )

        included_ids = {s.id for s in result.included.symbols}

        # Both should be included: low (forced) and high (centrality)
        assert low_centrality.id in included_ids
        assert high_centrality.id in included_ids

    def test_force_include_skips_in_centrality_loop(self):
        """Force-included symbols are skipped when iterating by centrality."""
        # This tests the 'continue' branch on line 810-812
        # We need force-included symbols to appear in sorted_symbols too
        # Use language_proportional=False so all symbols are in sorted_symbols
        symbols = [make_symbol(f"sym_{i}") for i in range(5)]

        # Create edges so sym_0 has high centrality
        edges = [make_edge(symbols[i].id, symbols[0].id) for i in range(1, 5)]

        # Force include sym_0 (which also has high centrality)
        # When iterating sorted_symbols, sym_0 will be first but already included
        force_ids = {symbols[0].id}
        config = CompactConfig(
            min_symbols=2, max_symbols=3,
            language_proportional=False,  # Ensures all symbols in sorted_symbols
        )
        result = select_by_coverage(
            symbols, edges, config, force_include_ids=force_ids
        )

        included_ids = {s.id for s in result.included.symbols}

        # sym_0 should be included (forced)
        assert symbols[0].id in included_ids
        # Other symbols should be included based on remaining budget
        assert len(included_ids) >= 2


class TestSelectByTokensForceInclude:
    """Tests for force_include_ids parameter in select_by_tokens."""

    def test_force_include_ids_respected(self):
        """Symbols in force_include_ids are always included."""
        symbols = [make_symbol(f"sym_{i}") for i in range(10)]
        force_ids = {symbols[0].id, symbols[1].id}

        result = select_by_tokens(
            symbols, [], target_tokens=10000,
            force_include_ids=force_ids,
        )

        included_ids = {s.id for s in result.included.symbols}

        # Force-included symbols should be present
        assert symbols[0].id in included_ids
        assert symbols[1].id in included_ids


class TestTieredBehaviorMapInducedSubgraph:
    """Tests for induced subgraph in tiered behavior maps."""

    def test_tiered_edges_require_both_endpoints(self):
        """Tiered output only keeps edges where both endpoints exist."""
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")

        edge_ab = make_edge(sym_a.id, sym_b.id)
        edge_bc = make_edge(sym_b.id, sym_c.id)

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [edge_ab.to_dict(), edge_bc.to_dict()],
            "entrypoints": [],
        }

        # Small token budget to force truncation
        result = format_tiered_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], [edge_ab, edge_bc],
            target_tokens=500,
            force_include_entrypoints=False,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # All remaining edges should have both endpoints in included set
        for edge in result["edges"]:
            assert edge["src"] in included_ids
            assert edge["dst"] in included_ids

    def test_tiered_entrypoints_filtered(self):
        """Tiered output filters entrypoints to only resolvable ones."""
        symbols = [make_symbol(f"sym_{i}") for i in range(10)]
        entrypoints = [
            {"symbol_id": s.id, "kind": "main_function", "confidence": 0.8}
            for s in symbols
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [],
            "entrypoints": entrypoints,
        }

        # Small budget to force truncation
        result = format_tiered_behavior_map(
            behavior_map, symbols, [],
            target_tokens=500,
            force_include_entrypoints=False,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # All remaining entrypoints should reference existing nodes
        for ep in result["entrypoints"]:
            assert ep["symbol_id"] in included_ids

    def test_tiered_force_include_entrypoints(self):
        """Tiered output with force_include_entrypoints=True includes entrypoints."""
        # Create symbols where entrypoints have low centrality
        hub = make_symbol("hub")
        entrypoint_syms = [make_symbol(f"entry_{i}") for i in range(3)]
        all_symbols = [hub] + entrypoint_syms

        # Hub has high centrality (called by entrypoints)
        edges = [make_edge(ep.id, hub.id) for ep in entrypoint_syms]

        entrypoints = [
            {"symbol_id": ep.id, "kind": "main_function", "confidence": 0.9}
            for ep in entrypoint_syms
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": entrypoints,
        }

        # With force_include_entrypoints=True (default), entrypoints should be included
        result = format_tiered_behavior_map(
            behavior_map, all_symbols, edges,
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # All entrypoints should be included
        for ep_sym in entrypoint_syms:
            assert ep_sym.id in included_ids


class TestUnionFind:
    """Tests for Union-Find data structure used in connectivity selection."""

    def test_init_single_element(self):
        """Each element starts in its own component."""
        from hypergumbo_core.compact import UnionFind

        uf = UnionFind(["a", "b", "c"])
        assert uf.find("a") != uf.find("b")
        assert uf.find("b") != uf.find("c")
        assert uf.component_size("a") == 1
        assert uf.component_size("b") == 1

    def test_union_merges_components(self):
        """Union merges two components."""
        from hypergumbo_core.compact import UnionFind

        uf = UnionFind(["a", "b", "c"])
        uf.union("a", "b")
        assert uf.find("a") == uf.find("b")
        assert uf.find("a") != uf.find("c")
        assert uf.component_size("a") == 2
        assert uf.component_size("b") == 2

    def test_union_chain(self):
        """Chained unions form single component."""
        from hypergumbo_core.compact import UnionFind

        uf = UnionFind(["a", "b", "c", "d"])
        uf.union("a", "b")
        uf.union("b", "c")
        uf.union("c", "d")
        assert uf.find("a") == uf.find("d")
        assert uf.component_size("a") == 4

    def test_largest_component_size(self):
        """Tracks largest component correctly."""
        from hypergumbo_core.compact import UnionFind

        uf = UnionFind(["a", "b", "c", "d", "e"])
        assert uf.largest_component_size() == 1  # All singletons

        uf.union("a", "b")
        assert uf.largest_component_size() == 2

        uf.union("c", "d")
        assert uf.largest_component_size() == 2  # Two size-2 components

        uf.union("a", "c")  # Merge to size-4
        assert uf.largest_component_size() == 4

    def test_add_element(self):
        """Can add elements after initialization."""
        from hypergumbo_core.compact import UnionFind

        uf = UnionFind(["a"])
        uf.add("b")
        assert uf.component_size("b") == 1
        uf.union("a", "b")
        assert uf.component_size("a") == 2


class TestConnectivityAwareSelection:
    """Tests for connectivity-aware symbol selection."""

    def test_bridges_preferred_over_leaves(self):
        """Nodes that bridge components are selected before leaves."""
        from hypergumbo_core.compact import select_by_connectivity

        # Graph: A -- B -- C -- D
        #             |
        #             E
        # If we seed with {A, D} (disconnected), B and C bridge them
        # E is a leaf off B

        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")
        sym_d = make_symbol("d")
        sym_e = make_symbol("e")
        symbols = [sym_a, sym_b, sym_c, sym_d, sym_e]

        edges = [
            make_edge(sym_a.id, sym_b.id),
            make_edge(sym_b.id, sym_c.id),
            make_edge(sym_c.id, sym_d.id),
            make_edge(sym_b.id, sym_e.id),
        ]

        # Seed with A and D (two isolated nodes)
        seed_ids = {sym_a.id, sym_d.id}

        # Budget for 2 more nodes
        result = select_by_connectivity(
            symbols, edges, seed_ids, max_additional=2
        )

        selected_ids = {s.id for s in result.included.symbols}

        # Should have selected B and C to bridge A-D, not E
        assert sym_a.id in selected_ids
        assert sym_d.id in selected_ids
        assert sym_b.id in selected_ids or sym_c.id in selected_ids
        # E should not be selected (leaf, doesn't help connectivity)
        # Unless tie-breaking picks it, which is fine

    def test_component_merge_scoring(self):
        """Merging larger components scores higher than merging smaller ones."""
        from hypergumbo_core.compact import select_by_connectivity

        # Two clusters:
        # Cluster 1: A-B-C (size 3)
        # Cluster 2: D-E (size 2)
        # Bridge F connects both
        # Leaf G connects only to A

        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")
        sym_d = make_symbol("d")
        sym_e = make_symbol("e")
        sym_f = make_symbol("f")  # Bridge
        sym_g = make_symbol("g")  # Leaf
        symbols = [sym_a, sym_b, sym_c, sym_d, sym_e, sym_f, sym_g]

        edges = [
            # Cluster 1
            make_edge(sym_a.id, sym_b.id),
            make_edge(sym_b.id, sym_c.id),
            # Cluster 2
            make_edge(sym_d.id, sym_e.id),
            # Bridge F
            make_edge(sym_c.id, sym_f.id),
            make_edge(sym_f.id, sym_d.id),
            # Leaf G
            make_edge(sym_a.id, sym_g.id),
        ]

        # Seed with A, B, C, D, E (two clusters)
        seed_ids = {sym_a.id, sym_b.id, sym_c.id, sym_d.id, sym_e.id}

        # Budget for 1 more node
        result = select_by_connectivity(
            symbols, edges, seed_ids, max_additional=1
        )

        selected_ids = {s.id for s in result.included.symbols}

        # F bridges the clusters (merge 3+2=5), G just adds 1 to cluster 1
        # So F should be selected
        assert sym_f.id in selected_ids
        assert sym_g.id not in selected_ids

    def test_empty_seed_builds_from_centrality(self):
        """With empty seed, falls back to centrality for initial selection."""
        from hypergumbo_core.compact import select_by_connectivity

        sym_a = make_symbol("a")
        sym_b = make_symbol("b")  # Hub
        sym_c = make_symbol("c")
        sym_d = make_symbol("d")
        symbols = [sym_a, sym_b, sym_c, sym_d]

        # B is the hub
        edges = [
            make_edge(sym_a.id, sym_b.id),
            make_edge(sym_b.id, sym_c.id),
            make_edge(sym_b.id, sym_d.id),
        ]

        result = select_by_connectivity(
            symbols, edges, seed_ids=set(), max_additional=2
        )

        selected_ids = {s.id for s in result.included.symbols}

        # B should be selected (highest centrality)
        assert sym_b.id in selected_ids

    def test_respects_max_additional_budget(self):
        """Stops after max_additional nodes are added."""
        from hypergumbo_core.compact import select_by_connectivity

        symbols = [make_symbol(f"s{i}") for i in range(10)]
        # Chain: s0 - s1 - s2 - ... - s9
        edges = [make_edge(symbols[i].id, symbols[i + 1].id) for i in range(9)]

        seed_ids = {symbols[0].id}

        result = select_by_connectivity(
            symbols, edges, seed_ids, max_additional=3
        )

        # Should have seed + 3 = 4 nodes
        assert len(result.included.symbols) == 4

    def test_disconnected_seeds_get_connected(self):
        """Previously-disconnected seeds become connected."""
        from hypergumbo_core.compact import select_by_connectivity

        # Django-like scenario: multiple entrypoints with shared utilities
        cmd1 = make_symbol("Command1", kind="class")
        cmd2 = make_symbol("Command2", kind="class")
        cmd3 = make_symbol("Command3", kind="class")
        util = make_symbol("mark_safe", kind="function")  # Shared utility

        symbols = [cmd1, cmd2, cmd3, util]

        edges = [
            make_edge(cmd1.id, util.id),
            make_edge(cmd2.id, util.id),
            make_edge(cmd3.id, util.id),
        ]

        # Seed with commands (disconnected)
        seed_ids = {cmd1.id, cmd2.id, cmd3.id}

        result = select_by_connectivity(
            symbols, edges, seed_ids, max_additional=1
        )

        selected_ids = {s.id for s in result.included.symbols}

        # util should be selected (connects all three)
        assert util.id in selected_ids

        # Verify edges exist in result (induced subgraph)
        result_edge_pairs = {(e.src, e.dst) for e in result.included_edges}
        assert (cmd1.id, util.id) in result_edge_pairs
        assert (cmd2.id, util.id) in result_edge_pairs
        assert (cmd3.id, util.id) in result_edge_pairs

    def test_returns_induced_subgraph_edges(self):
        """Result includes only edges where both endpoints are selected."""
        from hypergumbo_core.compact import select_by_connectivity

        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")  # Not selected
        symbols = [sym_a, sym_b, sym_c]

        edges = [
            make_edge(sym_a.id, sym_b.id),  # Both in
            make_edge(sym_b.id, sym_c.id),  # C not in
        ]

        seed_ids = {sym_a.id, sym_b.id}

        result = select_by_connectivity(
            symbols, edges, seed_ids, max_additional=0
        )

        # Only a->b edge should be in result
        assert len(result.included_edges) == 1
        assert result.included_edges[0].src == sym_a.id
        assert result.included_edges[0].dst == sym_b.id

    def test_preserves_parallel_edges(self):
        """WI-hakom: parallel edges between the same node pair are ALL retained.

        The induced subgraph must be derived from the edge LIST, not a
        (src, dst)-keyed dict — the latter collapses parallel edges (e.g. a
        ``calls`` and a ``references`` edge between the same two symbols),
        dropping every parallel but the last.
        """
        from hypergumbo_core.compact import select_by_connectivity

        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        symbols = [sym_a, sym_b]

        e_calls = make_edge(sym_a.id, sym_b.id, edge_type="calls")
        e_calls.id = "edge:a->b:calls"
        e_refs = make_edge(sym_a.id, sym_b.id, edge_type="references")
        e_refs.id = "edge:a->b:references"
        edges = [e_calls, e_refs]

        result = select_by_connectivity(
            symbols, edges, {sym_a.id, sym_b.id}, max_additional=0
        )

        # Both parallel edges are in the induced subgraph.
        assert len(result.included_edges) == 2
        assert {e.id for e in result.included_edges} == {
            "edge:a->b:calls", "edge:a->b:references"
        }

    def test_frontier_expands_via_incoming_edges(self):
        """Frontier includes nodes that have incoming edges to selected nodes."""
        from hypergumbo_core.compact import select_by_connectivity

        # Graph: A <- B <- C (B calls A, C calls B)
        # Seed with {A}, then B should be added (A's caller)
        # Then C should be added (B's caller)
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")
        sym_d = make_symbol("d")  # Unconnected
        symbols = [sym_a, sym_b, sym_c, sym_d]

        edges = [
            make_edge(sym_b.id, sym_a.id),  # B calls A
            make_edge(sym_c.id, sym_b.id),  # C calls B
        ]

        seed_ids = {sym_a.id}

        result = select_by_connectivity(
            symbols, edges, seed_ids, max_additional=2
        )

        selected_ids = {s.id for s in result.included.symbols}

        # A (seed), B (calls A), C (calls B) should be selected
        assert sym_a.id in selected_ids
        assert sym_b.id in selected_ids
        assert sym_c.id in selected_ids
        # D is unconnected, should not be selected
        assert sym_d.id not in selected_ids

    def test_self_loop_edges_excluded_from_adjacency(self):
        """Self-loop edges are ignored in connectivity-aware selection.

        Self-loops (src == dst) should not inflate a node's connectivity
        score or appear in the induced subgraph edges.
        """
        from hypergumbo_core.compact import select_by_connectivity

        sym_a = make_symbol("visitor")
        sym_b = make_symbol("handler")
        sym_c = make_symbol("caller")
        symbols = [sym_a, sym_b, sym_c]

        edges = [
            make_edge(sym_c.id, sym_a.id),
            make_edge(sym_c.id, sym_b.id),
            # Self-loop on visitor
            make_edge(sym_a.id, sym_a.id),
        ]

        result = select_by_connectivity(
            symbols, edges, seed_ids={sym_c.id}, max_additional=5
        )

        # Self-loop should not appear in induced edges
        for e in result.included_edges:
            assert e.src != e.dst, (
                f"Self-loop in induced edges: {e.src} -> {e.dst}"
            )


class TestSelectByConnectivityDampening:
    """Tests for the WI-lidum dampener-stack backfill in select_by_connectivity.

    With empty seed_ids, the function selects the highest-centrality
    symbol as the starting node (compact.py line ~620 fallback). Its
    internally-computed centrality previously skipped all 8 dampeners
    that rank_symbols applies — so a high-raw-centrality logger / file-
    kind / generated symbol would dominate the seed pick, propagating
    into the connectivity expansion. Patch wires the full dampener
    stack into the centrality-is-None branch.
    """

    def test_utility_dampener_applied_to_internal_centrality(self):
        """Empty-seeds seed pick uses utility-dampened centrality."""
        from hypergumbo_core.compact import select_by_connectivity

        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        logger_sym = Symbol(
            id="logger", name="Logger.error", kind="method", language="python",
            path="src/observability/logger.py", span=long_span,
        )
        logger_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="process_payment", kind="function", language="python",
            path="src/payments/processor.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "logger") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        result = select_by_connectivity(
            [logger_sym, domain_sym], edges, seed_ids=set(), max_additional=0,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids and "logger" not in included_ids, (
            "Expected process_payment as seed pick after utility dampening; "
            f"got included={included_ids}"
        )

    def test_trivial_sink_dampener_applied_to_internal_centrality(self):
        """Empty-seeds seed pick uses trivial-sink-dampened centrality."""
        from hypergumbo_core.compact import select_by_connectivity

        short_span = Span(start_line=1, end_line=5, start_col=0, end_col=0)
        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        sink_sym = Symbol(
            id="sink", name="get_status", kind="function", language="python",
            path="src/util/status.py", span=short_span,
        )
        sink_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="reconcile_ledger", kind="function",
            language="python", path="src/finance/reconcile.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "sink") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        result = select_by_connectivity(
            [sink_sym, domain_sym], edges, seed_ids=set(), max_additional=0,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids and "sink" not in included_ids, (
            f"Expected reconcile_ledger; got included={included_ids}"
        )

    def test_generated_dampener_applied_to_internal_centrality(self):
        """Empty-seeds seed pick uses generated-code-dampened centrality."""
        from hypergumbo_core.compact import select_by_connectivity

        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        generated_sym = Symbol(
            id="generated", name="V1beta1InferenceService", kind="class",
            language="python",
            path="kserve/models/v1beta1_inference_service.py", span=long_span,
        )
        generated_sym.supply_chain_tier = 1
        generated_sym.is_generated_file = True
        domain_sym = Symbol(
            id="domain", name="InferenceService", kind="class", language="python",
            path="kserve/api/inference_service.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "generated") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        result = select_by_connectivity(
            [generated_sym, domain_sym], edges, seed_ids=set(), max_additional=0,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids and "generated" not in included_ids, (
            f"Expected InferenceService; got included={included_ids}"
        )

    def test_file_kind_dampener_applied_to_internal_centrality(self):
        """Empty-seeds seed pick uses file-kind-suppressed centrality."""
        from hypergumbo_core.compact import select_by_connectivity

        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        file_sym = Symbol(
            id="file_sym", name="cmd/main.go", kind="file", language="go",
            path="cmd/main.go", span=long_span,
        )
        file_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="ServeRequest", kind="function", language="go",
            path="server/server.go", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "file_sym") for i in range(20)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        result = select_by_connectivity(
            [file_sym, domain_sym], edges, seed_ids=set(), max_additional=0,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids and "file_sym" not in included_ids, (
            f"Expected ServeRequest; got included={included_ids}"
        )

    def test_noise_dampener_applied_to_internal_centrality(self):
        """Empty-seeds seed pick uses noise-dampened centrality."""
        from hypergumbo_core.compact import select_by_connectivity

        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        migration_sym = Symbol(
            id="migration", name="ModelState", kind="class", language="python",
            path="django/db/migrations/state.py", span=long_span,
        )
        migration_sym.supply_chain_tier = 1
        domain_sym = Symbol(
            id="domain", name="DomainModel", kind="class", language="python",
            path="app/domain.py", span=long_span,
        )
        domain_sym.supply_chain_tier = 1
        edges = [
            make_edge(f"caller{i}", "migration") for i in range(15)
        ] + [
            make_edge(f"d_caller{i}", "domain") for i in range(3)
        ]
        result = select_by_connectivity(
            [migration_sym, domain_sym], edges, seed_ids=set(), max_additional=0,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "domain" in included_ids and "migration" not in included_ids, (
            f"Expected DomainModel; got included={included_ids}"
        )

    def test_centrality_params_match_rank_symbols(self):
        """select_by_connectivity's internal compute_centrality passes
        rank_symbols' tuned parameters (WI-dohaf): hub_threshold=100,
        within_file_weight=0.3, max_per_file_in=5,
        edge_type_weights=DEFAULT_EDGE_TYPE_WEIGHTS.

        Tests max_per_file_in=5 effect on the empty-seeds seed pick.
        """
        from hypergumbo_core.compact import select_by_connectivity

        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        target_a = Symbol(
            id="target_a", name="hot_helper", kind="function", language="python",
            path="src/helper.py", span=long_span,
        )
        target_a.supply_chain_tier = 1
        target_b = Symbol(
            id="target_b", name="distributed_callee", kind="function",
            language="python", path="src/callee.py", span=long_span,
        )
        target_b.supply_chain_tier = 1
        a_callers = []
        for i in range(30):
            c = Symbol(
                id=f"a_caller{i}", name=f"call_a_{i}", kind="function",
                language="python", path="src/single_caller.py",
                span=long_span,
            )
            c.supply_chain_tier = 1
            a_callers.append(c)
        b_callers = []
        for i in range(6):
            c = Symbol(
                id=f"b_caller{i}", name=f"call_b_{i}", kind="function",
                language="python", path=f"src/file_b_{i}.py",
                span=long_span,
            )
            c.supply_chain_tier = 1
            b_callers.append(c)
        edges = [
            make_edge(c.id, "target_a") for c in a_callers
        ] + [
            make_edge(c.id, "target_b") for c in b_callers
        ]
        result = select_by_connectivity(
            [target_a, target_b] + a_callers + b_callers, edges,
            seed_ids=set(), max_additional=0,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "target_b" in included_ids and "target_a" not in included_ids, (
            "Expected distributed_callee as seed pick after max_per_file_in=5 "
            f"capping; got included={included_ids}"
        )

    def test_tier_dampener_applied_to_internal_centrality(self):
        """Empty-seeds seed pick uses tier-weighted centrality.

        WI-lidum's 6-repo audit found tier-by-select_by_connectivity is
        the largest-evidence dampener cell at this surface (7-22 of
        top-100 across all 6 repos): without tier weighting, external/
        tier-3 symbols leak into the seed pick.
        """
        from hypergumbo_core.compact import select_by_connectivity

        long_span = Span(start_line=1, end_line=100, start_col=0, end_col=0)
        external_sym = Symbol(
            id="external", name="Sprintf", kind="function", language="go",
            path="<external>", span=long_span,
        )
        external_sym.supply_chain_tier = 3  # external dep
        first_party_sym = Symbol(
            id="first_party", name="ServeRequest", kind="function", language="go",
            path="server/server.go", span=long_span,
        )
        first_party_sym.supply_chain_tier = 1
        # External has 5 callers; first-party has 3. Tier weights
        # (1.0x external, 2.0x first-party) flip the order.
        edges = [
            make_edge(f"caller{i}", "external") for i in range(5)
        ] + [
            make_edge(f"fp_caller{i}", "first_party") for i in range(3)
        ]
        result = select_by_connectivity(
            [external_sym, first_party_sym], edges, seed_ids=set(),
            max_additional=0,
        )
        included_ids = {s.id for s in result.included.symbols}
        assert "first_party" in included_ids, (
            "Expected first-party ServeRequest after tier weighting; "
            f"got included={included_ids}"
        )


class TestSelectByConnectivityIntegration:
    """Integration tests for connectivity selection with format functions."""

    def test_compact_with_connectivity_produces_edges(self):
        """Compact mode with connectivity selection produces non-zero edges."""
        from hypergumbo_core.compact import (
            format_compact_behavior_map,
            CompactConfig,
        )

        # Django-like scenario: many entrypoints, shared utilities
        entrypoints = [make_symbol(f"Command{i}", kind="class") for i in range(5)]
        utilities = [make_symbol(f"util_{i}", kind="function") for i in range(3)]
        symbols = entrypoints + utilities

        # Each entrypoint calls all utilities
        edges = []
        for ep in entrypoints:
            for util in utilities:
                edges.append(make_edge(ep.id, util.id))

        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [
                {"symbol_id": ep.id, "kind": "cli_command", "confidence": 0.9}
                for ep in entrypoints
            ],
        }

        config = CompactConfig(
            target_coverage=0.8,
            max_symbols=10,  # Enough for entrypoints + some utilities
        )

        result = format_compact_behavior_map(
            behavior_map, symbols, edges, config,
            force_include_entrypoints=True,
            connectivity_aware=True,  # Enable new algorithm
        )

        # Should have edges (utilities connect entrypoints)
        assert len(result["edges"]) > 0, "Expected edges with connectivity selection"

        # Verify induced subgraph property
        included_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["src"] in included_ids
            assert edge["dst"] in included_ids


class TestTieredTokenBudget:
    """Tests for token budget compliance in tiered behavior maps.

    Tiered output must fit within the target token budget. Two fixes:
    1. Force-included entrypoints must be capped (like compact mode does).
    2. Non-essential fields (analysis_runs, usage_contexts, sketch_precomputed)
       must be stripped from tiered output.
    """

    def test_tiered_entrypoints_capped(self):
        """When many entrypoints exist, tiered mode caps them to fit budget."""
        # Simulate a repo with many entrypoints (like FastAPI with ~1400 routes)
        entrypoint_syms = [make_symbol(f"route_{i}") for i in range(50)]
        other_syms = [make_symbol(f"util_{i}") for i in range(50)]
        all_symbols = entrypoint_syms + other_syms

        entrypoints = [
            {"symbol_id": s.id, "kind": "http_route", "confidence": 0.9 - i * 0.001}
            for i, s in enumerate(entrypoint_syms)
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [],
            "entrypoints": entrypoints,
        }

        # With 4k budget, we should NOT include all 50 entrypoints
        result = format_tiered_behavior_map(
            behavior_map, all_symbols, [],
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        # The total token count of the output should be under budget
        from hypergumbo_core.compact import estimate_behavior_map_tokens
        actual_tokens = estimate_behavior_map_tokens(result)
        assert actual_tokens <= 4000, (
            f"Tiered output is {actual_tokens} tokens, exceeds 4000 budget. "
            f"Nodes: {len(result['nodes'])}"
        )

    def test_tiered_many_entrypoints_still_produces_nodes(self):
        """With 500+ entrypoints, tiered mode should still include nodes.

        Regression: entrypoint reserve was ep_count * 30 + 400 which for
        500 entrypoints = 15,400 tokens — exceeding the 4K budget entirely,
        leaving 0 tokens for nodes. The reserve must be capped.
        """
        entrypoint_syms = [make_symbol(f"route_{i}") for i in range(500)]
        other_syms = [make_symbol(f"util_{i}") for i in range(50)]
        all_symbols = entrypoint_syms + other_syms

        entrypoints = [
            {"symbol_id": s.id, "kind": "http_route", "confidence": 0.9 - i * 0.0001}
            for i, s in enumerate(entrypoint_syms)
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [],
            "entrypoints": entrypoints,
        }

        result = format_tiered_behavior_map(
            behavior_map, all_symbols, [],
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        # Must include at least some nodes (not 0)
        assert len(result["nodes"]) > 0, (
            "Tiered output has 0 nodes despite 550 available symbols"
        )

        # Budget compliance
        actual_tokens = estimate_behavior_map_tokens(result)
        assert actual_tokens <= 4000, (
            f"Tiered output is {actual_tokens} tokens, exceeds 4000 budget"
        )

    def test_tiered_strips_analysis_runs(self):
        """Tiered output should not include full analysis_runs (too large)."""
        symbols = [make_symbol("core")]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [],
            "entrypoints": [],
            "analysis_runs": [
                {"analyzer": f"analyzer_{i}", "files": 100, "symbols": 500}
                for i in range(20)
            ],
        }

        result = format_tiered_behavior_map(
            behavior_map, symbols, [], target_tokens=4000
        )

        # analysis_runs should be stripped or summarized
        assert "analysis_runs" not in result, (
            "Tiered output should strip analysis_runs to save tokens"
        )

    def test_tiered_strips_usage_contexts(self):
        """Tiered output should not include usage_contexts (too large)."""
        symbols = [make_symbol("core")]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [],
            "entrypoints": [],
            "usage_contexts": [
                {"symbol_id": f"sym_{i}", "context": "call", "source": "file.py"}
                for i in range(100)
            ],
        }

        result = format_tiered_behavior_map(
            behavior_map, symbols, [], target_tokens=4000
        )

        assert "usage_contexts" not in result, (
            "Tiered output should strip usage_contexts to save tokens"
        )

    def test_tiered_strips_sketch_precomputed(self):
        """Tiered output should not include sketch_precomputed (irrelevant)."""
        symbols = [make_symbol("core")]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [],
            "entrypoints": [],
            "sketch_precomputed": {
                "config_info": "x" * 1000,
                "vocabulary": ["word"] * 100,
                "additional_file_centrality_scores": {"file.py": 0.5},
            },
        }

        result = format_tiered_behavior_map(
            behavior_map, symbols, [], target_tokens=4000
        )

        assert "sketch_precomputed" not in result, (
            "Tiered output should strip sketch_precomputed to save tokens"
        )

    def test_tiered_metrics_describe_projection_not_source(self):
        """WI-pizat: tiered recomputes its metrics block from the projected
        arrays instead of echoing the source (full-repo) totals."""
        symbols = [make_symbol("a"), make_symbol("b")]
        edges = [make_edge(symbols[1].id, symbols[0].id)]
        behavior_map = {
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
            "metrics": {
                "total_nodes": 9999,
                "total_edges": 9999,
                "total_files": 999,
                "by_supply_chain_tier": {},
            },
        }

        result = format_tiered_behavior_map(
            behavior_map, symbols, edges, target_tokens=4000
        )

        m = result["metrics"]
        assert m["total_nodes"] == len(result["nodes"]) < 9999
        assert m["total_edges"] == len(result["edges"]) < 9999

    def test_tiered_low_confidence_entrypoints_dont_crowd_bridge_nodes(self):
        """Low-confidence entrypoints should not crowd out bridge nodes.

        Regression: DMD bakeoff found 64k tiered view had 226 nodes but 0 edges.
        Root cause: 1790 test main() functions were all force-included, filling
        the budget before bridge nodes that provide edges could be selected.
        Test mains have confidence ~0.65 (0.9 base * 0.5 test penalty + 0.2
        connectivity boost), so the threshold must be >= 0.7 to filter them.

        Fix: format_tiered_behavior_map filters force_include_ids by confidence
        (>= 0.7) and caps count. Low-confidence entrypoints compete on centrality
        with bridge nodes in the regular fill phase.
        """
        # Real entrypoints (high confidence, like actual main functions)
        real_eps = [make_symbol(f"real_ep_{i}") for i in range(3)]
        # Test main() entrypoints (confidence 0.65: 0.9 * 0.5 + connectivity)
        test_eps = [make_symbol(f"test_main_{i}") for i in range(80)]
        # Bridge nodes that connect real entrypoints (high centrality)
        bridges = [make_symbol(f"bridge_{i}") for i in range(10)]

        all_symbols = real_eps + test_eps + bridges

        # Edges: real_eps → bridges, bridges → bridges (connected subgraph)
        edge_list = []
        for ep in real_eps:
            edge_list.append(make_edge(ep.id, bridges[0].id))
        for i in range(len(bridges) - 1):
            edge_list.append(make_edge(bridges[i].id, bridges[i + 1].id))

        entrypoints = [
            {"symbol_id": ep.id, "kind": "main_function", "confidence": 0.9}
            for ep in real_eps
        ] + [
            # 0.65 matches real DMD scenario: test penalty (0.5x) + connectivity
            {"symbol_id": ep.id, "kind": "main_function", "confidence": 0.65}
            for ep in test_eps
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edge_list],
            "entrypoints": entrypoints,
        }

        # 4k budget: ~35-40 symbols fit. Without the fix, 80 test mains
        # fill the force-include phase, leaving no room for bridge nodes.
        result = format_tiered_behavior_map(
            behavior_map, all_symbols, edge_list,
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        included_ids = {n["id"] for n in result["nodes"]}

        # Bridge nodes should be present — they provide connectivity
        bridge_included = sum(1 for b in bridges if b.id in included_ids)
        assert bridge_included > 0, (
            f"No bridge nodes included. {len(result['nodes'])} nodes total, "
            f"0 bridge nodes. Low-confidence test entrypoints crowded them out."
        )

        # Edges should exist (bridge nodes connect things)
        assert len(result["edges"]) > 0, (
            f"Tiered view has {len(result['nodes'])} nodes but 0 edges. "
            f"Low-confidence test entrypoints crowded out bridge nodes."
        )

    def test_select_by_tokens_respects_budget_for_forced(self):
        """Force-included symbols should not exceed token budget."""
        # Create many symbols to force-include
        symbols = [make_symbol(f"sym_{i}") for i in range(100)]
        force_ids = {s.id for s in symbols}  # Force ALL

        result = select_by_tokens(
            symbols, [],
            target_tokens=1000,
            force_include_ids=force_ids,
        )

        # Total tokens used by included symbols should be under budget
        total_tokens = sum(
            estimate_node_tokens(s.to_dict()) for s in result.included.symbols
        )
        # Allow for overhead (200 + 200), but shouldn't be wildly over
        assert total_tokens <= 1000, (
            f"Force-included symbols use {total_tokens} tokens, "
            f"exceeds 1000 budget. Included {result.included.count} symbols."
        )

    def test_tiered_self_loop_edges_excluded(self):
        """Self-loop edges (src==dst) are excluded from tiered output.

        Regression: DMD bakeoff iter-002 found 28% of 64k view edges were
        self-loops (visitor pattern self-references where src==dst). These
        waste token budget and inflate edge counts without adding useful
        connectivity information.
        """
        sym_a = make_symbol("process")
        sym_b = make_symbol("handle")

        # Normal edge + self-loop
        normal_edge = make_edge(sym_a.id, sym_b.id)
        self_loop = make_edge(sym_a.id, sym_a.id)
        all_edges = [normal_edge, self_loop]

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b]],
            "edges": [e.to_dict() for e in all_edges],
            "entrypoints": [],
        }

        result = format_tiered_behavior_map(
            behavior_map, [sym_a, sym_b], all_edges,
            target_tokens=10000,
        )

        # Self-loop should not appear in output edges
        for e in result["edges"]:
            assert e.get("src") != e.get("dst"), (
                f"Self-loop found in tiered output: {e.get('src')} -> {e.get('dst')}"
            )
        # Normal edge should still be present
        assert len(result["edges"]) == 1

    def test_tiered_self_loops_dont_inflate_centrality(self):
        """Self-loops should not inflate centrality during token selection.

        A symbol with a self-loop should not get extra centrality relative
        to an otherwise-equivalent symbol without one.
        """
        sym_a = make_symbol("visitor_accept")
        sym_b = make_symbol("handler_process")
        sym_c = make_symbol("caller")

        # Normal edges: caller -> visitor, caller -> handler
        # Plus self-loop on visitor
        edges = [
            make_edge(sym_c.id, sym_a.id),
            make_edge(sym_c.id, sym_b.id),
            make_edge(sym_a.id, sym_a.id),  # self-loop
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        result = format_tiered_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], edges,
            target_tokens=10000,
        )

        # Self-loop should not be in output
        for e in result["edges"]:
            assert e.get("src") != e.get("dst"), (
                f"Self-loop in output: {e.get('src')} -> {e.get('dst')}"
            )
        # Should have exactly 2 edges: caller->visitor, caller->handler
        assert len(result["edges"]) == 2

    def test_tiered_output_respects_token_budget_with_edges(self):
        """Tiered output must not exceed the target token budget.

        Regression: DMD bakeoff iter-002 showed 64k tiered view at 175K tokens
        (2.7x over budget) because induced edges were added without any budget
        accounting. DMD 16k was 3.4x over budget (54K tokens for 16K target).

        The root cause: format_tiered_behavior_map selects nodes within budget,
        then adds ALL induced edges and entrypoints without checking if the
        total output fits. With dense graphs (DMD has 130K edges for 76K nodes),
        the induced edge set can dwarf the node budget.

        Fix: Post-selection budget validation with node shrinking to fit edges.
        """
        # Create a dense graph: 50 nodes, each connected to ~10 others
        all_symbols = [make_symbol(f"func_{i}") for i in range(50)]
        all_edges = []
        for i in range(50):
            for j in range(1, 11):
                target = (i + j) % 50
                if target != i:
                    all_edges.append(make_edge(
                        all_symbols[i].id, all_symbols[target].id
                    ))

        entrypoints = [
            {"symbol_id": s.id, "kind": "main_function", "confidence": 0.9}
            for s in all_symbols[:5]
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in all_edges],
            "entrypoints": entrypoints,
        }

        # Test with 4k budget — dense graph should NOT blow the budget
        result_4k = format_tiered_behavior_map(
            behavior_map, all_symbols, all_edges,
            target_tokens=4000,
            force_include_entrypoints=True,
        )
        actual_4k = estimate_behavior_map_tokens(result_4k)
        assert actual_4k <= 4000, (
            f"4k tiered output is {actual_4k} tokens, exceeds 4000 budget. "
            f"{len(result_4k['nodes'])} nodes, {len(result_4k['edges'])} edges."
        )

        # Test with 16k budget
        result_16k = format_tiered_behavior_map(
            behavior_map, all_symbols, all_edges,
            target_tokens=16000,
            force_include_entrypoints=True,
        )
        actual_16k = estimate_behavior_map_tokens(result_16k)
        assert actual_16k <= 16000, (
            f"16k tiered output is {actual_16k} tokens, exceeds 16000 budget. "
            f"{len(result_16k['nodes'])} nodes, {len(result_16k['edges'])} edges."
        )

    def test_tiered_budget_compliance_edge_heavy_graph(self):
        """Edge-heavy graph (like DMD visitor pattern) must still comply.

        DMD has ~1.7 edges per node. Many nodes have high fan-out to
        the same targets. The induced edge set for the selected nodes
        should be truncated when it would blow the budget.
        """
        # 30 nodes, high fan-out: each calls 15 others
        syms = [make_symbol(f"visit_{i}") for i in range(30)]
        edges = []
        for i in range(30):
            for j in range(15):
                target = (i + j + 1) % 30
                if target != i:
                    edges.append(make_edge(syms[i].id, syms[target].id))

        behavior_map = {
            "nodes": [s.to_dict() for s in syms],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        # 16k budget: should include many nodes but truncate edges to fit
        result = format_tiered_behavior_map(
            behavior_map, syms, edges,
            target_tokens=16000,
        )
        actual = estimate_behavior_map_tokens(result)
        assert actual <= 16000, (
            f"16k tiered output is {actual} tokens, exceeds 16000 budget. "
            f"{len(result['nodes'])} nodes, {len(result['edges'])} edges."
        )

    def test_tiered_4k_produces_edges_when_graph_has_connectivity(self):
        """4k tiered view should include edges when the graph is connected.

        Regression: All three bakeoff repos (Django, DMD, NestJS) produced
        0 edges in their 4k views despite having dense graphs. The 4k view
        selected 7-12 high-centrality nodes independently, without considering
        whether they were adjacent. A connected subgraph of 5 nodes with
        edges is more useful than 12 disconnected nodes.

        Fix: Use connectivity-aware selection in format_tiered_behavior_map.
        """
        # Create a graph with clear connected components
        # Component 1: entrypoint -> service -> repository
        ep = make_symbol("AppController")
        svc = make_symbol("UserService")
        repo = make_symbol("UserRepository")
        # Component 2: isolated high-centrality nodes
        isolated = [make_symbol(f"util_{i}") for i in range(10)]

        all_symbols = [ep, svc, repo] + isolated

        # Connected edges: ep -> svc -> repo (linear chain)
        connected_edges = [
            make_edge(ep.id, svc.id),
            make_edge(svc.id, repo.id),
        ]
        # Also give isolated nodes high in-degree (many edges pointing to them)
        # so centrality-only selection would prefer them
        extra_callers = [make_symbol(f"caller_{i}") for i in range(30)]
        all_symbols.extend(extra_callers)
        isolated_edges = []
        for i, caller in enumerate(extra_callers):
            # Each caller -> 3 isolated utils (boosts their centrality)
            for j in range(3):
                idx = (i + j) % len(isolated)
                isolated_edges.append(make_edge(caller.id, isolated[idx].id))

        all_edges = connected_edges + isolated_edges

        entrypoints = [
            {"symbol_id": ep.id, "kind": "main_function", "confidence": 0.9},
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in all_edges],
            "entrypoints": entrypoints,
        }

        result = format_tiered_behavior_map(
            behavior_map, all_symbols, all_edges,
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        # Should have at least 1 edge (the ep -> svc connection)
        assert len(result["edges"]) >= 1, (
            f"4k tiered view has {len(result['nodes'])} nodes but 0 edges. "
            f"Connectivity-aware selection should produce connected output."
        )

    def test_shrink_loop_preserves_edges_with_disconnected_seeds(self):
        """Shrink loop must preserve edges when seeds don't share edges.

        Regression: DEEP bakeoff cohort #5 (iceberg) showed 64k tiered view
        with 169 nodes but 0 edges, despite select_by_connectivity returning
        362 nodes with 1056 induced edges. Root cause: the shrink loop sorted
        removal candidates by (force_include, global_centrality). Force-included
        seeds (test entrypoints) were protected, but frontier-expanded production
        nodes (which provided all the edges) had LOW global centrality and were
        removed first. After shrinking, only disconnected seeds remained.

        Fix: Shrink loop considers local edge degree when choosing victims.
        Nodes with zero local edges are removed first (they add no connectivity
        value), and among nodes with edges, those with fewer local edges are
        preferred for removal.
        """
        # 60 entrypoint seeds in integration test paths — each tests a
        # different subsystem. They don't directly call each other.
        seeds = [
            make_symbol(f"test_ep_{i}", path=f"src/integration/Test{i}.java",
                        kind="method", language="java")
            for i in range(60)
        ]

        # 40 production code nodes — each seed calls 2 production funcs.
        prod = [
            make_symbol(f"prod_{i}", path="src/main/Prod.java",
                        kind="method", language="java")
            for i in range(40)
        ]

        # Edges: seeds → prod (each seed → 2 prod nodes, with overlap)
        seed_prod_edges = []
        for i, s in enumerate(seeds):
            for j in range(2):
                idx = (i * 2 + j) % len(prod)
                seed_prod_edges.append(make_edge(s.id, prod[idx].id))

        # Production backbone: connected chain providing inter-prod edges
        backbone_edges = [
            make_edge(prod[i].id, prod[i + 1].id) for i in range(len(prod) - 1)
        ]

        # 300 callers → 20 popular utilities: inflates global centrality
        # to make prod nodes look unimportant by comparison.
        utils = [
            make_symbol(f"util_{i}", path="src/main/Utils.java",
                        kind="method", language="java")
            for i in range(20)
        ]
        callers = [
            make_symbol(f"caller_{i}", path="src/main/Callers.java",
                        kind="method", language="java")
            for i in range(300)
        ]
        util_edges = []
        for i, c in enumerate(callers):
            for j in range(3):
                idx = (i + j) % len(utils)
                util_edges.append(make_edge(c.id, utils[idx].id))

        all_symbols = seeds + prod + utils + callers
        all_edges = seed_prod_edges + backbone_edges + util_edges

        entrypoints = [
            {"symbol_id": s.id, "kind": "test_function", "confidence": 1.0}
            for s in seeds
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in all_edges],
            "entrypoints": entrypoints,
        }

        # 4k budget: forces aggressive shrinking.
        # Without the fix, the shrink loop removes all prod nodes (low global
        # centrality, not force-included) leaving only disconnected seeds
        # with 0 edges.
        result = format_tiered_behavior_map(
            behavior_map, all_symbols, all_edges,
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        # The result must have edges — a graph with zero edges is useless
        assert len(result["edges"]) > 0, (
            f"Shrink loop produced {len(result['nodes'])} nodes but 0 edges. "
            f"Production nodes providing connectivity were removed because "
            f"they had low global centrality. Shrink should prefer removing "
            f"disconnected singletons before nodes with local edges."
        )

    def test_language_proportional_sketch(self):
        """Dominant language must be represented in budget views.

        Regression: DEEP bakeoff cohort #6 (git) showed 64k tiered view
        with 99 Python nodes and 0 C nodes, despite git being 99%+ C code.
        Root cause: Python entrypoints (git-p4.py main) had 33 frontier
        edges while C entrypoints (common-main.c main) had only 3. BFS
        frontier was 88% Python, so greedy selection picked Python nodes
        exclusively.

        Fix: After building the seed set from entrypoints, check language
        distribution of the frontier. For any language with >10% of total
        edges but underrepresented in the frontier, inject top-centrality
        nodes from that language as additional seeds.
        """
        # 100 C nodes forming a dense call graph (the dominant language)
        c_funcs = [
            make_symbol(f"c_func_{i}", path=f"src/core_{i // 10}.c",
                        kind="function", language="c")
            for i in range(100)
        ]
        # Dense C call graph: chain + cross-calls = ~200 edges
        c_edges = []
        for i in range(99):
            c_edges.append(make_edge(c_funcs[i].id, c_funcs[i + 1].id))
        for i in range(0, 100, 5):
            for j in range(i + 1, min(i + 5, 100)):
                c_edges.append(make_edge(c_funcs[i].id, c_funcs[j].id))

        # 15 Python nodes forming a small but dense cluster
        py_funcs = [
            make_symbol(f"py_func_{i}", path="scripts/tool.py",
                        kind="function", language="python")
            for i in range(15)
        ]
        py_edges = []
        for i in range(14):
            py_edges.append(make_edge(py_funcs[i].id, py_funcs[i + 1].id))
        for i in range(0, 15, 3):
            for j in range(i + 1, min(i + 3, 15)):
                py_edges.append(make_edge(py_funcs[i].id, py_funcs[j].id))

        # 1 C entrypoint with NO outgoing C call edges.
        # This models git's common-main.c:main which dispatches via
        # function pointer table — tree-sitter can't resolve those calls.
        c_main = make_symbol("main", path="src/main.c",
                             kind="function", language="c")
        # c_main has zero edges to c_funcs — completely isolated

        # 1 Python entrypoint with edges to all Python funcs (dense frontier)
        py_main = make_symbol("py_main", path="scripts/tool.py",
                              kind="function", language="python")
        py_main_edges = [
            make_edge(py_main.id, py_funcs[i].id) for i in range(15)
        ]

        all_symbols = [c_main, py_main] + c_funcs + py_funcs
        all_edges = c_edges + py_main_edges + py_edges

        entrypoints = [
            {"symbol_id": c_main.id, "kind": "main_function", "confidence": 1.0},
            {"symbol_id": py_main.id, "kind": "main_function", "confidence": 1.0},
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in all_edges],
            "entrypoints": entrypoints,
        }

        # 16k budget: should be enough for ~30 nodes.
        result = format_tiered_behavior_map(
            behavior_map, all_symbols, all_edges,
            target_tokens=16000,
            force_include_entrypoints=True,
        )

        result_nodes = result["nodes"]
        c_nodes = [n for n in result_nodes if n.get("language") == "c"]
        py_nodes = [n for n in result_nodes if n.get("language") == "python"]

        # C has ~200 edges (87% of total) and 100 nodes.
        # Without the fix: BFS from seeds finds no C frontier (c_main is
        # isolated), so only Python nodes are selected.
        # With the fix: top-centrality C nodes are injected as seeds,
        # letting BFS expand into the C subgraph.
        assert len(c_nodes) >= len(py_nodes), (
            f"C has {len(c_nodes)} nodes vs Python {len(py_nodes)}. "
            f"C is the dominant language (100 nodes, {len(c_edges)} edges) "
            f"but was underrepresented because its entrypoint had zero "
            f"frontier edges. Language-proportional seeding should fix this."
        )


    def test_tiered_excludes_boundary_nodes(self):
        """Boundary nodes (external_symbol) should not appear in tiered output.

        Boundary nodes exist in all_symbols for slice traversal but are filtered
        from the full behavior_map["nodes"]. The tiered view should also exclude
        them. Without this fix, when no high-confidence entrypoints exist, the
        connectivity selection picks boundary nodes (high in-degree from imports)
        instead of first-party code nodes.
        """
        # First-party function nodes
        func_a = make_symbol("processData", path="src/process.js", language="javascript")
        func_b = make_symbol("loadConfig", path="src/config.js", language="javascript")

        # Boundary node: unresolved external import target
        boundary = Symbol(
            id="javascript:lodash:0-0:module:module",
            name="module",
            kind="external_symbol",
            language="javascript",
            path="<external>",
            span=Span(start_line=0, end_line=0, start_col=0, end_col=0),
            meta={"external_boundary": True},
            supply_chain_tier=3,
            supply_chain_reason="unresolved external reference",
        )

        # Edges: both functions import the boundary node (high in-degree)
        edge_a = make_edge(func_a.id, boundary.id, edge_type="imports")
        edge_b = make_edge(func_b.id, boundary.id, edge_type="imports")
        edge_ab = make_edge(func_a.id, func_b.id, edge_type="calls")

        all_symbols = [func_a, func_b, boundary]
        all_edges = [edge_a, edge_b, edge_ab]

        behavior_map = {
            "nodes": [s.to_dict() for s in [func_a, func_b]],  # Full view excludes boundary
            "edges": [e.to_dict() for e in all_edges],
            "entrypoints": [],
        }

        result = format_tiered_behavior_map(
            behavior_map, all_symbols, all_edges,
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        result_nodes = result["nodes"]
        # No boundary nodes in output
        boundary_in_output = [
            n for n in result_nodes
            if n.get("kind") == "external_symbol"
            or (n.get("meta") or {}).get("external_boundary")
        ]
        assert len(boundary_in_output) == 0, (
            f"Boundary nodes should be excluded from tiered output, "
            f"found {len(boundary_in_output)}: "
            f"{[n.get('name') for n in boundary_in_output]}"
        )

        # First-party nodes should be present
        first_party = [n for n in result_nodes if n.get("supply_chain", {}).get("tier") == 1]
        assert len(first_party) >= 1, "At least one first-party node should be in tiered output"

    def test_tiered_excludes_cfg_test_annotated_nodes(self):
        """Symbols with cfg(test) annotation should be excluded from tiered output.

        Rust idiomatically puts test code inside ``#[cfg(test)] mod tests { ... }``
        within source files (not test files). The Rust analyzer annotates these
        symbols with cfg(test) in their metadata. The compact view must use
        ``is_test_node(path, meta)`` (which checks both path and annotations)
        rather than ``is_test_path(path)`` (path-only), so test infrastructure
        like ``StubClient::new`` doesn't dominate centrality rankings.
        """
        # Production symbols
        prod_a = make_symbol("CodexAgent::new", path="src/agent.rs", language="rust", kind="method")
        prod_b = make_symbol("setup", path="src/thread.rs", language="rust", kind="function")

        # Test infrastructure: cfg(test) annotated but in a production file path
        test_stub = Symbol(
            id="rust:src/thread.rs:3692-3696:StubClient::new:method",
            name="StubClient::new",
            kind="method",
            language="rust",
            path="src/thread.rs",
            span=Span(start_line=3692, end_line=3696, start_col=0, end_col=1),
            meta={"annotations": [{"name": "cfg", "args": ["test"], "kwargs": {}}]},
            supply_chain_tier=1,
        )

        # StubClient::new has high in-degree (called from many tests)
        edges = [
            make_edge(f"test_{i}", test_stub.id) for i in range(20)
        ] + [
            make_edge(prod_a.id, prod_b.id),
        ]

        all_symbols = [prod_a, prod_b, test_stub]
        all_edges = edges

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in all_edges],
            "entrypoints": [],
        }

        result = format_tiered_behavior_map(
            behavior_map, all_symbols, all_edges,
            target_tokens=4000,
            force_include_entrypoints=True,
        )

        result_names = [n["name"] for n in result["nodes"]]
        # Test stub should NOT appear in output despite high in-degree
        assert "StubClient::new" not in result_names, (
            f"cfg(test)-annotated StubClient::new should be excluded from "
            f"tiered output, but found in: {result_names}"
        )
        # Production nodes should be present
        assert "CodexAgent::new" in result_names or "setup" in result_names, (
            f"At least one production node should be in tiered output, got: {result_names}"
        )

    def test_select_by_tokens_excludes_cfg_test_annotated(self):
        """select_by_tokens should exclude cfg(test) annotated symbols when exclude_tests=True."""
        prod = make_symbol("handle_request", path="src/server.rs", language="rust")
        test_helper = Symbol(
            id="rust:src/server.rs:500-510:make_stub:function",
            name="make_stub",
            kind="function",
            language="rust",
            path="src/server.rs",  # NOT a test path
            span=Span(start_line=500, end_line=510, start_col=0, end_col=1),
            meta={"annotations": [{"name": "cfg", "args": ["test"], "kwargs": {}}]},
            supply_chain_tier=1,
        )

        symbols = [prod, test_helper]
        edges = [make_edge("caller", test_helper.id) for _ in range(10)]

        result = select_by_tokens(
            symbols, edges,
            target_tokens=4000,
            exclude_tests=True,
            exclude_examples=True,
            exclude_non_code=True,
        )

        selected_names = [s.name for s in result.included.symbols]
        assert "make_stub" not in selected_names, (
            f"cfg(test)-annotated make_stub should be excluded, got: {selected_names}"
        )
        assert "handle_request" in selected_names


class TestCrossCuttingEdgeSeeding:
    """Tests for cross-cutting edge endpoint seeding (INV-posun).

    Compact mode must retain cross-cutting edge types (calls,
    dispatches_to, event_publishes) by pre-seeding their endpoints
    into the node selection. Post WI-vumum-juvil, HTTP/gRPC/GraphQL
    edges fold to canonical 'calls' and survive via that membership.
    """

    def test_routes_to_edges_preserved(self):
        """routes_to edges survive compact when endpoints are seeded."""
        # High-centrality hub (will be selected by centrality)
        hub = make_symbol("hub")
        # Route node (zero in-degree, would be excluded by centrality)
        route = make_symbol("route_get_users", kind="function")
        # Handler that hub calls
        handler = make_symbol("get_users_handler")

        edges = [
            make_edge(hub.id, handler.id),  # hub calls handler
            make_edge(route.id, handler.id, edge_type="routes_to"),  # route → handler
        ]

        behavior_map = {
            "nodes": [s.to_dict() for s in [hub, route, handler]],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        config = CompactConfig(min_symbols=3, max_symbols=3)
        result = format_compact_behavior_map(
            behavior_map, [hub, route, handler], edges, config,
            force_include_entrypoints=False,
        )

        edge_types = {e["type"] for e in result["edges"]}
        assert "routes_to" in edge_types, (
            "routes_to edge should be preserved via cross-cutting seeding"
        )

    def test_dispatches_to_edges_preserved(self):
        """dispatches_to edges survive compact when endpoints are seeded."""
        # Interface method (called by many → high centrality)
        interface = make_symbol("IService_process", kind="method")
        # Concrete implementations (low centrality individually)
        impl_a = make_symbol("ConcreteA_process", kind="method")
        impl_b = make_symbol("ConcreteB_process", kind="method")
        # Callers to give interface high centrality
        callers = [make_symbol(f"caller_{i}") for i in range(5)]

        edges = [
            *[make_edge(c.id, interface.id) for c in callers],
            make_edge(interface.id, impl_a.id, edge_type="dispatches_to"),
            make_edge(interface.id, impl_b.id, edge_type="dispatches_to"),
        ]

        all_symbols = [interface, impl_a, impl_b] + callers
        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        # Budget large enough for callers + interface + at least one impl
        config = CompactConfig(min_symbols=5, max_symbols=8)
        result = format_compact_behavior_map(
            behavior_map, all_symbols, edges, config,
            force_include_entrypoints=False,
        )

        edge_types = {e["type"] for e in result["edges"]}
        assert "dispatches_to" in edge_types, (
            "dispatches_to edge should be preserved via cross-cutting seeding"
        )

    def test_http_calls_edges_preserved(self):
        """HTTP-protocol calls survive compact when endpoints are seeded.

        Post WI-vumum-juvil, HTTP edges emit as canonical 'calls' with
        meta['protocol']='http'; cross-cutting seeding picks them up
        via the canonical name.
        """
        # Client function making HTTP call
        client = make_symbol("fetch_users")
        # Server handler
        server = make_symbol("handle_users")
        # Hub to anchor centrality
        hub = make_symbol("main")

        http_edge = make_edge(client.id, server.id, edge_type="calls")
        http_edge.meta = {"protocol": "http"}
        edges = [
            make_edge(hub.id, client.id),
            make_edge(hub.id, server.id),
            http_edge,
        ]

        all_symbols = [hub, client, server]
        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        config = CompactConfig(min_symbols=3, max_symbols=3)
        result = format_compact_behavior_map(
            behavior_map, all_symbols, edges, config,
            force_include_entrypoints=False,
        )

        edge_types = {e["type"] for e in result["edges"]}
        assert "calls" in edge_types, (
            "HTTP call (canonical 'calls') should be preserved via "
            "cross-cutting seeding"
        )

    def test_cross_cutting_seeds_capped(self):
        """Cross-cutting seeds don't exceed 25% of max_symbols budget."""
        from hypergumbo_core.compact import CROSS_CUTTING_EDGE_TYPES

        # Create many dispatch targets (more than budget allows)
        interface = make_symbol("interface")
        impls = [make_symbol(f"impl_{i}", path=f"src/impl_{i}.py") for i in range(50)]

        edges = [
            make_edge(interface.id, impl.id, edge_type="dispatches_to")
            for impl in impls
        ]
        # Add a call so interface has centrality
        caller = make_symbol("caller")
        edges.append(make_edge(caller.id, interface.id))

        all_symbols = [interface, caller] + impls
        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [],
        }

        # max_symbols=20, so cross-cutting cap = 20//4 = 5
        config = CompactConfig(min_symbols=5, max_symbols=20)
        result = format_compact_behavior_map(
            behavior_map, all_symbols, edges, config,
            force_include_entrypoints=False,
        )

        # Should have at most 20 nodes
        assert len(result["nodes"]) <= 20

    def test_cross_cutting_with_nonexistent_endpoint_ignored(self):
        """Cross-cutting edges with endpoints not in symbols are ignored."""
        hub = make_symbol("hub")
        real = make_symbol("real_handler")
        edges_list = [
            make_edge(hub.id, real.id),
            # Edge pointing to non-existent symbol
            make_edge("phantom::nonexistent", real.id, edge_type="di_resolves"),
        ]

        all_symbols = [hub, real]
        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edges_list],
            "entrypoints": [],
        }

        config = CompactConfig(min_symbols=2, max_symbols=2)
        result = format_compact_behavior_map(
            behavior_map, all_symbols, edges_list, config,
            force_include_entrypoints=False,
        )

        # Should not crash, should include real nodes
        included_ids = {n["id"] for n in result["nodes"]}
        assert real.id in included_ids

    def test_cross_cutting_constant_values(self):
        """CROSS_CUTTING_EDGE_TYPES contains the expected edge types.

        Per ADR-0023 §6 Phase 3 (WI-vasik-jofiv / WI-mifor-vabul /
        WI-hahap-farid / WI-vumum-juvil), bridge / IPC / protocol-call
        / route / DI / annotation edges all fold to canonical 'calls' /
        'dispatches_to' / 'event_publishes' (with mechanism in meta).
        The set includes 'calls' and 'event_publishes' so cross-cutting
        endpoint seeding picks up FFI/IPC/HTTP/gRPC/GraphQL/queue/CRDT/MQ
        endpoints after each rename. Phase 4b (WI-vomoj-suhaz) pruned the
        bridge/IPC entries; WI-vumum-juvil pruned the protocol-call
        entry (http_calls); the canonical members transparently cover
        the folds.
        """
        from hypergumbo_core.compact import CROSS_CUTTING_EDGE_TYPES

        # Canonical relationship-axis values (post-Phase-3 fold targets):
        assert "calls" in CROSS_CUTTING_EDGE_TYPES
        assert "dispatches_to" in CROSS_CUTTING_EDGE_TYPES
        assert "event_publishes" in CROSS_CUTTING_EDGE_TYPES
        # Phase 4b / WI-vumum-juvil pruned the deprecated entries:
        assert "routes_to" not in CROSS_CUTTING_EDGE_TYPES
        assert "di_resolves" not in CROSS_CUTTING_EDGE_TYPES
        assert "http_calls" not in CROSS_CUTTING_EDGE_TYPES
        # "ffi_calls" was removed: it's the name of a Python local variable
        # in the FFI linkers, not an emitted Edge.edge_type value.
        assert "ffi_calls" not in CROSS_CUTTING_EDGE_TYPES
        # Pure structural edges should NOT be in the set:
        assert "imports" not in CROSS_CUTTING_EDGE_TYPES
        assert "contains" not in CROSS_CUTTING_EDGE_TYPES


class TestCompactSeedBudget:
    """Tests for seed budget management in compact mode.

    Large repos (keycloak: 79k nodes, 500 entrypoints) produce fragmented
    compact output when forced seeds consume most of the node budget, leaving
    insufficient room for bridge nodes.  The fix caps total forced seeds
    (entrypoints + cross-cutting endpoints) to at most 1/3 of max_symbols,
    reserving 2/3 for frontier expansion.
    """

    def test_many_entrypoints_limited_for_bridging(self):
        """With many isolated entrypoints, compact mode caps forced seeds
        to leave room for bridge nodes that reduce singletons."""
        from hypergumbo_core.compact import (
            format_compact_behavior_map,
            CompactConfig,
        )

        # Simulate keycloak-like: 200 isolated entrypoints, each with its
        # own private helper.  Only every 10th helper connects to core.
        all_symbols = []
        edges = []
        for i in range(200):
            ep = make_symbol(f"Resource{i}", kind="method",
                             path=f"src/r{i}.java", language="java")
            helper = make_symbol(f"Helper{i}", kind="function",
                                 path=f"src/h{i}.java", language="java")
            all_symbols.extend([ep, helper])
            edges.append(make_edge(ep.id, helper.id))

        core = make_symbol("SessionManager", kind="class",
                           path="src/core.java", language="java")
        all_symbols.append(core)
        for i in range(0, 200, 10):
            edges.append(make_edge(all_symbols[i * 2 + 1].id, core.id))

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [
                {"symbol_id": all_symbols[i * 2].id,
                 "kind": "route_handler", "confidence": 0.9}
                for i in range(200)
            ],
        }

        config = CompactConfig(max_symbols=100)
        result = format_compact_behavior_map(
            behavior_map, all_symbols, edges, config,
            force_include_entrypoints=True,
            connectivity_aware=True,
        )

        nodes = result["nodes"]
        result_edges = result["edges"]

        # Count singletons (nodes with 0 edges in the induced subgraph)
        adj: dict = {n["id"]: set() for n in nodes}
        for e in result_edges:
            src, dst = e["src"], e["dst"]
            if src in adj and dst in adj:
                adj[src].add(dst)
                adj[dst].add(src)
        singletons = sum(1 for nid in adj if not adj[nid])

        # Key assertion: zero singletons.  With proper seed budget, every
        # forced entrypoint has room for its helper to be pulled in by
        # the frontier, so no node is disconnected.
        assert singletons == 0, (
            f"Singletons: {singletons}/{len(nodes)} = "
            f"{singletons / len(nodes):.0%}. "
            f"Every forced seed should have at least one edge."
        )

    def test_total_forced_seeds_capped(self):
        """Total forced seeds (entrypoints + cross-cutting) don't exceed
        1/3 of max_symbols, leaving 2/3 for frontier expansion."""
        from hypergumbo_core.compact import (
            format_compact_behavior_map,
            CompactConfig,
        )

        # 100 entrypoints + 60 cross-cutting edge endpoints
        entrypoint_syms = [
            make_symbol(f"Ep{i}", kind="method",
                        path=f"src/ep{i}.java", language="java")
            for i in range(100)
        ]
        handler_syms = [
            make_symbol(f"Handler{i}", kind="function",
                        path=f"src/h{i}.java", language="java")
            for i in range(60)
        ]
        bridge_syms = [
            make_symbol(f"Bridge{i}", kind="class",
                        path=f"src/b{i}.java", language="java")
            for i in range(40)
        ]
        all_symbols = entrypoint_syms + handler_syms + bridge_syms

        edges = []
        # Cross-cutting edges: routes_to from entrypoints to handlers
        for i, ep in enumerate(entrypoint_syms[:60]):
            edges.append(make_edge(ep.id, handler_syms[i].id,
                                   edge_type="routes_to"))
        # Bridge edges: handlers call bridges
        for i, h in enumerate(handler_syms):
            edges.append(make_edge(h.id, bridge_syms[i % len(bridge_syms)].id))
        # Bridge chain
        for i in range(len(bridge_syms) - 1):
            edges.append(make_edge(bridge_syms[i].id, bridge_syms[i + 1].id))

        behavior_map = {
            "nodes": [s.to_dict() for s in all_symbols],
            "edges": [e.to_dict() for e in edges],
            "entrypoints": [
                {"symbol_id": ep.id, "kind": "route_handler",
                 "confidence": 0.9}
                for ep in entrypoint_syms
            ],
        }

        config = CompactConfig(max_symbols=100)
        result = format_compact_behavior_map(
            behavior_map, all_symbols, edges, config,
            force_include_entrypoints=True,
            connectivity_aware=True,
        )

        nodes = result["nodes"]
        included_ids = {n["id"] for n in nodes}

        # At least some bridge nodes should be included (frontier expansion)
        bridge_count = sum(1 for b in bridge_syms if b.id in included_ids)
        assert bridge_count > 0, (
            "No bridge nodes included — forced seeds consumed all budget"
        )
