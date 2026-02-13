"""Tests for OTP GenServer dispatch linker.

Tests linking of GenServer.call/cast sites to handle_call/handle_cast/handle_info
handler functions in Elixir code. OTP is Elixir's concurrency framework, and
GenServer is the most common pattern for stateful processes. The linker bridges
the gap between call sites (GenServer.call/cast) and handler functions
(handle_call/handle_cast) that the Elixir analyzer can't connect because
dispatch happens at runtime through the OTP runtime.

Matching strategies tested:
- Same-module: variable target or __MODULE__ → handlers in same module
- Cross-module: explicit module name → handlers in target module
"""
from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext


class TestOTPCallSiteDetection:
    """Tests for detecting GenServer.call/cast patterns in source code."""

    def test_detect_genserver_call_variable_target(self) -> None:
        """Detects GenServer.call(variable, message) pattern."""
        from hypergumbo_core.linkers.otp import detect_otp_call_sites

        source = b"""
defmodule MyApp.Client do
  def get_data(server) do
    GenServer.call(server, :get_data)
  end
end
"""
        sites = detect_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["call_type"] == "call"
        assert sites[0]["target"] == "server"
        assert sites[0]["is_module"] is False

    def test_detect_genserver_cast_variable_target(self) -> None:
        """Detects GenServer.cast(variable, message) pattern."""
        from hypergumbo_core.linkers.otp import detect_otp_call_sites

        source = b"""
defmodule MyApp.Worker do
  def update(server, data) do
    GenServer.cast(server, {:update, data})
  end
end
"""
        sites = detect_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["call_type"] == "cast"
        assert sites[0]["target"] == "server"
        assert sites[0]["is_module"] is False

    def test_detect_explicit_module_target(self) -> None:
        """Detects GenServer.call with explicit module name target."""
        from hypergumbo_core.linkers.otp import detect_otp_call_sites

        source = b"""
defmodule MyApp.Consumer do
  def fetch(id) do
    GenServer.call(MyApp.DataServer, {:fetch, id})
  end
end
"""
        sites = detect_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["target"] == "MyApp.DataServer"
        assert sites[0]["is_module"] is True

    def test_detect_dunder_module_target(self) -> None:
        """Detects GenServer.call(__MODULE__, ...) pattern."""
        from hypergumbo_core.linkers.otp import detect_otp_call_sites

        source = b"""
defmodule MyApp.Cache do
  def get(key) do
    GenServer.call(__MODULE__, {:get, key})
  end
end
"""
        sites = detect_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["target"] == "__MODULE__"

    def test_detect_multiple_call_sites(self) -> None:
        """Detects multiple GenServer.call and cast sites in same file."""
        from hypergumbo_core.linkers.otp import detect_otp_call_sites

        source = b"""
defmodule MyApp.Multi do
  def get(s), do: GenServer.call(s, :get)
  def set(s, v), do: GenServer.cast(s, {:set, v})
  def ping(s), do: GenServer.call(s, :ping)
end
"""
        sites = detect_otp_call_sites(source)
        calls = [s for s in sites if s["call_type"] == "call"]
        casts = [s for s in sites if s["call_type"] == "cast"]
        assert len(calls) == 2
        assert len(casts) == 1

    def test_no_genserver_patterns(self) -> None:
        """Returns empty for code without GenServer patterns."""
        from hypergumbo_core.linkers.otp import detect_otp_call_sites

        source = b"""
defmodule MyApp.Math do
  def add(a, b), do: a + b
end
"""
        sites = detect_otp_call_sites(source)
        assert len(sites) == 0

    def test_line_number_tracking(self) -> None:
        """Tracks line numbers for call sites."""
        from hypergumbo_core.linkers.otp import detect_otp_call_sites

        source = b"""line1
line2
line3
GenServer.call(server, :msg)
line5
"""
        sites = detect_otp_call_sites(source)
        assert len(sites) == 1
        assert sites[0]["line"] == 4


class TestOTPLinkerSameModule:
    """Tests for same-module GenServer dispatch linking.

    The most common OTP pattern: a module defines both a client API
    (using GenServer.call/cast with a variable or __MODULE__) and
    server callbacks (handle_call/handle_cast) in the same defmodule.
    """

    def test_links_call_to_handle_call_same_module(self, tmp_path: Path) -> None:
        """Links GenServer.call(variable) to handle_call in same module."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "my_server.ex"
        server_file.parent.mkdir(parents=True)
        # Line numbers:
        # 1: (blank)
        # 2: defmodule MyApp.MyServer do
        # 3:   use GenServer
        # 4: (blank)
        # 5:   def get_data(server) do
        # 6:     GenServer.call(server, :get_data)
        # 7:   end
        # 8: (blank)
        # 9:   def handle_call(:get_data, _from, state) do
        # 10:    {:reply, state.data, state}
        # 11:  end
        # 12: end
        server_file.write_text("""
defmodule MyApp.MyServer do
  use GenServer

  def get_data(server) do
    GenServer.call(server, :get_data)
  end

  def handle_call(:get_data, _from, state) do
    {:reply, state.data, state}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.MyServer.get_data:function",
            name="MyApp.MyServer.get_data",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:9-11:MyApp.MyServer.handle_call:function",
            name="MyApp.MyServer.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=9, end_line=11, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_call"

    def test_links_cast_to_handle_cast_same_module(self, tmp_path: Path) -> None:
        """Links GenServer.cast(variable) to handle_cast in same module."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "worker.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
defmodule MyApp.Worker do
  use GenServer

  def update(server, data) do
    GenServer.cast(server, {:update, data})
  end

  def handle_cast({:update, data}, state) do
    {:noreply, %{state | data: data}}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.Worker.update:function",
            name="MyApp.Worker.update",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:9-11:MyApp.Worker.handle_cast:function",
            name="MyApp.Worker.handle_cast",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=9, end_line=11, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_cast"

    def test_links_dunder_module_to_same_module(self, tmp_path: Path) -> None:
        """Links GenServer.call(__MODULE__, ...) to same module's handle_call."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "cache.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
defmodule MyApp.Cache do
  use GenServer

  def get(key) do
    GenServer.call(__MODULE__, {:get, key})
  end

  def handle_call({:get, _key}, _from, state) do
    {:reply, state, state}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.Cache.get:function",
            name="MyApp.Cache.get",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:9-11:MyApp.Cache.handle_call:function",
            name="MyApp.Cache.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=9, end_line=11, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        # __MODULE__ is high confidence since it's explicit self-reference
        assert edge.confidence == 0.90

    def test_links_call_and_cast_in_same_module(self, tmp_path: Path) -> None:
        """Links both call and cast to respective handlers in same module."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "multi.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
defmodule MyApp.Multi do
  use GenServer

  def get(server) do
    GenServer.call(server, :get)
  end

  def update(server, v) do
    GenServer.cast(server, {:update, v})
  end

  def handle_call(:get, _from, state) do
    {:reply, state, state}
  end

  def handle_cast({:update, v}, state) do
    {:noreply, v}
  end
end
""")

        get_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.Multi.get:function",
            name="MyApp.Multi.get",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )
        update_sym = Symbol(
            id=f"elixir:{server_file}:9-11:MyApp.Multi.update:function",
            name="MyApp.Multi.update",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=9, end_line=11, start_col=0, end_col=0),
        )
        handle_call_sym = Symbol(
            id=f"elixir:{server_file}:13-15:MyApp.Multi.handle_call:function",
            name="MyApp.Multi.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=13, end_line=15, start_col=0, end_col=0),
        )
        handle_cast_sym = Symbol(
            id=f"elixir:{server_file}:17-19:MyApp.Multi.handle_cast:function",
            name="MyApp.Multi.handle_cast",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=17, end_line=19, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[get_sym, update_sym, handle_call_sym, handle_cast_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        call_edges = [e for e in result.edges if e.edge_type == "otp_call"]
        cast_edges = [e for e in result.edges if e.edge_type == "otp_cast"]
        assert len(call_edges) >= 1
        assert len(cast_edges) >= 1
        # call edge: get → handle_call
        assert call_edges[0].src == get_sym.id
        assert call_edges[0].dst == handle_call_sym.id
        # cast edge: update → handle_cast
        assert cast_edges[0].src == update_sym.id
        assert cast_edges[0].dst == handle_cast_sym.id


class TestOTPLinkerCrossModule:
    """Tests for cross-module GenServer dispatch linking.

    When GenServer.call(ExplicitModule, ...) names a module, the linker
    resolves the target module and links to its handler functions.
    """

    def test_links_explicit_module_call(self, tmp_path: Path) -> None:
        """Links GenServer.call(ModuleName, ...) to ModuleName.handle_call."""
        from hypergumbo_core.linkers.otp import otp_linker

        lib = tmp_path / "lib"
        lib.mkdir()

        client_file = lib / "client.ex"
        client_file.write_text("""
defmodule MyApp.Client do
  def fetch(id) do
    GenServer.call(MyApp.DataServer, {:fetch, id})
  end
end
""")

        server_file = lib / "data_server.ex"
        server_file.write_text("""
defmodule MyApp.DataServer do
  use GenServer

  def handle_call({:fetch, id}, _from, state) do
    {:reply, Map.get(state, id), state}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{client_file}:3-5:MyApp.Client.fetch:function",
            name="MyApp.Client.fetch",
            kind="function",
            language="elixir",
            path=str(client_file),
            span=Span(start_line=3, end_line=5, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.DataServer.handle_call:function",
            name="MyApp.DataServer.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_call"
        # Cross-module has lower confidence than same-module
        assert edge.confidence == 0.80

    def test_links_explicit_module_cast(self, tmp_path: Path) -> None:
        """Links GenServer.cast(ModuleName, ...) to ModuleName.handle_cast."""
        from hypergumbo_core.linkers.otp import otp_linker

        lib = tmp_path / "lib"
        lib.mkdir()

        client_file = lib / "sender.ex"
        client_file.write_text("""
defmodule MyApp.Sender do
  def notify(data) do
    GenServer.cast(MyApp.Receiver, {:notify, data})
  end
end
""")

        server_file = lib / "receiver.ex"
        server_file.write_text("""
defmodule MyApp.Receiver do
  use GenServer

  def handle_cast({:notify, data}, state) do
    {:noreply, [data | state]}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{client_file}:3-5:MyApp.Sender.notify:function",
            name="MyApp.Sender.notify",
            kind="function",
            language="elixir",
            path=str(client_file),
            span=Span(start_line=3, end_line=5, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.Receiver.handle_cast:function",
            name="MyApp.Receiver.handle_cast",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_cast"
        assert edge.confidence == 0.80


class TestOTPLinkerEdgeCases:
    """Tests for edge cases and error handling."""

    def test_no_elixir_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty result when no Elixir symbols detected."""
        from hypergumbo_core.linkers.otp import otp_linker

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"python", "javascript"},
        )
        result = otp_linker(ctx)
        assert len(result.edges) == 0
        assert len(result.symbols) == 0

    def test_handlers_without_callers(self, tmp_path: Path) -> None:
        """Handler symbols exist but no GenServer.call/cast sites in source."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "server.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
defmodule MyApp.Server do
  use GenServer

  def handle_call(:ping, _from, state) do
    {:reply, :pong, state}
  end
end
""")

        handler_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.Server.handle_call:function",
            name="MyApp.Server.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)
        assert len(result.edges) == 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Handles empty directory gracefully."""
        from hypergumbo_core.linkers.otp import otp_linker

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)
        assert result.run is not None
        assert len(result.edges) == 0

    def test_file_read_error(self, tmp_path: Path) -> None:
        """Handles file read errors without crashing."""
        from unittest.mock import patch

        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "broken.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("GenServer.call(server, :ping)")

        handler_sym = Symbol(
            id=f"elixir:{server_file}:1-3:MyApp.Broken.handle_call:function",
            name="MyApp.Broken.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
        )

        original_read_bytes = Path.read_bytes

        def mock_read_bytes(self_: Path) -> bytes:
            if self_.suffix in (".ex", ".exs"):
                raise IOError("Mock read error")
            return original_read_bytes(self_)

        with patch.object(Path, "read_bytes", mock_read_bytes):
            ctx = LinkerContext(
                repo_root=tmp_path,
                symbols=[handler_sym],
                detected_languages={"elixir"},
            )
            result = otp_linker(ctx)

        assert result.run is not None
        # Should not crash, just skip the file

    def test_call_outside_any_function(self, tmp_path: Path) -> None:
        """GenServer.call at module level (no enclosing function) is skipped."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "toplevel.ex"
        server_file.parent.mkdir(parents=True)
        # GenServer.call at line 2, but no function symbol encloses it
        server_file.write_text("""
GenServer.call(MyApp.Server, :init)
defmodule MyApp.TopLevel do
end
""")

        handler_sym = Symbol(
            id="elixir:other:1-3:MyApp.Server.handle_call:function",
            name="MyApp.Server.handle_call",
            kind="function",
            language="elixir",
            path="other.ex",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)
        # No enclosing function found → no edge created
        assert len(result.edges) == 0

    def test_multiple_handler_clauses(self, tmp_path: Path) -> None:
        """Links to all handle_call symbols when module has multiple clauses."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "multi_clause.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
defmodule MyApp.MultiClause do
  use GenServer

  def api(server, msg) do
    GenServer.call(server, msg)
  end

  def handle_call(:ping, _from, state) do
    {:reply, :pong, state}
  end

  def handle_call(:status, _from, state) do
    {:reply, state, state}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.MultiClause.api:function",
            name="MyApp.MultiClause.api",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )
        handler1 = Symbol(
            id=f"elixir:{server_file}:9-11:MyApp.MultiClause.handle_call:function",
            name="MyApp.MultiClause.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=9, end_line=11, start_col=0, end_col=0),
        )
        handler2 = Symbol(
            id=f"elixir:{server_file}:13-15:MyApp.MultiClause.handle_call:function",
            name="MyApp.MultiClause.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=13, end_line=15, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler1, handler2],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        # Should link to both handler clauses
        assert len(result.edges) >= 2
        dst_ids = {e.dst for e in result.edges}
        assert handler1.id in dst_ids
        assert handler2.id in dst_ids

    def test_no_duplicate_edges(self, tmp_path: Path) -> None:
        """Does not create duplicate edges for same caller→handler pair."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "dedup.ex"
        server_file.parent.mkdir(parents=True)
        # Two GenServer.call sites in same function → only one edge to handler
        server_file.write_text("""
defmodule MyApp.Dedup do
  use GenServer

  def double_call(server) do
    GenServer.call(server, :first)
    GenServer.call(server, :second)
  end

  def handle_call(_msg, _from, state) do
    {:reply, :ok, state}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{server_file}:5-8:MyApp.Dedup.double_call:function",
            name="MyApp.Dedup.double_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=8, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:10-12:MyApp.Dedup.handle_call:function",
            name="MyApp.Dedup.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=10, end_line=12, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        # Only one edge per caller→handler pair
        pairs = [(e.src, e.dst) for e in result.edges]
        assert len(pairs) == len(set(pairs))


class TestOTPLinkerMetadata:
    """Tests for OTP linker run metadata and edge properties."""

    def test_run_has_pass_id(self, tmp_path: Path) -> None:
        """Run has correct pass ID."""
        from hypergumbo_core.linkers.otp import otp_linker

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)
        assert result.run is not None
        assert result.run.pass_id == "otp-linker-v1"

    def test_edge_confidence_range(self, tmp_path: Path) -> None:
        """All edges have confidence between 0 and 1."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "srv.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
defmodule MyApp.Srv do
  def ping(s), do: GenServer.call(s, :ping)
  def handle_call(:ping, _f, st), do: {:reply, :pong, st}
end
""")

        caller = Symbol(
            id=f"elixir:{server_file}:3-3:MyApp.Srv.ping:function",
            name="MyApp.Srv.ping",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=3, end_line=3, start_col=0, end_col=0),
        )
        handler = Symbol(
            id=f"elixir:{server_file}:4-4:MyApp.Srv.handle_call:function",
            name="MyApp.Srv.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=4, end_line=4, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller, handler],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        for edge in result.edges:
            assert 0.0 <= edge.confidence <= 1.0

    def test_edge_has_evidence_type(self, tmp_path: Path) -> None:
        """Edges include evidence_type for provenance tracking."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "lib" / "ev.ex"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
defmodule MyApp.Ev do
  def call_it(s), do: GenServer.call(s, :msg)
  def handle_call(:msg, _f, st), do: {:reply, :ok, st}
end
""")

        caller = Symbol(
            id=f"elixir:{server_file}:3-3:MyApp.Ev.call_it:function",
            name="MyApp.Ev.call_it",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=3, end_line=3, start_col=0, end_col=0),
        )
        handler = Symbol(
            id=f"elixir:{server_file}:4-4:MyApp.Ev.handle_call:function",
            name="MyApp.Ev.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=4, end_line=4, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller, handler],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        assert result.edges[0].meta.get("evidence_type") == "otp_genserver_dispatch"


class TestOTPLinkerRegistered:
    """Tests for linker registry integration."""

    def test_otp_linker_registered(self) -> None:
        """OTP linker is registered in the linker registry."""
        from hypergumbo_core.linkers.registry import get_linker

        import hypergumbo_core.linkers.otp

        linker = get_linker("otp")
        assert linker is not None
        assert linker.name == "otp"

    def test_otp_linker_returns_linker_result(self, tmp_path: Path) -> None:
        """otp_linker function returns a LinkerResult with expected fields."""
        from hypergumbo_core.linkers.otp import otp_linker

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)
        assert result is not None
        assert hasattr(result, "symbols")
        assert hasattr(result, "edges")
        assert hasattr(result, "run")
