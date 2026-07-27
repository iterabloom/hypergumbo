# SPDX-License-Identifier: AGPL-3.0-or-later
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

from hypergumbo_lang_extended1 import nim as nim_module
from hypergumbo_lang_extended1.nim import (
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

    def test_import_edge_dst_is_well_formed(self, temp_repo: Path) -> None:
        """INV-lumuh: the import-edge dst must be a well-formed 5-slot
        ``{lang}:{path}:{span}:{name}:{kind}`` id with the module name in the
        PATH slot (``parts[1]``) — not the malformed ``nim:?:{name}:module``
        (4 slots, ``?`` path), which leaves ``module_path``/``src`` at the
        ``<unknown>`` sentinel and makes the module invisible to
        ``refine_frameworks`` (reads ``parts[1]``) and ``is_test_file``, so
        jester/karax get false-demoted."""
        (temp_repo / "main.nim").write_text("import jester\n")
        result = analyze_nim(temp_repo)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) == 1
        parts = import_edges[0].dst.split(":")
        assert len(parts) == 5, import_edges[0].dst
        assert parts[0] == "nim"
        assert parts[1] == "jester"   # module in the path slot, not "?"


class TestNimExportedSymbols:
    """INV-bisom: Nim marks a public symbol with a ``*`` export postfix
    (``proc greet*``), which the tree-sitter grammar wraps in an
    ``exported_symbol`` node instead of exposing a bare ``identifier`` child.
    The extractors previously looked only for a direct ``identifier``, so EVERY
    exported proc / func / method / type was silently dropped (0/246 on the
    filed corpus). They must be extracted AND marked ``is_exported``."""

    def test_extracts_exported_proc(self, temp_repo: Path) -> None:
        (temp_repo / "m.nim").write_text('''
proc greet*(name: string): string =
  result = "hi " & name
''')
        result = analyze_nim(temp_repo)
        procs = [s for s in result.symbols if s.name == "greet"]
        assert len(procs) == 1
        assert procs[0].kind == "function"
        assert procs[0].is_exported is True

    def test_extracts_exported_func(self, temp_repo: Path) -> None:
        (temp_repo / "m.nim").write_text('''
func double*(n: int): int = n * 2
''')
        result = analyze_nim(temp_repo)
        funcs = [s for s in result.symbols if s.name == "double"]
        assert len(funcs) == 1
        assert funcs[0].kind == "function"
        assert funcs[0].is_exported is True

    def test_extracts_exported_method(self, temp_repo: Path) -> None:
        (temp_repo / "m.nim").write_text('''
type Animal = ref object of RootObj
  name: string

method speak*(a: Animal): string =
  result = "sound"
''')
        result = analyze_nim(temp_repo)
        methods = [
            s for s in result.symbols
            if s.name == "speak" and s.kind == "method"
        ]
        assert len(methods) == 1
        assert methods[0].is_exported is True

    def test_extracts_exported_type(self, temp_repo: Path) -> None:
        (temp_repo / "m.nim").write_text('''
type Person* = object
  name: string
''')
        result = analyze_nim(temp_repo)
        types = [
            s for s in result.symbols
            if s.name == "Person" and s.kind == "type"
        ]
        assert len(types) == 1
        assert types[0].is_exported is True

    def test_non_exported_symbols_not_marked_exported(
        self, temp_repo: Path,
    ) -> None:
        """Regression: a plain (non-``*``) proc is still extracted and is NOT
        marked exported."""
        (temp_repo / "m.nim").write_text('''
proc internalHelper(x: int): int =
  result = x + 1
''')
        result = analyze_nim(temp_repo)
        procs = [s for s in result.symbols if s.name == "internalHelper"]
        assert len(procs) == 1
        assert procs[0].is_exported is False


class TestNimCallResolution:
    """Tests for Nim call resolution."""

    def test_proc_call_edge(self, temp_repo: Path) -> None:
        """Creates call edges for proc calls."""
        (temp_repo / "math.nim").write_text('''
proc double(x: int): int =
  x * 2

proc quadruple(x: int): int =
  double(double(x))
''')

        result = analyze_nim(temp_repo)

        # Should have call edges from quadruple to double
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        quad_calls = [e for e in call_edges if "quadruple" in e.src]
        assert len(quad_calls) >= 1
        assert any("double" in e.dst for e in quad_calls)

    def test_external_proc_call(self, temp_repo: Path) -> None:
        """Creates call edges for external proc calls with lower confidence."""
        (temp_repo / "io_test.nim").write_text('''
proc printHello() =
  echo("Hello")
''')

        result = analyze_nim(temp_repo)

        # Should have call edge to external echo
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        external_calls = [e for e in call_edges if "external" in e.dst]
        assert len(external_calls) >= 1
        assert any("echo" in e.dst for e in external_calls)
        # External calls have lower confidence
        for e in external_calls:
            assert e.confidence == 0.50

    def test_resolved_call_confidence(self, temp_repo: Path) -> None:
        """Resolved calls have higher confidence than external calls."""
        (temp_repo / "test.nim").write_text('''
proc internalProc() =
  discard

proc caller() =
  internalProc()
''')

        result = analyze_nim(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Find the edge from caller to internalProc
        resolved_call = next((e for e in call_edges if "internalProc" in e.dst and "external" not in e.dst), None)
        assert resolved_call is not None
        # Resolved calls have confidence 0.85 * lookup confidence (usually 1.0)
        assert resolved_call.confidence > 0.70


class TestNimAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "test.nim").write_text("echo \"test\"")

        with patch.object(nim_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="nim analysis skipped"):
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
        assert result.run.pass_id == "nim"
        assert result.run.files_analyzed >= 1


class TestNimImportAliases:
    """Tests for import alias extraction and qualified call resolution."""

    def test_extracts_import_alias(self, temp_repo: Path) -> None:
        """Extracts import alias from 'import as' statement."""
        from hypergumbo_lang_extended1.nim import _extract_import_aliases
        from tree_sitter_language_pack import get_parser

        parser = get_parser("nim")

        nim_file = temp_repo / "Main.nim"
        nim_file.write_text("""
import strutils as su
import os as osmod

proc greet(name: string) =
    echo su.strip(name)
""")

        source = nim_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_import_aliases(tree, source)

        # Both aliases should be extracted
        assert "su" in aliases
        assert aliases["su"] == "strutils"
        assert "osmod" in aliases
        assert aliases["osmod"] == "os"

    def test_import_alias_creates_edge(self, temp_repo: Path) -> None:
        """Import with alias creates import edge."""
        (temp_repo / "main.nim").write_text("""
import strutils as su

proc greet(name: string) =
    echo su.strip(name)
""")

        result = analyze_nim(temp_repo)

        # Should have import edge for strutils
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        imported = {e.dst for e in import_edges}
        assert any("strutils" in dst for dst in imported)

    def test_qualified_call_uses_alias(self, temp_repo: Path) -> None:
        """Qualified call resolution uses import alias for path hint."""
        (temp_repo / "main.nim").write_text("""
import strutils as su

proc processText(text: string) =
    echo su.strip(text)
""")

        result = analyze_nim(temp_repo)

        # Should have call edge (we can't verify path_hint directly but can verify it doesn't crash)
        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "processText" for s in symbols)

        # Should have call edges from processText
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        proc_calls = [e for e in call_edges if "processText" in e.src]
        # Should have at least the echo call
        assert len(proc_calls) >= 1


class TestNimCyclomaticComplexity:
    """INV-loguk slice C: callable Nim symbols carry non-null CC + LOC.
    Real-grammar verification of the collision-free arm nodes (elif_branch /
    of_branch / except_branch) + and/or short-circuit via infix_expression.
    Note: primary if/for/while/case/try are NOT counted (keyword/named-node
    .type collision would double-count), so CC is a conservative estimate."""

    def test_branchy_proc_has_cc_and_loc(self, tmp_path: Path) -> None:
        (tmp_path / "f.nim").write_text("""proc classify(n: int): string =
  if n > 0 and n < 10:
    result = "small"
  elif n >= 10 or n == -1:
    result = "big"
  else:
    result = "other"
  case n
  of 1:
    echo "one"
  of 2:
    echo "two"
  else:
    echo "many"
  try:
    echo n
  except ValueError:
    echo "err"
""")
        result = analyze_nim(tmp_path)
        fn = next(s for s in result.symbols
                  if s.kind in ("function", "method") and s.name == "classify")
        # base 1 + elif + 2 of_branch + except + and + or = 7
        assert fn.cyclomatic_complexity == 7
        assert fn.line_span is not None and fn.line_span >= 4

    def test_straight_line_proc_cc_is_one(self, tmp_path: Path) -> None:
        (tmp_path / "g.nim").write_text("proc g(x: int): int =\n  return x\n")
        result = analyze_nim(tmp_path)
        fn = next(s for s in result.symbols
                  if s.kind in ("function", "method") and s.name == "g")
        assert fn.cyclomatic_complexity == 1
        assert fn.line_span is not None

    def test_callables_non_null_type_null(self, tmp_path: Path) -> None:
        (tmp_path / "m.nim").write_text("""type
  Color = enum
    Red, Green, Blue

proc f(x: int): int =
  if x > 0:
    return 1
  return 0
""")
        result = analyze_nim(tmp_path)
        callables = [s for s in result.symbols if s.kind in ("function", "method")]
        assert callables
        for s in callables:
            assert s.cyclomatic_complexity is not None, s.name
            assert s.line_span is not None, s.name
        for s in result.symbols:
            if s.kind not in ("function", "method"):
                assert s.cyclomatic_complexity is None, (s.kind, s.name)
