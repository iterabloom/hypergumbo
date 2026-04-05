# SPDX-License-Identifier: MPL-2.0
"""Starlette/uvicorn server for htrac serve (ADR-0019).

Provides an HTTP server bound to 127.0.0.1 with a ``/health`` endpoint and
REST API routes for TrackerSet operations (list, get, ready, add, update,
discuss). External access is routed through Tor onion service or native
iOS/macOS app; the server never binds to 0.0.0.0.

How It Works
------------
``create_app(tracker_set=ts)`` returns a Starlette application wired to
the given TrackerSet. API routes delegate to TrackerSet methods and serialize
results via ``_item_to_dict``. If no TrackerSet is provided, API routes
return HTTP 503.

PID file management supports ``--background``, ``--stop``, and ``--status``
flags: ``write_pid_file`` / ``read_pid_file`` / ``remove_pid_file`` handle
the lifecycle, and ``stop_server`` sends SIGTERM to a running instance.

Why This Design
---------------
- Same core engine (TrackerSet) as CLI and TUI — no separate state.
- Starlette is async-native, lightweight, and supports WebSocket (needed for
  real-time state sync in future WI-fakud).
- uvicorn is the standard ASGI server for Starlette.
- PID file is simpler than systemd socket activation for the MVP.
- Binding to 127.0.0.1 only is a security requirement: Tor and the native
  app handle external routing (ADR-0019 Part A).
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

if TYPE_CHECKING:
    from hypergumbo_tracker.trackerset import TrackerSet

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7380  # "htrac" on a phone keypad: 4-8-7-2-2 → 7380
PID_FILENAME = "htrac-serve.pid"

# Module-level state set by create_app()
_start_time: float = time.monotonic()
_tracker_set: TrackerSet | None = None
_ws_clients: set[WebSocket] = set()
_watcher_task: asyncio.Task | None = None


def _item_to_dict(item: Any) -> dict[str, Any]:
    """Convert CompiledItem to a JSON-serializable dict."""
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "status": item.status,
        "priority": item.priority,
        "parent": item.parent,
        "tags": item.tags,
        "before": item.before,
        "pr_ref": item.pr_ref,
        "description": item.description,
        "fields": item.fields,
        "locked_fields": sorted(item.locked_fields),
        "discussion": [
            {"by": d.by, "actor": d.actor, "at": d.at,
             "message": d.message, "is_summary": d.is_summary}
            for d in item.discussion
        ],
        "frozen": item.frozen,
        "tier": item.tier.value if item.tier else None,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _require_tracker(request: Request) -> JSONResponse | None:
    """Return a 503 response if no TrackerSet is configured, else None."""
    if _tracker_set is None:
        return JSONResponse(
            {"error": "No tracker configured"}, status_code=503,
        )
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def _health(request: Request) -> JSONResponse:
    """Health check endpoint. Returns server status and PID."""
    return JSONResponse({
        "status": "ok",
        "pid": os.getpid(),
        "uptime_seconds": time.monotonic() - _start_time,
    })


async def _api_list_items(request: Request) -> JSONResponse:
    """GET /api/items — list all items."""
    err = _require_tracker(request)
    if err:
        return err
    items = _tracker_set.list_items()  # type: ignore[union-attr]
    return JSONResponse({"items": [_item_to_dict(i) for i in items]})


async def _api_get_item(request: Request) -> JSONResponse:
    """GET /api/items/{id} — get a single item by ID."""
    err = _require_tracker(request)
    if err:
        return err
    item_id = request.path_params["item_id"]
    try:
        item = _tracker_set.get(item_id)  # type: ignore[union-attr]
    except Exception:
        return JSONResponse({"error": "Item not found"}, status_code=404)
    return JSONResponse(_item_to_dict(item))


async def _api_ready(request: Request) -> JSONResponse:
    """GET /api/ready — list actionable items sorted by priority."""
    err = _require_tracker(request)
    if err:
        return err
    items = _tracker_set.ready()  # type: ignore[union-attr]
    return JSONResponse({"items": [_item_to_dict(i) for i in items]})


async def _api_add_item(request: Request) -> JSONResponse:
    """POST /api/items — create a new item."""
    err = _require_tracker(request)
    if err:
        return err
    body = await request.json()
    kind = body.get("kind")
    title = body.get("title")
    if not kind or not title:
        return JSONResponse(
            {"error": "Missing required fields: kind, title"}, status_code=400,
        )
    from hypergumbo_tracker.models import Tier
    tier_str = body.get("tier", "workspace")
    try:
        tier = Tier(tier_str)
    except ValueError:
        return JSONResponse(
            {"error": f"Invalid tier: {tier_str}"}, status_code=400,
        )
    item_id = _tracker_set.add(  # type: ignore[union-attr]
        kind=kind, title=title, tier=tier,
        status=body.get("status"),
        priority=body.get("priority"),
        description=body.get("description", ""),
        tags=body.get("tags"),
    )
    return JSONResponse({"id": item_id}, status_code=201)


async def _api_update_item(request: Request) -> JSONResponse:
    """POST /api/items/{id}/update — update item fields."""
    err = _require_tracker(request)
    if err:
        return err
    item_id = request.path_params["item_id"]
    body = await request.json()
    try:
        _tracker_set.get(item_id)  # type: ignore[union-attr] — verify exists
    except Exception:
        return JSONResponse({"error": "Item not found"}, status_code=404)
    set_fields: dict[str, Any] = {}
    for key in ("status", "priority", "title", "description", "tags", "pr_ref"):
        if key in body:
            set_fields[key] = body[key]
    _tracker_set.update(item_id, set_fields=set_fields)  # type: ignore[union-attr]
    return JSONResponse({"ok": True})


async def _api_discuss_item(request: Request) -> JSONResponse:
    """POST /api/items/{id}/discuss — add a discussion message."""
    err = _require_tracker(request)
    if err:
        return err
    item_id = request.path_params["item_id"]
    body = await request.json()
    message = body.get("message")
    if not message:
        return JSONResponse(
            {"error": "Missing required field: message"}, status_code=400,
        )
    try:
        _tracker_set.get(item_id)  # type: ignore[union-attr] — verify exists
    except Exception:
        return JSONResponse({"error": "Item not found"}, status_code=404)
    _tracker_set.discuss(item_id, message)  # type: ignore[union-attr]
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# WebSocket protocol
# ---------------------------------------------------------------------------


async def _ws_handler(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time tracker state sync.

    Protocol:
    - On connect: sends ``state_snapshot`` with all current items.
    - Client sends ``command`` messages to invoke tracker operations.
    - Server replies with ``result`` (for reads), ``event`` (for mutations),
      or ``error`` (on failure).
    """
    await websocket.accept()

    if _tracker_set is None:
        await websocket.send_json({"type": "error", "message": "No tracker configured"})
        await websocket.close()
        return

    # Send initial state snapshot
    items = _tracker_set.list_items()
    await websocket.send_json({
        "type": "state_snapshot",
        "items": [_item_to_dict(i) for i in items],
    })

    # Register client for broadcast
    _ws_clients.add(websocket)

    # Message loop
    try:
        while True:
            msg = await websocket.receive_json()
            response = _handle_ws_message(msg)
            await websocket.send_json(response)
    except Exception:  # WebSocket disconnect or JSON decode error
        pass
    finally:
        _ws_clients.discard(websocket)


def _handle_ws_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a WebSocket command message and return a response dict."""
    if not isinstance(msg, dict) or "type" not in msg:
        return {"type": "error", "message": "Missing 'type' field"}

    if msg["type"] != "command":
        return {"type": "error", "message": f"Unknown message type: {msg['type']}"}

    action = msg.get("action", "")
    try:
        if action == "list":
            items = _tracker_set.list_items()  # type: ignore[union-attr]
            return {"type": "result", "items": [_item_to_dict(i) for i in items]}

        if action == "show":
            item = _tracker_set.get(msg["item_id"])  # type: ignore[union-attr]
            return {"type": "result", "item": _item_to_dict(item)}

        if action == "ready":
            items = _tracker_set.ready()  # type: ignore[union-attr]
            return {"type": "result", "items": [_item_to_dict(i) for i in items]}

        if action == "update":
            item_id = msg["item_id"]
            set_fields = msg.get("set_fields", {})
            _tracker_set.update(item_id, set_fields=set_fields)  # type: ignore[union-attr]
            return {"type": "event", "action": "updated", "item_id": item_id}

        if action == "discuss":
            item_id = msg["item_id"]
            message = msg["message"]
            _tracker_set.discuss(item_id, message)  # type: ignore[union-attr]
            return {"type": "event", "action": "discussed", "item_id": item_id}

        return {"type": "error", "message": f"Unknown action: {action}"}

    except KeyError as e:
        return {"type": "error", "message": f"Missing field: {e}"}
    except Exception as e:
        return {"type": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Filesystem watcher for CLI/TUI coexistence
# ---------------------------------------------------------------------------


async def broadcast_state_snapshot() -> None:
    """Push a fresh state_snapshot to all connected WebSocket clients.

    Called when the filesystem watcher detects ops file changes (CLI/TUI wrote
    new ops) or after a WebSocket mutation command.
    """
    if _tracker_set is None or not _ws_clients:
        return
    items = _tracker_set.list_items()
    snapshot = {
        "type": "state_snapshot",
        "items": [_item_to_dict(i) for i in items],
    }
    stale: list[WebSocket] = []
    for ws in _ws_clients.copy():
        try:
            await ws.send_json(snapshot)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _ws_clients.discard(ws)


def get_watch_paths() -> list[Path]:
    """Return the ops directories to watch for filesystem changes.

    These are the directories where CLI/TUI write .ops files. When any file
    in these directories changes, the watcher triggers a state recompile and
    WebSocket broadcast.
    """
    if _tracker_set is None:
        return []
    paths = []
    for store in [_tracker_set.canonical, _tracker_set.workspace, _tracker_set.stealth]:
        ops_dir = store._ops_dir
        if ops_dir.exists():
            paths.append(ops_dir)
    return paths


async def _watch_ops_files(stop_event: asyncio.Event) -> None:
    """Background coroutine that watches ops directories for changes.

    Uses ``watchfiles.awatch`` for async, Rust-backed filesystem notifications.
    On any change, broadcasts a fresh state_snapshot to all WebSocket clients.
    """
    import watchfiles

    paths = get_watch_paths()
    if not paths:
        return

    try:
        async for _changes in watchfiles.awatch(  # pragma: no cover — async filesystem events
            *paths, stop_event=stop_event,
        ):
            await broadcast_state_snapshot()  # pragma: no cover
    except Exception:  # pragma: no cover — defensive for watcher errors
        pass


async def start_watcher() -> None:
    """Start the filesystem watcher background task."""
    global _watcher_task
    if _watcher_task is not None:
        return
    _stop_event = asyncio.Event()
    _watcher_task = asyncio.create_task(_watch_ops_files(_stop_event))
    # Store stop event on the task for cleanup
    _watcher_task._stop_event = _stop_event  # type: ignore[attr-defined]


async def stop_watcher() -> None:
    """Stop the filesystem watcher background task."""
    global _watcher_task
    if _watcher_task is None:
        return
    stop_event = getattr(_watcher_task, "_stop_event", None)
    if stop_event is not None:
        stop_event.set()
    _watcher_task.cancel()
    try:
        await _watcher_task
    except (asyncio.CancelledError, Exception):
        pass
    _watcher_task = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    tracker_set: TrackerSet | None = None,
    static_dir: Path | None = None,
) -> Starlette:
    """Create the Starlette application with routes.

    Args:
        tracker_set: Optional TrackerSet to wire into the API routes.
            If None, API routes return 503 but /health still works.
        static_dir: Optional path to a directory of static files (BlockSuite
            frontend build output). Mounted at ``/`` as a fallback after all
            API/WebSocket routes. If None, no static files are served.

    Returns a configured Starlette app. Does not start the server;
    use ``run_server()`` for that.
    """
    global _start_time, _tracker_set
    _start_time = time.monotonic()
    _tracker_set = tracker_set

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: Starlette):  # type: ignore[type-arg]
        """Manage watcher lifecycle: start on startup, stop on shutdown."""
        if tracker_set is not None:
            await start_watcher()
        yield
        if tracker_set is not None:
            await stop_watcher()

    routes = [
        Route("/health", _health, methods=["GET"]),
        Route("/api/items", _api_list_items, methods=["GET"]),
        Route("/api/items", _api_add_item, methods=["POST"]),
        Route("/api/items/{item_id}", _api_get_item, methods=["GET"]),
        Route("/api/items/{item_id}/update", _api_update_item, methods=["POST"]),
        Route("/api/items/{item_id}/discuss", _api_discuss_item, methods=["POST"]),
        Route("/api/ready", _api_ready, methods=["GET"]),
        WebSocketRoute("/ws", _ws_handler),
    ]
    if static_dir is not None and static_dir.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=str(static_dir), html=True)))
    return Starlette(routes=routes, lifespan=lifespan)


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
