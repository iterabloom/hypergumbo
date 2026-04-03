# SPDX-License-Identifier: MPL-2.0
"""Tests for the htrac serve skeleton (Starlette/uvicorn server).

The serve module provides a Starlette application with a /health endpoint,
PID file management for --background/--stop/--status, and uvicorn integration.
Bind to 127.0.0.1 only for security (external access via Tor/native app).
"""
from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import patch

import pytest


class TestCreateApp:
    """Tests for Starlette app creation and health endpoint."""

    def test_create_app_returns_starlette_app(self) -> None:
        """create_app() returns a Starlette application."""
        from hypergumbo_tracker.serve import create_app

        app = create_app()
        # Starlette apps have a .routes attribute
        assert hasattr(app, "routes")

    def test_health_endpoint(self) -> None:
        """GET /health returns 200 with status ok."""
        from starlette.testclient import TestClient

        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_endpoint_includes_pid(self) -> None:
        """Health response includes the server's PID."""
        from starlette.testclient import TestClient

        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()
        assert "pid" in data
        assert data["pid"] == os.getpid()

    def test_unknown_route_returns_404(self) -> None:
        """Unknown routes return 404."""
        from starlette.testclient import TestClient

        from hypergumbo_tracker.serve import create_app

        app = create_app()
        client = TestClient(app)
        response = client.get("/nonexistent")
        assert response.status_code == 404


class TestPidFile:
    """Tests for PID file management (--background/--stop/--status)."""

    def test_write_pid_file(self, tmp_path: Path) -> None:
        """write_pid_file creates a file with the current PID."""
        from hypergumbo_tracker.serve import write_pid_file

        pid_path = tmp_path / "htrac-serve.pid"
        write_pid_file(pid_path)
        assert pid_path.exists()
        assert pid_path.read_text().strip() == str(os.getpid())

    def test_read_pid_file(self, tmp_path: Path) -> None:
        """read_pid_file returns the PID from the file."""
        from hypergumbo_tracker.serve import read_pid_file

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text("12345\n")
        assert read_pid_file(pid_path) == 12345

    def test_read_pid_file_missing(self, tmp_path: Path) -> None:
        """read_pid_file returns None when file doesn't exist."""
        from hypergumbo_tracker.serve import read_pid_file

        pid_path = tmp_path / "htrac-serve.pid"
        assert read_pid_file(pid_path) is None

    def test_read_pid_file_invalid(self, tmp_path: Path) -> None:
        """read_pid_file returns None when file contains non-numeric content."""
        from hypergumbo_tracker.serve import read_pid_file

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text("not-a-pid\n")
        assert read_pid_file(pid_path) is None

    def test_remove_pid_file(self, tmp_path: Path) -> None:
        """remove_pid_file deletes the PID file."""
        from hypergumbo_tracker.serve import remove_pid_file

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text("12345\n")
        remove_pid_file(pid_path)
        assert not pid_path.exists()

    def test_remove_pid_file_missing(self, tmp_path: Path) -> None:
        """remove_pid_file is a no-op when file doesn't exist."""
        from hypergumbo_tracker.serve import remove_pid_file

        pid_path = tmp_path / "htrac-serve.pid"
        remove_pid_file(pid_path)  # Should not raise
        assert not pid_path.exists()

    def test_is_pid_alive_with_live_pid(self) -> None:
        """is_pid_alive returns True for the current process."""
        from hypergumbo_tracker.serve import is_pid_alive

        assert is_pid_alive(os.getpid()) is True

    def test_is_pid_alive_with_dead_pid(self) -> None:
        """is_pid_alive returns False for a non-existent PID."""
        from hypergumbo_tracker.serve import is_pid_alive

        # Use a very large PID that almost certainly doesn't exist
        assert is_pid_alive(4_000_000_000) is False


class TestServerStatus:
    """Tests for server status reporting."""

    def test_status_when_not_running(self, tmp_path: Path) -> None:
        """get_server_status reports not running when no PID file."""
        from hypergumbo_tracker.serve import get_server_status

        pid_path = tmp_path / "htrac-serve.pid"
        status = get_server_status(pid_path)
        assert status["running"] is False

    def test_status_with_stale_pid(self, tmp_path: Path) -> None:
        """get_server_status reports not running for stale PID file."""
        from hypergumbo_tracker.serve import get_server_status

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text("4000000000\n")  # Non-existent PID
        status = get_server_status(pid_path)
        assert status["running"] is False
        assert status.get("stale_pid") == 4_000_000_000

    def test_status_with_live_pid(self, tmp_path: Path) -> None:
        """get_server_status reports running for live PID."""
        from hypergumbo_tracker.serve import get_server_status

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text(f"{os.getpid()}\n")
        status = get_server_status(pid_path)
        assert status["running"] is True
        assert status["pid"] == os.getpid()


class TestStopServer:
    """Tests for server stop functionality."""

    def test_stop_no_pid_file(self, tmp_path: Path) -> None:
        """stop_server returns False when no PID file exists."""
        from hypergumbo_tracker.serve import stop_server

        pid_path = tmp_path / "htrac-serve.pid"
        assert stop_server(pid_path) is False

    def test_stop_stale_pid(self, tmp_path: Path) -> None:
        """stop_server cleans up stale PID file and returns False."""
        from hypergumbo_tracker.serve import stop_server

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text("4000000000\n")
        assert stop_server(pid_path) is False
        assert not pid_path.exists()  # Cleaned up

    def test_stop_live_pid_sends_sigterm(self, tmp_path: Path) -> None:
        """stop_server sends SIGTERM to a live process."""
        from hypergumbo_tracker.serve import stop_server

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text(f"{os.getpid()}\n")

        with patch("os.kill") as mock_kill:
            # Simulate that the process dies after SIGTERM
            mock_kill.side_effect = None
            with patch("hypergumbo_tracker.serve.is_pid_alive", side_effect=[True, False]):
                result = stop_server(pid_path)

        assert result is True
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)


class TestCmdServe:
    """Tests for the serve CLI command integration."""

    def test_serve_status_not_running(self, tmp_path: Path) -> None:
        """'serve --status' reports not running when no PID file."""
        import argparse
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve

        args = argparse.Namespace(
            command="serve",
            stop=False,
            status=True,
            background=False,
            port=None,
            json=False,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()
        exit_code = _cmd_serve(args, ts)
        assert exit_code == 0

    def test_serve_stop_no_server(self, tmp_path: Path) -> None:
        """'serve --stop' returns error when no server running."""
        import argparse
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve

        args = argparse.Namespace(
            command="serve",
            stop=True,
            status=False,
            background=False,
            port=None,
            json=False,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()
        exit_code = _cmd_serve(args, ts)
        assert exit_code == 1  # EXIT_USER_ERROR

    def test_serve_status_json_output(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """'serve --status --json' outputs JSON."""
        import argparse
        import json
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve

        args = argparse.Namespace(
            command="serve",
            stop=False,
            status=True,
            background=False,
            port=None,
            json=True,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()
        exit_code = _cmd_serve(args, ts)
        assert exit_code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["running"] is False


class TestMainServeEarlyExit:
    """Tests for serve --stop/--status early exit in main()."""

    def test_main_serve_status_not_running(self, tmp_path: Path) -> None:
        """main(['serve', '--status']) reports not running."""
        from hypergumbo_tracker.cli import main

        # Create minimal tracker structure so _find_tracker_root works
        tracker_dir = tmp_path / ".agent" / "tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / "config.yaml").write_text("tiers:\n  workspace:\n    dir: .\n")

        with pytest.raises(SystemExit) as exc_info:
            main(["--tracker-root", str(tmp_path / ".agent"), "serve", "--status"])
        assert exc_info.value.code == 0

    def test_main_serve_stop_not_running(self, tmp_path: Path) -> None:
        """main(['serve', '--stop']) exits with error when not running."""
        from hypergumbo_tracker.cli import main

        tracker_dir = tmp_path / ".agent" / "tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / "config.yaml").write_text("tiers:\n  workspace:\n    dir: .\n")

        with pytest.raises(SystemExit) as exc_info:
            main(["--tracker-root", str(tmp_path / ".agent"), "serve", "--stop"])
        assert exc_info.value.code == 1

    def test_main_serve_status_with_stale_pid(self, tmp_path: Path) -> None:
        """main(['serve', '--status']) reports stale PID."""
        from hypergumbo_tracker.cli import main
        from hypergumbo_tracker.serve import PID_FILENAME

        tracker_dir = tmp_path / ".agent" / "tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / "config.yaml").write_text("tiers:\n  workspace:\n    dir: .\n")

        # Write stale PID
        pid_path = tmp_path / ".agent" / PID_FILENAME
        pid_path.write_text("4000000000\n")

        with pytest.raises(SystemExit) as exc_info:
            main(["--tracker-root", str(tmp_path / ".agent"), "serve", "--status"])
        assert exc_info.value.code == 0

    def test_main_serve_status_with_live_pid(self, tmp_path: Path) -> None:
        """main(['serve', '--status']) reports running for live PID."""
        from hypergumbo_tracker.cli import main
        from hypergumbo_tracker.serve import PID_FILENAME

        tracker_dir = tmp_path / ".agent" / "tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / "config.yaml").write_text("tiers:\n  workspace:\n    dir: .\n")

        # Write our own PID
        pid_path = tmp_path / ".agent" / PID_FILENAME
        pid_path.write_text(f"{os.getpid()}\n")

        with pytest.raises(SystemExit) as exc_info:
            main(["--tracker-root", str(tmp_path / ".agent"), "serve", "--status"])
        assert exc_info.value.code == 0

    def test_main_serve_status_json(self, tmp_path: Path) -> None:
        """main(['serve', '--status', '--json']) outputs JSON."""
        from hypergumbo_tracker.cli import main

        tracker_dir = tmp_path / ".agent" / "tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / "config.yaml").write_text("tiers:\n  workspace:\n    dir: .\n")

        with pytest.raises(SystemExit) as exc_info:
            main(["--tracker-root", str(tmp_path / ".agent"), "--json", "serve", "--status"])
        assert exc_info.value.code == 0

    def test_main_serve_stop_success(self, tmp_path: Path) -> None:
        """main(['serve', '--stop']) succeeds when server is running."""
        from hypergumbo_tracker.cli import main
        from hypergumbo_tracker.serve import PID_FILENAME

        tracker_dir = tmp_path / ".agent" / "tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / "config.yaml").write_text("tiers:\n  workspace:\n    dir: .\n")

        pid_path = tmp_path / ".agent" / PID_FILENAME
        pid_path.write_text(f"{os.getpid()}\n")

        with patch("os.kill") as mock_kill, \
             patch("hypergumbo_tracker.serve.is_pid_alive", side_effect=[True, False]):
            mock_kill.side_effect = None
            with pytest.raises(SystemExit) as exc_info:
                main(["--tracker-root", str(tmp_path / ".agent"), "serve", "--stop"])
        assert exc_info.value.code == 0


    def test_main_serve_status_auto_discover_tracker(self, tmp_path: Path) -> None:
        """main(['serve', '--status']) auto-discovers tracker root."""
        from hypergumbo_tracker.cli import main

        with patch("hypergumbo_tracker.cli._find_tracker_root", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main(["serve", "--status"])
        assert exc_info.value.code == 0


class TestCmdServeForegound:
    """Tests for the foreground serve path in _cmd_serve."""

    def test_foreground_serve_calls_run_server(self, tmp_path: Path) -> None:
        """'serve' without --stop/--status calls run_server."""
        import argparse
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve

        args = argparse.Namespace(
            command="serve",
            stop=False,
            status=False,
            background=False,
            port=9999,
            json=False,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()

        with patch("uvicorn.run") as mock_uvicorn:
            exit_code = _cmd_serve(args, ts)

        assert exit_code == 0
        mock_uvicorn.assert_called_once()

    def test_foreground_serve_default_port(self, tmp_path: Path) -> None:
        """'serve' without --port uses DEFAULT_PORT."""
        import argparse
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve
        from hypergumbo_tracker.serve import DEFAULT_PORT

        args = argparse.Namespace(
            command="serve",
            stop=False,
            status=False,
            background=False,
            port=None,
            json=False,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()

        with patch("uvicorn.run") as mock_uvicorn:
            exit_code = _cmd_serve(args, ts)

        assert exit_code == 0
        mock_uvicorn.assert_called_once()
        # Verify default port was passed to uvicorn
        call_kwargs = mock_uvicorn.call_args[1]
        assert call_kwargs["port"] == DEFAULT_PORT

    def test_cmd_serve_stop_success(self, tmp_path: Path) -> None:
        """_cmd_serve --stop returns 0 when server is stopped."""
        import argparse
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve
        from hypergumbo_tracker.serve import PID_FILENAME

        pid_path = tmp_path / PID_FILENAME
        pid_path.write_text(f"{os.getpid()}\n")

        args = argparse.Namespace(
            command="serve",
            stop=True,
            status=False,
            background=False,
            port=None,
            json=False,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()

        with patch("os.kill") as mock_kill, \
             patch("hypergumbo_tracker.serve.is_pid_alive", side_effect=[True, False]):
            mock_kill.side_effect = None
            exit_code = _cmd_serve(args, ts)

        assert exit_code == 0

    def test_cmd_serve_status_running(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """_cmd_serve --status reports running server."""
        import argparse
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve
        from hypergumbo_tracker.serve import PID_FILENAME

        pid_path = tmp_path / PID_FILENAME
        pid_path.write_text(f"{os.getpid()}\n")

        args = argparse.Namespace(
            command="serve",
            stop=False,
            status=True,
            background=False,
            port=None,
            json=False,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()
        exit_code = _cmd_serve(args, ts)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "running" in captured.out.lower()

    def test_cmd_serve_status_stale(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """_cmd_serve --status reports stale PID."""
        import argparse
        from unittest.mock import MagicMock

        from hypergumbo_tracker.cli import _cmd_serve
        from hypergumbo_tracker.serve import PID_FILENAME

        pid_path = tmp_path / PID_FILENAME
        pid_path.write_text("4000000000\n")

        args = argparse.Namespace(
            command="serve",
            stop=False,
            status=True,
            background=False,
            port=None,
            json=False,
            tracker_root_resolved=str(tmp_path),
        )
        ts = MagicMock()
        exit_code = _cmd_serve(args, ts)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "stale" in captured.out.lower()


class TestStopServerTimeout:
    """Test the timeout path when a process doesn't exit after SIGTERM."""

    def test_stop_returns_true_even_if_process_lingers(self, tmp_path: Path) -> None:
        """stop_server returns True even if process doesn't exit within timeout."""
        from hypergumbo_tracker.serve import stop_server

        pid_path = tmp_path / "htrac-serve.pid"
        pid_path.write_text(f"{os.getpid()}\n")

        with patch("os.kill") as mock_kill, \
             patch("hypergumbo_tracker.serve.is_pid_alive", return_value=True), \
             patch("time.sleep"):  # Skip actual sleeping
            mock_kill.side_effect = None
            result = stop_server(pid_path)

        assert result is True


class TestDefaultPidPath:
    """Tests for default PID file path resolution."""

    def test_default_pid_path_uses_tracker_root(self, tmp_path: Path) -> None:
        """default_pid_path places PID file inside tracker root."""
        from hypergumbo_tracker.serve import default_pid_path

        path = default_pid_path(tmp_path)
        assert path.parent == tmp_path
        assert "htrac-serve" in path.name
        assert path.suffix == ".pid"
