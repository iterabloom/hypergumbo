"""Tests for the Pascal language analyzer."""

from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import pascal as pascal_module
from hypergumbo_lang_extended1.pascal import (
    PascalAnalysisResult,
    analyze_pascal,
    find_pascal_files,
    is_pascal_tree_sitter_available,
)


def make_pascal_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Pascal file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindPascalFiles:
    """Tests for find_pascal_files function."""

    def test_finds_pas_files(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "main.pas", "program Main; begin end.")
        make_pascal_file(tmp_path, "unit.pas", "unit MyUnit; interface implementation end.")
        files = list(find_pascal_files(tmp_path))
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"main.pas", "unit.pas"}

    def test_finds_pp_files(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "lib.pp", "unit Lib; interface implementation end.")
        files = list(find_pascal_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "lib.pp"

    def test_finds_dpr_files(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "project.dpr", "program MyProject; begin end.")
        files = list(find_pascal_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "project.dpr"

    def test_finds_lpr_files(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "project.lpr", "program LazarusProject; begin end.")
        files = list(find_pascal_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "project.lpr"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = list(find_pascal_files(tmp_path))
        assert files == []


class TestIsPascalTreeSitterAvailable:
    """Tests for is_pascal_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_pascal_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(pascal_module, "is_pascal_tree_sitter_available", return_value=False):
            assert pascal_module.is_pascal_tree_sitter_available() is False


class TestAnalyzePascal:
    """Tests for analyze_pascal function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "test.pas", "program Test; begin end.")
        with patch.object(pascal_module, "is_pascal_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Pascal analysis skipped"):
                result = pascal_module.analyze_pascal(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_programs(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "main.pas", """program HelloWorld;
begin
  WriteLn('Hello');
end.
""")
        result = analyze_pascal(tmp_path)
        assert not result.skipped
        prog = next((s for s in result.symbols if s.name == "HelloWorld"), None)
        assert prog is not None
        assert prog.kind == "program"
        assert prog.language == "pascal"

    def test_extracts_units(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "myunit.pas", """unit MyUnit;
interface
implementation
end.
""")
        result = analyze_pascal(tmp_path)
        assert not result.skipped
        unit = next((s for s in result.symbols if s.name == "MyUnit"), None)
        assert unit is not None
        assert unit.kind == "module"
        assert unit.language == "pascal"

    def test_extracts_functions(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "funcs.pas", """program FuncTest;

function Add(A, B: Integer): Integer;
begin
  Result := A + B;
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        assert not result.skipped
        func = next((s for s in result.symbols if s.name == "Add"), None)
        assert func is not None
        assert func.kind == "function"
        assert "function Add" in func.signature
        assert "Integer" in func.signature

    def test_extracts_procedures(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "procs.pas", """program ProcTest;

procedure Greet(Name: string);
begin
  WriteLn('Hello, ', Name);
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        assert not result.skipped
        proc = next((s for s in result.symbols if s.name == "Greet"), None)
        assert proc is not None
        assert proc.kind == "function"
        assert "procedure Greet" in proc.signature
        assert proc.meta["proc_kind"] == "procedure"

    def test_function_param_count(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "params.pas", """program ParamTest;

function Calculate(X, Y, Z: Integer): Integer;
begin
  Result := X + Y + Z;
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        func = next((s for s in result.symbols if s.name == "Calculate"), None)
        assert func is not None
        assert func.meta["param_count"] == 3

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "calls.pas", """program CallTest;

procedure Helper;
begin
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
        assert not result.skipped
        # Main calls Helper
        edge = next(
            (e for e in result.edges if "Main" in e.src and "Helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"
        assert edge.confidence == 1.0

    def test_recursive_calls(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "recursive.pas", """program Recursive;

function Factorial(N: Integer): Integer;
begin
  if N <= 1 then
    Result := 1
  else
    Result := N * Factorial(N - 1);
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        # Factorial calls itself
        edge = next(
            (e for e in result.edges if "Factorial" in e.src and "Factorial" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_filters_builtins(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "builtins.pas", """program BuiltinTest;

procedure Main;
begin
  WriteLn('Hello');
  Inc(x);
  SetLength(arr, 10);
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        # Should not have edges to WriteLn, Inc, SetLength (builtins)
        builtin_edges = [e for e in result.edges if any(
            b in e.dst.lower() for b in ["writeln", "inc", "setlength"]
        )]
        assert len(builtin_edges) == 0

    def test_case_insensitive_matching(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "case.pas", """program CaseTest;

procedure DoSomething;
begin
end;

procedure Main;
begin
  DOSOMETHING;
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        # Should match despite case difference
        edge = next(
            (e for e in result.edges if "Main" in e.src and "DoSomething" in e.dst),
            None
        )
        assert edge is not None
        assert edge.confidence == 1.0

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "unresolved.pas", """program Unresolved;

procedure Main;
begin
  ExternalProc;
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        edge = next(
            (e for e in result.edges if "ExternalProc" in e.dst),
            None
        )
        assert edge is not None
        assert "unresolved" in edge.dst
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "test.pas", """program Test;

procedure Foo;
begin
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        func = next((s for s in result.symbols if s.name == "Foo"), None)
        assert func is not None
        assert func.origin == "pascal.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "test.pas", "program Test; begin end.")
        result = analyze_pascal(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "pascal.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_pascal(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "test.pas", """program Test;

function MyFunc: Integer;
begin
  Result := 42;
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        func = next((s for s in result.symbols if s.name == "MyFunc"), None)
        assert func is not None
        assert func.id == func.stable_id
        assert "pascal:" in func.id
        assert "test.pas" in func.id
        assert "MyFunc" in func.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_pascal_file(tmp_path, "test.pas", """program Test;

function MyFunc: Integer;
begin
  Result := 42;
end;

begin
end.
""")
        result = analyze_pascal(tmp_path)
        func = next((s for s in result.symbols if s.name == "MyFunc"), None)
        assert func is not None
        assert func.span is not None
        assert func.span.start_line >= 1
        assert func.span.end_line >= func.span.start_line
