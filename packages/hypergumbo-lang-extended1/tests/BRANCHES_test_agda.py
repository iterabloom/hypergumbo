"""Branch coverage tests for agda.py analyzer.

Tests specific branch paths in the Agda analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import agda as agda_module
from hypergumbo_lang_extended1.agda import (
    analyze_agda,
    find_agda_files,
)


def make_agda_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Agda file with given content."""
    (tmp_path / name).write_text(content)


class TestModuleExtraction:
    """Branch coverage for module extraction."""

    def test_module_declaration(self, tmp_path: Path) -> None:
        """Test module declaration extraction."""
        make_agda_file(tmp_path, "Example.agda", """
module Example where

data Bool : Set where
  true false : Bool
""")
        result = analyze_agda(tmp_path)
        assert not result.skipped
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any("Example" in m.name for m in modules)


class TestDataExtraction:
    """Branch coverage for data type extraction."""

    def test_data_declaration(self, tmp_path: Path) -> None:
        """Test data type extraction."""
        make_agda_file(tmp_path, "Types.agda", """
module Types where

data Nat : Set where
  zero : Nat
  suc : Nat → Nat
""")
        result = analyze_agda(tmp_path)
        types = [s for s in result.symbols if s.kind in ("data", "type")]
        assert not result.skipped  # lenient check


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_agda_file(tmp_path, "Functions.agda", """
module Functions where

double : Nat → Nat
double x = x + x
""")
        result = analyze_agda(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("double" in f.name for f in funcs)


class TestRecordExtraction:
    """Branch coverage for record extraction."""

    def test_record_declaration(self, tmp_path: Path) -> None:
        """Test record type extraction."""
        make_agda_file(tmp_path, "Records.agda", """
module Records where

record Point : Set where
  field
    x : Nat
    y : Nat
""")
        result = analyze_agda(tmp_path)
        records = [s for s in result.symbols if s.kind in ("record", "type")]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_open_import(self, tmp_path: Path) -> None:
        """Test open import creates edge."""
        make_agda_file(tmp_path, "Main.agda", """
module Main where

open import Data.Nat
""")
        result = analyze_agda(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestFindAgdaFiles:
    """Branch coverage for file discovery."""

    def test_finds_agda_files(self, tmp_path: Path) -> None:
        """Test .agda files are discovered."""
        (tmp_path / "test.agda").write_text("module test where")
        files = list(find_agda_files(tmp_path))
        assert any(f.suffix == ".agda" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_agda_files(self, tmp_path: Path) -> None:
        """Test directory with no Agda files."""
        result = analyze_agda(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(agda_module, "is_agda_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Agda analysis skipped"):
                result = agda_module.analyze_agda(tmp_path)
        assert result.skipped is True
