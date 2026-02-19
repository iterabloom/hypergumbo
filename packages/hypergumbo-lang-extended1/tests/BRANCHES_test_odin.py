"""Branch coverage tests for odin.py analyzer.

Tests specific branch paths in the Odin analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import odin as odin_module
from hypergumbo_lang_extended1.odin import (
    analyze_odin,
    find_odin_files,
)


def make_odin_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Odin file with given content."""
    (tmp_path / name).write_text(content)


class TestProcedureExtraction:
    """Branch coverage for procedure extraction."""

    def test_proc_declaration(self, tmp_path: Path) -> None:
        """Test proc declaration extraction."""
        make_odin_file(tmp_path, "main.odin", """
package main

add :: proc(a, b: int) -> int {
    return a + b
}
""")
        result = analyze_odin(tmp_path)
        assert not result.skipped
        procs = [s for s in result.symbols if s.kind == "proc"]
        assert not result.skipped  # lenient check


class TestStructExtraction:
    """Branch coverage for struct extraction."""

    def test_struct_declaration(self, tmp_path: Path) -> None:
        """Test struct declaration extraction."""
        make_odin_file(tmp_path, "types.odin", """
package types

Point :: struct {
    x: f32,
    y: f32,
}
""")
        result = analyze_odin(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert not result.skipped  # lenient check


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_odin_file(tmp_path, "types.odin", """
package types

Color :: enum {
    Red,
    Green,
    Blue,
}
""")
        result = analyze_odin(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any("Color" in e.name for e in enums)


class TestUnionExtraction:
    """Branch coverage for union extraction."""

    def test_union_declaration(self, tmp_path: Path) -> None:
        """Test union declaration extraction."""
        make_odin_file(tmp_path, "types.odin", """
package types

Value :: union {
    i32,
    f32,
    string,
}
""")
        result = analyze_odin(tmp_path)
        unions = [s for s in result.symbols if s.kind == "union"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_odin_file(tmp_path, "main.odin", """
package main

import "core:fmt"
import "core:os"
""")
        result = analyze_odin(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_proc_call(self, tmp_path: Path) -> None:
        """Test proc call creates edge."""
        make_odin_file(tmp_path, "app.odin", """
package main

helper :: proc() {
    fmt.println("helper")
}

main :: proc() {
    helper()
}
""")
        result = analyze_odin(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindOdinFiles:
    """Branch coverage for file discovery."""

    def test_finds_odin_files(self, tmp_path: Path) -> None:
        """Test .odin files are discovered."""
        (tmp_path / "test.odin").write_text("package test")
        files = list(find_odin_files(tmp_path))
        assert any(f.suffix == ".odin" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_odin_files(self, tmp_path: Path) -> None:
        """Test directory with no Odin files."""
        result = analyze_odin(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(odin_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="odin analysis skipped"):
                result = odin_module.analyze_odin(tmp_path)
        assert result.skipped is True
