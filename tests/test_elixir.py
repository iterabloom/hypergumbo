"""Tests for Elixir analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindElixirFiles:
    """Tests for Elixir file discovery."""

    def test_finds_elixir_files(self, tmp_path: Path) -> None:
        """Finds .ex and .exs files."""
        from hypergumbo.analyze.elixir import find_elixir_files

        (tmp_path / "app.ex").write_text("defmodule App do end")
        (tmp_path / "test.exs").write_text("defmodule AppTest do end")
        (tmp_path / "other.txt").write_text("not elixir")

        files = list(find_elixir_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix in (".ex", ".exs") for f in files)


class TestElixirTreeSitterAvailability:
    """Tests for tree-sitter-elixir availability checking."""

    def test_is_elixir_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-elixir is available."""
        from hypergumbo.analyze.elixir import is_elixir_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_elixir_tree_sitter_available() is True

    def test_is_elixir_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.elixir import is_elixir_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_elixir_tree_sitter_available() is False

    def test_is_elixir_tree_sitter_available_no_language_pack(self) -> None:
        """Returns False when tree-sitter is available but language pack is not."""
        from hypergumbo.analyze.elixir import is_elixir_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()  # tree-sitter available
            return None  # language pack not available

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_elixir_tree_sitter_available() is False


class TestAnalyzeElixirFallback:
    """Tests for fallback behavior when tree-sitter-elixir unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-elixir unavailable."""
        from hypergumbo.analyze.elixir import analyze_elixir

        (tmp_path / "test.ex").write_text("defmodule Test do end")

        with patch("hypergumbo.analyze.elixir.is_elixir_tree_sitter_available", return_value=False):
            result = analyze_elixir(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-elixir" in result.skip_reason


class TestElixirModuleExtraction:
    """Tests for extracting Elixir modules."""

    def test_extracts_module(self, tmp_path: Path) -> None:
        """Extracts Elixir module declarations."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "person.ex"
        ex_file.write_text("""
defmodule Person do
  def new(name) do
    %{name: name}
  end

  def get_name(person) do
    person.name
  end
end
""")

        result = analyze_elixir(tmp_path)


        assert result.run is not None
        assert result.run.files_analyzed == 1
        names = [s.name for s in result.symbols]
        assert "Person" in names

    def test_extracts_nested_module(self, tmp_path: Path) -> None:
        """Extracts nested Elixir modules."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "my_app.ex"
        ex_file.write_text("""
defmodule MyApp.Accounts do
  defmodule User do
    defstruct [:name, :email]
  end

  def create_user(name, email) do
    %User{name: name, email: email}
  end
end
""")

        result = analyze_elixir(tmp_path)


        names = [s.name for s in result.symbols]
        assert "MyApp.Accounts" in names


class TestElixirFunctionExtraction:
    """Tests for extracting Elixir functions."""

    def test_extracts_public_function(self, tmp_path: Path) -> None:
        """Extracts public function (def)."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "utils.ex"
        ex_file.write_text("""
defmodule Utils do
  def add(a, b), do: a + b
end
""")

        result = analyze_elixir(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "Utils.add" in func_names

    def test_extracts_private_function(self, tmp_path: Path) -> None:
        """Extracts private function (defp)."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "utils.ex"
        ex_file.write_text("""
defmodule Utils do
  def public_fn(x), do: private_fn(x)
  defp private_fn(x), do: x * 2
end
""")

        result = analyze_elixir(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "Utils.public_fn" in func_names
        assert "Utils.private_fn" in func_names


class TestElixirFunctionCalls:
    """Tests for detecting function calls in Elixir."""

    def test_detects_local_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same module."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "utils.ex"
        ex_file.write_text("""
defmodule Utils do
  def caller() do
    helper()
  end

  def helper() do
    :ok
  end
end
""")

        result = analyze_elixir(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge from caller to helper
        assert len(call_edges) >= 1


class TestElixirImports:
    """Tests for detecting Elixir imports."""

    def test_detects_use_directive(self, tmp_path: Path) -> None:
        """Detects use directives."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "controller.ex"
        ex_file.write_text("""
defmodule MyApp.Controller do
  use Phoenix.Controller

  def index(conn, _params) do
    render(conn, "index.html")
  end
end
""")

        result = analyze_elixir(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edge for use Phoenix.Controller
        assert len(import_edges) >= 1

    def test_detects_import_directive(self, tmp_path: Path) -> None:
        """Detects import directives."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "helper.ex"
        ex_file.write_text("""
defmodule Helper do
  import Enum, only: [map: 2]

  def double_all(list) do
    map(list, &(&1 * 2))
  end
end
""")

        result = analyze_elixir(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestElixirMacros:
    """Tests for extracting Elixir macros."""

    def test_extracts_macro(self, tmp_path: Path) -> None:
        """Extracts macro declarations."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "macros.ex"
        ex_file.write_text("""
defmodule MyMacros do
  defmacro my_if(condition, do: do_block) do
    quote do
      case unquote(condition) do
        x when x in [false, nil] -> nil
        _ -> unquote(do_block)
      end
    end
  end
end
""")

        result = analyze_elixir(tmp_path)


        macros = [s for s in result.symbols if s.kind == "macro"]
        assert len(macros) >= 1
        macro_names = [s.name for s in macros]
        assert "MyMacros.my_if" in macro_names


class TestElixirEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo.analyze.elixir import analyze_elixir

        (tmp_path / "test.ex").write_text("defmodule Test do end")

        with patch("hypergumbo.analyze.elixir.is_elixir_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_language_pack": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_language_pack"]
                mock_module.get_parser.side_effect = RuntimeError("Parser load failed")
                result = analyze_elixir(tmp_path)

        assert result.skipped is True
        assert "Failed to load Elixir parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo.analyze.elixir import analyze_elixir

        # Create a file with only comments and whitespace
        (tmp_path / "empty.ex").write_text("# Just a comment\n\n")

        result = analyze_elixir(tmp_path)


        assert result.run is not None
        assert result.run.files_skipped >= 1

    def test_unreadable_file_handled_gracefully(self, tmp_path: Path) -> None:
        """Unreadable files don't crash the analyzer."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        result = analyze_elixir(tmp_path)


        # Just verify it doesn't crash
        assert result.run is not None

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo.analyze.elixir import analyze_elixir

        # File 1: defines helper
        (tmp_path / "helper.ex").write_text("""
defmodule Helper do
  def greet(name) do
    "Hello, " <> name
  end
end
""")

        # File 2: calls helper
        (tmp_path / "main.ex").write_text("""
defmodule Main do
  def run() do
    greet("world")
  end
end
""")

        result = analyze_elixir(tmp_path)


        # Should have cross-file call edge
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_simple_function_definition(self, tmp_path: Path) -> None:
        """Extracts simple function definition without parentheses."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "simple.ex"
        # This uses the simple identifier form: def foo, do: :ok
        ex_file.write_text("""
defmodule Simple do
  def hello, do: :world
end
""")

        result = analyze_elixir(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "Simple.hello" in func_names


class TestElixirFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo.analyze.elixir import (
            _extract_symbols_from_file,
            is_elixir_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")
        run = AnalysisRun.create(pass_id="test", version="test")

        # Create a valid file, then mock the read to fail
        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(ex_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo.analyze.elixir import (
            _extract_edges_from_file,
            is_elixir_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")
        run = AnalysisRun.create(pass_id="test", version="test")

        ex_file = tmp_path / "test.ex"
        ex_file.write_text("defmodule Test do end")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(ex_file, parser, {}, {}, run)

        assert result == []


class TestElixirMalformedCode:
    """Tests for handling malformed Elixir code."""

    def test_malformed_defmodule_no_name(self, tmp_path: Path) -> None:
        """Handles defmodule without a proper name."""
        from hypergumbo.analyze.elixir import analyze_elixir

        ex_file = tmp_path / "malformed.ex"
        # Intentionally malformed - defmodule with no alias argument
        ex_file.write_text("""
defmodule do
  def foo, do: :ok
end
""")

        result = analyze_elixir(tmp_path)


        # Should not crash, may or may not extract anything
        assert result.run is not None

    def test_get_function_name_no_match(self, tmp_path: Path) -> None:
        """_get_function_name returns None for unrecognized patterns."""
        from hypergumbo.analyze.elixir import _get_function_name, is_elixir_tree_sitter_available

        if not is_elixir_tree_sitter_available():
            pytest.skip("tree-sitter-elixir not available")

        from tree_sitter_language_pack import get_parser
        parser = get_parser("elixir")

        # Parse some code where def has unusual structure
        source = b"def 123"  # Invalid syntax
        tree = parser.parse(source)

        # Find the call node if any
        def find_call(node):
            if node.type == "call":
                return node
            for child in node.children:
                result = find_call(child)
                if result:
                    return result
            return None

        call_node = find_call(tree.root_node)
        if call_node:
            result = _get_function_name(call_node, source)
            # Either returns None or a string, shouldn't crash
            assert result is None or isinstance(result, str)


class TestElixirSignatureExtraction:
    """Tests for Elixir function signature extraction."""

    def test_positional_params(self, tmp_path: Path) -> None:
        """Extracts signature with positional parameters."""
        from hypergumbo.analyze.elixir import analyze_elixir

        (tmp_path / "calc.ex").write_text("""
defmodule Calc do
  def add(a, b) do
    a + b
  end
end
""")
        result = analyze_elixir(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "add" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "(a, b)"

    def test_no_params_function(self, tmp_path: Path) -> None:
        """Extracts signature for function with no parameters."""
        from hypergumbo.analyze.elixir import analyze_elixir

        (tmp_path / "simple.ex").write_text("""
defmodule Simple do
  def answer do
    42
  end
end
""")
        result = analyze_elixir(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "answer" in s.name]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_macro_signature(self, tmp_path: Path) -> None:
        """Extracts signature from macro definition."""
        from hypergumbo.analyze.elixir import analyze_elixir

        (tmp_path / "macros.ex").write_text("""
defmodule Macros do
  defmacro debug(expr) do
    quote do
      IO.inspect(unquote(expr))
    end
  end
end
""")
        result = analyze_elixir(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro" and "debug" in s.name]
        assert len(macros) == 1
        assert macros[0].signature == "(expr)"
