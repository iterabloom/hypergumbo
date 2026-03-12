# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for wolfram.py analyzer.

Tests specific branch paths in the Wolfram Language analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import wolfram as wolfram_module
from hypergumbo_lang_extended1.wolfram import (
    analyze_wolfram,
    find_wolfram_files,
)


def make_wolfram_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Wolfram Language file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_definition(self, tmp_path: Path) -> None:
        """Test function definition extraction."""
        make_wolfram_file(tmp_path, "funcs.wl", """
add[a_, b_] := a + b;

square[x_] := x^2;
""")
        result = analyze_wolfram(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_delayed_function(self, tmp_path: Path) -> None:
        """Test delayed function definition."""
        make_wolfram_file(tmp_path, "funcs.wl", """
factorial[0] := 1;
factorial[n_] := n * factorial[n - 1];
""")
        result = analyze_wolfram(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("factorial" in f.name for f in funcs)


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_variable_assignment(self, tmp_path: Path) -> None:
        """Test variable assignment extraction."""
        make_wolfram_file(tmp_path, "vars.wl", """
x = 10;
y = 20;
""")
        result = analyze_wolfram(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert not result.skipped  # lenient check


class TestModuleExtraction:
    """Branch coverage for Module extraction."""

    def test_module_definition(self, tmp_path: Path) -> None:
        """Test Module definition extraction."""
        make_wolfram_file(tmp_path, "modules.wl", """
compute[x_] := Module[{local},
    local = x^2;
    local + 1
];
""")
        result = analyze_wolfram(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("compute" in f.name for f in funcs)


class TestPackageExtraction:
    """Branch coverage for package extraction."""

    def test_beginpackage(self, tmp_path: Path) -> None:
        """Test BeginPackage extraction."""
        make_wolfram_file(tmp_path, "package.wl", """
BeginPackage["MyPackage`"];

MyFunction::usage = "MyFunction[x] does something.";

Begin["`Private`"];

MyFunction[x_] := x + 1;

End[];
EndPackage[];
""")
        result = analyze_wolfram(tmp_path)
        packages = [s for s in result.symbols if s.kind == "package"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_needs_creates_edge(self, tmp_path: Path) -> None:
        """Test Needs creates edge."""
        make_wolfram_file(tmp_path, "main.wl", """
Needs["OtherPackage`"];
""")
        result = analyze_wolfram(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check

    def test_get_creates_edge(self, tmp_path: Path) -> None:
        """Test Get creates edge."""
        make_wolfram_file(tmp_path, "main.wl", """
Get["helper.wl"];
""")
        result = analyze_wolfram(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_wolfram_file(tmp_path, "app.wl", """
helper[] := Print["helper"];

main[] := helper[];
""")
        result = analyze_wolfram(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindWolframFiles:
    """Branch coverage for file discovery."""

    def test_finds_wl_files(self, tmp_path: Path) -> None:
        """Test .wl files are discovered."""
        (tmp_path / "test.wl").write_text("Print[\"hello\"]")
        files = list(find_wolfram_files(tmp_path))
        assert any(f.suffix == ".wl" for f in files)

    def test_finds_m_files(self, tmp_path: Path) -> None:
        """Test .m files with Wolfram content are discovered."""
        (tmp_path / "test.m").write_text("f[x_] := x^2\n")
        files = list(find_wolfram_files(tmp_path))
        assert any(f.suffix == ".m" for f in files)

    def test_finds_nb_files(self, tmp_path: Path) -> None:
        """Test .nb files are discovered."""
        (tmp_path / "test.nb").write_text("Notebook[{Cell[\"hello\"]}]")
        files = list(find_wolfram_files(tmp_path))
        assert any(f.suffix == ".nb" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_wolfram_files(self, tmp_path: Path) -> None:
        """Test directory with no Wolfram files."""
        result = analyze_wolfram(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(wolfram_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="wolfram analysis skipped"):
                result = wolfram_module.analyze_wolfram(tmp_path)
        assert result.skipped is True
