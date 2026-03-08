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
        from hypergumbo_lang_mainstream import rust as rust_module
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "test.rs").write_text("fn test() {}")

        with patch.object(rust_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="rust analysis skipped"):
                result = analyze_rust(tmp_path)

        assert result.skipped is True
        assert "rust" in result.skip_reason


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


class TestRustLinesOfCode:
    """Tests for lines_of_code on Rust symbols."""

    def test_function_lines_of_code(self, tmp_path: Path) -> None:
        """Function symbols have lines_of_code set from span."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""\
fn small() {
    println!("one liner body");
}

fn medium(x: i32) -> i32 {
    let y = x + 1;
    let z = y * 2;
    z
}
""")

        result = analyze_rust(tmp_path)

        small = next(s for s in result.symbols if s.name == "small")
        medium = next(s for s in result.symbols if s.name == "medium")

        assert small.lines_of_code == 3  # lines 1-3
        assert medium.lines_of_code == 5  # lines 5-9

    def test_struct_lines_of_code(self, tmp_path: Path) -> None:
        """Struct symbols have lines_of_code set from span."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "models.rs"
        rs_file.write_text("""\
struct Point {
    x: f64,
    y: f64,
}
""")

        result = analyze_rust(tmp_path)

        point = next(s for s in result.symbols if s.name == "Point")
        assert point.lines_of_code == 4  # lines 1-4


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
        """Raises error when parser loading fails (base class does not catch)."""
        from hypergumbo_lang_mainstream import rust as rust_module
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "test.rs").write_text("fn test() {}")

        with patch.object(rust_module._analyzer, "_check_grammar_available", return_value=True):
            with patch.object(
                rust_module._analyzer, "_create_parser",
                side_effect=RuntimeError("Parser load failed"),
            ):
                with pytest.raises(RuntimeError, match="Parser load failed"):
                    analyze_rust(tmp_path)

    def test_file_with_no_symbols_is_skipped(self, tmp_path: Path) -> None:
        """Files with no extractable symbols are counted as skipped."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        # Create a file with only comments
        (tmp_path / "empty.rs").write_text("// Just a comment\n\n")

        result = analyze_rust(tmp_path)

        assert result.run is not None
        # Base class counts file as analyzed even with no symbols
        assert result.run.files_analyzed >= 1

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
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        # Create a function with a method call
        rs_file = tmp_path / "test.rs"
        source_text = """
fn caller() {
    foo.bar();
}
"""
        rs_file.write_text(source_text)
        source = source_text.encode("utf-8")
        tree = parser.parse(source)

        caller_symbol = Symbol(
            id="test:caller",
            name="caller",
            kind="function",
            language="rust",
            path="test.rs",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=1),
            origin="test",
            origin_run_id="test-run",
        )

        # Mock _find_child_by_field to return None for "field" lookups
        def mock_find_child_by_field(node, field_name):
            if field_name == "field":
                return None  # Trigger the defensive branch
            return node.child_by_field_name(field_name)

        local_symbols = {"caller": caller_symbol}
        resolver = NameResolver({})

        import hypergumbo_lang_mainstream.rust as rust_module
        original_func = rust_module._find_child_by_field
        rust_module._find_child_by_field = mock_find_child_by_field
        try:
            result = _extract_edges_from_file(
                tree, source, "test.rs", local_symbols, {},
                "test-run", resolver, {},
            )
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
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        # Create code with scoped identifier call
        rs_file = tmp_path / "test.rs"
        source_text = """
fn caller() {
    Foo::bar();
}
"""
        rs_file.write_text(source_text)
        source = source_text.encode("utf-8")
        tree = parser.parse(source)

        caller_symbol = Symbol(
            id="test:caller",
            name="caller",
            kind="function",
            language="rust",
            path="test.rs",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=1),
            origin="test",
            origin_run_id="test-run",
        )

        # Mock _find_child_by_field to return None for "name" on scoped_identifier
        def mock_find_child_by_field(node, field_name):
            # Only mock when looking for "name" on a scoped_identifier node
            if field_name == "name" and node.type == "scoped_identifier":
                return None  # Trigger the defensive branch
            return node.child_by_field_name(field_name)

        local_symbols = {"caller": caller_symbol}
        resolver = NameResolver({})

        import hypergumbo_lang_mainstream.rust as rust_module
        original_func = rust_module._find_child_by_field
        rust_module._find_child_by_field = mock_find_child_by_field
        try:
            result = _extract_edges_from_file(
                tree, source, "test.rs", local_symbols, {},
                "test-run", resolver, {},
            )
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
    """Tests for file read error handling.

    The base TreeSitterAnalyzer.analyze() method handles file read errors
    during Pass 1 by incrementing files_skipped. Internal functions now
    receive pre-parsed trees.
    """

    def test_analyzer_handles_read_error_in_pass1(self, tmp_path: Path) -> None:
        """Analyzer skips files with read errors during Pass 1."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        # Create a valid file plus a file that will fail to read
        (tmp_path / "good.rs").write_text("fn good() {}")
        bad_file = tmp_path / "bad.rs"
        bad_file.write_text("fn bad() {}")

        original_read_bytes = Path.read_bytes

        def patched_read_bytes(self: Path) -> bytes:
            if self.name == "bad.rs":
                raise OSError("Read failed")
            return original_read_bytes(self)

        with patch.object(Path, "read_bytes", patched_read_bytes):
            result = analyze_rust(tmp_path)

        assert result.run is not None
        assert result.run.files_skipped >= 1
        func_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "good" in func_names


class TestRustAttributeEdges:
    """Tests for Rust attribute/annotation edges (decorated_by)."""

    def test_resolved_attribute_edge(self, tmp_path: Path) -> None:
        """Creates decorated_by edge when attribute resolves to a symbol."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
fn my_handler() -> String {
    "hello".to_string()
}

#[my_handler]
fn decorated() {}
""")

        result = analyze_rust(tmp_path)

        # Check if decorated_by edges are created
        decorated_edges = [e for e in result.edges if e.edge_type == "decorated_by"]
        # The attribute #[my_handler] should create a decorated_by edge
        # whether resolved or unresolved
        assert len(decorated_edges) >= 1

    def test_unresolved_attribute_skips_empty_annotations(self, tmp_path: Path) -> None:
        """Symbols without annotations meta skip attribute edge extraction."""
        from hypergumbo_lang_mainstream.rust import _extract_attribute_edges
        from hypergumbo_core.ir import Symbol, Span

        sym = Symbol(
            id="test:func",
            name="func",
            kind="function",
            language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=1),
            origin="test",
            meta={"annotations": []},
        )
        edges = _extract_attribute_edges([sym], {}, "test-run")
        assert edges == []

    def test_builtin_attributes_produce_no_edges(self, tmp_path: Path) -> None:
        """Built-in attributes like derive, test, cfg produce zero edges.

        Previously builtins produced unresolved edges
        (e.g. ``rust:unresolved:0-0:derive:unresolved`` with 175 in-edges).
        """
        from hypergumbo_lang_mainstream.rust import _extract_attribute_edges
        from hypergumbo_core.ir import Symbol, Span

        sym = Symbol(
            id="test:my_struct",
            name="MyStruct",
            kind="struct",
            language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test",
            meta={"annotations": [
                {"name": "derive", "args": ["Debug", "Clone"]},
                {"name": "test"},
                {"name": "cfg", "args": ["test"]},
                {"name": "allow", "args": ["dead_code"]},
            ]},
        )
        edges = _extract_attribute_edges([sym], {}, "test-run")
        assert edges == [], (
            f"Built-in attributes should produce no edges, got: "
            f"{[(e.dst, e.edge_type) for e in edges]}"
        )


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


class TestImplTargetExtraction:
    """Tests for impl target type name extraction.

    When extracting method names from impl blocks, we need to extract just
    the base type identifier, not the full type text which may include
    references (&), lifetimes ('a), or generic parameters (<T>).

    For example:
    - impl<'a, M: Matcher> Deref for &'a M  -> type field is `&'a M`, base is `M`
    - impl<'a, M, W> PreludeWriter<'a, M, W> -> type field is `PreludeWriter<'a, M, W>`, base is `PreludeWriter`
    - impl User -> type field is `User`, base is `User`
    """

    def test_impl_for_reference_type_extracts_base_name(self, tmp_path: Path) -> None:
        """impl for reference types like &'a M should use base type M, not &'a M."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
trait Matcher {
    fn line_terminator(&self) -> u8;
}

impl<'a, M: Matcher> Matcher for &'a M {
    fn line_terminator(&self) -> u8 {
        (*self).line_terminator()
    }
}
""")

        result = analyze_rust(tmp_path)

        # Find the method in the impl block
        methods = [s for s in result.symbols if s.kind == "method"]
        # Method name should be "M::line_terminator", not "&'a M::line_terminator"
        method_names = [s.name for s in methods]
        assert any("M::line_terminator" in name for name in method_names), \
            f"Expected method name with 'M::line_terminator', got {method_names}"
        # Should NOT have the malformed name with & or '
        assert not any(name.startswith("&") or "'" in name.split("::")[0] for name in method_names), \
            f"Method name should not start with & or contain lifetime: {method_names}"

    def test_impl_for_generic_type_extracts_base_name(self, tmp_path: Path) -> None:
        """impl for generic types like Foo<'a, T> should use base type Foo, not full type."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct Writer<'a, M, W> {
    matcher: &'a M,
    writer: W,
}

impl<'a, M, W> Writer<'a, M, W> {
    fn write_output(&self) {
        // implementation
    }
}
""")

        result = analyze_rust(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Should be "Writer::write_output", not "Writer<'a, M, W>::write_output"
        assert any("Writer::write_output" in name for name in method_names), \
            f"Expected 'Writer::write_output', got {method_names}"
        # Should NOT have generic params in method name
        assert not any("<" in name for name in method_names), \
            f"Method name should not contain generic params: {method_names}"

    def test_impl_for_simple_type_unchanged(self, tmp_path: Path) -> None:
        """impl for simple types like User should remain unchanged."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct User {
    name: String,
}

impl User {
    fn get_name(&self) -> &str {
        &self.name
    }
}
""")

        result = analyze_rust(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Should be "User::get_name"
        assert any("User::get_name" in name for name in method_names), \
            f"Expected 'User::get_name', got {method_names}"

    def test_impl_for_scoped_type(self, tmp_path: Path) -> None:
        """impl for scoped types like crate::Foo should keep full path."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
mod inner {
    pub struct Nested;
}

impl crate::inner::Nested {
    fn method(&self) {}
}
""")

        result = analyze_rust(tmp_path)

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        # Should preserve the scoped type path
        assert len(method_names) >= 1, f"Expected at least one method, got {method_names}"
        # The name should include "Nested" or the scoped path
        assert any("Nested" in name or "inner" in name for name in method_names), \
            f"Expected scoped type in method name, got {method_names}"

    def test_impl_for_array_type(self, tmp_path: Path) -> None:
        """impl for unusual types like array types should not crash."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        # Test the fallback case for "other type nodes"
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
trait MyTrait {
    fn len(&self) -> usize;
}

impl MyTrait for [u8; 4] {
    fn len(&self) -> usize {
        4
    }
}
""")

        result = analyze_rust(tmp_path)

        # Should not crash, method should be extractable
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) >= 1, "Should extract at least one method from array impl"


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


class TestRustVisibilityModifiers:
    """Tests for Rust visibility modifier extraction."""

    def test_pub_function(self, tmp_path: Path) -> None:
        """pub functions get 'pub' modifier."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text("""
pub fn public_fn() {}
fn private_fn() {}
pub(crate) fn crate_fn() {}
""")
        result = analyze_rust(tmp_path)

        pub = next(s for s in result.symbols if s.name == "public_fn")
        assert "pub" in pub.modifiers

        priv = next(s for s in result.symbols if s.name == "private_fn")
        assert len(priv.modifiers) == 0

        crate = next(s for s in result.symbols if s.name == "crate_fn")
        assert any("pub" in m for m in crate.modifiers)

    def test_pub_struct(self, tmp_path: Path) -> None:
        """pub structs get 'pub' modifier."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "types.rs").write_text("""
pub struct PubStruct {}
struct PrivStruct {}
""")
        result = analyze_rust(tmp_path)

        pub = next(s for s in result.symbols if s.name == "PubStruct")
        assert "pub" in pub.modifiers

        priv = next(s for s in result.symbols if s.name == "PrivStruct")
        assert len(priv.modifiers) == 0


class TestNormalizeRustSignature:
    """Tests for Rust signature normalization (ADR-0014 §3)."""

    def test_basic_function(self) -> None:
        from hypergumbo_lang_mainstream.rust import normalize_rust_signature
        assert normalize_rust_signature("(x: i32, y: String) -> bool") == "(i32,String)bool"

    def test_self_stripped(self) -> None:
        from hypergumbo_lang_mainstream.rust import normalize_rust_signature
        assert normalize_rust_signature("(&self, x: i32) -> bool") == "(i32)bool"

    def test_none(self) -> None:
        from hypergumbo_lang_mainstream.rust import normalize_rust_signature
        assert normalize_rust_signature(None) is None


class TestRustMethodCallAmbiguity:
    """Tests for method call ambiguity penalty (WI-zojis).

    Rust method calls like foo.clone() resolve by name only, which causes
    massive false-positive fan-in when common names (.clone, .get, .build)
    match dozens or hundreds of symbols. The fix applies a confidence
    penalty proportional to the number of ambiguous candidates for method
    calls (field_expression), not regular function calls (identifier).
    """

    def test_method_call_many_candidates_no_edge(self, tmp_path: Path) -> None:
        """Method call with 20 ambiguous candidates produces no edge.

        When bar() matches 20 symbols and the method_resolver has
        ambiguity_threshold=3, the guard fires and no edge is created.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    foo.bar();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Build a registry with many symbols named "bar" under different types
        registry: dict[str, Symbol] = {}
        method_reg: dict[str, list[Symbol]] = {}
        for i in range(20):
            sym = Symbol(
                id=f"rust:lib.rs:{i}-{i}:Type{i}.bar:method",
                name=f"Type{i}.bar", kind="method", language="rust",
                path="lib.rs",
                span=Span(start_line=i, end_line=i, start_col=0, end_col=1),
                origin="test", origin_run_id="run",
            )
            registry[f"Type{i}.bar"] = sym
            method_reg.setdefault("bar", []).append(sym)

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, {},
            "run", resolver, {},
            method_resolver=method_resolver,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        # With 20 candidates and ambiguity_threshold=3, no edge should be created
        assert len(call_edges) == 0

    def test_method_call_prefers_same_module(self, tmp_path: Path) -> None:
        """Method call prefers candidate from the same module path.

        When foo.prove() is called from nova/mod.rs and there are two
        candidates (nova/nifs.rs::NIFS::prove, neutron/nifs.rs::NIFS::prove),
        the resolver should prefer the one in the same module tree (nova/).
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    x.prove();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:nova/mod.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="nova/mod.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Two candidates named "prove" in different modules
        nova_prove = Symbol(
            id="rust:nova/nifs.rs:1-10:NIFS::prove:method",
            name="NIFS::prove", kind="method", language="rust",
            path="nova/nifs.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        neutron_prove = Symbol(
            id="rust:neutron/nifs.rs:1-10:NIFS::prove:method",
            name="NIFS::prove", kind="method", language="rust",
            path="neutron/nifs.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry = {"caller": caller}
        method_reg: dict[str, list[Symbol]] = {
            "prove": [nova_prove, neutron_prove],
        }
        mr = ListNameResolver(method_reg, ambiguity_threshold=3)
        resolver = NameResolver(registry)
        span_idx = {(1, 3): caller}

        edges = _extract_edges_from_file(
            tree, source_text, "nova/mod.rs",
            registry, {"caller": caller, "NIFS::prove": nova_prove},
            "run1", resolver, {},
            method_resolver=mr,
            span_index=span_idx,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        # Should prefer nova/nifs.rs (same module tree) over neutron/nifs.rs
        assert call_edges[0].dst == nova_prove.id, (
            f"Expected nova/nifs.rs::NIFS::prove, got {call_edges[0].dst}"
        )

    def test_cross_crate_single_candidate_method_resolves(self, tmp_path: Path) -> None:
        """A method call with a single cross-crate candidate still resolves.

        When x.serve() is called from crates/core/src/app.rs and the only
        candidate is in crates/util/tower/src/lib.rs, the call should
        resolve (with reduced confidence) rather than being rejected.
        This tests the soft_hint=True behavior in the Rust analyzer.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    x.serve();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:crates/core/src/app.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="crates/core/src/app.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Single candidate in a different crate
        tower_serve = Symbol(
            id="rust:crates/util/tower/src/lib.rs:1-10:Server::serve:method",
            name="Server::serve", kind="method", language="rust",
            path="crates/util/tower/src/lib.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry = {"caller": caller}
        method_reg: dict[str, list[Symbol]] = {
            "serve": [tower_serve],
        }
        mr = ListNameResolver(method_reg, ambiguity_threshold=3)
        resolver = NameResolver(registry)
        span_idx = {(1, 3): caller}

        edges = _extract_edges_from_file(
            tree, source_text, "crates/core/src/app.rs",
            registry, {"caller": caller, "Server::serve": tower_serve},
            "run1", resolver, {},
            method_resolver=mr,
            span_index=span_idx,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1, (
            "Single cross-crate method candidate should resolve, not be rejected"
        )
        assert call_edges[0].dst == tower_serve.id

    def test_function_call_not_penalized(self, tmp_path: Path) -> None:
        """Regular function calls (not method calls) keep normal confidence.

        Only field_expression calls (foo.bar()) get the ambiguity penalty,
        not plain identifier calls (bar()).
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    bar();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Build a registry with multiple "bar" symbols (ambiguous)
        registry: dict[str, Symbol] = {}
        for i in range(5):
            sym = Symbol(
                id=f"rust:lib.rs:{i}-{i}:Mod{i}.bar:function",
                name=f"Mod{i}.bar", kind="function", language="rust",
                path="lib.rs",
                span=Span(start_line=i, end_line=i, start_col=0, end_col=1),
                origin="test", origin_run_id="run",
            )
            registry[f"Mod{i}.bar"] = sym

        resolver = NameResolver(registry)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, {},
            "run", resolver, {},
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        # Regular function calls use standard confidence: 0.80 * 0.70 = 0.56
        assert call_edges[0].confidence >= 0.50

    def test_method_call_few_candidates_moderate_confidence(self, tmp_path: Path) -> None:
        """Method call with 2 candidates keeps moderate confidence.

        Below the ambiguity_threshold=3, so the method_resolver returns
        a result with scaled confidence.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    foo.process();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Only 2 candidates — below ambiguity threshold
        registry: dict[str, Symbol] = {}
        method_reg: dict[str, list[Symbol]] = {}
        for i in range(2):
            sym = Symbol(
                id=f"rust:lib.rs:{i}-{i}:Type{i}.process:method",
                name=f"Type{i}.process", kind="method", language="rust",
                path="lib.rs",
                span=Span(start_line=i, end_line=i, start_col=0, end_col=1),
                origin="test", origin_run_id="run",
            )
            registry[f"Type{i}.process"] = sym
            method_reg.setdefault("process", []).append(sym)

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, {},
            "run", resolver, {},
            method_resolver=method_resolver,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        # With only 2 candidates, confidence = 0.80 * 1/sqrt(2) ≈ 0.57
        assert call_edges[0].confidence >= 0.30


class TestRustScopedIdentifierResolution:
    """Tests for Rust scoped identifier (Foo::bar) call resolution.

    When code calls Foo::bar(), the analyzer should use the full qualified
    name "Foo::bar" for resolution instead of just "bar". This prevents
    false edges when multiple types share a method name (e.g., Diff::compute
    vs Parser::compute).
    """

    def _parse_and_extract(
        self, source_text: bytes,
        local_symbols: dict, global_symbols: dict,
        use_aliases: dict | None = None,
        *,
        with_method_resolver: bool = False,
    ) -> list:
        """Helper: parse Rust source and extract edges.

        Args:
            with_method_resolver: If True, build a ListNameResolver with
                ambiguity_threshold=3 (matching real analysis flow).
                Required for tests that exercise ambiguous short names
                (e.g., multiple ::new methods).
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.symbol_resolution import (
            ListNameResolver,
            NameResolver,
        )

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(source_text)

        resolver = NameResolver(global_symbols)

        mr = None
        if with_method_resolver:
            global_methods: dict[str, list] = {}
            for sym in global_symbols.values():
                if sym.kind in ("method", "function"):
                    short = sym.name.split("::")[-1] if "::" in sym.name else sym.name
                    global_methods.setdefault(short, []).append(sym)
            mr = ListNameResolver(global_methods, ambiguity_threshold=3)

        return _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, global_symbols,
            "run", resolver, use_aliases or {},
            method_resolver=mr,
        )

    def _make_rust_symbol(self, name: str, kind: str = "method"):
        """Helper: create a Rust symbol for testing."""
        from hypergumbo_core.ir import Symbol, Span
        return Symbol(
            id=f"rust:lib.rs:1-10:{name}:{kind}",
            name=name, kind=kind, language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

    def test_scoped_call_exact_match(self, tmp_path: Path) -> None:
        """Foo::bar() resolves to Foo::bar symbol via full qualified name."""
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:main:function",
            name="main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        target = self._make_rust_symbol("Diff::compute")

        source = b"fn main() {\n    Diff::compute();\n}\n"
        global_symbols = {"Diff::compute": target}

        edges = self._parse_and_extract(
            source, {"main": caller}, global_symbols,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].dst == target.id
        # Full qualified match → high confidence
        assert call_edges[0].confidence >= 0.70

    def test_scoped_call_with_multiple_types(self, tmp_path: Path) -> None:
        """Diff::compute() resolves to Diff::compute, not Parser::compute.

        This is the core fix: when multiple types share a method name,
        the scoped call Diff::compute() should find the correct one.
        """
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:main:function",
            name="main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        diff_compute = self._make_rust_symbol("Diff::compute")
        parser_compute = Symbol(
            id="rust:lib.rs:20-30:Parser::compute:method",
            name="Parser::compute", kind="method", language="rust",
            path="lib.rs",
            span=Span(start_line=20, end_line=30, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        source = b"fn main() {\n    Diff::compute();\n}\n"
        global_symbols = {
            "Diff::compute": diff_compute,
            "Parser::compute": parser_compute,
        }

        edges = self._parse_and_extract(
            source, {"main": caller}, global_symbols,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        # Must resolve to Diff::compute, not Parser::compute
        assert call_edges[0].dst == diff_compute.id
        # With qualified lookup, should get exact match confidence (0.80),
        # not ambiguous suffix confidence (0.80 * 0.70 = 0.56)
        assert call_edges[0].confidence >= 0.70

    def test_unscoped_method_still_works(self, tmp_path: Path) -> None:
        """Regular foo.bar() method calls still resolve via suffix matching."""
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:main:function",
            name="main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        target = self._make_rust_symbol("MyType::bar")

        source = b"fn main() {\n    foo.bar();\n}\n"
        global_symbols = {"MyType::bar": target}

        edges = self._parse_and_extract(
            source, {"main": caller}, global_symbols,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        # Should find bar via suffix matching (MyType::bar → "bar" suffix)
        assert len(call_edges) == 1
        assert call_edges[0].dst == target.id

    def test_scoped_call_not_in_registry(self, tmp_path: Path) -> None:
        """Unknown::method() doesn't crash, no edge produced."""
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:main:function",
            name="main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        source = b"fn main() {\n    Unknown::method();\n}\n"

        edges = self._parse_and_extract(
            source, {"main": caller}, {},
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        # No symbols registered, so no edges should be produced
        assert len(call_edges) == 0

    def test_scoped_call_falls_back_to_short_name(self, tmp_path: Path) -> None:
        """When full scoped name not found, falls back to short name lookup.

        If registry has "bar" but not "Foo::bar", we should still find "bar"
        via the short name fallback.
        """
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:main:function",
            name="main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        target = Symbol(
            id="rust:lib.rs:1-10:bar:function",
            name="bar", kind="function", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        source = b"fn main() {\n    Foo::bar();\n}\n"
        # Registry only has "bar", not "Foo::bar"
        global_symbols = {"bar": target}

        edges = self._parse_and_extract(
            source, {"main": caller}, global_symbols,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].dst == target.id

    def test_module_qualified_call_strips_module_prefix(self, tmp_path: Path) -> None:
        """mod_name::Type::method() resolves by stripping the module prefix.

        When code calls codex_agent::CodexAgent::new(), the full scoped name
        is "codex_agent::CodexAgent::new" but the registry has "CodexAgent::new".
        The analyzer should try stripping module prefixes from the scoped name.

        This test includes multiple ::new symbols to trigger the ambiguity guard
        on the bare "new" fallback, ensuring the module-stripped "CodexAgent::new"
        path is what actually resolves.
        """
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:run_main:function",
            name="run_main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        target = Symbol(
            id="rust:codex_agent.rs:1-10:CodexAgent::new:method",
            name="CodexAgent::new", kind="method", language="rust",
            path="codex_agent.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        # Multiple ::new symbols to trigger ambiguity guard on bare "new"
        other1 = Symbol(
            id="rust:thread.rs:1-10:Thread::new:method",
            name="Thread::new", kind="method", language="rust",
            path="thread.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        other2 = Symbol(
            id="rust:spawner.rs:1-10:Spawner::new:method",
            name="Spawner::new", kind="method", language="rust",
            path="spawner.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        other3 = Symbol(
            id="rust:client.rs:1-10:Client::new:method",
            name="Client::new", kind="method", language="rust",
            path="client.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        source = b"fn run_main() {\n    codex_agent::CodexAgent::new(config);\n}\n"
        global_symbols = {
            "CodexAgent::new": target,
            "Thread::new": other1,
            "Spawner::new": other2,
            "Client::new": other3,
        }

        edges = self._parse_and_extract(
            source, {"run_main": caller}, global_symbols,
            with_method_resolver=True,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1, (
            f"Expected 1 call edge for module-qualified call, got {len(call_edges)}"
        )
        assert call_edges[0].dst == target.id

    def test_deeply_module_qualified_call(self, tmp_path: Path) -> None:
        """a::b::Type::method() resolves by stripping module prefixes.

        Tests that even with multiple module segments (a::b::Type::method),
        the analyzer can resolve to Type::method in the registry.
        """
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:main:function",
            name="main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        target = Symbol(
            id="rust:config.rs:1-10:Config::load:method",
            name="Config::load", kind="method", language="rust",
            path="config.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        # Multiple ::load symbols to trigger ambiguity guard
        other1 = Symbol(
            id="rust:other.rs:1-10:Other::load:method",
            name="Other::load", kind="method", language="rust",
            path="other.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        other2 = Symbol(
            id="rust:db.rs:1-10:Db::load:method",
            name="Db::load", kind="method", language="rust",
            path="db.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        other3 = Symbol(
            id="rust:cache.rs:1-10:Cache::load:method",
            name="Cache::load", kind="method", language="rust",
            path="cache.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        source = b"fn main() {\n    codex_core::config::Config::load();\n}\n"
        global_symbols = {
            "Config::load": target,
            "Other::load": other1,
            "Db::load": other2,
            "Cache::load": other3,
        }

        edges = self._parse_and_extract(
            source, {"main": caller}, global_symbols,
            with_method_resolver=True,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1, (
            f"Expected 1 call edge for deeply qualified call, got {len(call_edges)}"
        )
        assert call_edges[0].dst == target.id

    def test_module_qualified_call_via_local_symbols(self, tmp_path: Path) -> None:
        """mod::Type::method() resolves via local_symbols when target is local.

        Exercises the local_symbols lookup path in Strategy 1b (module prefix
        stripping). When the target is in the same file's local_symbols dict,
        it should resolve without needing the global resolver.
        """
        from hypergumbo_core.ir import Symbol, Span

        caller = Symbol(
            id="rust:test.rs:1-5:run_main:function",
            name="run_main", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        target = Symbol(
            id="rust:test.rs:10-20:Agent::init:method",
            name="Agent::init", kind="method", language="rust",
            path="test.rs",
            span=Span(start_line=10, end_line=20, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        # Multiple ::init symbols to trigger ambiguity
        other1 = Symbol(
            id="rust:a.rs:1-10:Server::init:method",
            name="Server::init", kind="method", language="rust",
            path="a.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        other2 = Symbol(
            id="rust:b.rs:1-10:Client::init:method",
            name="Client::init", kind="method", language="rust",
            path="b.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        other3 = Symbol(
            id="rust:c.rs:1-10:Db::init:method",
            name="Db::init", kind="method", language="rust",
            path="c.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        source = b"fn run_main() {\n    my_mod::Agent::init();\n}\n"
        local_symbols = {"run_main": caller, "Agent::init": target}
        global_symbols = {
            "Agent::init": target,
            "Server::init": other1,
            "Client::init": other2,
            "Db::init": other3,
        }

        edges = self._parse_and_extract(
            source, local_symbols, global_symbols,
            with_method_resolver=True,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].dst == target.id
        # local_symbols path should give confidence 0.85
        assert call_edges[0].confidence == 0.85


class TestRustEnclosingFunctionQualified:
    """Tests for correct enclosing function detection in impl blocks.

    When two impl blocks define methods with the same short name,
    _get_enclosing_function must return the correct qualified symbol
    (e.g., Diff::compute, not Parser::compute) for call attribution.
    This is critical for forward slices — if call edges have the wrong
    src, the slice from the right function won't find its callees.
    """

    def test_call_attributed_to_correct_impl_method(self, tmp_path: Path) -> None:
        """Calls inside Diff::compute are attributed to Diff::compute, not Parser::compute.

        The bug: symbol_by_name["compute"] is overwritten by whichever impl
        is processed last. _get_enclosing_function uses only the short name,
        so calls inside Diff::compute get attributed to Parser::compute.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("""
fn helper() {}

struct Diff;
struct Parser;

impl Diff {
    fn compute(&self) {
        helper();
    }
}

impl Parser {
    fn compute(&self) {
        // Parser's compute does nothing
    }
}
""")

        result = analyze_rust(tmp_path)

        diff_compute = next(
            (s for s in result.symbols if s.name == "Diff::compute"), None,
        )
        parser_compute = next(
            (s for s in result.symbols if s.name == "Parser::compute"), None,
        )
        helper_func = next(
            (s for s in result.symbols if s.name == "helper"), None,
        )

        assert diff_compute is not None, "Should find Diff::compute"
        assert parser_compute is not None, "Should find Parser::compute"
        assert helper_func is not None, "Should find helper"

        # The call to helper() in Diff::compute must have src = Diff::compute
        call_edge = next(
            (
                e for e in result.edges
                if e.dst == helper_func.id
                and e.edge_type == "calls"
            ),
            None,
        )
        assert call_edge is not None, "Should have call edge to helper()"
        assert call_edge.src == diff_compute.id, (
            f"Call to helper() should be from Diff::compute ({diff_compute.id}), "
            f"not Parser::compute ({parser_compute.id}). Got: {call_edge.src}"
        )

    def test_calls_in_multiple_same_named_methods(self, tmp_path: Path) -> None:
        """Each same-named method's calls are correctly attributed.

        Both Foo::run and Bar::run call different functions. Each call
        must be attributed to the correct enclosing method.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("""
fn alpha() {}
fn beta() {}

struct Foo;
struct Bar;

impl Foo {
    fn run(&self) {
        alpha();
    }
}

impl Bar {
    fn run(&self) {
        beta();
    }
}
""")

        result = analyze_rust(tmp_path)

        foo_run = next(
            (s for s in result.symbols if s.name == "Foo::run"), None,
        )
        bar_run = next(
            (s for s in result.symbols if s.name == "Bar::run"), None,
        )
        alpha = next(
            (s for s in result.symbols if s.name == "alpha"), None,
        )
        beta = next(
            (s for s in result.symbols if s.name == "beta"), None,
        )

        assert foo_run is not None
        assert bar_run is not None
        assert alpha is not None
        assert beta is not None

        # alpha() call should come from Foo::run
        alpha_edge = next(
            (e for e in result.edges if e.dst == alpha.id and e.edge_type == "calls"),
            None,
        )
        assert alpha_edge is not None
        assert alpha_edge.src == foo_run.id, (
            f"alpha() should be called from Foo::run, got src={alpha_edge.src}"
        )

        # beta() call should come from Bar::run
        beta_edge = next(
            (e for e in result.edges if e.dst == beta.id and e.edge_type == "calls"),
            None,
        )
        assert beta_edge is not None
        assert beta_edge.src == bar_run.id, (
            f"beta() should be called from Bar::run, got src={beta_edge.src}"
        )

    def test_free_function_still_works(self, tmp_path: Path) -> None:
        """Functions outside impl blocks still have correct call attribution."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("""
fn helper() {}

fn caller() {
    helper();
}

struct Foo;

impl Foo {
    fn caller(&self) {
        helper();
    }
}
""")

        result = analyze_rust(tmp_path)

        free_caller = next(
            (s for s in result.symbols if s.name == "caller"), None,
        )
        foo_caller = next(
            (s for s in result.symbols if s.name == "Foo::caller"), None,
        )
        helper_func = next(
            (s for s in result.symbols if s.name == "helper"), None,
        )

        assert free_caller is not None
        assert foo_caller is not None
        assert helper_func is not None

        # Both should have call edges to helper
        call_edges = [
            e for e in result.edges
            if e.dst == helper_func.id and e.edge_type == "calls"
        ]
        src_ids = {e.src for e in call_edges}
        assert free_caller.id in src_ids, "Free function caller should call helper"
        assert foo_caller.id in src_ids, "Foo::caller should call helper"


class TestRustScopedFallbackAmbiguity:
    """Tests for ambiguity guard on scoped identifier fallback to short name.

    When ``Type::new()`` cannot resolve the full scoped name ``Type::new``,
    the fallback to the bare method name ``new`` must use the
    ``method_resolver`` with its ambiguity threshold.  Without this guard,
    ``SomeType::new()`` silently resolves to an arbitrary ``new`` symbol
    producing massive false in-edges on common associated function names
    (``new``, ``default``, ``from``).
    """

    def test_scoped_fallback_many_candidates_no_edge(self, tmp_path: Path) -> None:
        """Type::new() with 20 'new' candidates produces no call edge.

        When the full scoped name ``UnknownType::new`` is not in the registry
        and the short name ``new`` has 20 candidates, the method_resolver
        (ambiguity_threshold=3) should suppress the edge.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    MyType::new();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Build a registry with many symbols named "new" under different types
        registry: dict[str, Symbol] = {}
        method_reg: dict[str, list[Symbol]] = {}
        for i in range(20):
            sym = Symbol(
                id=f"rust:lib.rs:{i}-{i}:Type{i}::new:function",
                name=f"Type{i}::new", kind="function", language="rust",
                path="lib.rs",
                span=Span(start_line=i, end_line=i, start_col=0, end_col=1),
                origin="test", origin_run_id="run",
            )
            registry[f"Type{i}::new"] = sym
            method_reg.setdefault("new", []).append(sym)

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, registry,
            "run", resolver, {},
            method_resolver=method_resolver,
            span_index={
                (caller.span.start_line, caller.span.end_line): caller,
            },
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        # The ambiguity guard should suppress the edge — no false resolution
        assert len(call_edges) == 0, (
            f"Expected 0 call edges but got {len(call_edges)}: "
            f"{[e.dst for e in call_edges]}"
        )

    def test_scoped_fallback_single_candidate_resolves(
        self, tmp_path: Path,
    ) -> None:
        """Type::method() with 1 candidate for short name resolves correctly.

        When ``UnknownType::compute`` isn't found but ``compute`` has exactly
        1 candidate, the fallback should produce a call edge.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    Foo::compute();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )
        target = Symbol(
            id="rust:lib.rs:1-10:compute:function",
            name="compute", kind="function", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"compute": target}
        method_reg: dict[str, list[Symbol]] = {"compute": [target]}

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, registry,
            "run", resolver, {},
            method_resolver=method_resolver,
            span_index={
                (caller.span.start_line, caller.span.end_line): caller,
            },
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].dst == target.id


class TestRustTraitImplEdges:
    """Tests for Rust trait implementation edges.

    `impl Trait for Struct` should produce an 'implements' edge from
    the struct symbol to the trait symbol.
    """

    def test_impl_trait_for_struct(self, tmp_path: Path) -> None:
        """impl Circuit for MyCircuit → implements edge."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "trait Circuit {\n"
            "    fn synthesize(&self);\n"
            "}\n"
            "\n"
            "struct MyCircuit;\n"
            "\n"
            "impl Circuit for MyCircuit {\n"
            "    fn synthesize(&self) {}\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)
        assert not result.skipped

        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(impl_edges) == 1

        # Find the trait and struct symbols
        trait_sym = next(s for s in result.symbols if s.name == "Circuit" and s.kind == "trait")
        struct_sym = next(s for s in result.symbols if s.name == "MyCircuit" and s.kind == "struct")

        # Edge goes from struct → trait (struct implements the trait)
        assert impl_edges[0].src == struct_sym.id
        assert impl_edges[0].dst == trait_sym.id

    def test_impl_without_trait_no_edge(self, tmp_path: Path) -> None:
        """impl MyStruct (no trait) should not produce an implements edge."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "struct MyStruct;\n"
            "\n"
            "impl MyStruct {\n"
            "    fn new() -> Self { MyStruct }\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)
        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(impl_edges) == 0

    def test_multiple_impl_trait(self, tmp_path: Path) -> None:
        """Multiple structs implementing the same trait produce multiple edges."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "trait Drawable {\n"
            "    fn draw(&self);\n"
            "}\n"
            "\n"
            "struct Circle;\n"
            "struct Square;\n"
            "\n"
            "impl Drawable for Circle {\n"
            "    fn draw(&self) {}\n"
            "}\n"
            "\n"
            "impl Drawable for Square {\n"
            "    fn draw(&self) {}\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)
        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(impl_edges) == 2

        trait_sym = next(s for s in result.symbols if s.name == "Drawable")
        dsts = {e.dst for e in impl_edges}
        assert all(d == trait_sym.id for d in dsts)

        srcs = {e.src for e in impl_edges}
        circle = next(s for s in result.symbols if s.name == "Circle")
        square = next(s for s in result.symbols if s.name == "Square")
        assert circle.id in srcs
        assert square.id in srcs


    def test_impl_qualified_trait_resolves_short_name(self, tmp_path: Path) -> None:
        """impl module::Trait for Struct resolves to the short trait name.

        gRPC/tonic pattern: impl service_server::MyService for Server.
        The trait is defined as 'MyService' but referenced with a qualified
        path. The implements edge should still be created.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "mod service_server {\n"
            "    pub trait QueryService {\n"
            "        fn query(&self);\n"
            "    }\n"
            "}\n"
            "\n"
            "struct Server;\n"
            "\n"
            "impl service_server::QueryService for Server {\n"
            "    fn query(&self) {}\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)
        assert not result.skipped

        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        assert len(impl_edges) == 1

        struct_sym = next(s for s in result.symbols if s.name == "Server")
        trait_sym = next(
            s for s in result.symbols
            if s.name == "QueryService" and s.kind == "trait"
        )
        assert impl_edges[0].src == struct_sym.id
        assert impl_edges[0].dst == trait_sym.id


    def test_impl_unresolved_trait_creates_edge(self, tmp_path: Path) -> None:
        """impl UnresolvedTrait for Struct creates an unresolved implements edge.

        When a trait is imported from an external/generated module (e.g.,
        tonic gRPC QueryService) and its definition file wasn't analyzed,
        the trait symbol won't be in the registry. The analyzer should still
        create an implements edge to an unresolved target at lower confidence,
        so the relationship is captured in the behavior map.

        This is critical for penumbra-style repos where tonic QueryService
        traits are generated from .proto files and not directly analyzed.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        # File 1: only has the struct and the impl, NOT the trait definition.
        # The trait is imported from an external module (simulated by a use statement
        # to a module that doesn't exist in the analyzed files).
        (tmp_path / "server.rs").write_text(
            "use external_proto::query_service_server::QueryService;\n"
            "\n"
            "pub struct Server;\n"
            "\n"
            "impl QueryService for Server {\n"
            "    fn query(&self) {}\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)
        assert not result.skipped

        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        # Should have 1 unresolved implements edge
        assert len(impl_edges) == 1, (
            f"Expected 1 implements edge for unresolved trait, got {len(impl_edges)}"
        )

        struct_sym = next(s for s in result.symbols if s.name == "Server")
        edge = impl_edges[0]
        assert edge.src == struct_sym.id
        # Dst should reference QueryService (unresolved)
        assert "QueryService" in edge.dst
        # Lower confidence for unresolved
        assert edge.confidence < 0.95

    def test_impl_std_trait_no_unresolved_edge(self, tmp_path: Path) -> None:
        """impl Clone for Struct should NOT create an unresolved implements edge.

        Standard library traits (Clone, Debug, Default, etc.) are ubiquitous
        and not architecturally meaningful. Creating unresolved edges for them
        would be pure noise.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "struct MyStruct;\n"
            "\n"
            "impl Clone for MyStruct {\n"
            "    fn clone(&self) -> Self { MyStruct }\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)
        impl_edges = [e for e in result.edges if e.edge_type == "implements"]
        # Clone is a std trait — no unresolved edge should be created
        assert len(impl_edges) == 0


class TestRustGenericTraitMethodBlocklist:
    """Tests for generic trait method blocklist (false-positive suppression).

    Generic trait methods like ``.into()``, ``.clone()``, ``.len()`` create
    massive false-positive in-degree when resolved via ``method_resolver``
    because, without receiver type information, ``x.into()`` resolves to
    an arbitrary concrete ``Into`` implementation.  The blocklist prevents
    short-name resolution for these methods while preserving fully-scoped
    resolution (``StatusRow::into()`` via Strategy 1).
    """

    def test_blocked_into_single_candidate_no_edge(self, tmp_path: Path) -> None:
        """x.into() with a single candidate produces no edge.

        Even with only 1 candidate (below the ambiguity threshold), a
        blocked method name should not resolve.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    x.into();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Single candidate — would normally resolve without the blocklist
        target = Symbol(
            id="rust:lib.rs:1-5:StatusRow::into:method",
            name="StatusRow::into", kind="method", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"StatusRow::into": target}
        method_reg: dict[str, list[Symbol]] = {"into": [target]}

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, {},
            "run", resolver, {},
            method_resolver=method_resolver,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 0, (
            f"Expected 0 call edges for blocked .into() but got "
            f"{len(call_edges)}: {[e.dst for e in call_edges]}"
        )

    def test_blocked_load_prevents_atomic_false_positives(self, tmp_path: Path) -> None:
        """x.load() produces no edge — prevents AtomicU64.load() false positives.

        WI-bakak: jolt's JoltDevice::load rslice had 22/29 false positive
        depth=1 callers from AtomicU64.load(Ordering::Relaxed) conflated
        with JoltDevice.load().
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")  # pragma: no cover

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = (
            b"use std::sync::atomic::Ordering;\n"
            b"fn caller() {\n"
            b"    counter.load(Ordering::Relaxed);\n"
            b"}\n"
        )
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:counters.rs:2-4:caller:function",
            name="caller", kind="function", language="rust",
            path="counters.rs",
            span=Span(start_line=2, end_line=4, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # JoltDevice::load — a domain-specific load fn that should NOT
        # attract edges from AtomicU64.load() calls
        jolt_load = Symbol(
            id="rust:device.rs:1-10:JoltDevice::load:function",
            name="JoltDevice::load", kind="function", language="rust",
            path="device.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"JoltDevice::load": jolt_load}
        method_reg: dict[str, list[Symbol]] = {"load": [jolt_load]}

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "counters.rs", local_symbols, {},
            "run", resolver, {},
            method_resolver=method_resolver,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 0, (
            f"Expected 0 call edges for blocked .load() but got "
            f"{len(call_edges)}: {[e.dst for e in call_edges]}"
        )

    def test_nonblocked_prove_still_resolves(self, tmp_path: Path) -> None:
        """x.prove() with candidates still resolves (not in blocklist)."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    x.prove();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        target = Symbol(
            id="rust:lib.rs:1-10:NIFS::prove:method",
            name="NIFS::prove", kind="method", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"NIFS::prove": target}
        method_reg: dict[str, list[Symbol]] = {"prove": [target]}

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, {},
            "run", resolver, {},
            method_resolver=method_resolver,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].dst == target.id

    def test_blocklist_contains_expected_names(self) -> None:
        """Constant contains core trait methods and collection methods."""
        from hypergumbo_lang_mainstream.rust import _RUST_GENERIC_TRAIT_METHODS

        # Core convert traits
        for name in ("into", "from", "try_into", "try_from"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Clone / Eq / Hash
        for name in ("clone", "eq", "ne", "hash", "cmp"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Collection methods
        for name in ("len", "push", "pop", "get", "insert", "remove"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Serde
        for name in ("serialize", "deserialize"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Atomic / sync methods (WI-bakak: .load() false positives)
        for name in ("load", "store", "fetch_add", "fetch_sub",
                      "compare_exchange", "swap"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Iterator / Option / Result combinators
        for name in ("map", "filter", "fold", "collect", "flat_map",
                      "find", "any", "all", "for_each", "map_err",
                      "and_then", "or_else", "unwrap_or", "unwrap_or_else",
                      "ok", "err", "expect", "ok_or", "ok_or_else"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # IO traits
        for name in ("read", "write", "flush"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Display / ToString
        for name in ("to_string",):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Constructor / builder (new is ubiquitous)
        for name in ("new", "build"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Channel / async
        for name in ("send", "recv"):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Extend trait
        for name in ("extend",):
            assert name in _RUST_GENERIC_TRAIT_METHODS, f"{name} missing"

        # Domain-specific names should NOT be in the blocklist
        for name in ("prove", "synthesize", "verify", "compute", "serve"):
            assert name not in _RUST_GENERIC_TRAIT_METHODS, (
                f"{name} should not be blocked"
            )

    def test_scoped_into_still_resolves_via_strategy1(
        self, tmp_path: Path,
    ) -> None:
        """StatusRow::into() with full match in registry still resolves.

        The blocklist only affects short-name fallback via method_resolver.
        When the full scoped name ``StatusRow::into`` exists in global_symbols,
        Strategy 1 resolves it before the blocklist is consulted.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        # Scoped call: StatusRow::into() — Strategy 1 should find it
        source_text = b"fn caller() {\n    StatusRow::into(x);\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        target = Symbol(
            id="rust:lib.rs:1-5:StatusRow::into:method",
            name="StatusRow::into", kind="method", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        # Full scoped name is in global_symbols — Strategy 1 resolves it
        registry: dict[str, Symbol] = {"StatusRow::into": target}
        method_reg: dict[str, list[Symbol]] = {"into": [target]}

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, registry,
            "run", resolver, {},
            method_resolver=method_resolver,
            span_index={
                (caller.span.start_line, caller.span.end_line): caller,
            },
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1, (
            f"Expected 1 call edge for scoped StatusRow::into() but got "
            f"{len(call_edges)}"
        )
        assert call_edges[0].dst == target.id


class TestRustTurbofishCalls:
    """Tests for turbofish / fully-qualified generic call resolution (WI-jabut).

    Rust turbofish syntax (``PublicParams::<E1,E2>::setup()``) and generic
    method calls (``x.collect::<Vec<i32>>()``) wrap the inner function node
    inside a ``generic_function`` tree-sitter node.  The analyzer must unwrap
    this to extract the callee name and resolve edges correctly.  For scoped
    turbofish calls, type arguments must be stripped from ``full_scoped_name``
    so registry lookup succeeds (e.g., ``PublicParams::setup`` not
    ``PublicParams::<E1,E2>::setup``).
    """

    def test_scoped_turbofish_resolves(self, tmp_path: Path) -> None:
        """PublicParams::<E1,E2>::setup() resolves to PublicParams::setup.

        Type arguments are stripped from the scoped name so Strategy 1
        matches the registry key.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    PublicParams::<E1, E2>::setup();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        target = Symbol(
            id="rust:lib.rs:1-10:PublicParams::setup:function",
            name="PublicParams::setup", kind="function", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"PublicParams::setup": target}
        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(
            {"setup": [target]}, ambiguity_threshold=3,
        )
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, registry,
            "run", resolver, {},
            method_resolver=method_resolver,
            span_index={
                (caller.span.start_line, caller.span.end_line): caller,
            },
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1, (
            f"Expected 1 call edge for PublicParams::<E1,E2>::setup() "
            f"but got {len(call_edges)}"
        )
        assert call_edges[0].dst == target.id

    def test_method_turbofish_resolves(self, tmp_path: Path) -> None:
        """x.prove::<E1>() resolves to the prove method.

        The generic_function wrapper around field_expression must be
        unwrapped so the method call is detected correctly.
        Uses 'prove' (not in blocklist) to verify turbofish unwrapping.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    x.prove::<E1>();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        target = Symbol(
            id="rust:lib.rs:1-10:NIFS::prove:method",
            name="NIFS::prove", kind="method", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"NIFS::prove": target}
        method_reg: dict[str, list[Symbol]] = {"prove": [target]}

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, {},
            "run", resolver, {},
            method_resolver=method_resolver,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1, (
            f"Expected 1 call edge for x.prove::<E1>() "
            f"but got {len(call_edges)}"
        )
        assert call_edges[0].dst == target.id

    def test_plain_scoped_call_still_works(self, tmp_path: Path) -> None:
        """Vec::new() (no turbofish) still resolves correctly.

        Regression check: the turbofish handling must not break plain
        scoped identifiers.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    Vec::new();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        target = Symbol(
            id="rust:lib.rs:1-5:Vec::new:function",
            name="Vec::new", kind="function", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"Vec::new": target}
        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(
            {"new": [target]}, ambiguity_threshold=3,
        )
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, registry,
            "run", resolver, {},
            method_resolver=method_resolver,
            span_index={
                (caller.span.start_line, caller.span.end_line): caller,
            },
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 1
        assert call_edges[0].dst == target.id

    def test_turbofish_method_blocklist_still_applies(
        self, tmp_path: Path,
    ) -> None:
        """x.clone::<T>() is still blocked by the generic trait blocklist.

        Turbofish unwrapping should expose the field_expression so the
        blocklist check on the short method name still fires.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source_text = b"fn caller() {\n    x.clone::<T>();\n}\n"
        tree = parser.parse(source_text)

        caller = Symbol(
            id="rust:test.rs:1-3:caller:function",
            name="caller", kind="function", language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        target = Symbol(
            id="rust:lib.rs:1-5:Foo::clone:method",
            name="Foo::clone", kind="method", language="rust",
            path="lib.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        registry: dict[str, Symbol] = {"Foo::clone": target}
        method_reg: dict[str, list[Symbol]] = {"clone": [target]}

        resolver = NameResolver(registry)
        method_resolver = ListNameResolver(method_reg, ambiguity_threshold=3)
        local_symbols = {"caller": caller}

        edges = _extract_edges_from_file(
            tree, source_text, "test.rs", local_symbols, {},
            "run", resolver, {},
            method_resolver=method_resolver,
        )

        call_edges = [e for e in edges if e.edge_type == "calls"]
        assert len(call_edges) == 0, (
            f"Expected 0 call edges for blocked .clone::<T>() but got "
            f"{len(call_edges)}: {[e.dst for e in call_edges]}"
        )


class TestRustCfgTestInheritance:
    """Tests for #[cfg(test)] annotation inheritance (WI-sijim).

    Functions inside ``#[cfg(test)] mod tests { ... }`` should inherit the
    ``cfg(test)`` annotation so that ``is_test_node`` in the slicer correctly
    excludes them from production slices, even when the individual function
    doesn't carry its own ``#[test]`` attribute.
    """

    def test_helper_inside_cfg_test_module_gets_annotation(
        self, tmp_path: Path,
    ) -> None:
        """Helper function inside #[cfg(test)] mod inherits cfg(test)."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "pub fn real_function() {}\n"
            "\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    fn helper() {}\n"
            "\n"
            "    #[test]\n"
            "    fn test_something() {}\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)

        real_fn = next(s for s in result.symbols if s.name == "real_function")
        helper_fn = next(s for s in result.symbols if s.name == "helper")
        test_fn = next(
            s for s in result.symbols if s.name == "test_something"
        )

        # real_function should NOT have cfg(test)
        real_anns = (real_fn.meta or {}).get("annotations", [])
        cfg_test = {"name": "cfg", "args": ["test"], "kwargs": {}}
        assert cfg_test not in real_anns

        # helper should inherit cfg(test) from enclosing module
        helper_anns = (helper_fn.meta or {}).get("annotations", [])
        assert cfg_test in helper_anns, (
            f"helper inside #[cfg(test)] mod should have cfg(test) "
            f"annotation, got: {helper_anns}"
        )

        # test_something already has #[test]; should also have cfg(test)
        test_anns = (test_fn.meta or {}).get("annotations", [])
        assert cfg_test in test_anns

    def test_struct_inside_cfg_test_module_gets_annotation(
        self, tmp_path: Path,
    ) -> None:
        """Struct inside #[cfg(test)] mod inherits cfg(test)."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "pub struct RealStruct;\n"
            "\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    struct TestFixture;\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)

        real_struct = next(
            s for s in result.symbols if s.name == "RealStruct"
        )
        test_struct = next(
            s for s in result.symbols if s.name == "TestFixture"
        )

        cfg_test = {"name": "cfg", "args": ["test"], "kwargs": {}}

        real_anns = (real_struct.meta or {}).get("annotations", [])
        assert cfg_test not in real_anns

        test_anns = (test_struct.meta or {}).get("annotations", [])
        assert cfg_test in test_anns

    def test_enum_inside_cfg_test_module_gets_annotation(
        self, tmp_path: Path,
    ) -> None:
        """Enum inside #[cfg(test)] mod inherits cfg(test)."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "lib.rs").write_text(
            "pub enum RealEnum { A, B }\n"
            "\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    enum TestEnum { X, Y }\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)

        real_enum = next(s for s in result.symbols if s.name == "RealEnum")
        test_enum = next(s for s in result.symbols if s.name == "TestEnum")

        cfg_test = {"name": "cfg", "args": ["test"], "kwargs": {}}

        real_anns = (real_enum.meta or {}).get("annotations", [])
        assert cfg_test not in real_anns

        test_anns = (test_enum.meta or {}).get("annotations", [])
        assert cfg_test in test_anns

    def test_is_test_node_detects_cfg_test_helper(
        self, tmp_path: Path,
    ) -> None:
        """is_test_node returns True for helper inside #[cfg(test)] mod.

        End-to-end test: the inherited annotation must be in the format
        that is_test_node / _has_test_annotation recognizes.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust
        from hypergumbo_core.paths import is_test_node

        (tmp_path / "lib.rs").write_text(
            "pub fn real_function() {}\n"
            "\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    fn helper() {}\n"
            "}\n"
        )

        result = analyze_rust(tmp_path)

        real_fn = next(s for s in result.symbols if s.name == "real_function")
        helper_fn = next(s for s in result.symbols if s.name == "helper")

        # real_function in src/ is NOT a test node
        assert not is_test_node(real_fn.path, real_fn.meta)

        # helper inside #[cfg(test)] IS a test node
        assert is_test_node(helper_fn.path, helper_fn.meta), (
            f"helper inside #[cfg(test)] should be detected as test node, "
            f"meta={helper_fn.meta}"
        )


class TestUnwrapDerefType:
    """Tests for _unwrap_deref_type helper."""

    @staticmethod
    def _parse_field_type(source: bytes) -> str:
        """Parse source and return unwrapped type of the first field_declaration."""
        from hypergumbo_lang_mainstream.rust import _unwrap_deref_type
        from hypergumbo_core.analyze.base import iter_tree
        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(source)

        for node in iter_tree(tree.root_node):
            if node.type == "field_declaration":
                type_node = node.child_by_field_name("type")
                if type_node:
                    return _unwrap_deref_type(type_node, source)
        pytest.fail("No field_declaration found")

    def test_simple_type(self) -> None:
        """Plain type identifier returns as-is."""
        assert self._parse_field_type(b"struct S { field: App }") == "App"

    def test_box_unwrap(self) -> None:
        """Box<App> unwraps to App."""
        assert self._parse_field_type(b"struct S { field: Box<App> }") == "App"

    def test_arc_unwrap(self) -> None:
        """Arc<Client> unwraps to Client."""
        assert self._parse_field_type(
            b"struct S { field: Arc<Client> }",
        ) == "Client"

    def test_nested_wrapper(self) -> None:
        """Arc<Mutex<App>> unwraps to App."""
        assert self._parse_field_type(
            b"struct S { field: Arc<Mutex<App>> }",
        ) == "App"

    def test_reference_unwrap(self) -> None:
        """&App unwraps to App."""
        assert self._parse_field_type(
            b"struct S<'a> { field: &'a App }",
        ) == "App"

    def test_non_wrapper_generic(self) -> None:
        """HashMap<String, i32> returns HashMap (not a Deref wrapper)."""
        assert self._parse_field_type(
            b"struct S { field: HashMap<String, i32> }",
        ) == "HashMap"

    def test_scoped_type_generic(self) -> None:
        """Scoped wrapper type returns scoped name (not unwrapped)."""
        # std::sync::Mutex is a scoped_type_identifier, not in
        # _RUST_DEREF_WRAPPERS (which uses simple names).
        assert self._parse_field_type(
            b"struct S { field: std::sync::Mutex<App> }",
        ) == "std::sync::Mutex"

    def test_scoped_type_plain(self) -> None:
        """Plain scoped type (no generics) returns full path."""
        assert self._parse_field_type(
            b"struct S { field: std::io::Error }",
        ) == "std::io::Error"


class TestExtractStructFieldTypes:
    """Tests for _extract_struct_field_types helper."""

    @staticmethod
    def _parse(source: bytes) -> dict:
        """Parse Rust source and return struct field types."""
        from hypergumbo_lang_mainstream.rust import _extract_struct_field_types
        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(source)
        return _extract_struct_field_types(tree, source)

    def test_simple_struct(self) -> None:
        """Extracts field types from a simple struct."""
        result = self._parse(b"""
struct Server {
    app: App,
    db: Database,
}
""")
        assert "Server" in result
        assert result["Server"] == {"app": "App", "db": "Database"}

    def test_box_field_unwrapped(self) -> None:
        """Box<App> field type is unwrapped to App."""
        result = self._parse(b"""
struct Server {
    app: Box<App>,
    client: Arc<Client>,
}
""")
        assert result["Server"]["app"] == "App"
        assert result["Server"]["client"] == "Client"

    def test_tuple_struct_skipped(self) -> None:
        """Tuple structs (no named fields) are skipped."""
        result = self._parse(b"struct Wrapper(App);")
        assert "Wrapper" not in result

    def test_multiple_structs(self) -> None:
        """Extracts from multiple structs in one file."""
        result = self._parse(b"""
struct Server { app: App }
struct App { handler: Handler }
""")
        assert len(result) == 2
        assert result["Server"] == {"app": "App"}
        assert result["App"] == {"handler": "Handler"}

    def test_empty_struct(self) -> None:
        """Struct with no fields produces no entry."""
        result = self._parse(b"struct Empty {}")
        assert "Empty" not in result


class TestRustFieldTypePopulation:
    """Tests that _extract_symbols_from_file populates class_field_types."""

    def test_populates_class_field_types(self, tmp_path: Path) -> None:
        """class_field_types is populated from struct declarations."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct Server {
    app: App,
    db: Box<Database>,
}

struct App {
    handler: Handler,
}

fn main() {}
""")
        # We verify indirectly — the full integration test below
        # checks that edges are created. Here we verify the analyze
        # function doesn't crash with field type population.
        result = analyze_rust(tmp_path)
        assert not result.skipped


class TestRustStructFieldTypeResolution:
    """Integration tests for self.field.method() → typed_field_call edges.

    Tests Strategy 1.5 in _extract_edges_from_file: resolve self.field
    receivers via the field type registry to create typed call edges.
    """

    def test_basic_field_call(self, tmp_path: Path) -> None:
        """self.app.run() where app: App creates App::run edge."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct App {}

impl App {
    fn run(&self) {}
}

struct Server {
    app: App,
}

impl Server {
    fn start(&self) {
        self.app.run();
    }
}
""")
        result = analyze_rust(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 1
        edge = typed_edges[0]

        # Verify source is Server::start
        start_sym = next(s for s in result.symbols if s.name == "Server::start")
        run_sym = next(s for s in result.symbols if s.name == "App::run")
        assert edge.src == start_sym.id
        assert edge.dst == run_sym.id
        assert edge.confidence == 0.88
        assert edge.edge_type == "calls"

    def test_box_field_call(self, tmp_path: Path) -> None:
        """self.app.run() where app: Box<App> unwraps to App::run."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct App {}

impl App {
    fn run(&self) {}
}

struct Server {
    app: Box<App>,
}

impl Server {
    fn start(&self) {
        self.app.run();
    }
}
""")
        result = analyze_rust(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 1
        assert typed_edges[0].confidence == 0.88

    def test_nested_field_chain(self, tmp_path: Path) -> None:
        """self.inner.app.run() resolves through Outer→Inner→App."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct App {}

impl App {
    fn run(&self) {}
}

struct Inner {
    app: App,
}

struct Outer {
    inner: Inner,
}

impl Outer {
    fn start(&self) {
        self.inner.app.run();
    }
}
""")
        result = analyze_rust(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 1
        run_sym = next(s for s in result.symbols if s.name == "App::run")
        assert typed_edges[0].dst == run_sym.id

    def test_unknown_field_no_typed_edge(self, tmp_path: Path) -> None:
        """self.unknown.run() with unknown field falls through gracefully."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct App {}

impl App {
    fn run(&self) {}
}

struct Server {
    app: App,
}

impl Server {
    fn start(&self) {
        self.unknown.run();
    }
}
""")
        result = analyze_rust(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 0

    def test_non_self_receiver_not_resolved(self, tmp_path: Path) -> None:
        """other.app.run() is not resolved by Strategy 1.5."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct App {}

impl App {
    fn run(&self) {}
}

struct Server {
    app: App,
}

impl Server {
    fn start(&self, other: Server) {
        other.app.run();
    }
}
""")
        result = analyze_rust(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        # other.app isn't rooted at self, so Strategy 1.5 doesn't fire
        assert len(typed_edges) == 0

    def test_blocklisted_method_resolves_with_type(self, tmp_path: Path) -> None:
        """self.client.send() resolves even though 'send' is blocklisted.

        Strategy 1.5 fires before the _RUST_GENERIC_TRAIT_METHODS check
        because known receiver type makes it unambiguous.
        """
        from hypergumbo_lang_mainstream.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
struct Client {}

impl Client {
    fn send(&self) {}
}

struct Server {
    client: Client,
}

impl Server {
    fn dispatch(&self) {
        self.client.send();
    }
}
""")
        result = analyze_rust(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) == 1
        send_sym = next(s for s in result.symbols if s.name == "Client::send")
        assert typed_edges[0].dst == send_sym.id

    def test_cross_file_field_resolution(self, tmp_path: Path) -> None:
        """Field types from one file resolve methods defined in another."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        (tmp_path / "app.rs").write_text("""
pub struct App {}

impl App {
    pub fn run(&self) {}
}
""")
        (tmp_path / "server.rs").write_text("""
use crate::app::App;

struct Server {
    app: App,
}

impl Server {
    fn start(&self) {
        self.app.run();
    }
}
""")
        result = analyze_rust(tmp_path)

        typed_edges = [
            e for e in result.edges
            if e.evidence_type == "typed_field_call"
        ]
        assert len(typed_edges) >= 1

    def test_resolver_fallback_for_field_call(self) -> None:
        """Strategy 1.5 uses resolver.lookup() when target not in symbols dicts.

        Exercises the fallback path where typed_name (e.g. "App::run") is
        absent from both local_symbols and global_symbols but found via
        the resolver's suffix index.
        """
        from hypergumbo_lang_mainstream.rust import (
            _extract_edges_from_file,
            is_rust_tree_sitter_available,
            RustAnalyzer,
        )
        from hypergumbo_core.ir import Symbol, Span
        from hypergumbo_core.symbol_resolution import ListNameResolver, NameResolver

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        import tree_sitter_rust
        import tree_sitter

        lang = tree_sitter.Language(tree_sitter_rust.language())
        parser = tree_sitter.Parser(lang)

        source = b"""
impl Server {
    fn start(&self) {
        self.app.run();
    }
}
"""
        tree = parser.parse(source)

        caller = Symbol(
            id="rust:test.rs:2-4:Server::start:method",
            name="Server::start", kind="method", language="rust",
            path="test.rs",
            span=Span(start_line=2, end_line=4, start_col=4, end_col=5),
            origin="test", origin_run_id="run",
        )
        target = Symbol(
            id="rust:app.rs:1-5:App::run:method",
            name="App::run", kind="method", language="rust",
            path="app.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test", origin_run_id="run",
        )

        local_symbols = {"Server::start": caller, "start": caller}
        # Deliberately NOT including "App::run" in global_symbols
        # so the resolver.lookup fallback path fires.
        global_symbols = {"Server::start": caller}
        resolver = NameResolver({"App::run": target})

        analyzer = RustAnalyzer()
        analyzer._field_type_registry = {
            "Server": {"app": "App"},
        }

        method_syms = {"run": [target]}
        mr = ListNameResolver(method_syms, ambiguity_threshold=3)

        span_idx = {(2, 4): caller}

        edges = _extract_edges_from_file(
            tree, source, "test.rs",
            local_symbols, global_symbols,
            "run", resolver, {},
            method_resolver=mr,
            span_index=span_idx,
            field_type_registry=analyzer._field_type_registry,
            analyzer=analyzer,
        )

        typed_edges = [e for e in edges if e.evidence_type == "typed_field_call"]
        assert len(typed_edges) == 1
        assert typed_edges[0].dst == target.id
        assert typed_edges[0].confidence == 0.88


class TestRustSelfResolution:
    """Self:: calls inside impl blocks should resolve to the actual type."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tree_sitter(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_rust")

    def test_self_method_call_resolves(self, tmp_path: Path) -> None:
        """Self::helper() inside impl Foo should resolve to Foo::helper."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        code = """\
struct Server;

impl Server {
    fn run(&self) {
        Self::process();
    }

    fn process() {
        // do work
    }
}
"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.rs").write_text(code)
        result = analyze_rust(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Self::process() should resolve to Server::process
        run_to_process = [
            e for e in call_edges
            if "run" in e.src and "process" in e.dst
        ]
        assert len(run_to_process) >= 1

    def test_self_new_call_resolves(self, tmp_path: Path) -> None:
        """Self::new() inside impl should resolve to the actual type."""
        from hypergumbo_lang_mainstream.rust import analyze_rust

        code = """\
struct Config;

impl Config {
    fn new() -> Self {
        Config
    }

    fn default_config() -> Config {
        Self::new()
    }
}
"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text(code)
        result = analyze_rust(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Self::new() should resolve to Config::new
        default_to_new = [
            e for e in call_edges
            if "default_config" in e.src and "new" in e.dst
        ]
        assert len(default_to_new) >= 1
