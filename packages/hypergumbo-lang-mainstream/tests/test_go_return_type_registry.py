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
        # "pkg.Type" → bare "Type"
        assert _go_return_type_from_signature("(x int) http.Client") == "Client"

    def test_package_qualified_in_tuple(self) -> None:
        assert _go_return_type_from_signature(
            "(x int) (*http.Response, error)"
        ) == "Response"

    def test_nested_params_with_func_type(self) -> None:
        # Params containing parentheses shouldn't confuse the parser
        sig = "(fn func(int) bool, x int) Result"
        assert _go_return_type_from_signature(sig) == "Result"


# ---------------------------------------------------------------------------
# Integration: chained method calls via analyze_go
# ---------------------------------------------------------------------------


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
    """Skip if Go tree-sitter grammar is not installed."""
    try:
        from hypergumbo_lang_mainstream.go import _analyzer
        if not _analyzer.is_grammar_available():
            pytest.skip("Go tree-sitter grammar not available")
    except Exception:
        pytest.skip("Go analyzer not available")


class TestGoReturnTypeRegistryIntegration:
    """End-to-end: analyze Go code with chained method calls and
    verify that the return-type registry enables ``typed_receiver_call``
    edges for the chained call."""

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
            e.evidence_type == "typed_receiver_call" for e in chained_edges
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
