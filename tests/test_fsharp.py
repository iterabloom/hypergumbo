"""Tests for F# language analyzer.

F# is a functional-first language on the .NET platform with strong
static typing and immutable-by-default semantics.

Key constructs: module, let (function/value), type (record/union), open.

Test strategy:
- Module detection
- Function detection
- Value detection
- Record type detection
- Discriminated union detection
- Open statements (imports)
- Function calls
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import fsharp as fsharp_module
from hypergumbo.analyze.fsharp import analyze_fsharp


def make_fsharp_file(tmp: Path, name: str, content: str) -> Path:
    """Create a .fs file for testing."""
    f = tmp / name
    f.write_text(content, encoding="utf-8")
    return f


class TestFsharpAnalyzer:
    """Test F# symbol and edge detection."""

    def test_detects_module(self, tmp_path: Path) -> None:
        """Detect module declarations."""
        make_fsharp_file(
            tmp_path,
            "Main.fs",
            """
module Main

let main args = 0
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "Main" in names

        mod = next(s for s in result.symbols if s.name == "Main")
        assert mod.kind == "module"
        assert mod.language == "fsharp"

    def test_detects_nested_module(self, tmp_path: Path) -> None:
        """Detect nested module declarations."""
        make_fsharp_file(
            tmp_path,
            "App.fs",
            """
module App.Utils

let helper x = x
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "App.Utils" in names

    def test_detects_functions(self, tmp_path: Path) -> None:
        """Detect function definitions."""
        make_fsharp_file(
            tmp_path,
            "Utils.fs",
            """
module Utils

let greet name =
    sprintf "Hello, %s!" name

let add a b =
    a + b
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "greet" in names
        assert "add" in names

        greet = next(s for s in result.symbols if s.name == "greet")
        assert greet.kind == "function"

    def test_detects_record_type(self, tmp_path: Path) -> None:
        """Detect record type definitions."""
        make_fsharp_file(
            tmp_path,
            "Models.fs",
            """
module Models

type Person = { Name: string; Age: int }

type Product = { Id: int; Title: string; Price: float }
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "Person" in names
        assert "Product" in names

        person = next(s for s in result.symbols if s.name == "Person")
        assert person.kind == "record"

    def test_detects_union_type(self, tmp_path: Path) -> None:
        """Detect discriminated union definitions."""
        make_fsharp_file(
            tmp_path,
            "Types.fs",
            """
module Types

type Shape =
    | Circle of radius: float
    | Rectangle of width: float * height: float
    | Triangle of base_: float * height: float
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "Shape" in names

        shape = next(s for s in result.symbols if s.name == "Shape")
        assert shape.kind == "union"

    def test_detects_open_statements(self, tmp_path: Path) -> None:
        """Detect open statements as import edges."""
        make_fsharp_file(
            tmp_path,
            "App.fs",
            """
module App

open System
open System.IO
open System.Collections.Generic

let main args = 0
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert len(imports) >= 3

        import_dsts = [e.dst for e in imports]
        assert any("System" in dst for dst in import_dsts)
        assert any("System.IO" in dst for dst in import_dsts)
        assert any("System.Collections.Generic" in dst for dst in import_dsts)

    def test_detects_function_calls(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        make_fsharp_file(
            tmp_path,
            "App.fs",
            """
module App

let helper x =
    x * 2

let main args =
    helper 21
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

        # main should call helper
        main_sym = next(s for s in result.symbols if s.name == "main")
        helper_sym = next(s for s in result.symbols if s.name == "helper")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (main_sym.id, helper_sym.id) in edge_pairs

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        """Handle empty F# file gracefully."""
        make_fsharp_file(tmp_path, "Empty.fs", "")
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

    def test_handles_fsi_files(self, tmp_path: Path) -> None:
        """Handle F# signature files (.fsi)."""
        make_fsharp_file(
            tmp_path,
            "Utils.fsi",
            """
module Utils

val add: int -> int -> int
""",
        )
        result = analyze_fsharp(tmp_path)
        # Should process the file without crashing
        assert result is not None

    def test_handles_fsx_files(self, tmp_path: Path) -> None:
        """Handle F# script files (.fsx)."""
        make_fsharp_file(
            tmp_path,
            "Script.fsx",
            """
let result = 1 + 2
printfn "%d" result
""",
        )
        result = analyze_fsharp(tmp_path)
        assert result is not None

    def test_cross_file_calls(self, tmp_path: Path) -> None:
        """Detect calls across files via two-pass resolution."""
        make_fsharp_file(
            tmp_path,
            "Utils.fs",
            """
module Utils

let double x =
    x * 2
""",
        )
        make_fsharp_file(
            tmp_path,
            "Main.fs",
            """
module Main

open Utils

let quadruple x =
    double (double x)
""",
        )
        result = analyze_fsharp(tmp_path)
        assert not result.skipped

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]

        # quadruple should call double
        quad = next(s for s in result.symbols if s.name == "quadruple")
        dbl = next(s for s in result.symbols if s.name == "double")
        edge_pairs = [(e.src, e.dst) for e in call_edges]
        assert (quad.id, dbl.id) in edge_pairs

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analysis skips gracefully when tree-sitter unavailable."""
        make_fsharp_file(tmp_path, "Test.fs", "module Test")

        with patch.object(
            fsharp_module,
            "is_fsharp_tree_sitter_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="F# analysis skipped"):
                result = fsharp_module.analyze_fsharp(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason


class TestFsharpSignatureExtraction:
    """Tests for F# function signature extraction."""

    def test_typed_params_with_return_type(self, tmp_path: Path) -> None:
        """Extracts signature from function with typed params and return type."""
        make_fsharp_file(
            tmp_path,
            "Calculator.fs",
            """
let add (x: int) (y: int): int = x + y
""",
        )
        result = analyze_fsharp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x: int, y: int): int"

    def test_typed_params_no_return_type(self, tmp_path: Path) -> None:
        """Extracts signature from function without explicit return type."""
        make_fsharp_file(
            tmp_path,
            "Logger.fs",
            """
let log (message: string) = printfn "%s" message
""",
        )
        result = analyze_fsharp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "log"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(message: string)"

    def test_unit_params_signature(self, tmp_path: Path) -> None:
        """Extracts signature from function with unit parameter."""
        make_fsharp_file(
            tmp_path,
            "Counter.fs",
            """
let getCount () = 0
""",
        )
        result = analyze_fsharp(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "getCount"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"
