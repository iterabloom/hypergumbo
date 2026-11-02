# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jonin: the Go analyzer writes ``meta["parameters"]``, the declared home.

ADR-0058 makes ``Symbol.signature`` display-only: a fact inside it is read from
where it is declared, and a parameter's is ``meta["parameters"]`` in the
``overload_arity.parameter_entry`` shape. Go wrote none, so the first version
of the ``ServeHTTP`` handler rule re-parsed the signature (PR #2014, reverted).
These tests pin the producer for functions, methods and interface method
specs, and that a spec, which has no body, carries ``abstract`` (the marking
cpp and kotlin give an abstract member) so the rule tells an interface's own
``ServeHTTP`` from an implementation without reading anything absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.framework_patterns import enrich_symbols
from hypergumbo_core.overload_arity import parameter_entry
from hypergumbo_lang_mainstream.go import analyze_go

_SOURCE = (
    "package main\n\n"
    'import "net/http"\n\n'
    "func join(a, b int, sep string, rest ...string) string { return sep }\n\n"
    "func none() {}\n\n"
    "func unnamed(int, *http.Request) {}\n\n"
    "type Handler interface {\n"
    "\tServeHTTP(w http.ResponseWriter, r *http.Request)\n"
    "}\n\n"
    "type saver struct{}\n\n"
    "func (s *saver) ServeHTTP(w http.ResponseWriter, r *http.Request) {}\n"
)


@pytest.fixture(scope="module")
def symbols(tmp_path_factory: pytest.TempPathFactory) -> dict:
    repo = tmp_path_factory.mktemp("goparams")
    (repo / "main.go").write_text(_SOURCE)
    result = analyze_go(repo)
    return {s.name: s for s in result.symbols if s.kind in ("function", "method")}


def test_a_function_lists_each_parameter(symbols: dict) -> None:
    assert symbols["join"].meta["parameters"] == [
        parameter_entry("a", "int"),
        parameter_entry("b", "int"),
        parameter_entry("sep", "string"),
        parameter_entry("rest", "string", variadic=True),
    ]


def test_no_parameters_is_an_empty_list_not_an_absent_key(symbols: dict) -> None:
    assert symbols["none"].meta["parameters"] == []


def test_an_unnamed_parameter_keeps_its_type(symbols: dict) -> None:
    assert symbols["unnamed"].meta["parameters"] == [
        parameter_entry("", "int"), parameter_entry("", "*http.Request"),
    ]


def test_a_method_lists_parameters_without_the_receiver(symbols: dict) -> None:
    assert symbols["saver.ServeHTTP"].meta["parameters"] == [
        parameter_entry("w", "http.ResponseWriter"),
        parameter_entry("r", "*http.Request"),
    ]
    assert "abstract" not in symbols["saver.ServeHTTP"].modifiers


def test_an_interface_method_spec_lists_parameters_and_is_abstract(
    symbols: dict,
) -> None:
    spec = symbols["Handler.ServeHTTP"]
    assert spec.meta["parameters"] == symbols["saver.ServeHTTP"].meta["parameters"]
    assert "abstract" in spec.modifiers


def test_only_the_implementation_is_a_go_http_handler(symbols: dict) -> None:
    """The production path end to end: analyzer meta -> go-net-http.yaml."""
    impl, spec = symbols["saver.ServeHTTP"], symbols["Handler.ServeHTTP"]
    enrich_symbols([impl, spec], set())

    def concepts(sym: object) -> list:
        return [c.get("concept") for c in sym.meta.get("concepts", [])]

    assert "go_http_handler" in concepts(impl)
    assert "go_http_handler" not in concepts(spec)
