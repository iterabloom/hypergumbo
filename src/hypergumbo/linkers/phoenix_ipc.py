"""Phoenix Channels IPC linker for detecting Elixir IPC patterns.

This linker detects Phoenix Channel patterns in Elixir code and creates
message_send and message_receive edges for cross-process communication.

Detected Patterns
-----------------
Phoenix Channels:
- broadcast!(socket, "event", payload) -> message_send
- broadcast(socket, "event", payload) -> message_send
- Endpoint.broadcast!("topic", "event", payload) -> message_send
- push(socket, "event", payload) -> message_send
- handle_in("event", payload, socket) -> message_receive

Phoenix LiveView:
- handle_event("event", params, socket) -> message_receive
- push_event(socket, "event", payload) -> message_send

How It Works
------------
1. Find all Elixir files (*.ex, *.exs) in the repository
2. Scan each file for Phoenix patterns using regex
3. Extract event names from send/receive patterns
4. Create edges linking files with matching events

Why This Design
---------------
- Regex-based detection is fast and doesn't require tree-sitter
- Event-based matching enables cross-file IPC graph construction
- Separate linker keeps language analyzers focused on their language
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import LinkerContext, LinkerResult, register_linker

PASS_ID = "phoenix-ipc-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class PhoenixPattern:
    """Represents a detected Phoenix Channel pattern."""

    type: str  # 'send' or 'receive'
    event: str  # Event name
    topic: str  # Channel topic (may be empty for socket-based broadcast)
    line: int  # Line number in source
    file_path: str  # Source file path
    pattern_type: str  # 'broadcast', 'push', 'handle_in', 'handle_event', 'push_event'


@dataclass
class PhoenixLinkResult:
    """Result of Phoenix IPC linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Regex patterns for Phoenix Channel detection
# broadcast!/broadcast with explicit topic: Endpoint.broadcast!("topic", "event", payload)
ENDPOINT_BROADCAST_PATTERN = re.compile(
    r"\.broadcast!?\s*\(\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"",
    re.MULTILINE,
)

# broadcast!/broadcast with socket: broadcast!(socket, "event", payload)
SOCKET_BROADCAST_PATTERN = re.compile(
    r"broadcast!?\s*\(\s*\w+\s*,\s*\"([^\"]+)\"",
    re.MULTILINE,
)

# push(socket, "event", payload)
PUSH_PATTERN = re.compile(
    r"\bpush\s*\(\s*\w+\s*,\s*\"([^\"]+)\"",
    re.MULTILINE,
)

# push_event(socket, "event", payload) - LiveView
PUSH_EVENT_PATTERN = re.compile(
    r"\bpush_event\s*\(\s*\w+\s*,\s*\"([^\"]+)\"",
    re.MULTILINE,
)

# handle_in("event", payload, socket) - Channel handler
HANDLE_IN_PATTERN = re.compile(
    r"\bdef\s+handle_in\s*\(\s*\"([^\"]+)\"",
    re.MULTILINE,
)

# handle_event("event", params, socket) - LiveView handler
HANDLE_EVENT_PATTERN = re.compile(
    r"\bdef\s+handle_event\s*\(\s*\"([^\"]+)\"",
    re.MULTILINE,
)


def detect_phoenix_patterns(source: bytes, language: str) -> list[dict]:
    """Detect Phoenix Channel patterns in source code.

    Args:
        source: Source code bytes
        language: Programming language ('elixir')

    Returns:
        List of detected patterns with type, event, topic, and line info.
    """
    # Only process Elixir
    if language != "elixir":
        return []

    patterns: list[dict] = []
    text = source.decode("utf-8", errors="replace")

    # Detect Endpoint.broadcast!("topic", "event", ...)
    for match in ENDPOINT_BROADCAST_PATTERN.finditer(text):
        topic = match.group(1)
        event = match.group(2)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "send",
            "event": event,
            "topic": topic,
            "line": line,
            "pattern_type": "broadcast",
        })

    # Detect broadcast!(socket, "event", ...)
    # We need to avoid double-matching endpoint broadcasts
    for match in SOCKET_BROADCAST_PATTERN.finditer(text):
        # Check if this is part of an endpoint broadcast (preceded by a dot)
        start = match.start()
        if start > 0 and text[start - 1] == ".":
            continue  # Skip, this is Endpoint.broadcast!, not socket broadcast

        event = match.group(1)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "send",
            "event": event,
            "topic": "",  # Topic comes from socket, unknown statically
            "line": line,
            "pattern_type": "broadcast",
        })

    # Detect push(socket, "event", ...)
    for match in PUSH_PATTERN.finditer(text):
        event = match.group(1)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "send",
            "event": event,
            "topic": "",
            "line": line,
            "pattern_type": "push",
        })

    # Detect push_event(socket, "event", ...) - LiveView
    for match in PUSH_EVENT_PATTERN.finditer(text):
        event = match.group(1)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "send",
            "event": event,
            "topic": "",
            "line": line,
            "pattern_type": "push_event",
        })

    # Detect handle_in("event", ...)
    for match in HANDLE_IN_PATTERN.finditer(text):
        event = match.group(1)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "receive",
            "event": event,
            "topic": "",
            "line": line,
            "pattern_type": "handle_in",
        })

    # Detect handle_event("event", ...) - LiveView
    for match in HANDLE_EVENT_PATTERN.finditer(text):
        event = match.group(1)
        line = text[:match.start()].count("\n") + 1
        patterns.append({
            "type": "receive",
            "event": event,
            "topic": "",
            "line": line,
            "pattern_type": "handle_event",
        })

    return patterns


def _find_elixir_files(repo_root: Path) -> Iterator[Path]:
    """Find all Elixir files in the repository."""
    yield from find_files(repo_root, ["*.ex", "*.exs"])


def link_phoenix_ipc(repo_root: Path) -> PhoenixLinkResult:
    """Link Phoenix Channel patterns across files in a repository.

    Scans Elixir files for Phoenix Channel patterns and creates
    message_send and message_receive edges for matching events.

    Args:
        repo_root: Root directory of the repository

    Returns:
        PhoenixLinkResult with edges and run metadata.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[PhoenixPattern] = []
    files_analyzed = 0
    files_skipped = 0

    # Scan all Elixir files for Phoenix patterns
    for file_path in _find_elixir_files(repo_root):
        try:
            source = file_path.read_bytes()
            patterns = detect_phoenix_patterns(source, "elixir")

            for p in patterns:
                all_patterns.append(PhoenixPattern(
                    type=p["type"],
                    event=p["event"],
                    topic=p.get("topic", ""),
                    line=p["line"],
                    file_path=str(file_path),
                    pattern_type=p["pattern_type"],
                ))

            files_analyzed += 1
        except (OSError, IOError):
            files_skipped += 1

    # Group patterns by event
    send_by_event: dict[str, list[PhoenixPattern]] = {}
    receive_by_event: dict[str, list[PhoenixPattern]] = {}

    for p in all_patterns:
        if p.type == "send":
            if p.event not in send_by_event:
                send_by_event[p.event] = []
            send_by_event[p.event].append(p)
        else:
            if p.event not in receive_by_event:
                receive_by_event[p.event] = []
            receive_by_event[p.event].append(p)

    # Create symbols and edges for matching events
    edges: list[Edge] = []
    symbols: list[Symbol] = []
    created_symbol_ids: set[str] = set()

    def _make_symbol_id(pattern: PhoenixPattern, event: str) -> str:
        return f"phoenix:{pattern.file_path}:{pattern.line}:{pattern.type}:{event}"

    def _ensure_symbol(pattern: PhoenixPattern, event: str) -> str:
        """Create symbol for Phoenix endpoint if not already created."""
        sym_id = _make_symbol_id(pattern, event)
        if sym_id not in created_symbol_ids:
            symbols.append(Symbol(
                id=sym_id,
                name=f"phoenix:{pattern.type}:{event}",
                kind=f"ipc_{pattern.type}",
                language="elixir",
                path=pattern.file_path,
                span=Span(
                    start_line=pattern.line,
                    end_line=pattern.line,
                    start_col=0,
                    end_col=0,
                ),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                meta={
                    "event": event,
                    "pattern_type": pattern.pattern_type,
                    "topic": pattern.topic,
                },
            ))
            created_symbol_ids.add(sym_id)
        return sym_id

    for event, senders in send_by_event.items():
        receivers = receive_by_event.get(event, [])
        for sender in senders:
            src_id = _ensure_symbol(sender, event)
            for receiver in receivers:
                dst_id = _ensure_symbol(receiver, event)
                # Create edge from sender to receiver
                edge = Edge.create(
                    src=src_id,
                    dst=dst_id,
                    edge_type="message_send",
                    line=sender.line,
                    confidence=0.85,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="phoenix_event_match",
                )
                edge.meta = {"event": event, "topic": sender.topic}
                edges.append(edge)

    # Also create edges for the receive side
    for event, receivers in receive_by_event.items():
        senders = send_by_event.get(event, [])
        for receiver in receivers:
            src_id = _ensure_symbol(receiver, event)
            for sender in senders:
                dst_id = _ensure_symbol(sender, event)
                edge = Edge.create(
                    src=src_id,
                    dst=dst_id,
                    edge_type="message_receive",
                    line=receiver.line,
                    confidence=0.85,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="phoenix_event_match",
                )
                edge.meta = {"event": event}
                edges.append(edge)

    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return PhoenixLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


@register_linker(
    "phoenix_ipc",
    priority=40,  # Run with other IPC linkers
    description="Phoenix Channels IPC linking (Elixir channels, LiveView)",
)
def phoenix_ipc_linker(ctx: LinkerContext) -> LinkerResult:
    """Phoenix IPC linker for registry-based dispatch.

    This wraps link_phoenix_ipc() to use the LinkerContext/LinkerResult interface.
    """
    result = link_phoenix_ipc(ctx.repo_root)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
