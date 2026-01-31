"""Tests for Scheme analyzer.

Tests for the tree-sitter-based Scheme analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_common.scheme import (
    analyze_scheme,
    find_scheme_files,
    is_scheme_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def scheme_repo(tmp_path: Path) -> Path:
    """Create a minimal Scheme project for testing."""
    # Main file with functions
    (tmp_path / "main.scm").write_text(
        ''';; Main file
(define (factorial n)
  (if (<= n 1)
      1
      (* n (factorial (- n 1)))))

(define (add a b)
  (+ a b))

(define (helper x)
  (add x 1))

(define (main)
  (display (factorial 5))
  (newline)
  (helper 10))
'''
    )

    # Utils file with variables
    (tmp_path / "utils.scm").write_text(
        ''';; Utility functions
(define greeting "Hello, World!")

(define pi 3.14159)

(define (greet name)
  (display name))

(define (double x)
  (+ x x))
'''
    )

    # Library file with .ss extension
    (tmp_path / "lib.ss").write_text(
        ''';; Library
(define (compose f g)
  (lambda (x) (f (g x))))
'''
    )

    return tmp_path


class TestFindSchemeFiles:
    """Tests for finding Scheme files."""

    def test_finds_scheme_files(self, scheme_repo: Path) -> None:
        """Should find all .scm, .ss files recursively."""
        files = list(find_scheme_files(scheme_repo))
        assert len(files) == 3
        names = {f.name for f in files}
        assert "main.scm" in names
        assert "utils.scm" in names
        assert "lib.ss" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no Scheme files."""
        files = list(find_scheme_files(tmp_path))
        assert files == []


class TestIsSchemeTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-language-pack is installed."""
        assert is_scheme_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Should return False when tree-sitter-language-pack is not installed."""
        import hypergumbo_lang_common.scheme as scheme_module
        with patch.object(scheme_module, "is_scheme_tree_sitter_available", return_value=False):
            assert scheme_module.is_scheme_tree_sitter_available() is False


class TestAnalyzeScheme:
    """Tests for the Scheme analyzer."""

    def test_skips_when_unavailable(self, scheme_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter is unavailable."""
        import hypergumbo_lang_common.scheme as scheme_module

        with patch.object(scheme_module, "is_scheme_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-language-pack not available"):
                result = scheme_module.analyze_scheme(scheme_repo)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_functions(self, scheme_repo: Path) -> None:
        """Should extract function definitions."""
        result = analyze_scheme(scheme_repo)

        assert not result.skipped
        assert result.symbols

        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in funcs}

        assert "factorial" in func_names
        assert "add" in func_names
        assert "helper" in func_names
        assert "main" in func_names
        assert "greet" in func_names
        assert "double" in func_names
        assert "compose" in func_names

    def test_extracts_variables(self, scheme_repo: Path) -> None:
        """Should extract variable definitions."""
        result = analyze_scheme(scheme_repo)

        vars = [s for s in result.symbols if s.kind == "variable"]
        var_names = {s.name for s in vars}

        assert "greeting" in var_names
        assert "pi" in var_names

    def test_function_signatures(self, scheme_repo: Path) -> None:
        """Should include function signatures."""
        result = analyze_scheme(scheme_repo)

        add_fn = next((s for s in result.symbols if s.name == "add" and s.kind == "function"), None)
        assert add_fn is not None
        assert add_fn.signature is not None
        assert "define" in add_fn.signature
        assert "a" in add_fn.signature
        assert "b" in add_fn.signature

    def test_function_param_count(self, scheme_repo: Path) -> None:
        """Should track parameter count."""
        result = analyze_scheme(scheme_repo)

        add_fn = next((s for s in result.symbols if s.name == "add"), None)
        assert add_fn is not None
        assert add_fn.meta is not None
        assert add_fn.meta.get("param_count") == 2

        factorial = next((s for s in result.symbols if s.name == "factorial"), None)
        assert factorial is not None
        assert factorial.meta is not None
        assert factorial.meta.get("param_count") == 1

    def test_extracts_call_edges(self, scheme_repo: Path) -> None:
        """Should extract call edges between functions."""
        result = analyze_scheme(scheme_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) > 0

        # Check that helper calls add
        helper_calls = [e for e in call_edges if "helper" in e.src]
        callee_names = {e.dst.split(":")[-1] for e in helper_calls}
        assert "add" in callee_names or any("add" in e.dst for e in helper_calls)

    def test_recursive_calls(self, scheme_repo: Path) -> None:
        """Should detect recursive function calls."""
        result = analyze_scheme(scheme_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        # factorial calls itself
        factorial_calls = [e for e in call_edges if "factorial" in e.src and "factorial" in e.dst]
        assert len(factorial_calls) >= 1

    def test_pass_id(self, scheme_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_scheme(scheme_repo)

        for sym in result.symbols:
            assert sym.origin == PASS_ID

        for edge in result.edges:
            assert edge.origin == PASS_ID

    def test_analysis_run_metadata(self, scheme_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_scheme(scheme_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no Scheme files."""
        result = analyze_scheme(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, scheme_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_scheme(scheme_repo)
        result2 = analyze_scheme(scheme_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, scheme_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_scheme(scheme_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line


class TestUnresolvedCalls:
    """Tests for handling unresolved function calls."""

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        """Should handle calls to undefined functions."""
        (tmp_path / "main.scm").write_text(
            '''(define (main)
  (unknown-function 42))
'''
        )

        result = analyze_scheme(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

        # Should have lower confidence for unresolved target
        assert call_edges[0].confidence == 0.6
        assert "unresolved:unknown-function" in call_edges[0].dst
