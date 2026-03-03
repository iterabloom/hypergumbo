"""Tests for the Circom language analyzer.

Tests the Circom (.circom) language analyzer that uses tree-sitter to extract:
- Template definitions (mapped to 'class' kind)
- Function definitions
- Main component definitions
- Signal declarations (input/output)
- Template instantiation (call) edges
- Function call edges
- Include (import) edges

Uses real tree-sitter parsing (no mocks) for correctness.
The unavailability path is tested with a mock.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import circom as circom_module
from hypergumbo_lang_extended1.circom import analyze_circom


def _make_circom_file(tmp_path: Path, name: str, content: str) -> Path:
    """Write a .circom file and return its path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class TestCircomBasicSymbols:
    """Tests for basic symbol extraction."""

    def test_template_definition(self, tmp_path: Path) -> None:
        """Template definitions produce 'class' symbols."""
        _make_circom_file(tmp_path, "mul.circom", """\
pragma circom 2.0.0;
template Multiplier(n) {
    signal input a;
    signal input b;
    signal output c;
    c <== a * b;
}
""")
        result = analyze_circom(tmp_path)
        assert not result.skipped
        templates = [s for s in result.symbols if s.kind == "class"]
        assert len(templates) == 1
        assert templates[0].name == "Multiplier"
        assert templates[0].language == "circom"

    def test_function_definition(self, tmp_path: Path) -> None:
        """Function definitions produce 'function' symbols."""
        _make_circom_file(tmp_path, "utils.circom", """\
pragma circom 2.0.0;
function factorial(n) {
    if (n <= 1) { return 1; }
    return n * factorial(n - 1);
}
function double(x) {
    return x * 2;
}
""")
        result = analyze_circom(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = {f.name for f in funcs}
        assert "factorial" in names
        assert "double" in names

    def test_main_component(self, tmp_path: Path) -> None:
        """Main component definition produces a 'variable' symbol."""
        _make_circom_file(tmp_path, "main.circom", """\
pragma circom 2.0.0;
template Adder() {
    signal input a;
    signal input b;
    signal output c;
    c <== a + b;
}
component main = Adder();
""")
        result = analyze_circom(tmp_path)
        mains = [s for s in result.symbols if s.name == "main"]
        assert len(mains) == 1
        assert mains[0].kind == "variable"

    def test_signals_as_variables(self, tmp_path: Path) -> None:
        """Signal declarations produce 'variable' symbols with visibility metadata."""
        _make_circom_file(tmp_path, "signals.circom", """\
pragma circom 2.0.0;
template MyCircuit() {
    signal input x;
    signal output y;
    signal z;
    y <== x * x;
    z <== y + 1;
}
""")
        result = analyze_circom(tmp_path)
        signals = [s for s in result.symbols
                   if s.kind == "variable" and s.name != "main"]
        names = {s.name for s in signals}
        # Should find x, y, z signals
        assert "x" in names
        assert "y" in names
        assert "z" in names

    def test_file_symbol_created(self, tmp_path: Path) -> None:
        """A file symbol is created for each .circom file."""
        _make_circom_file(tmp_path, "test.circom", """\
pragma circom 2.0.0;
template T() { signal input x; }
""")
        result = analyze_circom(tmp_path)
        files = [s for s in result.symbols if s.kind == "file"]
        assert len(files) == 1


class TestCircomEdges:
    """Tests for edge extraction."""

    def test_template_instantiation_edge(self, tmp_path: Path) -> None:
        """Component declarations create 'calls' edges to templates."""
        _make_circom_file(tmp_path, "circuit.circom", """\
pragma circom 2.0.0;
template Inner() {
    signal input a;
    signal output b;
    b <== a * 2;
}
template Outer() {
    signal input x;
    signal output y;
    component inner = Inner();
    inner.a <== x;
    y <== inner.b;
}
""")
        result = analyze_circom(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Should have at least one call from Outer to Inner
        assert len(call_edges) >= 1
        inner_sym = next(s for s in result.symbols if s.name == "Inner")
        assert any(e.dst == inner_sym.id for e in call_edges)

    def test_function_call_edge(self, tmp_path: Path) -> None:
        """Function calls create 'calls' edges."""
        _make_circom_file(tmp_path, "calls.circom", """\
pragma circom 2.0.0;
function helper(x) { return x + 1; }
template Main() {
    signal input a;
    signal output b;
    var t = helper(a);
    b <== t;
}
""")
        result = analyze_circom(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_sym = next(s for s in result.symbols if s.name == "helper")
        assert any(e.dst == helper_sym.id for e in call_edges)

    def test_include_edge(self, tmp_path: Path) -> None:
        """Include directives create 'imports' edges."""
        _make_circom_file(tmp_path, "main.circom", """\
pragma circom 2.0.0;
include "helpers.circom";
template T() { signal input x; }
""")
        result = analyze_circom(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

    def test_main_component_call_edge(self, tmp_path: Path) -> None:
        """Main component instantiation creates a 'calls' edge."""
        _make_circom_file(tmp_path, "entry.circom", """\
pragma circom 2.0.0;
template MyCircuit() {
    signal input x;
    signal output y;
    y <== x;
}
component main = MyCircuit();
""")
        result = analyze_circom(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        circuit_sym = next(s for s in result.symbols if s.name == "MyCircuit")
        assert any(e.dst == circuit_sym.id for e in call_edges)


class TestCircomCrossFile:
    """Tests for cross-file resolution."""

    def test_cross_file_template_call(self, tmp_path: Path) -> None:
        """Template instantiation resolves across files."""
        _make_circom_file(tmp_path, "lib.circom", """\
pragma circom 2.0.0;
template Hasher() {
    signal input data;
    signal output hash;
    hash <== data * data;
}
""")
        _make_circom_file(tmp_path, "main.circom", """\
pragma circom 2.0.0;
include "lib.circom";
template Verifier() {
    signal input x;
    signal output y;
    component h = Hasher();
    h.data <== x;
    y <== h.hash;
}
""")
        result = analyze_circom(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        hasher_sym = next(s for s in result.symbols if s.name == "Hasher")
        assert any(e.dst == hasher_sym.id for e in call_edges)


class TestCircomSkipped:
    """Test the unavailability code path."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Returns skipped result when tree-sitter grammar is not available."""
        _make_circom_file(tmp_path, "test.circom", "template T() {}")
        with patch.object(
            circom_module, "is_circom_tree_sitter_available", return_value=False
        ):
            with pytest.warns(UserWarning, match="Circom analysis skipped"):
                result = circom_module.analyze_circom(tmp_path)
        assert result.skipped is True
        assert len(result.symbols) == 0


class TestCircomSignalFlowConstraints:
    """Tests for signal flow constraint edge detection (WI-zijos)."""

    def test_signal_assignment_creates_references_edges(self, tmp_path: Path) -> None:
        """out <== a * b creates references edges to signals a, b, out."""
        _make_circom_file(tmp_path, "mult.circom", """
template Multiplier() {
    signal input a;
    signal input b;
    signal output out;
    out <== a * b;
}
""")
        result = analyze_circom(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        # Should have references from Multiplier to a, b, and out
        assert len(ref_edges) >= 2, (
            f"Expected >= 2 references edges for <== constraint, got {len(ref_edges)}: "
            f"{[(e.src, e.dst) for e in ref_edges]}"
        )
        ref_dst_names = {e.dst.split(":")[-2] for e in ref_edges}
        assert "a" in ref_dst_names or "b" in ref_dst_names

    def test_constraint_equality_creates_references_edges(self, tmp_path: Path) -> None:
        """a * b === c creates references edges."""
        _make_circom_file(tmp_path, "check.circom", """
template Check() {
    signal input a;
    signal input b;
    signal input c;
    a * b === c;
}
""")
        result = analyze_circom(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 2, (
            f"Expected >= 2 references edges for === constraint, got {len(ref_edges)}"
        )

    def test_constraint_evidence_type(self, tmp_path: Path) -> None:
        """Signal constraint edges have evidence_type='signal_constraint'."""
        _make_circom_file(tmp_path, "ev.circom", """
template T() {
    signal input x;
    signal output y;
    y <== x;
}
""")
        result = analyze_circom(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 1
        assert ref_edges[0].evidence_type == "signal_constraint"

    def test_no_constraint_no_references(self, tmp_path: Path) -> None:
        """Templates without constraints don't get references edges."""
        _make_circom_file(tmp_path, "empty.circom", """
template Empty() {
    signal input x;
    signal output y;
}
""")
        result = analyze_circom(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 0

    def test_regular_assignment_not_treated_as_constraint(self, tmp_path: Path) -> None:
        """x = a + 1 (regular = assignment) does not produce references edges."""
        _make_circom_file(tmp_path, "reg.circom", """
template T() {
    signal input a;
    var x;
    x = a + 1;
}
""")
        result = analyze_circom(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 0
