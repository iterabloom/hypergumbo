# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py
"""What a module ``use``s, recorded on it, and the rows that read it (WI-movur).

``use GenServer``, ``use Oban.Worker``, ``use MyAppWeb, :live_view`` hand the
framework a MODULE whose callbacks it will call. Before this change the
analyzer kept what a ``use`` adopts only long enough to emit edges, so the
module itself carried nothing a framework row could read, and the 22 phoenix /
plug rows keyed on ``decorator: "^use ..."`` never fired (nothing writes a
decorator for Elixir). These tests pin:

- ``meta["adopted_modules"]`` on the module: the modules each ``use`` runs
  (through in-repo ``__using__`` quotes and the web-module dispatcher) and the
  behaviours they declare, plus each ``@behaviour``; absent on a module that
  adopts nothing, and never on the module whose QUOTE holds the ``use``;
- the callback tables for the behaviours they lacked (Phoenix.Channel,
  Phoenix.Socket, Oban.Worker, Plug.ErrorHandler), and ``start_link`` for the
  modules whose injected ``child_spec/1`` calls it (GenServer, Agent, Task,
  Supervisor) in place of Agent's ``init``, which Agent does not have;
- end to end, the shipped ``adopts:`` rows stamp each concept on the
  analyzer's own module symbol; ``use Phoenix.Controller`` gets none (routes
  reach the actions, and a per-module controller entry pushed routes out of
  the capped entry list), and a schema gets one ``model``, not one per file.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.entrypoints import detect_entrypoints
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.elixir import analyze_elixir

_WEB = """defmodule MyAppWeb do
  def controller do
    quote do
      use Phoenix.Controller, formats: [:json]
    end
  end

  def live_view do
    quote do
      use Phoenix.LiveView
    end
  end

  def channel do
    quote do
      use Phoenix.Channel
    end
  end

  def router do
    quote do
      use Phoenix.Router
    end
  end

  defmacro __using__(which) when is_atom(which) do
    apply(__MODULE__, which, [])
  end
end
"""

_APP = """defmodule MyApp.Counter do
  use GenServer
  def start_link(n), do: GenServer.start_link(__MODULE__, n)
  def init(n), do: {:ok, n}
  def handle_call(:get, _from, n), do: {:reply, n, n}
end

defmodule MyApp.Cache do
  use Agent
  def start_link(_), do: Agent.start_link(fn -> %{} end)
  def init(x), do: x
end

defmodule MyApp.Warmup do
  use Task
  def start_link(arg), do: Task.start_link(__MODULE__, :run, [arg])
  def run(_arg), do: :ok
end

defmodule MyApp.Tree do
  use Supervisor
  def start_link(a), do: Supervisor.start_link(__MODULE__, a)
  def init(_), do: Supervisor.init([], strategy: :one_for_one)
end

defmodule MyApp.Mailer do
  use Oban.Worker, queue: :mailers
  def perform(_job), do: :ok
end

defmodule MyApp.Pipeline do
  use Broadway
  def handle_message(_p, m, _c), do: m
end

defmodule MyApp.Schema do
  use Absinthe.Schema
end

defmodule MyApp.Types do
  use Absinthe.Schema.Notation
end

defmodule MyApp.Post do
  use Ecto.Schema
end

defmodule MyApp.Registry do
  def lookup(name), do: GenServer.whereis(name)
end
"""

_PHX = """defmodule MyAppWeb.PageController do
  use MyAppWeb, :controller
  def index(conn, _), do: conn
end

defmodule MyAppWeb.RawController do
  use Phoenix.Controller
  def index(conn, _), do: conn
end

defmodule MyAppWeb.DashLive do
  use MyAppWeb, :live_view
  def mount(_p, _s, socket), do: {:ok, socket}
end

defmodule MyAppWeb.RoomChannel do
  use MyAppWeb, :channel
  def join(_t, _p, socket), do: {:ok, socket}
  def handle_in(_e, _p, socket), do: {:noreply, socket}
end

defmodule MyAppWeb.UserSocket do
  use Phoenix.Socket
  def connect(_p, socket, _i), do: {:ok, socket}
  def id(_socket), do: nil
end

defmodule MyAppWeb.Router do
  use MyAppWeb, :router
end

defmodule MyAppWeb.Badge do
  use Phoenix.Component
end
"""

_PLUG = """defmodule MyPlug.Router do
  use Plug.Router
  use Plug.ErrorHandler
  def handle_errors(conn, _), do: conn
end

defmodule MyPlug.Pipeline do
  use Plug.Builder
end

defmodule MyPlug.Auth do
  @behaviour Plug
  def init(o), do: o
  def call(conn, _), do: conn
end
"""


def _analyze(tmp_path: Path) -> list[Symbol]:
    for name, body in {"web.ex": _WEB, "app.ex": _APP, "phx.ex": _PHX,
                       "plug.ex": _PLUG}.items():
        (tmp_path / name).write_text(body)
    symbols = list(analyze_elixir(tmp_path).symbols)
    for symbol in symbols:
        if Path(symbol.path).is_absolute():
            symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
    return symbols


def _module(symbols: list[Symbol], name: str) -> Symbol:
    found = [s for s in symbols if s.name == name and s.kind == "module"]
    assert len(found) == 1, f"expected one module {name!r}, got {found}"
    return found[0]


def _adopted(symbols: list[Symbol], name: str) -> list[str] | None:
    return (_module(symbols, name).meta or {}).get("adopted_modules")


def _concepts(symbol: Symbol) -> set[str]:
    return {
        f"{c['concept']}@{c.get('framework')}"
        for c in (symbol.meta or {}).get("concepts", [])
        if c.get("concept") != "library_export"
    }


class TestTheModuleRecordsWhatItAdopts:
    def test_a_library_use_records_the_module_and_its_behaviour(self, tmp_path):
        symbols = _analyze(tmp_path)
        assert _adopted(symbols, "MyApp.Counter") == ["GenServer"]
        assert _adopted(symbols, "MyApp.Mailer") == ["Oban.Worker"]
        assert _adopted(symbols, "MyApp.Pipeline") == ["Broadway"]

    def test_a_dispatcher_use_records_what_its_quote_runs(self, tmp_path):
        symbols = _analyze(tmp_path)
        assert _adopted(symbols, "MyAppWeb.DashLive") == ["MyAppWeb", "Phoenix.LiveView"]
        assert _adopted(symbols, "MyAppWeb.PageController") == [
            "MyAppWeb", "Phoenix.Controller"]
        assert _adopted(symbols, "MyAppWeb.RoomChannel") == [
            "MyAppWeb", "Phoenix.Channel"]

    def test_a_behaviour_directive_is_adopted(self, tmp_path):
        symbols = _analyze(tmp_path)
        assert _adopted(symbols, "MyPlug.Auth") == ["Plug"]
        assert _adopted(symbols, "MyPlug.Router") == [
            "Plug.Router", "Plug", "Plug.ErrorHandler"]

    def test_a_module_that_only_calls_a_library_adopts_nothing(self, tmp_path):
        symbols = _analyze(tmp_path)
        assert _adopted(symbols, "MyApp.Registry") is None

    def test_a_module_naming_itself_adopts_nothing(self, tmp_path):
        (tmp_path / "self.ex").write_text(
            "defmodule MyApp.Store do\n"
            "  @callback get(term) :: term\n"
            "  @behaviour MyApp.Store\n"
            "end\n"
        )
        symbols = list(analyze_elixir(tmp_path).symbols)
        assert _adopted(symbols, "MyApp.Store") is None

    def test_the_web_module_is_not_what_its_quotes_use(self, tmp_path):
        symbols = _analyze(tmp_path)
        assert _adopted(symbols, "MyAppWeb") is None


def _dispatched(tmp_path: Path) -> set[tuple[str, str]]:
    for name, body in {"web.ex": _WEB, "app.ex": _APP, "phx.ex": _PHX,
                       "plug.ex": _PLUG}.items():
        (tmp_path / name).write_text(body)
    result = analyze_elixir(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    return {
        (names.get(e.src, e.src), names.get(e.dst, e.dst))
        for e in result.edges
        if e.edge_type == "dispatches_to" and e.evidence_type == "behaviour_callback"
    }


class TestTheCallbackTables:
    def test_channel_socket_worker_and_error_handler_callbacks_are_dispatched(
        self, tmp_path,
    ):
        got = _dispatched(tmp_path)
        assert ("MyAppWeb.RoomChannel", "MyAppWeb.RoomChannel.join") in got
        assert ("MyAppWeb.RoomChannel", "MyAppWeb.RoomChannel.handle_in") in got
        assert ("MyAppWeb.UserSocket", "MyAppWeb.UserSocket.connect") in got
        assert ("MyAppWeb.UserSocket", "MyAppWeb.UserSocket.id") in got
        assert ("MyApp.Mailer", "MyApp.Mailer.perform") in got
        assert ("MyPlug.Router", "MyPlug.Router.handle_errors") in got

    def test_start_link_is_what_the_injected_child_spec_calls(self, tmp_path):
        got = _dispatched(tmp_path)
        for module in ("MyApp.Counter", "MyApp.Cache", "MyApp.Warmup", "MyApp.Tree"):
            assert (module, f"{module}.start_link") in got
        assert ("MyApp.Warmup", "MyApp.Warmup.run") in got

    def test_agent_has_no_init_callback(self, tmp_path):
        got = _dispatched(tmp_path)
        assert ("MyApp.Cache", "MyApp.Cache.init") not in got
        assert ("MyApp.Counter", "MyApp.Counter.init") in got


class TestTheShippedRowsStampTheModule:
    def _enriched(self, tmp_path: Path, *frameworks: str) -> list[Symbol]:
        symbols = _analyze(tmp_path)
        clear_pattern_cache()
        enrich_symbols(symbols, set(frameworks))
        return symbols

    def test_phoenix_rows(self, tmp_path):
        symbols = self._enriched(tmp_path, "phoenix")
        # No row on Phoenix.Controller: routes reach a controller's actions,
        # and a controller entry per module pushed routes out of the capped
        # entry list (test_a_routed_action_stays_an_http_route_entry).
        assert _concepts(_module(symbols, "MyAppWeb.PageController")) == set()
        assert _concepts(_module(symbols, "MyAppWeb.RawController")) == set()
        assert _concepts(_module(symbols, "MyAppWeb.DashLive")) == {"liveview@phoenix"}
        assert _concepts(_module(symbols, "MyAppWeb.RoomChannel")) == {
            "websocket_handler@phoenix"}
        assert _concepts(_module(symbols, "MyAppWeb.Router")) == {"router@phoenix"}
        assert _concepts(_module(symbols, "MyAppWeb.Badge")) == set()
        assert _concepts(_module(symbols, "MyApp.Post")) == {"model@phoenix"}
        assert _concepts(_module(symbols, "MyAppWeb")) == set()

    def test_plug_rows(self, tmp_path):
        symbols = self._enriched(tmp_path, "plug")
        assert _concepts(_module(symbols, "MyPlug.Router")) == {
            "middleware@plug", "error_handler@plug"}
        assert _concepts(_module(symbols, "MyPlug.Pipeline")) == {"middleware@plug"}
        assert _concepts(_module(symbols, "MyPlug.Auth")) == set()

    def test_otp_rows_load_with_no_framework_detected(self, tmp_path):
        symbols = self._enriched(tmp_path)
        for name in ("MyApp.Counter", "MyApp.Cache", "MyApp.Warmup"):
            assert _concepts(_module(symbols, name)) == {"task@otp"}, name
        assert _concepts(_module(symbols, "MyApp.Tree")) == {"supervisor@otp"}
        assert _concepts(_module(symbols, "MyApp.Registry")) == set()
        assert _concepts(_module(symbols, "MyApp.Mailer")) == set()

    def test_a_schema_is_one_model_with_plug_detected_too(self, tmp_path):
        symbols = self._enriched(tmp_path, "phoenix", "plug")
        assert _concepts(_module(symbols, "MyApp.Post")) == {"model@phoenix"}

    def test_a_routed_action_stays_an_http_route_entry(self, tmp_path):
        (tmp_path / "router.ex").write_text(
            "defmodule MyAppWeb.Routes do\n"
            "  use Phoenix.Router\n"
            "  get \"/raw\", MyAppWeb.RawController, :index\n"
            "end\n"
        )
        (tmp_path / "web.ex").write_text(_WEB)
        (tmp_path / "phx.ex").write_text(_PHX)
        result = analyze_elixir(tmp_path)
        symbols = list(result.symbols)
        assert _module(symbols, "MyAppWeb.RawController")
        clear_pattern_cache()
        enrich_symbols(symbols, {"phoenix"})
        kinds = {
            (s.name, e.kind.value)
            for e in detect_entrypoints(symbols, list(result.edges))
            for s in symbols if s.id == e.symbol_id
        }
        assert ("GET /raw", "http_route") in kinds
        assert not [k for k in kinds if k[0] == "MyAppWeb.RawController"]

    def test_job_and_graphql_rows(self, tmp_path):
        symbols = self._enriched(tmp_path, "oban", "broadway", "absinthe")
        assert _concepts(_module(symbols, "MyApp.Mailer")) == {"task@oban"}
        assert _concepts(_module(symbols, "MyApp.Pipeline")) == {"task@broadway"}
        assert _concepts(_module(symbols, "MyApp.Schema")) == {
            "graphql_schema@absinthe"}
        assert _concepts(_module(symbols, "MyApp.Types")) == {
            "graphql_resolver@absinthe"}
