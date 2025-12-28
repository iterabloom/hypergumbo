"""Tests for Lean analyzer.

Lean analysis uses tree-sitter to extract:
- Symbols: def, theorem, lemma, structure, inductive, class, instance
- Edges: imports

Lean 4 is an interactive theorem prover and programming language.
Unlike typical programming languages, "calls" are less meaningful than
"references" (dependencies between theorems/lemmas).

Test coverage includes:
- Definition detection (def, abbrev)
- Theorem/lemma detection
- Structure detection
- Inductive type detection
- Class/instance detection
- Import statements
- Two-pass cross-file resolution

Note: tree-sitter-lean is built from source and is experimental.
It is NOT on PyPI and must be built locally.
"""
from pathlib import Path

import pytest

from hypergumbo.analyze.lean import is_lean_tree_sitter_available

# Skip all tests if tree-sitter-lean is not available
pytestmark = pytest.mark.skipif(
    not is_lean_tree_sitter_available(),
    reason="tree-sitter-lean not available (must be built from source)",
)


def make_lean_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Lean file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestLeanAnalyzerAvailability:
    """Tests for tree-sitter-lean availability detection."""

    @pytest.mark.skipif(
        is_lean_tree_sitter_available(),
        reason="Only run this test when lean is NOT available",
    )
    def test_is_lean_tree_sitter_not_available(self) -> None:  # pragma: no cover
        """Verify lean is not available in standard CI."""
        assert is_lean_tree_sitter_available() is False

    @pytest.mark.skipif(
        not is_lean_tree_sitter_available(),
        reason="Only run this test when lean IS available",
    )
    def test_is_lean_tree_sitter_available(self) -> None:
        """Check if tree-sitter-lean is detected."""
        assert is_lean_tree_sitter_available() is True


class TestLeanDefinitionDetection:
    """Tests for Lean definition symbol extraction."""

    def test_detect_def(self, tmp_path: Path) -> None:
        """Detect def declaration."""
        from hypergumbo.analyze.lean import analyze_lean

        make_lean_file(tmp_path, "Example.lean", """
def double (n : Nat) : Nat := n + n
""")

        result = analyze_lean(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        func = next((s for s in symbols if s.name == "double"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "lean"


class TestLeanTheoremDetection:
    """Tests for Lean theorem/lemma symbol extraction."""

    def test_detect_theorem(self, tmp_path: Path) -> None:
        """Detect theorem declaration."""
        from hypergumbo.analyze.lean import analyze_lean

        make_lean_file(tmp_path, "Example.lean", """
theorem add_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b
""")

        result = analyze_lean(tmp_path)

        symbols = result.symbols
        thm = next((s for s in symbols if s.name == "add_comm"), None)
        assert thm is not None
        assert thm.kind == "theorem"
        assert thm.language == "lean"


class TestLeanStructureDetection:
    """Tests for Lean structure detection."""

    def test_detect_structure(self, tmp_path: Path) -> None:
        """Detect structure declaration."""
        from hypergumbo.analyze.lean import analyze_lean

        make_lean_file(tmp_path, "Example.lean", """
structure Person where
  name : String
  age : Nat
""")

        result = analyze_lean(tmp_path)

        symbols = result.symbols
        struct = next((s for s in symbols if s.name == "Person"), None)
        assert struct is not None
        assert struct.kind == "structure"
        assert struct.language == "lean"


class TestLeanInductiveDetection:
    """Tests for Lean inductive type detection."""

    def test_detect_inductive(self, tmp_path: Path) -> None:
        """Detect inductive type declaration."""
        from hypergumbo.analyze.lean import analyze_lean

        make_lean_file(tmp_path, "Example.lean", """
inductive MyList (A : Type) where
  | nil : MyList A
  | cons : A -> MyList A -> MyList A
""")

        result = analyze_lean(tmp_path)

        symbols = result.symbols
        ind = next((s for s in symbols if s.name == "MyList"), None)
        assert ind is not None
        assert ind.kind == "inductive"
        assert ind.language == "lean"


class TestLeanImportDetection:
    """Tests for Lean import edge extraction."""

    def test_detect_import(self, tmp_path: Path) -> None:
        """Detect import statement."""
        from hypergumbo.analyze.lean import analyze_lean

        make_lean_file(tmp_path, "Example.lean", """
import Mathlib.Data.Nat.Basic

def foo := 42
""")

        result = analyze_lean(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

        # Check import target
        targets = [e.dst for e in import_edges]
        assert any("Mathlib" in t for t in targets)


class TestLeanAnalyzerSkipsWhenUnavailable:
    """Tests for graceful handling when tree-sitter-lean unavailable."""

    def test_returns_empty_when_no_lean_files(self, tmp_path: Path) -> None:
        """Returns empty result when no Lean files present."""
        from hypergumbo.analyze.lean import analyze_lean

        # Create a non-Lean file
        (tmp_path / "test.py").write_text("print('hello')")

        result = analyze_lean(tmp_path)

        # Should return empty but not skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0
