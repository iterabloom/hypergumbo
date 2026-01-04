"""Tests for Julia analyzer."""
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestJuliaHelpers:
    """Tests for Julia analyzer helper functions."""

    def test_find_child_by_type_returns_none(self) -> None:
        """Returns None when no matching child type is found."""
        from hypergumbo.analyze.julia import _find_child_by_type

        mock_node = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "different_type"
        mock_node.children = [mock_child]

        result = _find_child_by_type(mock_node, "identifier")
        assert result is None


class TestFindJuliaFiles:
    """Tests for Julia file discovery."""

    def test_finds_julia_files(self, tmp_path: Path) -> None:
        """Finds .jl files."""
        from hypergumbo.analyze.julia import find_julia_files

        (tmp_path / "Main.jl").write_text("function main() end")
        (tmp_path / "Utils.jl").write_text("module Utils end")
        (tmp_path / "other.txt").write_text("not julia")

        files = list(find_julia_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".jl" for f in files)


class TestJuliaTreeSitterAvailability:
    """Tests for tree-sitter-julia availability checking."""

    def test_is_julia_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-julia is available."""
        from hypergumbo.analyze.julia import is_julia_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()
            assert is_julia_tree_sitter_available() is True

    def test_is_julia_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.julia import is_julia_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_julia_tree_sitter_available() is False

    def test_is_julia_tree_sitter_available_no_julia(self) -> None:
        """Returns False when tree-sitter is available but julia grammar is not."""
        from hypergumbo.analyze.julia import is_julia_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()
            return None

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_julia_tree_sitter_available() is False


class TestAnalyzeJuliaFallback:
    """Tests for fallback behavior when tree-sitter-julia unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-julia unavailable."""
        from hypergumbo.analyze.julia import analyze_julia

        (tmp_path / "test.jl").write_text("function test() end")

        with patch("hypergumbo.analyze.julia.is_julia_tree_sitter_available", return_value=False):
            result = analyze_julia(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-julia" in result.skip_reason


class TestJuliaModuleExtraction:
    """Tests for extracting Julia modules."""

    def test_extracts_module(self, tmp_path: Path) -> None:
        """Extracts module declarations."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "MyModule.jl"
        julia_file.write_text("""
module MyModule

function greet()
    println("Hello!")
end

end
""")

        result = analyze_julia(tmp_path)


        assert result.run is not None
        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) >= 1
        assert any(s.name == "MyModule" for s in modules)


class TestJuliaFunctionExtraction:
    """Tests for extracting Julia functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts function declarations."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Utils.jl"
        julia_file.write_text("""
function greet(name)
    println("Hello, name!")
end

function add(a, b)
    return a + b
end
""")

        result = analyze_julia(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "greet" in func_names
        assert "add" in func_names

    def test_extracts_short_form_function(self, tmp_path: Path) -> None:
        """Extracts short-form function definitions (f(x) = expr)."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Short.jl"
        julia_file.write_text("""
double(x) = 2 * x
square(x) = x * x
""")

        result = analyze_julia(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "double" in func_names
        assert "square" in func_names


class TestJuliaStructExtraction:
    """Tests for extracting Julia structs."""

    def test_extracts_struct(self, tmp_path: Path) -> None:
        """Extracts struct declarations."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Types.jl"
        julia_file.write_text("""
struct Point
    x::Float64
    y::Float64
end

mutable struct Circle
    radius::Float64
end
""")

        result = analyze_julia(tmp_path)


        structs = [s for s in result.symbols if s.kind == "struct"]
        struct_names = [s.name for s in structs]
        assert "Point" in struct_names
        assert "Circle" in struct_names


class TestJuliaAbstractTypeExtraction:
    """Tests for extracting Julia abstract types."""

    def test_extracts_abstract_type(self, tmp_path: Path) -> None:
        """Extracts abstract type declarations."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Abstract.jl"
        julia_file.write_text("""
abstract type Shape end
abstract type Animal end
""")

        result = analyze_julia(tmp_path)


        abstracts = [s for s in result.symbols if s.kind == "abstract"]
        abstract_names = [s.name for s in abstracts]
        assert "Shape" in abstract_names
        assert "Animal" in abstract_names


class TestJuliaMacroExtraction:
    """Tests for extracting Julia macros."""

    def test_extracts_macro(self, tmp_path: Path) -> None:
        """Extracts macro declarations."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Macros.jl"
        julia_file.write_text("""
macro sayhello()
    println("Hello from macro!")
end

macro debug(ex)
    println(ex)
end
""")

        result = analyze_julia(tmp_path)


        macros = [s for s in result.symbols if s.kind == "macro"]
        macro_names = [s.name for s in macros]
        assert "sayhello" in macro_names
        assert "debug" in macro_names


class TestJuliaImportEdges:
    """Tests for extracting import statements."""

    def test_extracts_imports(self, tmp_path: Path) -> None:
        """Extracts import statements as edges."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Main.jl"
        julia_file.write_text("""
import Base.show
import LinearAlgebra
using Statistics
""")

        result = analyze_julia(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

        imported = [e.dst for e in import_edges]
        assert any("Base.show" in dst or "Base" in dst for dst in imported)


class TestJuliaCallEdges:
    """Tests for extracting function call edges."""

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between functions."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Main.jl"
        julia_file.write_text("""
function helper()
    println("helping")
end

function main()
    helper()
end
""")

        result = analyze_julia(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_extracts_cross_file_call_edges(self, tmp_path: Path) -> None:
        """Extracts call edges between functions in different files."""
        from hypergumbo.analyze.julia import analyze_julia

        helper_file = tmp_path / "Helper.jl"
        helper_file.write_text("""
function do_work()
    println("working")
end
""")

        main_file = tmp_path / "Main.jl"
        main_file.write_text("""
function run()
    do_work()
end
""")

        result = analyze_julia(tmp_path)


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

        # Check for cross-file call edge with lower confidence
        cross_file_edges = [e for e in call_edges if e.confidence == 0.80]
        assert len(cross_file_edges) >= 1


class TestJuliaSymbolProperties:
    """Tests for symbol property correctness."""

    def test_symbol_has_correct_span(self, tmp_path: Path) -> None:
        """Symbols have correct line number spans."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Test.jl"
        julia_file.write_text("""function test()
    println("test")
end
""")

        result = analyze_julia(tmp_path)


        test_func = next((s for s in result.symbols if s.name == "test"), None)
        assert test_func is not None
        assert test_func.span.start_line == 1
        assert test_func.language == "julia"
        assert test_func.origin == "julia-v1"


class TestJuliaEdgeProperties:
    """Tests for edge property correctness."""

    def test_edge_has_confidence(self, tmp_path: Path) -> None:
        """Edges have confidence values."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Test.jl"
        julia_file.write_text("""
import Base.show
""")

        result = analyze_julia(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        for edge in import_edges:
            assert edge.confidence > 0
            assert edge.confidence <= 1.0


class TestJuliaEmptyFile:
    """Tests for handling empty or minimal files."""

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handles empty Julia files gracefully."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Empty.jl"
        julia_file.write_text("")

        result = analyze_julia(tmp_path)


        assert result.run is not None

    def test_handles_comment_only_file(self, tmp_path: Path) -> None:
        """Handles files with only comments."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Comments.jl"
        julia_file.write_text("""
# This is a comment
#= Multi-line
   comment =#
""")

        result = analyze_julia(tmp_path)


        assert result.run is not None


class TestJuliaParserFailure:
    """Tests for parser failure handling."""

    def test_handles_parser_load_failure(self, tmp_path: Path) -> None:
        """Handles failure to load Julia parser."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "test.jl"
        julia_file.write_text("function test() end")

        with patch("hypergumbo.analyze.julia.is_julia_tree_sitter_available", return_value=True):
            with patch("tree_sitter_julia.language", side_effect=Exception("Parser error")):
                result = analyze_julia(tmp_path)

        assert result.skipped is True
        assert "Parser error" in result.skip_reason or "Failed to load" in result.skip_reason


class TestJuliaConstExtraction:
    """Tests for extracting Julia constants."""

    def test_extracts_const(self, tmp_path: Path) -> None:
        """Extracts const declarations."""
        from hypergumbo.analyze.julia import analyze_julia

        julia_file = tmp_path / "Constants.jl"
        julia_file.write_text("""
const PI = 3.14159
const VERSION = "1.0.0"
""")

        result = analyze_julia(tmp_path)


        consts = [s for s in result.symbols if s.kind == "const"]
        const_names = [s.name for s in consts]
        assert "PI" in const_names
        assert "VERSION" in const_names


class TestJuliaSignatureExtraction:
    """Tests for Julia function signature extraction."""

    def test_typed_function_with_return_type(self, tmp_path: Path) -> None:
        """Extracts signature from function with typed params and return type."""
        from hypergumbo.analyze.julia import analyze_julia

        (tmp_path / "Calculator.jl").write_text("""
function add(x::Int, y::Int)::Int
    return x + y
end
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x::Int, y::Int)::Int"

    def test_typed_function_no_return_type(self, tmp_path: Path) -> None:
        """Extracts signature from function without return type."""
        from hypergumbo.analyze.julia import analyze_julia

        (tmp_path / "Logger.jl").write_text("""
function log(message::String)
    println(message)
end
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "log"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(message::String)"

    def test_short_form_function(self, tmp_path: Path) -> None:
        """Extracts signature from short-form function."""
        from hypergumbo.analyze.julia import analyze_julia

        (tmp_path / "Math.jl").write_text("""
double(x) = x * 2
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "double"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x)"
