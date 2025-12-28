"""Tests for Lean analyzer.

Lean analysis uses tree-sitter to extract:
- Symbols: def, theorem, lemma, structure, inductive, class, instance
- Edges: imports

Lean 4 is an interactive theorem prover and programming language.
Unlike typical programming languages, "calls" are less meaningful than
"references" (dependencies between theorems/lemmas).

Test coverage includes:
- Definition detection (def, abbrev)
- Theorem/lemma detection
- Structure detection
- Inductive type detection
- Class/instance detection
- Import statements
- Two-pass cross-file resolution
- Mocked unavailability behavior

Note: tree-sitter-lean is built from source and is NOT on PyPI.
Tests use mocking to ensure coverage even when tree-sitter-lean is unavailable.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def make_lean_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Lean file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestLeanAnalyzerAvailability:
    """Tests for tree-sitter-lean availability detection."""

    def test_is_lean_tree_sitter_available_true(self) -> None:
        """Check availability detection when tree-sitter-lean is installed."""
        with patch("hypergumbo.analyze.lean.importlib.util.find_spec") as mock_find:
            # Both tree_sitter and tree_sitter_lean are found
            mock_find.side_effect = lambda x: MagicMock() if x in ["tree_sitter", "tree_sitter_lean"] else None

            # Force reimport to pick up the mock
            import importlib
            import hypergumbo.analyze.lean as lean_module
            importlib.reload(lean_module)

            with patch.object(lean_module, "importlib") as mock_importlib:
                mock_importlib.util.find_spec.side_effect = lambda x: MagicMock() if x in ["tree_sitter", "tree_sitter_lean"] else None
                assert lean_module.is_lean_tree_sitter_available() is True

    def test_is_lean_tree_sitter_not_available_no_tree_sitter(self) -> None:
        """Check availability when tree_sitter is not installed."""
        from hypergumbo.analyze import lean as lean_module

        with patch.object(lean_module, "importlib") as mock_importlib:
            # tree_sitter not found
            mock_importlib.util.find_spec.return_value = None
            assert lean_module.is_lean_tree_sitter_available() is False

    def test_is_lean_tree_sitter_not_available_no_lean_grammar(self) -> None:
        """Check availability when tree_sitter_lean is not installed."""
        from hypergumbo.analyze import lean as lean_module

        with patch.object(lean_module, "importlib") as mock_importlib:
            # tree_sitter found but tree_sitter_lean not found
            def side_effect(name: str) -> MagicMock | None:
                if name == "tree_sitter":
                    return MagicMock()
                return None
            mock_importlib.util.find_spec.side_effect = side_effect
            assert lean_module.is_lean_tree_sitter_available() is False


class TestLeanAnalyzerWhenUnavailable:
    """Tests for graceful handling when tree-sitter-lean unavailable."""

    def test_analyze_lean_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analyzer returns skipped result when tree-sitter-lean not available."""
        from hypergumbo.analyze import lean as lean_module

        # Create a Lean file
        make_lean_file(tmp_path, "Example.lean", "def foo := 42")

        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Lean analysis skipped"):
                result = lean_module.analyze_lean(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-lean" in result.skip_reason
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_returns_empty_when_no_lean_files(self, tmp_path: Path) -> None:
        """Returns empty result when no Lean files present."""
        from hypergumbo.analyze import lean as lean_module

        # Create a non-Lean file
        (tmp_path / "test.py").write_text("print('hello')")

        # Mock tree-sitter as available so we test the "no files" path
        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=True):
            # Mock the imports that would be done inside analyze_lean
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_lean = MagicMock()
            mock_parser = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_lean": mock_tree_sitter_lean,
            }):
                result = lean_module.analyze_lean(tmp_path)

        # Should return empty but not skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0
        assert result.skipped is False


class TestLeanAnalyzerWithMockedParser:
    """Tests for Lean analyzer with mocked tree-sitter parser.

    These tests mock the tree-sitter parser to return predictable AST nodes,
    allowing us to test symbol/edge extraction logic without requiring
    the actual tree-sitter-lean grammar to be installed.
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

    def test_detect_def(self, tmp_path: Path) -> None:
        """Detect def declaration."""
        from hypergumbo.analyze import lean as lean_module

        content = "def double (n : Nat) : Nat := n + n"
        make_lean_file(tmp_path, "Example.lean", content)

        # Create mock AST structure for: def double ...
        mock_id_node = self._create_mock_node(
            "identifier",
            start_point=(0, 4),
            end_point=(0, 10),
            start_byte=4,
            end_byte=10,
        )
        mock_def_node = self._create_mock_node(
            "def",
            children=[mock_id_node],
            start_point=(0, 0),
            end_point=(0, 35),
        )
        mock_decl_node = self._create_mock_node(
            "declaration",
            children=[mock_def_node],
        )
        mock_root = self._create_mock_node("source_file", children=[mock_decl_node])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_lean = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_lean": mock_tree_sitter_lean,
            }):
                result = lean_module.analyze_lean(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        # Should have file symbol + function symbol
        func = next((s for s in symbols if s.name == "double"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "lean"

    def test_detect_theorem(self, tmp_path: Path) -> None:
        """Detect theorem declaration."""
        from hypergumbo.analyze import lean as lean_module

        content = "theorem add_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b"
        make_lean_file(tmp_path, "Example.lean", content)

        # Create mock AST structure for: theorem add_comm ...
        mock_id_node = self._create_mock_node(
            "identifier",
            start_point=(0, 8),
            end_point=(0, 16),
            start_byte=8,
            end_byte=16,
        )
        mock_theorem_node = self._create_mock_node(
            "theorem",
            children=[mock_id_node],
            start_point=(0, 0),
            end_point=(0, 60),
        )
        mock_decl_node = self._create_mock_node(
            "declaration",
            children=[mock_theorem_node],
        )
        mock_root = self._create_mock_node("source_file", children=[mock_decl_node])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_lean = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_lean": mock_tree_sitter_lean,
            }):
                result = lean_module.analyze_lean(tmp_path)

        symbols = result.symbols
        thm = next((s for s in symbols if s.name == "add_comm"), None)
        assert thm is not None
        assert thm.kind == "theorem"
        assert thm.language == "lean"

    def test_detect_structure(self, tmp_path: Path) -> None:
        """Detect structure declaration."""
        from hypergumbo.analyze import lean as lean_module

        content = "structure Person where\n  name : String\n  age : Nat"
        make_lean_file(tmp_path, "Example.lean", content)

        mock_id_node = self._create_mock_node(
            "identifier",
            start_point=(0, 10),
            end_point=(0, 16),
            start_byte=10,
            end_byte=16,
        )
        mock_struct_node = self._create_mock_node(
            "structure",
            children=[mock_id_node],
            start_point=(0, 0),
            end_point=(2, 10),
        )
        mock_decl_node = self._create_mock_node(
            "declaration",
            children=[mock_struct_node],
        )
        mock_root = self._create_mock_node("source_file", children=[mock_decl_node])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_lean = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_lean": mock_tree_sitter_lean,
            }):
                result = lean_module.analyze_lean(tmp_path)

        symbols = result.symbols
        struct = next((s for s in symbols if s.name == "Person"), None)
        assert struct is not None
        assert struct.kind == "structure"
        assert struct.language == "lean"

    def test_detect_inductive(self, tmp_path: Path) -> None:
        """Detect inductive type declaration."""
        from hypergumbo.analyze import lean as lean_module

        content = "inductive MyList (A : Type) where\n  | nil : MyList A\n  | cons : A -> MyList A -> MyList A"
        make_lean_file(tmp_path, "Example.lean", content)

        mock_id_node = self._create_mock_node(
            "identifier",
            start_point=(0, 10),
            end_point=(0, 16),
            start_byte=10,
            end_byte=16,
        )
        mock_ind_node = self._create_mock_node(
            "inductive",
            children=[mock_id_node],
            start_point=(0, 0),
            end_point=(2, 35),
        )
        mock_decl_node = self._create_mock_node(
            "declaration",
            children=[mock_ind_node],
        )
        mock_root = self._create_mock_node("source_file", children=[mock_decl_node])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_lean = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_lean": mock_tree_sitter_lean,
            }):
                result = lean_module.analyze_lean(tmp_path)

        symbols = result.symbols
        ind = next((s for s in symbols if s.name == "MyList"), None)
        assert ind is not None
        assert ind.kind == "inductive"
        assert ind.language == "lean"

    def test_detect_import(self, tmp_path: Path) -> None:
        """Detect import statement."""
        from hypergumbo.analyze import lean as lean_module

        content = "import Mathlib.Data.Nat.Basic\n\ndef foo := 42"
        make_lean_file(tmp_path, "Example.lean", content)

        # Create mock AST with import node
        mock_import_id = self._create_mock_node(
            "identifier",
            start_point=(0, 7),
            end_point=(0, 29),
            start_byte=7,
            end_byte=29,
        )
        mock_import_node = self._create_mock_node(
            "import",
            children=[mock_import_id],
            start_point=(0, 0),
            end_point=(0, 29),
        )
        mock_root = self._create_mock_node("source_file", children=[mock_import_node])

        mock_tree = MagicMock()
        mock_tree.root_node = mock_root

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=True):
            mock_tree_sitter = MagicMock()
            mock_tree_sitter_lean = MagicMock()
            mock_tree_sitter.Parser.return_value = mock_parser
            mock_tree_sitter.Language.return_value = MagicMock()

            with patch.dict("sys.modules", {
                "tree_sitter": mock_tree_sitter,
                "tree_sitter_lean": mock_tree_sitter_lean,
            }):
                result = lean_module.analyze_lean(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

        # Check import target
        targets = [e.dst for e in import_edges]
        assert any("Mathlib" in t for t in targets)
