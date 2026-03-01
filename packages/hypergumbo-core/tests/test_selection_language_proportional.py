"""Tests for the selection.language_proportional module."""

from hypergumbo_core.ir import Edge, Symbol, Span
from hypergumbo_core.selection.language_proportional import (
    find_underrepresented_language_seeds,
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


def make_edge(src_id: str, dst_id: str) -> Edge:
    """Helper to create a test edge."""
    return Edge(
        id=f"edge:{src_id}->{dst_id}",
        src=src_id,
        dst=dst_id,
        edge_type="calls",
        line=1,
        confidence=0.9,
    )


class TestFindUnderrepresentedLanguageSeeds:
    """Tests for find_underrepresented_language_seeds function."""

    def test_no_underrepresented_languages(self):
        """When all significant languages are reachable, returns empty."""
        py_main = make_symbol("main", "python", "main.py")
        py_a = make_symbol("a", "python", "a.py")
        edges = [make_edge(py_main.id, py_a.id)]
        result = find_underrepresented_language_seeds(
            [py_main, py_a], edges, {py_main.id}
        )
        assert result == set()

    def test_isolated_entrypoint_triggers_injection(self):
        """Dominant language with isolated entrypoint gets a seed injected."""
        # C entrypoint with zero edges (function pointer dispatch)
        c_main = make_symbol("main", "c", "main.c")
        # Dense C subgraph
        c_a = make_symbol("func_a", "c", "core.c")
        c_b = make_symbol("func_b", "c", "core.c")
        c_c = make_symbol("func_c", "c", "core.c")
        c_edges = [
            make_edge(c_a.id, c_b.id),
            make_edge(c_b.id, c_c.id),
            make_edge(c_a.id, c_c.id),
        ]
        # Small Python cluster (reachable from py_main)
        py_main = make_symbol("py_main", "python", "tool.py")
        py_a = make_symbol("helper", "python", "tool.py")
        py_edges = [make_edge(py_main.id, py_a.id)]

        all_symbols = [c_main, c_a, c_b, c_c, py_main, py_a]
        all_edges = c_edges + py_edges

        result = find_underrepresented_language_seeds(
            all_symbols, all_edges, {c_main.id, py_main.id}
        )
        # Should inject a C node (c_a has highest degree: 2 out + 0 in = 2)
        assert len(result) == 1
        injected = result.pop()
        assert any(s.id == injected and s.language == "c" for s in all_symbols)

    def test_picks_highest_degree_node(self):
        """The injected seed is the highest-degree node in its language."""
        c_main = make_symbol("main", "c", "main.c")
        # c_hub has the most edges
        c_hub = make_symbol("hub", "c", "hub.c")
        c_leaf1 = make_symbol("leaf1", "c", "leaf.c")
        c_leaf2 = make_symbol("leaf2", "c", "leaf.c")
        c_leaf3 = make_symbol("leaf3", "c", "leaf.c")
        c_edges = [
            make_edge(c_hub.id, c_leaf1.id),
            make_edge(c_hub.id, c_leaf2.id),
            make_edge(c_hub.id, c_leaf3.id),
        ]
        # Small Python with frontier access
        py_main = make_symbol("py_main", "python", "tool.py")
        py_a = make_symbol("py_a", "python", "tool.py")
        py_edges = [make_edge(py_main.id, py_a.id)]

        all_symbols = [c_main, c_hub, c_leaf1, c_leaf2, c_leaf3, py_main, py_a]
        all_edges = c_edges + py_edges

        result = find_underrepresented_language_seeds(
            all_symbols, all_edges, {c_main.id, py_main.id}
        )
        assert result == {c_hub.id}

    def test_below_threshold_not_injected(self):
        """Languages below edge_share_threshold are not injected."""
        # 1 C edge, 20 Python edges → C has ~5% share (below 10%)
        py_funcs = [make_symbol(f"py_{i}", "python", "lib.py") for i in range(21)]
        py_edges = [make_edge(py_funcs[i].id, py_funcs[i + 1].id) for i in range(20)]

        c_a = make_symbol("c_a", "c", "lib.c")
        c_b = make_symbol("c_b", "c", "lib.c")
        c_edges = [make_edge(c_a.id, c_b.id)]

        # py_funcs[0] is the seed with frontier access to Python
        all_symbols = py_funcs + [c_a, c_b]
        all_edges = py_edges + c_edges

        result = find_underrepresented_language_seeds(
            all_symbols, all_edges, {py_funcs[0].id}
        )
        # C has only ~5% of edges, below 10% threshold → no injection
        assert result == set()

    def test_empty_edges(self):
        """No edges returns empty seeds."""
        s = make_symbol("main", "c", "main.c")
        result = find_underrepresented_language_seeds([s], [], {s.id})
        assert result == set()

    def test_already_in_frontier_not_injected(self):
        """Language already reachable via frontier gets no injection."""
        c_main = make_symbol("main", "c", "main.c")
        c_a = make_symbol("func_a", "c", "core.c")
        # c_main has an edge to c_a → C is in the frontier
        edges = [make_edge(c_main.id, c_a.id)]

        result = find_underrepresented_language_seeds(
            [c_main, c_a], edges, {c_main.id}
        )
        assert result == set()
