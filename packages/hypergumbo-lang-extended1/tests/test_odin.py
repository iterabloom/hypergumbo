"""Tests for Odin analyzer.

Tests for the tree-sitter-based Odin analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_extended1.odin import (
    analyze_odin,
    find_odin_files,
    is_odin_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def odin_repo(tmp_path: Path) -> Path:
    """Create a minimal Odin repository for testing."""
    src = tmp_path / "src"
    src.mkdir()

    # Main file
    (src / "main.odin").write_text(
        '''package main

import "core:fmt"
import "core:math"

helper :: proc() -> int {
    return 42
}

main :: proc() {
    fmt.println("Hello, Odin!")
    result := helper()
    _ = result
}

greet :: proc(name: string) -> string {
    return fmt.tprintf("Hello, %s!", name)
}
'''
    )

    # Math module
    (src / "math_utils.odin").write_text(
        '''package main

import "core:math"

Point :: struct {
    x: f32,
    y: f32,
}

Vector :: struct {
    dx: f32,
    dy: f32,
    dz: f32,
}

Direction :: enum {
    North,
    South,
    East,
    West,
}

Result :: union {
    success: int,
    error: string,
}

calculate :: proc(a, b: int) -> int {
    return add(a, b)
}

add :: proc(a, b: int) -> int {
    return a + b
}

multiply :: proc(a, b: int) -> int {
    return a * b
}
'''
    )

    return tmp_path


class TestFindOdinFiles:
    """Tests for finding Odin files."""

    def test_finds_odin_files(self, odin_repo: Path) -> None:
        """Should find all .odin files recursively."""
        files = list(find_odin_files(odin_repo))
        assert len(files) == 2
        names = {f.name for f in files}
        assert "main.odin" in names
        assert "math_utils.odin" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no Odin files."""
        files = list(find_odin_files(tmp_path))
        assert files == []


class TestIsOdinTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when both tree-sitter and tree-sitter-odin are installed."""
        # This test runs in CI where the dependency is installed
        assert is_odin_tree_sitter_available() is True

    def test_returns_false_when_tree_sitter_missing(self) -> None:
        """Should return False when tree-sitter is not installed."""
        with patch("importlib.util.find_spec", return_value=None):
            assert is_odin_tree_sitter_available() is False

    def test_returns_false_when_odin_missing(self) -> None:
        """Should return False when tree-sitter-odin is not installed."""
        import importlib.util
        original_find_spec = importlib.util.find_spec

        def mock_find_spec(name: str) -> object:
            if name == "tree_sitter_odin":
                return None
            return original_find_spec(name)

        with patch("importlib.util.find_spec", side_effect=mock_find_spec):
            assert is_odin_tree_sitter_available() is False


class TestAnalyzeOdin:
    """Tests for the Odin analyzer."""

    def test_skips_when_unavailable(self, odin_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter-odin is unavailable."""
        import hypergumbo_lang_extended1.odin as odin_module

        with patch.object(odin_module, "is_odin_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-odin not available"):
                result = odin_module.analyze_odin(odin_repo)

        assert result.skipped is True
        assert "tree-sitter-odin" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_procedures(self, odin_repo: Path) -> None:
        """Should extract procedure declarations."""
        result = analyze_odin(odin_repo)

        assert not result.skipped
        assert result.symbols

        # Find all procedures
        procs = [s for s in result.symbols if s.kind == "function"]
        proc_names = {s.name for s in procs}

        assert "main" in proc_names
        assert "helper" in proc_names
        assert "greet" in proc_names
        assert "calculate" in proc_names
        assert "add" in proc_names
        assert "multiply" in proc_names

    def test_extracts_structs(self, odin_repo: Path) -> None:
        """Should extract struct declarations."""
        result = analyze_odin(odin_repo)

        structs = [s for s in result.symbols if s.kind == "class" and (s.meta is None or "is_union" not in s.meta)]
        struct_names = {s.name for s in structs}

        assert "Point" in struct_names
        assert "Vector" in struct_names

    def test_extracts_enums(self, odin_repo: Path) -> None:
        """Should extract enum declarations."""
        result = analyze_odin(odin_repo)

        enums = [s for s in result.symbols if s.kind == "enum"]
        enum_names = {s.name for s in enums}

        assert "Direction" in enum_names

    def test_extracts_unions(self, odin_repo: Path) -> None:
        """Should extract union declarations."""
        result = analyze_odin(odin_repo)

        unions = [s for s in result.symbols if s.meta and s.meta.get("is_union")]
        union_names = {s.name for s in unions}

        assert "Result" in union_names

    def test_extracts_imports(self, odin_repo: Path) -> None:
        """Should extract import edges."""
        result = analyze_odin(odin_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        import_targets = {e.dst for e in import_edges}

        assert any("core:fmt" in t for t in import_targets)
        assert any("core:math" in t for t in import_targets)

    def test_extracts_call_edges(self, odin_repo: Path) -> None:
        """Should extract call edges between procedures."""
        result = analyze_odin(odin_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) > 0

        # Check that main calls helper
        main_calls = [e for e in call_edges if "main" in e.src]
        callee_names = {e.dst.split(":")[-1] for e in main_calls}
        assert "helper" in callee_names or any("helper" in e.dst for e in main_calls)

    def test_symbol_metadata(self, odin_repo: Path) -> None:
        """Should include signature in procedure."""
        result = analyze_odin(odin_repo)

        # Find the add procedure
        add_proc = next((s for s in result.symbols if s.name == "add"), None)
        assert add_proc is not None
        assert add_proc.signature is not None

    def test_pass_id(self, odin_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_odin(odin_repo)

        for sym in result.symbols:
            assert sym.origin == PASS_ID

        for edge in result.edges:
            assert edge.origin == PASS_ID

    def test_analysis_run_metadata(self, odin_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_odin(odin_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no Odin files."""
        result = analyze_odin(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, odin_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_odin(odin_repo)
        result2 = analyze_odin(odin_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, odin_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_odin(odin_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line

    def test_struct_field_count(self, odin_repo: Path) -> None:
        """Should count struct fields."""
        result = analyze_odin(odin_repo)

        point = next((s for s in result.symbols if s.name == "Point"), None)
        assert point is not None
        assert point.meta is not None
        assert point.meta.get("field_count") == 2

        vector = next((s for s in result.symbols if s.name == "Vector"), None)
        assert vector is not None
        assert vector.meta is not None
        assert vector.meta.get("field_count") == 3
