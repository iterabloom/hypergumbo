"""Tests for the selection.language_proportional module."""

from hypergumbo.ir import Symbol, Span
from hypergumbo.selection.language_proportional import (
    group_symbols_by_language,
    group_files_by_language,
    allocate_language_budget,
    select_proportionally,
)


def make_symbol(name: str, language: str, path: str = "test.py") -> Symbol:
    """Helper to create a test symbol."""
    return Symbol(
        id=f"{path}:{name}",
        name=name,
        kind="function",
        language=language,
        path=path,
        span=Span(1, 0, 1, 10),
    )


class TestGroupSymbolsByLanguage:
    """Tests for group_symbols_by_language function."""

    def test_empty_list(self):
        """Empty list returns empty dict."""
        result = group_symbols_by_language([])
        assert result == {}

    def test_single_language(self):
        """Single language symbols grouped correctly."""
        symbols = [
            make_symbol("foo", "python"),
            make_symbol("bar", "python"),
        ]
        result = group_symbols_by_language(symbols)
        assert len(result) == 1
        assert "python" in result
        assert len(result["python"]) == 2

    def test_multiple_languages(self):
        """Multiple languages grouped correctly."""
        symbols = [
            make_symbol("foo", "python"),
            make_symbol("bar", "javascript"),
            make_symbol("baz", "python"),
        ]
        result = group_symbols_by_language(symbols)
        assert len(result) == 2
        assert len(result["python"]) == 2
        assert len(result["javascript"]) == 1


class TestGroupFilesByLanguage:
    """Tests for group_files_by_language function."""

    def test_empty_dict(self):
        """Empty dict returns empty dict."""
        result = group_files_by_language({})
        assert result == {}

    def test_single_file(self):
        """Single file grouped by its language."""
        by_file = {
            "test.py": [make_symbol("foo", "python", "test.py")]
        }
        result = group_files_by_language(by_file)
        assert "python" in result
        assert "test.py" in result["python"]

    def test_multiple_files_same_language(self):
        """Multiple files of same language grouped together."""
        by_file = {
            "a.py": [make_symbol("foo", "python", "a.py")],
            "b.py": [make_symbol("bar", "python", "b.py")],
        }
        result = group_files_by_language(by_file)
        assert len(result) == 1
        assert len(result["python"]) == 2

    def test_multiple_languages(self):
        """Files grouped by their language."""
        by_file = {
            "a.py": [make_symbol("foo", "python", "a.py")],
            "b.js": [make_symbol("bar", "javascript", "b.js")],
        }
        result = group_files_by_language(by_file)
        assert len(result) == 2
        assert "a.py" in result["python"]
        assert "b.js" in result["javascript"]

    def test_empty_file_excluded(self):
        """Files with no symbols are excluded."""
        by_file = {
            "a.py": [make_symbol("foo", "python", "a.py")],
            "empty.py": [],
        }
        result = group_files_by_language(by_file)
        assert len(result) == 1
        # empty.py should not appear anywhere
        for files in result.values():
            assert "empty.py" not in files


class TestAllocateLanguageBudget:
    """Tests for allocate_language_budget function."""

    def test_empty_groups(self):
        """Empty groups return empty budget."""
        result = allocate_language_budget({}, 100)
        assert result == {}

    def test_single_language_gets_all(self):
        """Single language gets full budget."""
        symbols = [make_symbol("foo", "python")]
        lang_groups = group_symbols_by_language(symbols)
        result = allocate_language_budget(lang_groups, 100)
        assert result["python"] == 100

    def test_proportional_allocation(self):
        """Budget allocated proportionally by symbol count."""
        symbols = [
            make_symbol("a", "python"),
            make_symbol("b", "python"),
            make_symbol("c", "python"),
            make_symbol("d", "javascript"),
        ]
        lang_groups = group_symbols_by_language(symbols)
        result = allocate_language_budget(lang_groups, 100, min_per_language=0)
        # Python: 3/4 = 75%, JS: 1/4 = 25%
        assert result["python"] == 75
        assert result["javascript"] == 25

    def test_min_per_language_floor(self):
        """Minimum guarantee per language."""
        symbols = [
            make_symbol(f"py{i}", "python") for i in range(99)
        ] + [make_symbol("js1", "javascript")]
        lang_groups = group_symbols_by_language(symbols)
        result = allocate_language_budget(lang_groups, 10, min_per_language=2)
        # JS should get at least 2 despite being 1%
        assert result["javascript"] >= 2

    def test_file_based_grouping(self):
        """Works with file-based grouping (dict of dicts)."""
        by_file = {
            "a.py": [make_symbol("foo", "python", "a.py")],
            "b.js": [make_symbol("bar", "javascript", "b.js")],
        }
        lang_groups = group_files_by_language(by_file)
        result = allocate_language_budget(lang_groups, 100)
        assert result["python"] == 50
        assert result["javascript"] == 50


class TestSelectProportionally:
    """Tests for select_proportionally function."""

    def test_empty_symbols(self):
        """Empty symbols return empty list."""
        result = select_proportionally([], {}, 100)
        assert result == []

    def test_single_language(self):
        """Single language selection works."""
        symbols = [
            make_symbol("a", "python"),
            make_symbol("b", "python"),
        ]
        centrality = {s.id: 1.0 for s in symbols}
        result = select_proportionally(symbols, centrality, 10)
        assert len(result) == 2

    def test_respects_max_symbols(self):
        """Does not exceed max_symbols."""
        symbols = [make_symbol(f"s{i}", "python") for i in range(100)]
        centrality = {s.id: 1.0 for s in symbols}
        result = select_proportionally(symbols, centrality, 10)
        assert len(result) <= 10

    def test_proportional_representation(self):
        """Multiple languages get proportional representation."""
        # 8 Python + 2 JavaScript = 80/20 split
        symbols = [
            make_symbol(f"py{i}", "python") for i in range(8)
        ] + [
            make_symbol(f"js{i}", "javascript") for i in range(2)
        ]
        centrality = {s.id: 1.0 for s in symbols}
        result = select_proportionally(symbols, centrality, 10)

        # Check languages in result
        python_count = sum(1 for s in result if s.language == "python")
        js_count = sum(1 for s in result if s.language == "javascript")

        # Both languages should be represented
        assert python_count > 0
        assert js_count > 0

    def test_sorted_by_centrality(self):
        """Result is sorted by centrality."""
        symbols = [
            make_symbol("a", "python"),
            make_symbol("b", "python"),
        ]
        centrality = {
            symbols[0].id: 1.0,
            symbols[1].id: 2.0,
        }
        result = select_proportionally(symbols, centrality, 10)
        # Higher centrality should come first
        assert result[0].name == "b"
        assert result[1].name == "a"

    def test_min_per_language(self):
        """Minimum per language is respected."""
        # 99 Python + 1 JavaScript
        symbols = [
            make_symbol(f"py{i}", "python") for i in range(99)
        ] + [make_symbol("js1", "javascript")]
        centrality = {s.id: 1.0 for s in symbols}
        result = select_proportionally(
            symbols, centrality, 10, min_per_language=2
        )

        js_count = sum(1 for s in result if s.language == "javascript")
        # JavaScript should have at least 1 (floor guarantee) despite being 1%
        # With min_per_language=2, it might get 2 if there were 2 JS symbols
        assert js_count >= 1
