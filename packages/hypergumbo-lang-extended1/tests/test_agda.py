# SPDX-License-Identifier: AGPL-3.0-or-later
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

import pytest

from hypergumbo_core.overload_arity import declared_arity_stable_id


def make_agda_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an Agda file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


class TestAgdaAnalyzerAvailability:
    """Tests for Agda grammar availability detection."""

    def test_is_agda_tree_sitter_available(self) -> None:
        """The language pack's Agda grammar is a declared dependency."""
        from hypergumbo_lang_extended1.agda import is_agda_tree_sitter_available

        assert is_agda_tree_sitter_available() is True

    def test_the_grammar_is_the_language_packs(self) -> None:
        """INV-vokir: the pack's build of tree-sitter-agda, not the 1.3.3 wheel."""
        from hypergumbo_lang_extended1.agda import AgdaAnalyzer

        assert AgdaAnalyzer.grammar_distribution() == "tree-sitter-language-pack"


class TestAgdaModuleDetection:
    """Tests for Agda module detection."""

    def test_detect_module(self, tmp_path: Path) -> None:
        """Detect module declaration."""
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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


class TestAgdaImportAliases:
    """Tests for Agda import alias tracking (ADR-0007)."""

    def test_extract_renaming_aliases(self, tmp_path: Path) -> None:
        """Extract aliases from renaming directive."""
        from hypergumbo_lang_extended1.agda import (
            _extract_edges_from_file,
        )
        from hypergumbo_core.symbol_resolution import NameResolver
        import tree_sitter
        from tree_sitter_language_pack import get_language

        source = b"""module Example where
open import Data.List renaming (map to listMap)
"""
        parser = tree_sitter.Parser(get_language("agda"))
        tree = parser.parse(source)

        resolver = NameResolver({})
        edges, aliases = _extract_edges_from_file(
            tree, source, "Example.agda", [], resolver, "test-run"
        ,
        )

        # Should have import edge
        assert len(edges) >= 1
        # Should have extracted renaming alias
        assert "listMap" in aliases
        assert aliases["listMap"] == "Data.List.map"


class TestAgdaPostulateDetection:
    """Tests for Agda postulate detection."""

    def test_detect_postulate(self, tmp_path: Path) -> None:
        """Detect postulate declaration."""
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
    """Tests for graceful handling when there is nothing to analyze."""

    def test_returns_skipped_when_no_agda_files(self, tmp_path: Path) -> None:
        """Returns empty result when no Agda files present."""
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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
        from hypergumbo_lang_extended1.agda import analyze_agda

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


class TestAgdaReferenceEdges:
    """Tests for reference edge extraction in Agda."""

    def test_function_references_in_rhs(self, tmp_path: Path) -> None:
        """Function pattern clauses create references edges to used symbols."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """\
module Example where

data Nat : Set where
  zero : Nat
  suc  : Nat -> Nat

double : Nat -> Nat
double zero = zero
double (suc n) = suc (suc (double n))
""")
        result = analyze_agda(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        # double references suc and itself (recursion)
        double_sym = next(s for s in result.symbols if s.name == "double")
        suc_sym = next(s for s in result.symbols if s.name == "suc")
        # double → suc reference
        assert any(
            e.src == double_sym.id and e.dst == suc_sym.id
            for e in ref_edges
        ), f"Expected double→suc reference edge, got: {[(e.src, e.dst) for e in ref_edges]}"

    def test_cross_file_reference(self, tmp_path: Path) -> None:
        """References across files are resolved."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Nat.agda", """\
module Nat where

data Nat : Set where
  zero : Nat
  suc  : Nat -> Nat
""")
        make_agda_file(tmp_path, "Main.agda", """\
module Main where

open import Nat

double : Nat -> Nat
double zero = zero
double (suc n) = suc (suc (double n))
""")
        result = analyze_agda(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        # Should have at least one reference edge
        assert len(ref_edges) >= 1

    def test_type_signature_references(self, tmp_path: Path) -> None:
        """Type signatures create reference edges to types (WI-kujuf)."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """\
module Example where

data Nat : Set where
  zero : Nat
  suc  : Nat -> Nat

double : Nat -> Nat
double zero = zero
double (suc n) = suc (suc (double n))
""")
        result = analyze_agda(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        double_sym = next(s for s in result.symbols if s.name == "double")
        nat_sym = next(s for s in result.symbols if s.name == "Nat")
        # double's type signature references Nat
        assert any(
            e.src == double_sym.id and e.dst == nat_sym.id
            for e in ref_edges
        ), f"Expected double→Nat type-sig reference, got: {[(e.src, e.dst) for e in ref_edges]}"

    def test_type_sig_refs_with_implicit_args(self, tmp_path: Path) -> None:
        """Implicit argument types in signatures create reference edges."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """\
module Example where

data Nat : Set where
  zero : Nat
  suc  : Nat -> Nat

data Vec (A : Set) : Nat -> Set where
  nil  : Vec A zero
  cons : A -> Vec A Nat -> Vec A Nat

vmap : {n : Nat} -> Vec Nat n -> Vec Nat n
vmap nil = nil
""")
        result = analyze_agda(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        vmap_sym = next(s for s in result.symbols if s.name == "vmap")
        nat_sym = next(s for s in result.symbols if s.name == "Nat")
        vec_sym = next(s for s in result.symbols if s.name == "Vec")
        # vmap's type sig references both Nat and Vec
        assert any(
            e.src == vmap_sym.id and e.dst == nat_sym.id
            for e in ref_edges
        ), "Expected vmap→Nat type-sig reference"
        assert any(
            e.src == vmap_sym.id and e.dst == vec_sym.id
            for e in ref_edges
        ), "Expected vmap→Vec type-sig reference"

    def test_constructor_type_sig_references(self, tmp_path: Path) -> None:
        """Data constructor type signatures create reference edges."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """\
module Example where

data Nat : Set where
  zero : Nat
  suc  : Nat -> Nat
""")
        result = analyze_agda(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        suc_sym = next(s for s in result.symbols if s.name == "suc")
        nat_sym = next(s for s in result.symbols if s.name == "Nat")
        # suc's type signature ": Nat -> Nat" references Nat
        assert any(
            e.src == suc_sym.id and e.dst == nat_sym.id
            for e in ref_edges
        ), "Expected suc→Nat constructor type-sig reference"

    def test_type_sig_refs_confidence(self, tmp_path: Path) -> None:
        """A type-signature reference takes the resolver grade (ADR-0039 ruling 6)."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """\
module Example where

data Nat : Set where
  zero : Nat
  suc  : Nat -> Nat

double : Nat -> Nat
double zero = zero
""")
        result = analyze_agda(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        double_sym = next(s for s in result.symbols if s.name == "double")
        nat_sym = next(s for s in result.symbols if s.name == "Nat")
        # Find the type-sig reference edge
        sig_edge = next(
            (e for e in ref_edges
             if e.src == double_sym.id and e.dst == nat_sym.id),
            None,
        )
        assert sig_edge is not None
        # Exact match: the reference pathway base.
        assert sig_edge.confidence == 0.95


class TestAgdaRecordFieldExtraction:
    """Tests for record field symbol extraction (WI-kujuf Phase 2)."""

    def test_extract_record_fields(self, tmp_path: Path) -> None:
        """Record fields are extracted as symbols."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """\
module Example where

record Pair (A B : Set) : Set where
  field
    fst : A
    snd : B
""")
        result = analyze_agda(tmp_path)
        symbols = result.symbols
        # A field is named with its record, as Agda names it (INV-kagib).
        fst_sym = next((s for s in symbols if s.name == "Pair.fst"), None)
        snd_sym = next((s for s in symbols if s.name == "Pair.snd"), None)
        assert fst_sym is not None, f"Expected 'Pair.fst' field, got: {[s.name for s in symbols]}"
        assert snd_sym is not None, f"Expected 'Pair.snd' field, got: {[s.name for s in symbols]}"
        assert fst_sym.kind == "field"
        assert snd_sym.kind == "field"

    def test_record_field_signature(self, tmp_path: Path) -> None:
        """Record fields carry their type signature."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Example.agda", """\
module Example where

record Pair : Set where
  field
    fst : Nat
    snd : Bool
""")
        result = analyze_agda(tmp_path)
        fst_sym = next((s for s in result.symbols if s.name == "Pair.fst"), None)
        assert fst_sym is not None
        assert fst_sym.signature == ": Nat"


class TestAgdaCyclomaticComplexity:
    """INV-loguk slice C: callable agda symbols carry non-null CC + LOC.
    Real-grammar verification (degenerate grammar: no decision-point nodes, so callables get base CC=1 (non-null))."""

    def test_branchy_callables_have_expected_cc(self, tmp_path) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda
        (tmp_path / 'T.agda').write_text('module T where\n\nclassify : Nat -> Nat -> Nat\nclassify x y = 0\n\ncheck : Bool -> Nat\ncheck b = 1\n')
        result = analyze_agda(tmp_path)
        assert not result.skipped
        by = {s.name: s for s in result.symbols if s.kind in ('function',)}
        assert by['classify'].cyclomatic_complexity == 1, by['classify'].cyclomatic_complexity
        assert by['classify'].line_span is not None
        assert by['check'].cyclomatic_complexity == 1, by['check'].cyclomatic_complexity
        assert by['check'].line_span is not None

    def test_callables_non_null_non_callables_null(self, tmp_path) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda
        (tmp_path / 'T.agda').write_text('module T where\n\nclassify : Nat -> Nat -> Nat\nclassify x y = 0\n\ncheck : Bool -> Nat\ncheck b = 1\n')
        result = analyze_agda(tmp_path)
        callables = [s for s in result.symbols if s.kind in ('function',)]
        assert callables
        for s in callables:
            assert s.cyclomatic_complexity is not None, (s.kind, s.name)
            assert s.line_span is not None, (s.kind, s.name)
        for s in result.symbols:
            if s.kind not in ('function',):
                assert s.cyclomatic_complexity is None, (s.kind, s.name)


class TestAgdaSameNamedDefinitions:
    """WI-kosar: a reference binds the definition of that name in THIS file
    before any other file's (the enclosing definition is already by position,
    INV-kagib)."""

    def test_reference_prefers_this_files_definition(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda

        # ``postulate`` rather than ``data``: written when tree-sitter-agda
        # 1.3.3 lost the declaration that follows a ``data ... where`` block
        # in this layout (INV-vokir); the same reference rule holds either way.
        make_agda_file(tmp_path, "A.agda", """
module A where

postulate Nat : Set
postulate zero : Nat

run : Nat
run = zero

bar : Nat
bar = run
""")
        make_agda_file(tmp_path, "B.agda", """
module B where

postulate Nat : Set
postulate zero : Nat

run : Nat
run = zero

baz : Nat
baz = run
""")
        result = analyze_agda(tmp_path)

        by_id = {s.id: s for s in result.symbols}
        refs = [
            (by_id[e.src].path, by_id[e.src].name, by_id[e.dst].path, by_id[e.dst].name)
            for e in result.edges if e.edge_type == "references"
        ]
        cross_file = [r for r in refs if r[0] != r[2]]
        assert cross_file == [], refs
        assert ("A.agda", "bar", "A.agda", "run") in refs, refs
        assert ("B.agda", "baz", "B.agda", "run") in refs, refs


def _declared(result, kind: str, name: str):
    """The one ``kind`` symbol named ``name`` (fails on none or several)."""
    found = [s for s in result.symbols if s.kind == kind and s.name == name]
    assert len(found) == 1, sorted((s.kind, s.name) for s in result.symbols)
    return found[0]


def _referenced(result, src_name: str, dst_name: str) -> bool:
    by_id = {s.id: s.name for s in result.symbols}
    return any(
        by_id.get(e.src) == src_name and by_id.get(e.dst) == dst_name
        for e in result.edges if e.edge_type == "references"
    )


class TestAgdaDeclarationAfterALayoutBlock:
    """INV-vokir: every type signature and its clauses yield one function
    symbol, whatever layout block precedes them. Under tree-sitter-agda 1.3.3 a
    block of one entry (a one-constructor ``data``, a one-line ``postulate``, a
    one-entry ``variable`` block) split the next signature into two ``function`` nodes
    (``go`` | ``: Nat``), so the declaration and every reference to it were
    lost. The probes are the item's own."""

    @pytest.mark.parametrize("source, name, user", [
        pytest.param(
            "module A where\ndata Nat : Set where\n  zero : Nat\n\n"
            "go : Nat\ngo = zero\nbar : Nat\nbar = go\n",
            "go", "bar", id="one-constructor-data-blank-line"),
        pytest.param(
            "module A where\ndata Nat : Set where\n  zero : Nat\n"
            "go : Nat\ngo = zero\nbar : Nat\nbar = go\n",
            "go", "bar", id="one-constructor-data-no-blank-line"),
        pytest.param(
            "module A where\npostulate Nat : Set\n\n"
            "run : Nat -> Nat\nrun x = x\nbar : Nat -> Nat\nbar x = run x\n",
            "run", "bar", id="one-line-postulate"),
        pytest.param(
            "module A where\n\ndata Unit : Set where\n  tt : Unit\n\n"
            "foo : Unit -> Unit\nfoo x = x\n\nbar : Unit -> Unit\nbar x = foo x\n",
            "foo", "bar", id="one-constructor-data-function-type"),
        pytest.param(
            "module P where\n\nvariable\n  n : Nat\n\n"
            "recompute : Nat -> Nat\nrecompute x = x\n\n"
            "use : Nat -> Nat\nuse x = recompute x\n",
            "recompute", "use", id="one-entry-variable-block"),
    ])
    def test_the_next_declaration_is_emitted_and_referenced(
        self, tmp_path: Path, source: str, name: str, user: str,
    ) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "A.agda", source)
        result = analyze_agda(tmp_path)
        sym = _declared(result, "function", name)
        assert sym.signature is not None and sym.signature.startswith(":")
        assert _referenced(result, user, name)

    def test_a_data_header_after_a_one_constructor_data_keeps_its_constructors(
        self, tmp_path: Path,
    ) -> None:
        """1.3.3 lost ``data Bool``, and ``true`` / ``false`` hashed the wrong
        data type in their chain."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "One2.agda", (
            "module One2 where\n\ndata Unit : Set where\n  tt : Unit\n\n"
            "data Bool : Set where\n  true : Bool\n  false : Bool\n\n"
            "not : Bool -> Bool\nnot true = false\nnot false = true\n"
        ))
        result = analyze_agda(tmp_path)
        _declared(result, "data", "Bool")
        for ctor in ("true", "false"):
            assert _declared(result, "function", ctor).stable_id == declared_arity_stable_id(
                kind="function", lang="agda", path="One2.agda",
                name=ctor, qualified_name=f"Bool.{ctor}", arity=None,
            )


class TestAgdaPrimitives:
    """A ``primitive`` block's entries are ``type_signature`` nodes, one
    ``field_name`` per name. Under 1.3.3 a primitive after a one-line
    ``postulate`` came out as a stray ``function`` node and was emitted by
    accident; a well-parsed one was never emitted."""

    def test_every_name_of_a_primitive_block_is_a_function(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "P.agda", (
            "module P where\n\npostulate Float : Set\n\n"
            "primitive\n  primFloatPlus : Float -> Float -> Float\n\n"
            "module M where\n  primitive\n    primA primB : Set\n\n"
            "private\n  primitive\n    primC : Set\n"
        ))
        result = analyze_agda(tmp_path)
        plus = _declared(result, "function", "primFloatPlus")
        assert plus.signature == ": Float -> Float -> Float"
        for name, chain in (("primA", "M.primA"), ("primB", "M.primB"), ("primC", "primC")):
            sym = _declared(result, "function", chain)
            assert sym.signature == ": Set"
            assert sym.stable_id == declared_arity_stable_id(
                kind="function", lang="agda", path="P.agda",
                name=name, qualified_name=chain, arity=None,
            )


def _scoped(result, path: str) -> dict[tuple[str, str], object]:
    """``{(kind, name): symbol}`` of one file's declarations (the file symbol aside)."""
    out = {}
    for s in result.symbols:
        if s.kind != "file" and s.path == path:
            assert (s.kind, s.name) not in out, (s.kind, s.name)
            out[(s.kind, s.name)] = s
    return out


_HEAD = "module T where\n\n"
_C_NONEMPTY = "module A where\n  module C where\n    c : Set\n    c = Set\n"
_RUN_IN_A = "\n  run : Set\n  run = Set\n"
_B = "\nmodule B where\n  f : Set\n  f = Set\n"


class TestAgdaNestedLayoutBlocks:
    """WI-molon: layout blocks nested in a module keep every scope chain. Under
    tree-sitter-agda 1.3.3 a nested module, record or data block lost the
    enclosing module or attached later code to the wrong block. Rows are the
    item's probe grid (``module T where`` then the shape); each expects every
    declaration, named as Agda names it, hashing the chain in the third
    column (None for modules, whose id is the name's)."""

    @pytest.mark.parametrize("body, expected", [
        pytest.param(_C_NONEMPTY, [
            ("module", "A", None), ("module", "A.C", None),
            ("function", "A.C.c", "A.C.c"),
        ], id="non-empty-nested-module-last"),
        pytest.param(_C_NONEMPTY + _RUN_IN_A, [
            ("module", "A", None), ("module", "A.C", None),
            ("function", "A.C.c", "A.C.c"), ("function", "A.run", "A.run"),
        ], id="non-empty-nested-module-then-sibling"),
        pytest.param(_C_NONEMPTY + "    d : Set\n    d = Set\n" + _RUN_IN_A, [
            ("module", "A", None), ("module", "A.C", None),
            ("function", "A.C.c", "A.C.c"), ("function", "A.C.d", "A.C.d"),
            ("function", "A.run", "A.run"),
        ], id="nested-module-three-declarations-then-sibling"),
        pytest.param("module A where\n  module C where\n" + _RUN_IN_A, [
            ("module", "A", None), ("module", "A.C", None),
            ("function", "A.run", "A.run"),
        ], id="empty-nested-module-then-sibling"),
        pytest.param("module A where\n  module C where\n" + _RUN_IN_A + _B, [
            ("module", "A", None), ("module", "A.C", None), ("module", "B", None),
            ("function", "A.run", "A.run"), ("function", "B.f", "B.f"),
        ], id="empty-nested-module-then-top-level-module"),
        pytest.param("module A where\n  run : Set\n  run = Set\n\n  module C where\n" + _B, [
            ("module", "A", None), ("module", "A.C", None), ("module", "B", None),
            ("function", "A.run", "A.run"), ("function", "B.f", "B.f"),
        ], id="empty-nested-module-last-in-module-then-top-level"),
        pytest.param(_C_NONEMPTY + _B, [
            ("module", "A", None), ("module", "A.C", None), ("module", "B", None),
            ("function", "A.C.c", "A.C.c"), ("function", "B.f", "B.f"),
        ], id="nested-module-then-top-level-module"),
        pytest.param(
            "module A where\n  run : Set\n  run = Set\n\n"
            "  module C where\n    c : Set\n    c = Set\n" + _B, [
                ("module", "A", None), ("module", "A.C", None), ("module", "B", None),
                ("function", "A.run", "A.run"), ("function", "A.C.c", "A.C.c"),
                ("function", "B.f", "B.f"),
            ], id="sibling-then-nested-module-then-top-level"),
        pytest.param(
            "module A where\n  record R : Set where\n    field\n      x : Set\n" + _RUN_IN_A, [
                ("module", "A", None), ("record", "A.R", "A.R"),
                ("field", "A.R.x", "A.R.x"), ("function", "A.run", "A.run"),
            ], id="nested-record-then-sibling"),
        pytest.param(
            "module A where\n  data D : Set where\n    k1 : D\n    k2 : D\n"
            "\n  run : D\n  run = k1\n" + _B, [
                ("module", "A", None), ("data", "A.D", "A.D"),
                ("function", "A.k1", "A.D.k1"), ("function", "A.k2", "A.D.k2"),
                ("function", "A.run", "A.run"), ("module", "B", None),
                ("function", "B.f", "B.f"),
            ], id="nested-data-then-sibling-then-top-level"),
    ])
    def test_every_declaration_keeps_its_chain(
        self, tmp_path: Path, body: str, expected: list,
    ) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "T.agda", _HEAD + body)
        got = _scoped(analyze_agda(tmp_path), "T.agda")
        assert set(got) == {("module", "T")} | {(k, n) for k, n, _c in expected}
        for kind, name, chain in expected:
            if chain is not None:
                assert got[(kind, name)].stable_id == declared_arity_stable_id(
                    kind=kind, lang="agda", path="T.agda",
                    name=name.rsplit(".", 1)[-1], qualified_name=chain, arity=None,
                ), (kind, name)

    def test_the_filed_shape_and_its_where_blocks(self, tmp_path: Path) -> None:
        """The item's N.agda: an empty nested module, then a top-level module
        whose two functions each own a ``where`` block."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "N.agda", (
            "module N where\n\nmodule A where\n  module C where\n\n"
            "  run : Set\n  run = Set\n\nmodule B where\n  f : Set\n  f = g\n"
            "    where\n      g : Set\n      g = Set\n  h : Set\n  h = k\n"
            "    where\n      k : Set\n      k = Set\n"
        ))
        got = _scoped(analyze_agda(tmp_path), "N.agda")
        assert {n for k, n in got if k == "module"} == {"N", "A", "A.C", "B"}
        for name, chain in (("A.run", "A.run"), ("B.f", "B.f"), ("B.h", "B.h"),
                            ("g", "B.f/g"), ("k", "B.h/k")):
            assert got[("function", name)].stable_id == declared_arity_stable_id(
                kind="function", lang="agda", path="N.agda",
                name=name.rsplit(".", 1)[-1], qualified_name=chain, arity=None,
            ), name


class TestAgdaWhereBlocks:
    """Every ``where`` block nests under the clause that owns it, later ones
    included (the docstring said the grammar nested only the first; WI-molon's
    probes contradicted it). Under 1.3.3 a block after a layout-scanner failure
    was not nested."""

    @pytest.mark.parametrize("source, owned", [
        pytest.param(
            "module W where\n\ndata Nat : Set where\n  zero : Nat\n  suc : Nat -> Nat\n\n"
            "f : Nat -> Nat\nf x = g x\n  where\n    g : Nat -> Nat\n    g y = y\n\n"
            "h : Nat -> Nat\nh x = k x\n  where\n    k : Nat -> Nat\n    k y = y\n",
            [("g", "f/g"), ("k", "h/k")], id="two-functions"),
        pytest.param(
            "module Cl where\n\ndata Nat : Set where\n  zero : Nat\n  suc : Nat -> Nat\n\n"
            "run : Nat -> Nat\nrun zero = go zero\n  where\n    go : Nat -> Nat\n"
            "    go y = y\nrun (suc n) = go2 n\n  where\n    go2 : Nat -> Nat\n    go2 y = y\n",
            [("go", "run/go"), ("go2", "run/go2")], id="two-clauses-of-one-function"),
        pytest.param(
            "module Where where\n\npostulate N : Set\n\n"
            "run : N -> N\nrun x = go x\n  where\n    go : N -> N\n    go y = y\n\n"
            "walk : N -> N\nwalk x = step x\n  where\n    step : N -> N\n    step y = y\n",
            [("go", "run/go"), ("step", "walk/step")], id="after-a-one-line-postulate"),
    ])
    def test_each_where_block_is_owned_by_its_clause(
        self, tmp_path: Path, source: str, owned: list,
    ) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "M.agda", source)
        result = analyze_agda(tmp_path)
        for name, chain in owned:
            assert _declared(result, "function", name).stable_id == declared_arity_stable_id(
                kind="function", lang="agda", path="M.agda",
                name=name, qualified_name=chain, arity=None,
            ), name


_ANONYMOUS = (
    "module Anon where\n\n"
    "module _ (X : Set) where\n  f : Set\n  f = X\n\n"
    "module _ where\n  g : Set₁\n  g = Set\n\n"
    "module Outer where\n  module _ {Y : Set} (y : Y) where\n    h : Set\n    h = Y\n\n"
    "    module _ (Z : Set) where\n      deep : Set\n      deep = Z\n\n"
    "record R (A : Set) : Set₁ where\n  field\n    carrier : Set\n"
    "  module _ (B : Set) where\n    inRec : Set\n    inRec = B\n\n"
    "private\n  module _ (P : Set) where\n    priv : Set\n    priv = P\n\n"
    "module _ (Q : Set) where\n  private\n    hid : Set\n    hid = Q\n"
)


class TestAgdaAnonymousModules:
    """WI-bokud: ``module _ <telescope> where`` is a scoping device, not a
    declaration. Agda puts its contents in the enclosing module (each taking
    the telescope as extra parameters), so it emits no symbol and adds nothing
    to a chain. It was emitted as a module named ``_`` (``Outer._`` nested),
    two of which tied on that name and were split by order: deleting the first
    moved the second's stable id."""

    def test_no_anonymous_module_is_a_symbol(self, tmp_path: Path) -> None:
        """Deleting the first of two then moves no id: there is no ``_`` tie
        left to split by order (the floor gate's ``agda-anonymous`` case checks
        that through the full pipeline, where a module's id is minted)."""
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Anon.agda", _ANONYMOUS)
        modules = [s.name for s in analyze_agda(tmp_path).symbols if s.kind == "module"]
        assert sorted(modules) == ["Anon", "Outer"]

    @pytest.mark.parametrize("name, chain", [
        pytest.param("f", "f", id="with-parameters"),
        pytest.param("g", "g", id="without-parameters"),
        pytest.param("Outer.h", "Outer.h", id="inside-a-named-module"),
        pytest.param("Outer.deep", "Outer.deep", id="nested-in-an-anonymous-module"),
        pytest.param("R.inRec", "R.inRec", id="inside-a-record"),
        pytest.param("priv", "priv", id="inside-a-private-block"),
        pytest.param("hid", "hid", id="holding-a-private-block"),
    ])
    def test_members_belong_to_the_enclosing_module(
        self, tmp_path: Path, name: str, chain: str,
    ) -> None:
        from hypergumbo_lang_extended1.agda import analyze_agda

        make_agda_file(tmp_path, "Anon.agda", _ANONYMOUS)
        assert _declared(analyze_agda(tmp_path), "function", name).stable_id == declared_arity_stable_id(
            kind="function", lang="agda", path="Anon.agda",
            name=name.rsplit(".", 1)[-1], qualified_name=chain, arity=None,
        )
