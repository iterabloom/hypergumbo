"""Tests for Ada analysis pass.

Tests verify that the Ada analyzer correctly extracts:
- Package specifications and bodies
- Function and procedure declarations/implementations
- Type definitions (records, enums)
- Constants and variables
- With/use clauses (imports)
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import ada as ada_module
from hypergumbo.analyze.ada import (
    analyze_ada,
    find_ada_files,
    is_ada_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindAdaFiles:
    """Tests for find_ada_files function."""

    def test_finds_adb_files(self, temp_repo: Path) -> None:
        """Finds .adb (Ada body) files."""
        (temp_repo / "calculator.adb").write_text("package body Calculator is end Calculator;")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_ada_files(temp_repo))
        filenames = {f.name for f in files}

        assert "calculator.adb" in filenames
        assert "README.md" not in filenames

    def test_finds_ads_files(self, temp_repo: Path) -> None:
        """Finds .ads (Ada spec) files."""
        (temp_repo / "calculator.ads").write_text("package Calculator is end Calculator;")

        files = list(find_ada_files(temp_repo))
        filenames = {f.name for f in files}

        assert "calculator.ads" in filenames

    def test_finds_ada_files(self, temp_repo: Path) -> None:
        """Finds .ada files."""
        (temp_repo / "main.ada").write_text("procedure Main is begin null; end Main;")

        files = list(find_ada_files(temp_repo))
        filenames = {f.name for f in files}

        assert "main.ada" in filenames


class TestAdaTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_ada_tree_sitter_available()
        assert isinstance(result, bool)


class TestAdaAnalysis:
    """Tests for Ada analysis with tree-sitter."""

    def test_analyzes_package_declaration(self, temp_repo: Path) -> None:
        """Detects package declarations."""
        (temp_repo / "calculator.ads").write_text('''
package Calculator is
   function Add(A, B : Integer) return Integer;
end Calculator;
''')

        result = analyze_ada(temp_repo)

        assert not result.skipped
        pkg_names = {s.name for s in result.symbols if s.kind == "package"}
        assert "Calculator" in pkg_names

    def test_analyzes_package_body(self, temp_repo: Path) -> None:
        """Detects package bodies."""
        (temp_repo / "calculator.adb").write_text('''
package body Calculator is
   function Add(A, B : Integer) return Integer is
   begin
      return A + B;
   end Add;

   procedure Print_Result(Value : Integer) is
   begin
      null;
   end Print_Result;
end Calculator;
''')

        result = analyze_ada(temp_repo)

        pkg_names = {s.name for s in result.symbols if s.kind == "package"}
        assert "Calculator" in pkg_names
        # Verify both function and procedure bodies are detected
        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "Add" in func_names
        proc_names = {s.name for s in result.symbols if s.kind == "procedure"}
        assert "Print_Result" in proc_names

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function definitions."""
        (temp_repo / "math.ads").write_text('''
package Math is
   function Add(A, B : Integer) return Integer;
   function Multiply(X, Y : Float) return Float;
end Math;
''')

        result = analyze_ada(temp_repo)

        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "Add" in func_names
        assert "Multiply" in func_names

    def test_analyzes_procedure(self, temp_repo: Path) -> None:
        """Detects procedure definitions."""
        (temp_repo / "io.ads").write_text('''
package IO is
   procedure Print(Message : String);
   procedure Clear_Screen;
end IO;
''')

        result = analyze_ada(temp_repo)

        proc_names = {s.name for s in result.symbols if s.kind == "procedure"}
        assert "Print" in proc_names
        assert "Clear_Screen" in proc_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters and return type."""
        (temp_repo / "funcs.ads").write_text('''
package Funcs is
   function Compute(X, Y : Integer; Scale : Float) return Float;
end Funcs;
''')

        result = analyze_ada(temp_repo)

        func = next(s for s in result.symbols if s.name == "Compute")
        assert func.signature is not None
        assert "X" in func.signature or "Integer" in func.signature

    def test_analyzes_type_record(self, temp_repo: Path) -> None:
        """Detects record type definitions."""
        (temp_repo / "types.ads").write_text('''
package Types is
   type Point is record
      X, Y : Float;
   end record;

   type Rectangle is record
      TopLeft, BottomRight : Point;
   end record;
end Types;
''')

        result = analyze_ada(temp_repo)

        type_names = {s.name for s in result.symbols if s.kind == "type"}
        assert "Point" in type_names
        assert "Rectangle" in type_names

    def test_analyzes_constant(self, temp_repo: Path) -> None:
        """Detects constant declarations."""
        (temp_repo / "constants.ads").write_text('''
package Constants is
   Pi : constant Float := 3.14159;
   Max_Size : constant Integer := 1000;
end Constants;
''')

        result = analyze_ada(temp_repo)

        const_names = {s.name for s in result.symbols if s.kind == "constant"}
        assert "Pi" in const_names
        assert "Max_Size" in const_names

    def test_analyzes_with_clause(self, temp_repo: Path) -> None:
        """Detects with clauses as imports."""
        (temp_repo / "main.ads").write_text('''
with Ada.Text_IO;
with Ada.Integer_Text_IO;
with Calculator;

package Main is
   procedure Run;
end Main;
''')

        result = analyze_ada(temp_repo)

        # With clauses should create import edges
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        imported = {e.dst for e in import_edges}
        assert any("Ada.Text_IO" in dst for dst in imported)
        assert any("Calculator" in dst for dst in imported)


class TestAdaAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "test.ads").write_text("package Test is end Test;")

        with patch.object(ada_module, "is_ada_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Ada analysis skipped"):
                result = ada_module.analyze_ada(temp_repo)

        assert result.skipped is True


class TestAdaAnalysisRun:
    """Tests for Ada analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "test.ads").write_text('''
package Test is
   procedure Hello;
end Test;
''')

        result = analyze_ada(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "ada-v1"
        assert result.run.files_analyzed >= 1
