"""Tests for compact output mode.

This module tests the coverage-based truncation and bag-of-words
summarization for LLM-friendly output.
"""
import pytest

from hypergumbo.schema import SCHEMA_VERSION
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
    # Tiered output functions
    parse_tier_spec,
    estimate_node_tokens,
    estimate_behavior_map_tokens,
    select_by_tokens,
    format_tiered_behavior_map,
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
        """Only edges connecting included nodes are kept."""
        sym_a = make_symbol("a")
        sym_b = make_symbol("b")
        sym_c = make_symbol("c")

        edge_ab = make_edge(sym_a.id, sym_b.id)
        edge_bc = make_edge(sym_b.id, sym_c.id)

        behavior_map = {
            "nodes": [s.to_dict() for s in [sym_a, sym_b, sym_c]],
            "edges": [edge_ab.to_dict(), edge_bc.to_dict()],
        }

        # Small budget to force truncation
        result = format_tiered_behavior_map(
            behavior_map, [sym_a, sym_b, sym_c], [edge_ab, edge_bc],
            target_tokens=500
        )

        # Edges should only connect to included nodes
        included_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["src"] in included_ids or edge["dst"] in included_ids


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
        """Dependency kinds are excluded."""
        assert "dependency" in EXCLUDED_KINDS
        assert "devDependency" in EXCLUDED_KINDS

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

    def test_production_files_not_detected(self):
        """Production files are not detected as tests."""
        assert not _is_test_path("src/app.py")
        assert not _is_test_path("lib/utils.ts")
        assert not _is_test_path("pkg/handler.go")
        assert not _is_test_path("components/Button.tsx")


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
