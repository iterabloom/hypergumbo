# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for per-handler forward slices emitted by `run` (WI-sihok).

Covers the _emit_handler_slices helper: route-symbol detection, dedupe by
handler_id, test-file exclusion, filename convention (method+path-sanitized
plus fallback), cap behavior, the slice.handler.index.json companion file,
schema stamping, and the enabled/disabled switch.

Tests call _emit_handler_slices directly (not via subprocess) so coverage
picks up every branch.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.cli import (
    _DEFAULT_MAX_HANDLER_SLICES,
    _emit_handler_slices,
    _extract_route_info,
    _handler_slice_filename,
    _is_route_symbol,
)


def _concept_handler(name: str, path: str, http_method: str, route_path: str) -> Symbol:
    """Build a handler symbol whose meta.concepts flags it as a route."""
    sym_id = f"python:{path}:1-5:{name}:function"
    return Symbol(
        id=sym_id,
        name=name,
        kind="function",
        language="python",
        path=path,
        span=Span(1, 5, 0, 10),
        stable_id=f"sha256:{name}",
        meta={"concepts": [{"concept": "route", "method": http_method, "path": route_path}]},
    )


def _kind_route_handler(name: str, path: str, http_method: str, route_path: str) -> Symbol:
    """Build a kind='route' symbol (analyzer-materialized route)."""
    sym_id = f"go:{path}:1-5:{name}:route"
    return Symbol(
        id=sym_id,
        name=name,
        kind="route",
        language="go",
        path=path,
        span=Span(1, 5, 0, 10),
        stable_id=f"sha256:route-{name}",
        meta={"http_method": http_method, "route_path": route_path},
    )


def _non_route_symbol(name: str, path: str) -> Symbol:
    sym_id = f"python:{path}:1-5:{name}:function"
    return Symbol(
        id=sym_id,
        name=name,
        kind="function",
        language="python",
        path=path,
        span=Span(1, 5, 0, 10),
        stable_id=f"sha256:{name}",
    )


def _behavior_map(symbols: list[Symbol], edges: list[Edge]) -> dict:
    return {
        "schema_version": "0.1.0",
        "nodes": [s.to_dict() for s in symbols],
        "edges": [e.to_dict() for e in edges],
    }


# ---------------------------------------------------------------------------
# Route-symbol detection
# ---------------------------------------------------------------------------


def test_is_route_symbol_detects_kind_route() -> None:
    sym = _kind_route_handler("getAlertsHandler", "api/v2.go", "GET", "/alerts")
    assert _is_route_symbol(sym) is True


def test_is_route_symbol_detects_concept_route() -> None:
    sym = _concept_handler("get_user", "src/api.py", "GET", "/users/{id}")
    assert _is_route_symbol(sym) is True


def test_is_route_symbol_rejects_non_route() -> None:
    sym = _non_route_symbol("helper", "src/utils.py")
    assert _is_route_symbol(sym) is False


def test_is_route_symbol_rejects_non_route_concept() -> None:
    """Symbols tagged with non-route concepts (e.g. model) are not handlers."""
    sym_id = "python:src/models.py:1-5:User:class"
    sym = Symbol(
        id=sym_id, name="User", kind="class", language="python", path="src/models.py",
        span=Span(1, 5, 0, 10),
        stable_id="sha256:User",
        meta={"concepts": [{"concept": "model"}]},
    )
    assert _is_route_symbol(sym) is False


# ---------------------------------------------------------------------------
# Route-info extraction
# ---------------------------------------------------------------------------


def test_extract_route_info_from_kind_route() -> None:
    sym = _kind_route_handler("h", "p.go", "POST", "/submit")
    assert _extract_route_info(sym) == {"method": "POST", "path": "/submit"}


def test_extract_route_info_from_concept() -> None:
    sym = _concept_handler("h", "p.py", "DELETE", "/x")
    assert _extract_route_info(sym) == {"method": "DELETE", "path": "/x"}


def test_extract_route_info_missing_returns_none() -> None:
    sym = _non_route_symbol("h", "p.py")
    assert _extract_route_info(sym) is None


def test_extract_route_info_incomplete_kind_route_falls_back_to_concept() -> None:
    """kind='route' without method/path falls through to concept scan."""
    sym_id = "go:p.go:1-5:h:route"
    sym = Symbol(
        id=sym_id, name="h", kind="route", language="go", path="p.go",
        span=Span(1, 5, 0, 10),
        stable_id="sha256:h",
        meta={"concepts": [{"concept": "route", "method": "GET", "path": "/y"}]},
    )
    assert _extract_route_info(sym) == {"method": "GET", "path": "/y"}


# ---------------------------------------------------------------------------
# Filename convention
# ---------------------------------------------------------------------------


def test_handler_slice_filename_method_and_path() -> None:
    sym = _concept_handler("h", "p.py", "GET", "/api/v2/alerts")
    route_info = {"method": "GET", "path": "/api/v2/alerts"}
    assert _handler_slice_filename(sym, route_info) == "slice.handler.GET.api_v2_alerts.json"


def test_handler_slice_filename_fallback_without_route_info() -> None:
    sym = _non_route_symbol("myHandler", "p.py")
    assert _handler_slice_filename(sym, None) == "slice.handler.myHandler.json"


def test_handler_slice_filename_sanitizes_curly_braces() -> None:
    sym = _concept_handler("h", "p.py", "GET", "/users/{id}/posts")
    route_info = {"method": "GET", "path": "/users/{id}/posts"}
    # Curly braces and slashes → underscores via _sanitize_filename_part
    name = _handler_slice_filename(sym, route_info)
    assert name.startswith("slice.handler.GET.")
    assert name.endswith(".json")
    # No raw slashes or braces leak through
    middle = name[len("slice.handler.GET."):-len(".json")]
    assert "/" not in middle
    assert "{" not in middle
    assert "}" not in middle


# ---------------------------------------------------------------------------
# _emit_handler_slices end-to-end
# ---------------------------------------------------------------------------


def test_emit_handler_slices_happy_path(tmp_path: Path) -> None:
    h1 = _concept_handler("get_user", "src/api.py", "GET", "/users/{id}")
    h2 = _concept_handler("create_user", "src/api.py", "POST", "/users")
    helper = _non_route_symbol("helper", "src/utils.py")
    symbols = [h1, h2, helper]
    edges: list[Edge] = []
    bmap = _behavior_map(symbols, edges)

    written = _emit_handler_slices(
        bmap, symbols, edges, tmp_path, tmp_path, enabled=True,
    )

    filenames = {p.name for p in written}
    assert "slice.handler.GET.users_id.json" in filenames
    assert "slice.handler.POST.users.json" in filenames
    assert "slice.handler.index.json" in filenames

    # Each emitted file is a valid slice JSON with schema stamps
    get_path = tmp_path / "slice.handler.GET.users_id.json"
    payload = json.loads(get_path.read_text())
    assert payload["view"] == "slice"
    assert payload["feature"]["meta"]["entry_kind"] == "handler"
    assert payload["feature"]["meta"]["routes"] == [
        {"method": "GET", "path": "/users/{id}"}
    ]
    assert payload["feature"]["meta"]["slice_params"]["exclude_tests"] is True


def test_emit_handler_slices_excludes_test_files(tmp_path: Path) -> None:
    h_prod = _concept_handler("get_user", "src/api.py", "GET", "/users")
    h_test = _concept_handler("get_user_test_handler", "tests/test_api.py", "GET", "/test")
    symbols = [h_prod, h_test]
    bmap = _behavior_map(symbols, [])

    written = _emit_handler_slices(bmap, symbols, [], tmp_path, tmp_path, enabled=True)
    filenames = {p.name for p in written}
    assert "slice.handler.GET.users.json" in filenames
    # The test-file handler must not get emitted
    assert not any("test_api" in n for n in filenames)
    # And must not appear in the index
    index = json.loads((tmp_path / "slice.handler.index.json").read_text())
    assert all("test_api" not in (h.get("path") or "") for h in index["handlers"])


def test_emit_handler_slices_dedupes_by_id(tmp_path: Path) -> None:
    """Same handler_id registered under two routes → one file, routes list merged."""
    h_get = _concept_handler("dual", "src/api.py", "GET", "/dual")
    h_post = Symbol(
        id=h_get.id,  # same id
        name=h_get.name, kind="function", language="python", path="src/api.py",
        span=Span(1, 5, 0, 10),
        stable_id=h_get.stable_id,
        meta={"concepts": [{"concept": "route", "method": "POST", "path": "/dual"}]},
    )
    bmap = _behavior_map([h_get, h_post], [])
    written = _emit_handler_slices(bmap, [h_get, h_post], [], tmp_path, tmp_path)
    # Only one handler file (plus the index)
    handler_files = [p for p in written if p.name.startswith("slice.handler.") and p.name != "slice.handler.index.json"]
    assert len(handler_files) == 1
    payload = json.loads(handler_files[0].read_text())
    routes = payload["feature"]["meta"]["routes"]
    methods = {r["method"] for r in routes}
    assert methods == {"GET", "POST"}


def test_emit_handler_slices_cap_writes_overflow_in_index(tmp_path: Path) -> None:
    handlers = [
        _concept_handler(f"h_{i}", f"src/h{i}.py", "GET", f"/p{i}")
        for i in range(4)
    ]
    bmap = _behavior_map(handlers, [])
    written = _emit_handler_slices(
        bmap, handlers, [], tmp_path, tmp_path, max_handler_slices=2,
    )
    handler_files = [
        p for p in written
        if p.name.startswith("slice.handler.") and p.name != "slice.handler.index.json"
    ]
    assert len(handler_files) == 2
    index = json.loads((tmp_path / "slice.handler.index.json").read_text())
    assert index["max_handler_slices"] == 2
    emitted = [h for h in index["handlers"] if h.get("emitted")]
    dropped = [h for h in index["handlers"] if not h.get("emitted")]
    assert len(emitted) == 2
    assert len(dropped) == 2
    # Dropped entries explain how to recover
    for h in dropped:
        assert "slice --entry" in (h.get("reason") or "")


def test_emit_handler_slices_fallback_filename_without_route(tmp_path: Path) -> None:
    """kind='route' symbol with no method/path still gets emitted with fallback name."""
    sym_id = "go:p.go:1-5:mystery:route"
    sym = Symbol(
        id=sym_id, name="mystery", kind="route", language="go", path="p.go",
        span=Span(1, 5, 0, 10),
        stable_id="sha256:mystery",
        meta={},
    )
    bmap = _behavior_map([sym], [])
    written = _emit_handler_slices(bmap, [sym], [], Path("p.go"), tmp_path)
    assert (tmp_path / "slice.handler.mystery.json").exists()


def test_emit_handler_slices_disabled_writes_nothing(tmp_path: Path) -> None:
    h = _concept_handler("h", "src/api.py", "GET", "/x")
    bmap = _behavior_map([h], [])
    written = _emit_handler_slices(bmap, [h], [], tmp_path, tmp_path, enabled=False)
    assert written == []
    assert not (tmp_path / "slice.handler.index.json").exists()


def test_emit_handler_slices_no_handlers_still_writes_empty_index(tmp_path: Path) -> None:
    non_route = _non_route_symbol("helper", "src/utils.py")
    bmap = _behavior_map([non_route], [])
    written = _emit_handler_slices(bmap, [non_route], [], tmp_path, tmp_path)
    # Only the index file, and it has an empty handlers list
    assert [p.name for p in written] == ["slice.handler.index.json"]
    idx = json.loads((tmp_path / "slice.handler.index.json").read_text())
    assert idx["handlers"] == []


def test_default_max_handler_slices_is_25() -> None:
    """Documented contract: default cap is 25 handlers."""
    assert _DEFAULT_MAX_HANDLER_SLICES == 25
