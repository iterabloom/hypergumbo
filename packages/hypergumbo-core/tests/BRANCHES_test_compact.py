"""Branch coverage tests for compact.py.

Tests specific branch paths that may not be covered by the main test suite.
"""
from typing import List

import pytest

from hypergumbo_core.compact import (
    UnionFind,
    select_by_coverage,
    select_by_connectivity,
    compute_word_frequencies,
    compute_path_frequencies,
    compute_kind_distribution,
    tokenize_name,
    extract_path_pattern,
    CompactConfig,
)
from hypergumbo_core.ir import Symbol, Edge, Span


def make_symbol(
    id_: str,
    name: str,
    kind: str = "function",
    path: str = "test.py",
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language="python",
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
    )


def make_edge(src: str, dst: str, edge_type: str = "calls") -> Edge:
    """Create a test edge."""
    return Edge(
        id=f"{src}->{dst}",
        src=src,
        dst=dst,
        edge_type=edge_type,
        line=1,
    )


class TestUnionFind:
    """Branch coverage for UnionFind class."""

    def test_init_with_empty_elements(self) -> None:
        """Test UnionFind init with empty list."""
        uf = UnionFind([])
        assert uf.largest_component_size() == 0

    def test_init_with_none_elements(self) -> None:
        """Test UnionFind init with None (no elements)."""
        uf = UnionFind(None)
        assert uf.largest_component_size() == 0

    def test_add_duplicate_element(self) -> None:
        """Test adding same element twice."""
        uf = UnionFind()
        uf.add("a")
        uf.add("a")  # Duplicate - should not add again
        assert uf.largest_component_size() == 1

    def test_union_existing_elements(self) -> None:
        """Test union of two elements that are added first."""
        uf = UnionFind()
        uf.add("a")
        uf.add("b")
        uf.union("a", "b")
        assert uf.connected("a", "b")

    def test_find_existing_element(self) -> None:
        """Test find on existing element returns its root."""
        uf = UnionFind(["elem"])
        root = uf.find("elem")
        assert root == "elem"

    def test_union_same_component(self) -> None:
        """Test union of elements already in same component."""
        uf = UnionFind(["a", "b", "c"])
        uf.union("a", "b")
        uf.union("a", "c")
        # Union a and c again - already connected
        uf.union("a", "c")
        assert uf.connected("a", "c")

    def test_path_compression(self) -> None:
        """Test that path compression works during find."""
        uf = UnionFind(["a", "b", "c", "d"])
        uf.union("a", "b")
        uf.union("b", "c")
        uf.union("c", "d")
        # Now find should compress the path
        root = uf.find("d")
        # All should share the same root
        assert uf.find("a") == root
        assert uf.find("b") == root
        assert uf.find("c") == root


class TestSelectByCoverage:
    """Branch coverage for select_by_coverage function."""

    def test_with_default_config(self) -> None:
        """Test with default config settings."""
        symbols = [
            make_symbol("s1", "func1"),
            make_symbol("s2", "func2"),
        ]
        edges = [make_edge("s1", "s2")]
        config = CompactConfig()

        result = select_by_coverage(
            symbols=symbols,
            edges=edges,
            config=config,
        )
        assert result.included.count >= 0

    def test_empty_symbols(self) -> None:
        """Test with no symbols."""
        config = CompactConfig(target_coverage=0.8)
        result = select_by_coverage(
            symbols=[],
            edges=[],
            config=config,
        )
        assert result.included.count == 0

    def test_with_force_include_ids(self) -> None:
        """Test with force_include_ids set."""
        symbols = [
            make_symbol("s1", "func1"),
            make_symbol("s2", "func2"),
            make_symbol("s3", "func3"),
        ]
        edges = [make_edge("s1", "s2")]
        config = CompactConfig(target_coverage=0.1, max_symbols=5)

        result = select_by_coverage(
            symbols=symbols,
            edges=edges,
            config=config,
            force_include_ids={"s2"},
        )
        # s2 should be in the included symbols
        included_ids = {s.id for s in result.included.symbols}
        assert "s2" in included_ids


class TestSelectByConnectivity:
    """Branch coverage for select_by_connectivity function."""

    def test_with_seed_ids(self) -> None:
        """Test with valid seed IDs."""
        symbols = [
            make_symbol("s1", "func1"),
            make_symbol("s2", "func2"),
        ]
        edges = [make_edge("s1", "s2")]

        result = select_by_connectivity(
            symbols=symbols,
            edges=edges,
            seed_ids={"s1"},
            max_additional=10,
        )
        assert result.included.count >= 1

    def test_empty_seed_ids_with_symbols(self) -> None:
        """Test with empty seed IDs but symbols exist."""
        symbols = [
            make_symbol("s1", "func1"),
            make_symbol("s2", "func2"),
        ]
        edges = [make_edge("s1", "s2")]

        result = select_by_connectivity(
            symbols=symbols,
            edges=edges,
            seed_ids=set(),
            max_additional=10,
        )
        # Result is still returned even with no seeds
        assert result is not None

    def test_provided_centrality(self) -> None:
        """Test with pre-computed centrality."""
        symbols = [
            make_symbol("s1", "func1"),
            make_symbol("s2", "func2"),
        ]
        edges = [make_edge("s1", "s2")]
        centrality = {"s1": 0.9, "s2": 0.1}

        result = select_by_connectivity(
            symbols=symbols,
            edges=edges,
            seed_ids={"s1"},
            max_additional=10,
            centrality=centrality,
        )
        assert len(result.included.symbols) > 0


class TestTokenizeName:
    """Branch coverage for tokenize_name function."""

    def test_camel_case(self) -> None:
        """Test CamelCase name tokenization."""
        tokens = tokenize_name("getUserName")
        # "get" is a stop word, so filtered out
        assert "user" in tokens
        assert "name" in tokens

    def test_snake_case(self) -> None:
        """Test snake_case name tokenization."""
        tokens = tokenize_name("get_user_name")
        # "get" is a stop word, so filtered out
        assert "user" in tokens
        assert "name" in tokens

    def test_short_name(self) -> None:
        """Test very short name."""
        tokens = tokenize_name("x")
        # Single char (< MIN_WORD_LENGTH=3) gets filtered
        assert isinstance(tokens, list)
        assert len(tokens) == 0

    def test_pascal_case_without_stop_words(self) -> None:
        """Test PascalCase name without stop words."""
        tokens = tokenize_name("UserProfileManager")
        assert "user" in tokens
        assert "profile" in tokens
        assert "manager" in tokens


class TestExtractPathPattern:
    """Branch coverage for extract_path_pattern function."""

    def test_nested_path(self) -> None:
        """Test deeply nested path."""
        pattern = extract_path_pattern("src/app/components/Button.tsx")
        assert "src/" in pattern or "components/" in pattern

    def test_simple_file(self) -> None:
        """Test file in root directory."""
        pattern = extract_path_pattern("main.py")
        assert isinstance(pattern, str)

    def test_minified_file(self) -> None:
        """Test minified file pattern detection."""
        pattern = extract_path_pattern("bundle.min.js")
        assert pattern == "*.min.*"

    def test_bundled_file(self) -> None:
        """Test bundled file pattern detection."""
        pattern = extract_path_pattern("app.bundle.js")
        assert pattern == "*.bundle.*"


class TestComputeFrequencies:
    """Branch coverage for frequency computation functions."""

    def test_word_frequencies_empty(self) -> None:
        """Test word frequencies with empty list."""
        result = compute_word_frequencies([])
        assert len(result) == 0

    def test_path_frequencies_empty(self) -> None:
        """Test path frequencies with empty list."""
        result = compute_path_frequencies([])
        assert len(result) == 0

    def test_kind_distribution_empty(self) -> None:
        """Test kind distribution with empty list."""
        result = compute_kind_distribution([])
        assert len(result) == 0

    def test_kind_distribution_mixed(self) -> None:
        """Test kind distribution with various kinds."""
        symbols = [
            make_symbol("s1", "func1", kind="function"),
            make_symbol("s2", "Class1", kind="class"),
            make_symbol("s3", "method1", kind="method"),
        ]
        result = compute_kind_distribution(symbols)
        assert result.get("function", 0) >= 1
        assert result.get("class", 0) >= 1
        assert result.get("method", 0) >= 1

    def test_word_frequencies_with_symbols(self) -> None:
        """Test word frequencies with symbols having tokenizable names."""
        symbols = [
            make_symbol("s1", "UserProfileManager"),
            make_symbol("s2", "ProfileValidator"),
        ]
        result = compute_word_frequencies(symbols)
        # "profile" appears in both names
        assert result.get("profile", 0) >= 2

    def test_path_frequencies_with_symbols(self) -> None:
        """Test path frequencies with symbols in different directories."""
        symbols = [
            make_symbol("s1", "func1", path="src/utils/helper.py"),
            make_symbol("s2", "func2", path="src/core/main.py"),
        ]
        result = compute_path_frequencies(symbols)
        # Both have "src/" prefix
        assert result.get("src/", 0) >= 2
