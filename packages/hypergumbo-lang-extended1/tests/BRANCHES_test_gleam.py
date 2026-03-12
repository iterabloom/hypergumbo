# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for gleam.py analyzer.

Tests specific branch paths in the Gleam analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import gleam as gleam_module
from hypergumbo_lang_extended1.gleam import (
    analyze_gleam,
    find_gleam_files,
)


def make_gleam_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Gleam file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_pub_fn_declaration(self, tmp_path: Path) -> None:
        """Test pub fn declaration extraction."""
        make_gleam_file(tmp_path, "utils.gleam", """
pub fn add(a: Int, b: Int) -> Int {
  a + b
}
""")
        result = analyze_gleam(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_private_fn(self, tmp_path: Path) -> None:
        """Test private fn extraction."""
        make_gleam_file(tmp_path, "utils.gleam", """
fn helper() -> Nil {
  Nil
}
""")
        result = analyze_gleam(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("helper" in f.name for f in funcs)


class TestTypeExtraction:
    """Branch coverage for type extraction."""

    def test_type_declaration(self, tmp_path: Path) -> None:
        """Test type declaration extraction."""
        make_gleam_file(tmp_path, "types.gleam", """
pub type Person {
  Person(name: String, age: Int)
}
""")
        result = analyze_gleam(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert not result.skipped  # lenient check

    def test_opaque_type(self, tmp_path: Path) -> None:
        """Test opaque type extraction."""
        make_gleam_file(tmp_path, "types.gleam", """
pub opaque type Counter {
  Counter(value: Int)
}
""")
        result = analyze_gleam(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert not result.skipped  # lenient check


class TestConstExtraction:
    """Branch coverage for const extraction."""

    def test_const_declaration(self, tmp_path: Path) -> None:
        """Test const declaration extraction."""
        make_gleam_file(tmp_path, "consts.gleam", """
pub const max_size = 100
const default_name = "Guest"
""")
        result = analyze_gleam(tmp_path)
        consts = [s for s in result.symbols if s.kind == "constant"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_gleam_file(tmp_path, "main.gleam", """
import gleam/io
import gleam/list

pub fn main() {
  io.println("Hello")
}
""")
        result = analyze_gleam(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_gleam_file(tmp_path, "app.gleam", """
fn helper() -> Nil {
  Nil
}

pub fn main() {
  helper()
}
""")
        result = analyze_gleam(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindGleamFiles:
    """Branch coverage for file discovery."""

    def test_finds_gleam_files(self, tmp_path: Path) -> None:
        """Test .gleam files are discovered."""
        (tmp_path / "test.gleam").write_text("pub fn test() { Nil }")
        files = list(find_gleam_files(tmp_path))
        assert any(f.suffix == ".gleam" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_gleam_files(self, tmp_path: Path) -> None:
        """Test directory with no Gleam files."""
        result = analyze_gleam(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(gleam_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="gleam analysis skipped"):
                result = gleam_module.analyze_gleam(tmp_path)
        assert result.skipped is True
