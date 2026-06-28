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
        kind="function",
        language="go",
        path=path,
        span=Span(1, 5, 0, 10),
        stable_id=f"sha256:route-{name}",
        meta={"http_method": http_method, "route_path": route_path, "framework_role": "route"},
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
        id=sym_id, name="h", kind="function", language="go", path="p.go",
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
        id=sym_id, name="mystery", kind="function", language="go", path="p.go",
        span=Span(1, 5, 0, 10),
        stable_id="sha256:mystery",
        meta={"framework_role": "route"},
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


def test_run_behavior_map_writes_slices_to_stem_slices_subdir(tmp_path: Path) -> None:
    """WI-rimos / UX-A: ``run_behavior_map`` must place handler-slice fan-out
    inside ``<out-stem>.slices/`` next to the --out target, NOT spread the
    slice files alongside the main behavior-map JSON.

    Pre-fix, the caller passed ``out_path.parent`` to ``_emit_handler_slices``,
    so a ``--out /foo/bar/result.json`` invocation deposited 20-30
    ``slice.handler.*.json`` files directly in ``/foo/bar/``. The bakeoff
    campaign that surfaced this used ``--out /tmp/round-NN.json``, so the
    fan-out ended up in ``/tmp/`` and successive rounds clobbered each
    other's slices. Co-locating the fan-out under ``<stem>.slices/`` keeps
    the user's --out directory tidy and gives each invocation its own
    namespace.
    """
    from hypergumbo_core.cli import run_behavior_map

    # Repo root with a Flask-style route handler so the slice-emission
    # path actually fires (the cmd_routes detector matches concept=route
    # symbols, which the Python analyzer materialises from
    # ``@app.route(...)`` decorators).
    (tmp_path / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route('/users/<int:user_id>', methods=['GET'])\n"
        "def get_user(user_id):\n"
        "    return {'id': user_id}\n"
        "\n"
        "@app.route('/users', methods=['POST'])\n"
        "def create_user():\n"
        "    return {'ok': True}\n"
    )

    out_dir = tmp_path / "artifacts"
    out_path = out_dir / "result.json"

    run_behavior_map(
        repo_root=tmp_path,
        out_path=out_path,
        include_sketch_precomputed=False,
        progress=False,
    )

    # Main result lands at the requested path.
    assert out_path.exists(), out_path

    # The slice fan-out subdirectory is derived from the --out file's stem.
    slices_dir = out_dir / "result.slices"
    assert slices_dir.is_dir(), (
        f"Expected slice fan-out at {slices_dir}; out_dir contents: "
        f"{sorted(p.name for p in out_dir.iterdir())}"
    )

    # The companion index always exists, even when no handlers were
    # detected; here we expect at least one detected route.
    index_path = slices_dir / "slice.handler.index.json"
    assert index_path.is_file(), index_path
    index = json.loads(index_path.read_text())
    assert index["view"] == "handler_slice_index"

    # Crucially: NO slice.handler.* file leaks into the parent --out
    # directory. The only entries there are the main result, any tier
    # / sketch / supply-chain artifacts, and the slices subdirectory
    # itself.
    leaked = sorted(
        p.name for p in out_dir.iterdir()
        if p.is_file() and p.name.startswith("slice.handler.")
    )
    assert leaked == [], (
        f"Slice fan-out leaked into {out_dir} instead of {slices_dir}: "
        f"{leaked}"
    )


def test_default_max_handler_slices_is_25() -> None:
    """Documented contract: default cap is 25 handlers."""
    assert _DEFAULT_MAX_HANDLER_SLICES == 25


# ---------------------------------------------------------------------------
# WI-bujim: features[] index population (option (c) — index-only, no inline content)
# ---------------------------------------------------------------------------


def test_emit_handler_slices_populates_behavior_map_features(tmp_path: Path) -> None:
    """WI-bujim: each emitted handler slice contributes one entry to
    behavior_map['features'] in the spec-compliant shape (id/name/entry_nodes/
    node_ids/edge_ids/query/limits_hit). The full denormalized slice content
    (inline nodes/edges/meta) stays in the per-slice files."""
    h1 = _concept_handler("get_user", "src/api.py", "GET", "/users/{id}")
    h2 = _concept_handler("create_user", "src/api.py", "POST", "/users")
    symbols = [h1, h2]
    bmap = _behavior_map(symbols, [])
    bmap["features"] = []  # producer hardcodes this; mirror schema.py:101

    _emit_handler_slices(bmap, symbols, [], tmp_path, tmp_path, enabled=True)

    features = bmap["features"]
    assert len(features) == 2, features
    for entry in features:
        # Spec-compliant shape per docs/hypergumbo-spec.md §features[]
        assert entry["id"].startswith("sha256:"), entry
        assert isinstance(entry["name"], str)
        assert isinstance(entry["entry_nodes"], list)
        assert isinstance(entry["node_ids"], list)
        assert isinstance(entry["edge_ids"], list)
        assert isinstance(entry["query"], dict)
        assert "limits_hit" in entry
        # WI-bujim (c): index-only. The denormalized inline content lives
        # in slice.handler.<...>.json files, NOT in behavior_map['features'].
        assert "nodes" not in entry, entry
        assert "edges" not in entry, entry
        assert "meta" not in entry, entry


def test_emit_handler_slices_feature_id_matches_spec_formula(tmp_path: Path) -> None:
    """WI-bujim: id is sha256(json.dumps(query, sort_keys=True)) per spec
    line 802. Same query → same id (enables diff across commits)."""
    import hashlib
    import json as _json

    h = _concept_handler("get_user", "src/api.py", "GET", "/users")
    bmap = _behavior_map([h], [])
    bmap["features"] = []

    _emit_handler_slices(bmap, [h], [], tmp_path, tmp_path, enabled=True)

    entry = bmap["features"][0]
    expected = (
        "sha256:"
        + hashlib.sha256(
            _json.dumps(entry["query"], sort_keys=True).encode()
        ).hexdigest()
    )
    assert entry["id"] == expected


def test_emit_handler_slices_disabled_does_not_touch_features(tmp_path: Path) -> None:
    """WI-bujim: when --no-handler-slices is in effect, features[] is
    left as-is (empty, in the default producer state)."""
    h = _concept_handler("get_user", "src/api.py", "GET", "/users")
    bmap = _behavior_map([h], [])
    bmap["features"] = []

    _emit_handler_slices(bmap, [h], [], tmp_path, tmp_path, enabled=False)

    assert bmap["features"] == []


def test_emit_handler_slices_overflow_handlers_not_in_features(tmp_path: Path) -> None:
    """WI-bujim: handlers over the cap do not emit slice files and do not
    appear in features[]. They still appear in the index file with
    emitted=False so consumers can re-derive on demand."""
    handlers = [
        _concept_handler(f"handler_{i}", "src/api.py", "GET", f"/r{i}")
        for i in range(5)
    ]
    bmap = _behavior_map(handlers, [])
    bmap["features"] = []

    _emit_handler_slices(
        bmap, handlers, [], tmp_path, tmp_path,
        max_handler_slices=2, enabled=True,
    )

    # Only the 2 emitted handlers contribute features[] entries.
    assert len(bmap["features"]) == 2

    # The remaining 3 still show in the index with emitted=False.
    index = json.loads((tmp_path / "slice.handler.index.json").read_text())
    not_emitted = [h for h in index["handlers"] if not h.get("emitted", False)]
    assert len(not_emitted) == 3


# ---------------------------------------------------------------------------
# INV-nubub: route-marker / function-handler filename collision
# ---------------------------------------------------------------------------


def test_route_marker_does_not_overwrite_function_slice(tmp_path: Path) -> None:
    """INV-nubub: the framework route marker (framework_role='route', ~0
    edges) and the concept-enriched function handler map to the same
    (method, path) filename. The richer function slice must win on disk, not
    be silently overwritten by the degenerate marker.
    """
    func = _concept_handler("_health", "src/serve.py", "GET", "/health")
    marker = _kind_route_handler("GET_health", "src/serve.py", "GET", "/health")
    callee = _non_route_symbol("_compute", "src/serve.py")
    edge = Edge(
        id="e1", src=func.id, dst=callee.id, edge_type="calls", line=2,
        confidence=0.9, origin="test", origin_run_id="test",
    )
    # Order [func, marker] so the buggy "last writer wins" would leave the
    # degenerate marker slice on disk.
    symbols = [func, marker, callee]
    bmap = _behavior_map(symbols, [edge])
    bmap["features"] = []

    written = _emit_handler_slices(bmap, symbols, [edge], tmp_path, tmp_path)

    # Exactly one handler slice file for GET /health (the index is separate).
    slice_files = [p for p in written if p.name == "slice.handler.GET.health.json"]
    assert len(slice_files) == 1

    data = json.loads(slice_files[0].read_text())
    node_ids = data["feature"]["node_ids"]
    # The rich function slice (function + its callee), NOT the 1-node marker.
    assert len(node_ids) >= 2
    assert func.id in node_ids

    # Index consistency: one emitted entry for this file, keyed to the
    # function symbol, whose node_count matches the on-disk file (no orphaned
    # second entry pointing at an overwritten file).
    index = json.loads((tmp_path / "slice.handler.index.json").read_text())
    health = [
        h for h in index["handlers"]
        if h.get("file") == "slice.handler.GET.health.json"
    ]
    assert len(health) == 1
    assert health[0]["id"] == func.id
    assert health[0]["node_count"] == len(node_ids)
    # The marker's route metadata is merged onto the emitted entry.
    assert {"method": "GET", "path": "/health"} in health[0]["routes"]


def test_marker_only_route_still_emits(tmp_path: Path) -> None:
    """A route with ONLY a framework marker (no concept-function sibling, e.g.
    the Go kind='route' case) still emits its slice -- the dedup must not drop
    a marker when it is the sole candidate for its filename.
    """
    marker = _kind_route_handler("getAlerts", "api/v2.go", "GET", "/alerts")
    bmap = _behavior_map([marker], [])
    bmap["features"] = []

    written = _emit_handler_slices(bmap, [marker], [], tmp_path, tmp_path)

    slice_files = [p for p in written if p.name == "slice.handler.GET.alerts.json"]
    assert len(slice_files) == 1
    index = json.loads((tmp_path / "slice.handler.index.json").read_text())
    emitted = [h for h in index["handlers"] if h.get("emitted")]
    assert len(emitted) == 1
    assert emitted[0]["id"] == marker.id


def test_handler_index_has_no_duplicate_files(tmp_path: Path) -> None:
    """No two emitted index entries may share a `file` (the collision
    invariant): every emitted slice file is written exactly once.
    """
    func = _concept_handler("_health", "src/serve.py", "GET", "/health")
    marker = _kind_route_handler("GET_health", "src/serve.py", "GET", "/health")
    symbols = [func, marker]
    bmap = _behavior_map(symbols, [])
    bmap["features"] = []

    _emit_handler_slices(bmap, symbols, [], tmp_path, tmp_path)

    index = json.loads((tmp_path / "slice.handler.index.json").read_text())
    files = [h["file"] for h in index["handlers"] if h.get("emitted")]
    assert len(files) == len(set(files))


def test_marker_first_multiroute_function_swaps_and_merges(tmp_path: Path) -> None:
    """When the route marker is seen BEFORE the function handler, the chosen
    symbol upgrades to the function; and a function registered under a second
    route contributes that route to the merged list (INV-nubub collapse).
    """
    marker = _kind_route_handler("GET_health", "src/serve.py", "GET", "/health")
    f_get = _concept_handler("health", "src/serve.py", "GET", "/health")
    # Same id as f_get, a second route registration (GET + HEAD /health).
    f_head = Symbol(
        id=f_get.id, name=f_get.name, kind="function", language="python",
        path="src/serve.py", span=Span(1, 5, 0, 10), stable_id=f_get.stable_id,
        meta={"concepts": [{"concept": "route", "method": "HEAD", "path": "/health"}]},
    )
    # Marker first so the swap-to-function branch is exercised.
    symbols = [marker, f_get, f_head]
    bmap = _behavior_map(symbols, [])
    bmap["features"] = []

    _emit_handler_slices(bmap, symbols, [], tmp_path, tmp_path)

    index = json.loads((tmp_path / "slice.handler.index.json").read_text())
    health = [
        h for h in index["handlers"]
        if h.get("file") == "slice.handler.GET.health.json"
    ]
    assert len(health) == 1
    # Upgraded from the marker to the function handler.
    assert health[0]["id"] == f_get.id
    # Both the GET and the merged HEAD registration are present.
    methods = {r["method"] for r in health[0]["routes"]}
    assert methods == {"GET", "HEAD"}
