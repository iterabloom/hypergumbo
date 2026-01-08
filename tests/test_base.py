"""Tests for hypergumbo.analyze.base module.

Tests the shared infrastructure used by all tree-sitter analyzers:
- iter_tree: Iterative tree traversal
- iter_tree_with_context: Context-aware traversal
- node_text, find_child_by_type, find_child_by_field: Node helpers
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from hypergumbo.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    find_child_by_field,
    find_child_by_type,
    is_grammar_available,
    iter_tree,
    iter_tree_with_context,
    make_file_id,
    make_symbol_id,
    node_text,
)

if TYPE_CHECKING:
    pass


class TestIterTree:
    """Tests for iter_tree function."""

    def test_iterates_all_nodes(self) -> None:
        """Should yield all nodes in the tree."""
        # Build mock tree: root -> [child1, child2 -> [grandchild]]
        grandchild = MagicMock()
        grandchild.children = []
        grandchild.type = "identifier"

        child1 = MagicMock()
        child1.children = []
        child1.type = "type"

        child2 = MagicMock()
        child2.children = [grandchild]
        child2.type = "function"

        root = MagicMock()
        root.children = [child1, child2]
        root.type = "program"

        nodes = list(iter_tree(root))

        assert len(nodes) == 4
        assert nodes[0] is root
        assert nodes[1] is child1
        assert nodes[2] is child2
        assert nodes[3] is grandchild

    def test_handles_empty_tree(self) -> None:
        """Should handle a tree with only root node."""
        root = MagicMock()
        root.children = []
        root.type = "program"

        nodes = list(iter_tree(root))

        assert len(nodes) == 1
        assert nodes[0] is root

    def test_depth_first_order(self) -> None:
        """Should traverse in depth-first order."""
        # Build: root -> [a -> [a1, a2], b -> [b1]]
        a1 = MagicMock(children=[], type="a1")
        a2 = MagicMock(children=[], type="a2")
        b1 = MagicMock(children=[], type="b1")
        a = MagicMock(children=[a1, a2], type="a")
        b = MagicMock(children=[b1], type="b")
        root = MagicMock(children=[a, b], type="root")

        types = [n.type for n in iter_tree(root)]

        # Depth-first: root, a, a1, a2, b, b1
        assert types == ["root", "a", "a1", "a2", "b", "b1"]


class TestIterTreeWithContext:
    """Tests for iter_tree_with_context function."""

    def test_tracks_function_context(self) -> None:
        """Should track enclosing function for nested nodes."""
        # Build: root -> [func_def -> [call_expr]]
        call_expr = MagicMock(children=[], type="call_expression")
        func_def = MagicMock(children=[call_expr], type="function_definition")
        root = MagicMock(children=[func_def], type="program")

        results = list(iter_tree_with_context(root, {"function_definition"}))

        # root has no context, func_def has no context (it IS the context),
        # call_expr has func_def as context
        assert results[0] == (root, None)
        assert results[1] == (func_def, None)
        assert results[2] == (call_expr, func_def)

    def test_no_context_outside_function(self) -> None:
        """Should return None context for nodes outside any function."""
        import_node = MagicMock(children=[], type="import")
        root = MagicMock(children=[import_node], type="program")

        results = list(iter_tree_with_context(root, {"function_definition"}))

        assert results[0] == (root, None)
        assert results[1] == (import_node, None)

    def test_nested_contexts(self) -> None:
        """Should track innermost context when nested."""
        # Build: root -> [outer_func -> [inner_func -> [call]]]
        call = MagicMock(children=[], type="call")
        inner_func = MagicMock(children=[call], type="function_definition")
        outer_func = MagicMock(children=[inner_func], type="function_definition")
        root = MagicMock(children=[outer_func], type="program")

        results = list(iter_tree_with_context(root, {"function_definition"}))

        # call should have inner_func as context
        call_result = next(r for r in results if r[0].type == "call")
        assert call_result[1] is inner_func

    def test_multiple_context_types(self) -> None:
        """Should track context for multiple node types."""
        method_body = MagicMock(children=[], type="body")
        method = MagicMock(children=[method_body], type="method_definition")
        class_def = MagicMock(children=[method], type="class_definition")
        root = MagicMock(children=[class_def], type="program")

        # Track both class and method definitions
        results = list(
            iter_tree_with_context(root, {"class_definition", "method_definition"})
        )

        # method_body should have method as context (innermost)
        body_result = next(r for r in results if r[0].type == "body")
        assert body_result[1] is method


class TestNodeText:
    """Tests for node_text function."""

    def test_extracts_text(self) -> None:
        """Should extract text from node byte range."""
        node = MagicMock()
        node.start_byte = 6
        node.end_byte = 11
        source = b"hello world"

        result = node_text(node, source)

        assert result == "world"

    def test_handles_unicode(self) -> None:
        """Should handle UTF-8 encoded text."""
        node = MagicMock()
        node.start_byte = 0
        node.end_byte = 6
        source = "日本語".encode("utf-8")

        result = node_text(node, source)

        assert result == "日本"


class TestFindChildByType:
    """Tests for find_child_by_type function."""

    def test_finds_matching_child(self) -> None:
        """Should return first child matching type."""
        child1 = MagicMock()
        child1.type = "identifier"
        child2 = MagicMock()
        child2.type = "type"
        node = MagicMock()
        node.children = [child1, child2]

        result = find_child_by_type(node, "type")

        assert result is child2

    def test_returns_none_when_not_found(self) -> None:
        """Should return None when no child matches."""
        child = MagicMock()
        child.type = "identifier"
        node = MagicMock()
        node.children = [child]

        result = find_child_by_type(node, "type")

        assert result is None


class TestFindChildByField:
    """Tests for find_child_by_field function."""

    def test_delegates_to_node_method(self) -> None:
        """Should delegate to node's child_by_field_name method."""
        expected = MagicMock()
        node = MagicMock()
        node.child_by_field_name.return_value = expected

        result = find_child_by_field(node, "name")

        node.child_by_field_name.assert_called_once_with("name")
        assert result is expected


class TestMakeSymbolId:
    """Tests for make_symbol_id function."""

    def test_generates_correct_format(self) -> None:
        """Should generate ID in expected format."""
        result = make_symbol_id("go", "main.go", 10, 20, "main", "function")

        assert result == "go:main.go:10-20:main:function"


class TestMakeFileId:
    """Tests for make_file_id function."""

    def test_generates_correct_format(self) -> None:
        """Should generate file ID in expected format."""
        result = make_file_id("python", "src/main.py")

        assert result == "python:src/main.py:1-1:file:file"


class TestIsGrammarAvailable:
    """Tests for is_grammar_available function."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when grammar is importable."""
        # tree_sitter_go is installed in test environment
        result = is_grammar_available("tree_sitter_go")

        assert result is True

    def test_returns_false_when_missing(self) -> None:
        """Should return False when grammar is not installed."""
        result = is_grammar_available("tree_sitter_nonexistent_language")

        assert result is False


class TestAnalysisResult:
    """Tests for AnalysisResult dataclass."""

    def test_default_values(self) -> None:
        """Should have sensible defaults."""
        result = AnalysisResult()

        assert result.symbols == []
        assert result.edges == []
        assert result.run is None
        assert result.skipped is False
        assert result.skip_reason == ""


class TestFileAnalysis:
    """Tests for FileAnalysis dataclass."""

    def test_default_values(self) -> None:
        """Should have sensible defaults."""
        analysis = FileAnalysis()

        assert analysis.symbols == []
        assert analysis.symbol_by_name == {}
