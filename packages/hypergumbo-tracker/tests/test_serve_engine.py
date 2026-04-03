# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve core engine integration (TrackerSet + REST API).

The serve module exposes TrackerSet operations via REST API endpoints:
GET /api/items, GET /api/items/{id}, GET /api/ready, POST /api/items,
POST /api/items/{id}/update, POST /api/items/{id}/discuss.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hypergumbo_tracker.models import Tier
from hypergumbo_tracker.store import Store
from hypergumbo_tracker.trackerset import TrackerSet


def _make_tracker(tmp_path: Path) -> TrackerSet:
    """Create a minimal TrackerSet in tmp_path for testing."""
    tracker_root = tmp_path / ".agent"
    tracker_dir = tracker_root / "tracker"
    tracker_dir.mkdir(parents=True)

    config_yaml = (
        "kinds:\n"
        "  work_item:\n"
        "    prefix: WI\n"
        "    allowed_statuses: [todo_soft, todo_hard, in_progress, done, wont_do]\n"
        "statuses:\n"
        "  - todo_soft\n"
        "  - todo_hard\n"
        "  - in_progress\n"
        "  - done\n"
        "  - wont_do\n"
        "stop_hook:\n"
        "  blocking_statuses: [todo_soft, todo_hard]\n"
        "  resolved_statuses: [done, wont_do]\n"
        "agent_usernames: [test_agent]\n"
        "lamport_branches: [dev]\n"
    )
    (tracker_dir / "config.yaml").write_text(config_yaml)

    return TrackerSet(tracker_root)


def _make_client(tmp_path: Path) -> tuple[TestClient, TrackerSet]:
    """Create a test client with a wired TrackerSet."""
    from hypergumbo_tracker.serve import create_app

    ts = _make_tracker(tmp_path)
    app = create_app(tracker_set=ts)
    return TestClient(app), ts


class TestApiListItems:
    """Tests for GET /api/items endpoint."""

    def test_list_empty(self, tmp_path: Path) -> None:
        """Returns empty list when no items exist."""
        client, _ = _make_client(tmp_path)
        resp = client.get("/api/items")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []

    def test_list_with_items(self, tmp_path: Path) -> None:
        """Returns items after adding one."""
        client, ts = _make_client(tmp_path)
        ts.add(kind="work_item", title="Test item", tier=Tier.WORKSPACE)
        resp = client.get("/api/items")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "Test item"


class TestApiGetItem:
    """Tests for GET /api/items/{id} endpoint."""

    def test_get_existing_item(self, tmp_path: Path) -> None:
        """Returns item details for a valid ID."""
        client, ts = _make_client(tmp_path)
        item_id = ts.add(kind="work_item", title="Detail item", tier=Tier.WORKSPACE)
        resp = client.get(f"/api/items/{item_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == item_id
        assert data["title"] == "Detail item"

    def test_get_nonexistent_item(self, tmp_path: Path) -> None:
        """Returns 404 for nonexistent item ID."""
        client, _ = _make_client(tmp_path)
        resp = client.get("/api/items/nonexistent-id")
        assert resp.status_code == 404


class TestApiReady:
    """Tests for GET /api/ready endpoint."""

    def test_ready_empty(self, tmp_path: Path) -> None:
        """Returns empty list when no actionable items."""
        client, _ = _make_client(tmp_path)
        resp = client.get("/api/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []

    def test_ready_with_actionable_items(self, tmp_path: Path) -> None:
        """Returns actionable items sorted by priority."""
        client, ts = _make_client(tmp_path)
        ts.add(kind="work_item", title="Ready item", tier=Tier.WORKSPACE)
        resp = client.get("/api/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1


class TestApiAddItem:
    """Tests for POST /api/items endpoint."""

    def test_add_item(self, tmp_path: Path) -> None:
        """Creates a new item via POST."""
        client, ts = _make_client(tmp_path)
        resp = client.post("/api/items", json={
            "kind": "work_item",
            "title": "New item via API",
            "tier": "workspace",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        # Verify it exists in TrackerSet
        item = ts.get(data["id"])
        assert item.title == "New item via API"

    def test_add_item_missing_fields(self, tmp_path: Path) -> None:
        """Returns 400 when required fields are missing."""
        client, _ = _make_client(tmp_path)
        resp = client.post("/api/items", json={"kind": "work_item"})
        assert resp.status_code == 400

    def test_add_item_invalid_tier(self, tmp_path: Path) -> None:
        """Returns 400 when tier is invalid."""
        client, _ = _make_client(tmp_path)
        resp = client.post("/api/items", json={
            "kind": "work_item",
            "title": "Bad tier",
            "tier": "nonexistent_tier",
        })
        assert resp.status_code == 400
        assert "Invalid tier" in resp.json()["error"]


class TestApiUpdateItem:
    """Tests for POST /api/items/{id}/update endpoint."""

    def test_update_status(self, tmp_path: Path) -> None:
        """Updates item status via POST."""
        client, ts = _make_client(tmp_path)
        item_id = ts.add(kind="work_item", title="Update me", tier=Tier.WORKSPACE)
        resp = client.post(f"/api/items/{item_id}/update", json={
            "status": "in_progress",
        })
        assert resp.status_code == 200
        item = ts.get(item_id)
        assert item.status == "in_progress"

    def test_update_nonexistent(self, tmp_path: Path) -> None:
        """Returns 404 for nonexistent item."""
        client, _ = _make_client(tmp_path)
        resp = client.post("/api/items/nonexistent/update", json={"status": "done"})
        assert resp.status_code == 404


class TestApiDiscuss:
    """Tests for POST /api/items/{id}/discuss endpoint."""

    def test_add_discussion(self, tmp_path: Path) -> None:
        """Adds a discussion message via POST."""
        client, ts = _make_client(tmp_path)
        item_id = ts.add(kind="work_item", title="Discuss me", tier=Tier.WORKSPACE)
        resp = client.post(f"/api/items/{item_id}/discuss", json={
            "message": "This is a comment",
        })
        assert resp.status_code == 200
        item = ts.get(item_id)
        assert len(item.discussion) == 1
        assert item.discussion[0].message == "This is a comment"

    def test_discuss_nonexistent(self, tmp_path: Path) -> None:
        """Returns 404 for nonexistent item."""
        client, _ = _make_client(tmp_path)
        resp = client.post("/api/items/nonexistent/discuss", json={"message": "hi"})
        assert resp.status_code == 404

    def test_discuss_missing_message(self, tmp_path: Path) -> None:
        """Returns 400 when message field is missing."""
        client, ts = _make_client(tmp_path)
        item_id = ts.add(kind="work_item", title="Test", tier=Tier.WORKSPACE)
        resp = client.post(f"/api/items/{item_id}/discuss", json={})
        assert resp.status_code == 400


class TestHealthWithTracker:
    """Health endpoint still works when tracker is wired."""

    def test_health_with_tracker(self, tmp_path: Path) -> None:
        """GET /health works when TrackerSet is configured."""
        client, _ = _make_client(tmp_path)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestAppWithoutTracker:
    """App works without TrackerSet (API endpoints return 503)."""

    def test_api_items_without_tracker(self) -> None:
        """GET /api/items returns 503 when no TrackerSet configured."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()  # No tracker_set
        client = TestClient(app)
        resp = client.get("/api/items")
        assert resp.status_code == 503

    def test_api_ready_without_tracker(self) -> None:
        """GET /api/ready returns 503 when no TrackerSet configured."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/ready")
        assert resp.status_code == 503

    def test_api_get_item_without_tracker(self) -> None:
        """GET /api/items/{id} returns 503 when no TrackerSet configured."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/items/some-id")
        assert resp.status_code == 503

    def test_api_add_item_without_tracker(self) -> None:
        """POST /api/items returns 503 when no TrackerSet configured."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/items", json={"kind": "work_item", "title": "x"})
        assert resp.status_code == 503

    def test_api_update_without_tracker(self) -> None:
        """POST /api/items/{id}/update returns 503 when no TrackerSet configured."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/items/some-id/update", json={"status": "done"})
        assert resp.status_code == 503

    def test_api_discuss_without_tracker(self) -> None:
        """POST /api/items/{id}/discuss returns 503 when no TrackerSet configured."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.post("/api/items/some-id/discuss", json={"message": "hi"})
        assert resp.status_code == 503
