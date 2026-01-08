"""Tests for Agda analyzer.

Agda analysis uses tree-sitter to extract:
- Symbols: function (including theorems/lemmas), data type, record, module
- Edges: imports, references

Agda is a dependently typed programming language and proof assistant.
Unlike typical programming languages, "calls" are less meaningful than
"references" (dependencies between theorems/lemmas).

Test coverage includes:
- Module detection
- Function/theorem detection
- Data type detection
- Record detection
- Import statements (open import, import)
- Postulate detection
- Two-pass cross-file resolution
"""
from pathlib import Path


def make_agda_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an Agda file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestAgdaAnalyzerAvailability:
    """Tests for tree-sitter-agda availability detection."""

    def test_is_agda_tree_sitter_available(self) -> None:
        """Check if tree-sitter-agda is detected."""
        from hypergumbo.analyze.agda import is_agda_tree_sitter_available

        # Should be True since we installed tree-sitter-agda
        assert is_agda_tree_sitter_available() is True


class TestAgdaModuleDetection:
    """Tests for Agda module detection."""

    def test_detect_module(self, tmp_path: Path) -> None:
        """Detect module declaration."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

open import Data.Nat
""")

        result = analyze_agda(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        mod = next((s for s in symbols if s.name == "Example"), None)
        assert mod is not None
        assert mod.kind == "module"
        assert mod.language == "agda"


class TestAgdaFunctionDetection:
    """Tests for Agda function/theorem symbol extraction."""

    def test_detect_function_with_signature(self, tmp_path: Path) -> None:
        """Detect function with type signature."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

double : Nat -> Nat
double zero = zero
double (suc n) = suc (suc (double n))
""")

        result = analyze_agda(tmp_path)

        assert not result.skipped
        symbols = result.symbols
        func = next((s for s in symbols if s.name == "double"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.language == "agda"

    def test_detect_infix_operator(self, tmp_path: Path) -> None:
        """Detect infix operator definition."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

_+_ : Nat -> Nat -> Nat
zero + m = m
suc n + m = suc (n + m)
""")

        result = analyze_agda(tmp_path)

        symbols = result.symbols
        func = next((s for s in symbols if s.name == "_+_"), None)
        assert func is not None
        assert func.kind == "function"


class TestAgdaDataTypeDetection:
    """Tests for Agda data type symbol extraction."""

    def test_detect_data_type(self, tmp_path: Path) -> None:
        """Detect data type definition."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

data List (A : Set) : Set where
  []   : List A
  _::_ : A -> List A -> List A
""")

        result = analyze_agda(tmp_path)

        symbols = result.symbols
        dt = next((s for s in symbols if s.name == "List"), None)
        assert dt is not None
        assert dt.kind == "data"
        assert dt.language == "agda"

    def test_detect_data_constructors(self, tmp_path: Path) -> None:
        """Detect data constructors."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

data Bool : Set where
  true  : Bool
  false : Bool
""")

        result = analyze_agda(tmp_path)

        symbols = result.symbols
        names = [s.name for s in symbols]
        assert "Bool" in names
        # Constructors are also detected
        assert "true" in names or "_::_" in names  # At least some constructors


class TestAgdaRecordDetection:
    """Tests for Agda record type detection."""

    def test_detect_record(self, tmp_path: Path) -> None:
        """Detect record definition."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

record Pair (A B : Set) : Set where
  constructor _,_
  field
    fst : A
    snd : B
""")

        result = analyze_agda(tmp_path)

        symbols = result.symbols
        rec = next((s for s in symbols if s.name == "Pair"), None)
        assert rec is not None
        assert rec.kind == "record"
        assert rec.language == "agda"


class TestAgdaImportDetection:
    """Tests for Agda import edge extraction."""

    def test_detect_open_import(self, tmp_path: Path) -> None:
        """Detect open import statement."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

open import Data.Nat
open import Data.Bool using (Bool; true; false)
""")

        result = analyze_agda(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

        # Check import targets
        targets = [e.dst for e in import_edges]
        assert any("Data.Nat" in t for t in targets)
        assert any("Data.Bool" in t for t in targets)

    def test_detect_import_with_renaming(self, tmp_path: Path) -> None:
        """Detect import with renaming."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

open import Data.Nat renaming (zero to z; suc to s)
""")

        result = analyze_agda(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

    def test_detect_plain_import(self, tmp_path: Path) -> None:
        """Detect plain import statement (without open)."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

import Data.Nat
""")

        result = analyze_agda(tmp_path)

        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        # Plain import should create an import edge
        assert len(import_edges) >= 1
        assert any("Data.Nat" in e.dst for e in import_edges)


class TestAgdaPostulateDetection:
    """Tests for Agda postulate detection."""

    def test_detect_postulate(self, tmp_path: Path) -> None:
        """Detect postulate declaration."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

postulate
  extensionality : {A B : Set} {f g : A -> B} -> ((x : A) -> f x == g x) -> f == g
""")

        result = analyze_agda(tmp_path)

        symbols = result.symbols
        post = next((s for s in symbols if s.name == "extensionality"), None)
        assert post is not None
        # Postulates are treated as functions with meta indicating postulate
        assert post.kind == "function"


class TestAgdaCrossFileResolution:
    """Tests for Agda cross-file symbol resolution."""

    def test_cross_file_import(self, tmp_path: Path) -> None:
        """Detect cross-file imports."""
        from hypergumbo.analyze.agda import analyze_agda

        # Create two Agda files
        make_agda_file(tmp_path, "MyNat.agda", """
module MyNat where

data MyNat : Set where
  zero : MyNat
  suc  : MyNat -> MyNat
""")

        make_agda_file(tmp_path, "UseNat.agda", """
module UseNat where

open import MyNat

double : MyNat -> MyNat
double zero = zero
double (suc n) = suc (suc (double n))
""")

        result = analyze_agda(tmp_path)

        # Should have symbols from both files
        symbols = result.symbols
        names = [s.name for s in symbols]
        assert "MyNat" in names
        assert "double" in names

        # Should have import edge
        edges = result.edges
        import_edges = [e for e in edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestAgdaTheoremDetection:
    """Tests for Agda theorem/lemma detection (treated as functions)."""

    def test_detect_theorem_style_definition(self, tmp_path: Path) -> None:
        """Detect theorem-style definitions."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

open import Data.Nat
open import Relation.Binary.PropositionalEquality

+-comm : (m n : Nat) -> m + n == n + m
+-comm zero n = sym (+-identity n)
+-comm (suc m) n = cong suc (+-comm m n)
""")

        result = analyze_agda(tmp_path)

        symbols = result.symbols
        thm = next((s for s in symbols if s.name == "+-comm"), None)
        assert thm is not None
        assert thm.kind == "function"  # Theorems treated as functions


class TestAgdaAnalyzerSkipsWhenUnavailable:
    """Tests for graceful handling when tree-sitter-agda unavailable."""

    def test_returns_skipped_when_no_agda_files(self, tmp_path: Path) -> None:
        """Returns empty result when no Agda files present."""
        from hypergumbo.analyze.agda import analyze_agda

        # Create a non-Agda file
        (tmp_path / "test.py").write_text("print('hello')")

        result = analyze_agda(tmp_path)

        # Should return empty but not skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0


class TestAgdaSignatureExtraction:
    """Tests for Agda function signature extraction."""

    def test_function_signature(self, tmp_path: Path) -> None:
        """Extract type signature from function with type annotation."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

double : Nat -> Nat
double x = x + x
""")
        result = analyze_agda(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "double"]
        assert len(funcs) == 1
        assert funcs[0].signature == ": Nat -> Nat"

    def test_infix_operator_signature(self, tmp_path: Path) -> None:
        """Extract type signature from infix operator."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

_+_ : Nat -> Nat -> Nat
zero + m = m
suc n + m = suc (n + m)
""")
        result = analyze_agda(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "_+_"]
        assert len(funcs) == 1
        assert funcs[0].signature == ": Nat -> Nat -> Nat"

    def test_postulate_signature(self, tmp_path: Path) -> None:
        """Extract type signature from postulate."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

postulate
  axiom1 : A -> A
""")
        result = analyze_agda(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "axiom1"]
        assert len(funcs) == 1
        assert funcs[0].signature == ": A -> A"

    def test_constructor_signature(self, tmp_path: Path) -> None:
        """Extract type signature from data constructor."""
        from hypergumbo.analyze.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """
module Example where

data Bool : Set where
  true : Bool
  false : Bool
""")
        result = analyze_agda(tmp_path)
        # Find constructor 'true'
        ctors = [s for s in result.symbols if s.kind == "function" and s.name == "true"]
        assert len(ctors) == 1
        assert ctors[0].signature == ": Bool"
