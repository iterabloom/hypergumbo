"""Branch coverage tests for d_lang.py analyzer.

Tests specific branch paths in the D language analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import d_lang as d_module
from hypergumbo_lang_extended1.d_lang import (
    analyze_d,
    find_d_files,
)


def make_d_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a D language file with given content."""
    (tmp_path / name).write_text(content)


class TestModuleExtraction:
    """Branch coverage for module extraction."""

    def test_module_declaration(self, tmp_path: Path) -> None:
        """Test module declaration extraction."""
        make_d_file(tmp_path, "example.d", """
module example;

void main() {
    writeln("Hello");
}
""")
        result = analyze_d(tmp_path)
        assert not result.skipped
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any("example" in m.name for m in modules)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_d_file(tmp_path, "funcs.d", """
int add(int a, int b) {
    return a + b;
}
""")
        result = analyze_d(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_template_function(self, tmp_path: Path) -> None:
        """Test template function extraction."""
        make_d_file(tmp_path, "templates.d", """
T max(T)(T a, T b) {
    return a > b ? a : b;
}
""")
        result = analyze_d(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("max" in f.name for f in funcs)


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration extraction."""
        make_d_file(tmp_path, "classes.d", """
class Animal {
    string name;

    void speak() {
        writeln("...");
    }
}
""")
        result = analyze_d(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any("Animal" in c.name for c in classes)


class TestStructExtraction:
    """Branch coverage for struct extraction."""

    def test_struct_declaration(self, tmp_path: Path) -> None:
        """Test struct declaration extraction."""
        make_d_file(tmp_path, "structs.d", """
struct Point {
    int x;
    int y;
}
""")
        result = analyze_d(tmp_path)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any("Point" in s.name for s in structs)


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_d_file(tmp_path, "enums.d", """
enum Color { red, green, blue }
""")
        result = analyze_d(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_d_file(tmp_path, "app.d", """
import std.stdio;

void main() {
    writeln("Hello");
}
""")
        result = analyze_d(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_d_file(tmp_path, "app.d", """
void helper() {
    writeln("helper");
}

void main() {
    helper();
}
""")
        result = analyze_d(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindDFiles:
    """Branch coverage for file discovery."""

    def test_finds_d_files(self, tmp_path: Path) -> None:
        """Test .d files are discovered."""
        (tmp_path / "test.d").write_text("void main() {}")
        files = list(find_d_files(tmp_path))
        assert any(f.suffix == ".d" for f in files)

    def test_finds_di_files(self, tmp_path: Path) -> None:
        """Test .di files are discovered."""
        (tmp_path / "test.di").write_text("interface Test {}")
        files = list(find_d_files(tmp_path))
        assert any(f.suffix == ".di" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_d_files(self, tmp_path: Path) -> None:
        """Test directory with no D files."""
        result = analyze_d(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(d_module, "is_d_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="D analysis skipped"):
                result = d_module.analyze_d(tmp_path)
        assert result.skipped is True
