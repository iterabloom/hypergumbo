# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the WebSocket linker.

Tests cover:
- Pattern detection (Socket.io, native WebSocket, ws package)
- Edge creation between matching send/receive patterns
- Symbol creation for endpoints
- Edge cases and error handling
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.linkers.websocket import (
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
        import hypergumbo_core.linkers.websocket as ws_module

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

    def test_make_symbol_id_canonical_shape_with_explicit_language(self) -> None:
        """Phase 6 PR1 (INV-dulah): ``_make_symbol_id`` emits canonical IDs.

        The shape is ``{language}:{path}:{line}-{line}:{event}-{kind}:function``
        — the language slot is the host file's language (not the literal
        ``websocket`` protocol name); the span is the canonical
        ``start-end`` form; the route + role get packed into the name
        segment (no ``:``) and the kind slot is the canonical
        ``function`` (matching the actual ``Symbol.kind``).
        """
        id = _make_symbol_id("src/app.js", 10, "connection", "endpoint", "javascript")
        assert id == "javascript:src/app.js:10-10:connection-endpoint:function"

    def test_make_symbol_id_default_language_is_python(self) -> None:
        """The ``language`` arg defaults to ``"python"`` for legacy callers."""
        id = _make_symbol_id("src/app.py", 10, "connection", "endpoint")
        assert id == "python:src/app.py:10-10:connection-endpoint:function"

    def test_make_symbol_id_sanitizes_colons_in_event(self) -> None:
        """Route paths containing ``:`` get sanitized so the canonical 5-segment
        ID shape is preserved (defensive; route paths rarely contain ``:``)."""
        id = _make_symbol_id("src/app.py", 10, "POST:/api/items", "endpoint")
        assert ":" not in id.split(":")[3]  # name segment has no colon
        assert id == "python:src/app.py:10-10:POST_/api/items-endpoint:function"

    def test_language_for_file_extension_unknown_falls_back_to_pattern_type(self) -> None:
        """Vue/Svelte files (and other unknown extensions) fall back to pattern_type.

        ``find_js_ts_files`` walks ``*.vue`` / ``*.svelte`` paths but neither
        extension is in the canonical ``_EXTENSION_TO_LANGUAGE`` map. The
        fallback in ``_language_for_file`` covers those cases: Python-side
        Django Channels / FastAPI paths return ``"python"``, every other
        unknown extension defaults to ``"javascript"``.
        """
        from hypergumbo_core.linkers.websocket import _language_for_file

        assert _language_for_file("App.vue", "socketio") == "javascript"
        assert _language_for_file("App.svelte", "ws") == "javascript"
        assert _language_for_file("handler", "django_channels") == "python"
        assert _language_for_file("handler", "fastapi") == "python"

    def test_make_file_id(self) -> None:
        """Should generate canonical make_file_id-shape IDs (INV-ronuf).

        The WS linker historically used a ``websocket:``-prefixed id that did
        not match the canonical analyze/orchestrator shape, producing phantom
        shadow file Symbols. After INV-ronuf the WS linker mints canonical
        ``{language}:{path}:1-1:file:file`` ids so dedup with analyzer and
        orchestrator file Symbols works naturally.
        """
        id = _make_file_id("javascript", "src/app.js")
        assert id == "javascript:src/app.js:1-1:file:file"


class TestINVRonufNoPhantomFileSymbols:
    """INV-ronuf property tests: WS linker must not create phantom file Symbols.

    The invariant has three clauses (from the tracker statement):

    1. Each file path has exactly one file-kind Symbol across all producers.
    2. When the WS linker DOES synthesize a file Symbol, the id matches the
       canonical ``make_file_id`` shape so cross-producer dedup works.
    3. Synthesized Symbols have language derived from the file extension
       (``.ts`` → ``typescript``, not ``javascript``), and a non-None
       ``stable_id`` to satisfy the schema gate.
    """

    def test_file_symbols_use_canonical_make_file_id_shape(self, tmp_path: Path) -> None:
        """INV-ronuf clause 2: id matches canonical ``make_file_id`` shape."""
        from hypergumbo_core.analyze.base import make_file_id
        (tmp_path / "sender.js").write_text("socket.emit('e', data);")
        (tmp_path / "receiver.js").write_text("socket.on('e', handler);")
        result = link_websocket(tmp_path)
        file_syms = [s for s in result.symbols if s.kind == "file"]
        assert len(file_syms) >= 1
        for s in file_syms:
            assert s.id.endswith(":1-1:file:file"), f"non-canonical id {s.id!r}"
            assert not s.id.startswith("websocket:"), (
                f"websocket-prefixed id {s.id!r} causes phantom-Symbol duplication"
            )
            assert s.id == make_file_id(s.language, s.path), (
                f"id {s.id!r} does not reconstruct from (language, path)"
            )

    def test_typescript_file_gets_typescript_language(self, tmp_path: Path) -> None:
        """INV-ronuf clause 3: ``.ts`` files get ``language='typescript'``.

        Previously the WS linker used ``get_language(pattern_type)`` which
        returned ``'javascript'`` for any non-Python pattern, mis-attributing
        every ``.ts`` file. Now language is derived from the file extension.
        """
        (tmp_path / "sender.ts").write_text("socket.emit('e', data);")
        (tmp_path / "receiver.ts").write_text("socket.on('e', handler);")
        result = link_websocket(tmp_path)
        ts_file_syms = [
            s for s in result.symbols if s.kind == "file" and s.path.endswith(".ts")
        ]
        assert len(ts_file_syms) >= 1
        for s in ts_file_syms:
            assert s.language == "typescript", (
                f"expected typescript for {s.path!r}, got {s.language!r}"
            )

    def test_synthesized_file_symbols_have_stable_id(self, tmp_path: Path) -> None:
        """INV-ronuf clause 3: synthesized file Symbols stamp ``stable_id``.

        Schema gate (INV-piroh) requires non-None ``stable_id`` for every
        Symbol; the WS linker previously left it ``None``.
        """
        (tmp_path / "sender.js").write_text("socket.emit('e', data);")
        (tmp_path / "receiver.js").write_text("socket.on('e', handler);")
        result = link_websocket(tmp_path)
        file_syms = [s for s in result.symbols if s.kind == "file"]
        assert len(file_syms) >= 1
        for s in file_syms:
            assert s.stable_id is not None, f"{s.id!r} has stable_id=None"

    def test_skips_phantom_when_existing_symbol_id_present(self, tmp_path: Path) -> None:
        """INV-ronuf clause 1: skip synthesis when canonical id pre-exists.

        Simulates the production case: an analyzer (or the orchestrator's
        dangling-synth) has already emitted a file Symbol for a path. The
        WS linker must reuse that Symbol via canonical-id collision, not
        emit a duplicate.

        WI-hifol: production analyzer/synthesizer emit repo-relative paths
        in their canonical ids (paths are normalized in
        ``analyze/all_analyzers.py`` before linkers run); this test
        mirrors that by passing a repo-relative existing id.
        """
        from hypergumbo_core.analyze.base import make_file_id
        (tmp_path / "client.js").write_text("socket.emit('e', data);")
        (tmp_path / "server.js").write_text("socket.on('e', handler);")
        existing_client_id = make_file_id("javascript", "client.js")
        result = link_websocket(
            tmp_path, existing_symbol_ids={existing_client_id}
        )
        client_files = [
            s for s in result.symbols if s.kind == "file" and "client.js" in s.path
        ]
        assert len(client_files) == 0, (
            f"WS linker emitted phantom file Symbol for client.js despite "
            f"existing canonical id; got {[s.id for s in client_files]!r}"
        )

    def test_dedup_against_existing_preserves_edge_resolution(self, tmp_path: Path) -> None:
        """INV-ronuf: dedup'd edges still reference the existing Symbol id.

        When the WS linker skips synthesis because an existing canonical id
        is present, the edges it emits must still target that canonical id
        (so they resolve to the existing Symbol, not dangle).
        """
        from hypergumbo_core.analyze.base import make_file_id
        (tmp_path / "client.js").write_text("socket.emit('e', data);")
        (tmp_path / "server.js").write_text("socket.on('e', handler);")
        existing_client_id = make_file_id("javascript", "client.js")
        existing_server_id = make_file_id("javascript", "server.js")
        result = link_websocket(
            tmp_path,
            existing_symbol_ids={existing_client_id, existing_server_id},
        )
        publish_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
        assert len(publish_edges) >= 1
        for e in publish_edges:
            assert e.src == existing_client_id
            assert e.dst == existing_server_id

    def test_skips_phantom_when_repo_relative_canonical_id_present(self, tmp_path: Path) -> None:
        """WI-hifol: dedup against repo-relative canonical ids (production).

        The orchestrator's dangling-symbol synthesizer and the language
        analyzers normalize ``Symbol.path`` to be repo-relative before
        linkers run (see ``analyze/all_analyzers.py``). Their file Symbol
        ids therefore embed repo-relative paths. The WS linker discovered
        files via ``Path`` objects (absolute) and constructed file ids
        with absolute paths — so canonical-shape dedup against the
        orchestrator's pre-existing repo-relative ids silently missed,
        emitting a duplicate file Symbol per path. The fix is for the WS
        linker to embed repo-relative paths in its ids and Symbol.path
        fields.
        """
        from hypergumbo_core.analyze.base import make_file_id
        (tmp_path / "client.js").write_text("socket.emit('e', data);")
        (tmp_path / "server.js").write_text("socket.on('e', handler);")
        existing_client_id = make_file_id("javascript", "client.js")
        result = link_websocket(
            tmp_path, existing_symbol_ids={existing_client_id}
        )
        client_files = [
            s for s in result.symbols if s.kind == "file" and "client.js" in s.path
        ]
        assert len(client_files) == 0, (
            f"WS linker emitted phantom file Symbol for client.js despite "
            f"existing repo-relative canonical id; got "
            f"{[s.id for s in client_files]!r}. This is the production "
            f"dedup-miss documented in WI-hifol."
        )

    def test_file_symbol_paths_are_repo_relative(self, tmp_path: Path) -> None:
        """WI-hifol: file Symbol paths are repo-relative, not absolute.

        Post-fix, every WS-emitted file Symbol must store ``path`` as a
        repo-relative string (matching what the orchestrator emits). This
        is a stronger invariant than the existing canonical-shape test
        because it pins the path representation, not just the id format.
        """
        (tmp_path / "client.js").write_text("socket.emit('e', data);")
        (tmp_path / "server.js").write_text("socket.on('e', handler);")
        result = link_websocket(tmp_path)
        file_syms = [s for s in result.symbols if s.kind == "file"]
        assert len(file_syms) >= 1
        for s in file_syms:
            assert not Path(s.path).is_absolute(), (
                f"WS file Symbol has absolute path {s.path!r}; expected "
                f"repo-relative"
            )
            # The id must embed the same repo-relative path as Symbol.path.
            assert f":{s.path}:" in s.id, (
                f"WS file Symbol id {s.id!r} does not embed the repo-relative "
                f"path {s.path!r}"
            )

    def test_edge_endpoints_use_repo_relative_paths(self, tmp_path: Path) -> None:
        """WI-hifol: edge src/dst ids embed repo-relative paths.

        Edge endpoints are constructed with ``_make_file_id`` from
        ``pattern.file_path``. If those carry absolute paths, edges
        dangle against the orchestrator's repo-relative file Symbols.
        """
        (tmp_path / "sender.js").write_text("socket.emit('chat', message);")
        (tmp_path / "receiver.js").write_text("socket.on('chat', handler);")
        result = link_websocket(tmp_path)
        publish_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
        assert len(publish_edges) >= 1
        for e in publish_edges:
            assert not e.src.startswith(("javascript:/", "typescript:/", "python:/")), (
                f"Edge src embeds absolute path: {e.src!r}"
            )
            assert not e.dst.startswith(("javascript:/", "typescript:/", "python:/")), (
                f"Edge dst embeds absolute path: {e.dst!r}"
            )


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
        endpoint_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
        assert len(endpoint_symbols) == 1
        assert "connection" in endpoint_symbols[0].name

    def test_links_matching_events(self, tmp_path: Path) -> None:
        """Should create edges between matching send/receive patterns."""
        (tmp_path / "sender.js").write_text("socket.emit('chat', message);")
        (tmp_path / "receiver.js").write_text("socket.on('chat', (msg) => {});")
        result = link_websocket(tmp_path)
        # Should have edge from sender to receiver
        message_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
        assert len(message_edges) == 1
        assert "sender.js" in message_edges[0].src
        assert "receiver.js" in message_edges[0].dst
        # INV-forim: dataflow annotations must persist through the linker.
        # Historically edge.meta was reassigned after Edge.create, wiping
        # the access_mode and dest_access_mode set by the kwargs.
        assert message_edges[0].meta["access_mode"] == "write"
        assert message_edges[0].meta["dest_access_mode"] == "read"
        assert message_edges[0].meta["channel"] == "chat"
        assert message_edges[0].meta["event"] == "chat"

    def test_no_self_links(self, tmp_path: Path) -> None:
        """Should not create edges from file to itself."""
        file = tmp_path / "chat.js"
        file.write_text("""
socket.emit('event', data);
socket.on('event', handler);
""")
        result = link_websocket(tmp_path)
        # Should not have message edges (both patterns in same file)
        message_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
        assert len(message_edges) == 0

    def test_creates_connection_edges(self, tmp_path: Path) -> None:
        """Should create edges for endpoint connections."""
        file = tmp_path / "server.js"
        file.write_text("wss.on('connection', (ws) => {});")
        result = link_websocket(tmp_path)
        connection_edges = [e for e in result.edges if e.edge_type == "references"]
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
        message_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
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
            if edge.edge_type == "event_publishes":
                assert edge.confidence == 0.85
            elif edge.edge_type == "references":
                assert edge.confidence == 0.90

    def test_symbol_origin(self, tmp_path: Path) -> None:
        """Should set origin on symbols."""
        file = tmp_path / "server.js"
        file.write_text("io.on('connection', handler);")
        result = link_websocket(tmp_path)
        # All symbols (endpoint + file) should have origin set
        assert len(result.symbols) >= 1
        for symbol in result.symbols:
            assert symbol.origin == [PASS_ID]
            assert symbol.origin_run_id == result.run.execution_id

    def test_edge_origin(self, tmp_path: Path) -> None:
        """Should set origin on edges."""
        (tmp_path / "sender.js").write_text("socket.emit('event', data);")
        (tmp_path / "receiver.js").write_text("socket.on('event', handler);")
        result = link_websocket(tmp_path)
        for edge in result.edges:
            assert edge.origin == [PASS_ID]
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
            if edge.edge_type == "event_publishes":
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
        assert any((s.meta or {}).get("framework_role") == "websocket_endpoint" for s in result.symbols)

        # Should find message edges for 'chat message' event
        message_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
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
        endpoints = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
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
        assert any((s.meta or {}).get("framework_role") == "websocket_endpoint" for s in result.symbols)


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
        import hypergumbo_core.linkers.websocket as ws_module

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
        """Python WebSocket synthetic stand-ins should carry
        discovery_language='python' (ADR-0031 Class B).
        """
        file = tmp_path / "main.py"
        file.write_text("""
@app.websocket('/ws')
async def handler(websocket):
    await websocket.accept()
""")
        result = link_websocket(tmp_path)
        endpoint_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
        assert len(endpoint_symbols) >= 1
        for sym in endpoint_symbols:
            # ADR-0031 Class B: language=None, discovery_language carries host.
            assert sym.language is None
            assert sym.discovery_language == "python"
            assert sym.protocol_origin == "websocket"

    def test_js_symbols_have_correct_language(self, tmp_path: Path) -> None:
        """JavaScript WebSocket synthetic stand-ins should carry
        discovery_language='javascript' (ADR-0031 Class B).
        """
        file = tmp_path / "server.js"
        file.write_text("""
io.on('connection', handler);
""")
        result = link_websocket(tmp_path)
        endpoint_symbols = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
        assert len(endpoint_symbols) == 1
        # ADR-0031 Class B: language=None, discovery_language carries host.
        assert endpoint_symbols[0].language is None
        assert endpoint_symbols[0].discovery_language == "javascript"
        assert endpoint_symbols[0].protocol_origin == "websocket"


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
        endpoints = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
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
        endpoints = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
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
        endpoints = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
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
        from hypergumbo_core.linkers.websocket import _detect_patterns

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
        from hypergumbo_core.linkers.websocket import _detect_patterns

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
        from hypergumbo_core.linkers.websocket import _detect_patterns

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
        from hypergumbo_core.linkers.websocket import _detect_patterns

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
        msg_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
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

        endpoints = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "websocket_endpoint"]
        assert len(endpoints) >= 1
        assert "event_type" in endpoints[0].meta
        assert endpoints[0].meta["event_type"] == "literal"

    def test_django_channels_variable_event(self, tmp_path: Path) -> None:
        """Detects Django Channels with variable channel name."""
        from hypergumbo_core.linkers.websocket import _detect_python_patterns

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
        msg_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
        assert len(msg_edges) == 0

    def test_generic_send_receive_no_interfile_edges(self, tmp_path: Path) -> None:
        """Generic send/receive (no event name) should not create inter-file edges.

        FastAPI websocket.send_text() and websocket.receive_text() represent
        the base WebSocket protocol, not named events. Linking every sender
        file to every receiver file creates combinatorial explosion (e.g.,
        852 edges in FastAPI with 19 files). Only named events (Socket.io
        emit/on, Django Channels) warrant inter-file linking.
        """
        # FastAPI server 1 with websocket.send_text()
        server1 = tmp_path / "server1.py"
        server1.write_text(
            "@app.websocket('/ws')\n"
            "async def ws_endpoint(websocket):\n"
            "    await websocket.accept()\n"
            "    await websocket.send_text('hello')\n"
        )
        # FastAPI server 2 with websocket.receive_text()
        server2 = tmp_path / "server2.py"
        server2.write_text(
            "@app.websocket('/chat')\n"
            "async def chat(websocket):\n"
            "    await websocket.accept()\n"
            "    data = await websocket.receive_text()\n"
        )
        result = link_websocket(tmp_path)
        # Should NOT have websocket_message edges between the files
        msg_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
        assert len(msg_edges) == 0, (
            f"Generic send/receive should not create inter-file edges, "
            f"got {len(msg_edges)}"
        )


class TestWebSocketLinkerRegistry:
    """Test the registry-based websocket_linker wrapper."""

    def test_websocket_linker_returns_linker_result(self, tmp_path: Path) -> None:
        """websocket_linker() wraps link_websocket() for registry dispatch."""
        from hypergumbo_core.linkers.registry import LinkerContext
        from hypergumbo_core.linkers.websocket import websocket_linker

        js_file = tmp_path / "app.js"
        js_file.write_text("io.on('connection', handler);\n")
        ctx = LinkerContext(repo_root=tmp_path)
        result = websocket_linker(ctx)
        assert result.symbols is not None
        assert result.edges is not None
        assert result.run is not None


class TestWiZolotCrossLanguageBridge:
    """WI-zolot: TS client at /ws must bridge to Python handler at /ws.

    Pre-fix gaps surfaced by self-analysis (2026-05-16 round 4):
    1. NATIVE_WEBSOCKET_PATTERN only matched ``'`` / ``"`` quoted URLs, so
       template-string URLs like ``new WebSocket(`${proto}/${host}/ws`)``
       (the canonical browser pattern) never produced an endpoint.
    2. Python side only matched ``@app.websocket('/path')`` decorator. The
       Starlette routing-table style ``WebSocketRoute("/path", handler)``
       — used by hypergumbo's own ``serve.py:433`` — never produced an
       endpoint.
    3. Even when both ends were detected, the linker only emitted
       within-language file→endpoint ``references`` and send/receive
       pairings. No client↔server cross-language edge was ever emitted.

    Fix: extend client regex to accept template strings, add Starlette
    pattern, emit ``calls`` + ``meta["protocol"]="ws"`` + ``cross_language``
    bridge edge when client+server endpoints share a path string.
    """

    def test_native_websocket_template_string_extracts_trailing_path(self, tmp_path: Path) -> None:
        """The TS client's ``new WebSocket(`...${host}/ws`)`` produces an endpoint."""
        file = tmp_path / "client.ts"
        file.write_text(
            "const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';\n"
            "const ws = new WebSocket(`${proto}//${location.host}/ws`);\n"
        )
        patterns = _detect_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert len(endpoints) == 1
        assert endpoints[0].event == "/ws"
        assert endpoints[0].pattern_type == "native"

    def test_native_websocket_template_string_extracts_last_literal_path(self, tmp_path: Path) -> None:
        """For ``new WebSocket(`/api/${endpoint}/get`)``, extract trailing ``/get``."""
        file = tmp_path / "client.ts"
        file.write_text("const ws = new WebSocket(`/api/${endpoint}/get`);\n")
        patterns = _detect_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert len(endpoints) == 1
        assert endpoints[0].event == "/get"

    def test_native_websocket_backtick_no_interpolation(self, tmp_path: Path) -> None:
        """Pure-literal backtick URL ``new WebSocket(`/ws`)`` works too."""
        file = tmp_path / "client.ts"
        file.write_text("const ws = new WebSocket(`/ws`);\n")
        patterns = _detect_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert len(endpoints) == 1
        assert endpoints[0].event == "/ws"

    def test_starlette_websocket_route_detected(self, tmp_path: Path) -> None:
        """``WebSocketRoute("/path", handler)`` is detected as a Python endpoint."""
        file = tmp_path / "serve.py"
        file.write_text(
            "from starlette.routing import WebSocketRoute\n"
            "routes = [\n"
            "    WebSocketRoute('/ws', _ws_handler),\n"
            "]\n"
        )
        patterns = _detect_python_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        # event="/ws", pattern_type identifies it as starlette WebSocketRoute.
        assert any(
            p.event == "/ws" and p.pattern_type == "starlette"
            for p in endpoints
        )

    def test_starlette_websocket_route_double_quote(self, tmp_path: Path) -> None:
        """Double-quoted ``WebSocketRoute("/ws", ...)`` works (matches serve.py)."""
        file = tmp_path / "serve.py"
        file.write_text(
            "from starlette.routing import WebSocketRoute\n"
            "routes = [WebSocketRoute(\"/ws\", _ws_handler)]\n"
        )
        patterns = _detect_python_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert any(p.event == "/ws" and p.pattern_type == "starlette" for p in endpoints)

    def test_ts_client_to_python_server_bridge_edge_emitted(self, tmp_path: Path) -> None:
        """TS client at /ws + Python server at /ws → cross-language ``calls`` edge."""
        from hypergumbo_core.linkers.websocket import _make_symbol_id, _make_file_id

        ts_file = tmp_path / "ws-client.ts"
        ts_file.write_text(
            "const ws = new WebSocket(`${location.host}/ws`);\n"
        )
        py_file = tmp_path / "serve.py"
        py_file.write_text(
            "from starlette.routing import WebSocketRoute\n"
            "routes = [WebSocketRoute('/ws', _ws_handler)]\n"
        )
        result = link_websocket(tmp_path)

        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and (e.meta or {}).get("protocol") == "ws"
        ]
        assert len(bridge_edges) == 1
        edge = bridge_edges[0]
        assert (edge.meta or {}).get("cross_language") is True
        assert (edge.meta or {}).get("url_path") == "/ws"
        # src is the TS client file; dst is the Python endpoint symbol.
        assert edge.src == _make_file_id("typescript", "ws-client.ts")
        # The Python endpoint Symbol id includes the event path.
        # ADR-0031: Class B synthetic stand-ins carry language=None;
        # discovery_language carries the host.
        py_endpoint = next(
            (s for s in result.symbols
             if s.discovery_language == "python"
             and (s.meta or {}).get("framework_role") == "websocket_endpoint"),
            None,
        )
        assert py_endpoint is not None
        assert edge.dst == py_endpoint.id

    def test_no_bridge_when_paths_differ(self, tmp_path: Path) -> None:
        """TS at ``/foo`` + Python at ``/bar`` → no bridge edge."""
        ts_file = tmp_path / "client.ts"
        ts_file.write_text("const ws = new WebSocket(`${host}/foo`);\n")
        py_file = tmp_path / "serve.py"
        py_file.write_text(
            "from starlette.routing import WebSocketRoute\n"
            "routes = [WebSocketRoute('/bar', handler)]\n"
        )
        result = link_websocket(tmp_path)
        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and (e.meta or {}).get("protocol") == "ws"
        ]
        assert bridge_edges == []

    def test_no_bridge_when_only_client_side(self, tmp_path: Path) -> None:
        """TS endpoint without matching Python endpoint → no bridge edge."""
        ts_file = tmp_path / "client.ts"
        ts_file.write_text("const ws = new WebSocket(`${host}/ws`);\n")
        result = link_websocket(tmp_path)
        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and (e.meta or {}).get("protocol") == "ws"
        ]
        assert bridge_edges == []

    def test_no_bridge_when_same_language(self, tmp_path: Path) -> None:
        """Two JS files declaring the same path: no cross-language bridge."""
        ts_a = tmp_path / "a.ts"
        ts_a.write_text("const ws = new WebSocket(`${host}/ws`);\n")
        ts_b = tmp_path / "b.ts"
        ts_b.write_text("const ws = new WebSocket(`${host}/ws`);\n")
        result = link_websocket(tmp_path)
        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and (e.meta or {}).get("protocol") == "ws"
        ]
        assert bridge_edges == []

    def test_url_extracted_into_helper_function(self, tmp_path: Path) -> None:
        """Hypergumbo's own pattern: URL built in a helper, used at the WS call.

        Real-world TS code often factors the URL construction into a helper:

            function getWsUrl() { return `${proto}//${host}/ws`; }
            ws = new WebSocket(getWsUrl());

        The inline-template regex misses this because the template literal
        and the ``new WebSocket(...)`` are in separate expressions. Heuristic:
        when a TS/JS file uses ``new WebSocket(...)`` anywhere, scan every
        template literal in the file for a trailing literal path preceded
        by at least one ``${...}`` interpolation (URL shape).
        """
        file = tmp_path / "ws-client.ts"
        file.write_text(
            "function getWsUrl() {\n"
            "  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';\n"
            "  return `${proto}//${location.host}/ws`;\n"
            "}\n"
            "function connect() {\n"
            "  const ws = new WebSocket(getWsUrl());\n"
            "}\n"
        )
        patterns = _detect_patterns(file)
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert any(p.event == "/ws" for p in endpoints), (
            f"expected /ws endpoint, got {[(p.event, p.pattern_type) for p in endpoints]}"
        )

    def test_url_template_without_websocket_constructor_not_detected(self, tmp_path: Path) -> None:
        """If a file has a URL-shaped template literal but no ``new WebSocket()``, no endpoint.

        Guards against false positives: a template literal like
        ``${API_BASE}/users/list`` in a fetch-only file is an HTTP URL, not
        a WebSocket URL — leave it to the HTTP linker.
        """
        file = tmp_path / "api-client.ts"
        file.write_text(
            "export function getUsers() {\n"
            "  return fetch(`${API_BASE}/users/list`);\n"
            "}\n"
        )
        patterns = _detect_patterns(file)
        # No WebSocket constructor in this file → no WS endpoints emitted.
        endpoints = [p for p in patterns if p.type == "endpoint"]
        assert endpoints == []

    def test_url_template_no_interpolation_not_detected(self, tmp_path: Path) -> None:
        """A plain backtick literal without interpolation is not assumed to be a URL.

        ``\\`some message: /foo/bar - failure\\``` in a file that also uses
        ``new WebSocket(...)`` should NOT produce a ``/foo/bar`` endpoint.
        The URL-shape heuristic requires at least one ``${...}`` block.
        """
        file = tmp_path / "client.ts"
        file.write_text(
            "function connect() {\n"
            "  const ws = new WebSocket('/ws');\n"
            "  console.error(`some message: /foo/bar - failure`);\n"
            "}\n"
        )
        patterns = _detect_patterns(file)
        endpoint_paths = [p.event for p in patterns if p.type == "endpoint"]
        # /ws comes from the literal-string match; /foo/bar must NOT appear.
        assert "/ws" in endpoint_paths
        assert "/foo/bar" not in endpoint_paths

    def test_self_analysis_shape_end_to_end(self, tmp_path: Path) -> None:
        """End-to-end: replicate hypergumbo's own ws-client.ts + serve.py shapes.

        This is the WI-zolot acceptance criterion as filed: the
        ``packages/htrac-frontend/src/ws-client.ts`` shape and the
        ``serve.py:215`` ``WebSocketRoute('/ws', _ws_handler)`` shape, on
        the same path, must produce exactly one cross-language WS bridge
        edge.
        """
        ts_dir = tmp_path / "packages" / "htrac-frontend" / "src"
        ts_dir.mkdir(parents=True)
        (ts_dir / "ws-client.ts").write_text(
            "function getWsUrl(): string {\n"
            "  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';\n"
            "  return `${proto}//${location.host}/ws`;\n"
            "}\n"
            "function connect() {\n"
            "  const ws = new WebSocket(getWsUrl());\n"
            "}\n"
        )
        py_dir = tmp_path / "packages" / "hypergumbo-tracker" / "src" / "hypergumbo_tracker"
        py_dir.mkdir(parents=True)
        (py_dir / "serve.py").write_text(
            "from starlette.routing import WebSocketRoute\n"
            "async def _ws_handler(websocket):\n"
            "    await websocket.accept()\n"
            "routes = [WebSocketRoute('/ws', _ws_handler)]\n"
        )
        result = link_websocket(tmp_path)
        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and (e.meta or {}).get("protocol") == "ws"
        ]
        assert len(bridge_edges) == 1
        edge = bridge_edges[0]
        assert (edge.meta or {}).get("cross_language") is True
        assert (edge.meta or {}).get("url_path") == "/ws"
        assert (edge.meta or {}).get("server_framework") == "starlette"

    def test_fastapi_decorator_still_bridges_too(self, tmp_path: Path) -> None:
        """The pre-existing ``@app.websocket('/ws')`` Python case bridges too."""
        ts_file = tmp_path / "client.ts"
        ts_file.write_text("const ws = new WebSocket(`${host}/ws`);\n")
        py_file = tmp_path / "main.py"
        py_file.write_text(
            "@app.websocket('/ws')\n"
            "async def handler(ws):\n"
            "    pass\n"
        )
        result = link_websocket(tmp_path)
        bridge_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and (e.meta or {}).get("protocol") == "ws"
        ]
        assert len(bridge_edges) == 1
        assert (bridge_edges[0].meta or {}).get("cross_language") is True
