"""Tests for Wolfram Language analyzer.

Wolfram Language (also known as Mathematica) analysis uses tree-sitter to extract:
- Symbols: function definitions (SetDelayed), assignments, package declarations
- Edges: imports (Get, Import, Needs), function calls

Wolfram Language is a symbolic programming language used for technical computing,
data science, and mathematical modeling.

Test coverage includes:
- Function definition detection (SetDelayed :=)
- Assignment detection (Set =)
- Function call detection
- Import/Get detection
- Package structure detection
- Two-pass cross-file resolution
- Mocked unavailability behavior

Note: tree-sitter-wolfram is built from source and is NOT on PyPI.
Tests use mocking to ensure coverage even when tree-sitter-wolfram is unavailable.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def make_wolfram_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Wolfram file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestWolframAnalyzerAvailability:
    """Tests for tree-sitter-wolfram availability detection."""

    def test_is_wolfram_tree_sitter_available_true(self) -> None:
        """Check availability detection when tree-sitter-wolfram is installed."""
        from hypergumbo.analyze import wolfram as wolfram_module

        with patch.object(wolfram_module, "importlib") as mock_importlib:
            mock_importlib.util.find_spec.side_effect = lambda x: MagicMock() if x in ["tree_sitter", "tree_sitter_wolfram"] else None
            assert wolfram_module.is_wolfram_tree_sitter_available() is True

    def test_is_wolfram_tree_sitter_not_available_no_tree_sitter(self) -> None:
        """Check availability when tree_sitter is not installed."""
        from hypergumbo.analyze import wolfram as wolfram_module

        with patch.object(wolfram_module, "importlib") as mock_importlib:
            # tree_sitter not found
            mock_importlib.util.find_spec.return_value = None
            assert wolfram_module.is_wolfram_tree_sitter_available() is False

    def test_is_wolfram_tree_sitter_not_available_no_wolfram_grammar(self) -> None:
        """Check availability when tree_sitter_wolfram is not installed."""
        from hypergumbo.analyze import wolfram as wolfram_module

        with patch.object(wolfram_module, "importlib") as mock_importlib:
            # tree_sitter found but tree_sitter_wolfram not found
            def side_effect(name: str) -> MagicMock | None:
                if name == "tree_sitter":
                    return MagicMock()
                return None
            mock_importlib.util.find_spec.side_effect = side_effect
            assert wolfram_module.is_wolfram_tree_sitter_available() is False


class TestWolframAnalyzerWhenUnavailable:
    """Tests for graceful handling when tree-sitter-wolfram unavailable."""

    def test_analyze_wolfram_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analyzer returns skipped result when tree-sitter-wolfram not available."""
        from hypergumbo.analyze import wolfram as wolfram_module

        # Create a Wolfram file
        make_wolfram_file(tmp_path, "Example.wl", "x = 42")

        with patch.object(wolfram_module, "is_wolfram_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Wolfram analysis skipped"):
                result = wolfram_module.analyze_wolfram(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-wolfram" in result.skip_reason
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_returns_empty_when_no_wolfram_files(self, tmp_path: Path) -> None:
        """Returns empty result when no Wolfram files present."""
        from hypergumbo.analyze import wolfram as wolfram_module

        # Create a non-Wolfram file
        (tmp_path / "test.py").write_text("print('hello')")

        # Mock tree-sitter as available so we test the "no files" path
        with patch.object(wolfram_module, "is_wolfram_tree_sitter_available", return_value=True):
            # Mock the imports that would be done inside analyze_wolfram
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_wolfram = MagicMock()
            mock_parser = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_wolfram": mock_tree_sitter_wolfram,
            }):
                result = wolfram_module.analyze_wolfram(tmp_path)

        # Should return empty but not skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0
        assert result.skipped is False


class TestWolframAnalyzerWithMockedParser:
    """Tests for Wolfram analyzer with mocked tree-sitter parser.

    These tests mock the tree-sitter parser to return predictable AST nodes,
    allowing us to test symbol/edge extraction logic without requiring
    the actual tree-sitter-wolfram grammar to be installed.
    """

    def _create_mock_node(
        self,
        node_type: str,
        children: list | None = None,
        text: str = "",
        start_point: tuple[int, int] = (0, 0),
        end_point: tuple[int, int] = (0, 0),
        start_byte: int = 0,
        end_byte: int = 0,
    ) -> MagicMock:
        """Create a mock tree-sitter node."""
        node = MagicMock()
        node.type = node_type
        node.children = children or []
        node.start_point = start_point
        node.end_point = end_point
        node.start_byte = start_byte
        node.end_byte = end_byte
        return node

    def test_detect_function_definition(self, tmp_path: Path) -> None:
        """Detect function definition with SetDelayed (:=)."""
        from hypergumbo.analyze import wolfram as wolfram_module

        content = "f[x_] := x^2"
        make_wolfram_file(tmp_path, "Example.wl", content)

        # Create mock AST structure for: f[x_] := x^2
        # binary(call(symbol("f"), ...), ":=", ...)
        mock_func_symbol = self._create_mock_node(
            "symbol",
            start_point=(0, 0),
            end_point=(0, 1),
            start_byte=0,
            end_byte=1,
        )
        mock_call_node = self._create_mock_node(
            "call",
            children=[mock_func_symbol],
            start_point=(0, 0),
            end_point=(0, 5),
        )
        mock_setdelayed = self._create_mock_node(
            ":=",
            start_point=(0, 6),
            end_point=(0, 8),
        )
        mock_rhs = self._create_mock_node(
            "power",
            start_point=(0, 9),
            end_point=(0, 12),
        )
        mock_binary = self._create_mock_node(
            "binary",
            children=[mock_call_node, mock_setdelayed, mock_rhs],
            start_point=(0, 0),
            end_point=(0, 12),
        )
        mock_root = self._create_mock_node("source_file", children=[mock_binary])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(wolfram_module, "is_wolfram_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_wolfram = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_wolfram": mock_tree_sitter_wolfram,
            }):
                result = wolfram_module.analyze_wolfram(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        func = next((s for s in symbols if s.name == "f"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "wolfram"

    def test_detect_assignment(self, tmp_path: Path) -> None:
        """Detect assignment with Set (=)."""
        from hypergumbo.analyze import wolfram as wolfram_module

        content = "x = 42"
        make_wolfram_file(tmp_path, "Example.wl", content)

        # Create mock AST structure for: x = 42
        # binary(symbol("x"), "=", number(42))
        mock_var_symbol = self._create_mock_node(
            "symbol",
            start_point=(0, 0),
            end_point=(0, 1),
            start_byte=0,
            end_byte=1,
        )
        mock_set = self._create_mock_node(
            "=",
            start_point=(0, 2),
            end_point=(0, 3),
        )
        mock_value = self._create_mock_node(
            "number",
            start_point=(0, 4),
            end_point=(0, 6),
        )
        mock_binary = self._create_mock_node(
            "binary",
            children=[mock_var_symbol, mock_set, mock_value],
            start_point=(0, 0),
            end_point=(0, 6),
        )
        mock_root = self._create_mock_node("source_file", children=[mock_binary])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(wolfram_module, "is_wolfram_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_wolfram = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_wolfram": mock_tree_sitter_wolfram,
            }):
                result = wolfram_module.analyze_wolfram(tmp_path)

        symbols = result.symbols
        var = next((s for s in symbols if s.name == "x"), None)
        assert var is not None
        assert var.kind == "variable"
        assert var.language == "wolfram"

    def test_detect_builtin_calls(self, tmp_path: Path) -> None:
        """Detect calls to built-in functions."""
        from hypergumbo.analyze import wolfram as wolfram_module

        content = "result = Sin[x]"
        make_wolfram_file(tmp_path, "Example.wl", content)

        # Create mock AST structure for: result = Sin[x]
        # binary(symbol("result"), "=", call(symbol("Sin"), ...))
        mock_result_symbol = self._create_mock_node(
            "symbol",
            start_point=(0, 0),
            end_point=(0, 6),
            start_byte=0,
            end_byte=6,
        )
        mock_set = self._create_mock_node(
            "=",
            start_point=(0, 7),
            end_point=(0, 8),
        )
        mock_sin_symbol = self._create_mock_node(
            "symbol",
            start_point=(0, 9),
            end_point=(0, 12),
            start_byte=9,
            end_byte=12,
        )
        mock_sin_call = self._create_mock_node(
            "call",
            children=[mock_sin_symbol],
            start_point=(0, 9),
            end_point=(0, 15),
        )
        mock_binary = self._create_mock_node(
            "binary",
            children=[mock_result_symbol, mock_set, mock_sin_call],
            start_point=(0, 0),
            end_point=(0, 15),
        )
        mock_root = self._create_mock_node("source_file", children=[mock_binary])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(wolfram_module, "is_wolfram_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_wolfram = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_wolfram": mock_tree_sitter_wolfram,
            }):
                result = wolfram_module.analyze_wolfram(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # Should have call to Sin
        assert len(call_edges) >= 1
        assert any("Sin" in e.dst for e in call_edges)

    def test_detect_get_import(self, tmp_path: Path) -> None:
        """Detect Get import statement."""
        from hypergumbo.analyze import wolfram as wolfram_module

        content = 'Get["MyPackage`"]'
        make_wolfram_file(tmp_path, "Example.wl", content)

        # Create mock AST structure for: Get["MyPackage`"]
        # call(symbol("Get"), string("MyPackage`"))
        mock_get_symbol = self._create_mock_node(
            "symbol",
            start_point=(0, 0),
            end_point=(0, 3),
            start_byte=0,
            end_byte=3,
        )
        mock_string = self._create_mock_node(
            "string",
            start_point=(0, 4),
            end_point=(0, 16),
            start_byte=4,
            end_byte=16,
        )
        mock_get_call = self._create_mock_node(
            "call",
            children=[mock_get_symbol, mock_string],
            start_point=(0, 0),
            end_point=(0, 17),
        )
        mock_root = self._create_mock_node("source_file", children=[mock_get_call])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(wolfram_module, "is_wolfram_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_wolfram = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_wolfram": mock_tree_sitter_wolfram,
            }):
                result = wolfram_module.analyze_wolfram(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        targets = [e.dst for e in import_edges]
        assert any("MyPackage" in t for t in targets)

    def test_detect_needs_import(self, tmp_path: Path) -> None:
        """Detect Needs import statement."""
        from hypergumbo.analyze import wolfram as wolfram_module

        content = 'Needs["ComputationalGeometry`"]'
        make_wolfram_file(tmp_path, "Example.wl", content)

        # Create mock AST structure for: Needs["ComputationalGeometry`"]
        mock_needs_symbol = self._create_mock_node(
            "symbol",
            start_point=(0, 0),
            end_point=(0, 5),
            start_byte=0,
            end_byte=5,
        )
        mock_string = self._create_mock_node(
            "string",
            start_point=(0, 6),
            end_point=(0, 30),
            start_byte=6,
            end_byte=30,
        )
        mock_needs_call = self._create_mock_node(
            "call",
            children=[mock_needs_symbol, mock_string],
            start_point=(0, 0),
            end_point=(0, 31),
        )
        mock_root = self._create_mock_node("source_file", children=[mock_needs_call])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(wolfram_module, "is_wolfram_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_wolfram = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_wolfram": mock_tree_sitter_wolfram,
            }):
                result = wolfram_module.analyze_wolfram(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        targets = [e.dst for e in import_edges]
        assert any("ComputationalGeometry" in t for t in targets)
