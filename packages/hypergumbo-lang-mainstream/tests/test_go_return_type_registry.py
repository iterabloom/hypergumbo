# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Go return-type registry (history: WI-kuroj, INV-dihos).

The return-type registry enables chained receiver-type resolution:
when ``x := e.Query()`` and ``e`` has type ``Engine``, the registry
maps ``Engine.Query → Result`` so ``x`` gets type ``Result`` and a
subsequent ``x.Rows()`` resolves to ``Result.Rows``.

These tests exercise:
1. ``_go_return_type_from_signature`` — the signature parser
2. Integration via ``analyze_go`` — chained method calls produce
   ``typed_receiver_call`` edges
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from hypergumbo_lang_mainstream.go import (
    _bare_go_type,
    _go_return_type_from_signature,
)


# ---------------------------------------------------------------------------
# Unit tests for _go_return_type_from_signature
# ---------------------------------------------------------------------------


class TestGoReturnTypeFromSignature:
    """Parse return type from Go function signature strings."""

    def test_single_return_type(self) -> None:
        assert _go_return_type_from_signature("(x int) Result") == "Result"

    def test_pointer_return_type(self) -> None:
        assert _go_return_type_from_signature("(x int) *Result") == "Result"

    def test_tuple_return_with_error(self) -> None:
        assert _go_return_type_from_signature("(x int) (Result, error)") == "Result"

    def test_tuple_return_with_pointer_and_error(self) -> None:
        assert _go_return_type_from_signature("(x int) (*Result, error)") == "Result"

    def test_tuple_return_all_builtins(self) -> None:
        # (int, error) → no non-builtin types → None
        assert _go_return_type_from_signature("(x int) (int, error)") is None

    def test_tuple_return_ambiguous(self) -> None:
        # Two non-builtin types → ambiguous → None
        assert _go_return_type_from_signature("(x int) (Foo, Bar)") is None

    def test_no_return_type(self) -> None:
        assert _go_return_type_from_signature("(x int)") is None

    def test_void_return(self) -> None:
        assert _go_return_type_from_signature("()") is None

    def test_empty_signature(self) -> None:
        assert _go_return_type_from_signature("") is None

    def test_none_signature(self) -> None:
        assert _go_return_type_from_signature(None) is None

    def test_return_part_is_whitespace_only(self) -> None:
        # Signature with trailing whitespace after params but no actual type
        assert _go_return_type_from_signature("(x int) ") is None

    def test_builtin_return_type(self) -> None:
        assert _go_return_type_from_signature("(x int) error") is None
        assert _go_return_type_from_signature("(x int) string") is None
        assert _go_return_type_from_signature("(x int) bool") is None

    def test_package_qualified_return_type(self) -> None:
        """The qualifier SURVIVES; the bare name is derived (``_bare_go_type``)
        where a registry key or symbol lookup needs one -- see
        TestExternalReturnTypeReachesTheModuleSlot for why the stored form has
        to be the qualified one (history: WI-doluf)."""
        assert _go_return_type_from_signature("(x int) http.Client") == "http.Client"

    def test_package_qualified_in_tuple(self) -> None:
        assert _go_return_type_from_signature(
            "(x int) (*http.Response, error)"
        ) == "http.Response"

    def test_nested_params_with_func_type(self) -> None:
        # Params containing parentheses shouldn't confuse the parser
        sig = "(fn func(int) bool, x int) Result"
        assert _go_return_type_from_signature(sig) == "Result"


# ---------------------------------------------------------------------------
# Integration: chained method calls via analyze_go
# ---------------------------------------------------------------------------


class TestUnwrapRhsExpression:
    """The shared unwrap, asserted directly.

    ``short_var_declaration``'s last child is an ``expression_list``, never the
    expression. Two consumers ask "what is on the right" -- ``_type_from_rhs``
    and the return-type registry lookup -- and both go through this helper. A
    consumer that tests the wrapper itself (``rhs.type == "call_expression"``)
    can never match and is dead code (history: WI-jolif).
    """

    @staticmethod
    def _rhs_of(source: str):
        import tree_sitter
        import tree_sitter_go

        from hypergumbo_lang_mainstream.go import iter_tree

        parser = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_go.language()),
        )
        tree = parser.parse(source.encode("utf-8"))
        for node in iter_tree(tree.root_node):
            if node.type == "short_var_declaration":
                return node.children[-1]
        raise AssertionError("no short_var_declaration in fixture")

    def test_wrapper_is_peeled_to_the_call(self, go_available: None) -> None:
        from hypergumbo_lang_mainstream.go import _unwrap_rhs_expression

        rhs = self._rhs_of("package main\nfunc f() { x := e.Query() }\n")
        assert rhs.type == "expression_list", (
            "if this ever stops being a wrapper the fix is unnecessary and the "
            "guard it replaced was not dead after all"
        )
        assert _unwrap_rhs_expression(rhs).type == "call_expression"

    def test_a_list_with_no_type_bearing_expression_yields_none(
        self, go_available: None,
    ) -> None:
        """``x := 42`` carries an int literal — no type to infer, and the
        registry lookup must not be handed one."""
        from hypergumbo_lang_mainstream.go import _unwrap_rhs_expression

        rhs = self._rhs_of("package main\nfunc f() { x := 42 }\n")
        assert _unwrap_rhs_expression(rhs) is None

    def test_a_bare_expression_passes_through(self, go_available: None) -> None:
        """Not every caller hands in a wrapper, so the helper is idempotent."""
        from hypergumbo_lang_mainstream.go import _unwrap_rhs_expression

        rhs = self._rhs_of("package main\nfunc f() { x := &Server{} }\n")
        inner = _unwrap_rhs_expression(rhs)
        assert _unwrap_rhs_expression(inner) is inner


def _make_go_module(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a fake Go module with the given files."""
    repo = tmp_path / "fakerepo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
    for name, content in files.items():
        fpath = repo / name
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)
    return repo


@pytest.fixture()
def go_available():
    """Skip ONLY when the Go grammar is genuinely absent.

    The probe is a direct call to ``is_grammar_available`` (a module function
    in ``hypergumbo_core.analyze.base``) with no exception handler. Wrapping
    it in a bare ``except Exception: pytest.skip`` would turn any error in the
    probe -- a typo included -- into a silent skip of every test using this
    fixture: a green tick over a hole. If the probe breaks, the suite must
    break loudly (history: WI-jolif).
    """
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


class TestGoReturnTypeRegistryIntegration:
    """End-to-end: analyze Go code with chained method calls and
    verify that the return-type registry enables ``typed_receiver_call``
    edges for the chained call.

    The unit tests above cover what the registry holds; these cover whether
    the call-site lookup ever reaches it. That lookup must unwrap the
    ``short_var_declaration``'s ``expression_list`` WRAPPER (via
    ``_unwrap_rhs_expression``, as ``_type_from_rhs`` does) before checking
    for a ``call_expression``; a guard on the wrapper's own type can never be
    satisfied, leaving the registry built but unconsulted (history: WI-jolif,
    WI-kuroj).
    """

    def test_chained_method_call_resolves_via_registry(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        """Given:
            type Engine struct{}
            func (e *Engine) Query() *Result { ... }
            type Result struct{}
            func (r *Result) Rows() []string { ... }
            func main() {
                e := &Engine{}
                result := e.Query()   // result has type Result via registry
                result.Rows()         // should resolve to Result.Rows
            }
        The return-type registry should infer that ``result`` has type
        ``Result`` (from Engine.Query's return type), enabling the
        second call to resolve as ``typed_receiver_call``.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "engine.go": '''\
package main

type Engine struct{}

func (e *Engine) Query() *Result {
    return &Result{}
}

type Result struct{}

func (r *Result) Rows() []string {
    return nil
}

func main() {
    e := &Engine{}
    result := e.Query()
    result.Rows()
}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped

        # Find edges where src contains "main" and dst contains "Result.Rows"
        chained_edges = [
            edge for edge in analysis.edges
            if "main" in edge.src
            and "Result.Rows" in edge.dst
            and edge.edge_type == "calls"
        ]
        assert len(chained_edges) >= 1, (
            f"Expected a call edge from main to Result.Rows via chained "
            f"return-type resolution; got edges: "
            f"{[(e.src, e.dst, e.evidence_type) for e in analysis.edges if 'Rows' in e.dst]}"
        )
        # Verify it was resolved via typed_receiver_call (not unresolved)
        assert any(
            (e.evidence_type == "ast_call" and e.meta.get("call_construct") == "method" and e.meta.get("resolution_quality") == "typed_receiver") for e in chained_edges
        ), (
            f"Chained call should resolve as typed_receiver_call; "
            f"got evidence types: {[e.evidence_type for e in chained_edges]}"
        )

    def test_constructor_return_type_enables_chaining(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        """NewFoo() constructor pattern combined with return-type chaining:
            e := NewEngine()       → e has type Engine (constructor inference)
            result := e.Query()    → result has type Result (registry)
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "engine.go": '''\
package main

type Engine struct{}

func NewEngine() *Engine {
    return &Engine{}
}

func (e *Engine) Query() *Result {
    return &Result{}
}

type Result struct{}

func (r *Result) Execute() bool {
    return true
}

func main() {
    e := NewEngine()
    result := e.Query()
    result.Execute()
}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped

        # The full chain: NewEngine → Engine, e.Query() → Result,
        # result.Execute() → Result.Execute
        execute_edges = [
            edge for edge in analysis.edges
            if "main" in edge.src
            and "Result.Execute" in edge.dst
            and edge.edge_type == "calls"
        ]
        assert len(execute_edges) >= 1, (
            f"Expected call from main to Result.Execute via chained "
            f"return-type resolution; got edges with Execute: "
            f"{[(e.src, e.dst, e.evidence_type) for e in analysis.edges if 'Execute' in e.dst]}"
        )

    def test_tuple_return_with_error_resolves(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        """Go tuple return (Result, error) → pick non-error type.

            result, err := e.Query()  → result has type Result
            result.Rows()             → resolves to Result.Rows
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "engine.go": '''\
package main

type Engine struct{}

func (e *Engine) Query() (*Result, error) {
    return &Result{}, nil
}

type Result struct{}

func (r *Result) Rows() []string {
    return nil
}

func main() {
    e := &Engine{}
    result, _ := e.Query()
    result.Rows()
}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped

        # result.Rows() should resolve via return-type registry
        rows_edges = [
            edge for edge in analysis.edges
            if "main" in edge.src
            and "Result.Rows" in edge.dst
            and edge.edge_type == "calls"
        ]
        assert len(rows_edges) >= 1, (
            f"Expected call from main to Result.Rows via tuple return "
            f"registry resolution; got: "
            f"{[(e.src, e.dst, e.evidence_type) for e in analysis.edges if 'Rows' in e.dst]}"
        )


class TestExternalReturnTypeReachesTheModuleSlot:
    """A return-value-inferred receiver must keep its PACKAGE.

    A DECLARED external receiver type reaches the io-boundary module slot --
    ``func f(conn net.Conn)`` emits dst ``go:net.Conn:0-0:Read:unresolved``,
    which the catalogue's ``net.Conn.Read`` row matches. A type INFERRED FROM
    A RETURN VALUE must reach it the same way: a slot holding the ``external``
    placeholder matches no method row, leaving go's receive surface
    unreachable on code that assigns a stdlib handle from a factory.

    In-repo symbols are stored unqualified, so the SYMBOL LOOKUP wants the
    bare name while the MODULE SLOT needs the package. Storing only the bare
    form would serve the slot a placeholder. So the registry stores the
    qualified form, and the bare form is DERIVED where it is needed
    (``_bare_go_type``), as the call-site receiver path does for declared
    types (``bare_recv``) -- one stored form, so the two cannot drift apart
    (history: WI-doluf, INV-linub).
    """

    def test_the_fold_is_one_function(self) -> None:
        """``_bare_go_type`` is the single place a qualified value becomes a
        key. Asserted directly so an open-coded ``rsplit`` at a call site is a
        visible divergence rather than an invisible one."""
        assert _bare_go_type("net.Conn") == "Conn"
        assert _bare_go_type("promql.Query") == "Query"
        assert _bare_go_type("Result") == "Result"
        assert _bare_go_type("") == ""

    def test_signature_parser_keeps_the_package_qualifier(self) -> None:
        """The unit half. ``net.Conn`` must survive as ``net.Conn``."""
        assert _go_return_type_from_signature("(x int) net.Conn") == "net.Conn"
        assert _go_return_type_from_signature(
            "(a, b string) (net.Listener, error)"
        ) == "net.Listener"
        assert _go_return_type_from_signature(
            "(x int) (*http.Response, error)"
        ) == "http.Response"

    def test_unqualified_return_types_are_unchanged(self) -> None:
        """CONTROL. The in-repo case must not move -- chained receiver
        resolution rests on it (INV-dihos), and a change here would silently
        regress it."""
        assert _go_return_type_from_signature("(x int) Result") == "Result"
        assert _go_return_type_from_signature("(x int) *Result") == "Result"
        assert _go_return_type_from_signature("(x int) (Result, error)") == "Result"
        assert _go_return_type_from_signature("(x int) error") is None

    def test_factory_returning_a_stdlib_type_reaches_the_module_slot(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        """THE BEHAVIOURAL HALF, and the one that matters.

        ``conn := makeConn()`` where ``makeConn`` is declared IN THIS REPO
        returning ``net.Conn``. The registry has the return type, so the only
        question is whether the package survives into the emitted dst.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "conn.go": '''\
package main

import "net"

func makeConn() net.Conn {
    var c net.Conn
    return c
}

func readIt() {
    conn := makeConn()
    buf := make([]byte, 16)
    _, _ = conn.Read(buf)
}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped

        # The ANALYZER's unresolved-dst suffix is ``:unresolved``; the
        # ``:external_symbol`` spelling is the pipeline-level form the survey
        # emits later. Assert the shape this producer actually makes.
        reads = [e for e in analysis.edges
                 if e.edge_type == "calls" and ":Read:" in e.dst]
        assert reads, "no call edge emitted for conn.Read"
        assert not any(":external:" in e.dst for e in reads), (
            "the receiver's package was dropped; dst still names the 'external' "
            f"placeholder: {[e.dst for e in reads]}"
        )
        # The slot names the TYPE under its package (history: INV-vusum).
        assert any(e.dst.startswith("go:net.Conn:") for e in reads), (
            f"expected the dst to name net.Conn; got {[e.dst for e in reads]}"
        )

    def test_in_repo_factory_return_type_still_resolves_to_the_repo_symbol(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        """THE CONTROL THAT COSTS SOMETHING.

        A factory returning an IN-REPO type must still resolve to that repo's
        symbol, not acquire a bogus package hint. Without this test, a change
        to the registry's stored form could trade one unreachable surface for
        another and leave the suite green.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "local.go": '''\
package main

type Result struct{}

func (r *Result) Rows() []string { return nil }

func makeResult() *Result { return &Result{} }

func useIt() {
    res := makeResult()
    res.Rows()
}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped
        assert any(
            e.edge_type == "calls" and "Result.Rows" in e.dst and "useIt" in e.src
            for e in analysis.edges
        ), (
            "in-repo factory chaining regressed: "
            f"{[(e.src, e.dst) for e in analysis.edges if 'Rows' in e.dst]}"
        )
