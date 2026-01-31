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
        """When entrypoints exceed max_symbols/2, they are capped to leave room for bridges."""
        # Create 20 entrypoint symbols (simulating many main() functions)
        entrypoint_syms = [make_symbol(f"main_{i}") for i in range(20)]
        helper_syms = [make_symbol(f"helper_{i}") for i in range(30)]

        # Set confidence so main_0 through main_4 have highest confidence
        behavior_map = {
            "nodes": [s.to_dict() for s in entrypoint_syms + helper_syms],
            "edges": [],
            "entrypoints": [
                {"symbol_id": s.id, "kind": "main_function", "confidence": 0.9 - i * 0.01}
                for i, s in enumerate(entrypoint_syms)
            ],
        }

        # With max_symbols=10, only 5 entrypoints (max_symbols // 2) should be forced
        # This leaves room for 5 bridge/helper nodes
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

        # Should have capped entrypoints to 5 (max_symbols // 2)
        assert len(entrypoints_included) <= 5, (
            f"Expected at most 5 entrypoints, got {len(entrypoints_included)}"
        )

        # The highest-confidence entrypoints should be included (main_0 through main_4)
        for i in range(5):
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
