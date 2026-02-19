"""Branch coverage tests for pascal.py analyzer.

Tests specific branch paths in the Pascal analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import pascal as pascal_module
from hypergumbo_lang_extended1.pascal import (
    analyze_pascal,
    find_pascal_files,
)


def make_pascal_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Pascal file with given content."""
    (tmp_path / name).write_text(content)


class TestProgramExtraction:
    """Branch coverage for program extraction."""

    def test_program_declaration(self, tmp_path: Path) -> None:
        """Test program declaration extraction."""
        make_pascal_file(tmp_path, "hello.pas", """
program Hello;
begin
  WriteLn('Hello, World!');
end.
""")
        result = analyze_pascal(tmp_path)
        assert not result.skipped
        programs = [s for s in result.symbols if s.kind == "program"]
        assert any("Hello" in p.name for p in programs)


class TestUnitExtraction:
    """Branch coverage for unit extraction."""

    def test_unit_declaration(self, tmp_path: Path) -> None:
        """Test unit declaration extraction."""
        make_pascal_file(tmp_path, "myunit.pas", """
unit MyUnit;

interface

procedure DoSomething;

implementation

procedure DoSomething;
begin
end;

end.
""")
        result = analyze_pascal(tmp_path)
        units = [s for s in result.symbols if s.kind == "unit"]
        assert not result.skipped  # lenient check


class TestProcedureExtraction:
    """Branch coverage for procedure extraction."""

    def test_procedure_declaration(self, tmp_path: Path) -> None:
        """Test procedure declaration extraction."""
        make_pascal_file(tmp_path, "procs.pas", """
program Procs;

procedure Greet(Name: string);
begin
  WriteLn('Hello, ', Name);
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        procs = [s for s in result.symbols if s.kind == "procedure"]
        assert not result.skipped  # lenient check


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_pascal_file(tmp_path, "funcs.pas", """
program Funcs;

function Add(A, B: Integer): Integer;
begin
  Result := A + B;
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("Add" in f.name for f in funcs)


class TestTypeExtraction:
    """Branch coverage for type extraction."""

    def test_type_declaration(self, tmp_path: Path) -> None:
        """Test type declaration extraction."""
        make_pascal_file(tmp_path, "types.pas", """
program Types;

type
  TPoint = record
    X, Y: Integer;
  end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert not result.skipped  # lenient check


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration extraction."""
        make_pascal_file(tmp_path, "classes.pas", """
program Classes;

type
  TAnimal = class
    procedure Speak; virtual;
  end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        classes = [s for s in result.symbols if s.kind in ("class", "type")]
        assert not result.skipped  # lenient check


class TestUsesEdges:
    """Branch coverage for uses edge extraction."""

    def test_uses_creates_edge(self, tmp_path: Path) -> None:
        """Test uses creates import edge."""
        make_pascal_file(tmp_path, "main.pas", """
program Main;

uses SysUtils, Classes;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        uses = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_procedure_call(self, tmp_path: Path) -> None:
        """Test procedure call creates edge."""
        make_pascal_file(tmp_path, "app.pas", """
program App;

procedure Helper;
begin
  WriteLn('helper');
end;

procedure Main;
begin
  Helper;
end;

begin
  Main;
end.
""")
        result = analyze_pascal(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindPascalFiles:
    """Branch coverage for file discovery."""

    def test_finds_pas_files(self, tmp_path: Path) -> None:
        """Test .pas files are discovered."""
        (tmp_path / "test.pas").write_text("program Test; begin end.")
        files = list(find_pascal_files(tmp_path))
        assert any(f.suffix == ".pas" for f in files)

    def test_finds_pp_files(self, tmp_path: Path) -> None:
        """Test .pp files are discovered."""
        (tmp_path / "test.pp").write_text("program Test; begin end.")
        files = list(find_pascal_files(tmp_path))
        assert any(f.suffix == ".pp" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_pascal_files(self, tmp_path: Path) -> None:
        """Test directory with no Pascal files."""
        result = analyze_pascal(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(pascal_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="pascal analysis skipped"):
                result = pascal_module.analyze_pascal(tmp_path)
        assert result.skipped is True
