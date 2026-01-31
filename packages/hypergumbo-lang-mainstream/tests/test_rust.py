"""Tests for Rust analyzer."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindRustFiles:
    """Tests for Rust file discovery."""

    def test_finds_rust_files(self, tmp_path: Path) -> None:
        """Finds .rs files."""
        from hypergumbo_lang_mainstream.rust import find_rust_files

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
        from hypergumbo_lang_mainstream.rust import is_rust_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = object()  # Non-None = available
            assert is_rust_tree_sitter_available() is True

    def test_is_rust_tree_sitter_available_false(self) -> None:
        """Returns False when tree-sitter is not available."""
        from hypergumbo_lang_mainstream.rust import is_rust_tree_sitter_available

        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            assert is_rust_tree_sitter_available() is False

    def test_is_rust_tree_sitter_available_no_rust(self) -> None:
        """Returns False when tree-sitter is available but rust grammar is not."""
        from hypergumbo_lang_mainstream.rust import is_rust_tree_sitter_available

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
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "test.rs").write_text("fn test() {}")

        with patch("hypergumbo_lang_mainstream.rust.is_rust_tree_sitter_available", return_value=False):
            result = analyze_rust(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-rust" in result.skip_reason


class TestRustFunctionExtraction:
    """Tests for extracting Rust functions."""

    def test_extracts_function(self, tmp_path: Path) -> None:
        """Extracts Rust function declarations."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        assert result.run is not None
        assert result.run.files_analyzed == 1
        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "main" in func_names
        assert "helper" in func_names

    def test_extracts_pub_function(self, tmp_path: Path) -> None:
        """Extracts public function declarations."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("""
pub fn public_api() -> String {
    "hello".to_string()
}

fn private_helper() {}
""")

        result = analyze_rust(tmp_path)


        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = [s.name for s in funcs]
        assert "public_api" in func_names
        assert "private_helper" in func_names


class TestRustStructExtraction:
    """Tests for extracting Rust structs."""

    def test_extracts_struct(self, tmp_path: Path) -> None:
        """Extracts struct declarations."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        structs = [s for s in result.symbols if s.kind == "struct"]
        struct_names = [s.name for s in structs]
        assert "User" in struct_names
        assert "InternalData" in struct_names


class TestRustEnumExtraction:
    """Tests for extracting Rust enums."""

    def test_extracts_enum(self, tmp_path: Path) -> None:
        """Extracts enum declarations."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = [s.name for s in enums]
        assert "Status" in enum_names
        assert "Color" in enum_names


class TestRustImplExtraction:
    """Tests for extracting Rust impl blocks."""

    def test_extracts_impl_methods(self, tmp_path: Path) -> None:
        """Extracts methods from impl blocks."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Methods should be qualified with struct name
        assert any("new" in name for name in method_names)
        assert any("get_name" in name for name in method_names)


class TestRustTraitExtraction:
    """Tests for extracting Rust traits."""

    def test_extracts_trait(self, tmp_path: Path) -> None:
        """Extracts trait declarations."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        traits = [s for s in result.symbols if s.kind == "trait"]
        trait_names = [s.name for s in traits]
        assert "Displayable" in trait_names
        assert "Internal" in trait_names


class TestRustFunctionCalls:
    """Tests for detecting function calls in Rust."""

    def test_detects_function_call(self, tmp_path: Path) -> None:
        """Detects calls to functions in same file."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have edge from caller to helper
        assert len(call_edges) >= 1


class TestRustImports:
    """Tests for detecting Rust use statements."""

    def test_detects_use_statement(self, tmp_path: Path) -> None:
        """Detects use statements."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
use std::collections::HashMap;
use std::io::{self, Read};

fn main() {
    let map: HashMap<String, i32> = HashMap::new();
}
""")

        result = analyze_rust(tmp_path)


        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edges for use statements
        assert len(import_edges) >= 1

    def test_use_aliases_extracted(self, tmp_path: Path) -> None:
        """Use statement aliases are extracted for disambiguation."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_use_aliases,
            is_rust_tree_sitter_available,
        )

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
use std::collections::HashMap;
use crate::module::helper;

fn main() {
    let map = HashMap::new();
    helper();
}
""")

        source = rs_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_use_aliases(tree, source)

        # Check that aliases are extracted
        assert "HashMap" in aliases
        assert aliases["HashMap"] == "std::collections::HashMap"
        assert "helper" in aliases
        assert aliases["helper"] == "crate::module::helper"

    def test_extracts_simple_use(self, tmp_path: Path) -> None:
        """Extracts simple use statements without qualified path."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_use_aliases,
            is_rust_tree_sitter_available,
        )

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        rs_file = tmp_path / "simple.rs"
        rs_file.write_text("""
use helper;

fn main() {
    helper();
}
""")

        source = rs_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_use_aliases(tree, source)

        # Simple use should map name to itself
        assert "helper" in aliases
        assert aliases["helper"] == "helper"

    def test_extracts_use_as_alias(self, tmp_path: Path) -> None:
        """Extracts 'use foo::bar as baz;' aliased use statements."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_use_aliases,
            is_rust_tree_sitter_available,
        )

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        rs_file = tmp_path / "aliased.rs"
        rs_file.write_text("""
use std::collections::HashMap as Map;
use crate::module::helper as h;
use helper as simple_alias;

fn main() {
    let m: Map = Map::new();
    h();
    simple_alias();
}
""")

        source = rs_file.read_bytes()
        tree = parser.parse(source)

        aliases = _extract_use_aliases(tree, source)

        # Check aliased uses
        assert "Map" in aliases
        assert aliases["Map"] == "std::collections::HashMap"
        assert "h" in aliases
        assert aliases["h"] == "crate::module::helper"
        # Simple alias without :: scoping
        assert "simple_alias" in aliases
        assert aliases["simple_alias"] == "helper"


class TestRustEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parser_load_failure(self, tmp_path: Path) -> None:
        """Returns skipped with run when parser loading fails."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "test.rs").write_text("fn test() {}")

        with patch("hypergumbo_lang_mainstream.rust.is_rust_tree_sitter_available", return_value=True):
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
        from hypergumbo_lang_mainstream.rust import analyze_rust

        # Create a file with only comments
        (tmp_path / "empty.rs").write_text("// Just a comment\n\n")

        result = analyze_rust(tmp_path)


        assert result.run is not None
        assert result.run.files_skipped >= 1

    def test_cross_file_function_call(self, tmp_path: Path) -> None:
        """Detects function calls across files."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        # Verify both files analyzed
        assert result.run.files_analyzed >= 2


class TestRustCallPatterns:
    """Tests for various Rust call expression patterns."""

    def test_method_call_without_field(self, tmp_path: Path) -> None:
        """Handles method calls where field extraction fails gracefully."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        # Should not crash, edges may or may not be detected
        assert result.run is not None

    def test_edge_extraction_field_expr_no_field(self, tmp_path: Path) -> None:
        """Tests field_expression without field child (defensive branch)."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun, Symbol, Span

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

        import hypergumbo_lang_mainstream.rust as rust_module
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
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun, Symbol, Span

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

        import hypergumbo_lang_mainstream.rust as rust_module
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
        from hypergumbo_lang_mainstream.rust import analyze_rust

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


        # Should detect call to Foo::new
        assert result.run is not None
        # Verify we have method symbols
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) >= 1


class TestRustFileReadErrors:
    """Tests for file read error handling."""

    def test_symbol_extraction_handles_read_error(self, tmp_path: Path) -> None:
        """Symbol extraction handles file read errors gracefully."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_symbols_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun

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


class TestReexportResolution:
    """Tests for pub use re-export resolution."""

    def test_reexport_call_edges_resolved(self, tmp_path: Path) -> None:
        """Calls to re-exported symbols should create proper call edges.

        When lib.rs re-exports symbols from submodules:
            // src/utils/helper.rs
            pub fn helper() -> i32 { 42 }

            // src/lib.rs
            pub mod utils;
            pub use utils::helper::helper;

        And another module calls the re-exported function:
            // src/main.rs
            fn caller() { helper(); }

        The call edge from caller -> helper should be created.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        # Create project structure
        src = tmp_path / "src"
        src.mkdir()

        # Create utils module with helper function
        utils = src / "utils"
        utils.mkdir()
        helper_file = utils / "helper.rs"
        helper_file.write_text("pub fn helper() -> i32 { 42 }\n")

        utils_mod = utils / "mod.rs"
        utils_mod.write_text("pub mod helper;\n")

        # Create lib.rs that re-exports
        lib_file = src / "lib.rs"
        lib_file.write_text(
            "pub mod utils;\n"
            "pub use utils::helper::helper;\n"
        )

        # Create main.rs that calls helper
        main_file = src / "main.rs"
        main_file.write_text(
            "fn caller() {\n"
            "    helper();\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)


        # Should have both functions
        functions = [s for s in result.symbols if s.kind == "function"]
        func_names = {f.name for f in functions}
        assert "helper" in func_names, f"helper function should be detected, got {func_names}"
        assert "caller" in func_names, f"caller function should be detected, got {func_names}"

        # Find the actual helper symbol
        helper_syms = [f for f in functions if f.name == "helper"]
        assert len(helper_syms) >= 1
        helper_sym = helper_syms[0]

        # Find call edges from caller
        caller_syms = [f for f in functions if f.name == "caller"]
        assert len(caller_syms) == 1
        caller_id = caller_syms[0].id

        call_edges = [e for e in result.edges
                      if e.edge_type == "calls" and e.src == caller_id]

        # There should be a call edge to helper
        assert len(call_edges) >= 1, \
            f"Expected call edge from caller to helper, got: {call_edges}"

        # The call edge should point to the real helper
        helper_id = helper_sym.id
        call_dsts = {e.dst for e in call_edges}
        assert helper_id in call_dsts, \
            f"Call edge should point to real helper {helper_id}, got {call_dsts}"


class TestRustSignatureExtraction:
    """Tests for extracting function signatures from Rust code."""

    def test_extracts_simple_signature(self, tmp_path: Path) -> None:
        """Extracts signature with simple parameter types."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn add(x: i32, y: i32) -> i32 {
    x + y
}
""")

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x: i32, y: i32) -> i32"

    def test_extracts_signature_with_no_return(self, tmp_path: Path) -> None:
        """Extracts signature for function with no return type."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn print_hello(name: String) {
    println!("Hello, {}", name);
}
""")

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(name: String)"

    def test_extracts_signature_with_no_params(self, tmp_path: Path) -> None:
        """Extracts signature for function with no parameters."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn get_answer() -> i32 {
    42
}
""")

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].signature == "() -> i32"

    def test_extracts_signature_with_self(self, tmp_path: Path) -> None:
        """Extracts signature for method with &self."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct Counter {
    value: i32,
}

impl Counter {
    fn get(&self) -> i32 {
        self.value
    }

    fn set(&mut self, value: i32) {
        self.value = value;
    }
}
""")

        result = analyze_rust(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        sigs = {s.name.split("::")[-1]: s.signature for s in methods}

        assert sigs.get("get") == "(&self) -> i32"
        assert sigs.get("set") == "(&mut self, value: i32)"

    def test_extracts_signature_with_complex_types(self, tmp_path: Path) -> None:
        """Extracts signature with complex generic types."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn get_items() -> Vec<String> {
    vec![]
}
""")

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        sig = funcs[0].signature
        assert sig is not None
        assert sig == "() -> Vec<String>"

    def test_symbol_to_dict_includes_signature(self, tmp_path: Path) -> None:
        """Symbol.to_dict() includes the signature field."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}
""")

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1

        as_dict = funcs[0].to_dict()
        assert "signature" in as_dict
        assert as_dict["signature"] == "(name: &str) -> String"


# ============================================================================
# Annotation Extraction Tests (ADR-0003 v1.0.x - YAML Pattern Support)
# ============================================================================


class TestRustAnnotationExtraction:
    """Tests for extracting Rust attributes as annotations."""

    def test_extracts_function_annotations(self, tmp_path: Path) -> None:
        """Extracts attributes from functions."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
#[test]
fn test_something() {
    assert!(true);
}
''')

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1

        func = funcs[0]
        assert func.meta is not None
        assert "annotations" in func.meta
        annotations = func.meta["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["name"] == "test"
        assert annotations[0]["args"] == []

    def test_extracts_struct_annotations(self, tmp_path: Path) -> None:
        """Extracts derive attributes from structs."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
#[derive(Debug, Clone)]
struct User {
    name: String,
}
''')

        result = analyze_rust(tmp_path)

        structs = [s for s in result.symbols if s.kind == "struct"]
        assert len(structs) == 1

        struct = structs[0]
        assert struct.meta is not None
        assert "annotations" in struct.meta
        annotations = struct.meta["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["name"] == "derive"
        assert "Debug" in annotations[0]["args"]
        assert "Clone" in annotations[0]["args"]

    def test_extracts_actix_web_annotations(self, tmp_path: Path) -> None:
        """Extracts Actix-web route attributes for YAML pattern matching."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
#[get("/users")]
async fn list_users() -> String {
    "[]".to_string()
}

#[post("/users")]
async fn create_user() -> String {
    "created".to_string()
}
''')

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 2

        # Find list_users
        list_users = next((f for f in funcs if f.name == "list_users"), None)
        assert list_users is not None
        assert list_users.meta is not None
        annotations = list_users.meta.get("annotations", [])
        get_ann = next((a for a in annotations if a["name"] == "get"), None)
        assert get_ann is not None
        assert get_ann["args"] == ["/users"]

        # Find create_user
        create_user = next((f for f in funcs if f.name == "create_user"), None)
        assert create_user is not None
        assert create_user.meta is not None
        annotations = create_user.meta.get("annotations", [])
        post_ann = next((a for a in annotations if a["name"] == "post"), None)
        assert post_ann is not None
        assert post_ann["args"] == ["/users"]

    def test_extracts_qualified_annotations(self, tmp_path: Path) -> None:
        """Extracts fully qualified attribute names like actix_web::get."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
#[actix_web::get("/api/v1")]
async fn api_handler() -> String {
    "api".to_string()
}
''')

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1

        func = funcs[0]
        assert func.meta is not None
        annotations = func.meta.get("annotations", [])
        assert len(annotations) == 1
        assert annotations[0]["name"] == "actix_web::get"
        assert annotations[0]["args"] == ["/api/v1"]

    def test_extracts_named_annotation_args(self, tmp_path: Path) -> None:
        """Extracts named arguments from annotations like #[derive(Serialize)]."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
#[serde(rename = "user_name")]
fn get_name() -> String {
    "test".to_string()
}
''')

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1

        func = funcs[0]
        assert func.meta is not None
        annotations = func.meta.get("annotations", [])
        assert len(annotations) == 1
        assert annotations[0]["name"] == "serde"
        # Named argument should be in kwargs
        assert annotations[0]["kwargs"].get("rename") == "user_name"

    def test_function_without_annotations(self, tmp_path: Path) -> None:
        """Functions without annotations have no meta or empty annotations."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
fn plain_function() {
    println!("hello");
}
''')

        result = analyze_rust(tmp_path)

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        # No annotations means meta should be None
        assert funcs[0].meta is None


class TestAxumUsageContext:
    """Tests for Axum route UsageContext extraction."""

    def test_axum_simple_route(self, tmp_path: Path) -> None:
        """Detects simple Axum .route() calls."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
use axum::{routing::get, Router};

async fn list_users() -> String {
    "users".to_string()
}

pub fn routes() -> Router {
    Router::new().route("/users", get(list_users))
}
''')
        result = analyze_rust(tmp_path)

        # Should have usage contexts for the route
        assert len(result.usage_contexts) >= 1
        ctx = result.usage_contexts[0]
        assert ctx.kind == "call"
        assert "route" in ctx.context_name
        assert ctx.metadata["route_path"] == "/users"
        assert ctx.metadata["http_method"] == "GET"
        assert ctx.metadata["handler_name"] == "list_users"

    def test_axum_chained_handlers(self, tmp_path: Path) -> None:
        """Detects chained HTTP methods like get(h1).post(h2)."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
use axum::{routing::{get, post}, Router};

async fn list_users() -> String { "list".to_string() }
async fn create_user() -> String { "create".to_string() }

pub fn routes() -> Router {
    Router::new().route("/users", get(list_users).post(create_user))
}
''')
        result = analyze_rust(tmp_path)

        # Should have contexts for both GET and POST
        assert len(result.usage_contexts) >= 2
        methods = {ctx.metadata["http_method"] for ctx in result.usage_contexts}
        assert "GET" in methods
        assert "POST" in methods

    def test_axum_handler_resolution(self, tmp_path: Path) -> None:
        """Handler symbol references are resolved."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text('''
async fn my_handler() -> String { "ok".to_string() }

fn routes() {
    Router::new().route("/path", get(my_handler))
}
''')
        result = analyze_rust(tmp_path)

        # Find the handler function
        funcs = [s for s in result.symbols if s.name == "my_handler"]
        assert len(funcs) == 1
        handler_id = funcs[0].id

        # Check if usage context references the handler
        for ctx in result.usage_contexts:
            if ctx.metadata.get("handler_name") == "my_handler":
                assert ctx.symbol_ref == handler_id
                break


class TestRustClosureCallAttribution:
    """Tests for call edge attribution inside Rust closures.

    Rust uses closures heavily in iterators (map, filter, for_each). Calls inside
    these closures must be attributed to the enclosing function.
    """

    def test_call_inside_iterator_closure_attributed(self, tmp_path: Path) -> None:
        """Calls inside iterator closures are attributed to enclosing function.

        When you have:
            fn process() {
                items.iter().for_each(|item| helper(item));
            }

        The call to helper() should be attributed to process, not lost.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn helper(x: i32) {
    println!("{}", x);
}

fn process() {
    let items = vec![1, 2, 3];
    items.iter().for_each(|item| helper(*item));
}
""")

        result = analyze_rust(tmp_path)

        # Find symbols
        process_func = next(
            (s for s in result.symbols if s.name == "process"),
            None,
        )
        helper_func = next(
            (s for s in result.symbols if s.name == "helper"),
            None,
        )

        assert process_func is not None, "Should find process function"
        assert helper_func is not None, "Should find helper function"

        # The call to helper() inside the closure should be attributed to process
        call_edge = next(
            (
                e for e in result.edges
                if e.src == process_func.id
                and e.dst == helper_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call to helper() inside iterator closure should be attributed to process"

    def test_call_inside_map_closure_attributed(self, tmp_path: Path) -> None:
        """Calls inside map closures are attributed to enclosing function."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("""
fn transform(x: i32) -> i32 {
    x * 2
}

fn caller() {
    let items = vec![1, 2, 3];
    let _result: Vec<i32> = items.iter().map(|x| transform(*x)).collect();
}
""")

        result = analyze_rust(tmp_path)

        # Find symbols
        caller_func = next(
            (s for s in result.symbols if s.name == "caller"),
            None,
        )
        transform_func = next(
            (s for s in result.symbols if s.name == "transform"),
            None,
        )

        assert caller_func is not None
        assert transform_func is not None

        # The call to transform() inside the map closure should be attributed to caller
        call_edge = next(
            (
                e for e in result.edges
                if e.src == caller_func.id
                and e.dst == transform_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Call inside map closure should be attributed to caller"

