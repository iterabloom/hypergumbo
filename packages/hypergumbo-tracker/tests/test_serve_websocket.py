# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve WebSocket protocol.

Message types:
- Client → Server: 'command' (invoke tracker ops like show, list, update, discuss)
- Server → Client: 'state_snapshot' (full state on connect/reconnect)
- Server → Client: 'event' (state change notification after mutation)
- Server → Client: 'error' (structured error response)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hypergumbo_tracker.models import Tier
from hypergumbo_tracker.trackerset import TrackerSet


def _make_tracker(tmp_path: Path) -> TrackerSet:
    """Create a minimal TrackerSet for testing."""
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


def _make_ws_client(tmp_path: Path) -> tuple[TestClient, TrackerSet]:
    """Create a test client with WebSocket support."""
    from hypergumbo_tracker.serve import create_app

    ts = _make_tracker(tmp_path)
    app = create_app(tracker_set=ts)
    return TestClient(app), ts


class TestWebSocketConnect:
    """Tests for WebSocket connection and state_snapshot."""

    def test_connect_receives_state_snapshot(self, tmp_path: Path) -> None:
        """Client receives state_snapshot message immediately on connect."""
        client, _ = _make_ws_client(tmp_path)
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state_snapshot"
            assert "items" in msg
            assert isinstance(msg["items"], list)

    def test_snapshot_includes_existing_items(self, tmp_path: Path) -> None:
        """state_snapshot includes items that existed before connect."""
        client, ts = _make_ws_client(tmp_path)
        ts.add(kind="work_item", title="Pre-existing", tier=Tier.WORKSPACE)

        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state_snapshot"
            assert len(msg["items"]) == 1
            assert msg["items"][0]["title"] == "Pre-existing"


class TestWebSocketCommand:
    """Tests for client→server command messages."""

    def test_command_list(self, tmp_path: Path) -> None:
        """'list' command returns items."""
        client, ts = _make_ws_client(tmp_path)
        ts.add(kind="work_item", title="Listed item", tier=Tier.WORKSPACE)

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume state_snapshot
            ws.send_json({"type": "command", "action": "list"})
            resp = ws.receive_json()
            assert resp["type"] == "result"
            assert len(resp["items"]) == 1

    def test_command_show(self, tmp_path: Path) -> None:
        """'show' command returns a single item."""
        client, ts = _make_ws_client(tmp_path)
        item_id = ts.add(kind="work_item", title="Show me", tier=Tier.WORKSPACE)

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume state_snapshot
            ws.send_json({"type": "command", "action": "show", "item_id": item_id})
            resp = ws.receive_json()
            assert resp["type"] == "result"
            assert resp["item"]["title"] == "Show me"

    def test_command_show_not_found(self, tmp_path: Path) -> None:
        """'show' command for nonexistent item returns error."""
        client, _ = _make_ws_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume state_snapshot
            ws.send_json({"type": "command", "action": "show", "item_id": "fake"})
            resp = ws.receive_json()
            assert resp["type"] == "error"

    def test_command_update_sends_event(self, tmp_path: Path) -> None:
        """'update' command mutates item and sends event confirmation."""
        client, ts = _make_ws_client(tmp_path)
        item_id = ts.add(kind="work_item", title="Update me", tier=Tier.WORKSPACE)

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume state_snapshot
            ws.send_json({
                "type": "command",
                "action": "update",
                "item_id": item_id,
                "set_fields": {"status": "in_progress"},
            })
            resp = ws.receive_json()
            assert resp["type"] == "event"
            assert resp["action"] == "updated"
            assert resp["item_id"] == item_id

        # Verify mutation persisted
        item = ts.get(item_id)
        assert item.status == "in_progress"

    def test_command_discuss_sends_event(self, tmp_path: Path) -> None:
        """'discuss' command adds message and sends event."""
        client, ts = _make_ws_client(tmp_path)
        item_id = ts.add(kind="work_item", title="Discuss me", tier=Tier.WORKSPACE)

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume state_snapshot
            ws.send_json({
                "type": "command",
                "action": "discuss",
                "item_id": item_id,
                "message": "A comment",
            })
            resp = ws.receive_json()
            assert resp["type"] == "event"
            assert resp["action"] == "discussed"

        item = ts.get(item_id)
        assert len(item.discussion) == 1

    def test_unknown_action_returns_error(self, tmp_path: Path) -> None:
        """Unknown action returns error message."""
        client, _ = _make_ws_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume state_snapshot
            ws.send_json({"type": "command", "action": "nonexistent"})
            resp = ws.receive_json()
            assert resp["type"] == "error"

    def test_malformed_message_returns_error(self, tmp_path: Path) -> None:
        """Message without 'type' field returns error."""
        client, _ = _make_ws_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume state_snapshot
            ws.send_json({"action": "list"})  # missing 'type'
            resp = ws.receive_json()
            assert resp["type"] == "error"


class TestWebSocketWithoutTracker:
    """WebSocket without TrackerSet configured."""

    def test_ws_without_tracker_sends_error(self) -> None:
        """WebSocket sends error and closes when no TrackerSet."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()  # No tracker_set
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "No tracker configured" in msg["message"]
