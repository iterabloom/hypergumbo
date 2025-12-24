"""Tests for Rust analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindRustFiles:
    """Tests for Rust file discovery."""

    def test_finds_rust_files(self, tmp_path: Path) -> None:
        """Finds .rs files."""
        from hypergumbo.analyze.rust import find_rust_files

        (tmp_path / "main.rs").write_text("fn main() {}")
        (tmp_path / "lib.rs").write_text("pub mod utils;")
        (tmp_path / "other.txt").write_text("not rust")

        files = list(find_rust_files(tmp_path))

        assert len(files) == 2
        assert all(f.suffix == ".rs" for f in files)


class TestRustTreeSitterAvailability:
    """Tests for tree-sitter-rust availability checking."""

    def test_is_rust_tree_sitter_available_true(self) -> None:
        """Returns True when tree-sitter-rust is available."""
        from hypergumbo.analyze.rust import is_rust_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_rust_tree_sitter_available() is True

    def test_is_rust_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo.analyze.rust import is_rust_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_rust_tree_sitter_available() is False

    def test_is_rust_tree_sitter_available_no_rust(self) -> None:
        """Returns False when tree-sitter is available but rust grammar is not."""
        from hypergumbo.analyze.rust import is_rust_tree_sitter_available

        def mock_find_spec(name: str) -> object | None:
            if name == "tree_sitter":
                return object()  # tree-sitter available
            return None  # rust grammar not available

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_rust_tree_sitter_available() is False


class TestAnalyzeRustFallback:
    """Tests for fallback behavior when tree-sitter-rust unavailable."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter-rust unavailable."""
        from hypergumbo.analyze.rust import analyze_rust

        (tmp_path / "test.rs").write_text("fn test() {}")

        with patch("hypergumbo.analyze.rust.is_rust_tree_sitter_available", return_value=False):
            result = analyze_rust(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-rust" in result.skip_reason


class TestRustFunctionExtraction:
    """Tests for extracting Rust functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Rust function declarations."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn main() {
    println!("Hello, world!");
}

fn helper(x: i32) -> i32 {
    x + 1
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        assert result.run is not None
        assert result.run.files_analyzed == 1
        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "main" in func_names
        assert "helper" in func_names

    def test_extracts_pub_function(self, tmp_path: Path) -> None:
        """Extracts public function declarations."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("""
pub fn public_api() -> String {
    "hello".to_string()
}

fn private_helper() {}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "public_api" in func_names
        assert "private_helper" in func_names


class TestRustStructExtraction:
    """Tests for extracting Rust structs."""

    def test_extracts_struct(self, tmp_path: Path) -> None:
        """Extracts struct declarations."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "models.rs"
        rs_file.write_text("""
pub struct User {
    name: String,
    age: u32,
}

struct InternalData {
    value: i64,
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        structs = [s for s in result.symbols if s.kind == "struct"]
        struct_names = [s.name for s in structs]
        assert "User" in struct_names
        assert "InternalData" in struct_names


class TestRustEnumExtraction:
    """Tests for extracting Rust enums."""

    def test_extracts_enum(self, tmp_path: Path) -> None:
        """Extracts enum declarations."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "types.rs"
        rs_file.write_text("""
pub enum Status {
    Active,
    Inactive,
    Pending,
}

enum Color {
    Red,
    Green,
    Blue,
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = [s.name for s in enums]
        assert "Status" in enum_names
        assert "Color" in enum_names


class TestRustImplExtraction:
    """Tests for extracting Rust impl blocks."""

    def test_extracts_impl_methods(self, tmp_path: Path) -> None:
        """Extracts methods from impl blocks."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "user.rs"
        rs_file.write_text("""
struct User {
    name: String,
}

impl User {
    pub fn new(name: String) -> Self {
        Self { name }
    }

    pub fn get_name(&self) -> &str {
        &self.name
    }
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Methods should be qualified with struct name
        assert any("new" in name for name in method_names)
        assert any("get_name" in name for name in method_names)


class TestRustTraitExtraction:
    """Tests for extracting Rust traits."""

    def test_extracts_trait(self, tmp_path: Path) -> None:
        """Extracts trait declarations."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "traits.rs"
        rs_file.write_text("""
pub trait Displayable {
    fn display(&self) -> String;
    fn debug(&self) -> String {
        format!("{:?}", self)
    }
}

trait Internal {
    fn process(&self);
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        traits = [s for s in result.symbols if s.kind == "trait"]
        trait_names = [s.name for s in traits]
        assert "Displayable" in trait_names
        assert "Internal" in trait_names


class TestRustFunctionCalls:
    """Tests for detecting function calls in Rust."""

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same file."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "utils.rs"
        rs_file.write_text("""
fn caller() {
    helper();
}

fn helper() {
    println!("helping");
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge from caller to helper
        assert len(call_edges) >= 1


class TestRustImports:
    """Tests for detecting Rust use statements."""

    def test_detects_use_statement(self, tmp_path: Path) -> None:
        """Detects use statements."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
use std::collections::HashMap;
use std::io::{self, Read};

fn main() {
    let map: HashMap<String, i32> = HashMap::new();
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edges for use statements
        assert len(import_edges) >= 1


class TestRustEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo.analyze.rust import analyze_rust

        (tmp_path / "test.rs").write_text("fn test() {}")

        with patch("hypergumbo.analyze.rust.is_rust_tree_sitter_available", return_value=True):
            with patch.dict("sys.modules", {"tree_sitter_rust": MagicMock()}):
                import sys
                mock_module = sys.modules["tree_sitter_rust"]
                mock_module.language.side_effect = RuntimeError("Parser load failed")
                result = analyze_rust(tmp_path)

        assert result.skipped is True
        assert "Failed to load Rust parser" in result.skip_reason
        assert result.run is not None

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo.analyze.rust import analyze_rust

        # Create a file with only comments
        (tmp_path / "empty.rs").write_text("// Just a comment\n\n")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        assert result.run is not None
        assert result.run.files_skipped >= 1

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo.analyze.rust import analyze_rust

        # File 1: defines helper
        (tmp_path / "helper.rs").write_text("""
pub fn greet(name: &str) -> String {
    format!("Hello, {}", name)
}
""")

        # File 2: calls helper
        (tmp_path / "main.rs").write_text("""
mod helper;

fn run() {
    greet("world");
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        # Verify both files analyzed
        assert result.run.files_analyzed >= 2


class TestRustCallPatterns:
    """Tests for various Rust call expression patterns."""

    def test_method_call_without_field(self, tmp_path: Path) -> None:
        """Handles method calls where field extraction fails gracefully."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "calls.rs"
        # Create code with various call patterns
        rs_file.write_text("""
fn caller() {
    // Method call
    foo.bar();
    // Qualified call
    Foo::bar();
    // Other expression call
    (get_fn())();
}

fn bar() {}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        # Should not crash, edges may or may not be detected
        assert result.run is not None

    def test_edge_extraction_field_expr_no_field(self, tmp_path: Path) -> None:
        """Tests field_expression without field child (defensive branch)."""
        from hypergumbo.analyze.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun, Symbol, Span

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        # Create a function with a method call
        rs_file = tmp_path / "test.rs"
        rs_file.write_text("""
fn caller() {
    foo.bar();
}
""")

        caller_symbol = Symbol(
            id="test:caller",
            name="caller",
            kind="function",
            language="rust",
            path=str(rs_file),
            span=Span(start_line=2, end_line=4, start_col=0, end_col=1),
            origin="test",
            origin_run_id=run.execution_id,
        )

        # Mock _find_child_by_field to return None for "field" lookups
        original_func = None

        def mock_find_child_by_field(node, field_name):
            if field_name == "field":
                return None  # Trigger the defensive branch
            return node.child_by_field_name(field_name)

        local_symbols = {"caller": caller_symbol}

        import hypergumbo.analyze.rust as rust_module
        original_func = rust_module._find_child_by_field
        rust_module._find_child_by_field = mock_find_child_by_field
        try:
            result = _extract_edges_from_file(rs_file, parser, local_symbols, {}, run)
        finally:
            rust_module._find_child_by_field = original_func

        # Should not crash
        assert isinstance(result, list)

    def test_edge_extraction_scoped_without_name(self, tmp_path: Path) -> None:
        """Tests scoped_identifier fallback branch (defensive branch)."""
        from hypergumbo.analyze.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun, Symbol, Span

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        # Create code with scoped identifier call
        rs_file = tmp_path / "test.rs"
        rs_file.write_text("""
fn caller() {
    Foo::bar();
}
""")

        caller_symbol = Symbol(
            id="test:caller",
            name="caller",
            kind="function",
            language="rust",
            path=str(rs_file),
            span=Span(start_line=2, end_line=4, start_col=0, end_col=1),
            origin="test",
            origin_run_id=run.execution_id,
        )

        # Mock _find_child_by_field to return None for "name" on scoped_identifier
        def mock_find_child_by_field(node, field_name):
            # Only mock when looking for "name" on a scoped_identifier node
            if field_name == "name" and node.type == "scoped_identifier":
                return None  # Trigger the defensive branch
            return node.child_by_field_name(field_name)

        local_symbols = {"caller": caller_symbol}

        import hypergumbo.analyze.rust as rust_module
        original_func = rust_module._find_child_by_field
        rust_module._find_child_by_field = mock_find_child_by_field
        try:
            result = _extract_edges_from_file(rs_file, parser, local_symbols, {}, run)
        finally:
            rust_module._find_child_by_field = original_func

        # Should not crash
        assert isinstance(result, list)

    def test_scoped_identifier_call(self, tmp_path: Path) -> None:
        """Detects calls using scoped identifiers."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "scoped.rs"
        rs_file.write_text("""
struct Foo;

impl Foo {
    fn new() -> Self {
        Foo
    }
}

fn main() {
    let f = Foo::new();
}
""")

        result = analyze_rust(tmp_path)

        if result.skipped:
            pytest.skip("tree-sitter-rust not available")

        # Should detect call to Foo::new
        assert result.run is not None
        # Verify we have method symbols
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) >= 1


class TestRustFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo.analyze.rust import (
            _extract_symbols_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        rs_file = tmp_path / "test.rs"
        rs_file.write_text("fn test() {}")

        with patch.object(Path, "read_bytes", side_effect=OSError("Read failed")):
            result = _extract_symbols_from_file(rs_file, parser, run)

        assert result.symbols == []

    def test_edge_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Edge extraction handles file read errors gracefully."""
        from hypergumbo.analyze.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo.ir import AnalysisRun

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
        run = AnalysisRun.create(pass_id="test", version="test")

        rs_file = tmp_path / "test.rs"
        rs_file.write_text("fn test() {}")

        with patch.object(Path, "read_bytes", side_effect=IOError("Read failed")):
            result = _extract_edges_from_file(rs_file, parser, {}, {}, run)

        assert result == []
