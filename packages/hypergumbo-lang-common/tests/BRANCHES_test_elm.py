"""Branch coverage tests for elm.py analyzer.

Tests specific branch paths in the Elm analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function ID format
- Module declaration extraction
- Function signature extraction
- Custom type and type alias extraction
- Port declaration extraction
- Import alias extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_common.elm import analyze_elm, find_elm_files

def make_elm_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Elm file with given content."""
    (tmp_path / name).write_text(content)

class TestElmHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("elm", "src/Main.elm", 5, 10, "update", "function")
        assert symbol_id == "elm:src/Main.elm:5-10:update:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("elm", "lib/Utils.elm")
        assert file_id == "elm:lib/Utils.elm:1-1:file:file"

class TestModuleDeclaration:
    """Branch coverage for module declaration extraction."""

    def test_nested_module_name(self, tmp_path: Path) -> None:
        """Test deeply nested module name extraction."""
        make_elm_file(tmp_path, "Main.elm", """
module My.App.Core.Main exposing (..)

main = text "Hello"
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) == 1
        assert modules[0].name == "My.App.Core.Main"

    def test_module_with_exposing_list(self, tmp_path: Path) -> None:
        """Test module with explicit exposing list."""
        make_elm_file(tmp_path, "Utils.elm", """
module Utils exposing (helper, compute)

helper x = x + 1

compute y = y * 2
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "helper" in names
        assert "compute" in names

class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function definitions in one file."""
        make_elm_file(tmp_path, "Math.elm", """
module Math exposing (..)

add x y = x + y

sub x y = x - y

mul x y = x * y

divide x y = x // y
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "add" in names
        assert "sub" in names
        assert "mul" in names
        assert "divide" in names

    def test_function_with_no_params(self, tmp_path: Path) -> None:
        """Test function with no parameters."""
        make_elm_file(tmp_path, "Const.elm", """
module Const exposing (..)

greeting = "Hello, World!"

answer = 42
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "greeting" in names
        assert "answer" in names

class TestTypeDefinitions:
    """Branch coverage for type alias and custom type extraction."""

    def test_type_alias(self, tmp_path: Path) -> None:
        """Test type alias extraction."""
        make_elm_file(tmp_path, "Types.elm", """
module Types exposing (..)

type alias User =
    { name : String
    , age : Int
    }

type alias Point =
    { x : Float
    , y : Float
    }
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        types = [s for s in result.symbols if s.kind == "type"]
        names = [t.name for t in types]
        assert "User" in names
        assert "Point" in names

    def test_custom_type(self, tmp_path: Path) -> None:
        """Test custom type (union type) extraction."""
        make_elm_file(tmp_path, "Enums.elm", """
module Enums exposing (..)

type Color
    = Red
    | Green
    | Blue

type Maybe a
    = Just a
    | Nothing
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        types = [s for s in result.symbols if s.kind == "type"]
        names = [t.name for t in types]
        assert "Color" in names
        assert "Maybe" in names

class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_multiple_imports(self, tmp_path: Path) -> None:
        """Test multiple import statements create edges."""
        make_elm_file(tmp_path, "Main.elm", """
module Main exposing (..)

import Html exposing (div, text)
import Html.Attributes exposing (class)
import Html.Events exposing (onClick)

view model = div [] [ text "Hello" ]
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        dests = [e.dst for e in import_edges]
        assert any("Html" in d for d in dests)
        assert any("Html.Attributes" in d for d in dests)
        assert any("Html.Events" in d for d in dests)

class TestImportAliases:
    """Branch coverage for import alias extraction."""

    def test_import_with_alias(self, tmp_path: Path) -> None:
        """Test import with as alias."""
        make_elm_file(tmp_path, "Main.elm", """
module Main exposing (..)

import Dict as D
import Html as H

view = H.div [] []
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        # Should have import edges
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

class TestFunctionCalls:
    """Branch coverage for function call edge extraction."""

    def test_internal_function_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_elm_file(tmp_path, "App.elm", """
module App exposing (..)

helper x = x * 2

main = helper 21
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # main calls helper
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_chained_function_calls(self, tmp_path: Path) -> None:
        """Test chain of function calls."""
        make_elm_file(tmp_path, "Chain.elm", """
module Chain exposing (..)

step1 x = x + 1
step2 x = step1 x + 1
step3 x = step2 x + 1
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # step2 calls step1, step3 calls step2
        step1_calls = [e for e in call_edges if "step1" in e.dst]
        step2_calls = [e for e in call_edges if "step2" in e.dst]
        assert len(step1_calls) >= 1
        assert len(step2_calls) >= 1

class TestFindElmFiles:
    """Branch coverage for file discovery."""

    def test_finds_nested_elm_files(self, tmp_path: Path) -> None:
        """Test .elm files in nested directories are found."""
        src = tmp_path / "src" / "App"
        src.mkdir(parents=True)
        (src / "Main.elm").write_text("module App.Main exposing (..)")

        files = list(find_elm_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "Main.elm"

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_module_only_file(self, tmp_path: Path) -> None:
        """Test file with only module declaration."""
        make_elm_file(tmp_path, "Empty.elm", "module Empty exposing (..)")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert any(m.name == "Empty" for m in modules)

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test empty Elm file."""
        make_elm_file(tmp_path, "Empty.elm", "")
        result = analyze_elm(tmp_path)
        # Should handle gracefully
        assert not result.skipped

class TestCrossFileResolution:
    """Branch coverage for cross-file call resolution."""

    def test_two_file_resolution(self, tmp_path: Path) -> None:
        """Test call resolution across two files."""
        make_elm_file(tmp_path, "Utils.elm", """
module Utils exposing (helper)

helper x = x * 2
""")
        make_elm_file(tmp_path, "Main.elm", """
module Main exposing (..)

import Utils exposing (helper)

main = helper 21
""")
        result = analyze_elm(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "helper" in names
        assert "main" in names
