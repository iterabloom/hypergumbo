"""Tests for the PureScript language analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import purescript as purescript_module
from hypergumbo_lang_common.purescript import (
    PureScriptAnalysisResult,
    analyze_purescript,
    find_purescript_files,
    is_purescript_tree_sitter_available,
)


def make_purescript_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a PureScript file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindPurescriptFiles:
    """Tests for find_purescript_files function."""

    def test_finds_purescript_files(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Main.purs", "module Main where")
        make_purescript_file(tmp_path, "Helper.purs", "module Helper where")
        files = list(find_purescript_files(tmp_path))
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"Main.purs", "Helper.purs"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = list(find_purescript_files(tmp_path))
        assert files == []


class TestIsPurescriptTreeSitterAvailable:
    """Tests for is_purescript_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_purescript_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(purescript_module, "is_purescript_tree_sitter_available", return_value=False):
            assert purescript_module.is_purescript_tree_sitter_available() is False


class TestAnalyzePurescript:
    """Tests for analyze_purescript function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", "module Test where")
        with patch.object(purescript_module, "is_purescript_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="PureScript analysis skipped"):
                result = purescript_module.analyze_purescript(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_modules(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Main.purs", """module Main where

import Prelude
""")
        result = analyze_purescript(tmp_path)
        assert not result.skipped
        mod = next((s for s in result.symbols if s.name == "Main"), None)
        assert mod is not None
        assert mod.kind == "module"
        assert mod.language == "purescript"

    def test_extracts_functions(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Math.purs", """module Math where

add :: Int -> Int -> Int
add x y = x + y
""")
        result = analyze_purescript(tmp_path)
        assert not result.skipped
        func = next((s for s in result.symbols if "add" in s.name), None)
        assert func is not None
        assert func.kind == "function"
        assert "Int -> Int -> Int" in (func.signature or "")

    def test_extracts_data_types(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Types.purs", """module Types where

data Color = Red | Green | Blue
""")
        result = analyze_purescript(tmp_path)
        dtype = next((s for s in result.symbols if "Color" in s.name), None)
        assert dtype is not None
        assert dtype.kind == "type"
        assert dtype.meta["constructor_count"] == 3

    def test_extracts_type_aliases(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Types.purs", """module Types where

type Point = { x :: Number, y :: Number }
""")
        result = analyze_purescript(tmp_path)
        alias = next((s for s in result.symbols if "Point" in s.name), None)
        assert alias is not None
        assert alias.kind == "type_alias"

    def test_extracts_classes(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Classes.purs", """module Classes where

class Show a where
  show :: a -> String
""")
        result = analyze_purescript(tmp_path)
        cls = next((s for s in result.symbols if "Show" in s.name), None)
        assert cls is not None
        assert cls.kind == "class"

    def test_extracts_instances(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Instances.purs", """module Instances where

data Color = Red

class Show a where
  show :: a -> String

instance showColor :: Show Color where
  show Red = "red"
""")
        result = analyze_purescript(tmp_path)
        inst = next((s for s in result.symbols if "showColor" in s.name), None)
        assert inst is not None
        assert inst.kind == "instance"

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", """module Test where

helper :: Int -> Int
helper x = x

main :: Int -> Int
main x = helper x
""")
        result = analyze_purescript(tmp_path)
        # main calls helper
        edge = next(
            (e for e in result.edges if "main" in e.src and "helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"
        assert edge.confidence == 1.0

    def test_recursive_calls(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Factorial.purs", """module Factorial where

factorial :: Int -> Int
factorial n = if n <= 1 then 1 else factorial n
""")
        result = analyze_purescript(tmp_path)
        # factorial calls itself
        edge = next(
            (e for e in result.edges if "factorial" in e.src and "factorial" in e.dst),
            None
        )
        assert edge is not None

    def test_filters_builtins(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", """module Test where

main :: String
main = show 42
""")
        result = analyze_purescript(tmp_path)
        # Should not have edges to show (builtin)
        builtin_edges = [e for e in result.edges if "show" in e.dst.lower() and "unresolved" in e.dst]
        # Built-ins are filtered out, so no edges to them
        show_edges = [e for e in result.edges if e.dst == "show"]
        assert len(show_edges) == 0

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", """module Test where

main :: Int -> Int
main x = externalFunc x
""")
        result = analyze_purescript(tmp_path)
        edge = next(
            (e for e in result.edges if "externalFunc" in e.dst),
            None
        )
        assert edge is not None
        assert "unresolved" in edge.dst
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", """module Test where

foo :: Int
foo = 42
""")
        result = analyze_purescript(tmp_path)
        func = next((s for s in result.symbols if "foo" in s.name), None)
        assert func is not None
        assert func.origin == "purescript.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", "module Test where")
        result = analyze_purescript(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "purescript.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_purescript(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", """module Test where

myFunc :: Int
myFunc = 42
""")
        result = analyze_purescript(tmp_path)
        func = next((s for s in result.symbols if "myFunc" in s.name), None)
        assert func is not None
        assert func.id == func.stable_id
        assert "purescript:" in func.id
        assert "Test.purs" in func.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "Test.purs", """module Test where

myFunc :: Int
myFunc = 42
""")
        result = analyze_purescript(tmp_path)
        func = next((s for s in result.symbols if "myFunc" in s.name), None)
        assert func is not None
        assert func.span is not None
        assert func.span.start_line >= 1
        assert func.span.end_line >= func.span.start_line

    def test_qualified_names(self, tmp_path: Path) -> None:
        make_purescript_file(tmp_path, "MyModule.purs", """module MyModule where

myFunc :: Int
myFunc = 42
""")
        result = analyze_purescript(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function"), None)
        assert func is not None
        assert func.name == "MyModule.myFunc"
        assert func.meta["module"] == "MyModule"
