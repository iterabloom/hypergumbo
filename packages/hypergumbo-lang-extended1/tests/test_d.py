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

from hypergumbo_lang_extended1 import d_lang as d_module
from hypergumbo_lang_extended1.d_lang import (
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


class TestDCallResolution:
    """Tests for D call resolution."""

    def test_function_call_edge(self, temp_repo: Path) -> None:
        """Creates call edges for function calls."""
        (temp_repo / "math.d").write_text('''
module math;

int double_it(int x) {
    return x * 2;
}

int quadruple(int x) {
    return double_it(double_it(x));
}
''')

        result = analyze_d(temp_repo)

        # Should have call edges from quadruple to double_it
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1
        quad_calls = [e for e in call_edges if "quadruple" in e.src]
        assert len(quad_calls) >= 1
        assert any("double_it" in e.dst for e in quad_calls)

    def test_external_function_call(self, temp_repo: Path) -> None:
        """Creates call edges for external function calls with lower confidence."""
        (temp_repo / "io_test.d").write_text('''
module io_test;

void print_hello() {
    writeln("Hello");
}
''')

        result = analyze_d(temp_repo)

        # Should have call edge to external writeln
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        external_calls = [e for e in call_edges if "external" in e.dst]
        assert len(external_calls) >= 1
        assert any("writeln" in e.dst for e in external_calls)
        # External calls have lower confidence
        for e in external_calls:
            assert e.confidence == 0.70

    def test_resolved_call_confidence(self, temp_repo: Path) -> None:
        """Resolved calls have higher confidence than external calls."""
        (temp_repo / "test.d").write_text('''
module test;

void internal_func() {
}

void caller() {
    internal_func();
}
''')

        result = analyze_d(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Find the edge from caller to internal_func
        resolved_call = next((e for e in call_edges if "internal_func" in e.dst and "external" not in e.dst), None)
        assert resolved_call is not None
        # Resolved calls have confidence 0.85 * lookup confidence (usually 1.0)
        assert resolved_call.confidence > 0.70


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


class TestDMethodExtraction:
    """Tests for D method extraction from struct/class/interface bodies."""

    def test_struct_method_extracted_as_method(self, temp_repo: Path) -> None:
        """Functions inside structs should be extracted as methods with qualified names."""
        (temp_repo / "types.d").write_text('''
module types;

struct Searcher {
    void search() {}
    int count() { return 42; }
}

void standalone() {}
''')

        result = analyze_d(temp_repo)

        methods = {s.name: s for s in result.symbols if s.kind == "method"}
        assert "Searcher.search" in methods
        assert "Searcher.count" in methods

        # standalone should remain a function, not a method
        funcs = {s.name for s in result.symbols if s.kind == "function"}
        assert "standalone" in funcs

    def test_class_method_extracted_as_method(self, temp_repo: Path) -> None:
        """Functions inside classes should be extracted as methods with qualified names."""
        (temp_repo / "animal.d").write_text('''
module animal;

class Animal {
    void speak() {}
    void move(int x, int y) {}
}
''')

        result = analyze_d(temp_repo)

        methods = {s.name: s for s in result.symbols if s.kind == "method"}
        assert "Animal.speak" in methods
        assert "Animal.move" in methods

    def test_interface_method_extracted_as_method(self, temp_repo: Path) -> None:
        """Functions inside interfaces should be extracted as methods with qualified names."""
        (temp_repo / "drawable.d").write_text('''
module drawable;

interface Drawable {
    void draw();
}
''')

        result = analyze_d(temp_repo)

        methods = {s.name: s for s in result.symbols if s.kind == "method"}
        assert "Drawable.draw" in methods

    def test_method_call_edge_from_method(self, temp_repo: Path) -> None:
        """Call edges from methods should have method as caller."""
        (temp_repo / "worker.d").write_text('''
module worker;

void helper() {}

struct Worker {
    void doWork() {
        helper();
    }
}
''')

        result = analyze_d(temp_repo)

        # doWork should be a method
        methods = {s.name: s for s in result.symbols if s.kind == "method"}
        assert "Worker.doWork" in methods

        # Should have call edge from Worker.doWork to helper
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        method_calls = [e for e in call_edges if "Worker.doWork" in e.src]
        assert len(method_calls) >= 1


class TestDImportAliases:
    """Tests for import alias extraction and qualified call resolution."""

    def test_extracts_import_alias(self, temp_repo: Path) -> None:
        """Extracts import alias from 'import alias = module' statement."""
        from hypergumbo_lang_extended1.d_lang import _extract_import_aliases
        from tree_sitter_language_pack import get_parser

        parser = get_parser("d")

        d_file = temp_repo / "main.d"
        d_file.write_text("""
module main;

import math = std.math;
import io = std.stdio;

void main() {
    math.sin(3.14);
}
""")

        source = d_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_import_aliases(tree, source)

        # Both aliases should be extracted
        assert "math" in aliases
        assert aliases["math"] == "std.math"
        assert "io" in aliases
        assert aliases["io"] == "std.stdio"

    def test_qualified_call_uses_alias(self, temp_repo: Path) -> None:
        """Qualified call resolution uses import alias for path hint."""
        (temp_repo / "main.d").write_text("""
module main;

import math = std.math;

void calculate() {
    math.sin(3.14);
}
""")

        result = analyze_d(temp_repo)

        # Should have call edge (we can't verify path_hint directly but can verify it doesn't crash)
        assert not result.skipped
        symbols = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "calculate" for s in symbols)

        # Should have call edges from calculate
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        calc_calls = [e for e in call_edges if "calculate" in e.src]
        # Should have at least the sin call
        assert len(calc_calls) >= 1

    def test_bare_call_prefers_imported_module(self, temp_repo: Path) -> None:
        """Bare function call resolves to imported module, not same-named file.

        This is the DMD pattern: main.d imports dmd.errors and calls error(),
        but another file also defines error(). The call should resolve to the
        imported module's symbol, not an arbitrary same-named symbol.

        To force the wrong symbol into the registry, we name the conflicting
        file so it sorts alphabetically AFTER the production file — the last
        file processed wins in the global_symbol_registry dict.
        """
        # Production error() function — at root level, processed first by rglob
        (temp_repo / "errors.d").write_text("""
module errors;

void error(string msg) {
}

void fatal() {
}
""")

        # Main file imports errors module and calls error()
        (temp_repo / "main.d").write_text("""
module main;

import errors;

void tryMain() {
    error("something went wrong");
    fatal();
}
""")

        # Conflicting error() in a subdirectory — rglob processes subdirectories
        # AFTER root-level files, so this overwrites errors.d's "error" in the
        # global_symbol_registry. Without import-scope disambiguation, the call
        # will resolve to this wrong target.
        sub = temp_repo / "other"
        sub.mkdir()
        (sub / "unrelated.d").write_text("""
module unrelated;

void error(int code) {
}
""")

        result = analyze_d(temp_repo)

        # Find call edges from tryMain
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        trymain_calls = [e for e in call_edges if "tryMain" in e.src]

        # Should have at least 2 calls (error + fatal)
        assert len(trymain_calls) >= 2

        # The error() call should resolve to errors.d, NOT other/unrelated.d
        error_call = next(
            (e for e in trymain_calls if "error" in e.dst and "external" not in e.dst),
            None,
        )
        assert error_call is not None, "error() call should be resolved (not external)"
        assert "errors.d" in error_call.dst and "unrelated" not in error_call.dst, (
            f"error() should resolve to errors.d (imported module), got: {error_call.dst}"
        )
