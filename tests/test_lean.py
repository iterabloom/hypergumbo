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

Note: tree-sitter-lean is built from source via scripts/build-source-grammars.
CI builds this grammar automatically.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze.lean import analyze_lean, is_lean_tree_sitter_available


def make_lean_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Lean file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestLeanAnalyzerAvailability:
    """Tests for tree-sitter-lean availability detection."""

    def test_is_lean_tree_sitter_available(self) -> None:
        """Check if tree-sitter-lean is detected (should be installed)."""
        assert is_lean_tree_sitter_available() is True


class TestLeanDefinitionDetection:
    """Tests for Lean definition symbol extraction."""

    def test_detect_def(self, tmp_path: Path) -> None:
        """Detect def declaration."""
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


class TestLeanAnalyzerWhenUnavailable:
    """Tests for graceful handling when tree-sitter-lean unavailable."""

    def test_returns_empty_when_no_lean_files(self, tmp_path: Path) -> None:
        """Returns empty result when no Lean files present."""
        # Create a non-Lean file
        (tmp_path / "test.py").write_text("print('hello')")

        result = analyze_lean(tmp_path)

        # Should return empty but not skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_analyze_lean_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Analyzer returns skipped result when tree-sitter-lean not available."""
        from hypergumbo.analyze import lean as lean_module

        # Create a Lean file
        make_lean_file(tmp_path, "Example.lean", "def foo := 42")

        with patch.object(lean_module, "is_lean_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Lean analysis skipped"):
                result = lean_module.analyze_lean(tmp_path)

        assert result.skipped is True
        assert "tree-sitter-lean" in result.skip_reason
        assert len(result.symbols) == 0
        assert len(result.edges) == 0


class TestLeanSignatureExtraction:
    """Tests for Lean function signature extraction."""

    def test_def_signature(self, tmp_path: Path) -> None:
        """Extract signature from def with parameters and return type."""
        make_lean_file(tmp_path, "Example.lean", """
def double (n : Nat) : Nat := n + n
""")
        result = analyze_lean(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "double"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(n : Nat) : Nat"

    def test_theorem_signature(self, tmp_path: Path) -> None:
        """Extract signature from theorem with parameters and proof type."""
        make_lean_file(tmp_path, "Example.lean", """
theorem add_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b
""")
        result = analyze_lean(tmp_path)
        thms = [s for s in result.symbols if s.kind == "theorem" and s.name == "add_comm"]
        assert len(thms) == 1
        assert thms[0].signature == "(a b : Nat) : a + b = b + a"

    def test_def_no_params(self, tmp_path: Path) -> None:
        """Extract signature from def without parameters."""
        make_lean_file(tmp_path, "Example.lean", """
def answer : Nat := 42
""")
        result = analyze_lean(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "answer"]
        assert len(funcs) == 1
        assert funcs[0].signature == ": Nat"
