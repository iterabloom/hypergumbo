# SPDX-License-Identifier: AGPL-3.0-or-later
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

import warnings
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

    def test_skipped_silently_when_no_circom_files_in_tree(
        self, tmp_path: Path,
    ) -> None:
        """WI-ruman: no .circom files → no warning, even if grammar absent.

        The UAT campaign surfaced that the Circom skip warning fires on
        every ``hypergumbo run``, including on pure-Go/Java/Rust repos
        that don't contain any Circom files. The grammar's absence is
        unactionable for those users — they have nothing to analyze.
        Gate the warning on actual Circom file presence in the tree.
        """
        # tmp_path is empty — no .circom files anywhere.
        with patch.object(
            circom_module, "is_circom_tree_sitter_available", return_value=False
        ):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = circom_module.analyze_circom(tmp_path)
        # No Circom-skip warning emitted.
        circom_warnings = [
            w for w in caught
            if issubclass(w.category, UserWarning)
            and "Circom analysis skipped" in str(w.message)
        ]
        assert circom_warnings == []
        # Result is still a skipped result (linker context unchanged).
        assert result.skipped is True
        assert len(result.symbols) == 0

    def test_skip_toolchain_is_dict_not_str(self, tmp_path: Path) -> None:
        """WI-luliv: skip-path toolchain must be a ``Dict[str, str]``, not a str.

        The skip-path historically passed ``toolchain="tree-sitter-circom"``
        (a bare string) to the ``AnalysisRun`` constructor. ``to_dict`` then
        emitted a schema-violating string, and ``finalize`` recomputes
        run_signature via ``toolchain.get("name", "")`` — which raises
        ``AttributeError`` on a str. Assert the dict shape and that the
        finalize-style ``.get`` access does not crash.
        """
        _make_circom_file(tmp_path, "test.circom", "template T() {}")
        with patch.object(
            circom_module, "is_circom_tree_sitter_available", return_value=False
        ):
            with pytest.warns(UserWarning, match="Circom analysis skipped"):
                result = circom_module.analyze_circom(tmp_path)
        tc = result.run.toolchain
        assert isinstance(tc, dict), f"toolchain must be a dict, got {type(tc).__name__}"
        assert tc.get("name") == "python"  # WI-zugol: names the analyzing toolchain (python), not the language
        assert "version" in tc
        # to_dict must serialize a dict (schema contract); finalize's
        # ``run["toolchain"].get("name", "")`` must not crash.
        assert isinstance(result.run.to_dict()["toolchain"], dict)
        assert result.run.to_dict()["toolchain"].get("name", "") == "python"


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


class TestCircomCyclomaticComplexity:
    """INV-loguk slice C: callable circom symbols carry non-null CC + LOC.
    Real-grammar verification (if/for/while/ternary + &&/|| (templates excluded))."""

    def test_branchy_callables_have_expected_cc(self, tmp_path) -> None:
        from hypergumbo_lang_extended1.circom import analyze_circom
        (tmp_path / 'c.circom').write_text('pragma circom 2.0.0;\nfunction classify(a, b, n) {\n    var total = 0;\n    if (a > 0 && b > 0) { total = a + b; }\n    else if (a < 0 || b < 0) { total = a - b; }\n    else { total = 0; }\n    for (var i = 0; i < n; i++) { total += i; }\n    var j = 0;\n    while (j < n) { total += j; j++; }\n    return total;\n}\ntemplate Multiplier(N) {\n    signal input a;\n    signal output c;\n    c <== a * a;\n}\n')
        result = analyze_circom(tmp_path)
        assert not result.skipped
        by = {s.name: s for s in result.symbols if s.kind in ('function',)}
        m = by.get('classify') or next(s for n, s in by.items() if n.split('.')[-1] == 'classify' or n.endswith('classify'))
        assert m.cyclomatic_complexity == 7, m.cyclomatic_complexity
        assert m.line_span is not None

    def test_callables_non_null_non_callables_null(self, tmp_path) -> None:
        from hypergumbo_lang_extended1.circom import analyze_circom
        (tmp_path / 'c.circom').write_text('pragma circom 2.0.0;\nfunction classify(a, b, n) {\n    var total = 0;\n    if (a > 0 && b > 0) { total = a + b; }\n    else if (a < 0 || b < 0) { total = a - b; }\n    else { total = 0; }\n    for (var i = 0; i < n; i++) { total += i; }\n    var j = 0;\n    while (j < n) { total += j; j++; }\n    return total;\n}\ntemplate Multiplier(N) {\n    signal input a;\n    signal output c;\n    c <== a * a;\n}\n')
        result = analyze_circom(tmp_path)
        callables = [s for s in result.symbols if s.kind in ('function',)]
        assert callables
        for s in callables:
            assert s.cyclomatic_complexity is not None, (s.kind, s.name)
            assert s.line_span is not None, (s.kind, s.name)
        for s in result.symbols:
            if s.kind not in ('function',):
                assert s.cyclomatic_complexity is None, (s.kind, s.name)


# -- lexical scope of signal references (WI-loviz) -----------------------------


def _signal_scopes(result) -> dict[tuple[str, str], dict[str, set[str]]]:
    """Map (path, template name) to {signal name: signal ids}, by span.

    Ownership is read from the symbols' spans, not from the analyzer's own
    bookkeeping, so the tests do not trust the code under test: a signal
    belongs to the template whose span contains it.
    """
    templates = [s for s in result.symbols if s.kind == "class"]
    scopes: dict[tuple[str, str], dict[str, set[str]]] = {
        (t.path, t.name): {} for t in templates
    }
    for sig in result.symbols:
        if sig.kind != "variable" or sig.name == "main":
            continue
        owner = next(
            t for t in templates
            if t.path == sig.path
            and t.span.start_line <= sig.span.start_line <= t.span.end_line
        )
        scopes[(owner.path, owner.name)].setdefault(sig.name, set()).add(sig.id)
    return scopes


def _template(result, path: str, name: str):
    return next(
        s for s in result.symbols
        if s.kind == "class" and s.path == path and s.name == name
    )


def _constraint_targets(result, src_id: str) -> set[str]:
    return {
        e.dst for e in result.edges
        if e.edge_type == "references" and e.src == src_id
        and e.evidence_type == "signal_constraint"
    }


def _ids(scope: dict[str, set[str]], *names: str) -> set[str]:
    out: set[str] = set()
    for n in names:
        out |= scope[n]
    return out


class TestCircomSignalScope:
    """A signal reference in template T resolves to T's own signal of that
    name and to nothing else; only a component access ``c.in`` (with
    ``component c = B()``) reaches another template's signal, and then only
    B's (WI-loviz)."""

    _TWO_TEMPLATES = """\
pragma circom 2.0.0;
template A() {
    signal input in;
    signal output out;
    out <== in;
}
template B() {
    signal input in;
    signal output out;
    out <== in;
}
"""

    def test_same_file_templates_reference_their_own_signals(
        self, tmp_path: Path
    ) -> None:
        _make_circom_file(tmp_path, "ab.circom", self._TWO_TEMPLATES)
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        for name in ("A", "B"):
            tpl = _template(result, "ab.circom", name)
            own = _ids(scopes[("ab.circom", name)], "in", "out")
            assert len(own) == 2
            assert _constraint_targets(result, tpl.id) == own, name

    def test_cross_file_templates_reference_their_own_signals(
        self, tmp_path: Path
    ) -> None:
        a, b = self._TWO_TEMPLATES.split("template B")
        _make_circom_file(tmp_path, "a.circom", a)
        _make_circom_file(tmp_path, "b.circom", "template B" + b)
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        for path, name in (("a.circom", "A"), ("b.circom", "B")):
            tpl = _template(result, path, name)
            own = _ids(scopes[(path, name)], "in", "out")
            assert _constraint_targets(result, tpl.id) == own, name

    def test_non_signal_names_bind_to_no_other_templates_signal(
        self, tmp_path: Path
    ) -> None:
        """A parameter, a var and a loop index named like another
        template's signals reference nothing."""
        _make_circom_file(tmp_path, "p.circom", """\
pragma circom 2.0.0;
template Other() {
    signal input n;
    signal input k;
    signal input i;
    signal output o;
    o <== n + k + i;
}
template User(n) {
    signal input x;
    signal output y[2];
    var k = 3;
    for (var i = 0; i < 2; i++) {
        y[i] <== x * n * k * i;
    }
}
""")
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        user = _template(result, "p.circom", "User")
        assert _constraint_targets(result, user.id) == _ids(
            scopes[("p.circom", "User")], "x", "y",
        )

    def test_component_access_reaches_only_the_instantiated_template(
        self, tmp_path: Path
    ) -> None:
        """``c.in`` / ``c[i].out`` reach B's signals, not A's (declared
        last, so a last-wins bare-name lookup would pick A), and not C's."""
        _make_circom_file(tmp_path, "c.circom", """\
pragma circom 2.0.0;
template B() {
    signal input in;
    signal output out;
    out <== in;
}
template C() {
    signal input in;
    signal output out;
    signal output w[2];
    component c = B();
    component d[2];
    c.in <== in;
    out <== c.out;
    for (var i = 0; i < 2; i++) {
        d[i] = B();
        d[i].in <== in;
        w[i] <== d[i].out * 2;
    }
    in === d[1].out;
}
template A() {
    signal input in;
    signal output out;
    out <== in;
}
""")
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        c = _template(result, "c.circom", "C")
        expected = _ids(scopes[("c.circom", "C")], "in", "out", "w") | _ids(
            scopes[("c.circom", "B")], "in", "out",
        )
        assert _constraint_targets(result, c.id) == expected

    def test_member_access_on_a_non_component_is_an_own_reference(
        self, tmp_path: Path
    ) -> None:
        """``in.x`` with ``in`` a signal (not a component) references the
        template's own ``in``; ``(in).x`` has no named base and also only
        references own signals."""
        _make_circom_file(tmp_path, "e.circom", """\
template E() {
    signal input in;
    signal output out;
    out <== (in).x;
    out <== in.x;
}
template F() {
    signal input x;
    signal output y;
    y <== x;
}
""")
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        e = _template(result, "e.circom", "E")
        assert _constraint_targets(result, e.id) == _ids(
            scopes[("e.circom", "E")], "in", "out",
        )

    def test_component_access_follows_the_included_definition(
        self, tmp_path: Path
    ) -> None:
        """Two files define ``template B``; the user includes one of them.
        The component's signals and the calls edge are the included B's."""
        body = """\
template B() {
    signal input in;
    signal output out;
    out <== in;
}
"""
        (tmp_path / "lib").mkdir()
        (tmp_path / "vendor").mkdir()
        (tmp_path / "app").mkdir()
        _make_circom_file(tmp_path, "lib/b.circom", body)
        _make_circom_file(tmp_path, "vendor/b.circom", body)
        _make_circom_file(tmp_path, "app/main.circom", """\
pragma circom 2.0.0;
include "../lib/b.circom";
template M() {
    signal input x;
    signal output y;
    component c = B();
    c.in <== x;
    y <== c.out;
}
component main = M();
""")
        _make_circom_file(tmp_path, "app/other.circom", """\
include "../vendor/b.circom";
template N() {
    signal input x;
    component c = B();
    c.in <== x;
}
""")
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        lib_b = _template(result, "lib/b.circom", "B")
        vendor_b = _template(result, "vendor/b.circom", "B")
        for path, user, b_path, b in (
            ("app/main.circom", "M", "lib/b.circom", lib_b),
            ("app/other.circom", "N", "vendor/b.circom", vendor_b),
        ):
            tpl = _template(result, path, user)
            targets = _constraint_targets(result, tpl.id)
            assert _ids(scopes[(b_path, "B")], "in") <= targets, user
            other = vendor_b if b is lib_b else lib_b
            assert not (_ids(scopes[(other.path, "B")], "in", "out") & targets)
            calls = {
                e.dst for e in result.edges
                if e.edge_type == "calls" and e.src == tpl.id
            }
            assert calls == {b.id}, user

    def test_same_file_definition_wins_over_another_files(
        self, tmp_path: Path
    ) -> None:
        """Each of two files defines and calls its own ``helper``; a
        last-wins bare-name lookup sends one of the two calls astray,
        whichever order the files are read in."""
        for stem, k in (("x", 1), ("y", 2)):
            _make_circom_file(tmp_path, f"{stem}.circom", f"""\
function helper(v) {{ return v + {k}; }}
template T{stem}() {{
    signal input a;
    signal output b;
    var t = helper(a);
    b <== t;
}}
""")
        result = analyze_circom(tmp_path)
        for stem in ("x", "y"):
            path = f"{stem}.circom"
            t = _template(result, path, f"T{stem}")
            helper = next(
                s for s in result.symbols
                if s.name == "helper" and s.path == path
            )
            calls = {
                e.dst for e in result.edges
                if e.edge_type == "calls" and e.src == t.id
            }
            assert calls == {helper.id}, path

    def test_signal_declaration_initializer_is_a_constraint(
        self, tmp_path: Path
    ) -> None:
        """``signal output out <== in * 2;`` constrains like ``out <== in*2``."""
        _make_circom_file(tmp_path, "d.circom", """\
template D() {
    signal input in;
    signal output out <== in * 2;
}
""")
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        d = _template(result, "d.circom", "D")
        assert _constraint_targets(result, d.id) == _ids(
            scopes[("d.circom", "D")], "in", "out",
        )

    def test_every_name_of_a_multi_name_declaration_is_a_signal(
        self, tmp_path: Path
    ) -> None:
        _make_circom_file(tmp_path, "m.circom", """\
template M() {
    signal input a, b;
    signal output c;
    c <== a * b;
}
""")
        result = analyze_circom(tmp_path)
        scopes = _signal_scopes(result)
        own = scopes[("m.circom", "M")]
        assert set(own) == {"a", "b", "c"}
        sigs = [s for s in result.symbols if s.kind == "variable"]
        assert len({s.stable_id for s in sigs}) == len(sigs) == 3
        m = _template(result, "m.circom", "M")
        assert _constraint_targets(result, m.id) == _ids(own, "a", "b", "c")


class TestCircomIncludeTargets:
    """An include directive's edge points at the included file's node when
    the file is in the repository (WI-loviz sweep)."""

    def test_relative_include_targets_the_file_node(self, tmp_path: Path) -> None:
        (tmp_path / "circuits").mkdir()
        (tmp_path / "test").mkdir()
        _make_circom_file(tmp_path, "circuits/bitify.circom", "template Num2Bits() {}\n")
        _make_circom_file(
            tmp_path, "test/t.circom",
            'include "../circuits/bitify.circom";\ninclude "missing.circom";\n',
        )
        result = analyze_circom(tmp_path)
        files = {s.path: s.id for s in result.symbols if s.kind == "file"}
        dsts = {
            e.dst for e in result.edges
            if e.edge_type == "imports" and e.src == files["test/t.circom"]
        }
        assert files["circuits/bitify.circom"] in dsts
        # An include the repository cannot satisfy keeps its unresolved target.
        assert "circom:missing.circom:0-0:file:file" in dsts

    def test_library_include_resolves_by_path_suffix(self, tmp_path: Path) -> None:
        """``include "circomlib/circuits/x.circom"`` (a ``-l`` library path)
        resolves to the repository file whose path ends with it."""
        lib = tmp_path / "node_modules" / "circomlib" / "circuits"
        lib.mkdir(parents=True)
        _make_circom_file(
            tmp_path, "node_modules/circomlib/circuits/poseidon.circom",
            "template Poseidon() { signal input in; }\n",
        )
        _make_circom_file(
            tmp_path, "node_modules/circomlib/circuits/bitify.circom",
            "template Num2Bits() {}\n",
        )
        _make_circom_file(tmp_path, "poseidon2.circom", "template Poseidon2() {}\n")
        _make_circom_file(
            tmp_path, "main.circom",
            'include "circomlib/circuits/poseidon.circom";\n'
            'include "poseidon.circom";\n'
            # Climbs past the repository root: resolved by its tail.
            'include "../../circuits/bitify.circom";\n',
        )
        result = analyze_circom(tmp_path)
        files = {s.path: s.id for s in result.symbols if s.kind == "file"}
        dsts = {
            e.dst for e in result.edges
            if e.edge_type == "imports" and e.src == files["main.circom"]
        }
        # Both poseidon directives name the library file (the bare one by its
        # tail); poseidon2.circom does not end in "/poseidon.circom".
        assert dsts == {
            files["node_modules/circomlib/circuits/poseidon.circom"],
            files["node_modules/circomlib/circuits/bitify.circom"],
        }


class TestCircomResolutionFallbacks:
    """A name no include makes visible: the only definition, else the
    last registered at the ambiguity confidence, else nothing (WI-loviz)."""

    def test_unique_ambiguous_and_unknown_names(self, tmp_path: Path) -> None:
        (tmp_path / "lib1").mkdir()
        (tmp_path / "lib2").mkdir()
        _make_circom_file(tmp_path, "lib1/solo.circom", "template Solo() {}\n")
        _make_circom_file(tmp_path, "lib1/dup.circom", "template Dup() {}\n")
        _make_circom_file(tmp_path, "lib2/dup.circom", "template Dup() {}\n")
        _make_circom_file(tmp_path, "user.circom", """\
template U() {
    signal input x;
    component s = Solo();
    component d = Dup();
    var z = unknown(1);
}
""")
        result = analyze_circom(tmp_path)
        u = _template(result, "user.circom", "U")
        solo = _template(result, "lib1/solo.circom", "Solo")
        dups = {
            s.id for s in result.symbols if s.kind == "class" and s.name == "Dup"
        }
        calls = [
            e for e in result.edges
            if e.edge_type == "calls" and e.src == u.id
        ]
        assert {e.dst for e in calls} <= {solo.id} | dups
        solo_edges = [e for e in calls if e.dst == solo.id]
        dup_edges = [e for e in calls if e.dst in dups]
        assert solo_edges and dup_edges
        # The ambiguous pick is marked as such on the component-declaration
        # branch (the call_expression branch keeps its fixed 0.85; see
        # WI-volat for that duplicate edge).
        assert min(e.confidence for e in dup_edges) < min(
            e.confidence for e in solo_edges
        )
