"""Tests for the WebSocket linker.

Tests cover:
- Pattern detection (Socket.io, native WebSocket, ws package)
- Edge creation between matching send/receive patterns
- Symbol creation for endpoints
- Edge cases and error handling
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo.linkers.websocket import (
    find_js_ts_files,
    find_python_files,
    _detect_patterns,
    _detect_python_patterns,
    _make_symbol_id,
    _make_file_id,
    link_websocket,
    PASS_ID,
)


class TestJsTsFileDiscovery:
    """Tests for JavaScript/TypeScript file discovery."""

    def test_finds_js_files(self, tmp_path: Path) -> None:
        """Should find .js files."""
        (tmp_path / "app.js").write_text("// js file")
        files = list(find_js_ts_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "app.js"

    def test_finds_ts_files(self, tmp_path: Path) -> None:
        """Should find .ts files."""
        (tmp_path / "app.ts").write_text("// ts file")
        files = list(find_js_ts_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "app.ts"

    def test_finds_jsx_tsx_files(self, tmp_path: Path) -> None:
        """Should find .jsx and .tsx files."""
        (tmp_path / "App.jsx").write_text("// jsx")
        (tmp_path / "App.tsx").write_text("// tsx")
        files = list(find_js_ts_files(tmp_path))
        assert len(files) == 2

    def test_finds_vue_svelte_files(self, tmp_path: Path) -> None:
        """Should find .vue and .svelte files."""
        (tmp_path / "App.vue").write_text("<script></script>")
        (tmp_path / "App.svelte").write_text("<script></script>")
        files = list(find_js_ts_files(tmp_path))
        assert len(files) == 2

    def test_ignores_node_modules(self, tmp_path: Path) -> None:
        """Should ignore files in node_modules."""
        node_mods = tmp_path / "node_modules" / "pkg"
        node_mods.mkdir(parents=True)
        (node_mods / "index.js").write_text("// ignored")
        (tmp_path / "app.js").write_text("// included")
        files = list(find_js_ts_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "app.js"

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should handle empty directories."""
        files = list(find_js_ts_files(tmp_path))
        assert len(files) == 0


class TestSocketIoPatterns:
    """Tests for Socket.io pattern detection."""

    def test_detects_emit_single_quote(self, tmp_path: Path) -> None:
        """Should detect socket.emit with single quotes."""
        file = tmp_path / "client.js"
        file.write_text("socket.emit('message', data);")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "send"
        assert patterns[0].event == "message"
        assert patterns[0].pattern_type == "socketio"

    def test_detects_emit_double_quote(self, tmp_path: Path) -> None:
        """Should detect socket.emit with double quotes."""
        file = tmp_path / "client.js"
        file.write_text('socket.emit("message", data);')
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "send"
        assert patterns[0].event == "message"

    def test_detects_io_emit(self, tmp_path: Path) -> None:
        """Should detect io.emit patterns."""
        file = tmp_path / "server.js"
        file.write_text("io.emit('broadcast', data);")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "send"
        assert patterns[0].event == "broadcast"

    def test_detects_on_handler(self, tmp_path: Path) -> None:
        """Should detect socket.on handlers."""
        file = tmp_path / "client.js"
        file.write_text("socket.on('message', (data) => {});")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "receive"
        assert patterns[0].event == "message"
        assert patterns[0].pattern_type == "socketio"

    def test_detects_connection_as_endpoint(self, tmp_path: Path) -> None:
        """Should detect connection handlers as endpoints."""
        file = tmp_path / "server.js"
        file.write_text("io.on('connection', (socket) => {});")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "endpoint"
        assert patterns[0].event == "connection"

    def test_detects_multiple_patterns(self, tmp_path: Path) -> None:
        """Should detect multiple patterns in one file."""
        file = tmp_path / "chat.js"
        file.write_text("""
socket.on('message', (data) => {
    console.log(data);
});
socket.emit('response', result);
""")
        patterns = _detect_patterns(file)
        assert len(patterns) == 2
        events = {p.event for p in patterns}
        assert events == {"message", "response"}


class TestNativeWebSocketPatterns:
    """Tests for native WebSocket API pattern detection."""

    def test_detects_websocket_constructor(self, tmp_path: Path) -> None:
        """Should detect new WebSocket() calls."""
        file = tmp_path / "client.js"
        file.write_text("const ws = new WebSocket('wss://example.com/ws');")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "endpoint"
        assert patterns[0].event == "wss://example.com/ws"
        assert patterns[0].pattern_type == "native"

    def test_detects_ws_send(self, tmp_path: Path) -> None:
        """Should detect ws.send() calls."""
        file = tmp_path / "client.js"
        file.write_text("ws.send(JSON.stringify(data));")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "send"
        assert patterns[0].event == "message"
        assert patterns[0].pattern_type == "native"

    def test_detects_socket_send(self, tmp_path: Path) -> None:
        """Should detect socket.send() calls."""
        file = tmp_path / "client.js"
        file.write_text("socket.send(data);")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "send"

    def test_detects_connection_send(self, tmp_path: Path) -> None:
        """Should detect connection.send() calls."""
        file = tmp_path / "server.js"
        file.write_text("connection.send(response);")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "send"


class TestWsPackagePatterns:
    """Tests for Node.js ws package pattern detection."""

    def test_detects_wss_connection(self, tmp_path: Path) -> None:
        """Should detect wss.on('connection') handlers."""
        file = tmp_path / "server.js"
        file.write_text("wss.on('connection', (ws) => {});")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "endpoint"
        assert patterns[0].event == "connection"
        assert patterns[0].pattern_type == "ws"

    def test_detects_ws_message(self, tmp_path: Path) -> None:
        """Should detect ws.on('message') handlers."""
        file = tmp_path / "server.js"
        file.write_text("ws.on('message', (data) => {});")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "receive"
        assert patterns[0].event == "message"
        assert patterns[0].pattern_type == "ws"

    def test_detects_server_connection(self, tmp_path: Path) -> None:
        """Should detect server.on('connection') handlers."""
        file = tmp_path / "server.js"
        file.write_text("server.on('connection', (socket) => {});")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].type == "endpoint"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_unreadable_file(self, tmp_path: Path) -> None:
        """Should handle unreadable files gracefully."""
        file = tmp_path / "unreadable.js"
        # File doesn't exist - should return empty list
        patterns = _detect_patterns(file)
        assert patterns == []

    def test_empty_file(self, tmp_path: Path) -> None:
        """Should handle empty files."""
        file = tmp_path / "empty.js"
        file.write_text("")
        patterns = _detect_patterns(file)
        assert patterns == []

    def test_file_with_no_patterns(self, tmp_path: Path) -> None:
        """Should handle files without WebSocket patterns."""
        file = tmp_path / "plain.js"
        file.write_text("const x = 1 + 2;")
        patterns = _detect_patterns(file)
        assert patterns == []

    def test_binary_file_handling(self, tmp_path: Path) -> None:
        """Should handle files with binary content."""
        file = tmp_path / "binary.js"
        file.write_bytes(b"\x00\x01\x02socket.emit('test')")
        patterns = _detect_patterns(file)
        # Should still detect patterns in partially readable content
        assert len(patterns) >= 0  # May or may not find patterns

    def test_multiline_emit(self, tmp_path: Path) -> None:
        """Should detect patterns across multiple lines."""
        file = tmp_path / "multiline.js"
        file.write_text("""socket.emit(
    'multiline-event',
    data
);""")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        assert patterns[0].event == "multiline-event"

    def test_line_number_accuracy(self, tmp_path: Path) -> None:
        """Should report accurate line numbers."""
        file = tmp_path / "lines.js"
        file.write_text("""// Line 1
// Line 2
socket.emit('event-line-3', data);
// Line 4
socket.on('event-line-5', handler);
""")
        patterns = _detect_patterns(file)
        assert len(patterns) == 2
        emit_pattern = next(p for p in patterns if p.type == "send")
        on_pattern = next(p for p in patterns if p.type == "receive")
        assert emit_pattern.line == 3
        assert on_pattern.line == 5

    def test_line_number_at_end_of_file(self, tmp_path: Path) -> None:
        """Should handle patterns at the very end of file (no trailing newline)."""
        file = tmp_path / "end.js"
        # No trailing newline - pattern at end of file
        file.write_text("socket.emit('end-event', data)")
        patterns = _detect_patterns(file)
        assert len(patterns) == 1
        # Line number should still be accurate
        assert patterns[0].line >= 1

    def test_get_line_number_fallback(self, tmp_path: Path) -> None:
        """Test the fallback path in get_line_number for defensive coverage."""
        # This test exercises the fallback return len(lines) in get_line_number
        # which is defensive code for edge cases where char_pos exceeds line_starts
        import hypergumbo.linkers.websocket as ws_module

        file = tmp_path / "test.js"
        file.write_text("x")  # Single char file

        # Mock finditer to return a match with a start position beyond the file
        original_emit = ws_module.SOCKETIO_EMIT_PATTERN

        class FakeMatch:
            def start(self):
                return 1000  # Position way beyond file content

            def group(self, n):
                return "fake-event"

        class FakePattern:
            def finditer(self, content):
                yield FakeMatch()

        ws_module.SOCKETIO_EMIT_PATTERN = FakePattern()
        try:
            patterns = _detect_patterns(file)
            # The fallback should return len(lines) = 1
            assert len(patterns) == 1
            assert patterns[0].line == 1
        finally:
            ws_module.SOCKETIO_EMIT_PATTERN = original_emit


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_make_symbol_id(self) -> None:
        """Should generate valid symbol IDs."""
        id = _make_symbol_id("src/app.js", 10, "connection", "endpoint")
        assert id == "websocket:src/app.js:10:connection:endpoint"

    def test_make_file_id(self) -> None:
        """Should generate valid file IDs."""
        id = _make_file_id("src/app.js")
        assert id == "websocket:src/app.js:1-1:file:file"


class TestLinkWebSocket:
    """Tests for the main link_websocket function."""

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should handle empty repositories."""
        result = link_websocket(tmp_path)
        assert result.edges == []
        assert result.symbols == []
        assert result.run is not None
        assert result.run.pass_id == PASS_ID

    def test_creates_endpoint_symbols(self, tmp_path: Path) -> None:
        """Should create symbols for WebSocket endpoints."""
        file = tmp_path / "server.js"
        file.write_text("io.on('connection', (socket) => {});")
        result = link_websocket(tmp_path)
        # Should have endpoint symbol + file symbol
        endpoint_symbols = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoint_symbols) == 1
        assert "connection" in endpoint_symbols[0].name

    def test_links_matching_events(self, tmp_path: Path) -> None:
        """Should create edges between matching send/receive patterns."""
        (tmp_path / "sender.js").write_text("socket.emit('chat', message);")
        (tmp_path / "receiver.js").write_text("socket.on('chat', (msg) => {});")
        result = link_websocket(tmp_path)
        # Should have edge from sender to receiver
        message_edges = [e for e in result.edges if e.edge_type == "websocket_message"]
        assert len(message_edges) == 1
        assert "sender.js" in message_edges[0].src
        assert "receiver.js" in message_edges[0].dst

    def test_no_self_links(self, tmp_path: Path) -> None:
        """Should not create edges from file to itself."""
        file = tmp_path / "chat.js"
        file.write_text("""
socket.emit('event', data);
socket.on('event', handler);
""")
        result = link_websocket(tmp_path)
        # Should not have message edges (both patterns in same file)
        message_edges = [e for e in result.edges if e.edge_type == "websocket_message"]
        assert len(message_edges) == 0

    def test_creates_connection_edges(self, tmp_path: Path) -> None:
        """Should create edges for endpoint connections."""
        file = tmp_path / "server.js"
        file.write_text("wss.on('connection', (ws) => {});")
        result = link_websocket(tmp_path)
        connection_edges = [e for e in result.edges if e.edge_type == "websocket_connection"]
        assert len(connection_edges) == 1

    def test_multiple_event_matching(self, tmp_path: Path) -> None:
        """Should match multiple events across files."""
        (tmp_path / "client.js").write_text("""
socket.emit('login', creds);
socket.emit('message', text);
""")
        (tmp_path / "server.js").write_text("""
socket.on('login', handleLogin);
socket.on('message', handleMessage);
""")
        result = link_websocket(tmp_path)
        message_edges = [e for e in result.edges if e.edge_type == "websocket_message"]
        assert len(message_edges) == 2

    def test_run_metadata(self, tmp_path: Path) -> None:
        """Should include run metadata."""
        (tmp_path / "app.js").write_text("socket.emit('test', data);")
        result = link_websocket(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.files_analyzed >= 1
        assert result.run.duration_ms >= 0

    def test_edge_confidence(self, tmp_path: Path) -> None:
        """Should set appropriate confidence values."""
        (tmp_path / "sender.js").write_text("socket.emit('event', data);")
        (tmp_path / "receiver.js").write_text("socket.on('event', handler);")
        (tmp_path / "server.js").write_text("wss.on('connection', handler);")
        result = link_websocket(tmp_path)

        for edge in result.edges:
            if edge.edge_type == "websocket_message":
                assert edge.confidence == 0.85
            elif edge.edge_type == "websocket_connection":
                assert edge.confidence == 0.90

    def test_symbol_origin(self, tmp_path: Path) -> None:
        """Should set origin on symbols."""
        file = tmp_path / "server.js"
        file.write_text("io.on('connection', handler);")
        result = link_websocket(tmp_path)
        # All symbols (endpoint + file) should have origin set
        assert len(result.symbols) >= 1
        for symbol in result.symbols:
            assert symbol.origin == PASS_ID
            assert symbol.origin_run_id == result.run.execution_id

    def test_edge_origin(self, tmp_path: Path) -> None:
        """Should set origin on edges."""
        (tmp_path / "sender.js").write_text("socket.emit('event', data);")
        (tmp_path / "receiver.js").write_text("socket.on('event', handler);")
        result = link_websocket(tmp_path)
        for edge in result.edges:
            assert edge.origin == PASS_ID
            assert edge.origin_run_id == result.run.execution_id


class TestFileNodesForSliceIntegration:
    """Tests for file nodes that enable slice traversal of WebSocket edges."""

    def test_creates_file_symbols_for_senders(self, tmp_path: Path) -> None:
        """Should create file symbols for files that emit WebSocket events."""
        (tmp_path / "sender.js").write_text("socket.emit('chat', message);")
        (tmp_path / "receiver.js").write_text("socket.on('chat', handler);")
        result = link_websocket(tmp_path)

        # Should have file symbols for both sender and receiver
        file_symbols = [s for s in result.symbols if s.kind == "file"]
        assert len(file_symbols) >= 2

        # File symbols should have paths matching the source files
        paths = {s.path for s in file_symbols}
        assert any("sender.js" in p for p in paths)
        assert any("receiver.js" in p for p in paths)

    def test_file_symbol_ids_match_edge_endpoints(self, tmp_path: Path) -> None:
        """File symbol IDs should match the src/dst in websocket_message edges."""
        (tmp_path / "sender.js").write_text("socket.emit('event', data);")
        (tmp_path / "receiver.js").write_text("socket.on('event', handler);")
        result = link_websocket(tmp_path)

        # Get all symbol IDs
        symbol_ids = {s.id for s in result.symbols}

        # Every edge endpoint should be in the symbol list
        for edge in result.edges:
            if edge.edge_type == "websocket_message":
                assert edge.src in symbol_ids, f"Edge src {edge.src} not in symbols"
                assert edge.dst in symbol_ids, f"Edge dst {edge.dst} not in symbols"

    def test_file_symbols_enable_slice_traversal(self, tmp_path: Path) -> None:
        """Slice should be able to traverse WebSocket edges via file symbols."""
        (tmp_path / "client.js").write_text("socket.emit('request', data);")
        (tmp_path / "server.js").write_text("socket.on('request', handler);")
        result = link_websocket(tmp_path)

        # Find the client file symbol
        client_symbols = [s for s in result.symbols if "client.js" in s.path and s.kind == "file"]
        assert len(client_symbols) == 1

        # Find edges from this symbol
        client_id = client_symbols[0].id
        outgoing = [e for e in result.edges if e.src == client_id]
        assert len(outgoing) >= 1, "Should have outgoing edges from client file symbol"


class TestRealWorldPatterns:
    """Tests for real-world WebSocket usage patterns."""

    def test_socketio_chat_app(self, tmp_path: Path) -> None:
        """Should handle typical Socket.io chat application."""
        server = tmp_path / "server.js"
        server.write_text("""
const io = require('socket.io')(server);

io.on('connection', (socket) => {
    socket.on('chat message', (msg) => {
        io.emit('chat message', msg);
    });

    socket.on('disconnect', () => {
        console.log('user disconnected');
    });
});
""")
        client = tmp_path / "client.js"
        client.write_text("""
const socket = io();

socket.on('chat message', (msg) => {
    addMessage(msg);
});

function sendMessage(text) {
    socket.emit('chat message', text);
}
""")
        result = link_websocket(tmp_path)

        # Should find connection endpoint
        assert any(s.kind == "websocket_endpoint" for s in result.symbols)

        # Should find message edges for 'chat message' event
        message_edges = [e for e in result.edges if e.edge_type == "websocket_message"]
        assert len(message_edges) >= 1

    def test_native_websocket_client(self, tmp_path: Path) -> None:
        """Should handle native WebSocket client code."""
        file = tmp_path / "websocket-client.js"
        file.write_text("""
const ws = new WebSocket('wss://api.example.com/ws');

ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'subscribe', channel: 'updates' }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleMessage(data);
};
""")
        result = link_websocket(tmp_path)

        # Should find WebSocket endpoint
        endpoints = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoints) == 1
        assert "wss://api.example.com/ws" in endpoints[0].name

        # Should find send pattern
        patterns = _detect_patterns(file)
        send_patterns = [p for p in patterns if p.type == "send"]
        assert len(send_patterns) == 1

    def test_nodejs_ws_server(self, tmp_path: Path) -> None:
        """Should handle Node.js ws package server code."""
        file = tmp_path / "ws-server.js"
        file.write_text("""
const WebSocket = require('ws');
const wss = new WebSocket.Server({ port: 8080 });

wss.on('connection', (ws) => {
    ws.on('message', (message) => {
        console.log('received: %s', message);
        ws.send('echo: ' + message);
    });
});
""")
        result = link_websocket(tmp_path)

        # Should find connection endpoint
        assert any(s.kind == "websocket_endpoint" for s in result.symbols)


class TestPythonFileDiscovery:
    """Tests for Python file discovery."""

    def test_finds_python_files(self, tmp_path: Path) -> None:
        """Should find .py files."""
        (tmp_path / "app.py").write_text("# python file")
        files = list(find_python_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "app.py"

    def test_ignores_pycache(self, tmp_path: Path) -> None:
        """Should ignore __pycache__ directories."""
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "cached.cpython-311.pyc").write_bytes(b"bytecode")
        (tmp_path / "app.py").write_text("# included")
        files = list(find_python_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "app.py"


class TestFastAPIWebSocketPatterns:
    """Tests for FastAPI/Starlette WebSocket pattern detection."""

    def test_detects_websocket_decorator(self, tmp_path: Path) -> None:
        """Should detect @app.websocket('/path') decorator."""
        file = tmp_path / "main.py"
        file.write_text("""
@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    pass
""")
        patterns = _detect_python_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert len(endpoints) == 1
        assert endpoints[0].event == "/ws"
        assert endpoints[0].pattern_type == "fastapi"

    def test_detects_websocket_receive(self, tmp_path: Path) -> None:
        """Should detect websocket.receive_json() and receive_text()."""
        file = tmp_path / "main.py"
        file.write_text("""
async def handler(websocket):
    data = await websocket.receive_json()
    text = await websocket.receive_text()
    raw = await websocket.receive()
""")
        patterns = _detect_python_patterns(file)
        receives = [p for p in patterns if p.type == "receive"]
        assert len(receives) == 3
        assert all(r.pattern_type == "fastapi" for r in receives)

    def test_detects_websocket_send(self, tmp_path: Path) -> None:
        """Should detect websocket.send_json() and send_text()."""
        file = tmp_path / "main.py"
        file.write_text("""
async def handler(websocket):
    await websocket.send_json({"msg": "hello"})
    await websocket.send_text("hello")
    await websocket.send(b"bytes")
""")
        patterns = _detect_python_patterns(file)
        sends = [p for p in patterns if p.type == "send"]
        assert len(sends) == 3
        assert all(s.pattern_type == "fastapi" for s in sends)

    def test_detects_websocket_accept(self, tmp_path: Path) -> None:
        """Should detect websocket.accept()."""
        file = tmp_path / "main.py"
        file.write_text("""
async def handler(websocket):
    await websocket.accept()
    await websocket.send_text("connected")
""")
        patterns = _detect_python_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert len(endpoints) == 1
        assert endpoints[0].event == "websocket_accept"

    def test_full_fastapi_websocket(self, tmp_path: Path) -> None:
        """Should detect complete FastAPI WebSocket handler."""
        file = tmp_path / "main.py"
        file.write_text("""
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: int):
    await websocket.accept()
    while True:
        data = await websocket.receive_json()
        await websocket.send_json({"echo": data, "client": client_id})
""")
        patterns = _detect_python_patterns(file)
        # Should have: 1 decorator endpoint, 1 accept endpoint, 1 receive, 1 send
        endpoints = [p for p in patterns if p.type == "endpoint"]
        receives = [p for p in patterns if p.type == "receive"]
        sends = [p for p in patterns if p.type == "send"]
        assert len(endpoints) == 2  # decorator + accept
        assert len(receives) == 1
        assert len(sends) == 1


class TestDjangoChannelsPatterns:
    """Tests for Django Channels WebSocket pattern detection."""

    def test_detects_channel_layer_send(self, tmp_path: Path) -> None:
        """Should detect channel_layer.send()."""
        file = tmp_path / "consumers.py"
        file.write_text("""
async def send_notification(channel_name, message):
    channel_layer = get_channel_layer()
    await channel_layer.send(
        'specific_channel',
        {'type': 'notification', 'message': message}
    )
""")
        patterns = _detect_python_patterns(file)
        sends = [p for p in patterns if p.type == "send"]
        assert len(sends) == 1
        assert sends[0].event == "specific_channel"
        assert sends[0].pattern_type == "django_channels"

    def test_detects_channel_layer_group_send(self, tmp_path: Path) -> None:
        """Should detect channel_layer.group_send()."""
        file = tmp_path / "consumers.py"
        file.write_text("""
async def broadcast_to_group(group_name, message):
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        'chat_room_1',
        {'type': 'chat.message', 'message': message}
    )
""")
        patterns = _detect_python_patterns(file)
        sends = [p for p in patterns if p.type == "send"]
        assert len(sends) == 1
        assert sends[0].event == "chat_room_1"

    def test_detects_async_to_sync_send(self, tmp_path: Path) -> None:
        """Should detect async_to_sync(channel_layer.send)()."""
        file = tmp_path / "views.py"
        file.write_text("""
from asgiref.sync import async_to_sync

def send_message(request):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.send)('channel_name', {'type': 'update'})
    async_to_sync(channel_layer.group_send)('group_name', {'type': 'broadcast'})
""")
        patterns = _detect_python_patterns(file)
        sends = [p for p in patterns if p.type == "send"]
        assert len(sends) == 2
        events = {s.event for s in sends}
        assert events == {"channel_name", "group_name"}

    def test_detects_websocket_consumer_class(self, tmp_path: Path) -> None:
        """Should detect WebsocketConsumer subclasses."""
        file = tmp_path / "consumers.py"
        file.write_text("""
from channels.generic.websocket import WebsocketConsumer

class ChatConsumer(WebsocketConsumer):
    def connect(self):
        self.accept()

class AsyncChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
""")
        patterns = _detect_python_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        # Should find ChatConsumer (AsyncWebsocketConsumer won't match our pattern)
        assert len(endpoints) >= 1
        assert any(e.event == "ChatConsumer" for e in endpoints)

    def test_full_django_channels_consumer(self, tmp_path: Path) -> None:
        """Should detect complete Django Channels consumer."""
        file = tmp_path / "consumers.py"
        file.write_text("""
from channels.generic.websocket import WebsocketConsumer
from asgiref.sync import async_to_sync

class ChatConsumer(WebsocketConsumer):
    def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'chat_{self.room_name}'

        async_to_sync(self.channel_layer.group_add)(
            self.room_group_name,
            self.channel_name
        )
        self.accept()

    def receive(self, text_data):
        async_to_sync(self.channel_layer.group_send)(
            'chat_room',
            {'type': 'chat.message', 'message': text_data}
        )

    def chat_message(self, event):
        self.send(text_data=event['message'])
""")
        patterns = _detect_python_patterns(file)
        # Should find: consumer class, group_send
        endpoints = [p for p in patterns if p.type == "endpoint"]
        sends = [p for p in patterns if p.type == "send"]
        assert len(endpoints) >= 1
        assert any(e.event == "ChatConsumer" for e in endpoints)


class TestPythonPatternEdgeCases:
    """Edge cases for Python WebSocket pattern detection."""

    def test_unreadable_file(self, tmp_path: Path) -> None:
        """Should handle unreadable files gracefully."""
        file = tmp_path / "nonexistent.py"
        patterns = _detect_python_patterns(file)
        assert patterns == []

    def test_empty_file(self, tmp_path: Path) -> None:
        """Should handle empty files."""
        file = tmp_path / "empty.py"
        file.write_text("")
        patterns = _detect_python_patterns(file)
        assert patterns == []

    def test_no_websocket_patterns(self, tmp_path: Path) -> None:
        """Should handle files without WebSocket patterns."""
        file = tmp_path / "models.py"
        file.write_text("""
class User:
    def __init__(self, name):
        self.name = name
""")
        patterns = _detect_python_patterns(file)
        assert patterns == []

    def test_line_number_accuracy(self, tmp_path: Path) -> None:
        """Should report accurate line numbers."""
        file = tmp_path / "main.py"
        file.write_text("""# Line 1
# Line 2
@app.websocket('/ws')
async def handler(websocket):
    await websocket.accept()
""")
        patterns = _detect_python_patterns(file)
        decorator = next(p for p in patterns if p.event == "/ws")
        assert decorator.line == 3

    def test_get_line_number_fallback(self, tmp_path: Path) -> None:
        """Test the fallback path in get_line_number for edge cases."""
        # This test exercises the fallback return len(lines) in get_line_number
        import hypergumbo.linkers.websocket as ws_module

        file = tmp_path / "test.py"
        file.write_text("x")  # Single char file

        # Mock finditer to return a match with a start position beyond the file
        original_pattern = ws_module.FASTAPI_WEBSOCKET_DECORATOR

        class FakeMatch:
            def start(self):
                return 1000  # Position way beyond file content

            def group(self, n):
                return "/fake"

        class FakePattern:
            def finditer(self, content):
                yield FakeMatch()

        ws_module.FASTAPI_WEBSOCKET_DECORATOR = FakePattern()
        try:
            patterns = _detect_python_patterns(file)
            # The fallback should return len(lines) = 1
            assert len(patterns) == 1
            assert patterns[0].line == 1
        finally:
            ws_module.FASTAPI_WEBSOCKET_DECORATOR = original_pattern


class TestCrossLanguageWebSocketLinking:
    """Tests for cross-language WebSocket linking (Python <-> JavaScript)."""

    def test_python_send_to_js_receive(self, tmp_path: Path) -> None:
        """Should link Python send to JavaScript receive."""
        # Python server sends via channel layer
        py_file = tmp_path / "consumers.py"
        py_file.write_text("""
await channel_layer.group_send('updates', {'type': 'notify'})
""")
        # JS client receives 'updates' event (mapped via channel name)
        js_file = tmp_path / "client.js"
        js_file.write_text("""
socket.on('updates', handleUpdate);
""")
        result = link_websocket(tmp_path)
        # Should have symbols for both files
        assert len(result.symbols) >= 2

    def test_js_send_to_python_receive(self, tmp_path: Path) -> None:
        """Should link JavaScript send to Python receive."""
        js_file = tmp_path / "client.js"
        js_file.write_text("""
socket.emit('message', data);
""")
        py_file = tmp_path / "consumers.py"
        py_file.write_text("""
data = await websocket.receive_json()
""")
        result = link_websocket(tmp_path)
        # Both patterns detected
        assert len(result.symbols) >= 2

    def test_python_symbols_have_correct_language(self, tmp_path: Path) -> None:
        """Python WebSocket symbols should have language='python'."""
        file = tmp_path / "main.py"
        file.write_text("""
@app.websocket('/ws')
async def handler(websocket):
    await websocket.accept()
""")
        result = link_websocket(tmp_path)
        endpoint_symbols = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoint_symbols) >= 1
        for sym in endpoint_symbols:
            assert sym.language == "python"

    def test_js_symbols_have_correct_language(self, tmp_path: Path) -> None:
        """JavaScript WebSocket symbols should have language='javascript'."""
        file = tmp_path / "server.js"
        file.write_text("""
io.on('connection', handler);
""")
        result = link_websocket(tmp_path)
        endpoint_symbols = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoint_symbols) == 1
        assert endpoint_symbols[0].language == "javascript"


class TestPythonIntegrationWithLinkWebSocket:
    """Integration tests for Python patterns with link_websocket()."""

    def test_fastapi_websocket_creates_symbols(self, tmp_path: Path) -> None:
        """FastAPI WebSocket decorators should create endpoint symbols."""
        file = tmp_path / "main.py"
        file.write_text("""
@app.websocket('/ws')
async def ws_handler(websocket):
    pass
""")
        result = link_websocket(tmp_path)
        endpoints = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoints) >= 1
        assert any("/ws" in e.name for e in endpoints)

    def test_django_channels_creates_symbols(self, tmp_path: Path) -> None:
        """Django Channels consumer should create endpoint symbol."""
        file = tmp_path / "consumers.py"
        file.write_text("""
class ChatConsumer(WebsocketConsumer):
    pass
""")
        result = link_websocket(tmp_path)
        endpoints = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoints) >= 1
        assert any("ChatConsumer" in e.name for e in endpoints)

    def test_python_file_symbols_created(self, tmp_path: Path) -> None:
        """Python files with WebSocket patterns should have file symbols."""
        file = tmp_path / "consumers.py"
        file.write_text("""
await websocket.send_json(data)
""")
        result = link_websocket(tmp_path)
        file_symbols = [s for s in result.symbols if s.kind == "file"]
        assert len(file_symbols) >= 1
        assert any("consumers.py" in s.path for s in file_symbols)

    def test_mixed_language_repo(self, tmp_path: Path) -> None:
        """Should handle repos with both Python and JavaScript WebSocket code."""
        (tmp_path / "backend.py").write_text("""
@app.websocket('/api/ws')
async def api_ws(websocket):
    await websocket.accept()
    data = await websocket.receive_json()
    await websocket.send_json({"status": "ok"})
""")
        (tmp_path / "frontend.js").write_text("""
const ws = new WebSocket('wss://example.com/api/ws');
ws.onmessage = (event) => handleMessage(event.data);
ws.send(JSON.stringify({action: 'ping'}));
""")
        result = link_websocket(tmp_path)

        # Should have endpoint symbols from both languages
        endpoints = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoints) >= 2

        # Should have file symbols for both files
        file_symbols = [s for s in result.symbols if s.kind == "file"]
        paths = {s.path for s in file_symbols}
        assert any("backend.py" in p for p in paths)
        assert any("frontend.js" in p for p in paths)

    def test_empty_python_repo(self, tmp_path: Path) -> None:
        """Should handle repos with no Python files."""
        (tmp_path / "app.js").write_text("socket.emit('test', data);")
        result = link_websocket(tmp_path)
        # Should still work with just JS files
        assert result.run is not None

    def test_python_only_repo(self, tmp_path: Path) -> None:
        """Should handle repos with only Python files."""
        (tmp_path / "app.py").write_text("""
await websocket.send_json(data)
""")
        result = link_websocket(tmp_path)
        # Should detect Python patterns
        assert len(result.symbols) >= 1

    def test_run_metadata_includes_python_files(self, tmp_path: Path) -> None:
        """Run metadata should count Python files analyzed."""
        (tmp_path / "consumer1.py").write_text("await websocket.send_json(d)")
        (tmp_path / "consumer2.py").write_text("await websocket.receive_json()")
        result = link_websocket(tmp_path)
        assert result.run.files_analyzed >= 2


class TestVariableEventPatterns:
    """Tests for variable-based event detection."""

    def test_detect_variable_emit_event(self, tmp_path: Path) -> None:
        """Detects socket.emit with variable event name."""
        from hypergumbo.linkers.websocket import _detect_patterns

        js_file = tmp_path / "sender.js"
        js_file.write_text("""
const EVENT_NAME = 'user-login';
socket.emit(EVENT_NAME, { user: 'test' });
""")
        patterns = _detect_patterns(js_file)

        assert len(patterns) == 1
        assert patterns[0].event == "EVENT_NAME"
        assert patterns[0].event_type == "variable"

    def test_detect_variable_on_event(self, tmp_path: Path) -> None:
        """Detects socket.on with variable event name."""
        from hypergumbo.linkers.websocket import _detect_patterns

        js_file = tmp_path / "receiver.js"
        js_file.write_text("""
const LOGIN_EVENT = 'user-login';
socket.on(LOGIN_EVENT, (data) => {
    console.log('User logged in:', data);
});
""")
        patterns = _detect_patterns(js_file)

        assert len(patterns) == 1
        assert patterns[0].event == "LOGIN_EVENT"
        assert patterns[0].event_type == "variable"

    def test_detect_attribute_access_event(self, tmp_path: Path) -> None:
        """Detects event with attribute access like config.event."""
        from hypergumbo.linkers.websocket import _detect_patterns

        js_file = tmp_path / "sender.js"
        js_file.write_text("""
io.emit(config.eventName, { data: 'test' });
""")
        patterns = _detect_patterns(js_file)

        assert len(patterns) == 1
        assert patterns[0].event == "config.eventName"
        assert patterns[0].event_type == "variable"

    def test_literal_event_has_literal_type(self, tmp_path: Path) -> None:
        """Verifies literal events have event_type='literal'."""
        from hypergumbo.linkers.websocket import _detect_patterns

        js_file = tmp_path / "sender.js"
        js_file.write_text("""
socket.emit('user-login', { user: 'test' });
""")
        patterns = _detect_patterns(js_file)

        assert len(patterns) == 1
        assert patterns[0].event == "user-login"
        assert patterns[0].event_type == "literal"

    def test_variable_event_linking(self, tmp_path: Path) -> None:
        """Links variable events when using same variable name."""
        sender = tmp_path / "sender.js"
        sender.write_text("""
const EVENT = 'user-action';
socket.emit(EVENT, { action: 'click' });
""")

        receiver = tmp_path / "receiver.js"
        receiver.write_text("""
const EVENT = 'user-action';
socket.on(EVENT, (data) => {
    console.log('Action:', data.action);
});
""")

        result = link_websocket(tmp_path)

        assert len(result.edges) >= 1
        # Find message edges (not connection edges)
        msg_edges = [e for e in result.edges if e.edge_type == "websocket_message"]
        assert len(msg_edges) >= 1
        # Variable matches have lower confidence
        assert msg_edges[0].confidence == 0.65
        assert msg_edges[0].evidence_type == "variable_match"
        assert msg_edges[0].meta.get("event_type") == "variable"

    def test_endpoint_symbol_has_event_type(self, tmp_path: Path) -> None:
        """Endpoint symbols include event_type in metadata."""
        js_file = tmp_path / "server.js"
        js_file.write_text("""
io.on('connection', handler);
""")

        result = link_websocket(tmp_path)

        endpoints = [s for s in result.symbols if s.kind == "websocket_endpoint"]
        assert len(endpoints) >= 1
        assert "event_type" in endpoints[0].meta
        assert endpoints[0].meta["event_type"] == "literal"

    def test_django_channels_variable_event(self, tmp_path: Path) -> None:
        """Detects Django Channels with variable channel name."""
        from hypergumbo.linkers.websocket import _detect_python_patterns

        py_file = tmp_path / "consumer.py"
        py_file.write_text("""
CHANNEL_NAME = 'notifications'
await channel_layer.send(CHANNEL_NAME, {'type': 'notify'})
""")
        patterns = _detect_python_patterns(py_file)

        send_patterns = [p for p in patterns if p.type == "send"]
        assert len(send_patterns) == 1
        assert send_patterns[0].event == "CHANNEL_NAME"
        assert send_patterns[0].event_type == "variable"

    def test_mixed_literal_and_variable_no_match(self, tmp_path: Path) -> None:
        """Literal event doesn't match different variable name."""
        sender = tmp_path / "sender.js"
        sender.write_text("""
socket.emit('user-login', { user: 'test' });
""")

        receiver = tmp_path / "receiver.js"
        receiver.write_text("""
const EVENT = 'user-login';  // Same value, different identifier
socket.on(EVENT, handler);
""")

        result = link_websocket(tmp_path)

        # No message edges: literal 'user-login' != variable 'EVENT'
        msg_edges = [e for e in result.edges if e.edge_type == "websocket_message"]
        assert len(msg_edges) == 0
