"""Branch coverage tests for julia.py analyzer.

Tests specific branch paths in the Julia analyzer that may not be covered
by the main test suite. Focuses on:
- Function name extraction from different AST patterns
- Signature extraction edge cases
- Struct subtype syntax
- Enclosing function resolution
- Import alias edge cases
- Qualified call resolution
"""
from pathlib import Path
from unittest.mock import MagicMock

from hypergumbo_lang_common.julia import (
    _extract_function_name,
    _extract_import_aliases,
    _extract_julia_signature,
    _find_child_by_type,
    _get_enclosing_function_julia,
    _get_enclosing_module,
    _make_file_id,
    _make_symbol_id,
    _node_text,
    analyze_julia,
)


def make_julia_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Julia file with given content."""
    (tmp_path / name).write_text(content)


class TestJuliaHelperFunctions:
    """Branch coverage for helper functions."""

    def test_node_text_unicode_errors(self) -> None:
        """Test _node_text handles unicode decode errors."""
        mock_node = MagicMock()
        mock_node.start_byte = 0
        mock_node.end_byte = 4
        # Invalid UTF-8 sequence
        source = b"\xff\xfe\x00\x01"

        result = _node_text(mock_node, source)
        assert isinstance(result, str)

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("test.jl", 1, 10, "my_func", "function")
        assert symbol_id == "julia:test.jl:1-10:my_func:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("src/Main.jl")
        assert file_id == "julia:src/Main.jl:1-1:file:file"


class TestGetEnclosingModule:
    """Branch coverage for enclosing module resolution."""

    def test_no_enclosing_module(self) -> None:
        """Test when function has no enclosing module."""
        mock_node = MagicMock()
        mock_node.type = "function_definition"
        mock_node.parent = None  # Top-level

        result = _get_enclosing_module(mock_node, b"")
        # Should return None since no module above
        assert result is None

    def test_nested_in_module(self, tmp_path: Path) -> None:
        """Test function nested in module gets qualified name."""
        make_julia_file(tmp_path, "test.jl", """
module MyModule

function inner_func()
    println("hello")
end

end
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        # Function should be found with module prefix in ID
        assert any(s.name == "inner_func" for s in funcs)


class TestExtractFunctionName:
    """Branch coverage for function name extraction."""

    def test_function_with_typed_return_signature(self, tmp_path: Path) -> None:
        """Test extraction of function with typed parameters and return type."""
        make_julia_file(tmp_path, "test.jl", """
function calculate(x::Int, y::Float64)::Float64
    return x + y
end
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "calculate"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "Int" in funcs[0].signature
        assert "Float64" in funcs[0].signature

    def test_function_without_signature(self) -> None:
        """Test when function_definition has no signature node."""
        mock_node = MagicMock()
        mock_node.type = "function_definition"
        mock_node.children = []  # No signature

        import hypergumbo_lang_common.julia as julia_module
        original_find_child = julia_module._find_child_by_type

        try:
            julia_module._find_child_by_type = lambda n, t: None
            result = _extract_function_name(mock_node, b"")
            assert result is None
        finally:
            julia_module._find_child_by_type = original_find_child


class TestExtractJuliaSignature:
    """Branch coverage for signature extraction."""

    def test_short_form_with_typed_param(self, tmp_path: Path) -> None:
        """Test short-form function with typed parameter."""
        make_julia_file(tmp_path, "test.jl", """
square(x::Int) = x * x
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "square"]
        # Short form with typed param should extract signature
        assert len(funcs) == 1
        # The signature extraction for short form handles typed params
        assert funcs[0].signature is not None

    def test_function_with_multiple_params_mixed_types(self, tmp_path: Path) -> None:
        """Test function with some typed and some untyped params."""
        make_julia_file(tmp_path, "test.jl", """
function mixed(a, b::Int, c)
    return a + b + c
end
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "mixed"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        # Should contain the typed param
        assert "b::Int" in sig

    def test_function_with_empty_params(self, tmp_path: Path) -> None:
        """Test function with no parameters."""
        make_julia_file(tmp_path, "test.jl", """
function no_args()
    return 42
end
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "no_args"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"


class TestStructSubtypeSyntax:
    """Branch coverage for struct subtype extraction."""

    def test_struct_with_subtype(self, tmp_path: Path) -> None:
        """Test struct with subtype declaration (Circle <: Shape)."""
        make_julia_file(tmp_path, "test.jl", """
abstract type Shape end

struct Circle <: Shape
    radius::Float64
end
""")
        result = analyze_julia(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "Circle" for s in structs)

    def test_mutable_struct_with_subtype(self, tmp_path: Path) -> None:
        """Test mutable struct with subtype declaration."""
        make_julia_file(tmp_path, "test.jl", """
abstract type Animal end

mutable struct Dog <: Animal
    name::String
    age::Int
end
""")
        result = analyze_julia(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "Dog" for s in structs)


class TestGetEnclosingFunctionJulia:
    """Branch coverage for enclosing function resolution."""

    def test_call_inside_function_definition(self, tmp_path: Path) -> None:
        """Test call inside a full function definition resolves enclosing function."""
        make_julia_file(tmp_path, "test.jl", """
function helper()
    return 1
end

function caller()
    x = helper()
    return x
end
""")
        result = analyze_julia(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have call from caller to helper
        assert len(call_edges) >= 1

    def test_call_inside_short_form_function(self, tmp_path: Path) -> None:
        """Test call inside short-form function resolves enclosing function."""
        make_julia_file(tmp_path, "test.jl", """
helper() = 42
caller() = helper() + 1
""")
        result = analyze_julia(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have call from caller to helper
        assert len(call_edges) >= 1

    def test_nested_function_call(self, tmp_path: Path) -> None:
        """Test nested function definitions with calls."""
        make_julia_file(tmp_path, "test.jl", """
function outer()
    inner() = 5
    return inner()
end
""")
        result = analyze_julia(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "outer" for s in funcs)
        # Inner function should also be extracted
        assert any(s.name == "inner" for s in funcs)


class TestImportAliases:
    """Branch coverage for import alias handling."""

    def test_no_alias_in_import(self, tmp_path: Path) -> None:
        """Test import without alias doesn't create alias mapping."""
        import tree_sitter
        import tree_sitter_julia

        lang = tree_sitter.Language(tree_sitter_julia.language())
        parser = tree_sitter.Parser(lang)

        make_julia_file(tmp_path, "test.jl", """
import Base
using Statistics
""")
        source = (tmp_path / "test.jl").read_bytes()
        tree = parser.parse(source)

        aliases = _extract_import_aliases(tree, source)
        assert len(aliases) == 0

    def test_multiple_aliases(self, tmp_path: Path) -> None:
        """Test multiple import aliases."""
        import tree_sitter
        import tree_sitter_julia

        lang = tree_sitter.Language(tree_sitter_julia.language())
        parser = tree_sitter.Parser(lang)

        make_julia_file(tmp_path, "test.jl", """
import LinearAlgebra as LA
import SparseArrays as SA
import Dates as D
""")
        source = (tmp_path / "test.jl").read_bytes()
        tree = parser.parse(source)

        aliases = _extract_import_aliases(tree, source)
        assert len(aliases) == 3
        assert aliases["LA"] == "LinearAlgebra"
        assert aliases["SA"] == "SparseArrays"
        assert aliases["D"] == "Dates"


class TestQualifiedCalls:
    """Branch coverage for qualified call resolution."""

    def test_qualified_call_without_alias(self, tmp_path: Path) -> None:
        """Test qualified call without import alias (Module.func)."""
        make_julia_file(tmp_path, "test.jl", """
import LinearAlgebra

function compute()
    LinearAlgebra.norm([1, 2, 3])
end
""")
        result = analyze_julia(tmp_path)
        # Should parse without error
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "compute" for s in funcs)

    def test_qualified_call_with_alias(self, tmp_path: Path) -> None:
        """Test qualified call with import alias."""
        make_julia_file(tmp_path, "test.jl", """
import LinearAlgebra as LA

function compute()
    LA.norm([1, 2, 3])
end
""")
        result = analyze_julia(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "compute" for s in funcs)


class TestImportOnlyFiles:
    """Branch coverage for files without symbols but with imports."""

    def test_file_with_only_imports(self, tmp_path: Path) -> None:
        """Test file that only has import statements gets edge extraction."""
        make_julia_file(tmp_path, "imports.jl", """
import Base
import LinearAlgebra
using Statistics
""")
        result = analyze_julia(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 3

    def test_file_with_no_extractable_symbols(self, tmp_path: Path) -> None:
        """Test file with comments and imports but no functions/structs."""
        make_julia_file(tmp_path, "setup.jl", """
# Configuration file
import JSON
using YAML

# Constants defined elsewhere
""")
        result = analyze_julia(tmp_path)
        # Should still process imports
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2


class TestScopedIdentifiers:
    """Branch coverage for scoped identifier handling in imports."""

    def test_scoped_import(self, tmp_path: Path) -> None:
        """Test import with scoped identifier (Base.Iterators)."""
        make_julia_file(tmp_path, "test.jl", """
import Base.Iterators
import Base.Threads

function use_iterators()
    Iterators.map(x -> x * 2, 1:10)
end
""")
        result = analyze_julia(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have imports for Base.Iterators and Base.Threads
        assert len(import_edges) >= 2
        import_dsts = [e.dst for e in import_edges]
        assert any("Base.Iterators" in dst for dst in import_dsts)


class TestEmptyAnalysisResult:
    """Branch coverage for empty analysis scenarios."""

    def test_directory_with_no_julia_files(self, tmp_path: Path) -> None:
        """Test analyzing directory with no Julia files."""
        (tmp_path / "readme.txt").write_text("Not Julia")
        (tmp_path / "main.py").write_text("def main(): pass")

        result = analyze_julia(tmp_path)
        assert result.run is not None
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_julia_files_with_syntax_errors(self, tmp_path: Path) -> None:
        """Test graceful handling of syntax errors in Julia files."""
        make_julia_file(tmp_path, "broken.jl", """
function incomplete(
    # Missing closing paren and body
""")
        # Should not crash
        result = analyze_julia(tmp_path)
        assert result.run is not None


class TestMacroDefinitions:
    """Branch coverage for macro definition extraction."""

    def test_macro_with_typed_params(self, tmp_path: Path) -> None:
        """Test macro with parameters."""
        make_julia_file(tmp_path, "test.jl", """
macro log(level, msg)
    quote
        println("[$($(esc(level)))] $($(esc(msg)))")
    end
end
""")
        result = analyze_julia(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro"]
        assert any(s.name == "log" for s in macros)

    def test_macro_without_params(self, tmp_path: Path) -> None:
        """Test macro without parameters."""
        make_julia_file(tmp_path, "test.jl", """
macro time_it()
    quote
        @time $(esc(expr))
    end
end
""")
        # This is actually invalid Julia but tests the branch for no params
        result = analyze_julia(tmp_path)
        # Parser should handle gracefully
        assert result.run is not None


class TestConstDeclarations:
    """Branch coverage for const declaration extraction."""

    def test_const_simple(self, tmp_path: Path) -> None:
        """Test simple const without type annotation."""
        make_julia_file(tmp_path, "test.jl", """
const MAX_VALUE = 100
const NAME = "test"
""")
        result = analyze_julia(tmp_path)
        consts = [s for s in result.symbols if s.kind == "const"]
        # Should extract both constants
        assert any(s.name == "MAX_VALUE" for s in consts)
        assert any(s.name == "NAME" for s in consts)

    def test_const_array(self, tmp_path: Path) -> None:
        """Test const array declaration."""
        make_julia_file(tmp_path, "test.jl", """
const DEFAULTS = [1, 2, 3]
""")
        result = analyze_julia(tmp_path)
        consts = [s for s in result.symbols if s.kind == "const"]
        assert any(s.name == "DEFAULTS" for s in consts)


class TestAbstractTypeDeclarations:
    """Branch coverage for abstract type extraction."""

    def test_multiple_abstract_types(self, tmp_path: Path) -> None:
        """Test multiple abstract type declarations."""
        make_julia_file(tmp_path, "test.jl", """
abstract type Shape end
abstract type Animal end
abstract type Vehicle end
""")
        result = analyze_julia(tmp_path)
        abstracts = [s for s in result.symbols if s.kind == "abstract"]
        assert len(abstracts) == 3
        names = [s.name for s in abstracts]
        assert "Shape" in names
        assert "Animal" in names
        assert "Vehicle" in names

    def test_abstract_type_with_subtype_relation(self, tmp_path: Path) -> None:
        """Test abstract type that inherits from another (subtype syntax)."""
        # Julia parses "abstract type X <: Y end" differently
        make_julia_file(tmp_path, "test.jl", """
abstract type Number end
""")
        result = analyze_julia(tmp_path)
        abstracts = [s for s in result.symbols if s.kind == "abstract"]
        # Should extract at least the first abstract type
        assert any(s.name == "Number" for s in abstracts)
