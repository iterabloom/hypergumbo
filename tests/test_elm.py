"""Tests for Elm language analyzer.

Elm is a functional programming language for web frontends that compiles
to JavaScript. It has strong static typing with no runtime exceptions.

Key constructs: module, exposing, type alias, type (union), port.

Test strategy:
- Module detection
- Function detection
- Type alias detection
- Custom type (union type) detection
- Port detection (JS interop)
- Import edges
- Function calls
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import elm as elm_module
from hypergumbo.analyze.elm import analyze_elm


def make_elm_file(tmp: Path, name: str, content: str) -> Path:
    """Create a .elm file for testing."""
    f = tmp / name
    f.write_text(content, encoding="utf-8")
    return f


class TestElmAnalyzer:
    """Test Elm symbol and edge detection."""

    def test_detects_module(self, tmp_path: Path) -> None:
        """Detect module declarations."""
        make_elm_file(
            tmp_path,
            "Main.elm",
            """
module Main exposing (main)

main =
    "Hello"
""",
        )
        result = analyze_elm(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "Main" in names

        mod = next(s for s in result.symbols if s.name == "Main")
        assert mod.kind == "module"
        assert mod.language == "elm"

    def test_detects_functions(self, tmp_path: Path) -> None:
        """Detect function definitions."""
        make_elm_file(
            tmp_path,
            "Utils.elm",
            """
module Utils exposing (greet, add)

greet name =
    "Hello, " ++ name

add a b =
    a + b
""",
        )
        result = analyze_elm(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "greet" in names
        assert "add" in names

        greet = next(s for s in result.symbols if s.name == "greet")
        assert greet.kind == "function"

    def test_detects_type_alias(self, tmp_path: Path) -> None:
        """Detect type alias definitions."""
        make_elm_file(
            tmp_path,
            "Models.elm",
            """
module Models exposing (User, Product)

type alias User =
    { name : String
    , age : Int
    }

type alias Product =
    { id : Int
    , title : String
    , price : Float
    }
""",
        )
        result = analyze_elm(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "User" in names
        assert "Product" in names

        user = next(s for s in result.symbols if s.name == "User")
        assert user.kind == "type"

    def test_detects_custom_type(self, tmp_path: Path) -> None:
        """Detect custom type (union type) definitions."""
        make_elm_file(
            tmp_path,
            "Messages.elm",
            """
module Messages exposing (Msg)

type Msg
    = Increment
    | Decrement
    | Reset
""",
        )
        result = analyze_elm(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "Msg" in names

        msg = next(s for s in result.symbols if s.name == "Msg")
        assert msg.kind == "type"

    def test_detects_port(self, tmp_path: Path) -> None:
        """Detect port declarations (JavaScript interop)."""
        make_elm_file(
            tmp_path,
            "Ports.elm",
            """
port module Ports exposing (sendMessage, receiveMessage)

port sendMessage : String -> Cmd msg

port receiveMessage : (String -> msg) -> Sub msg
""",
        )
        result = analyze_elm(tmp_path)
        assert not result.skipped

        names = [s.name for s in result.symbols]
        assert "sendMessage" in names
        assert "receiveMessage" in names

        port = next(s for s in result.symbols if s.name == "sendMessage")
        assert port.kind == "port"

    def test_detects_imports(self, tmp_path: Path) -> None:
        """Detect import statements as import edges."""
        make_elm_file(
            tmp_path,
            "App.elm",
            """
module App exposing (main)

import Html exposing (text)
import Browser
import Http

main =
    text "Hello"
""",
        )
        result = analyze_elm(tmp_path)
        assert not result.skipped

        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert len(imports) >= 3

        import_dsts = [e.dst for e in imports]
        assert any("Html" in dst for dst in import_dsts)
        assert any("Browser" in dst for dst in import_dsts)
        assert any("Http" in dst for dst in import_dsts)

    def test_detects_function_calls(self, tmp_path: Path) -> None:
        """Detect function call edges."""
        make_elm_file(
            tmp_path,
            "App.elm",
            """
module App exposing (main, helper)

helper x =
    x * 2

main =
    helper 21
""",
        )
        result = analyze_elm(tmp_path)
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
        """Handle empty Elm file gracefully."""
        make_elm_file(tmp_path, "Empty.elm", "")
        result = analyze_elm(tmp_path)
        assert not result.skipped

    def test_handles_syntax_error(self, tmp_path: Path) -> None:
        """Handle files with syntax errors gracefully."""
        make_elm_file(tmp_path, "Bad.elm", "module Bad exposing")  # Incomplete
        result = analyze_elm(tmp_path)
        # Should not crash
        assert result is not None

    def test_cross_file_calls(self, tmp_path: Path) -> None:
        """Detect calls across files via two-pass resolution."""
        make_elm_file(
            tmp_path,
            "Utils.elm",
            """
module Utils exposing (double)

double x =
    x * 2
""",
        )
        make_elm_file(
            tmp_path,
            "Main.elm",
            """
module Main exposing (quadruple)

import Utils exposing (double)

quadruple x =
    double (double x)
""",
        )
        result = analyze_elm(tmp_path)
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
        make_elm_file(tmp_path, "Test.elm", "module Test exposing (..)")

        with patch.object(
            elm_module,
            "is_elm_tree_sitter_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="Elm analysis skipped"):
                result = elm_module.analyze_elm(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason


class TestElmSignatureExtraction:
    """Tests for Elm function signature extraction."""

    def test_function_with_parameters(self, tmp_path: Path) -> None:
        """Extract signature for function with parameters."""
        make_elm_file(
            tmp_path,
            "Math.elm",
            """
module Math exposing (add)

add x y =
    x + y
""",
        )
        result = analyze_elm(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x, y)"

    def test_function_no_parameters(self, tmp_path: Path) -> None:
        """Extract signature for function with no parameters."""
        make_elm_file(
            tmp_path,
            "Constants.elm",
            """
module Constants exposing (answer)

answer =
    42
""",
        )
        result = analyze_elm(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "answer"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_function_single_parameter(self, tmp_path: Path) -> None:
        """Extract signature for function with single parameter."""
        make_elm_file(
            tmp_path,
            "Greet.elm",
            """
module Greet exposing (hello)

hello name =
    "Hello, " ++ name
""",
        )
        result = analyze_elm(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "hello"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(name)"

    def test_function_many_parameters(self, tmp_path: Path) -> None:
        """Extract signature for function with many parameters."""
        make_elm_file(
            tmp_path,
            "Utils.elm",
            """
module Utils exposing (combine)

combine a b c d =
    a ++ b ++ c ++ d
""",
        )
        result = analyze_elm(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "combine"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(a, b, c, d)"
