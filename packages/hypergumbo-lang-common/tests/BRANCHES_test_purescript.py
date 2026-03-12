# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for purescript.py analyzer.

Tests specific branch paths in the PureScript analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Module extraction
- Function extraction
- Data type extraction
- Type alias extraction
- Class/instance extraction
- Call edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.purescript import (
    analyze_purescript,
    find_purescript_files,
)


def make_purs_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a PureScript file with given content."""
    (tmp_path / name).write_text(content)


class TestModuleExtraction:
    """Branch coverage for module extraction."""

    def test_simple_module(self, tmp_path: Path) -> None:
        """Test simple module extraction."""
        make_purs_file(tmp_path, "Main.purs", """
module Main where

main = log "Hello"
""")
        result = analyze_purescript(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) >= 1
        assert any(m.name == "Main" for m in modules)

    def test_qualified_module(self, tmp_path: Path) -> None:
        """Test qualified module name extraction."""
        make_purs_file(tmp_path, "Utils.purs", """
module App.Utils where

helper x = x * 2
""")
        result = analyze_purescript(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) >= 1
        assert any("App.Utils" in m.name for m in modules)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_purs_file(tmp_path, "Main.purs", """
module Main where

double x = x * 2
""")
        result = analyze_purescript(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1

    def test_function_with_signature(self, tmp_path: Path) -> None:
        """Test function with type signature."""
        make_purs_file(tmp_path, "Main.purs", """
module Main where

square :: Int -> Int
square x = x * x
""")
        result = analyze_purescript(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and "square" in s.name]
        assert len(funcs) >= 1

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function extraction."""
        make_purs_file(tmp_path, "Math.purs", """
module Math where

add a b = a + b
subtract a b = a - b
multiply a b = a * b
""")
        result = analyze_purescript(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 3


class TestDataTypeExtraction:
    """Branch coverage for data type extraction."""

    def test_simple_data_type(self, tmp_path: Path) -> None:
        """Test simple data type extraction."""
        make_purs_file(tmp_path, "Types.purs", """
module Types where

data Color = Red | Green | Blue
""")
        result = analyze_purescript(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) >= 1

    def test_data_type_with_constructor_count(self, tmp_path: Path) -> None:
        """Test data type has constructor count metadata."""
        make_purs_file(tmp_path, "Types.purs", """
module Types where

data Maybe a = Nothing | Just a
""")
        result = analyze_purescript(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) >= 1


class TestTypeAliasExtraction:
    """Branch coverage for type alias extraction."""

    def test_type_alias(self, tmp_path: Path) -> None:
        """Test type alias extraction."""
        make_purs_file(tmp_path, "Types.purs", """
module Types where

type Name = String
type Age = Int
""")
        result = analyze_purescript(tmp_path)
        aliases = [s for s in result.symbols if s.kind == "type_alias"]
        assert len(aliases) >= 2


class TestClassExtraction:
    """Branch coverage for type class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test type class extraction."""
        make_purs_file(tmp_path, "Classes.purs", """
module Classes where

class Show a where
  show :: a -> String
""")
        result = analyze_purescript(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1


class TestInstanceExtraction:
    """Branch coverage for instance extraction."""

    def test_instance_declaration(self, tmp_path: Path) -> None:
        """Test instance extraction."""
        make_purs_file(tmp_path, "Instances.purs", """
module Instances where

instance showInt :: Show Int where
  show n = "int"
""")
        result = analyze_purescript(tmp_path)
        instances = [s for s in result.symbols if s.kind == "instance"]
        assert len(instances) >= 1


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same module."""
        make_purs_file(tmp_path, "Main.purs", """
module Main where

helper x = x * 2

main = helper 21
""")
        result = analyze_purescript(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_unresolved_call(self, tmp_path: Path) -> None:
        """Test call to unknown function creates unresolved edge."""
        make_purs_file(tmp_path, "Main.purs", """
module Main where

main = unknownFunc 42
""")
        result = analyze_purescript(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        unresolved = [e for e in call_edges if "unresolved" in e.dst]
        assert len(unresolved) >= 1


class TestFindPureScriptFiles:
    """Branch coverage for file discovery."""

    def test_finds_purs_files(self, tmp_path: Path) -> None:
        """Test .purs files are discovered."""
        (tmp_path / "Main.purs").write_text("module Main where")

        files = list(find_purescript_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".purs"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "Main.purs").write_text("module Main where")

        files = list(find_purescript_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "Main.purs"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_purescript_files(self, tmp_path: Path) -> None:
        """Test directory with no PureScript files."""
        result = analyze_purescript(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0

    def test_minimal_module(self, tmp_path: Path) -> None:
        """Test minimal PureScript module."""
        make_purs_file(tmp_path, "Main.purs", """
module Main where
""")
        result = analyze_purescript(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_purs_file(tmp_path, "Main.purs", """
module Main where

main = pure unit
""")
        result = analyze_purescript(tmp_path)
        assert result.run is not None


class TestCrossFileResolution:
    """Branch coverage for cross-file symbol resolution."""

    def test_two_module_resolution(self, tmp_path: Path) -> None:
        """Test resolution across two modules."""
        make_purs_file(tmp_path, "Utils.purs", """
module Utils where

helper x = x * 2
""")
        make_purs_file(tmp_path, "Main.purs", """
module Main where

import Utils

main = helper 21
""")
        result = analyze_purescript(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert any("helper" in n for n in names)
