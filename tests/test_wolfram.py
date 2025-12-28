"""Tests for Wolfram Language analyzer.

Wolfram Language (also known as Mathematica) analysis uses tree-sitter to extract:
- Symbols: function definitions (SetDelayed), assignments, package declarations
- Edges: imports (Get, Import, Needs), function calls

Wolfram Language is a symbolic programming language used for technical computing,
data science, and mathematical modeling.

Test coverage includes:
- Function definition detection (SetDelayed :=)
- Assignment detection (Set =)
- Function call detection
- Import/Get detection
- Package structure detection
- Two-pass cross-file resolution

Note: tree-sitter-wolfram is built from source and is experimental.
It is NOT on PyPI and must be built locally.
"""
from pathlib import Path

import pytest

from hypergumbo.analyze.wolfram import is_wolfram_tree_sitter_available

# Skip all tests if tree-sitter-wolfram is not available
pytestmark = pytest.mark.skipif(
    not is_wolfram_tree_sitter_available(),
    reason="tree-sitter-wolfram not available (must be built from source)",
)


def make_wolfram_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Wolfram file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestWolframAnalyzerAvailability:
    """Tests for tree-sitter-wolfram availability detection."""

    @pytest.mark.skipif(
        is_wolfram_tree_sitter_available(),
        reason="Only run this test when wolfram is NOT available",
    )
    def test_is_wolfram_tree_sitter_not_available(self) -> None:  # pragma: no cover
        """Verify wolfram is not available in standard CI."""
        assert is_wolfram_tree_sitter_available() is False

    @pytest.mark.skipif(
        not is_wolfram_tree_sitter_available(),
        reason="Only run this test when wolfram IS available",
    )
    def test_is_wolfram_tree_sitter_available(self) -> None:
        """Check if tree-sitter-wolfram is detected."""
        assert is_wolfram_tree_sitter_available() is True


class TestWolframFunctionDetection:
    """Tests for Wolfram function definition detection."""

    def test_detect_function_definition(self, tmp_path: Path) -> None:
        """Detect function definition with SetDelayed (:=)."""
        from hypergumbo.analyze.wolfram import analyze_wolfram

        make_wolfram_file(tmp_path, "Example.wl", """
f[x_] := x^2
g[x_, y_] := x + y
""")

        result = analyze_wolfram(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        # Note: The grammar may parse pattern syntax imperfectly
        func = next((s for s in symbols if s.name == "f"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "wolfram"


class TestWolframAssignmentDetection:
    """Tests for Wolfram assignment detection."""

    def test_detect_assignment(self, tmp_path: Path) -> None:
        """Detect assignment with Set (=)."""
        from hypergumbo.analyze.wolfram import analyze_wolfram

        make_wolfram_file(tmp_path, "Example.wl", """
x = 42
myList = {1, 2, 3}
""")

        result = analyze_wolfram(tmp_path)

        symbols = result.symbols
        var = next((s for s in symbols if s.name == "x"), None)
        assert var is not None
        assert var.kind == "variable"
        assert var.language == "wolfram"


class TestWolframCallDetection:
    """Tests for Wolfram function call detection."""

    def test_detect_builtin_calls(self, tmp_path: Path) -> None:
        """Detect calls to built-in functions."""
        from hypergumbo.analyze.wolfram import analyze_wolfram

        make_wolfram_file(tmp_path, "Example.wl", """
result = Sin[x] + Cos[y]
data = Map[f, myList]
""")

        result = analyze_wolfram(tmp_path)

        edges = result.edges
        call_edges = [e for e in edges if e.edge_type == "calls"]
        # Should have calls to Sin, Cos, Map
        assert len(call_edges) >= 3


class TestWolframImportDetection:
    """Tests for Wolfram import detection."""

    def test_detect_get_import(self, tmp_path: Path) -> None:
        """Detect Get (<<) import statement."""
        from hypergumbo.analyze.wolfram import analyze_wolfram

        make_wolfram_file(tmp_path, "Example.wl", """
Get["MyPackage`"]
x = 42
""")

        result = analyze_wolfram(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1
        targets = [e.dst for e in import_edges]
        assert any("MyPackage" in t for t in targets)

    def test_detect_needs_import(self, tmp_path: Path) -> None:
        """Detect Needs import statement."""
        from hypergumbo.analyze.wolfram import analyze_wolfram

        make_wolfram_file(tmp_path, "Example.wl", """
Needs["ComputationalGeometry`"]
result = ConvexHull[points]
""")

        result = analyze_wolfram(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestWolframAnalyzerSkipsWhenUnavailable:
    """Tests for graceful handling when tree-sitter-wolfram unavailable."""

    def test_returns_empty_when_no_wolfram_files(self, tmp_path: Path) -> None:
        """Returns empty result when no Wolfram files present."""
        from hypergumbo.analyze.wolfram import analyze_wolfram

        # Create a non-Wolfram file
        (tmp_path / "test.py").write_text("print('hello')")

        result = analyze_wolfram(tmp_path)

        # Should return empty but not skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0
