# SPDX-License-Identifier: AGPL-3.0-or-later
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


class TestOTPLinkerAliasResolution:
    """Tests for alias resolution in GenServer targets.

    Elixir modules are commonly aliased (alias MyApp.UserCache → UserCache).
    GenServer.call(UserCache, :msg) should resolve via suffix matching to
    MyApp.UserCache.handle_call in the handler index.
    """

    def test_suffix_match_resolves_alias(self, tmp_path: Path) -> None:
        """Short alias name resolves to fully-qualified handler module."""
        from hypergumbo_core.linkers.otp import otp_linker

        lib = tmp_path / "lib"
        lib.mkdir()

        client_file = lib / "client.ex"
        client_file.write_text("""
defmodule MyApp.Client do
  alias MyApp.UserCache

  def get_user(id) do
    GenServer.call(UserCache, {:get, id})
  end
end
""")

        server_file = lib / "user_cache.ex"
        server_file.write_text("""
defmodule MyApp.UserCache do
  use GenServer

  def handle_call({:get, id}, _from, state) do
    {:reply, Map.get(state, id), state}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{client_file}:5-7:MyApp.Client.get_user:function",
            name="MyApp.Client.get_user",
            kind="function",
            language="elixir",
            path=str(client_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.UserCache.handle_call:function",
            name="MyApp.UserCache.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=5, end_line=7, start_col=0, end_col=0),
        )
        # Include a non-Elixir symbol to exercise the language filter
        # in _build_handler_index (line 139)
        python_sym = Symbol(
            id="python:/test.py:1-1:handler:function",
            name="handler",
            kind="function",
            language="python",
            path="/test.py",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym, python_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_call"

    def test_deeply_nested_alias_resolves(self, tmp_path: Path) -> None:
        """Deeply nested module name resolves via suffix match."""
        from hypergumbo_core.linkers.otp import otp_linker

        lib = tmp_path / "lib"
        lib.mkdir()

        client_file = lib / "worker.ex"
        client_file.write_text("""
defmodule MyApp.Workers.Processor do
  def process(item) do
    GenServer.cast(Queue, {:enqueue, item})
  end
end
""")

        server_file = lib / "queue.ex"
        server_file.write_text("""
defmodule MyApp.Services.Queue do
  use GenServer

  def handle_cast({:enqueue, item}, state) do
    {:noreply, [item | state]}
  end
end
""")

        caller_sym = Symbol(
            id=f"elixir:{client_file}:3-5:MyApp.Workers.Processor.process:function",
            name="MyApp.Workers.Processor.process",
            kind="function",
            language="elixir",
            path=str(client_file),
            span=Span(start_line=3, end_line=5, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:5-7:MyApp.Services.Queue.handle_cast:function",
            name="MyApp.Services.Queue.handle_cast",
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
        assert result.edges[0].dst == handler_sym.id
        assert result.edges[0].edge_type == "otp_cast"

    def test_no_match_when_module_unresolvable(self, tmp_path: Path) -> None:
        """When no exact or suffix match exists, no edge is created."""
        from hypergumbo_core.linkers.otp import otp_linker

        lib = tmp_path / "lib"
        lib.mkdir()

        client_file = lib / "caller.ex"
        client_file.write_text("""
defmodule MyApp.Caller do
  def ping() do
    GenServer.call(UnknownModule, :ping)
  end
end
""")

        # Include a handler so the handler_index is non-empty
        server_file = lib / "other_server.ex"
        server_file.write_text("""
defmodule MyApp.OtherServer do
  use GenServer
  def handle_call(:other, _from, state), do: {:reply, :ok, state}
end
""")

        caller_sym = Symbol(
            id=f"elixir:{client_file}:3-5:MyApp.Caller.ping:function",
            name="MyApp.Caller.ping",
            kind="function",
            language="elixir",
            path=str(client_file),
            span=Span(start_line=3, end_line=5, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"elixir:{server_file}:4-4:MyApp.OtherServer.handle_call:function",
            name="MyApp.OtherServer.handle_call",
            kind="function",
            language="elixir",
            path=str(server_file),
            span=Span(start_line=4, end_line=4, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[caller_sym, handler_sym],
            detected_languages={"elixir"},
        )
        result = otp_linker(ctx)

        # UnknownModule has no matching handler in index
        unresolved_edges = [
            e for e in result.edges
            if "MyApp.Caller.ping" in e.src and "UnknownModule" in str(e.meta)
        ]
        assert len(unresolved_edges) == 0


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


class TestErlangCallSiteDetection:
    """Tests for detecting gen_server:call/cast patterns in Erlang source code."""

    def test_detect_erlang_gen_server_call(self) -> None:
        """Detects gen_server:call(Target, Msg) pattern in Erlang."""
        from hypergumbo_core.linkers.otp import detect_erlang_otp_call_sites

        source = b"""
-module(my_client).
-export([get_data/1]).

get_data(Pid) ->
    gen_server:call(Pid, get_data).
"""
        sites = detect_erlang_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["call_type"] == "call"
        assert sites[0]["target"] == "Pid"
        assert sites[0]["is_module"] is False

    def test_detect_erlang_gen_server_cast(self) -> None:
        """Detects gen_server:cast(Target, Msg) pattern in Erlang."""
        from hypergumbo_core.linkers.otp import detect_erlang_otp_call_sites

        source = b"""
-module(my_worker).

update(Pid, Data) ->
    gen_server:cast(Pid, {update, Data}).
"""
        sites = detect_erlang_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["call_type"] == "cast"

    def test_detect_erlang_module_atom_target(self) -> None:
        """Detects gen_server:call with explicit module atom target."""
        from hypergumbo_core.linkers.otp import detect_erlang_otp_call_sites

        source = b"""
-module(client).

fetch(Id) ->
    gen_server:call(data_server, {fetch, Id}).
"""
        sites = detect_erlang_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["target"] == "data_server"
        assert sites[0]["is_module"] is True

    def test_detect_erlang_question_module_macro(self) -> None:
        """Detects gen_server:call(?MODULE, ...) pattern."""
        from hypergumbo_core.linkers.otp import detect_erlang_otp_call_sites

        source = b"""
-module(cache).

get(Key) ->
    gen_server:call(?MODULE, {get, Key}).
"""
        sites = detect_erlang_otp_call_sites(source)
        assert len(sites) >= 1
        assert sites[0]["target"] == "?MODULE"

    def test_no_erlang_gen_server_patterns(self) -> None:
        """Returns empty for Erlang code without gen_server patterns."""
        from hypergumbo_core.linkers.otp import detect_erlang_otp_call_sites

        source = b"""
-module(math).

add(A, B) -> A + B.
"""
        sites = detect_erlang_otp_call_sites(source)
        assert len(sites) == 0

    def test_detect_multiple_erlang_call_sites(self) -> None:
        """Detects multiple gen_server:call and cast sites."""
        from hypergumbo_core.linkers.otp import detect_erlang_otp_call_sites

        source = b"""
-module(multi).

get(S) -> gen_server:call(S, get).
set(S, V) -> gen_server:cast(S, {set, V}).
ping(S) -> gen_server:call(S, ping).
"""
        sites = detect_erlang_otp_call_sites(source)
        calls = [s for s in sites if s["call_type"] == "call"]
        casts = [s for s in sites if s["call_type"] == "cast"]
        assert len(calls) == 2
        assert len(casts) == 1


class TestErlangOTPLinkerSameModule:
    """Tests for same-module Erlang gen_server dispatch linking."""

    def test_links_erlang_call_to_handle_call(self, tmp_path: Path) -> None:
        """Links gen_server:call(Pid, msg) to handle_call/3 in same module."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "src" / "my_server.erl"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
-module(my_server).
-behaviour(gen_server).
-export([get_data/1, handle_call/3]).

get_data(Pid) ->
    gen_server:call(Pid, get_data).

handle_call(get_data, _From, State) ->
    {reply, State, State}.
""")

        module_sym = Symbol(
            id=f"erlang:{server_file}:2-11:my_server:module",
            name="my_server",
            kind="module",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=2, end_line=11, start_col=0, end_col=0),
        )
        caller_sym = Symbol(
            id=f"erlang:{server_file}:6-7:get_data/1:function",
            name="get_data/1",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=6, end_line=7, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"erlang:{server_file}:9-10:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=9, end_line=10, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[module_sym, caller_sym, handler_sym],
            detected_languages={"erlang"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_call"

    def test_links_erlang_cast_to_handle_cast(self, tmp_path: Path) -> None:
        """Links gen_server:cast(Pid, msg) to handle_cast/2 in same module."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "src" / "worker.erl"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
-module(worker).
-behaviour(gen_server).

update(Pid, Data) ->
    gen_server:cast(Pid, {update, Data}).

handle_cast({update, Data}, State) ->
    {noreply, Data}.
""")

        module_sym = Symbol(
            id=f"erlang:{server_file}:2-9:worker:module",
            name="worker",
            kind="module",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=2, end_line=9, start_col=0, end_col=0),
        )
        caller_sym = Symbol(
            id=f"erlang:{server_file}:5-6:update/2:function",
            name="update/2",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=5, end_line=6, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"erlang:{server_file}:8-9:handle_cast/2:function",
            name="handle_cast/2",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=8, end_line=9, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[module_sym, caller_sym, handler_sym],
            detected_languages={"erlang"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_cast"

    def test_links_erlang_question_module(self, tmp_path: Path) -> None:
        """Links gen_server:call(?MODULE, ...) to same module's handle_call."""
        from hypergumbo_core.linkers.otp import otp_linker

        server_file = tmp_path / "src" / "cache.erl"
        server_file.parent.mkdir(parents=True)
        server_file.write_text("""
-module(cache).
-behaviour(gen_server).

get(Key) ->
    gen_server:call(?MODULE, {get, Key}).

handle_call({get, _Key}, _From, State) ->
    {reply, State, State}.
""")

        module_sym = Symbol(
            id=f"erlang:{server_file}:2-9:cache:module",
            name="cache",
            kind="module",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=2, end_line=9, start_col=0, end_col=0),
        )
        caller_sym = Symbol(
            id=f"erlang:{server_file}:5-6:get/1:function",
            name="get/1",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=5, end_line=6, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"erlang:{server_file}:8-9:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=8, end_line=9, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[module_sym, caller_sym, handler_sym],
            detected_languages={"erlang"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.confidence == 0.90

    def test_erlang_no_erlang_skips(self, tmp_path: Path) -> None:
        """OTP linker returns empty when no Erlang or Elixir detected."""
        from hypergumbo_core.linkers.otp import otp_linker

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            detected_languages={"python"},
        )
        result = otp_linker(ctx)
        assert len(result.edges) == 0


class TestErlangOTPLinkerCrossModule:
    """Tests for cross-module Erlang gen_server dispatch linking."""

    def test_links_erlang_cross_module_call(self, tmp_path: Path) -> None:
        """Links gen_server:call(target_module, msg) to handler in target module."""
        from hypergumbo_core.linkers.otp import otp_linker

        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)

        client_file = src_dir / "client.erl"
        client_file.write_text("""
-module(client).

fetch(Id) ->
    gen_server:call(data_server, {fetch, Id}).
""")
        server_file = src_dir / "data_server.erl"
        server_file.write_text("""
-module(data_server).
-behaviour(gen_server).

handle_call({fetch, Id}, _From, State) ->
    {reply, ok, State}.
""")

        client_module = Symbol(
            id=f"erlang:{client_file}:2-5:client:module",
            name="client",
            kind="module",
            language="erlang",
            path=str(client_file),
            span=Span(start_line=2, end_line=5, start_col=0, end_col=0),
        )
        caller_sym = Symbol(
            id=f"erlang:{client_file}:4-5:fetch/1:function",
            name="fetch/1",
            kind="function",
            language="erlang",
            path=str(client_file),
            span=Span(start_line=4, end_line=5, start_col=0, end_col=0),
        )
        server_module = Symbol(
            id=f"erlang:{server_file}:2-6:data_server:module",
            name="data_server",
            kind="module",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=2, end_line=6, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"erlang:{server_file}:5-6:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=5, end_line=6, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[client_module, caller_sym, server_module, handler_sym],
            detected_languages={"erlang"},
        )
        result = otp_linker(ctx)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.src == caller_sym.id
        assert edge.dst == handler_sym.id
        assert edge.edge_type == "otp_call"
        assert edge.confidence == 0.80

    def test_erlang_no_matching_module_in_handler_index(
        self, tmp_path: Path,
    ) -> None:
        """No edge when target module has no handlers indexed."""
        from hypergumbo_core.linkers.otp import otp_linker

        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)

        client_file = src_dir / "client.erl"
        client_file.write_text("""
-module(client).

fetch(Id) ->
    gen_server:call(nonexistent_server, {fetch, Id}).
""")

        client_module = Symbol(
            id=f"erlang:{client_file}:2-5:client:module",
            name="client",
            kind="module",
            language="erlang",
            path=str(client_file),
            span=Span(start_line=2, end_line=5, start_col=0, end_col=0),
        )
        caller_sym = Symbol(
            id=f"erlang:{client_file}:4-5:fetch/1:function",
            name="fetch/1",
            kind="function",
            language="erlang",
            path=str(client_file),
            span=Span(start_line=4, end_line=5, start_col=0, end_col=0),
        )
        # Provide a handler in a different module so the index isn't empty
        other_file = src_dir / "other_server.erl"
        other_file.write_text("-module(other_server).\n")
        other_module = Symbol(
            id=f"erlang:{other_file}:1-1:other_server:module",
            name="other_server",
            kind="module",
            language="erlang",
            path=str(other_file),
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )
        other_handler = Symbol(
            id=f"erlang:{other_file}:2-3:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path=str(other_file),
            span=Span(start_line=2, end_line=3, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[client_module, caller_sym, other_module, other_handler],
            detected_languages={"erlang"},
        )
        result = otp_linker(ctx)
        # No edge because nonexistent_server is not in the handler index
        assert len(result.edges) == 0


class TestErlangOTPLinkerEdgeCases:
    """Tests for Erlang OTP linker defensive code paths."""

    def test_erlang_handler_without_module_symbol(self) -> None:
        """Handler function without a corresponding module symbol is skipped."""
        from hypergumbo_core.linkers.otp import _build_erlang_handler_index

        # Handler in a file with no module symbol
        handler = Symbol(
            id="erlang:src/orphan.erl:5-6:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path="src/orphan.erl",
            span=Span(start_line=5, end_line=6, start_col=0, end_col=0),
        )
        index = _build_erlang_handler_index([handler])
        assert len(index) == 0

    def test_erlang_unreadable_file_skipped(self, tmp_path: Path) -> None:
        """Unreadable .erl files are counted as skipped."""
        from unittest.mock import patch

        from hypergumbo_core.linkers.otp import otp_linker

        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)
        bad_file = src_dir / "bad.erl"
        bad_file.write_text("-module(bad).\n")

        module_sym = Symbol(
            id=f"erlang:{bad_file}:1-1:bad:module",
            name="bad",
            kind="module",
            language="erlang",
            path=str(bad_file),
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"erlang:{bad_file}:2-3:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path=str(bad_file),
            span=Span(start_line=2, end_line=3, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[module_sym, handler_sym],
            detected_languages={"erlang"},
        )

        # Patch read_bytes to raise IOError
        orig_read = Path.read_bytes

        def patched_read(self: Path) -> bytes:
            if self.name == "bad.erl":
                raise IOError("simulated read failure")
            return orig_read(self)

        with patch.object(Path, "read_bytes", patched_read):
            result = otp_linker(ctx)
        # Should not crash, just skip
        assert len(result.edges) == 0

    def test_erlang_no_enclosing_symbol(self, tmp_path: Path) -> None:
        """Call site without enclosing symbol produces no edge."""
        from hypergumbo_core.linkers.otp import otp_linker

        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)
        server_file = src_dir / "server.erl"
        # gen_server:call on line 7, but only symbols on lines 2 and 10-11
        server_file.write_text("""
-module(server).



% orphan call outside any function
gen_server:call(server, msg).


handle_call(msg, _From, State) ->
    {reply, ok, State}.
""")

        module_sym = Symbol(
            id=f"erlang:{server_file}:2-2:server:module",
            name="server",
            kind="module",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=2, end_line=2, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"erlang:{server_file}:10-11:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=10, end_line=11, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[module_sym, handler_sym],
            detected_languages={"erlang"},
        )
        result = otp_linker(ctx)
        # Call at line 7 has no enclosing function symbol, so no edge
        assert len(result.edges) == 0

    def test_erlang_duplicate_edges_deduped(self, tmp_path: Path) -> None:
        """Duplicate call sites to same handler produce only one edge."""
        from hypergumbo_core.linkers.otp import otp_linker

        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)
        server_file = src_dir / "dup.erl"
        server_file.write_text("""
-module(dup).

retry(Pid) ->
    gen_server:call(Pid, msg),
    gen_server:call(Pid, msg).

handle_call(msg, _From, State) ->
    {reply, ok, State}.
""")

        module_sym = Symbol(
            id=f"erlang:{server_file}:2-9:dup:module",
            name="dup",
            kind="module",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=2, end_line=9, start_col=0, end_col=0),
        )
        caller_sym = Symbol(
            id=f"erlang:{server_file}:4-6:retry/1:function",
            name="retry/1",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=4, end_line=6, start_col=0, end_col=0),
        )
        handler_sym = Symbol(
            id=f"erlang:{server_file}:8-9:handle_call/3:function",
            name="handle_call/3",
            kind="function",
            language="erlang",
            path=str(server_file),
            span=Span(start_line=8, end_line=9, start_col=0, end_col=0),
        )

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[module_sym, caller_sym, handler_sym],
            detected_languages={"erlang"},
        )
        result = otp_linker(ctx)
        # Two call sites to same handler → only 1 edge (deduped)
        assert len(result.edges) == 1
