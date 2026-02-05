"""Branch coverage tests for scheme.py analyzer.

Tests specific branch paths in the Scheme analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Define form detection
- Function vs variable extraction
- Call edge extraction
- Enclosing function detection
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.scheme import (
    _is_define_form,
    _is_function_define,
    _make_stable_id,
    analyze_scheme,
    find_scheme_files,
)


def make_scheme_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Scheme file with given content."""
    (tmp_path / name).write_text(content)


class TestSchemeHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_stable_id_format(self, tmp_path: Path) -> None:
        """Test stable ID format."""
        path = tmp_path / "test.scm"
        path.touch()
        stable_id = _make_stable_id(path, tmp_path, "add", "fn")
        assert stable_id == "scheme:test.scm:add:fn"


class TestDefineForms:
    """Branch coverage for define form detection."""

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function definitions."""
        make_scheme_file(tmp_path, "math.scm", """
(define (add x y)
  (+ x y))

(define (sub x y)
  (- x y))

(define (mul x y)
  (* x y))
""")
        result = analyze_scheme(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "add" in names
        assert "sub" in names
        assert "mul" in names

    def test_variable_definition(self, tmp_path: Path) -> None:
        """Test variable (non-function) definitions."""
        make_scheme_file(tmp_path, "constants.scm", """
(define pi 3.14159)
(define greeting "Hello")
(define answer 42)
""")
        result = analyze_scheme(tmp_path)
        assert not result.skipped

        variables = [s for s in result.symbols if s.kind == "variable"]
        names = [v.name for v in variables]
        assert "pi" in names
        assert "greeting" in names
        assert "answer" in names


class TestFunctionSignatures:
    """Branch coverage for function signature extraction."""

    def test_function_with_multiple_params(self, tmp_path: Path) -> None:
        """Test function with multiple parameters."""
        make_scheme_file(tmp_path, "multi.scm", """
(define (process a b c d)
  (+ a b c d))
""")
        result = analyze_scheme(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "process"]
        assert len(funcs) == 1
        assert funcs[0].meta is not None
        assert funcs[0].meta.get("param_count") == 4

    def test_function_with_no_params(self, tmp_path: Path) -> None:
        """Test function with no parameters."""
        make_scheme_file(tmp_path, "noargs.scm", """
(define (get-answer)
  42)
""")
        result = analyze_scheme(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "get-answer"]
        assert len(funcs) == 1
        assert funcs[0].meta.get("param_count") == 0


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_scheme_file(tmp_path, "app.scm", """
(define (helper x)
  (* x 2))

(define (main)
  (helper 21))
""")
        result = analyze_scheme(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # main calls helper
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_unresolved_call(self, tmp_path: Path) -> None:
        """Test call to undefined function creates unresolved edge."""
        make_scheme_file(tmp_path, "unresolved.scm", """
(define (caller)
  (unknown-fn 42))
""")
        result = analyze_scheme(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        unresolved = [e for e in call_edges if "unresolved" in e.dst]
        assert len(unresolved) >= 1

    def test_call_chain(self, tmp_path: Path) -> None:
        """Test chain of function calls."""
        make_scheme_file(tmp_path, "chain.scm", """
(define (step1 x) (+ x 1))
(define (step2 x) (step1 (step1 x)))
(define (step3 x) (step2 x))
""")
        result = analyze_scheme(tmp_path)
        assert not result.skipped

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        step1_calls = [e for e in call_edges if "step1" in e.dst]
        assert len(step1_calls) >= 2  # step2 calls step1 twice


class TestFindSchemeFiles:
    """Branch coverage for file discovery."""

    def test_finds_scm_files(self, tmp_path: Path) -> None:
        """Test .scm files are discovered."""
        (tmp_path / "test.scm").write_text("(define x 1)")

        files = list(find_scheme_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".scm"

    def test_finds_ss_files(self, tmp_path: Path) -> None:
        """Test .ss files are discovered."""
        (tmp_path / "test.ss").write_text("(define x 1)")

        files = list(find_scheme_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".ss"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        lib = tmp_path / "lib" / "utils"
        lib.mkdir(parents=True)
        (lib / "core.scm").write_text("(define x 1)")

        files = list(find_scheme_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "core.scm"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test empty Scheme file."""
        make_scheme_file(tmp_path, "empty.scm", "")
        result = analyze_scheme(tmp_path)
        # Should handle gracefully
        assert not result.skipped

    def test_comment_only_file(self, tmp_path: Path) -> None:
        """Test file with only comments."""
        make_scheme_file(tmp_path, "comments.scm", """
; This is a comment
;; Another comment
""")
        result = analyze_scheme(tmp_path)
        assert not result.skipped


class TestCrossFileResolution:
    """Branch coverage for cross-file call resolution."""

    def test_two_file_resolution(self, tmp_path: Path) -> None:
        """Test call resolution across two files."""
        make_scheme_file(tmp_path, "utils.scm", """
(define (helper x)
  (* x 2))
""")
        make_scheme_file(tmp_path, "main.scm", """
(define (main)
  (helper 21))
""")
        result = analyze_scheme(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "helper" in names
        assert "main" in names

        # Call should be resolved
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        resolved = [e for e in call_edges if "unresolved" not in e.dst]
        assert len(resolved) >= 1
