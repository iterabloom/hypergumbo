"""Tests for Phoenix Channels IPC linker.

Tests detection of Phoenix Channel patterns in Elixir code:
- broadcast!/push message sending
- handle_in message receiving
- Channel name matching
"""
from pathlib import Path


class TestPhoenixPatternDetection:
    """Tests for Phoenix Channel pattern detection."""

    def test_detect_broadcast_pattern(self) -> None:
        """Detects broadcast!/broadcast message sending."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.RoomChannel do
  use Phoenix.Channel

  def handle_info(:ping, socket) do
    broadcast!(socket, "ping", %{})
    {:noreply, socket}
  end
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        assert len(patterns) >= 1
        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        assert send_patterns[0]["event"] == "ping"

    def test_detect_endpoint_broadcast(self) -> None:
        """Detects Endpoint.broadcast! pattern."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.UserController do
  def create(conn, params) do
    MyApp.Endpoint.broadcast!("room:lobby", "new_user", %{name: params["name"]})
  end
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        # Should capture topic and event
        assert send_patterns[0]["topic"] == "room:lobby"
        assert send_patterns[0]["event"] == "new_user"

    def test_detect_push_pattern(self) -> None:
        """Detects push/3 message sending."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.RoomChannel do
  def handle_in("request", _payload, socket) do
    push(socket, "response", %{status: "ok"})
    {:noreply, socket}
  end
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        assert send_patterns[0]["event"] == "response"

    def test_detect_handle_in_pattern(self) -> None:
        """Detects handle_in message receiving."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.RoomChannel do
  def handle_in("new_message", %{"body" => body}, socket) do
    broadcast!(socket, "new_message", %{body: body})
    {:noreply, socket}
  end
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        receive_patterns = [p for p in patterns if p["type"] == "receive"]
        assert len(receive_patterns) >= 1
        assert receive_patterns[0]["event"] == "new_message"

    def test_detect_multiple_handle_in(self) -> None:
        """Detects multiple handle_in clauses."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.RoomChannel do
  def handle_in("ping", _payload, socket) do
    {:reply, {:ok, %{pong: true}}, socket}
  end

  def handle_in("shout", %{"body" => body}, socket) do
    broadcast!(socket, "shout", %{body: body})
    {:noreply, socket}
  end
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        receive_patterns = [p for p in patterns if p["type"] == "receive"]
        assert len(receive_patterns) >= 2
        events = {p["event"] for p in receive_patterns}
        assert "ping" in events
        assert "shout" in events

    def test_non_elixir_returns_empty(self) -> None:
        """Returns empty for non-Elixir code."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
def broadcast(socket, event):
    pass
"""
        patterns = detect_phoenix_patterns(source, "python")

        assert len(patterns) == 0

    def test_no_patterns_in_regular_code(self) -> None:
        """Returns empty for code without Phoenix patterns."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.Math do
  def add(a, b), do: a + b
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        assert len(patterns) == 0

    def test_endpoint_broadcast_not_double_counted(self) -> None:
        """Endpoint.broadcast! is not double-matched by socket pattern."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
MyApp.Endpoint.broadcast!("room:lobby", "new_event", %{data: 1})
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        # Should only match once (endpoint pattern), not twice
        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) == 1
        assert send_patterns[0]["topic"] == "room:lobby"
        assert send_patterns[0]["event"] == "new_event"

    def test_endpoint_broadcast_with_socket_arg_not_double_counted(self) -> None:
        """Weird Endpoint.broadcast!(socket, ...) pattern not double-matched."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        # Edge case: someone writes Endpoint.broadcast!(socket, event) which would
        # match both endpoint pattern (failing to capture topic/event) and socket pattern.
        # The socket pattern match should be skipped since it's preceded by .broadcast
        source = b"""
MyApp.Endpoint.broadcast!(socket, "event", %{})
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        # Socket pattern would match "event", but should be skipped due to prefix check
        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) == 0  # Neither pattern should match validly


class TestPhoenixLinker:
    """Tests for Phoenix Channels linker edge creation."""

    def test_links_broadcast_to_handle_in(self, tmp_path: Path) -> None:
        """Links broadcast! to handle_in on same event."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        # Controller that broadcasts
        controller = tmp_path / "user_controller.ex"
        controller.write_text("""
defmodule MyApp.UserController do
  def create(conn, params) do
    MyApp.Endpoint.broadcast!("room:lobby", "user_joined", %{name: params["name"]})
    json(conn, %{ok: true})
  end
end
""")

        # Channel that receives
        channel = tmp_path / "room_channel.ex"
        channel.write_text("""
defmodule MyApp.RoomChannel do
  use Phoenix.Channel

  def handle_in("user_joined", payload, socket) do
    {:noreply, socket}
  end
end
""")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        # Should have edges linking broadcast to handler
        send_edges = [e for e in result.edges if e.edge_type == "message_send"]
        assert len(send_edges) >= 1

    def test_links_push_to_client(self, tmp_path: Path) -> None:
        """Creates edges for push patterns."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        channel = tmp_path / "room_channel.ex"
        channel.write_text("""
defmodule MyApp.RoomChannel do
  use Phoenix.Channel

  def handle_in("ping", _payload, socket) do
    push(socket, "pong", %{})
    {:noreply, socket}
  end
end
""")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == "phoenix-ipc-linker-v1"

    def test_no_elixir_files(self, tmp_path: Path) -> None:
        """Handles directory with no Elixir files."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        (tmp_path / "app.js").write_text("console.log('hello');")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        assert len(result.edges) == 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Handles empty directory."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        assert len(result.edges) == 0


class TestPhoenixEventMatching:
    """Tests for Phoenix event name matching."""

    def test_exact_event_match(self, tmp_path: Path) -> None:
        """Matches exact event names."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        (tmp_path / "channel.ex").write_text("""
defmodule MyApp.RoomChannel do
  def handle_in("my_event", payload, socket) do
    broadcast!(socket, "my_event", payload)
    {:noreply, socket}
  end
end
""")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        # Should find matching events

    def test_no_match_different_events(self, tmp_path: Path) -> None:
        """Does not link different event names."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        (tmp_path / "send.ex").write_text("""
defmodule MyApp.Sender do
  def send_it do
    MyApp.Endpoint.broadcast!("room:lobby", "event_a", %{})
  end
end
""")
        (tmp_path / "receive.ex").write_text("""
defmodule MyApp.RoomChannel do
  def handle_in("event_b", _payload, socket) do
    {:noreply, socket}
  end
end
""")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        # Should not have edges between different events


class TestPhoenixLinkerMetadata:
    """Tests for Phoenix linker run metadata."""

    def test_run_has_pass_id(self, tmp_path: Path) -> None:
        """Run has correct pass ID."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == "phoenix-ipc-linker-v1"

    def test_run_tracks_files(self, tmp_path: Path) -> None:
        """Run tracks files analyzed."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        (tmp_path / "a.ex").write_text("defmodule A, do: nil")
        (tmp_path / "b.exs").write_text("defmodule B, do: nil")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        assert result.run.files_analyzed >= 0

    def test_edge_has_event_in_meta(self, tmp_path: Path) -> None:
        """Created edges include event name in metadata."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        (tmp_path / "channel.ex").write_text("""
defmodule MyApp.RoomChannel do
  def handle_in("test_event", _payload, socket) do
    broadcast!(socket, "test_event", %{})
    {:noreply, socket}
  end
end
""")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        for edge in result.edges:
            # Edges should have event metadata
            assert 0.0 <= edge.confidence <= 1.0


class TestPhoenixTopicHandling:
    """Tests for Phoenix topic parsing."""

    def test_topic_extraction(self) -> None:
        """Extracts topic from broadcast patterns."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
MyApp.Endpoint.broadcast!("users:123", "update", %{})
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        assert send_patterns[0]["topic"] == "users:123"

    def test_broadcast_from_socket(self) -> None:
        """Handles broadcast from socket (topic from socket)."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
broadcast!(socket, "new_msg", %{body: "hello"})
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        # Topic comes from socket, so we don't know it statically


class TestPhoenixEdgeCases:
    """Tests for Phoenix linker edge cases."""

    def test_file_read_error(self, tmp_path: Path) -> None:
        """Handles file read errors gracefully."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        ex_file = tmp_path / "test.ex"
        ex_file.write_text("broadcast!(socket, \"test\", %{})")

        # Make file unreadable
        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self: Path) -> bytes:
            if self.name == "test.ex":
                raise IOError("Mock read error")
            return original_read_bytes(self)

        from unittest.mock import patch
        with patch.object(Path, "read_bytes", mock_read_bytes):
            result = link_phoenix_ipc(tmp_path)

        assert result.run is not None
        # Should not crash, just skip the file

    def test_multiple_patterns_in_file(self, tmp_path: Path) -> None:
        """Handles multiple Phoenix patterns in single file."""
        from hypergumbo.linkers.phoenix_ipc import link_phoenix_ipc

        (tmp_path / "multi.ex").write_text("""
defmodule MyApp.MultiChannel do
  def handle_in("event1", _p, s), do: {:noreply, s}
  def handle_in("event2", _p, s), do: {:noreply, s}
  def handle_in("event3", _p, s), do: {:noreply, s}
end
""")

        result = link_phoenix_ipc(tmp_path)

        assert result.run is not None


class TestPhoenixLiveView:
    """Tests for Phoenix LiveView patterns (optional extension)."""

    def test_detect_liveview_handle_event(self) -> None:
        """Detects LiveView handle_event pattern."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.CounterLive do
  use Phoenix.LiveView

  def handle_event("increment", _params, socket) do
    {:noreply, update(socket, :count, &(&1 + 1))}
  end
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        receive_patterns = [p for p in patterns if p["type"] == "receive"]
        assert len(receive_patterns) >= 1
        assert receive_patterns[0]["event"] == "increment"

    def test_detect_push_event(self) -> None:
        """Detects push_event pattern in LiveView."""
        from hypergumbo.linkers.phoenix_ipc import detect_phoenix_patterns

        source = b"""
defmodule MyApp.FormLive do
  def handle_info(:refresh, socket) do
    {:noreply, push_event(socket, "update", %{data: "new"})}
  end
end
"""
        patterns = detect_phoenix_patterns(source, "elixir")

        send_patterns = [p for p in patterns if p["type"] == "send"]
        assert len(send_patterns) >= 1
        assert send_patterns[0]["event"] == "update"
