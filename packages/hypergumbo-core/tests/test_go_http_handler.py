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

These tests pin the pieces: the ``signature`` pattern filter, the
always-loaded ``go-net-http.yaml`` rule (method named ``ServeHTTP``, Go, a
request parameter, never in test code), and the ``go_http_handler`` concept's
mapping to ``EntrypointKind.GO_HANDLER``, a request-ingress kind that had no
producer until now.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.entrypoints import (
    REQUEST_INGRESS_KINDS,
    EntrypointKind,
    detect_entrypoints,
)
from hypergumbo_core.framework_patterns import (
    FrameworkPatternDef,
    Pattern,
    _CONVENTION_PATTERN_IDS,
    clear_pattern_cache,
    enrich_symbols,
    load_framework_patterns,
    strip_test_file_only_concepts,
)
from hypergumbo_core.ir import Span, Symbol

_REQUEST_SIG = "(w http.ResponseWriter, r *http.Request)"
_CADDY_SIG = "(w http.ResponseWriter, r *http.Request, next caddyhttp.Handler) error"


def _method(
    name: str, signature: str | None, *, language: str = "go",
    kind: str = "method", path: str = "server/handler.go",
) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-9:{name}:{kind}",
        name=name,
        kind=kind,
        language=language,
        path=path,
        span=Span(1, 9, 0, 0),
        signature=signature,
        meta={},
    )


# ---------------------------------------------------------------- signature


def test_signature_filter_searches_the_symbol_signature() -> None:
    pattern = Pattern(
        concept="c", symbol_name=r"(^|\.)ServeHTTP$", signature=r"\*(\w+\.)?Request\b",
    )
    assert pattern.matches(_method("H.ServeHTTP", _REQUEST_SIG)) is not None
    assert pattern.matches(_method("H.ServeHTTP", "(ctx context.Context)")) is None


def test_signature_filter_rejects_a_symbol_with_no_signature() -> None:
    pattern = Pattern(concept="c", symbol_name="ServeHTTP", signature="Request")
    assert pattern.matches(_method("H.ServeHTTP", None)) is None


def test_signature_is_a_key_the_yaml_loader_reads() -> None:
    fw = FrameworkPatternDef.from_dict({
        "id": "x", "language": "go",
        "patterns": [{"concept": "c", "symbol_name": "S", "signature": "Request"}],
    })
    assert fw.patterns[0].signature == "Request"


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


@pytest.mark.parametrize("name,signature", [
    ("saver.ServeHTTP", _REQUEST_SIG),
    ("FileServer.ServeHTTP", _CADDY_SIG),
    ("proxy.ServeHTTP", "(rw nethttp.ResponseWriter, req *nethttp.Request)"),
])
def test_a_go_servehttp_method_taking_a_request_is_a_handler(
    name: str, signature: str,
) -> None:
    assert "go_http_handler" in _concepts(_method(name, signature))


@pytest.mark.parametrize("symbol", [
    _method("H.ServeHTTP", "(ctx context.Context, msg []byte) error"),
    _method("H.ServeHTTP", None),
    _method("H.ServeHTTPS", _REQUEST_SIG),
    _method("ServeHTTP", _REQUEST_SIG, kind="function"),
    _method("H.ServeHTTP", _REQUEST_SIG, language="java", path="src/H.java"),
], ids=["no-request", "no-signature", "other-name", "function", "java"])
def test_a_lookalike_is_not_a_handler(symbol: Symbol) -> None:
    assert "go_http_handler" not in _concepts(symbol)


def test_a_test_files_fake_handler_loses_the_concept() -> None:
    """A test server's handler is not the program's ingress (WI-bosab)."""
    symbol = _method("fakeHandler.ServeHTTP", _REQUEST_SIG, path="srv/h_test.go")
    assert "go_http_handler" in _concepts(symbol)
    symbol.is_test_file = True
    strip_test_file_only_concepts([symbol])
    assert "go_http_handler" not in [
        c.get("concept") for c in symbol.meta.get("concepts", [])
    ]


# ---------------------------------------------------------------- the kind


def test_the_concept_is_a_go_handler_entrypoint() -> None:
    symbol = _method("saver.ServeHTTP", _REQUEST_SIG)
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
    symbol = _method("saver.ServeHTTP", _REQUEST_SIG)
    symbol.meta = {"concepts": [
        {"concept": "go_http_handler"}, {"concept": "go_http_handler"},
    ]}
    eps = [e for e in detect_entrypoints([symbol], [])
           if e.kind == EntrypointKind.GO_HANDLER]
    assert len(eps) == 1
