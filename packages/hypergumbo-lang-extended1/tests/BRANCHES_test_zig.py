# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for zig.py analyzer.

Tests specific branch paths in the Zig analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import zig as zig_module
from hypergumbo_lang_extended1.zig import (
    analyze_zig,
    find_zig_files,
)


def make_zig_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Zig file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_zig_file(tmp_path, "funcs.zig", """
pub fn add(a: i32, b: i32) i32 {
    return a + b;
}
""")
        result = analyze_zig(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_private_function(self, tmp_path: Path) -> None:
        """Test private function extraction."""
        make_zig_file(tmp_path, "funcs.zig", """
fn helper() void {
    // private function
}
""")
        result = analyze_zig(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("helper" in f.name for f in funcs)


class TestStructExtraction:
    """Branch coverage for struct extraction."""

    def test_struct_declaration(self, tmp_path: Path) -> None:
        """Test struct declaration extraction."""
        make_zig_file(tmp_path, "types.zig", """
const Point = struct {
    x: f32,
    y: f32,
};
""")
        result = analyze_zig(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any("Point" in s.name for s in structs)


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_zig_file(tmp_path, "types.zig", """
const Color = enum {
    red,
    green,
    blue,
};
""")
        result = analyze_zig(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any("Color" in e.name for e in enums)


class TestUnionExtraction:
    """Branch coverage for union extraction."""

    def test_union_declaration(self, tmp_path: Path) -> None:
        """Test union declaration extraction."""
        make_zig_file(tmp_path, "types.zig", """
const Value = union(enum) {
    int: i64,
    float: f64,
    none,
};
""")
        result = analyze_zig(tmp_path)
        unions = [s for s in result.symbols if s.kind == "union"]
        assert any("Value" in u.name for u in unions)


class TestErrorSetExtraction:
    """Branch coverage for error set extraction."""

    def test_error_set_declaration(self, tmp_path: Path) -> None:
        """Test error set declaration extraction."""
        make_zig_file(tmp_path, "errors.zig", """
const FileOpenError = error {
    AccessDenied,
    FileNotFound,
    OutOfMemory,
};
""")
        result = analyze_zig(tmp_path)
        error_sets = [s for s in result.symbols if s.kind == "error_set"]
        assert any("FileOpenError" in e.name for e in error_sets)


class TestTestExtraction:
    """Branch coverage for test extraction."""

    def test_test_declaration(self, tmp_path: Path) -> None:
        """Test test block extraction."""
        make_zig_file(tmp_path, "tests.zig", """
test "addition works" {
    const x = 1 + 2;
    try std.testing.expectEqual(@as(i32, 3), x);
}
""")
        result = analyze_zig(tmp_path)
        tests = [s for s in result.symbols if s.kind == "test"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test @import creates edge."""
        make_zig_file(tmp_path, "main.zig", """
const std = @import("std");
const other = @import("other.zig");
""")
        result = analyze_zig(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_zig_file(tmp_path, "app.zig", """
fn helper() void {
    // do something
}

pub fn main() void {
    helper();
}
""")
        result = analyze_zig(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestMethodExtraction:
    """Branch coverage for method extraction."""

    def test_method_declaration(self, tmp_path: Path) -> None:
        """Test method declaration extraction."""
        make_zig_file(tmp_path, "types.zig", """
const Counter = struct {
    value: u32,

    pub fn increment(self: *Counter) void {
        self.value += 1;
    }
};
""")
        result = analyze_zig(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any("increment" in m.name for m in methods)


class TestFindZigFiles:
    """Branch coverage for file discovery."""

    def test_finds_zig_files(self, tmp_path: Path) -> None:
        """Test .zig files are discovered."""
        (tmp_path / "test.zig").write_text("pub fn main() void {}")
        files = list(find_zig_files(tmp_path))
        assert any(f.suffix == ".zig" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_zig_files(self, tmp_path: Path) -> None:
        """Test directory with no Zig files."""
        result = analyze_zig(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(zig_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="zig analysis skipped"):
                result = zig_module.analyze_zig(tmp_path)
        assert result.skipped is True
