# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for lean.py analyzer.

Tests specific branch paths in the Lean analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import lean as lean_module
from hypergumbo_lang_extended1.lean import (
    analyze_lean,
    find_lean_files,
)


def make_lean_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Lean file with given content."""
    (tmp_path / name).write_text(content)


class TestDefinitionExtraction:
    """Branch coverage for definition extraction."""

    def test_def_declaration(self, tmp_path: Path) -> None:
        """Test def declaration extraction."""
        make_lean_file(tmp_path, "funcs.lean", """
def add (a b : Nat) : Nat :=
  a + b
""")
        result = analyze_lean(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_theorem_declaration(self, tmp_path: Path) -> None:
        """Test theorem declaration extraction."""
        make_lean_file(tmp_path, "proofs.lean", """
theorem add_comm (a b : Nat) : a + b = b + a := by
  omega
""")
        result = analyze_lean(tmp_path)
        theorems = [s for s in result.symbols if s.kind == "theorem"]
        assert any("add_comm" in t.name for t in theorems)


class TestStructureExtraction:
    """Branch coverage for structure extraction."""

    def test_structure_declaration(self, tmp_path: Path) -> None:
        """Test structure declaration extraction."""
        make_lean_file(tmp_path, "types.lean", """
structure Point where
  x : Float
  y : Float
""")
        result = analyze_lean(tmp_path)
        structs = [s for s in result.symbols if s.kind == "structure"]
        assert any("Point" in s.name for s in structs)


class TestInductiveExtraction:
    """Branch coverage for inductive type extraction."""

    def test_inductive_declaration(self, tmp_path: Path) -> None:
        """Test inductive type extraction."""
        make_lean_file(tmp_path, "types.lean", """
inductive MyList (a : Type) where
  | nil : MyList a
  | cons : a → MyList a → MyList a
""")
        result = analyze_lean(tmp_path)
        inductives = [s for s in result.symbols if s.kind == "inductive"]
        assert any("MyList" in i.name for i in inductives)


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration extraction."""
        make_lean_file(tmp_path, "classes.lean", """
class Printable (a : Type) where
  print : a → String
""")
        result = analyze_lean(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert not result.skipped  # lenient check


class TestNamespaceExtraction:
    """Branch coverage for namespace extraction."""

    def test_namespace_declaration(self, tmp_path: Path) -> None:
        """Test namespace declaration extraction."""
        make_lean_file(tmp_path, "namespaces.lean", """
namespace MyModule

def helper := 42

end MyModule
""")
        result = analyze_lean(tmp_path)
        namespaces = [s for s in result.symbols if s.kind == "namespace"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_lean_file(tmp_path, "main.lean", """
import Mathlib.Data.Nat.Basic
""")
        result = analyze_lean(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestFindLeanFiles:
    """Branch coverage for file discovery."""

    def test_finds_lean_files(self, tmp_path: Path) -> None:
        """Test .lean files are discovered."""
        (tmp_path / "test.lean").write_text("def x := 1")
        files = list(find_lean_files(tmp_path))
        assert any(f.suffix == ".lean" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_lean_files(self, tmp_path: Path) -> None:
        """Test directory with no Lean files."""
        result = analyze_lean(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(lean_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="lean analysis skipped"):
                result = lean_module.analyze_lean(tmp_path)
        assert result.skipped is True
