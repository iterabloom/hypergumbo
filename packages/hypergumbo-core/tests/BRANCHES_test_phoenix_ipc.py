"""Branch coverage tests for phoenix_ipc.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.phoenix_ipc import link_phoenix_ipc


class TestLinkPhoenixIpc:
    """Branch coverage for link_phoenix_ipc function."""

    def test_multiple_sends_same_event(self, tmp_path: Path) -> None:
        """Test multiple send patterns on the same event (branch 256->258).

        When we have multiple send patterns using the same event name,
        we should skip the dict initialization and just append.
        """
        # Create Elixir file with multiple pushes on same event
        ex_file = tmp_path / "channel.ex"
        ex_file.write_text('''
defmodule MyApp.UserChannel do
  use Phoenix.Channel

  def handle_in("action", payload, socket) do
    push(socket, "response", %{status: "ok"})
    push(socket, "response", %{data: payload})
    {:noreply, socket}
  end
end
''')

        result = link_phoenix_ipc(tmp_path)
        assert result is not None
        # Both pushes to "response" should create symbols
        send_symbols = [s for s in result.symbols if s.kind == "ipc_send"]
        response_sends = [s for s in send_symbols if "response" in s.name.lower()]
        assert len(response_sends) == 2

    def test_multiple_receives_same_event(self, tmp_path: Path) -> None:
        """Test multiple receive patterns on the same event (branch 260->262).

        When we have multiple receive handlers for the same event,
        we should skip the dict initialization and just append.
        """
        # Create Elixir file with multiple handle_in for same event
        ex_file = tmp_path / "channel.ex"
        ex_file.write_text('''
defmodule MyApp.RoomChannel do
  use Phoenix.Channel

  def handle_in("shout", %{"body" => body}, socket) when is_binary(body) do
    broadcast(socket, "shout", %{body: body})
    {:noreply, socket}
  end

  def handle_in("shout", %{"body" => body, "user" => user}, socket) do
    broadcast(socket, "shout", %{body: body, user: user})
    {:noreply, socket}
  end
end
''')

        result = link_phoenix_ipc(tmp_path)
        assert result is not None
        # Multiple handle_in for "shout" should create symbols
        receive_symbols = [s for s in result.symbols if s.kind == "ipc_receive"]
        shout_receives = [s for s in receive_symbols if "shout" in s.name.lower()]
        assert len(shout_receives) == 2

    def test_mixed_send_receive_same_event(self, tmp_path: Path) -> None:
        """Test both send and receive with same event name."""
        ex_file = tmp_path / "channel.ex"
        ex_file.write_text('''
defmodule MyApp.ChatChannel do
  use Phoenix.Channel

  def handle_in("message", payload, socket) do
    push(socket, "message", payload)
    broadcast(socket, "message", payload)
    {:noreply, socket}
  end

  def handle_in("message", %{action: "delete"} = payload, socket) do
    {:noreply, socket}
  end
end
''')

        result = link_phoenix_ipc(tmp_path)
        assert result is not None
        # Should create edges linking sends to receives
        # (exact behavior depends on implementation)
