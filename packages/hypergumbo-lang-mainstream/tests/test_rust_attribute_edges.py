"""Tests for Rust attribute edge detection (INV-012 scope expansion).

Verifies that attribute applications create edges in the call graph for Rust.
"""
import pytest
from pathlib import Path

from hypergumbo_lang_mainstream.rust import analyze_rust, is_rust_tree_sitter_available


pytestmark = pytest.mark.skipif(
    not is_rust_tree_sitter_available(),
    reason="tree-sitter-rust not available"
)


class TestRustAttributeEdges:
    """Test attribute edge detection for Rust."""

    def test_function_attribute_creates_edge(self, tmp_path: Path) -> None:
        """A #[attribute] on a function creates a decorated_by edge."""
        code = '''
fn custom_attr() {}

#[custom_attr]
fn handler() {
    println!("handling");
}
'''
        rs_file = tmp_path / "main.rs"
        rs_file.write_text(code)

        result = analyze_rust(tmp_path)

        # Find the decorated_by edge
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge"

        # The edge should be from handler to custom_attr
        edge = decorated_by_edges[0]
        assert "handler" in edge.src
        assert "custom_attr" in edge.dst

    def test_multiple_attributes_create_multiple_edges(self, tmp_path: Path) -> None:
        """Multiple attributes create multiple edges."""
        code = '''
fn attr1() {}
fn attr2() {}

#[attr1]
#[attr2]
fn handler() {}
'''
        rs_file = tmp_path / "main.rs"
        rs_file.write_text(code)

        result = analyze_rust(tmp_path)

        # Find decorated_by edges for handler
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "handler" in e.src
        ]

        assert len(decorated_by_edges) >= 2, "Expected two decorated_by edges for stacked attributes"

    def test_struct_derive_creates_edge(self, tmp_path: Path) -> None:
        """A #[derive(...)] on a struct creates a decorated_by edge."""
        code = '''
#[derive(Debug)]
struct User {
    name: String,
}
'''
        rs_file = tmp_path / "models.rs"
        rs_file.write_text(code)

        result = analyze_rust(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        # Should have edge from User to derive
        assert len(decorated_by_edges) >= 1, "Expected at least one decorated_by edge for derive"

        edge = decorated_by_edges[0]
        assert "User" in edge.src
        # derive is the attribute name
        assert "derive" in edge.dst

    def test_actix_web_attribute_creates_edge(self, tmp_path: Path) -> None:
        """A #[get("/path")] Actix-web attribute creates a decorated_by edge."""
        code = '''
#[get("/users")]
async fn list_users() -> String {
    "[]".to_string()
}
'''
        rs_file = tmp_path / "handlers.rs"
        rs_file.write_text(code)

        result = analyze_rust(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected decorated_by edge for actix-web attribute"

        edge = decorated_by_edges[0]
        assert "list_users" in edge.src
        assert "get" in edge.dst

    def test_qualified_attribute_creates_edge(self, tmp_path: Path) -> None:
        """A fully qualified #[actix_web::get("/path")] creates a decorated_by edge."""
        code = '''
#[actix_web::get("/api")]
async fn api_handler() -> String {
    "api".to_string()
}
'''
        rs_file = tmp_path / "api.rs"
        rs_file.write_text(code)

        result = analyze_rust(tmp_path)

        # Find decorated_by edges
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by"
        ]

        assert len(decorated_by_edges) >= 1, "Expected decorated_by edge for qualified attribute"

        edge = decorated_by_edges[0]
        assert "api_handler" in edge.src
        # Qualified name includes path
        assert "actix_web::get" in edge.dst or "get" in edge.dst

    def test_enum_attribute_creates_edge(self, tmp_path: Path) -> None:
        """A #[derive(...)] on an enum creates a decorated_by edge."""
        code = '''
#[derive(Clone)]
enum Status {
    Active,
    Inactive,
}
'''
        rs_file = tmp_path / "types.rs"
        rs_file.write_text(code)

        result = analyze_rust(tmp_path)

        # Find decorated_by edges for Status
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "Status" in e.src
        ]

        assert len(decorated_by_edges) >= 1, "Expected decorated_by edge for enum derive"

    def test_trait_attribute_creates_edge(self, tmp_path: Path) -> None:
        """A #[attribute] on a trait creates a decorated_by edge."""
        code = '''
fn deprecated() {}

#[deprecated]
trait OldApi {
    fn old_method(&self);
}
'''
        rs_file = tmp_path / "traits.rs"
        rs_file.write_text(code)

        result = analyze_rust(tmp_path)

        # Find decorated_by edges for OldApi
        decorated_by_edges = [
            e for e in result.edges
            if e.edge_type == "decorated_by" and "OldApi" in e.src
        ]

        assert len(decorated_by_edges) >= 1, "Expected decorated_by edge for trait attribute"


class TestRustAttributeEdgesDefensiveBranches:
    """Tests for defensive branches in Rust attribute edge extraction."""

    def test_symbol_with_meta_but_no_annotations(self, tmp_path: Path) -> None:
        """Symbol with meta but no annotations key doesn't create edges."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_attribute_edges,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun, Symbol, Span

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        run = AnalysisRun.create(pass_id="test", version="test")

        # Create a symbol with meta but no annotations key
        symbol = Symbol(
            id="rust:test.rs:1-5:foo:function",
            name="foo",
            kind="function",
            language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test",
            origin_run_id=run.execution_id,
            meta={"other_key": "value"},  # meta exists but no "annotations"
        )

        edges = _extract_attribute_edges([symbol], {}, run)

        assert edges == [], "Should not create edges for symbol without annotations"

    def test_symbol_with_empty_annotations_list(self, tmp_path: Path) -> None:
        """Symbol with empty annotations list doesn't create edges."""
        from hypergumbo_lang_mainstream.rust import (
            _extract_attribute_edges,
            is_rust_tree_sitter_available,
        )
        from hypergumbo_core.ir import AnalysisRun, Symbol, Span

        if not is_rust_tree_sitter_available():
            pytest.skip("tree-sitter-rust not available")

        run = AnalysisRun.create(pass_id="test", version="test")

        # Create a symbol with empty annotations list
        symbol = Symbol(
            id="rust:test.rs:1-5:bar:function",
            name="bar",
            kind="function",
            language="rust",
            path="test.rs",
            span=Span(start_line=1, end_line=5, start_col=0, end_col=1),
            origin="test",
            origin_run_id=run.execution_id,
            meta={"annotations": []},  # Empty list
        )

        edges = _extract_attribute_edges([symbol], {}, run)

        assert edges == [], "Should not create edges for symbol with empty annotations"
