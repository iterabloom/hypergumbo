# SPDX-License-Identifier: MPL-2.0
"""htrac serve denies every route but /health by default (WI-hopip).

The write API (POST /api/items, .../update, .../discuss) and /ws had no auth
check, so the change that wires a TrackerSet into ``run_server`` (ADR-0019
Part A) would have published an unauthenticated write API. ``create_app`` now
takes an ``authenticate`` callable and, without one, rejects every request
except ``/health`` -- so the safe state is the default and forgetting auth
fails closed. The tests enumerate the app's own routes, so a route added
later is covered without editing this file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hypergumbo_tracker.serve import create_app

from .test_serve_engine import _make_tracker


def _concrete(path: str) -> str:
    return path.replace("{item_id}", "WI-x")


def _http_routes(app) -> list[tuple[str, str]]:
    return [
        (method, _concrete(r.path))
        for r in app.routes if isinstance(r, Route)
        for method in sorted(r.methods or ()) if method != "HEAD"
    ]


def _assert_every_route_denies(app) -> None:
    client = TestClient(app)
    routes = _http_routes(app)
    assert ("POST", "/api/items") in routes
    for method, path in routes:
        resp = client.request(method, path, json={"kind": "work_item", "title": "x", "message": "m"})
        if path == "/health":
            assert resp.status_code == 200
        else:
            assert resp.status_code == 401, (method, path, resp.status_code)
    ws_paths = [r.path for r in app.routes if isinstance(r, WebSocketRoute)]
    assert ws_paths == ["/ws"]
    for path in ws_paths:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(path) as ws:
                ws.receive_json()
        assert exc.value.code == 1008


def test_without_an_authenticator_every_route_but_health_is_denied(tmp_path: Path) -> None:
    ts = _make_tracker(tmp_path)
    _assert_every_route_denies(create_app(tracker_set=ts))
    assert ts.list_items() == []  # the unauthenticated POST reached nothing


def test_an_authenticator_that_says_no_or_raises_denies(tmp_path: Path) -> None:
    def boom(conn) -> bool:
        raise RuntimeError("store unavailable")

    for authenticate in (lambda conn: False, boom):
        ts = _make_tracker(tmp_path / str(id(authenticate)))
        _assert_every_route_denies(create_app(tracker_set=ts, authenticate=authenticate))


def test_an_accepted_request_reaches_the_tracker(tmp_path: Path) -> None:
    ts = _make_tracker(tmp_path)
    seen: list[str] = []

    def by_header(conn) -> bool:
        seen.append(conn.url.path)
        return conn.headers.get("authorization") == "Bearer ok"

    client = TestClient(create_app(tracker_set=ts, authenticate=by_header))
    assert client.post("/api/items", json={"kind": "work_item", "title": "x"}).status_code == 401
    resp = client.post(
        "/api/items", json={"kind": "work_item", "title": "x"},
        headers={"Authorization": "Bearer ok"},
    )
    assert resp.status_code == 201
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer ok"}) as ws:
        assert ws.receive_json()["type"] == "state_snapshot"
    assert seen == ["/api/items", "/api/items", "/ws"]


def test_the_static_frontend_is_public(tmp_path: Path) -> None:
    """The built frontend holds no tracker data and carries the login page."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>login</html>")
    app = create_app(static_dir=static)
    assert any(isinstance(r, Mount) for r in app.routes)
    assert TestClient(app).get("/").status_code == 200
