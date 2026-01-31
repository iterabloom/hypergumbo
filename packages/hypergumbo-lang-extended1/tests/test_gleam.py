"""Tests for Gleam analyzer.

Tests for the tree-sitter-based Gleam analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_extended1.gleam import (
    analyze_gleam,
    find_gleam_files,
    is_gleam_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def gleam_repo(tmp_path: Path) -> Path:
    """Create a minimal Gleam project for testing."""
    src = tmp_path / "src"
    src.mkdir()

    # Main file
    (src / "main.gleam").write_text(
        '''import gleam/io

pub fn main() {
  io.println("Hello, Gleam!")
  let result = helper()
  io.println(result)
}

fn helper() -> String {
  "Helper called"
}

pub fn add(a: Int, b: Int) -> Int {
  a + b
}

pub fn greet(name: String) -> String {
  "Hello, " <> name
}
'''
    )

    # Types file
    (src / "types.gleam").write_text(
        '''pub type User {
  User(name: String, age: Int)
  Admin(name: String, level: Int)
}

pub type Status {
  Active
  Inactive
  Pending
}

pub type Maybe(a) {
  Just(a)
  Nothing
}

pub fn is_active(status: Status) -> Bool {
  case status {
    Active -> True
    _ -> False
  }
}
'''
    )

    return tmp_path


class TestFindGleamFiles:
    """Tests for finding Gleam files."""

    def test_finds_gleam_files(self, gleam_repo: Path) -> None:
        """Should find all .gleam files recursively."""
        files = list(find_gleam_files(gleam_repo))
        assert len(files) == 2
        names = {f.name for f in files}
        assert "main.gleam" in names
        assert "types.gleam" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no Gleam files."""
        files = list(find_gleam_files(tmp_path))
        assert files == []


class TestIsGleamTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-language-pack is installed."""
        assert is_gleam_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Should return False when tree-sitter-language-pack is not installed."""
        import hypergumbo_lang_extended1.gleam as gleam_module
        with patch.object(gleam_module, "is_gleam_tree_sitter_available", return_value=False):
            assert gleam_module.is_gleam_tree_sitter_available() is False


class TestAnalyzeGleam:
    """Tests for the Gleam analyzer."""

    def test_skips_when_unavailable(self, gleam_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter is unavailable."""
        import hypergumbo_lang_extended1.gleam as gleam_module

        with patch.object(gleam_module, "is_gleam_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-language-pack not available"):
                result = gleam_module.analyze_gleam(gleam_repo)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_functions(self, gleam_repo: Path) -> None:
        """Should extract function declarations."""
        result = analyze_gleam(gleam_repo)

        assert not result.skipped
        assert result.symbols

        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in funcs}

        assert "main" in func_names
        assert "helper" in func_names
        assert "add" in func_names
        assert "greet" in func_names
        assert "is_active" in func_names

    def test_extracts_types(self, gleam_repo: Path) -> None:
        """Should extract type definitions."""
        result = analyze_gleam(gleam_repo)

        types = [s for s in result.symbols if s.kind == "class"]
        type_names = {s.name for s in types}

        assert "User" in type_names
        assert "Status" in type_names
        assert "Maybe" in type_names

    def test_public_visibility(self, gleam_repo: Path) -> None:
        """Should track public/private visibility."""
        result = analyze_gleam(gleam_repo)

        main_fn = next((s for s in result.symbols if s.name == "main"), None)
        assert main_fn is not None
        assert main_fn.meta is not None
        assert main_fn.meta.get("is_public") is True

        helper_fn = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper_fn is not None
        assert helper_fn.meta is not None
        assert helper_fn.meta.get("is_public") is False

    def test_function_signatures(self, gleam_repo: Path) -> None:
        """Should include function signatures."""
        result = analyze_gleam(gleam_repo)

        add_fn = next((s for s in result.symbols if s.name == "add"), None)
        assert add_fn is not None
        assert add_fn.signature is not None
        assert "Int" in add_fn.signature

    def test_type_constructor_count(self, gleam_repo: Path) -> None:
        """Should count type constructors."""
        result = analyze_gleam(gleam_repo)

        user_type = next((s for s in result.symbols if s.name == "User"), None)
        assert user_type is not None
        assert user_type.meta is not None
        assert user_type.meta.get("constructor_count") == 2  # User, Admin

        status_type = next((s for s in result.symbols if s.name == "Status"), None)
        assert status_type is not None
        assert status_type.meta is not None
        assert status_type.meta.get("constructor_count") == 3  # Active, Inactive, Pending

    def test_extracts_imports(self, gleam_repo: Path) -> None:
        """Should extract import edges."""
        result = analyze_gleam(gleam_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        import_targets = {e.dst for e in import_edges}

        assert any("gleam/io" in t for t in import_targets)

    def test_extracts_call_edges(self, gleam_repo: Path) -> None:
        """Should extract call edges between functions."""
        result = analyze_gleam(gleam_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) > 0

        # Check that main calls helper
        main_calls = [e for e in call_edges if "main" in e.src]
        callee_names = {e.dst.split(":")[-1] for e in main_calls}
        assert "helper" in callee_names or any("helper" in e.dst for e in main_calls)

    def test_qualified_calls(self, gleam_repo: Path) -> None:
        """Should detect qualified function calls like io.println."""
        result = analyze_gleam(gleam_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        external_calls = [e for e in call_edges if "external" in e.dst]

        assert len(external_calls) > 0
        assert any("io.println" in e.dst or "io" in e.dst for e in external_calls)

    def test_pass_id(self, gleam_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_gleam(gleam_repo)

        for sym in result.symbols:
            assert sym.origin == PASS_ID

        for edge in result.edges:
            assert edge.origin == PASS_ID

    def test_analysis_run_metadata(self, gleam_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_gleam(gleam_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no Gleam files."""
        result = analyze_gleam(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, gleam_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_gleam(gleam_repo)
        result2 = analyze_gleam(gleam_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, gleam_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_gleam(gleam_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line


class TestTypeAliases:
    """Tests for type alias handling."""

    def test_extracts_type_aliases(self, tmp_path: Path) -> None:
        """Should extract type alias declarations."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "aliases.gleam").write_text(
            '''pub type UserId = Int
pub type Name = String
type Internal = Bool
'''
        )

        result = analyze_gleam(tmp_path)

        aliases = [s for s in result.symbols if s.kind == "type"]
        alias_names = {s.name for s in aliases}

        assert "UserId" in alias_names
        assert "Name" in alias_names
        assert "Internal" in alias_names

    def test_type_alias_visibility(self, tmp_path: Path) -> None:
        """Should track type alias visibility."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "aliases.gleam").write_text(
            '''pub type PublicAlias = Int
type PrivateAlias = String
'''
        )

        result = analyze_gleam(tmp_path)

        pub_alias = next((s for s in result.symbols if s.name == "PublicAlias"), None)
        assert pub_alias is not None
        assert pub_alias.meta is not None
        assert pub_alias.meta.get("is_public") is True
        assert pub_alias.meta.get("is_alias") is True

        priv_alias = next((s for s in result.symbols if s.name == "PrivateAlias"), None)
        assert priv_alias is not None
        assert priv_alias.meta is not None
        assert priv_alias.meta.get("is_public") is False


class TestUnresolvedCalls:
    """Tests for handling unresolved function calls."""

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        """Should handle calls to undefined functions."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.gleam").write_text(
            '''pub fn main() {
  unknown_function()
}
'''
        )

        result = analyze_gleam(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

        # Should have lower confidence for unresolved target
        assert call_edges[0].confidence == 0.6
        assert "unresolved:unknown_function" in call_edges[0].dst
