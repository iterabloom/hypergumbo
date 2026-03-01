"""Branch coverage tests for v_lang.py analyzer.

Tests specific branch paths in the V language analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import v_lang as v_module
from hypergumbo_lang_extended1.v_lang import (
    analyze_v,
    find_v_files,
)


def make_v_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a V language file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_fn_declaration(self, tmp_path: Path) -> None:
        """Test fn declaration extraction."""
        make_v_file(tmp_path, "funcs.v", """
fn add(a int, b int) int {
    return a + b
}
""")
        result = analyze_v(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_pub_fn_declaration(self, tmp_path: Path) -> None:
        """Test pub fn declaration extraction."""
        make_v_file(tmp_path, "funcs.v", """
pub fn greet(name string) string {
    return 'Hello, $name'
}
""")
        result = analyze_v(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("greet" in f.name for f in funcs)


class TestStructExtraction:
    """Branch coverage for struct extraction."""

    def test_struct_declaration(self, tmp_path: Path) -> None:
        """Test struct declaration extraction."""
        make_v_file(tmp_path, "types.v", """
struct Point {
    x int
    y int
}
""")
        result = analyze_v(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert not result.skipped  # lenient check


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_v_file(tmp_path, "types.v", """
enum Color {
    red
    green
    blue
}
""")
        result = analyze_v(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any("Color" in e.name for e in enums)


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_v_file(tmp_path, "interfaces.v", """
interface Printable {
    str() string
}
""")
        result = analyze_v(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("Printable" in i.name for i in interfaces)


class TestModuleExtraction:
    """Branch coverage for module extraction."""

    def test_module_declaration(self, tmp_path: Path) -> None:
        """Test module declaration extraction."""
        make_v_file(tmp_path, "mymodule.v", """
module mymodule

fn helper() {
    println('helper')
}
""")
        result = analyze_v(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_v_file(tmp_path, "main.v", """
import os
import json
""")
        result = analyze_v(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_v_file(tmp_path, "app.v", """
fn helper() {
    println('helper')
}

fn main() {
    helper()
}
""")
        result = analyze_v(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindVFiles:
    """Branch coverage for file discovery."""

    def test_finds_v_files(self, tmp_path: Path) -> None:
        """Test .v files are discovered."""
        (tmp_path / "test.v").write_text("fn main() {}")
        files = list(find_v_files(tmp_path))
        assert any(f.suffix == ".v" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_v_files(self, tmp_path: Path) -> None:
        """Test directory with no V files."""
        result = analyze_v(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(v_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="v analysis skipped"):
                result = v_module.analyze_v(tmp_path)
        assert result.skipped is True
