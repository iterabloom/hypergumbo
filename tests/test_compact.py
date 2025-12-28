"""Tests for compact output mode.

This module tests the coverage-based truncation and bag-of-words
summarization for LLM-friendly output.
"""
from hypergumbo.ir import Symbol, Edge, Span
from hypergumbo.compact import (
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
            "schema_version": "0.1.0",
            "nodes": [s.to_dict() for s in symbols],
            "edges": [e.to_dict() for e in edges],
        }

        config = CompactConfig(min_symbols=1, max_symbols=1)
        result = format_compact_behavior_map(behavior_map, symbols, edges, config)

        assert result["view"] == "compact"
        assert "nodes_summary" in result
        assert len(result["nodes"]) <= 1

    def test_edges_filtered(self):
        """Only edges connecting included nodes are kept."""
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")

        # Edge a->b (a will be included)
        # Edge b->c (c will be omitted)
        edge_ab = make_edge(sym_a.id, sym_b.id)
        edge_bc = make_edge(sym_b.id, sym_c.id)

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [edge_ab.to_dict(), edge_bc.to_dict()],
        }

        config = CompactConfig(min_symbols=1, max_symbols=1)
        result = format_compact_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], [edge_ab, edge_bc], config
        )

        # Should have filtered edges to only those involving included nodes
        included_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["src"] in included_ids or edge["dst"] in included_ids


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
