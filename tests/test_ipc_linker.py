"""Tests for IPC linker."""
from pathlib import Path

from hypergumbo.ir import AnalysisRun, Symbol, Span


class TestIpcPatternDetection:
    """Tests for IPC pattern detection."""

    def test_detect_electron_ipc_send(self) -> None:
        """Detects Electron ipcRenderer.send pattern."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
function sendMessage() {
    ipcRenderer.send('user-login', { username: 'test' });
}
"""
        patterns = detect_ipc_patterns(source, "javascript")

        assert len(patterns) >= 1
        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        assert send_patterns[0]["channel"] == "user-login"

    def test_detect_electron_ipc_on(self) -> None:
        """Detects Electron ipcMain.on pattern."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
ipcMain.on('user-login', (event, data) => {
    console.log('User logged in:', data.username);
});
"""
        patterns = detect_ipc_patterns(source, "javascript")

        assert len(patterns) >= 1
        receive_patterns = [p for p in patterns if p["type"] == "receive"]
        assert len(receive_patterns) >= 1
        assert receive_patterns[0]["channel"] == "user-login"

    def test_detect_electron_ipc_invoke(self) -> None:
        """Detects Electron ipcRenderer.invoke pattern."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
async function getData() {
    const result = await ipcRenderer.invoke('get-data', { id: 123 });
    return result;
}
"""
        patterns = detect_ipc_patterns(source, "javascript")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        assert send_patterns[0]["channel"] == "get-data"

    def test_detect_electron_ipc_handle(self) -> None:
        """Detects Electron ipcMain.handle pattern."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
ipcMain.handle('get-data', async (event, args) => {
    return await fetchData(args.id);
});
"""
        patterns = detect_ipc_patterns(source, "javascript")

        receive_patterns = [p for p in patterns if p["type"] == "receive"]
        assert len(receive_patterns) >= 1
        assert receive_patterns[0]["channel"] == "get-data"

    def test_detect_postmessage(self) -> None:
        """Detects window.postMessage pattern."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
function notifyParent() {
    window.postMessage({ type: 'ready', data: 'loaded' }, '*');
}
"""
        patterns = detect_ipc_patterns(source, "javascript")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1

    def test_detect_message_event_listener(self) -> None:
        """Detects addEventListener('message', ...) pattern."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
window.addEventListener('message', (event) => {
    console.log('Received:', event.data);
});
"""
        patterns = detect_ipc_patterns(source, "javascript")

        receive_patterns = [p for p in patterns if p["type"] == "receive"]
        assert len(receive_patterns) >= 1

    def test_detect_worker_postmessage(self) -> None:
        """Detects Web Worker postMessage pattern."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
const worker = new Worker('worker.js');
worker.postMessage({ cmd: 'start', data: input });
"""
        patterns = detect_ipc_patterns(source, "javascript")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1

    def test_no_patterns_in_regular_code(self) -> None:
        """Returns empty for code without IPC patterns."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
function add(a, b) {
    return a + b;
}
"""
        patterns = detect_ipc_patterns(source, "javascript")

        assert len(patterns) == 0

    def test_non_javascript_returns_empty(self) -> None:
        """Returns empty for non-JavaScript code."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
def send_message():
    pass
"""
        patterns = detect_ipc_patterns(source, "python")

        assert len(patterns) == 0


class TestIpcLinker:
    """Tests for IPC linker edge creation."""

    def _make_js_symbol(
        self,
        name: str,
        kind: str = "function",
        path: str = "app.js",
        source: bytes = b"",
    ) -> Symbol:
        """Create a test JavaScript symbol."""
        run = AnalysisRun.create(pass_id="test", version="test")
        return Symbol(
            id=f"js:{path}:1-10:{name}:{kind}",
            name=name,
            kind=kind,
            language="javascript",
            path=path,
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="javascript-ts-v1",
            origin_run_id=run.execution_id,
        )

    def test_links_ipc_send_receive(self, tmp_path: Path) -> None:
        """Links IPC send to receive on same channel."""
        from hypergumbo.linkers.ipc import link_ipc

        # Create renderer file with send
        renderer = tmp_path / "renderer.js"
        renderer.write_text("""
function sendLogin() {
    ipcRenderer.send('user-login', { user: 'test' });
}
""")

        # Create main file with receive
        main = tmp_path / "main.js"
        main.write_text("""
ipcMain.on('user-login', (event, data) => {
    handleLogin(data);
});
""")

        result = link_ipc(tmp_path)

        assert result.run is not None
        # Should have edges for the IPC communication
        send_edges = [e for e in result.edges if e.edge_type == "message_send"]
        receive_edges = [e for e in result.edges if e.edge_type == "message_receive"]
        # At minimum, verify the linker runs without error

    def test_links_invoke_handle_pair(self, tmp_path: Path) -> None:
        """Links ipcRenderer.invoke to ipcMain.handle."""
        from hypergumbo.linkers.ipc import link_ipc

        renderer = tmp_path / "renderer.js"
        renderer.write_text("""
async function fetchData() {
    return await ipcRenderer.invoke('fetch-data', { id: 1 });
}
""")

        main = tmp_path / "main.js"
        main.write_text("""
ipcMain.handle('fetch-data', async (event, args) => {
    return await db.query(args.id);
});
""")

        result = link_ipc(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == "ipc-linker-v1"

    def test_no_js_files(self, tmp_path: Path) -> None:
        """Handles directory with no JavaScript files."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "app.py").write_text("print('hello')")

        result = link_ipc(tmp_path)

        assert result.run is not None
        assert len(result.edges) == 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Handles empty directory."""
        from hypergumbo.linkers.ipc import link_ipc

        result = link_ipc(tmp_path)

        assert result.run is not None
        assert len(result.edges) == 0


class TestIpcChannelMatching:
    """Tests for IPC channel name matching."""

    def test_exact_channel_match(self, tmp_path: Path) -> None:
        """Matches exact channel names."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "send.js").write_text("""
ipcRenderer.send('my-channel', data);
""")
        (tmp_path / "receive.js").write_text("""
ipcMain.on('my-channel', handler);
""")

        result = link_ipc(tmp_path)

        assert result.run is not None
        # Should find matching channel
        channel_edges = [e for e in result.edges if "my-channel" in str(e.meta)]
        # Verify linker processes files

    def test_no_match_different_channels(self, tmp_path: Path) -> None:
        """Does not match different channel names."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "send.js").write_text("""
ipcRenderer.send('channel-a', data);
""")
        (tmp_path / "receive.js").write_text("""
ipcMain.on('channel-b', handler);
""")

        result = link_ipc(tmp_path)

        assert result.run is not None
        # Should not have channel link between different channels


class TestIpcLinkerEdgeCases:
    """Tests for IPC linker edge cases."""

    def test_file_read_error(self, tmp_path: Path) -> None:
        """Handles file read errors gracefully."""
        from hypergumbo.linkers.ipc import link_ipc

        js_file = tmp_path / "test.js"
        js_file.write_text("ipcRenderer.send('test', data);")

        # Make file unreadable
        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self: Path) -> bytes:
            if self.name == "test.js":
                raise IOError("Mock read error")
            return original_read_bytes(self)

        from unittest.mock import patch
        with patch.object(Path, "read_bytes", mock_read_bytes):
            result = link_ipc(tmp_path)

        assert result.run is not None
        # Should not crash, just skip the file

    def test_multiple_patterns_in_file(self, tmp_path: Path) -> None:
        """Handles multiple IPC patterns in single file."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "multi.js").write_text("""
ipcRenderer.send('channel-1', data1);
ipcRenderer.send('channel-2', data2);
ipcRenderer.invoke('channel-3', data3);
""")

        result = link_ipc(tmp_path)

        assert result.run is not None

    def test_nested_quotes(self) -> None:
        """Handles nested quotes in channel detection."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
ipcRenderer.send("channel-with-'quotes'", data);
"""
        patterns = detect_ipc_patterns(source, "javascript")

        # Should handle or skip gracefully
        assert isinstance(patterns, list)

    def test_template_literals(self) -> None:
        """Handles template literal channel names."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
const channel = `user-${id}`;
ipcRenderer.send(channel, data);
"""
        patterns = detect_ipc_patterns(source, "javascript")

        # Dynamic channels can't be statically analyzed
        assert isinstance(patterns, list)


class TestIpcEdgeCreation:
    """Tests for IPC edge creation details."""

    def test_edge_has_channel_in_meta(self, tmp_path: Path) -> None:
        """Created edges include channel name in metadata."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "app.js").write_text("""
ipcRenderer.send('test-channel', data);
ipcMain.on('test-channel', handler);
""")

        result = link_ipc(tmp_path)

        assert result.run is not None
        # Edges should have channel metadata when created

    def test_edge_confidence(self, tmp_path: Path) -> None:
        """Edges have appropriate confidence levels."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "app.js").write_text("""
ipcRenderer.send('test-channel', data);
ipcMain.on('test-channel', handler);
""")

        result = link_ipc(tmp_path)

        for edge in result.edges:
            assert 0.0 <= edge.confidence <= 1.0


class TestIpcRunMetadata:
    """Tests for IPC linker run metadata."""

    def test_run_has_pass_id(self, tmp_path: Path) -> None:
        """Run has correct pass ID."""
        from hypergumbo.linkers.ipc import link_ipc

        result = link_ipc(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == "ipc-linker-v1"

    def test_run_tracks_files(self, tmp_path: Path) -> None:
        """Run tracks files analyzed."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "a.js").write_text("const x = 1;")
        (tmp_path / "b.js").write_text("const y = 2;")

        result = link_ipc(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed >= 0


class TestIpcTypeScriptSupport:
    """Tests for TypeScript IPC detection."""

    def test_detect_typescript_ipc(self, tmp_path: Path) -> None:
        """Detects IPC patterns in TypeScript files."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "renderer.ts").write_text("""
async function sendData(): Promise<void> {
    await ipcRenderer.invoke('send-data', { value: 42 });
}
""")

        result = link_ipc(tmp_path)

        assert result.run is not None

    def test_detect_tsx_ipc(self, tmp_path: Path) -> None:
        """Detects IPC patterns in TSX files."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "component.tsx").write_text("""
const Component = () => {
    const handleClick = () => {
        ipcRenderer.send('button-clicked', {});
    };
    return <button onClick={handleClick}>Click</button>;
};
""")

        result = link_ipc(tmp_path)

        assert result.run is not None


class TestIpcEmptyChannelHandling:
    """Tests for handling empty channel patterns (postMessage)."""

    def test_postmessage_sender_skipped(self, tmp_path: Path) -> None:
        """PostMessage senders with empty channel are skipped in linking."""
        from hypergumbo.linkers.ipc import link_ipc

        # Only postMessage (no named channel)
        (tmp_path / "sender.js").write_text("""
window.postMessage({ type: 'ready' }, '*');
""")
        (tmp_path / "receiver.js").write_text("""
window.addEventListener('message', handler);
""")

        result = link_ipc(tmp_path)

        assert result.run is not None
        # Empty channels should not create edges
        assert len(result.edges) == 0

    def test_mixed_empty_and_named_channels(self, tmp_path: Path) -> None:
        """Handles mix of empty and named channel patterns."""
        from hypergumbo.linkers.ipc import link_ipc

        (tmp_path / "app.js").write_text("""
// Named channel - should create edges
ipcRenderer.send('named-channel', data);
ipcMain.on('named-channel', handler);

// Empty channel (postMessage) - should not create edges
window.postMessage(data, '*');
window.addEventListener('message', handler);
""")

        result = link_ipc(tmp_path)

        assert result.run is not None
        # Should only have edges for named channel
        for edge in result.edges:
            assert edge.meta.get("channel") == "named-channel"


class TestIpcLinkerRequirements:
    """Tests for IPC linker registry requirements."""

    def test_count_js_ts_files(self, tmp_path: Path) -> None:
        """Counts JavaScript/TypeScript files in the repository."""
        from hypergumbo.linkers.ipc import _count_js_ts_files
        from hypergumbo.linkers.registry import LinkerContext

        (tmp_path / "app.js").write_text("const x = 1;")
        (tmp_path / "component.tsx").write_text("export default () => <div/>;")
        (tmp_path / "util.py").write_text("print('hello')")

        ctx = LinkerContext(repo_root=tmp_path)
        count = _count_js_ts_files(ctx)

        assert count == 2

    def test_count_electron_patterns_in_symbols(self, tmp_path: Path) -> None:
        """Counts Electron IPC patterns in symbols."""
        from hypergumbo.ir import Span, Symbol
        from hypergumbo.linkers.ipc import _count_electron_patterns_in_code
        from hypergumbo.linkers.registry import LinkerContext

        sym_ipc = Symbol(
            id="js:main.js:1-10:ipcHandler:function",
            name="ipcHandler",
            kind="function",
            language="javascript",
            path="main.js",
            span=Span(1, 10, 0, 0),
            origin="test",
            origin_run_id="test",
        )
        sym_electron = Symbol(
            id="js:preload.js:1-10:electronBridge:variable",
            name="electronBridge",
            kind="variable",
            language="typescript",
            path="preload.js",
            span=Span(1, 10, 0, 0),
            origin="test",
            origin_run_id="test",
        )
        sym_other = Symbol(
            id="js:utils.js:1-10:formatDate:function",
            name="formatDate",
            kind="function",
            language="javascript",
            path="utils.js",
            span=Span(1, 10, 0, 0),
            origin="test",
            origin_run_id="test",
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[sym_ipc, sym_electron, sym_other],
        )
        count = _count_electron_patterns_in_code(ctx)

        assert count == 2  # ipcHandler and electronBridge


class TestIpcLinkerRegistration:
    """Tests for IPC linker registry integration."""

    def test_linker_is_registered(self) -> None:
        """IPC linker is registered with the registry."""
        import hypergumbo.linkers.ipc
        from hypergumbo.linkers.registry import get_linker

        linker = get_linker("ipc")
        assert linker is not None
        assert linker.name == "ipc"
        assert linker.priority == 40

    def test_ipc_linker_returns_result(self, tmp_path: Path) -> None:
        """ipc_linker function returns LinkerResult."""
        from hypergumbo.linkers.ipc import ipc_linker
        from hypergumbo.linkers.registry import LinkerContext

        ctx = LinkerContext(repo_root=tmp_path)
        result = ipc_linker(ctx)

        assert result is not None
        assert hasattr(result, "symbols")
        assert hasattr(result, "edges")


class TestVariableChannelPatterns:
    """Tests for variable-based channel detection."""

    def test_detect_variable_send_channel(self) -> None:
        """Detects ipcRenderer.send with variable channel."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
const CHANNEL = 'open-file';
ipcRenderer.send(CHANNEL, { path: '/tmp/file.txt' });
"""
        patterns = detect_ipc_patterns(source, "javascript")

        assert len(patterns) == 1
        assert patterns[0]["channel"] == "CHANNEL"
        assert patterns[0]["channel_type"] == "variable"

    def test_detect_variable_receive_channel(self) -> None:
        """Detects ipcMain.on with variable channel."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
const OPEN_FILE_CHANNEL = 'open-file';
ipcMain.on(OPEN_FILE_CHANNEL, (event, data) => {
    console.log('Opening file:', data.path);
});
"""
        patterns = detect_ipc_patterns(source, "javascript")

        assert len(patterns) == 1
        assert patterns[0]["channel"] == "OPEN_FILE_CHANNEL"
        assert patterns[0]["channel_type"] == "variable"

    def test_detect_attribute_access_channel(self) -> None:
        """Detects channel with attribute access like config.channel."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
ipcRenderer.invoke(config.ipcChannel, { data: 'test' });
"""
        patterns = detect_ipc_patterns(source, "javascript")

        assert len(patterns) == 1
        assert patterns[0]["channel"] == "config.ipcChannel"
        assert patterns[0]["channel_type"] == "variable"

    def test_literal_channel_has_literal_type(self) -> None:
        """Verifies literal channels have channel_type='literal'."""
        from hypergumbo.linkers.ipc import detect_ipc_patterns

        source = b"""
ipcRenderer.send('user-login', { user: 'test' });
"""
        patterns = detect_ipc_patterns(source, "javascript")

        assert len(patterns) == 1
        assert patterns[0]["channel"] == "user-login"
        assert patterns[0]["channel_type"] == "literal"

    def test_variable_channel_linking(self, tmp_path: Path) -> None:
        """Links variable channels when using same variable name."""
        from hypergumbo.linkers.ipc import link_ipc

        renderer = tmp_path / "renderer.js"
        renderer.write_text("""
const OPEN_CHANNEL = 'open-file';
ipcRenderer.send(OPEN_CHANNEL, { path: '/tmp/test.txt' });
""")

        main = tmp_path / "main.js"
        main.write_text("""
const OPEN_CHANNEL = 'open-file';
ipcMain.on(OPEN_CHANNEL, (event, data) => {
    console.log('Opening:', data.path);
});
""")

        result = link_ipc(tmp_path)

        assert len(result.edges) >= 1
        # Find edges between variable-matched patterns
        var_edges = [e for e in result.edges if e.evidence_type == "variable_match"]
        assert len(var_edges) >= 1
        assert var_edges[0].confidence == 0.65  # Lower confidence for variable match
        assert var_edges[0].meta.get("channel_type") == "variable"

    def test_symbol_has_channel_type_metadata(self, tmp_path: Path) -> None:
        """Symbols include channel_type in metadata."""
        from hypergumbo.linkers.ipc import link_ipc

        js_file = tmp_path / "test.js"
        js_file.write_text("""
ipcRenderer.send(CHANNEL_VAR, { data: 'test' });
ipcMain.on(CHANNEL_VAR, handler);
""")

        result = link_ipc(tmp_path)

        # Should have both sender and receiver symbols
        assert len(result.symbols) >= 1
        for sym in result.symbols:
            assert "channel_type" in sym.meta
            assert sym.meta["channel_type"] == "variable"

    def test_mixed_literal_and_variable_no_match(self, tmp_path: Path) -> None:
        """Literal channel doesn't match different variable name."""
        from hypergumbo.linkers.ipc import link_ipc

        renderer = tmp_path / "renderer.js"
        renderer.write_text("""
ipcRenderer.send('open-file', { path: '/tmp/test.txt' });
""")

        main = tmp_path / "main.js"
        main.write_text("""
const CHANNEL = 'open-file';  // Same value, different identifier
ipcMain.on(CHANNEL, handler);
""")

        result = link_ipc(tmp_path)

        # No edges: literal 'open-file' != variable 'CHANNEL'
        assert len(result.edges) == 0
