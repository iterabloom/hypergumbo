# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical route accessor (WI-tosul / target-D)."""

from __future__ import annotations

from types import SimpleNamespace

from hypergumbo_core.routes import is_route, route_of


def _sym(meta):
    """A minimal symbol stand-in — the accessor only reads ``.meta``."""
    return SimpleNamespace(meta=meta)


# --- marker mechanism (framework_role == 'route') ---

def test_marker_with_path_and_method():
    r = route_of(_sym({"framework_role": "route", "route_path": "/users", "http_method": "GET"}))
    assert r == {"path": "/users", "method": "GET", "framework": None, "protocol": "http"}
    assert is_route(_sym({"framework_role": "route", "route_path": "/users", "http_method": "GET"}))


def test_marker_ws_sentinel_becomes_protocol():
    r = route_of(_sym({"framework_role": "route", "route_path": "/ws", "http_method": "WS"}))
    assert r == {"path": "/ws", "method": None, "framework": None, "protocol": "websocket"}


def test_marker_without_path_or_method_is_still_a_route():
    r = route_of(_sym({"framework_role": "route"}))
    assert r == {"path": None, "method": None, "framework": None, "protocol": "http"}
    assert is_route(_sym({"framework_role": "route"})) is True


# --- concept mechanism (meta.concepts[concept=='route']) ---

def test_concept_route_carries_framework():
    r = route_of(_sym({"concepts": [{"concept": "route", "path": "/x", "method": "POST", "framework": "flask"}]}))
    assert r == {"path": "/x", "method": "POST", "framework": "flask", "protocol": "http"}


def test_concept_route_ws():
    r = route_of(_sym({"concepts": [{"concept": "route", "path": "/live", "method": "WS"}]}))
    assert r == {"path": "/live", "method": None, "framework": None, "protocol": "websocket"}


def test_non_route_concept_is_not_a_route():
    assert route_of(_sym({"concepts": [{"concept": "middleware"}]})) is None
    assert is_route(_sym({"concepts": [{"concept": "middleware"}]})) is False


# --- marker-first precedence ---

def test_marker_wins_over_concept():
    # framework_role marker present AND a concept entry: the marker's own
    # path/method win (marker-first), but framework is UNIONED from the
    # co-resident concept — it is NOT dropped (WI-tosul Phase-1b-alpha, BUG-1).
    r = route_of(_sym({
        "framework_role": "route", "route_path": "/m", "http_method": "PUT",
        "concepts": [{"concept": "route", "path": "/c", "method": "GET", "framework": "django"}],
    }))
    assert r == {"path": "/m", "method": "PUT", "framework": "django", "protocol": "http"}


# --- marker-first framework UNION (WI-tosul Phase-1b-alpha; BUG-1) ---

def test_marker_dual_carry_concept_supplies_framework():
    # Rails/Phoenix/Sinatra/Laravel: the analyzer marker carries route_path/
    # http_method, and a path-less concept=route carries the framework. route_of
    # must UNION the framework onto the marker result, never drop it.
    r = route_of(_sym({
        "framework_role": "route", "route_path": "/users", "http_method": "GET",
        "concepts": [{"concept": "route", "framework": "rails"}],
    }))
    assert r == {"path": "/users", "method": "GET", "framework": "rails", "protocol": "http"}


def test_marker_legacy_framework_meta_key():
    # Starlette stamps meta['framework'] directly on the marker; honor it.
    r = route_of(_sym({
        "framework_role": "route", "route_path": "/ws", "http_method": "WS",
        "framework": "starlette",
    }))
    assert r == {"path": "/ws", "method": None, "framework": "starlette", "protocol": "websocket"}


def test_marker_route_framework_key_wins():
    # The canonical additive route_framework key takes precedence over both the
    # legacy meta['framework'] and the co-resident concept.framework.
    r = route_of(_sym({
        "framework_role": "route", "route_path": "/x", "http_method": "GET",
        "route_framework": "flask", "framework": "legacy",
        "concepts": [{"concept": "route", "framework": "django"}],
    }))
    assert r["framework"] == "flask"


def test_persisted_route_protocol_key_wins():
    # A producer-stamped route_protocol overrides the WS-derivation and strips
    # method for a websocket endpoint (additive, forward-compat with INV-tibap).
    r = route_of(_sym({
        "framework_role": "route", "route_path": "/live", "http_method": "GET",
        "route_protocol": "websocket",
    }))
    assert r == {"path": "/live", "method": None, "framework": None, "protocol": "websocket"}


# --- absence / defensiveness ---

def test_no_meta():
    assert route_of(_sym(None)) is None
    assert is_route(_sym(None)) is False


def test_empty_concepts():
    assert route_of(_sym({"concepts": None})) is None
    assert route_of(_sym({})) is None


def test_non_dict_concept_entry_skipped():
    assert route_of(_sym({"concepts": ["route", {"concept": "route", "path": "/y", "method": "GET"}]})) == {
        "path": "/y", "method": "GET", "framework": None, "protocol": "http",
    }


# --- INV-tibap: LIVE/RPC transport folded out of http_method into protocol ---

def test_marker_live_becomes_liveview_protocol():
    r = route_of(_sym({"framework_role": "route", "route_path": "/dash", "http_method": "LIVE"}))
    assert r == {"path": "/dash", "method": None, "framework": None, "protocol": "liveview"}


def test_marker_rpc_becomes_grpc_protocol():
    r = route_of(_sym({"framework_role": "route", "route_path": "/svc.M", "http_method": "RPC"}))
    assert r == {"path": "/svc.M", "method": None, "framework": None, "protocol": "grpc"}


def test_concept_live_becomes_liveview():
    # Phoenix phoenix.yaml:45 surfaces LIVE via concept.method.
    r = route_of(_sym({"concepts": [{"concept": "route", "path": "/live", "method": "LIVE", "framework": "phoenix"}]}))
    assert r == {"path": "/live", "method": None, "framework": "phoenix", "protocol": "liveview"}


def test_method_token_reconstructs_transport():
    from hypergumbo_core.routes import method_token
    assert method_token({"protocol": "websocket", "method": None}) == "WS"
    assert method_token({"protocol": "liveview", "method": None}) == "LIVE"
    assert method_token({"protocol": "grpc", "method": None}) == "RPC"
    assert method_token({"protocol": "http", "method": "GET"}) == "GET"
    assert method_token({"protocol": "http", "method": None}) is None


def test_transport_meta():
    from hypergumbo_core.routes import transport_meta
    assert transport_meta("WS") == {"http_method": None, "route_protocol": "websocket"}
    assert transport_meta("LIVE") == {"http_method": None, "route_protocol": "liveview"}
    assert transport_meta("RPC") == {"http_method": None, "route_protocol": "grpc"}
    assert transport_meta("GET") == {"http_method": "GET"}
    assert transport_meta(None) == {"http_method": None}


def test_protocol_method_token():
    from hypergumbo_core.routes import protocol_method_token
    assert protocol_method_token("websocket") == "WS"
    assert protocol_method_token("liveview") == "LIVE"
    assert protocol_method_token("grpc") == "RPC"
    assert protocol_method_token("http") is None
    assert protocol_method_token(None) is None


def test_transport_never_leaks_into_http_method():
    """INV-tibap value-set invariant: the chokepoint (transport_meta) never leaves a
    transport sentinel (WS/LIVE/RPC) in http_method, and route_of never surfaces one
    as the method. Scoped to hypergumbo's own producer chokepoint — NOT a whole-corpus
    assertion (a third-party repo may legitimately carry a developer-written
    ``method: 'ws'`` string; per the census that is a real, weird HTTP verb, not a
    transport marker)."""
    from hypergumbo_core.routes import _TRANSPORT_SENTINELS, transport_meta
    for sentinel, expected_protocol in _TRANSPORT_SENTINELS.items():
        frag = transport_meta(sentinel)
        assert frag["http_method"] is None
        assert frag["route_protocol"] == expected_protocol
        info = route_of(_sym({"framework_role": "route", "route_path": "/x", "http_method": sentinel}))
        assert info["method"] is None
        assert info["protocol"] == expected_protocol
    # A real HTTP verb is never split out.
    keep = route_of(_sym({"framework_role": "route", "route_path": "/x", "http_method": "GET"}))
    assert keep["method"] == "GET" and keep["protocol"] == "http"
