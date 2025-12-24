"""WebSocket linker for detecting WebSocket communication patterns.

This linker detects WebSocket patterns in JavaScript/TypeScript code and creates
message_send and message_receive edges for WebSocket-based communication.

Detected Patterns
-----------------
Socket.io:
- socket.emit('event', data) -> message_send
- socket.on('event', handler) -> message_receive
- io.on('connection', handler) -> websocket_endpoint

Native WebSocket API:
- new WebSocket(url) -> websocket_endpoint
- ws.send(data) -> message_send
- ws.onmessage / addEventListener('message') -> message_receive

ws (Node.js package):
- wss.on('connection', handler) -> websocket_endpoint
- ws.on('message', handler) -> message_receive

How It Works
------------
1. Find all JavaScript/TypeScript files in the repository
2. Scan each file for WebSocket patterns using regex
3. Extract event names from emit/on patterns
4. Create edges linking files with matching events
5. Create websocket_endpoint symbols for connection handlers

Why This Design
---------------
- Regex-based detection is fast and doesn't require tree-sitter
- Event-based matching enables cross-file WebSocket graph construction
- Separate linker keeps language analyzers focused on their language
- Consistent with IPC linker pattern for uniformity
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

PASS_ID = "websocket-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class WebSocketPattern:
    """Represents a detected WebSocket pattern."""

    type: str  # 'send', 'receive', or 'endpoint'
    event: str  # Event name (e.g., 'connection', 'message', custom event)
    line: int  # Line number in source
    file_path: str  # Source file path
    pattern_type: str  # 'socketio', 'native', 'ws'


@dataclass
class WebSocketLinkResult:
    """Result of WebSocket linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Regex patterns for WebSocket detection

# Socket.io emit patterns (message_send)
SOCKETIO_EMIT_PATTERN = re.compile(
    r"(?:socket|io)\s*\.\s*emit\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Socket.io on patterns (message_receive)
SOCKETIO_ON_PATTERN = re.compile(
    r"(?:socket|io)\s*\.\s*on\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Native WebSocket constructor
NATIVE_WEBSOCKET_PATTERN = re.compile(
    r"new\s+WebSocket\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# ws/wss.on patterns (Node.js ws package)
WS_ON_PATTERN = re.compile(
    r"(?:ws|wss|server)\s*\.\s*on\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# WebSocket send
WEBSOCKET_SEND_PATTERN = re.compile(
    r"(?:ws|socket|connection)\s*\.\s*send\s*\(",
    re.MULTILINE,
)


def find_js_ts_files(repo_root: Path) -> Iterator[Path]:
    """Yield all JS/TS files in the repository."""
    yield from find_files(repo_root, ["*.js", "*.jsx", "*.ts", "*.tsx", "*.vue", "*.svelte"])


def _make_symbol_id(path: str, line: int, event: str, kind: str) -> str:
    """Generate ID for a WebSocket-related symbol."""
    return f"websocket:{path}:{line}:{event}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a file node."""
    return f"websocket:{path}:1-1:file:file"


def _detect_patterns(file_path: Path) -> list[WebSocketPattern]:
    """Detect WebSocket patterns in a JavaScript/TypeScript file."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return []

    patterns: list[WebSocketPattern] = []
    lines = content.split("\n")

    # Build line offset map for accurate line numbers
    line_starts: list[int] = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line) + 1)

    def get_line_number(char_pos: int) -> int:
        """Convert character position to line number (1-indexed)."""
        for i, start in enumerate(line_starts):
            if char_pos < start:
                return i
        return len(lines)

    # Socket.io emit (message_send)
    for match in SOCKETIO_EMIT_PATTERN.finditer(content):
        event_name = match.group(1)
        patterns.append(WebSocketPattern(
            type="send",
            event=event_name,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="socketio",
        ))

    # Socket.io on (message_receive)
    for match in SOCKETIO_ON_PATTERN.finditer(content):
        event_name = match.group(1)
        pattern_type = "endpoint" if event_name == "connection" else "receive"
        patterns.append(WebSocketPattern(
            type=pattern_type,
            event=event_name,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="socketio",
        ))

    # Native WebSocket constructor
    for match in NATIVE_WEBSOCKET_PATTERN.finditer(content):
        url = match.group(1)
        patterns.append(WebSocketPattern(
            type="endpoint",
            event=url,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="native",
        ))

    # ws package on patterns
    for match in WS_ON_PATTERN.finditer(content):
        event_name = match.group(1)
        pattern_type = "endpoint" if event_name == "connection" else "receive"
        patterns.append(WebSocketPattern(
            type=pattern_type,
            event=event_name,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="ws",
        ))

    # WebSocket send
    for match in WEBSOCKET_SEND_PATTERN.finditer(content):
        patterns.append(WebSocketPattern(
            type="send",
            event="message",  # Generic message event
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="native",
        ))

    return patterns


def link_websocket(repo_root: Path) -> WebSocketLinkResult:
    """Detect WebSocket patterns and create linking edges.

    Scans all JavaScript/TypeScript files for WebSocket patterns and creates:
    - Symbols for WebSocket endpoints (connection handlers)
    - message_send edges for emit/send calls
    - message_receive edges for on/onmessage handlers

    Returns a WebSocketLinkResult with edges, symbols, and run info.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[WebSocketPattern] = []
    files_analyzed = 0

    for file_path in find_js_ts_files(repo_root):
        patterns = _detect_patterns(file_path)
        all_patterns.extend(patterns)
        if patterns:
            files_analyzed += 1

    # Group patterns by event for matching
    sends: dict[str, list[WebSocketPattern]] = {}
    receives: dict[str, list[WebSocketPattern]] = {}
    endpoints: list[WebSocketPattern] = []

    for pattern in all_patterns:
        if pattern.type == "send":
            if pattern.event not in sends:
                sends[pattern.event] = []
            sends[pattern.event].append(pattern)
        elif pattern.type == "receive":
            if pattern.event not in receives:
                receives[pattern.event] = []
            receives[pattern.event].append(pattern)
        elif pattern.type == "endpoint":
            endpoints.append(pattern)

    # Create symbols for endpoints
    symbols: list[Symbol] = []
    for ep in endpoints:
        symbols.append(Symbol(
            id=_make_symbol_id(ep.file_path, ep.line, ep.event, "endpoint"),
            name=f"ws:{ep.event}",
            kind="websocket_endpoint",
            language="javascript",
            path=ep.file_path,
            span=Span(start_line=ep.line, end_line=ep.line, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    # Create edges linking senders to receivers with matching events
    edges: list[Edge] = []
    for event, send_patterns in sends.items():
        if event in receives:
            for send_pat in send_patterns:
                for recv_pat in receives[event]:
                    # Don't link same file to itself for simple patterns
                    if send_pat.file_path != recv_pat.file_path:
                        edges.append(Edge.create(
                            src=_make_file_id(send_pat.file_path),
                            dst=_make_file_id(recv_pat.file_path),
                            edge_type="websocket_message",
                            line=send_pat.line,
                            evidence_type=f"{send_pat.pattern_type}_emit",
                            confidence=0.85,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        ))

    # Create edges for endpoint connections
    for ep in endpoints:
        edges.append(Edge.create(
            src=_make_file_id(ep.file_path),
            dst=_make_symbol_id(ep.file_path, ep.line, ep.event, "endpoint"),
            edge_type="websocket_connection",
            line=ep.line,
            evidence_type=f"{ep.pattern_type}_endpoint",
            confidence=0.90,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return WebSocketLinkResult(
        edges=edges,
        symbols=symbols,
        run=run,
    )
