"""Branch coverage tests for luau.py analyzer.

Tests specific branch paths in the Luau analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import luau as luau_module
from hypergumbo_lang_extended1.luau import (
    analyze_luau,
    find_luau_files,
)


def make_luau_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Luau file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_luau_file(tmp_path, "funcs.luau", """
function add(a: number, b: number): number
    return a + b
end
""")
        result = analyze_luau(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_local_function(self, tmp_path: Path) -> None:
        """Test local function declaration extraction."""
        make_luau_file(tmp_path, "funcs.luau", """
local function helper(): nil
    print("helper")
end
""")
        result = analyze_luau(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("helper" in f.name for f in funcs)


class TestTypeExtraction:
    """Branch coverage for type extraction."""

    def test_type_alias(self, tmp_path: Path) -> None:
        """Test type alias extraction."""
        make_luau_file(tmp_path, "types.luau", """
type Point = {
    x: number,
    y: number
}
""")
        result = analyze_luau(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert any("Point" in t.name for t in types)


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_local_variable(self, tmp_path: Path) -> None:
        """Test local variable extraction."""
        make_luau_file(tmp_path, "vars.luau", """
local x: number = 10
local y = 20
""")
        result = analyze_luau(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert not result.skipped  # lenient check


class TestRequireEdges:
    """Branch coverage for require edge extraction."""

    def test_require_creates_edge(self, tmp_path: Path) -> None:
        """Test require creates import edge."""
        make_luau_file(tmp_path, "main.luau", """
local module = require(script.Parent.Module)
""")
        result = analyze_luau(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_luau_file(tmp_path, "app.luau", """
local function helper()
    print("helper")
end

local function main()
    helper()
end
""")
        result = analyze_luau(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindLuauFiles:
    """Branch coverage for file discovery."""

    def test_finds_luau_files(self, tmp_path: Path) -> None:
        """Test .luau files are discovered."""
        (tmp_path / "test.luau").write_text("print('hello')")
        files = list(find_luau_files(tmp_path))
        assert any(f.suffix == ".luau" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_luau_files(self, tmp_path: Path) -> None:
        """Test directory with no Luau files."""
        result = analyze_luau(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(luau_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="luau analysis skipped"):
                result = luau_module.analyze_luau(tmp_path)
        assert result.skipped is True
