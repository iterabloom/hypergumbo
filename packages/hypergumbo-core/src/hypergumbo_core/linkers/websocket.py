# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protocol linker: WebSocket for detecting WebSocket communication patterns.

This linker detects WebSocket patterns in JavaScript/TypeScript and Python code
and creates message_send and message_receive edges for WebSocket-based communication.

Detected Patterns
-----------------
Socket.io (JavaScript):
- socket.emit('event', data) -> message_send
- socket.emit(eventVar, data) -> message_send (variable event)
- socket.on('event', handler) -> message_receive
- socket.on(eventVar, handler) -> message_receive (variable event)
- io.on('connection', handler) -> websocket_endpoint

Native WebSocket API (JavaScript):
- new WebSocket(url) -> websocket_endpoint
- ws.send(data) -> message_send
- ws.onmessage / addEventListener('message') -> message_receive

ws (Node.js package):
- wss.on('connection', handler) -> websocket_endpoint
- ws.on('message', handler) -> message_receive

Django Channels (Python):
- @app.websocket_route('/path') -> websocket_endpoint
- channel_layer.send('channel', message) -> message_send
- channel_layer.send(channel_var, message) -> message_send (variable channel)
- channel_layer.group_send('group', message) -> message_send
- async for message in websocket.receive() -> message_receive
- await self.send(message) -> message_send

FastAPI WebSocket (Python):
- @app.websocket('/path') -> websocket_endpoint
- websocket.receive_json() / receive_text() -> message_receive
- websocket.send_json() / send_text() -> message_send
- websocket.accept() -> websocket_endpoint

Event Detection Strategy
------------------------
Patterns can use either string literals or variables for event names:
- Literal: socket.emit('user-login', data) -> exact event 'user-login'
- Variable: socket.emit(EVENT_NAME, data) -> variable name 'EVENT_NAME'

For variable-based events, we use heuristic matching:
- If emitter uses `LOGIN_EVENT` and listener uses `LOGIN_EVENT`, link them
- Confidence is lower for variable-based matches (0.65 vs 0.85)

How It Works
------------
1. Find all JavaScript/TypeScript/Python files in the repository
2. Scan each file for WebSocket patterns using regex
3. Extract event names (literals) or variable names from patterns
4. Create edges linking files with matching events/variables
5. Create websocket_endpoint symbols for connection handlers

Why This Design
---------------
- Regex-based detection is fast and doesn't require tree-sitter
- Event-based matching enables cross-file WebSocket graph construction
- Variable detection catches patterns missed by literal-only matching
- Separate linker keeps language analyzers focused on their language
- Consistent with IPC linker pattern for uniformity
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..analyze.base import make_file_id, make_file_stable_id
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import LinkerContext, LinkerResult, register_linker
from ._text_filters import language_from_path, read_masked_source

PASS_ID = make_pass_id("websocket-linker")


@dataclass
class WebSocketPattern:
    """Represents a detected WebSocket pattern."""

    type: str  # 'send', 'receive', or 'endpoint'
    event: str  # Event name (literal value or variable name)
    line: int  # Line number in source
    file_path: str  # Source file path
    pattern_type: str  # 'socketio', 'native', 'ws', 'fastapi', 'django_channels'
    event_type: str = "literal"  # 'literal' or 'variable'


@dataclass
class WebSocketLinkResult:
    """Result of WebSocket linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# ============================================================================
# Common patterns for variable detection (shared with MQ/IPC linker pattern)
# ============================================================================

# Identifier pattern: matches variable names, constants, and simple attribute access
_IDENTIFIER = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"

# Event argument pattern: matches either a string literal OR an identifier
# Group 1: string literal content (if literal)
# Group 2: identifier/variable name (if variable)
_EVENT_ARG = rf"(?:['\"]([^'\"]+)['\"]|({_IDENTIFIER}))"


def _extract_event_from_match(match: re.Match, literal_group: int, var_group: int) -> tuple[str, str]:
    """Extract event and event_type from a regex match.

    Args:
        match: Regex match object
        literal_group: Group index for string literal content
        var_group: Group index for variable name

    Returns:
        tuple of (event_value, event_type) where event_type is 'literal' or 'variable'
    """
    literal = match.group(literal_group)
    variable = match.group(var_group)

    if literal:
        return (literal, "literal")
    elif variable:
        return (variable, "variable")
    else:
        return ("unknown", "variable")  # pragma: no cover


# Regex patterns for WebSocket detection

# Socket.io emit patterns (message_send) - matches both literals and variables
SOCKETIO_EMIT_PATTERN = re.compile(
    rf"(?:socket|io)\s*\.\s*emit\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# Socket.io on patterns (message_receive) - matches both literals and variables
SOCKETIO_ON_PATTERN = re.compile(
    rf"(?:socket|io)\s*\.\s*on\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# Native WebSocket constructor (literal quoted URL)
NATIVE_WEBSOCKET_PATTERN = re.compile(
    r"new\s+WebSocket\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# WI-zolot: Native WebSocket constructor with template-string URL.
# Browser code canonically computes the URL as
#   `${proto}//${location.host}/ws`
# i.e. interpolates the host/scheme but keeps the route path as a trailing
# literal. We extract the TRAILING literal path component (``/ws``) — the
# part that matches the server's ``WebSocketRoute("/ws", handler)`` /
# ``@app.websocket("/ws")`` declaration. The character class
# ``[a-zA-Z0-9_/-]`` excludes ``$`` and ``{``/``}`` so the non-greedy
# ``[^`]*?`` consumes any leading ``${...}`` interpolations before the
# capture latches onto the last contiguous run of literal path chars
# preceding the closing backtick.
NATIVE_WEBSOCKET_TEMPLATE_PATTERN = re.compile(
    r"new\s+WebSocket\s*\(\s*`[^`]*?(/[a-zA-Z0-9_/-]+)\s*`",
    re.MULTILINE,
)

# WI-zolot: function-extracted URL constructor pattern.
# Real-world TS/JS code often factors the URL into a helper:
#     function getWsUrl() { return `${proto}//${host}/ws`; }
#     ws = new WebSocket(getWsUrl());
# Hypergumbo's own ws-client.ts uses this exact idiom. The inline-template
# regex above misses it because the literal and the constructor are in
# different expressions. The heuristic: a TS/JS file that uses
# ``new WebSocket(...)`` AND has a template literal whose trailing path is
# preceded by at least one ``${...}`` block — i.e. URL-shaped, not a plain
# log message — is treated as opening a WS to that path. The ``\}`` in the
# pattern guarantees an interpolation occurred before the trailing path,
# which discriminates URL templates from generic log strings.
HAS_WEBSOCKET_CONSTRUCTOR = re.compile(r"\bnew\s+WebSocket\s*\(", re.MULTILINE)
TS_TEMPLATE_URL_PATH_PATTERN = re.compile(
    r"`[^`]*?\}[^`]*?(/[a-zA-Z0-9_/-]+)\s*`",
    re.MULTILINE,
)

# ws/wss.on patterns (Node.js ws package) - matches both literals and variables
WS_ON_PATTERN = re.compile(
    rf"(?:ws|wss|server)\s*\.\s*on\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# WebSocket send
WEBSOCKET_SEND_PATTERN = re.compile(
    r"(?:ws|socket|connection)\s*\.\s*send\s*\(",
    re.MULTILINE,
)

# ============================================================================
# Python WebSocket patterns
# ============================================================================

# FastAPI @app.websocket('/path') decorator
FASTAPI_WEBSOCKET_DECORATOR = re.compile(
    r"@\w+\.websocket\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# WI-zolot: Starlette routing-table style. ``WebSocketRoute("/path", handler)``
# is the routing-table style used by Starlette (and by hypergumbo's own
# serve.py:433 ``WebSocketRoute("/ws", _ws_handler)``). The pre-fix linker
# only recognised the FastAPI decorator form, so Starlette WS handlers
# disappeared.
STARLETTE_WEBSOCKET_ROUTE = re.compile(
    r"WebSocketRoute\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# FastAPI/Starlette websocket.receive_json() / receive_text()
PYTHON_WEBSOCKET_RECEIVE = re.compile(
    r"websocket\s*\.\s*(?:receive_json|receive_text|receive)\s*\(",
    re.MULTILINE,
)

# FastAPI/Starlette websocket.send_json() / send_text()
PYTHON_WEBSOCKET_SEND = re.compile(
    r"websocket\s*\.\s*(?:send_json|send_text|send)\s*\(",
    re.MULTILINE,
)

# FastAPI/Starlette websocket.accept()
PYTHON_WEBSOCKET_ACCEPT = re.compile(
    r"websocket\s*\.\s*accept\s*\(",
    re.MULTILINE,
)

# Django Channels: channel_layer.send('channel', message) - matches both literals and variables
DJANGO_CHANNEL_SEND = re.compile(
    rf"channel_layer\s*\.\s*(?:send|group_send)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# Django Channels: async_to_sync(channel_layer.send) - matches both literals and variables
DJANGO_ASYNC_SEND = re.compile(
    rf"async_to_sync\s*\(\s*channel_layer\s*\.\s*(?:send|group_send)\s*\)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# Django Channels: self.send() in consumer
DJANGO_CONSUMER_SEND = re.compile(
    r"self\s*\.\s*send\s*\(",
    re.MULTILINE,
)

# Django Channels: WebsocketConsumer class
DJANGO_WEBSOCKET_CONSUMER = re.compile(
    r"class\s+(\w+)\s*\([^)]*WebsocketConsumer[^)]*\)",
    re.MULTILINE,
)

# Django Channels: routing path
DJANGO_CHANNELS_ROUTE = re.compile(
    r"(?:re_)?path\s*\(\s*['\"]([^'\"]+)['\"].*?(?:AsgiHandler|as_asgi)\s*\(",
    re.MULTILINE | re.DOTALL,
)


def find_js_ts_files(repo_root: Path) -> Iterator[Path]:
    """Yield all JS/TS files in the repository."""
    yield from find_files(repo_root, ["*.js", "*.jsx", "*.ts", "*.tsx", "*.vue", "*.svelte"])


def find_python_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Python files in the repository."""
    yield from find_files(repo_root, ["*.py"])


def _make_symbol_id(path: str, line: int, event: str, kind: str) -> str:
    """Generate ID for a WebSocket-related symbol."""
    return f"websocket:{path}:{line}:{event}:{kind}"


def _make_file_id(language: str, path: str) -> str:
    """Generate ID for a file node using the canonical ``make_file_id`` shape.

    INV-ronuf: historically this returned ``websocket:{path}:1-1:file:file``,
    which never collided with the orchestrator's canonical
    ``{language}:{path}:1-1:file:file`` shape — every WS-emitted file Symbol
    became a phantom shadow of the analyzer/orchestrator-emitted one.
    Using the canonical shape lets ``ctx.symbols``-based dedup (and the
    orchestrator's dangling-edge synthesizer) collapse them naturally.
    """
    return make_file_id(language, path)


def _language_for_file(file_path: str, pattern_type: str) -> str:
    """Resolve a file's language, preferring extension over ``pattern_type``.

    INV-ronuf clause 3: a ``.ts`` file with Socket.io patterns must be
    recorded as ``typescript``, not ``javascript``. Falls back to the
    pattern_type-derived language only when the extension is unknown
    (e.g., Django Channels handler in a path without a recognised
    extension, or a synthetic test path).
    """
    by_ext = language_from_path(Path(file_path))
    if by_ext is not None:
        return by_ext
    if pattern_type in ("fastapi", "django_channels", "starlette"):
        return "python"
    return "javascript"


def _detect_patterns(file_path: Path) -> list[WebSocketPattern]:
    """Detect WebSocket patterns in a JavaScript/TypeScript file."""
    try:
        content = read_masked_source(file_path, encoding="utf-8", errors="replace")
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
    # Groups: 1=literal event, 2=variable event
    for match in SOCKETIO_EMIT_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match, 1, 2)
        patterns.append(WebSocketPattern(
            type="send",
            event=event_name,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="socketio",
            event_type=event_type,
        ))

    # Socket.io on (message_receive)
    # Groups: 1=literal event, 2=variable event
    for match in SOCKETIO_ON_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match, 1, 2)
        ws_type = "endpoint" if event_name == "connection" else "receive"
        patterns.append(WebSocketPattern(
            type=ws_type,
            event=event_name,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="socketio",
            event_type=event_type,
        ))

    # Native WebSocket constructor (literal-quoted URL)
    for match in NATIVE_WEBSOCKET_PATTERN.finditer(content):
        url = match.group(1)
        patterns.append(WebSocketPattern(
            type="endpoint",
            event=url,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="native",
            event_type="literal",
        ))

    # WI-zolot: Native WebSocket constructor with template-string URL.
    # The TS client at packages/htrac-frontend/src/ws-client.ts builds the
    # URL as ``${proto}//${location.host}/ws`` — backticks, with the route
    # path as a trailing literal. We extract that trailing path so the
    # cross-language bridge edge has something to match the server's route
    # declaration against.
    seen_template_paths: set[tuple[str, int]] = set()
    for match in NATIVE_WEBSOCKET_TEMPLATE_PATTERN.finditer(content):
        url_path = match.group(1)
        line_no = get_line_number(match.start())
        seen_template_paths.add((url_path, line_no))
        patterns.append(WebSocketPattern(
            type="endpoint",
            event=url_path,
            line=line_no,
            file_path=str(file_path),
            pattern_type="native",
            event_type="literal",
        ))

    # WI-zolot: function-extracted URL pattern. When the file uses
    # ``new WebSocket(...)`` anywhere AND has a URL-shaped template literal
    # (interpolation followed by trailing literal path), treat each such
    # template as a probable WS endpoint. Skip paths already captured by
    # the inline-template scan above so the inline case isn't double-counted.
    if HAS_WEBSOCKET_CONSTRUCTOR.search(content):
        for match in TS_TEMPLATE_URL_PATH_PATTERN.finditer(content):
            url_path = match.group(1)
            line_no = get_line_number(match.start())
            if (url_path, line_no) in seen_template_paths:
                continue
            patterns.append(WebSocketPattern(
                type="endpoint",
                event=url_path,
                line=line_no,
                file_path=str(file_path),
                pattern_type="native",
                event_type="literal",
            ))

    # ws package on patterns
    # Groups: 1=literal event, 2=variable event
    for match in WS_ON_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match, 1, 2)
        ws_type = "endpoint" if event_name == "connection" else "receive"
        patterns.append(WebSocketPattern(
            type=ws_type,
            event=event_name,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="ws",
            event_type=event_type,
        ))

    # WebSocket send (no specific event, generic message)
    for match in WEBSOCKET_SEND_PATTERN.finditer(content):
        patterns.append(WebSocketPattern(
            type="send",
            event="message",  # Generic message event
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="native",
            event_type="literal",
        ))

    return patterns


def _detect_python_patterns(file_path: Path) -> list[WebSocketPattern]:
    """Detect WebSocket patterns in a Python file (Django Channels, FastAPI)."""
    try:
        content = read_masked_source(file_path, encoding="utf-8", errors="replace")
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

    # FastAPI @app.websocket('/path') decorator (literal paths only)
    for match in FASTAPI_WEBSOCKET_DECORATOR.finditer(content):
        path = match.group(1)
        patterns.append(WebSocketPattern(
            type="endpoint",
            event=path,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="fastapi",
            event_type="literal",
        ))

    # WI-zolot: Starlette ``WebSocketRoute("/path", handler)`` routing-table
    # entries. Hypergumbo's own serve.py uses this form (no decorator). The
    # pattern_type "starlette" distinguishes it from FastAPI's decorator
    # form so downstream consumers can tell the dispatch shape apart, even
    # though both produce the same canonical endpoint Symbol.
    for match in STARLETTE_WEBSOCKET_ROUTE.finditer(content):
        path = match.group(1)
        patterns.append(WebSocketPattern(
            type="endpoint",
            event=path,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="starlette",
            event_type="literal",
        ))

    # FastAPI websocket.receive_*() (generic message, literal)
    for match in PYTHON_WEBSOCKET_RECEIVE.finditer(content):
        patterns.append(WebSocketPattern(
            type="receive",
            event="message",
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="fastapi",
            event_type="literal",
        ))

    # FastAPI websocket.send_*() (generic message, literal)
    for match in PYTHON_WEBSOCKET_SEND.finditer(content):
        patterns.append(WebSocketPattern(
            type="send",
            event="message",
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="fastapi",
            event_type="literal",
        ))

    # FastAPI websocket.accept()
    for match in PYTHON_WEBSOCKET_ACCEPT.finditer(content):
        patterns.append(WebSocketPattern(
            type="endpoint",
            event="websocket_accept",
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="fastapi",
            event_type="literal",
        ))

    # Django Channels: channel_layer.send/group_send
    # Groups: 1=literal channel, 2=variable channel
    for match in DJANGO_CHANNEL_SEND.finditer(content):
        channel, event_type = _extract_event_from_match(match, 1, 2)
        patterns.append(WebSocketPattern(
            type="send",
            event=channel,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="django_channels",
            event_type=event_type,
        ))

    # Django Channels: async_to_sync(channel_layer.send)
    # Groups: 1=literal channel, 2=variable channel
    for match in DJANGO_ASYNC_SEND.finditer(content):
        channel, event_type = _extract_event_from_match(match, 1, 2)
        patterns.append(WebSocketPattern(
            type="send",
            event=channel,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="django_channels",
            event_type=event_type,
        ))

    # Django Channels: WebsocketConsumer class (literal class names)
    for match in DJANGO_WEBSOCKET_CONSUMER.finditer(content):
        class_name = match.group(1)
        patterns.append(WebSocketPattern(
            type="endpoint",
            event=class_name,
            line=get_line_number(match.start()),
            file_path=str(file_path),
            pattern_type="django_channels",
            event_type="literal",
        ))

    return patterns


def link_websocket(
    repo_root: Path,
    existing_symbol_ids: "set[str] | None" = None,
) -> WebSocketLinkResult:
    """Detect WebSocket patterns and create linking edges.

    Scans all JavaScript/TypeScript and Python files for WebSocket patterns and creates:
    - Symbols for WebSocket endpoints (connection handlers)
    - message_send edges for emit/send calls
    - message_receive edges for on/onmessage handlers

    INV-ronuf
    ---------
    File-kind Symbols are synthesized only for paths whose canonical
    ``make_file_id`` is **not** already present in ``existing_symbol_ids``
    (which the linker registry wires up from ``LinkerContext.symbols``).
    When the orchestrator's dangling-edge synthesizer or a language
    analyzer has already produced a canonical file Symbol, the WS linker
    reuses that id via canonical-shape match rather than emitting a
    parallel shadow node. When ``existing_symbol_ids`` is ``None`` (the
    legacy direct-call form retained for unit tests), the linker emits
    file Symbols for every path it discovers.

    Returns a WebSocketLinkResult with edges, symbols, and run info.
    """
    existing_ids = existing_symbol_ids or set()
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[WebSocketPattern] = []
    files_analyzed = 0

    # Scan JavaScript/TypeScript files
    for file_path in find_js_ts_files(repo_root):
        patterns = _detect_patterns(file_path)
        all_patterns.extend(patterns)
        if patterns:
            files_analyzed += 1

    # Scan Python files
    for file_path in find_python_files(repo_root):
        patterns = _detect_python_patterns(file_path)
        all_patterns.extend(patterns)
        if patterns:
            files_analyzed += 1

    # WI-hifol: normalize pattern.file_path values to repo-relative strings
    # using the SAME prefix-strip algorithm as the orchestrator's path
    # normalizer (analyze/all_analyzers.py::run_all_analyzers, ~line 204).
    # Pattern collection uses Path objects from find_*_files which yield
    # absolute paths. The orchestrator's file-symbol synthesis and language
    # analyzers emit ids with repo-relative paths (paths are normalized
    # before linkers run), so the WS linker must match that convention or
    # canonical-shape dedup silently misses every cross-producer collision.
    # We mirror the orchestrator's exact algorithm (forward-slash + prefix
    # strip, NO symlink resolution) so resulting paths are byte-equivalent.
    from ..paths import normalize_path as _norm_path
    _ws_root_prefix = _norm_path(str(repo_root)).rstrip("/") + "/"
    for pattern in all_patterns:
        normed = _norm_path(pattern.file_path)
        if normed.startswith(_ws_root_prefix):
            pattern.file_path = normed[len(_ws_root_prefix):]
        else:  # pragma: no cover
            # find_*_files only yields paths under repo_root, so this branch
            # is structurally unreachable in production; keep as defense.
            pattern.file_path = normed

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

    # ADR-0027 Phase 3 / audit-findings 0013: Symbol.kind="websocket_endpoint"
    # is a framework-role leak (Test 4: mechanism vs. category). Fold to the
    # canonical Cluster A construct kind="function" + meta["framework_role"]
    # so the kind axis stays language-construct-only.
    #
    # ADR-0028 Phase 3 / audit-findings 0014: the dynamic f-string emits
    # f"{pattern_type}_emit" and f"{pattern_type}_endpoint" leak framework
    # identity into evidence_type. Fold to canonical inference label
    # ast_call_direct + meta["framework_dispatch"]=<framework_name>.
    # _PATTERN_TYPE_TO_FRAMEWORK maps the linker's internal pattern_type slug
    # to the framework_dispatch value prescribed by audit-findings 0014:
    #   pattern_type "native"  →  framework_dispatch "native_websocket"
    #   (all other pattern_types pass through as their own framework name)
    _PATTERN_TYPE_TO_FRAMEWORK = {
        "django_channels": "django_channels",
        "fastapi": "fastapi",
        "native": "native_websocket",
        "socketio": "socketio",
        "starlette": "starlette",
        "ws": "ws",
    }

    # Create symbols for endpoints
    symbols: list[Symbol] = []
    for ep in endpoints:
        # ADR-0031 Class B: synthetic stand-in for a WebSocket endpoint.
        symbols.append(Symbol(
            id=_make_symbol_id(ep.file_path, ep.line, ep.event, "endpoint"),
            name=f"ws:{ep.event}",
            kind="function",
            language=None,
            discovery_language=_language_for_file(ep.file_path, ep.pattern_type),
            protocol_origin="websocket",
            path=ep.file_path,
            span=Span(start_line=ep.line, end_line=ep.line, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta={
                "pattern_type": ep.pattern_type,
                "event_type": ep.event_type,
                "framework_role": "websocket_endpoint",
            },
        ))

    # Collect all files involved in WebSocket messaging for file symbol creation
    files_with_patterns: dict[str, str] = {}  # file_path -> pattern_type
    for patterns_list in sends.values():
        for pat in patterns_list:
            files_with_patterns[pat.file_path] = pat.pattern_type
    for patterns_list in receives.values():
        for pat in patterns_list:
            files_with_patterns[pat.file_path] = pat.pattern_type
    for ep in endpoints:
        files_with_patterns[ep.file_path] = ep.pattern_type

    # Create file symbols for all files with WebSocket patterns
    # These enable slice traversal of websocket_message edges.
    #
    # INV-ronuf: skip synthesis when the canonical id is already present in
    # ``existing_ids`` (i.e., an analyzer or the orchestrator's dangling-
    # edge synthesizer has already minted a file Symbol for this path).
    # Synthesized Symbols use the canonical ``make_file_id`` shape, derive
    # ``language`` from the file extension, and stamp ``stable_id`` via
    # ``make_file_stable_id`` to satisfy the INV-piroh schema gate.
    for file_path, pattern_type in files_with_patterns.items():
        language = _language_for_file(file_path, pattern_type)
        file_id = _make_file_id(language, file_path)
        if file_id in existing_ids:
            continue
        file_name = Path(file_path).name
        symbols.append(Symbol(
            id=file_id,
            name=file_name,
            kind="file",
            language=language,
            path=file_path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            stable_id=make_file_stable_id(language, file_path),
        ))

    # Pattern types that use "message" as a synthetic placeholder for
    # generic protocol operations (ws.send(), websocket.receive_text(), etc.)
    # rather than a real named event.  Linking these across files creates
    # NxM combinatorial explosion without meaningful semantic signal.
    _GENERIC_PROTOCOL_TYPES = frozenset(("fastapi", "native"))

    # Create edges linking senders to receivers with matching events
    edges: list[Edge] = []
    for event, send_patterns in sends.items():
        if event in receives:
            for send_pat in send_patterns:
                for recv_pat in receives[event]:
                    # Don't link same file to itself for simple patterns
                    if send_pat.file_path == recv_pat.file_path:
                        continue
                    # Skip generic protocol send/receive (no named event).
                    # FastAPI/native websocket.send_*/receive_* are raw
                    # WebSocket operations—not named events like Socket.io
                    # emit/on or Django Channels typed messages.
                    if (
                        event == "message"
                        and send_pat.pattern_type in _GENERIC_PROTOCOL_TYPES
                        and recv_pat.pattern_type in _GENERIC_PROTOCOL_TYPES
                    ):
                        continue
                    # Confidence depends on whether events are literal or variable
                    is_variable_match = (
                        send_pat.event_type == "variable" or recv_pat.event_type == "variable"
                    )
                    confidence = 0.65 if is_variable_match else 0.85
                    # ADR-0028 Phase 3 / audit-findings 0014: f"{pattern_type}_emit"
                    # leaked framework identity into evidence_type. Fold to canonical
                    # ast_call_direct + meta["framework_dispatch"]=<framework_name>.
                    # The variable_match branch keeps its Cluster A canonical.
                    evidence_type = "variable_match" if is_variable_match else "ast_call_direct"
                    # Pass linker-specific meta via Edge.create's meta= kwarg
                    # so Edge.create merges it with the dataflow fields —
                    # ADR-0023 §6 Phase 3 / audit-findings 0002 (WI-hahap-farid):
                    # WebSocket sender→receiver via channel is publish-
                    # family shape; "websocket" is the channel kind.
                    # Canonical 'event_publishes' +
                    # meta['channel_kind']='websocket'. Pass meta via
                    # Edge.create's meta= kwarg so it merges with
                    # dataflow fields (assigning edge.meta afterward
                    # would wipe access_mode/dest_access_mode — INV-forim).
                    edge = Edge.create(
                        src=_make_file_id(
                            _language_for_file(send_pat.file_path, send_pat.pattern_type),
                            send_pat.file_path,
                        ),
                        dst=_make_file_id(
                            _language_for_file(recv_pat.file_path, recv_pat.pattern_type),
                            recv_pat.file_path,
                        ),
                        edge_type="event_publishes",
                        line=send_pat.line,
                        evidence_type=evidence_type,
                        confidence=confidence,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        access_mode="write",
                        dest_access_mode="read",
                        channel=event,
                        meta={
                            "channel_kind": "websocket",
                            "event": event,
                            "event_type": "variable" if is_variable_match else "literal",
                            "framework_dispatch": _PATTERN_TYPE_TO_FRAMEWORK[
                                send_pat.pattern_type
                            ],
                        },
                        derived_from=[_make_file_id(_language_for_file(send_pat.file_path, send_pat.pattern_type), send_pat.file_path), _make_file_id(_language_for_file(recv_pat.file_path, recv_pat.pattern_type), recv_pat.file_path)],
                    )
                    edges.append(edge)

    # ADR-0023 §6 Phase 3 / audit-findings 0002 (WI-hahap-farid): WebSocket
    # endpoint connections declare connectivity (file → endpoint
    # symbol), they don't carry messages. Canonical 'references' +
    # meta['construct']='websocket_endpoint'.
    #
    # ADR-0028 Phase 3 / audit-findings 0014: f"{pattern_type}_endpoint" leaked
    # framework identity into evidence_type. Fold to canonical ast_call_direct
    # + meta["framework_dispatch"]=<framework_name>.
    for ep in endpoints:
        edges.append(Edge.create(
            src=_make_file_id(
                _language_for_file(ep.file_path, ep.pattern_type),
                ep.file_path,
            ),
            dst=_make_symbol_id(ep.file_path, ep.line, ep.event, "endpoint"),
            edge_type="references",
            line=ep.line,
            evidence_type="ast_call_direct",
            confidence=0.90,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            access_mode="write",
            channel=ep.event,
            meta={
                "construct": "websocket_endpoint",
                "framework_dispatch": _PATTERN_TYPE_TO_FRAMEWORK[ep.pattern_type],
            },
            derived_from=[_make_file_id(_language_for_file(ep.file_path, ep.pattern_type), ep.file_path), _make_symbol_id(ep.file_path, ep.line, ep.event, 'endpoint')],
        ))

    # WI-zolot: cross-language client↔server bridge.
    #
    # Hypothesis investigation for hypergumbo's own repo
    # (notebookjournal_05162026_0438.md round 4) identified three concrete
    # gaps: (1) template-string URL not extracted on the client side; (2)
    # Starlette routing-table form not recognised on the server side; (3)
    # no cross-language pairing logic at all — only within-language
    # send/receive and file→endpoint references. Patterns (1) and (2) are
    # fixed above. This block fixes (3) by emitting a ``calls`` edge with
    # ``meta["protocol"]="ws"`` and ``meta["cross_language"]=True`` from
    # each client endpoint to each server endpoint that shares a path
    # string, following the HTTP linker convention (http.py:1511-1528) so
    # downstream consumers can treat WS and HTTP cross-language edges
    # uniformly.
    _CLIENT_PATTERN_TYPES = frozenset({"native", "socketio", "ws"})
    _SERVER_PATTERN_TYPES = frozenset({"fastapi", "starlette", "django_channels"})

    by_path: dict[str, list[WebSocketPattern]] = {}
    for ep in endpoints:
        by_path.setdefault(ep.event, []).append(ep)

    for path_str, eps in by_path.items():
        clients = [ep for ep in eps if ep.pattern_type in _CLIENT_PATTERN_TYPES]
        servers = [ep for ep in eps if ep.pattern_type in _SERVER_PATTERN_TYPES]
        if not clients or not servers:
            continue
        for client_ep in clients:
            client_lang = _language_for_file(client_ep.file_path, client_ep.pattern_type)
            for server_ep in servers:
                server_lang = _language_for_file(server_ep.file_path, server_ep.pattern_type)
                edges.append(Edge.create(
                    src=_make_file_id(client_lang, client_ep.file_path),
                    dst=_make_symbol_id(
                        server_ep.file_path,
                        server_ep.line,
                        server_ep.event,
                        "endpoint",
                    ),
                    edge_type="calls",
                    line=client_ep.line,
                    evidence_type="ast_call_direct",
                    confidence=0.85,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    access_mode="write",
                    channel=path_str,
                    meta={
                        "protocol": "ws",
                        "url_path": path_str,
                        "cross_language": client_lang != server_lang,
                        "client_framework": _PATTERN_TYPE_TO_FRAMEWORK[
                            client_ep.pattern_type
                        ],
                        "server_framework": _PATTERN_TYPE_TO_FRAMEWORK[
                            server_ep.pattern_type
                        ],
                    },
                    derived_from=[_make_file_id(client_lang, client_ep.file_path), _make_symbol_id(server_ep.file_path, server_ep.line, server_ep.event, 'endpoint')],
                ))

    run.files_analyzed = files_analyzed
    run.duration_ms = int((time.time() - start_time) * 1000)

    return WebSocketLinkResult(
        edges=edges,
        symbols=symbols,
        run=run,
    )


# =============================================================================
# Linker Registry Integration
# =============================================================================


@register_linker(
    "websocket-linker",
    priority=50,
    description="WebSocket communication pattern linking (Socket.io, ws, Django Channels)",
    # CNF: WebSocket patterns appear in any language with a WS library —
    # Socket.io (JS/TS), Django Channels (python), Phoenix (elixir),
    # Spring WebFlux (java), Action Cable (ruby), Gorilla (go), SignalR (csharp).
    depends_on=[["python", "javascript", "ruby", "java", "go", "csharp", "elixir"]],
)
def websocket_linker(ctx: LinkerContext) -> LinkerResult:
    """WebSocket linker for registry-based dispatch.

    This wraps link_websocket() to use the LinkerContext/LinkerResult interface.

    INV-ronuf: feeds ``ctx.symbols``-derived ids to ``link_websocket`` so
    file-Symbol synthesis dedupes against analyzer/orchestrator output via
    canonical ``make_file_id`` shape collision.
    """
    existing_ids = {s.id for s in ctx.symbols}
    result = link_websocket(ctx.repo_root, existing_symbol_ids=existing_ids)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
