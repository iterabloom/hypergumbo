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


class TestAxumRouteDetection:
    """Tests for Axum route handler detection."""

    def test_detects_simple_get_route(self, tmp_path: Path) -> None:
        """Detects .route("/path", get(handler)) pattern."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "routes.rs"
        rs_file.write_text("""
use axum::{Router, routing::get};

fn app() -> Router {
    Router::new()
        .route("/", get(root))
        .route("/users", get(list_users))
}

async fn root() -> &'static str {
    "Hello, World!"
}

async fn list_users() -> &'static str {
    "[]"
}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        assert "root" in route_names
        assert "list_users" in route_names

    def test_detects_multiple_http_methods(self, tmp_path: Path) -> None:
        """Detects post, put, delete routes."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "api.rs"
        rs_file.write_text("""
use axum::{Router, routing::{get, post, put, delete}};

fn api_routes() -> Router {
    Router::new()
        .route("/items", post(create_item))
        .route("/items/:id", put(update_item))
        .route("/items/:id", delete(delete_item))
}

async fn create_item() {}
async fn update_item() {}
async fn delete_item() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        assert "create_item" in route_names
        assert "update_item" in route_names
        assert "delete_item" in route_names

        # Check HTTP methods in metadata
        for route in routes:
            assert route.meta is not None
            assert "http_method" in route.meta
            assert "route_path" in route.meta

    def test_detects_method_chaining(self, tmp_path: Path) -> None:
        """Detects .route("/path", get(h1).post(h2)) pattern."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "chained.rs"
        rs_file.write_text("""
use axum::{Router, routing::{get, post}};

fn app() -> Router {
    Router::new()
        .route("/items", get(list_items).post(create_item))
}

async fn list_items() {}
async fn create_item() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        # Both handlers should be detected
        assert "list_items" in route_names
        assert "create_item" in route_names

        # Verify different HTTP methods
        http_methods = [s.meta["http_method"] for s in routes if s.meta]
        assert "GET" in http_methods
        assert "POST" in http_methods

    def test_route_has_stable_id(self, tmp_path: Path) -> None:
        """Route symbols have stable_id set to HTTP method."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "stable.rs"
        rs_file.write_text("""
use axum::{Router, routing::get};

fn app() -> Router {
    Router::new().route("/api", get(api_handler))
}

async fn api_handler() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = routes[0]
        assert route.stable_id == "get"

    def test_route_path_extraction(self, tmp_path: Path) -> None:
        """Route path is correctly extracted to metadata."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "paths.rs"
        rs_file.write_text("""
use axum::{Router, routing::get};

fn app() -> Router {
    Router::new()
        .route("/api/v1/users/:id", get(get_user))
}

async fn get_user() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1

        route = routes[0]
        assert route.meta["route_path"] == "/api/v1/users/:id"

    def test_extract_axum_routes_directly(self, tmp_path: Path) -> None:
        """Tests _extract_axum_routes function directly."""
        from hypergumbo.analyze.rust import (
            _extract_axum_routes,
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
        rs_file.write_text("""
fn app() -> Router {
    Router::new().route("/test", get(handler))
}
""")

        source = rs_file.read_bytes()
        tree = parser.parse(source)

        routes = _extract_axum_routes(tree.root_node, source, rs_file, run)

        assert len(routes) == 1
        assert routes[0].name == "handler"
        assert routes[0].kind == "route"
        assert routes[0].stable_id == "get"

    def test_axum_route_read_error_handled(self, tmp_path: Path) -> None:
        """Axum route extraction handles read errors in analyze_rust."""
        from hypergumbo.analyze.rust import analyze_rust, is_rust_tree_sitter_available

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        rs_file = tmp_path / "routes.rs"
        rs_file.write_text("""
fn app() -> Router {
    Router::new().route("/", get(handler))
}
async fn handler() {}
""")

        # The file is read twice - once for symbols, once for routes
        # We want the second read (for routes) to fail
        call_count = [0]
        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self):
            call_count[0] += 1
            if call_count[0] <= 2:  # Allow symbol extraction
                return original_read_bytes(self)
            raise OSError("Read failed")

        with patch.object(Path, "read_bytes", mock_read_bytes):
            result = analyze_rust(tmp_path)

        # Should not crash, just skip route extraction for that file
        assert result.run is not None

    def test_no_routes_in_non_axum_code(self, tmp_path: Path) -> None:
        """No routes detected in code without Axum patterns."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "plain.rs"
        rs_file.write_text("""
fn main() {
    let x = get_value();
    println!("{}", x);
}

fn get_value() -> i32 {
    42
}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 0


class TestActixWebRouteDetection:
    """Tests for Actix-web route handler detection."""

    def test_detects_get_attribute(self, tmp_path: Path) -> None:
        """Detects #[get("/path")] attribute pattern."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "main.rs"
        rs_file.write_text("""
use actix_web::{get, App, HttpServer};

#[get("/")]
async fn index() -> &'static str {
    "Hello, World!"
}

#[get("/users")]
async fn list_users() -> &'static str {
    "[]"
}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        assert "index" in route_names
        assert "list_users" in route_names

    def test_detects_post_attribute(self, tmp_path: Path) -> None:
        """Detects #[post("/path")] attribute pattern."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "api.rs"
        rs_file.write_text("""
use actix_web::{post, web, HttpResponse};

#[post("/users")]
async fn create_user(body: web::Json<User>) -> HttpResponse {
    HttpResponse::Created().finish()
}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "create_user"
        assert routes[0].meta["http_method"] == "POST"
        assert routes[0].meta["route_path"] == "/users"

    def test_detects_multiple_http_methods(self, tmp_path: Path) -> None:
        """Detects various HTTP method attributes."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "crud.rs"
        rs_file.write_text("""
use actix_web::{get, post, put, delete};

#[get("/items")]
async fn list_items() {}

#[post("/items")]
async fn create_item() {}

#[put("/items/{id}")]
async fn update_item() {}

#[delete("/items/{id}")]
async fn delete_item() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        assert "list_items" in route_names
        assert "create_item" in route_names
        assert "update_item" in route_names
        assert "delete_item" in route_names

        # Check HTTP methods
        http_methods = {s.meta["http_method"] for s in routes if s.meta}
        assert "GET" in http_methods
        assert "POST" in http_methods
        assert "PUT" in http_methods
        assert "DELETE" in http_methods

    def test_detects_qualified_attribute(self, tmp_path: Path) -> None:
        """Detects #[actix_web::get("/path")] qualified pattern."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "qualified.rs"
        rs_file.write_text("""
#[actix_web::get("/api/health")]
async fn health_check() -> &'static str {
    "OK"
}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "health_check"
        assert routes[0].meta["route_path"] == "/api/health"

    def test_actix_route_has_stable_id(self, tmp_path: Path) -> None:
        """Actix-web route symbols have stable_id set to HTTP method."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "stable.rs"
        rs_file.write_text("""
use actix_web::post;

#[post("/submit")]
async fn submit_form() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].stable_id == "post"

    def test_extract_actix_routes_directly(self, tmp_path: Path) -> None:
        """Tests _extract_actix_routes function directly."""
        from hypergumbo.analyze.rust import (
            _extract_actix_routes,
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
        rs_file.write_text("""
#[get("/test")]
async fn test_handler() {}
""")

        source = rs_file.read_bytes()
        tree = parser.parse(source)

        routes = _extract_actix_routes(tree.root_node, source, rs_file, run)

        assert len(routes) == 1
        assert routes[0].name == "test_handler"
        assert routes[0].kind == "route"
        assert routes[0].stable_id == "get"

    def test_actix_with_path_params(self, tmp_path: Path) -> None:
        """Actix-web routes with path parameters are detected."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "params.rs"
        rs_file.write_text("""
use actix_web::get;

#[get("/users/{user_id}/posts/{post_id}")]
async fn get_user_post() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].meta["route_path"] == "/users/{user_id}/posts/{post_id}"

    def test_mixed_axum_and_actix(self, tmp_path: Path) -> None:
        """Both Axum and Actix-web routes are detected in same file."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "mixed.rs"
        rs_file.write_text("""
use actix_web::get;
use axum::routing::post;

// Actix-web style
#[get("/actix")]
async fn actix_handler() {}

// Axum style
fn app() -> Router {
    Router::new().route("/axum", post(axum_handler))
}

async fn axum_handler() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]
        route_names = [s.name for s in routes]

        # Both should be detected
        assert "actix_handler" in route_names
        assert "axum_handler" in route_names

    def test_rocket_style_multi_param_attribute(self, tmp_path: Path) -> None:
        """Rocket-style attributes with extra params extract path correctly."""
        from hypergumbo.analyze.rust import analyze_rust

        rs_file = tmp_path / "rocket.rs"
        rs_file.write_text("""
use rocket::{get, post};

#[post("/submit", data = "<form>")]
async fn submit_form() {}

#[get("/data", format = "json")]
async fn get_json() {}

#[get("/ranked", rank = 2)]
async fn ranked_handler() {}
""")

        result = analyze_rust(tmp_path)


        routes = [s for s in result.symbols if s.kind == "route"]

        # Check that paths are extracted correctly (not including extra params)
        paths = {s.meta["route_path"] for s in routes if s.meta}
        assert "/submit" in paths
        assert "/data" in paths
        assert "/ranked" in paths

        # Ensure no malformed paths like "/submit", data = "<form>"
        for route in routes:
            if route.meta:
                assert "data =" not in route.meta["route_path"]
                assert "format =" not in route.meta["route_path"]
                assert "rank =" not in route.meta["route_path"]


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
        from hypergumbo.analyze.rust import analyze_rust

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
        from hypergumbo.analyze.rust import analyze_rust

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
        from hypergumbo.analyze.rust import analyze_rust

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
        from hypergumbo.analyze.rust import analyze_rust

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
        from hypergumbo.analyze.rust import analyze_rust

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
        from hypergumbo.analyze.rust import analyze_rust

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
        from hypergumbo.analyze.rust import analyze_rust

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
