# SPDX-License-Identifier: MPL-2.0
"""Starlette/uvicorn server skeleton for htrac serve (ADR-0019).

Provides a lightweight HTTP server bound to 127.0.0.1 with a /health endpoint.
External access is routed through Tor onion service or native iOS/macOS app;
the server never binds to 0.0.0.0.

How It Works
------------
``create_app()`` returns a Starlette application with a ``/health`` endpoint
that reports server status and PID. The app is run via uvicorn (``run_server``).

PID file management supports ``--background``, ``--stop``, and ``--status``
flags: ``write_pid_file`` / ``read_pid_file`` / ``remove_pid_file`` handle
the lifecycle, and ``stop_server`` sends SIGTERM to a running instance.

Why This Design
---------------
- Starlette is async-native, lightweight, and supports WebSocket (needed for
  real-time state sync in future WI-fakud).
- uvicorn is the standard ASGI server for Starlette.
- PID file is simpler than systemd socket activation for the MVP.
- Binding to 127.0.0.1 only is a security requirement: Tor and the native
  app handle external routing (ADR-0019 Part A).
"""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7380  # "htrac" on a phone keypad: 4-8-7-2-2 → 7380
PID_FILENAME = "htrac-serve.pid"


async def _health(request: Request) -> JSONResponse:
    """Health check endpoint. Returns server status and PID."""
    return JSONResponse({
        "status": "ok",
        "pid": os.getpid(),
        "uptime_seconds": time.monotonic() - _start_time,
    })


_start_time: float = time.monotonic()


def create_app() -> Starlette:
    """Create the Starlette application with routes.

    Returns a configured Starlette app. Does not start the server;
    use ``run_server()`` for that.
    """
    global _start_time  # noqa: PLW0603
    _start_time = time.monotonic()

    routes = [
        Route("/health", _health, methods=["GET"]),
    ]
    return Starlette(routes=routes)


# ---------------------------------------------------------------------------
# PID file management
# ---------------------------------------------------------------------------


def default_pid_path(tracker_root: Path) -> Path:
    """Return the default PID file path inside the tracker root."""
    return tracker_root / PID_FILENAME


def write_pid_file(pid_path: Path) -> None:
    """Write the current process PID to the given file path."""
    pid_path.write_text(f"{os.getpid()}\n")


def read_pid_file(pid_path: Path) -> int | None:
    """Read a PID from the given file path.

    Returns None if the file doesn't exist or contains invalid content.
    """
    try:
        content = pid_path.read_text().strip()
        return int(content)
    except (FileNotFoundError, ValueError):
        return None


def remove_pid_file(pid_path: Path) -> None:
    """Remove the PID file if it exists."""
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive.

    Uses ``os.kill(pid, 0)`` which checks existence without sending a signal.
    """
    try:
        os.kill(pid, 0)
        return True
    except (OSError, OverflowError):
        return False


# ---------------------------------------------------------------------------
# Server status and lifecycle
# ---------------------------------------------------------------------------


def get_server_status(pid_path: Path) -> dict:
    """Get the status of the htrac serve process.

    Returns a dict with 'running' (bool), and optionally 'pid' or 'stale_pid'.
    """
    pid = read_pid_file(pid_path)
    if pid is None:
        return {"running": False}

    if is_pid_alive(pid):
        return {"running": True, "pid": pid}

    return {"running": False, "stale_pid": pid}


def stop_server(pid_path: Path) -> bool:
    """Stop a running htrac serve process via SIGTERM.

    Returns True if a process was signaled, False if no server was running.
    Cleans up stale PID files.
    """
    pid = read_pid_file(pid_path)
    if pid is None:
        return False

    if not is_pid_alive(pid):
        remove_pid_file(pid_path)
        return False

    os.kill(pid, signal.SIGTERM)

    # Wait briefly for the process to exit
    for _ in range(10):
        if not is_pid_alive(pid):
            remove_pid_file(pid_path)
            return True
        time.sleep(0.1)

    # Process didn't exit within 1 second — still return True (signal was sent)
    return True


def run_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    pid_path: Path | None = None,
) -> None:
    """Start the uvicorn server with the Starlette app.

    Args:
        host: Bind address (default: 127.0.0.1).
        port: Bind port (default: 7380).
        pid_path: If provided, write PID file on start, remove on shutdown.
    """
    import uvicorn

    app = create_app()

    if pid_path is not None:
        write_pid_file(pid_path)

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        if pid_path is not None:
            remove_pid_file(pid_path)
