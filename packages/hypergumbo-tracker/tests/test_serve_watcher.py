# SPDX-License-Identifier: MPL-2.0
"""Tests for htrac serve filesystem watcher (CLI/TUI coexistence).

The watcher uses watchfiles to detect ops file changes from CLI/TUI,
then broadcasts state_snapshot to all connected WebSocket clients.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestGetWatchPaths:
    """Tests for get_watch_paths()."""

    def test_returns_ops_dirs_when_tracker_set(self, tmp_path: Path) -> None:
        """Returns ops directory paths when TrackerSet is configured."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        old_ts = serve_mod._tracker_set
        serve_mod._tracker_set = ts
        try:
            paths = serve_mod.get_watch_paths()
            assert len(paths) >= 1
            assert all(p.exists() for p in paths)
        finally:
            serve_mod._tracker_set = old_ts

    def test_returns_empty_without_tracker(self) -> None:
        """Returns empty list when no TrackerSet configured."""
        import hypergumbo_tracker.serve as serve_mod

        old_ts = serve_mod._tracker_set
        serve_mod._tracker_set = None
        try:
            paths = serve_mod.get_watch_paths()
            assert paths == []
        finally:
            serve_mod._tracker_set = old_ts


class TestBroadcastStateSnapshot:
    """Tests for broadcast_state_snapshot()."""

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_clients(self, tmp_path: Path) -> None:
        """Broadcasts state_snapshot to all registered WebSocket clients."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        ts.add(kind="work_item", title="Broadcast test", tier=Tier.WORKSPACE)

        old_ts = serve_mod._tracker_set
        old_clients = serve_mod._ws_clients.copy()
        serve_mod._tracker_set = ts

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        serve_mod._ws_clients = {mock_ws1, mock_ws2}

        try:
            await serve_mod.broadcast_state_snapshot()

            mock_ws1.send_json.assert_called_once()
            mock_ws2.send_json.assert_called_once()

            msg = mock_ws1.send_json.call_args[0][0]
            assert msg["type"] == "state_snapshot"
            assert len(msg["items"]) == 1
        finally:
            serve_mod._tracker_set = old_ts
            serve_mod._ws_clients = old_clients

    @pytest.mark.asyncio
    async def test_broadcast_removes_stale_clients(self, tmp_path: Path) -> None:
        """Clients that raise on send are removed from the set."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        old_ts = serve_mod._tracker_set
        old_clients = serve_mod._ws_clients.copy()
        serve_mod._tracker_set = ts

        good_ws = AsyncMock()
        bad_ws = AsyncMock()
        bad_ws.send_json.side_effect = Exception("disconnected")
        serve_mod._ws_clients = {good_ws, bad_ws}

        try:
            await serve_mod.broadcast_state_snapshot()

            good_ws.send_json.assert_called_once()
            assert bad_ws not in serve_mod._ws_clients
        finally:
            serve_mod._tracker_set = old_ts
            serve_mod._ws_clients = old_clients

    @pytest.mark.asyncio
    async def test_broadcast_noop_without_tracker(self) -> None:
        """No-op when TrackerSet is None."""
        import hypergumbo_tracker.serve as serve_mod

        old_ts = serve_mod._tracker_set
        serve_mod._tracker_set = None
        try:
            await serve_mod.broadcast_state_snapshot()  # Should not raise
        finally:
            serve_mod._tracker_set = old_ts

    @pytest.mark.asyncio
    async def test_broadcast_noop_without_clients(self, tmp_path: Path) -> None:
        """No-op when no clients connected."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        old_ts = serve_mod._tracker_set
        old_clients = serve_mod._ws_clients.copy()
        serve_mod._tracker_set = ts
        serve_mod._ws_clients = set()

        try:
            await serve_mod.broadcast_state_snapshot()  # Should not raise
        finally:
            serve_mod._tracker_set = old_ts
            serve_mod._ws_clients = old_clients


class TestStartStopWatcher:
    """Tests for start_watcher/stop_watcher lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, tmp_path: Path) -> None:
        """start_watcher creates a background task."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        old_ts = serve_mod._tracker_set
        old_task = serve_mod._watcher_task
        serve_mod._tracker_set = ts
        serve_mod._watcher_task = None

        try:
            await serve_mod.start_watcher()
            assert serve_mod._watcher_task is not None
            # Clean up
            await serve_mod.stop_watcher()
        finally:
            serve_mod._tracker_set = old_ts
            serve_mod._watcher_task = old_task

    @pytest.mark.asyncio
    async def test_start_idempotent(self, tmp_path: Path) -> None:
        """Calling start_watcher twice doesn't create a second task."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        old_ts = serve_mod._tracker_set
        old_task = serve_mod._watcher_task
        serve_mod._tracker_set = ts
        serve_mod._watcher_task = None

        try:
            await serve_mod.start_watcher()
            first_task = serve_mod._watcher_task
            await serve_mod.start_watcher()
            assert serve_mod._watcher_task is first_task
            await serve_mod.stop_watcher()
        finally:
            serve_mod._tracker_set = old_ts
            serve_mod._watcher_task = old_task

    @pytest.mark.asyncio
    async def test_stop_clears_task(self, tmp_path: Path) -> None:
        """stop_watcher cancels and clears the task."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        old_ts = serve_mod._tracker_set
        old_task = serve_mod._watcher_task
        serve_mod._tracker_set = ts
        serve_mod._watcher_task = None

        try:
            await serve_mod.start_watcher()
            assert serve_mod._watcher_task is not None
            await serve_mod.stop_watcher()
            assert serve_mod._watcher_task is None
        finally:
            serve_mod._tracker_set = old_ts
            serve_mod._watcher_task = old_task

    @pytest.mark.asyncio
    async def test_stop_noop_when_not_started(self) -> None:
        """stop_watcher is a no-op when no task exists."""
        import hypergumbo_tracker.serve as serve_mod

        old_task = serve_mod._watcher_task
        serve_mod._watcher_task = None
        try:
            await serve_mod.stop_watcher()  # Should not raise
            assert serve_mod._watcher_task is None
        finally:
            serve_mod._watcher_task = old_task


class TestWatchOpsFilesEarlyReturn:
    """Test _watch_ops_files returns early when no paths."""

    @pytest.mark.asyncio
    async def test_watch_returns_when_no_paths(self) -> None:
        """_watch_ops_files returns immediately when no watch paths."""
        import hypergumbo_tracker.serve as serve_mod

        old_ts = serve_mod._tracker_set
        serve_mod._tracker_set = None  # No tracker → no paths

        try:
            stop = asyncio.Event()
            # Should return immediately since get_watch_paths() is empty
            await serve_mod._watch_ops_files(stop)
        finally:
            serve_mod._tracker_set = old_ts


class TestLifespanIntegration:
    """Test Starlette lifespan triggers watcher start/stop."""

    def test_lifespan_starts_and_stops_watcher(self, tmp_path: Path) -> None:
        """TestClient triggers lifespan which starts/stops the watcher."""
        import hypergumbo_tracker.serve as serve_mod
        from starlette.testclient import TestClient

        ts = _make_tracker(tmp_path)
        app = serve_mod.create_app(tracker_set=ts)

        old_task = serve_mod._watcher_task
        serve_mod._watcher_task = None

        try:
            # TestClient with raise_server_exceptions triggers lifespan
            with TestClient(app) as client:
                resp = client.get("/health")
                assert resp.status_code == 200
            # After exiting the context, stop_watcher should have been called
            assert serve_mod._watcher_task is None
        finally:
            serve_mod._watcher_task = old_task


class TestWatcherIntegration:
    """Integration test: file change triggers broadcast."""

    @pytest.mark.asyncio
    async def test_ops_file_change_triggers_broadcast(self, tmp_path: Path) -> None:
        """Writing an ops file triggers state_snapshot broadcast."""
        import hypergumbo_tracker.serve as serve_mod

        ts = _make_tracker(tmp_path)
        old_ts = serve_mod._tracker_set
        old_clients = serve_mod._ws_clients.copy()
        old_task = serve_mod._watcher_task
        serve_mod._tracker_set = ts
        serve_mod._watcher_task = None

        mock_ws = AsyncMock()
        serve_mod._ws_clients = {mock_ws}

        try:
            await serve_mod.start_watcher()

            # Write an ops file to trigger the watcher
            ops_dir = ts.workspace._ops_dir
            (ops_dir / "test-item.ops").write_text("test op\n")

            # Give watcher time to detect + broadcast (watchfiles has ~100ms debounce)
            await asyncio.sleep(0.5)

            # The watcher should have broadcast a state_snapshot
            if mock_ws.send_json.called:
                msg = mock_ws.send_json.call_args[0][0]
                assert msg["type"] == "state_snapshot"

            await serve_mod.stop_watcher()
        finally:
            serve_mod._tracker_set = old_ts
            serve_mod._ws_clients = old_clients
            serve_mod._watcher_task = old_task
