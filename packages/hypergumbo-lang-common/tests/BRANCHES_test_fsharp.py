"""Branch coverage tests for fsharp.py analyzer.

Tests specific branch paths in the F# analyzer that may not be covered
by the main test suite. Focuses on:
- Enclosing function resolution
- Unresolved call handling
- Multiple modules in file
- Value vs function detection
- Forth file detection edge cases
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_common.fsharp import _is_likely_forth_file, analyze_fsharp, find_fsharp_files

def make_fsharp_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an F# file with given content."""
    (tmp_path / name).write_text(content)

class TestFsharpHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("fsharp", "src/Main.fs", 1, 10, "greet", "function")
        assert symbol_id == "fsharp:src/Main.fs:1-10:greet:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("fsharp", "lib/Utils.fs")
        assert file_id == "fsharp:lib/Utils.fs:1-1:file:file"

class TestFsharpUnresolvedCalls:
    """Branch coverage for unresolved call handling."""

    def test_call_to_internal_function(self, tmp_path: Path) -> None:
        """Test call to internal function creates call edge."""
        make_fsharp_file(tmp_path, "Main.fs", """
module Main

let helper x = x + 1
let main args = helper 42
""")
        result = analyze_fsharp(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge from main to helper
        assert len(call_edges) >= 1

    def test_call_to_stdlib_in_function(self, tmp_path: Path) -> None:
        """Test calls to standard library functions."""
        make_fsharp_file(tmp_path, "Main.fs", """
module Main

let main args =
    printfn "Hello, World!"
    0
""")
        result = analyze_fsharp(tmp_path)
        # Should handle gracefully - printfn is external
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any(f.name == "main" for f in funcs)

class TestFsharpMultipleModules:
    """Branch coverage for files with multiple modules."""

    def test_multiple_functions_in_module(self, tmp_path: Path) -> None:
        """Test extraction of multiple functions from one module."""
        make_fsharp_file(tmp_path, "Math.fs", """
module Math

let add x y = x + y
let sub x y = x - y
let mul x y = x * y
let div x y = x / y
""")
        result = analyze_fsharp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "add" in names
        assert "sub" in names
        assert "mul" in names
        assert "div" in names

    def test_function_calling_another_in_same_module(self, tmp_path: Path) -> None:
        """Test call edge between functions in same module."""
        make_fsharp_file(tmp_path, "Utils.fs", """
module Utils

let double x = x * 2

let quadruple x = double (double x)
""")
        result = analyze_fsharp(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # quadruple should call double (twice)
        double_calls = [e for e in call_edges if "double" in e.dst]
        assert len(double_calls) >= 2

class TestFsharpTypeDefinitions:
    """Branch coverage for type extraction."""

    def test_multiple_record_types(self, tmp_path: Path) -> None:
        """Test multiple record type definitions in one file."""
        make_fsharp_file(tmp_path, "Types.fs", """
module Types

type Point = { X: float; Y: float }
type Color = { R: int; G: int; B: int }
type Person = { Name: string; Age: int }
""")
        result = analyze_fsharp(tmp_path)
        records = [s for s in result.symbols if s.kind == "record"]
        names = [t.name for t in records]
        assert "Point" in names
        assert "Color" in names
        assert "Person" in names

    def test_multiple_union_types(self, tmp_path: Path) -> None:
        """Test multiple discriminated union definitions."""
        make_fsharp_file(tmp_path, "Enums.fs", """
module Enums

type Shape =
    | Circle of float
    | Rectangle of float * float

type Status =
    | Active
    | Inactive
    | Pending

type Priority =
    | Low
    | Medium
    | High
""")
        result = analyze_fsharp(tmp_path)
        unions = [s for s in result.symbols if s.kind == "union"]
        names = [u.name for u in unions]
        assert "Shape" in names
        assert "Status" in names
        assert "Priority" in names

class TestFsharpOpenStatements:
    """Branch coverage for open statement handling."""

    def test_nested_open_statements(self, tmp_path: Path) -> None:
        """Test deeply nested open statements."""
        make_fsharp_file(tmp_path, "Main.fs", """
module Main

open System.Collections.Generic
open System.Runtime.CompilerServices
open Microsoft.FSharp.Collections

let main args = 0
""")
        result = analyze_fsharp(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        dests = [e.dst for e in import_edges]
        assert any("System.Collections.Generic" in d for d in dests)
        assert any("System.Runtime.CompilerServices" in d for d in dests)
        assert any("Microsoft.FSharp.Collections" in d for d in dests)

class TestFsharpFunctionDefinitions:
    """Branch coverage for function definition patterns."""

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with parameters is detected."""
        make_fsharp_file(tmp_path, "Utils.fs", """
module Utils

let double x = x * 2
let add x y = x + y
""")
        result = analyze_fsharp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "double" in names
        assert "add" in names

    def test_function_with_unit_param(self, tmp_path: Path) -> None:
        """Test function with unit parameter."""
        make_fsharp_file(tmp_path, "Factory.fs", """
module Factory

let create () = { Value = 42 }
let getDefault () = 0
""")
        result = analyze_fsharp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "create" in names
        assert "getDefault" in names

class TestForthFileDetection:
    """Branch coverage for Forth file detection edge cases."""

    def test_fsharp_with_constant_keyword(self, tmp_path: Path) -> None:
        """Test F# file with constant-like word in code is not filtered as Forth."""
        make_fsharp_file(tmp_path, "App.fs", """
module App

// Use functions that happen to have "constant" in comments
let double x = x * 2
let helper y = y + 1
""")
        result = analyze_fsharp(tmp_path)
        # Should analyze as F#
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "double" in names

    def test_forth_with_create_does(self, tmp_path: Path) -> None:
        """Test Forth file with CREATE ... DOES> is detected."""
        forth_file = tmp_path / "defining.fs"
        forth_file.write_text("""
CREATE buffer 1024 ALLOT
: fill-buffer ( -- )
    DOES> buffer 1024 0 fill ;
""", encoding="utf-8")
        assert _is_likely_forth_file(forth_file) is True

    def test_forth_with_include(self, tmp_path: Path) -> None:
        """Test Forth file with INCLUDE is detected."""
        forth_file = tmp_path / "main.fs"
        forth_file.write_text("""
INCLUDE lib/utils.fs
INCLUDE lib/io.fs
""", encoding="utf-8")
        assert _is_likely_forth_file(forth_file) is True

    def test_fsharp_create_not_matched(self, tmp_path: Path) -> None:
        """Test F# file with 'create' in comments not matched as Forth."""
        make_fsharp_file(tmp_path, "Factory.fs", """
module Factory

// Create a new instance
let create x = { Value = x }
""")
        result = analyze_fsharp(tmp_path)
        # Should analyze as F#, not detect as Forth
        assert not result.skipped

class TestFsharpCrossFileResolution:
    """Branch coverage for cross-file resolution."""

    def test_three_file_dependency_chain(self, tmp_path: Path) -> None:
        """Test call resolution across three files."""
        make_fsharp_file(tmp_path, "Base.fs", """
module Base

let baseFunc x = x * 2
""")
        make_fsharp_file(tmp_path, "Middle.fs", """
module Middle

let middleFunc x = Base.baseFunc x + 1
""")
        make_fsharp_file(tmp_path, "Top.fs", """
module Top

let topFunc x = Middle.middleFunc (Middle.middleFunc x)
""")
        result = analyze_fsharp(tmp_path)
        assert not result.skipped
        # All modules should be extracted
        modules = [s for s in result.symbols if s.kind == "module"]
        names = [m.name for m in modules]
        assert "Base" in names
        assert "Middle" in names
        assert "Top" in names

class TestFsharpEmptyAndMinimal:
    """Branch coverage for empty/minimal files."""

    def test_module_only_file(self, tmp_path: Path) -> None:
        """Test file with only module declaration."""
        make_fsharp_file(tmp_path, "Empty.fs", """
module Empty
""")
        result = analyze_fsharp(tmp_path)
        assert not result.skipped
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any(m.name == "Empty" for m in modules)

    def test_file_with_only_comments(self, tmp_path: Path) -> None:
        """Test file with only F# comments."""
        make_fsharp_file(tmp_path, "Comments.fs", """
// This is a comment
// Another comment
(*
   Multi-line comment
*)
""")
        result = analyze_fsharp(tmp_path)
        # Should handle gracefully
        assert not result.skipped

class TestFsharpFindFiles:
    """Branch coverage for file discovery."""

    def test_finds_nested_fs_files(self, tmp_path: Path) -> None:
        """Test finding .fs files in nested directories."""
        lib = tmp_path / "lib" / "core"
        lib.mkdir(parents=True)
        (lib / "Core.fs").write_text("module Core\nlet x = 1")

        files = list(find_fsharp_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "Core.fs"

    def test_finds_all_fsharp_extensions(self, tmp_path: Path) -> None:
        """Test finding all F# file extensions."""
        (tmp_path / "Main.fs").write_text("module Main")
        (tmp_path / "Api.fsi").write_text("module Api")
        (tmp_path / "Script.fsx").write_text("let x = 1")

        files = list(find_fsharp_files(tmp_path))
        extensions = {f.suffix for f in files}
        assert ".fs" in extensions
        assert ".fsi" in extensions
        assert ".fsx" in extensions

    def test_excludes_forth_in_mixed_directory(self, tmp_path: Path) -> None:
        """Test Forth files are excluded when mixed with F#."""
        (tmp_path / "App.fs").write_text("module App\nlet x = 1")
        forth_file = tmp_path / "boot.fs"
        forth_file.write_text("\\ Forth boot script\n0 VALUE flag")

        files = list(find_fsharp_files(tmp_path))
        names = [f.name for f in files]
        assert "App.fs" in names
        assert "boot.fs" not in names
