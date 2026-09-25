# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Go return-type registry (WI-kuroj / INV-dihos Phase 1).

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
        """The qualifier SURVIVES (WI-doluf). It used to be stripped here, and
        the strip is now derived at the two sites that need a bare name -- see
        TestExternalReturnTypeReachesTheModuleSlot for why the stored form has
        to be the qualified one."""
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
    """The shared unwrap, asserted directly — it is the whole WI-jolif fix.

    ``short_var_declaration``'s last child is an ``expression_list``, never the
    expression. Two consumers ask "what is on the right": ``_type_from_rhs``
    unwrapped it and worked, the registry lookup did not and was dead code. One
    helper now answers for both.
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

    This fixture used to probe ``_analyzer.is_grammar_available()`` inside a bare
    ``except Exception: pytest.skip``. No such method exists — ``is_grammar_available``
    is a module function in ``hypergumbo_core.analyze.base`` — so every call raised
    ``AttributeError``, the handler swallowed it, and all three integration tests below
    reported as skipped on a machine where the grammar was installed and working. They
    had never executed. A skip reachable by a typo is a green tick over a hole, so the
    probe is now a direct call with no handler: if it breaks, the suite breaks loudly.
    """
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


class TestGoReturnTypeRegistryIntegration:
    """End-to-end: analyze Go code with chained method calls and
    verify that the return-type registry enables ``typed_receiver_call``
    edges for the chained call.

    WI-jolif, CLOSED. These three never executed: the ``go_available`` fixture
    probed a method that does not exist inside a bare ``except Exception:
    pytest.skip``, so every call raised ``AttributeError``, the handler swallowed
    it, and all three reported "grammar unavailable" on a machine where the
    grammar is installed and the 15 unit tests beside them pass. The unit half of
    the feature was covered; the end-to-end half was a green tick over a hole.

    WHEN THE FIXTURE WAS REPAIRED, ALL THREE FAILED, and they were marked
    ``xfail(strict=True)`` rather than left skipped so they would RUN and fail
    honestly. The defect they were failing on: the WI-kuroj registry lookup
    guarded on ``rhs.type == "call_expression"``, but a
    ``short_var_declaration``'s last child is an ``expression_list`` WRAPPER —
    a condition that node can never satisfy. The block was dead from the day it
    shipped, so the registry was built, threaded through three call layers, and
    consulted by a branch nothing could reach. It now unwraps through the same
    helper ``_type_from_rhs`` uses. ``strict=True`` is what turned the fix into a
    red suite demanding this marker's removal, which is exactly the signal it was
    put there for.
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
    """WI-doluf: a return-value-inferred receiver must keep its PACKAGE.

    THE DEFECT, MEASURED (INV-linub L3 residual). A DECLARED external receiver
    type reaches the io-boundary module slot -- ``func f(conn net.Conn)`` emits
    dst ``go:net:0-0:Read``, which the catalogue's ``net.Conn.Read`` row
    matches. A type INFERRED FROM A RETURN VALUE did not: the slot was filled
    with the ``external`` placeholder, so no method row could ever match it,
    and go's real receive surface was unreachable on any code that assigns a
    stdlib handle from a factory.

    WHY IT DROPPED. ``_go_return_type_from_signature`` stripped the package
    qualifier (``net.Conn`` -> ``Conn``) because in-repo symbols are stored
    unqualified. That is true of the SYMBOL LOOKUP and false of the MODULE
    SLOT, which is the one consumer that needs the package. One fact was being
    stored in the form only one of its two consumers wanted, so the other was
    silently served a placeholder.

    THE FIX IS DERIVATION, NOT A SECOND HOME: the registry now stores the
    qualified truth and the symbol-lookup path strips it where it needs a bare
    name -- which the receiver path at the call site already did for declared
    types (``bare_recv``), so the two forms cannot drift apart.
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
        """CONTROL. The in-repo case must not move -- INV-dihos Phase 1 rests
        on it, and a change here would be a silent regression in chained
        receiver resolution rather than a fix."""
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
        assert any(":net:" in e.dst for e in reads), (
            f"expected the dst to name package 'net'; got {[e.dst for e in reads]}"
        )

    def test_in_repo_factory_return_type_still_resolves_to_the_repo_symbol(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        """THE CONTROL THAT COSTS SOMETHING.

        A factory returning an IN-REPO type must still resolve to that repo's
        symbol, not acquire a bogus package hint. If qualifying the registry
        broke this, the fix would have traded one unreachable surface for
        another and the suite would still be green without this test.
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
