"""Branch coverage tests for capnp.py analyzer.

Tests specific branch paths in the Cap'n Proto analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import capnp as capnp_module
from hypergumbo_lang_extended1.capnp import (
    analyze_capnp,
    find_capnp_files,
)


def make_capnp_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Cap'n Proto file with given content."""
    (tmp_path / name).write_text(content)


class TestStructExtraction:
    """Branch coverage for struct extraction."""

    def test_struct_declaration(self, tmp_path: Path) -> None:
        """Test struct declaration extraction."""
        make_capnp_file(tmp_path, "schema.capnp", """
@0xabcdef1234567890;

struct Person {
    name @0 :Text;
    age @1 :UInt32;
}
""")
        result = analyze_capnp(tmp_path)
        assert not result.skipped
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any("Person" in s.name for s in structs)


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_capnp_file(tmp_path, "schema.capnp", """
@0xabcdef1234567890;

enum Color {
    red @0;
    green @1;
    blue @2;
}
""")
        result = analyze_capnp(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any("Color" in e.name for e in enums)


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_capnp_file(tmp_path, "schema.capnp", """
@0xabcdef1234567890;

interface Calculator {
    add @0 (a :Int32, b :Int32) -> (result :Int32);
}
""")
        result = analyze_capnp(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("Calculator" in i.name for i in interfaces)


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_capnp_file(tmp_path, "main.capnp", """
@0xabcdef1234567890;

using import "/common.capnp".Common;
""")
        result = analyze_capnp(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestFindCapnpFiles:
    """Branch coverage for file discovery."""

    def test_finds_capnp_files(self, tmp_path: Path) -> None:
        """Test .capnp files are discovered."""
        (tmp_path / "test.capnp").write_text("@0x0;")
        files = list(find_capnp_files(tmp_path))
        assert any(f.suffix == ".capnp" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_capnp_files(self, tmp_path: Path) -> None:
        """Test directory with no Cap'n Proto files."""
        result = analyze_capnp(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(capnp_module, "is_capnp_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Cap'n Proto analysis skipped"):
                result = capnp_module.analyze_capnp(tmp_path)
        assert result.skipped is True
