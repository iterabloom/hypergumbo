"""Tests for Nim language analysis pass.

Tests verify that the Nim analyzer correctly extracts:
- Import statements
- Type definitions (objects, enums)
- Proc definitions (procedures)
- Func definitions (pure functions)
- Method definitions
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import nim as nim_module
from hypergumbo.analyze.nim import (
    analyze_nim,
    find_nim_files,
    is_nim_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindNimFiles:
    """Tests for find_nim_files function."""

    def test_finds_nim_files(self, temp_repo: Path) -> None:
        """Finds .nim files."""
        (temp_repo / "main.nim").write_text("echo \"Hello\"")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_nim_files(temp_repo))
        filenames = {f.name for f in files}

        assert "main.nim" in filenames
        assert "README.md" not in filenames

    def test_finds_nims_files(self, temp_repo: Path) -> None:
        """Finds .nims (NimScript) files."""
        (temp_repo / "config.nims").write_text("switch(\"threads\", \"on\")")

        files = list(find_nim_files(temp_repo))
        filenames = {f.name for f in files}

        assert "config.nims" in filenames

    def test_finds_nimble_files(self, temp_repo: Path) -> None:
        """Finds .nimble (package) files."""
        (temp_repo / "mypackage.nimble").write_text("version = \"0.1.0\"")

        files = list(find_nim_files(temp_repo))
        filenames = {f.name for f in files}

        assert "mypackage.nimble" in filenames


class TestNimTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_nim_tree_sitter_available()
        assert isinstance(result, bool)


class TestNimAnalysis:
    """Tests for Nim analysis with tree-sitter."""

    def test_analyzes_proc(self, temp_repo: Path) -> None:
        """Detects proc definitions."""
        (temp_repo / "procs.nim").write_text('''
proc add(a, b: int): int =
  result = a + b

proc sayHello(name: string) =
  echo "Hello, " & name
''')

        result = analyze_nim(temp_repo)

        assert not result.skipped
        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "add" in func_names
        assert "sayHello" in func_names

    def test_analyzes_func(self, temp_repo: Path) -> None:
        """Detects func definitions (pure functions)."""
        (temp_repo / "funcs.nim").write_text('''
func multiply(x, y: int): int =
  x * y

func square(n: int): int =
  n * n
''')

        result = analyze_nim(temp_repo)

        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "multiply" in func_names
        assert "square" in func_names

    def test_analyzes_method(self, temp_repo: Path) -> None:
        """Detects method definitions."""
        (temp_repo / "methods.nim").write_text('''
type Animal = ref object of RootObj
  name: string

method speak(a: Animal) {.base.} =
  echo "..."

method speak(a: Dog) =
  echo "Woof!"
''')

        result = analyze_nim(temp_repo)

        method_names = {s.name for s in result.symbols if s.kind == "method"}
        assert "speak" in method_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters."""
        (temp_repo / "sig.nim").write_text('''
proc compute(x, y: int, scale: float): float =
  result = float(x + y) * scale
''')

        result = analyze_nim(temp_repo)

        func = next(s for s in result.symbols if s.name == "compute")
        assert func.signature is not None
        assert "x" in func.signature or "int" in func.signature

    def test_analyzes_type_object(self, temp_repo: Path) -> None:
        """Detects object type definitions."""
        (temp_repo / "types.nim").write_text('''
type
  Point = object
    x, y: int

  Rectangle = object
    topLeft, bottomRight: Point
''')

        result = analyze_nim(temp_repo)

        type_names = {s.name for s in result.symbols if s.kind == "type"}
        assert "Point" in type_names
        assert "Rectangle" in type_names

    def test_analyzes_import(self, temp_repo: Path) -> None:
        """Detects import statements as edges."""
        (temp_repo / "main.nim").write_text('''
import strutils, sequtils
import os

proc main() =
  echo "Hello"
''')

        result = analyze_nim(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        imported = {e.dst for e in import_edges}
        assert any("strutils" in dst for dst in imported)
        assert any("sequtils" in dst for dst in imported)
        assert any("os" in dst for dst in imported)


class TestNimAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "test.nim").write_text("echo \"test\"")

        with patch.object(nim_module, "is_nim_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Nim analysis skipped"):
                result = nim_module.analyze_nim(temp_repo)

        assert result.skipped is True


class TestNimAnalysisRun:
    """Tests for Nim analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "test.nim").write_text('''
proc hello() =
  echo "Hello"
''')

        result = analyze_nim(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "nim-v1"
        assert result.run.files_analyzed >= 1
