"""Tests for D language analysis pass.

Tests verify that the D analyzer correctly extracts:
- Module declarations
- Import statements
- Function definitions
- Struct definitions
- Class definitions
- Interface definitions
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import d_lang as d_module
from hypergumbo.analyze.d_lang import (
    analyze_d,
    find_d_files,
    is_d_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindDFiles:
    """Tests for find_d_files function."""

    def test_finds_d_files(self, temp_repo: Path) -> None:
        """Finds .d files."""
        (temp_repo / "main.d").write_text("void main() {}")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_d_files(temp_repo))
        filenames = {f.name for f in files}

        assert "main.d" in filenames
        assert "README.md" not in filenames

    def test_finds_di_files(self, temp_repo: Path) -> None:
        """Finds .di (D interface) files."""
        (temp_repo / "module.di").write_text("module mod;")

        files = list(find_d_files(temp_repo))
        filenames = {f.name for f in files}

        assert "module.di" in filenames


class TestDTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_d_tree_sitter_available()
        assert isinstance(result, bool)


class TestDAnalysis:
    """Tests for D analysis with tree-sitter."""

    def test_analyzes_module(self, temp_repo: Path) -> None:
        """Detects module declarations."""
        (temp_repo / "mymodule.d").write_text('''
module mymodule;

void main() {}
''')

        result = analyze_d(temp_repo)

        assert not result.skipped
        mod_names = {s.name for s in result.symbols if s.kind == "module"}
        assert "mymodule" in mod_names

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function definitions."""
        (temp_repo / "funcs.d").write_text('''
module funcs;

int add(int a, int b) {
    return a + b;
}

void print_hello() {
    writeln("Hello");
}
''')

        result = analyze_d(temp_repo)

        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "add" in func_names
        assert "print_hello" in func_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters."""
        (temp_repo / "sig.d").write_text('''
module sig;

int compute(int x, int y, float scale) {
    return cast(int)(x + y * scale);
}
''')

        result = analyze_d(temp_repo)

        func = next(s for s in result.symbols if s.name == "compute")
        assert func.signature is not None
        assert "x" in func.signature or "int" in func.signature

    def test_analyzes_struct(self, temp_repo: Path) -> None:
        """Detects struct definitions."""
        (temp_repo / "types.d").write_text('''
module types;

struct Point {
    int x, y;
}

struct Rectangle {
    Point topLeft;
    Point bottomRight;
}
''')

        result = analyze_d(temp_repo)

        struct_names = {s.name for s in result.symbols if s.kind == "struct"}
        assert "Point" in struct_names
        assert "Rectangle" in struct_names

    def test_analyzes_class(self, temp_repo: Path) -> None:
        """Detects class definitions."""
        (temp_repo / "classes.d").write_text('''
module classes;

class Animal {
    void speak() {}
}

class Dog : Animal {
    override void speak() {}
}
''')

        result = analyze_d(temp_repo)

        class_names = {s.name for s in result.symbols if s.kind == "class"}
        assert "Animal" in class_names
        assert "Dog" in class_names

    def test_analyzes_interface(self, temp_repo: Path) -> None:
        """Detects interface definitions."""
        (temp_repo / "interfaces.d").write_text('''
module interfaces;

interface Drawable {
    void draw();
}

interface Movable {
    void move(int x, int y);
}
''')

        result = analyze_d(temp_repo)

        iface_names = {s.name for s in result.symbols if s.kind == "interface"}
        assert "Drawable" in iface_names
        assert "Movable" in iface_names

    def test_analyzes_import(self, temp_repo: Path) -> None:
        """Detects import statements as edges."""
        (temp_repo / "main.d").write_text('''
module main;

import std.stdio;
import std.string : format;

void main() {}
''')

        result = analyze_d(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        imported = {e.dst for e in import_edges}
        assert any("std.stdio" in dst for dst in imported)
        assert any("std.string" in dst for dst in imported)


class TestDAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "test.d").write_text("module test;")

        with patch.object(d_module, "is_d_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="D analysis skipped"):
                result = d_module.analyze_d(temp_repo)

        assert result.skipped is True


class TestDAnalysisRun:
    """Tests for D analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "test.d").write_text('''
module test;

void hello() {}
''')

        result = analyze_d(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "d-v1"
        assert result.run.files_analyzed >= 1
