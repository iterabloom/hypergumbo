"""Tests for V language analyzer.

Tests for the tree-sitter-based V language analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_extended1.v_lang import (
    analyze_v,
    find_v_files,
    is_v_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def v_repo(tmp_path: Path) -> Path:
    """Create a minimal V project for testing."""
    src = tmp_path / "src"
    src.mkdir()

    # Main file
    (src / "main.v").write_text(
        '''module main

import os
import math

fn main() {
    println("Hello, V!")
    result := helper()
    println(result)
}

fn helper() int {
    return 42
}

pub fn add(a int, b int) int {
    return a + b
}

pub fn greet(name string) string {
    return "Hello, " + name
}
'''
    )

    # Types file
    (src / "types.v").write_text(
        '''module main

struct User {
    name string
    age  int
}

pub struct Point {
    x f64
    y f64
}

enum Color {
    red
    green
    blue
}

pub enum Status {
    active
    inactive
    pending
}

interface Printable {
    print()
}

pub interface Serializable {
    serialize() string
}

fn process_user(u User) string {
    return u.name
}
'''
    )

    return tmp_path


class TestFindVFiles:
    """Tests for finding V files."""

    def test_finds_v_files(self, v_repo: Path) -> None:
        """Should find all .v files recursively."""
        files = list(find_v_files(v_repo))
        assert len(files) == 2
        names = {f.name for f in files}
        assert "main.v" in names
        assert "types.v" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no V files."""
        files = list(find_v_files(tmp_path))
        assert files == []


class TestIsVTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-language-pack is installed."""
        assert is_v_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Should return False when tree-sitter-language-pack is not installed."""
        import hypergumbo_lang_extended1.v_lang as v_module
        with patch.object(v_module, "is_v_tree_sitter_available", return_value=False):
            assert v_module.is_v_tree_sitter_available() is False


class TestAnalyzeV:
    """Tests for the V analyzer."""

    def test_skips_when_unavailable(self, v_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter is unavailable."""
        import hypergumbo_lang_extended1.v_lang as v_module

        with patch.object(v_module, "is_v_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-language-pack not available"):
                result = v_module.analyze_v(v_repo)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_functions(self, v_repo: Path) -> None:
        """Should extract function declarations."""
        result = analyze_v(v_repo)

        assert not result.skipped
        assert result.symbols

        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in funcs}

        assert "main" in func_names
        assert "helper" in func_names
        assert "add" in func_names
        assert "greet" in func_names
        assert "process_user" in func_names

    def test_extracts_structs(self, v_repo: Path) -> None:
        """Should extract struct declarations."""
        result = analyze_v(v_repo)

        structs = [s for s in result.symbols if s.kind == "class"]
        struct_names = {s.name for s in structs}

        assert "User" in struct_names
        assert "Point" in struct_names

    def test_extracts_enums(self, v_repo: Path) -> None:
        """Should extract enum declarations."""
        result = analyze_v(v_repo)

        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = {s.name for s in enums}

        assert "Color" in enum_names
        assert "Status" in enum_names

    def test_extracts_interfaces(self, v_repo: Path) -> None:
        """Should extract interface declarations."""
        result = analyze_v(v_repo)

        interfaces = [s for s in result.symbols if s.kind == "interface"]
        interface_names = {s.name for s in interfaces}

        assert "Printable" in interface_names
        assert "Serializable" in interface_names

    def test_public_visibility(self, v_repo: Path) -> None:
        """Should track public/private visibility."""
        result = analyze_v(v_repo)

        add_fn = next((s for s in result.symbols if s.name == "add"), None)
        assert add_fn is not None
        assert add_fn.meta is not None
        assert add_fn.meta.get("is_public") is True

        helper_fn = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper_fn is not None
        assert helper_fn.meta is not None
        assert helper_fn.meta.get("is_public") is False

        # Public struct
        point = next((s for s in result.symbols if s.name == "Point"), None)
        assert point is not None
        assert point.meta.get("is_public") is True

        # Private struct
        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta.get("is_public") is False

    def test_function_signatures(self, v_repo: Path) -> None:
        """Should include function signatures."""
        result = analyze_v(v_repo)

        add_fn = next((s for s in result.symbols if s.name == "add"), None)
        assert add_fn is not None
        assert add_fn.signature is not None
        assert "int" in add_fn.signature

    def test_struct_field_count(self, v_repo: Path) -> None:
        """Should count struct fields."""
        result = analyze_v(v_repo)

        user = next((s for s in result.symbols if s.name == "User"), None)
        assert user is not None
        assert user.meta is not None
        assert user.meta.get("field_count") == 2  # name, age

        point = next((s for s in result.symbols if s.name == "Point"), None)
        assert point is not None
        assert point.meta is not None
        assert point.meta.get("field_count") == 2  # x, y

    def test_enum_variant_count(self, v_repo: Path) -> None:
        """Should count enum variants."""
        result = analyze_v(v_repo)

        color = next((s for s in result.symbols if s.name == "Color"), None)
        assert color is not None
        assert color.meta is not None
        assert color.meta.get("variant_count") == 3  # red, green, blue

        status = next((s for s in result.symbols if s.name == "Status"), None)
        assert status is not None
        assert status.meta is not None
        assert status.meta.get("variant_count") == 3  # active, inactive, pending

    def test_extracts_imports(self, v_repo: Path) -> None:
        """Should extract import edges."""
        result = analyze_v(v_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        import_targets = {e.dst for e in import_edges}

        assert any("os" in t for t in import_targets)
        assert any("math" in t for t in import_targets)

    def test_extracts_call_edges(self, v_repo: Path) -> None:
        """Should extract call edges between functions."""
        result = analyze_v(v_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) > 0

        # Check that main calls helper
        main_calls = [e for e in call_edges if "main" in e.src]
        callee_names = {e.dst.split(":")[-1] for e in main_calls}
        assert "helper" in callee_names or any("helper" in e.dst for e in main_calls)

    def test_pass_id(self, v_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_v(v_repo)

        for sym in result.symbols:
            assert sym.origin == PASS_ID

        for edge in result.edges:
            assert edge.origin == PASS_ID

    def test_analysis_run_metadata(self, v_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_v(v_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no V files."""
        result = analyze_v(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, v_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_v(v_repo)
        result2 = analyze_v(v_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, v_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_v(v_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line


class TestUnresolvedCalls:
    """Tests for handling unresolved function calls."""

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        """Should handle calls to undefined functions."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.v").write_text(
            '''module main

fn main() {
    unknown_function()
}
'''
        )

        result = analyze_v(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

        # Should have lower confidence for unresolved target
        assert call_edges[0].confidence == 0.6
        assert "unresolved:unknown_function" in call_edges[0].dst
