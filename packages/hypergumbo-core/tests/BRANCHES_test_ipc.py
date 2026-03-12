# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for ipc.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.ipc import link_ipc


class TestLinkIpc:
    """Branch coverage for link_ipc function."""

    def test_multiple_sends_same_channel(self, tmp_path: Path) -> None:
        """Test multiple send patterns on the same channel (branch 281->283).

        When we have multiple send patterns using the same channel name,
        we should skip the dict initialization and just append.
        """
        # Create a main.js with sends and renderer.js with receive
        # so edges get created
        main_file = tmp_path / "main.js"
        main_file.write_text('''
const { ipcRenderer } = require('electron');

function sendData() {
    ipcRenderer.send('my-channel', { data: 'first' });
}

function sendMore() {
    ipcRenderer.send('my-channel', { data: 'second' });
}
''')

        renderer_file = tmp_path / "renderer.js"
        renderer_file.write_text('''
const { ipcMain } = require('electron');

ipcMain.on('my-channel', (event, data) => {
    console.log(data);
});
''')

        result = link_ipc(tmp_path)
        # Multiple sends + one receive creates multiple edges
        assert result is not None
        # Should have multiple symbols (one per send endpoint + one receive)
        send_symbols = [s for s in result.symbols if s.kind == "ipc_send"]
        assert len(send_symbols) == 2  # Two sends on same channel

    def test_multiple_receives_same_channel(self, tmp_path: Path) -> None:
        """Test multiple receive patterns on the same channel (branch 285->287).

        When we have multiple receive patterns using the same channel name,
        we should skip the dict initialization and just append.
        """
        # Create main.js with send and renderer.js with multiple receives
        main_file = tmp_path / "main.js"
        main_file.write_text('''
const { ipcRenderer } = require('electron');

ipcRenderer.send('shared-channel', { data: 'test' });
''')

        renderer_file = tmp_path / "renderer.js"
        renderer_file.write_text('''
const { ipcMain } = require('electron');

ipcMain.on('shared-channel', (event, data) => {
    console.log('First handler:', data);
});

ipcMain.on('shared-channel', (event, data) => {
    console.log('Second handler:', data);
});
''')

        result = link_ipc(tmp_path)
        # Multiple receives should be detected as symbols
        assert result is not None
        receive_symbols = [s for s in result.symbols if s.kind == "ipc_receive"]
        assert len(receive_symbols) == 2  # Two receives on same channel

    def test_mixed_multiple_channels(self, tmp_path: Path) -> None:
        """Test both send and receive with duplicate channels."""
        # Create both send and receive in same file
        js_file = tmp_path / "app.js"
        js_file.write_text('''
const { ipcRenderer, ipcMain } = require('electron');

// Multiple sends to same channel
ipcRenderer.send('data-channel', { type: 'request' });
ipcRenderer.send('data-channel', { type: 'update' });

// Multiple receives on same channel
ipcMain.on('data-channel', handleRequest);
ipcMain.on('data-channel', handleUpdate);
''')

        result = link_ipc(tmp_path)
        assert result is not None
        # Verify multiple symbols created
        send_symbols = [s for s in result.symbols if s.kind == "ipc_send"]
        receive_symbols = [s for s in result.symbols if s.kind == "ipc_receive"]
        assert len(send_symbols) == 2
        assert len(receive_symbols) == 2
        # Edges should be created linking sends to receives
        assert len(result.edges) >= 1
