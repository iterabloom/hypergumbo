# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jonin: a Go ``http.Handler`` implementation is a request-ingress entrypoint.

Go's ``net/http`` reaches a handler through an INTERFACE, not a registration a
pattern can see: any type with ``ServeHTTP(http.ResponseWriter, *http.Request)``
is an ``http.Handler``, and the server calls it for every request. caddy has
31 non-test ``ServeHTTP`` methods and, before this, zero request-ingress
entrypoints, so WI-fuzik's unseeded-ingress withhold never fired there. Worse,
the one-file probe below read ``confirmed_with_caveats`` on "request data never
chooses a file to write" with NO overlay at all, over
``os.WriteFile(r.URL.Query().Get("p"), ...)``.

These tests pin the pieces: ``parameter_type`` AND'd with ``symbol_name``
(read from ``meta["parameters"]``, the declared home, never the display
signature: ADR-0058, which the first version of this rule broke), the
always-loaded ``go-net-http.yaml`` rule (method named ``ServeHTTP``, Go, a
request parameter, not an interface's bodiless spec, never in test code), and
the ``go_http_handler`` concept's mapping to ``EntrypointKind.GO_HANDLER``, a
request-ingress kind that had no producer until now. The Go analyzer's half
(it writes the parameters and marks a spec ``abstract``) is pinned in
hypergumbo-lang-mainstream's ``test_go_parameters_meta.py``.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.entrypoints import (
    REQUEST_INGRESS_KINDS,
    EntrypointKind,
    detect_entrypoints,
)
from hypergumbo_core.framework_patterns import (
    Pattern,
    _CONVENTION_PATTERN_IDS,
    clear_pattern_cache,
    enrich_symbols,
    load_framework_patterns,
    strip_test_file_only_concepts,
)
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.overload_arity import parameter_entry

_REQUEST = [parameter_entry("w", "http.ResponseWriter"), parameter_entry("r", "*http.Request")]
_CADDY = [*_REQUEST, parameter_entry("next", "caddyhttp.Handler")]


def _method(
    name: str, parameters: list | None, *, language: str = "go",
    kind: str = "method", path: str = "server/handler.go",
    modifiers: list[str] | None = None,
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-9:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(1, 9, 0, 0),
        # A display signature that SAYS it takes a request, on every symbol:
        # the rule must never read it (ADR-0058).
        signature="(w http.ResponseWriter, r *http.Request)",
        modifiers=modifiers or [],
        meta={} if parameters is None else {"parameters": parameters},
    )


# ------------------------------------------------- parameter_type AND symbol_name

_AND = Pattern(concept="c", symbol_name=r"(^|\.)ServeHTTP$", symbol_kind="^method$",
               parameter_type=r"^\*(\w+\.)?Request$")


def test_name_and_parameter_type_both_matching_is_a_match() -> None:
    match = _AND.matches(_method("H.ServeHTTP", _REQUEST))
    assert match is not None
    assert match["matched_parameter_type"] == "*http.Request"
    assert match["matched_symbol_name"] == "H.ServeHTTP"
    assert match["matched_symbol_kind"] == "method"


@pytest.mark.parametrize("symbol", [
    _method("H.ServeHTTP", [parameter_entry("ctx", "context.Context")]),
    _method("H.ServeHTTP", None),
    _method("H.Other", _REQUEST),
    _method("ServeHTTP", _REQUEST, kind="function"),
], ids=["name-without-type", "no-parameters-key", "type-without-name", "kind-mismatch"])
def test_neither_half_matches_alone(symbol: Symbol) -> None:
    assert _AND.matches(symbol) is None


def test_parameter_type_alone_is_unchanged() -> None:
    """No symbol_name declared: the type alone decides, as fastapi's rules need."""
    pattern = Pattern(concept="c", parameter_type="^Request$")
    assert pattern.matches(_method("anything", [parameter_entry("r", "Request")])) is not None
    assert pattern.matches(_method("anything", [parameter_entry("r", "int")])) is None


# ---------------------------------------------------------------- the rule


def test_go_net_http_rules_are_always_loaded() -> None:
    assert "go-net-http" in _CONVENTION_PATTERN_IDS
    clear_pattern_cache()
    fw = load_framework_patterns("go-net-http")
    assert fw is not None
    assert {p.concept for p in fw.patterns} == {"go_http_handler"}


def _concepts(symbol: Symbol) -> list[str]:
    enrich_symbols([symbol], set())
    return [c.get("concept") for c in (symbol.meta or {}).get("concepts", [])]


@pytest.mark.parametrize("name,parameters", [
    ("saver.ServeHTTP", _REQUEST),
    ("FileServer.ServeHTTP", _CADDY),
    ("proxy.ServeHTTP", [parameter_entry("rw", "nethttp.ResponseWriter"),
                         parameter_entry("req", "*nethttp.Request")]),
])
def test_a_go_servehttp_method_taking_a_request_is_a_handler(
    name: str, parameters: list,
) -> None:
    assert "go_http_handler" in _concepts(_method(name, parameters))


@pytest.mark.parametrize("symbol", [
    _method("H.ServeHTTP", [parameter_entry("ctx", "context.Context"),
                            parameter_entry("msg", "[]byte")]),
    _method("H.ServeHTTP", None),
    _method("H.ServeHTTP", [parameter_entry("r", "http.Request")]),
    _method("H.ServeHTTPS", _REQUEST),
    _method("ServeHTTP", _REQUEST, kind="function"),
    _method("H.ServeHTTP", _REQUEST, language="java", path="src/H.java"),
    _method("Handler.ServeHTTP", _REQUEST, modifiers=["abstract"]),
], ids=["no-request", "no-parameters-only-a-signature", "request-by-value",
        "other-name", "function", "java", "interface-spec"])
def test_a_lookalike_is_not_a_handler(symbol: Symbol) -> None:
    assert "go_http_handler" not in _concepts(symbol)


def test_a_test_files_fake_handler_loses_the_concept() -> None:
    """A test server's handler is not the program's ingress (WI-bosab)."""
    symbol = _method("fakeHandler.ServeHTTP", _REQUEST, path="srv/h_test.go")
    assert "go_http_handler" in _concepts(symbol)
    symbol.is_test_file = True
    strip_test_file_only_concepts([symbol])
    assert "go_http_handler" not in [
        c.get("concept") for c in symbol.meta.get("concepts", [])
    ]


# ---------------------------------------------------------------- the kind


def test_the_concept_is_a_go_handler_entrypoint() -> None:
    symbol = _method("saver.ServeHTTP", _REQUEST)
    symbol.meta = {"concepts": [
        {"concept": "go_http_handler", "framework": "go-net-http"},
    ]}
    eps = [e for e in detect_entrypoints([symbol], [])
           if e.kind == EntrypointKind.GO_HANDLER]
    assert len(eps) == 1
    assert eps[0].symbol_id == symbol.id
    assert "saver.ServeHTTP" in eps[0].label
    assert EntrypointKind.GO_HANDLER.value in REQUEST_INGRESS_KINDS


def test_a_handler_carrying_the_concept_twice_is_one_entrypoint() -> None:
    symbol = _method("saver.ServeHTTP", _REQUEST)
    symbol.meta = {"concepts": [
        {"concept": "go_http_handler"}, {"concept": "go_http_handler"},
    ]}
    eps = [e for e in detect_entrypoints([symbol], [])
           if e.kind == EntrypointKind.GO_HANDLER]
    assert len(eps) == 1
