# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for TLA+ analyzer.

TLA+ analysis uses tree-sitter to extract:
- Symbols: module, operator, constant, variable, theorem, assumption
- Edges: imports (EXTENDS, INSTANCE), references (body references)

TLA+ is a formal specification language for modeling concurrent and
distributed systems.  Unlike typical programming languages, "calls"
are less meaningful than "references" (dependencies between operators
and theorems).  We model operator dependencies as "references" edges.

Test coverage includes:
- Module detection
- Operator detection (zero-param, parameterized, LOCAL, RECURSIVE)
- Constant and variable declarations
- Theorem and assumption detection (named and unnamed)
- EXTENDS import edges
- INSTANCE import edges
- Body reference edges
- Self-reference exclusion
- Reference deduplication
- Cross-file analysis
- File symbol creation
- Empty/no-TLA repos
- Unavailability mock
"""
from pathlib import Path
from unittest.mock import patch

import pytest


def make_tla_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a TLA+ file with given content."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestTLAPlusAvailability:
    """Tests for tree-sitter-tlaplus availability detection."""

    def test_is_tlaplus_tree_sitter_available(self) -> None:
        from hypergumbo_lang_extended1.tlaplus import is_tlaplus_tree_sitter_available

        assert is_tlaplus_tree_sitter_available() is True

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1 import tlaplus as tlaplus_module

        make_tla_file(tmp_path, "Test.tla", "---- MODULE Test ----\n====\n")
        with patch.object(
            tlaplus_module._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            with pytest.warns(UserWarning, match="tlaplus analysis skipped"):
                result = tlaplus_module.analyze_tlaplus(tmp_path)
        assert result.skipped is True


# ---------------------------------------------------------------------------
# Module detection
# ---------------------------------------------------------------------------


class TestTLAPlusModuleDetection:

    def test_detect_module(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
====
""")
        result = analyze_tlaplus(tmp_path)
        assert not result.skipped
        mod = next((s for s in result.symbols if s.name == "Test"), None)
        assert mod is not None
        assert mod.kind == "module"
        assert mod.language == "tlaplus"


# ---------------------------------------------------------------------------
# Operator detection
# ---------------------------------------------------------------------------


class TestTLAPlusOperatorDetection:

    def test_detect_operator(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
VARIABLE x
Init == x = 0
Next == x' = x + 1
====
""")
        result = analyze_tlaplus(tmp_path)
        init = next((s for s in result.symbols if s.name == "Init"), None)
        assert init is not None
        assert init.kind == "operator"
        nxt = next((s for s in result.symbols if s.name == "Next"), None)
        assert nxt is not None
        assert nxt.kind == "operator"

    def test_detect_zero_param_operator_no_signature(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
Op == 42
====
""")
        result = analyze_tlaplus(tmp_path)
        op = next((s for s in result.symbols if s.name == "Op"), None)
        assert op is not None
        assert op.signature is None

    def test_detect_parameterized_operator_signature(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
Add(a, b) == a + b
====
""")
        result = analyze_tlaplus(tmp_path)
        op = next((s for s in result.symbols if s.name == "Add"), None)
        assert op is not None
        assert op.kind == "operator"
        assert op.signature == "(a, b)"

    def test_local_operator_has_meta(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
LOCAL Helper == 42
====
""")
        result = analyze_tlaplus(tmp_path)
        op = next((s for s in result.symbols if s.name == "Helper"), None)
        assert op is not None
        assert op.kind == "operator"
        assert op.meta.get("is_local") is True

    def test_recursive_operator_meta(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
EXTENDS Naturals
RECURSIVE Fact(_)
Fact(n) == IF n = 0 THEN 1 ELSE n * Fact(n - 1)
====
""")
        result = analyze_tlaplus(tmp_path)
        op = next((s for s in result.symbols if s.name == "Fact"), None)
        assert op is not None
        assert op.kind == "operator"
        assert op.meta.get("is_recursive") is True
        assert op.signature == "(n)"


# ---------------------------------------------------------------------------
# Constant and variable detection
# ---------------------------------------------------------------------------


class TestTLAPlusConstantsAndVariables:

    def test_detect_constants(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
CONSTANT N, M
====
""")
        result = analyze_tlaplus(tmp_path)
        n = next((s for s in result.symbols if s.name == "N"), None)
        assert n is not None
        assert n.kind == "constant"
        m = next((s for s in result.symbols if s.name == "M"), None)
        assert m is not None
        assert m.kind == "constant"

    def test_detect_variables(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
VARIABLE x, y
====
""")
        result = analyze_tlaplus(tmp_path)
        x = next((s for s in result.symbols if s.name == "x"), None)
        assert x is not None
        assert x.kind == "variable"
        y = next((s for s in result.symbols if s.name == "y"), None)
        assert y is not None
        assert y.kind == "variable"


# ---------------------------------------------------------------------------
# Theorem and assumption detection
# ---------------------------------------------------------------------------


class TestTLAPlusTheoremAndAssumption:

    def test_detect_named_theorem(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
EXTENDS Naturals
THEOREM Thm == \\A x \\in Nat : x >= 0
====
""")
        result = analyze_tlaplus(tmp_path)
        thm = next((s for s in result.symbols if s.name == "Thm"), None)
        assert thm is not None
        assert thm.kind == "theorem"

    def test_unnamed_theorem_skipped(self, tmp_path: Path) -> None:
        """Unnamed theorems should not produce a symbol."""
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
EXTENDS Naturals
THEOREM \\A x \\in Nat : x >= 0
====
""")
        result = analyze_tlaplus(tmp_path)
        theorems = [s for s in result.symbols if s.kind == "theorem"]
        assert len(theorems) == 0

    def test_detect_assumption(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
CONSTANT N
ASSUME Asm == N > 0
====
""")
        result = analyze_tlaplus(tmp_path)
        asm = next((s for s in result.symbols if s.name == "Asm"), None)
        assert asm is not None
        assert asm.kind == "assumption"


# ---------------------------------------------------------------------------
# Import edges (EXTENDS, INSTANCE)
# ---------------------------------------------------------------------------


class TestTLAPlusImportEdges:

    def test_detect_extends(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
EXTENDS Naturals, Sequences
====
""")
        result = analyze_tlaplus(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        extends_edges = [e for e in import_edges if e.evidence_type == "extends"]
        assert len(extends_edges) == 2
        targets = {e.dst for e in extends_edges}
        assert "tlaplus:Naturals:0-0:module:module" in targets
        assert "tlaplus:Sequences:0-0:module:module" in targets
        # Confidence should be 0.95
        for e in extends_edges:
            assert e.confidence == 0.95

    def test_detect_instance(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
INSTANCE FiniteSets
====
""")
        result = analyze_tlaplus(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        instance_edges = [e for e in import_edges if e.evidence_type == "instance"]
        assert len(instance_edges) == 1
        assert instance_edges[0].dst == "tlaplus:FiniteSets:0-0:module:module"
        assert instance_edges[0].confidence == 0.90

    def test_instance_with_substitution(self, tmp_path: Path) -> None:
        """INSTANCE ... WITH should still produce an import edge."""
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
CONSTANT val
INSTANCE SomeModule WITH x <- val
====
""")
        result = analyze_tlaplus(tmp_path)
        instance_edges = [
            e for e in result.edges
            if e.edge_type == "imports" and e.evidence_type == "instance"
        ]
        assert len(instance_edges) == 1
        assert "SomeModule" in instance_edges[0].dst


# ---------------------------------------------------------------------------
# Reference edges
# ---------------------------------------------------------------------------


class TestTLAPlusReferenceEdges:

    def test_operator_body_references(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
VARIABLE x
Init == x = 0
Next == x' = x + 1
Spec == Init /\\ [][Next]_x
====
""")
        result = analyze_tlaplus(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        # Spec should reference Init and Next
        spec_sym = next((s for s in result.symbols if s.name == "Spec"), None)
        assert spec_sym is not None
        spec_refs = [e for e in ref_edges if e.src == spec_sym.id]
        ref_targets = set()
        for e in spec_refs:
            target_sym = next(
                (s for s in result.symbols if s.id == e.dst), None
            )
            if target_sym:
                ref_targets.add(target_sym.name)
        assert "Init" in ref_targets
        assert "Next" in ref_targets

    def test_no_self_reference(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
EXTENDS Naturals
RECURSIVE Fact(_)
Fact(n) == IF n = 0 THEN 1 ELSE n * Fact(n - 1)
====
""")
        result = analyze_tlaplus(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        fact_sym = next((s for s in result.symbols if s.name == "Fact"), None)
        assert fact_sym is not None
        # No edge from Fact to Fact
        self_refs = [e for e in ref_edges if e.src == fact_sym.id and e.dst == fact_sym.id]
        assert len(self_refs) == 0

    def test_reference_deduplication(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
VARIABLE x
Helper == x
UseHelper == Helper /\\ Helper /\\ Helper
====
""")
        result = analyze_tlaplus(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        use_sym = next((s for s in result.symbols if s.name == "UseHelper"), None)
        assert use_sym is not None
        # Only one reference edge from UseHelper to Helper, not three
        helper_refs = [
            e for e in ref_edges
            if e.src == use_sym.id
            and any(s.name == "Helper" and s.id == e.dst for s in result.symbols)
        ]
        assert len(helper_refs) == 1

    def test_reference_confidence(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
A == 1
B == A
====
""")
        result = analyze_tlaplus(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) > 0
        for e in ref_edges:
            assert e.confidence == 0.80

    def test_theorem_body_references(self, tmp_path: Path) -> None:
        """Theorem bodies should produce reference edges."""
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
CONSTANT N
THEOREM Safety == N > 0
====
""")
        result = analyze_tlaplus(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        thm = next((s for s in result.symbols if s.name == "Safety"), None)
        assert thm is not None
        thm_refs = [e for e in ref_edges if e.src == thm.id]
        # Safety references N
        n_sym = next((s for s in result.symbols if s.name == "N"), None)
        assert n_sym is not None
        assert any(e.dst == n_sym.id for e in thm_refs)


# ---------------------------------------------------------------------------
# Cross-file analysis and file symbols
# ---------------------------------------------------------------------------


class TestTLAPlusCrossFileAndFileSymbols:

    def test_file_symbol_created(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
====
""")
        result = analyze_tlaplus(tmp_path)
        file_syms = [s for s in result.symbols if s.kind == "file"]
        assert len(file_syms) == 1
        assert file_syms[0].language == "tlaplus"

    def test_cross_file_analysis(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Helpers.tla", """\
---- MODULE Helpers ----
Helper == 42
====
""")
        make_tla_file(tmp_path, "Main.tla", """\
---- MODULE Main ----
EXTENDS Helpers
UseHelper == Helper + 1
====
""")
        result = analyze_tlaplus(tmp_path)
        # Both modules should be detected
        module_names = {s.name for s in result.symbols if s.kind == "module"}
        assert "Helpers" in module_names
        assert "Main" in module_names

        # EXTENDS edge from Main file to Helpers module
        import_edges = [
            e for e in result.edges
            if e.edge_type == "imports" and e.evidence_type == "extends"
        ]
        assert any("Helpers" in e.dst for e in import_edges)

    def test_no_tla_files(self, tmp_path: Path) -> None:
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        (tmp_path / "readme.md").write_text("# No TLA+ here")
        result = analyze_tlaplus(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_primed_variable_reference(self, tmp_path: Path) -> None:
        """Primed variables (x') should still reference x correctly."""
        from hypergumbo_lang_extended1.tlaplus import analyze_tlaplus

        make_tla_file(tmp_path, "Test.tla", """\
---- MODULE Test ----
VARIABLE x
Next == x' = x + 1
====
""")
        result = analyze_tlaplus(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        next_sym = next((s for s in result.symbols if s.name == "Next"), None)
        x_sym = next((s for s in result.symbols if s.name == "x"), None)
        assert next_sym is not None
        assert x_sym is not None
        # Next references x (both primed and unprimed)
        assert any(e.src == next_sym.id and e.dst == x_sym.id for e in ref_edges)
