# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/elixir.py
"""Macro calls that hand the framework code of the repository (WI-movur).

``plug :authenticate``, ``plug MyApp.Auth``, ``action_fallback
MyAppWeb.Fallback``, ``socket "/s", MyAppWeb.UserSocket``, ``channel "room:*",
MyAppWeb.RoomChannel``, ``live_render(conn, MyAppWeb.ClockLive)``,
``import_types MyApp.Types`` and ``forward "/x", to: MyApp.Admin`` each name a
function or module of the repository that the framework will call. Nothing
in the repository calls it, so before this change it had no caller and no
entry (plug.yaml's deleted ``^plug$`` row keyed a decorator nothing writes).

The rule pinned here: a call to a name that is NOT a function of the
repository, which a shipped ``usage:`` row names
(``framework_patterns.usage_row_names_call``), with an argument that is an
alias naming a module of the repository, or an atom naming a function of the
enclosing module, gets a ``references`` edge (evidence
``callback_argument_reference``) from the calling scope to that code and a
``UsageContext`` (kind ``call``, the name as written, ``args[i]`` /
``kwargs[key]``, ``symbol_ref`` the code). The macro NAMES live in the rows:
the same shape under a name no row has is not recorded. Route macros of a
router module stay the route extractor's.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.framework_patterns import (
    clear_pattern_cache,
    enrich_symbols,
    usage_row_names_call,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.elixir import analyze_elixir

_ROUTER = """defmodule MyAppWeb.Router do
  use Phoenix.Router

  pipeline :browser do
    plug :fetch_session
    plug :require_user
    plug MyAppWeb.Plugs.Auth, realm: "app"
    plug Plug.Logger
  end

  scope "/" do
    get "/", MyAppWeb.PageController, :index
  end

  forward "/admin", to: MyAppWeb.AdminRouter
  forward "/api", Absinthe.Plug, schema: MyApp.Schema

  def require_user(conn, _opts), do: conn
end
"""

_WEB = """defmodule MyAppWeb.PageController do
  use Phoenix.Controller
  plug :load when action in [:index]
  action_fallback MyAppWeb.Fallback
  def index(conn, _), do: conn
  def embed(conn, _), do: live_render(conn, MyAppWeb.ClockLive, session: %{})
  defp load(conn, _opts), do: conn
end

defmodule MyAppWeb.Fallback do
  def call(conn, _), do: conn
end

defmodule MyAppWeb.ClockLive do
  def mount(_p, _s, socket), do: {:ok, socket}
end

defmodule MyAppWeb.Plugs.Auth do
  def init(o), do: o
  def call(conn, _), do: conn
end

defmodule MyAppWeb.AdminRouter do
  def init(o), do: o
  def call(conn, _), do: conn
end

defmodule MyAppWeb.Endpoint do
  socket "/socket", MyAppWeb.UserSocket, websocket: true
  plug MyAppWeb.Router
end

defmodule MyAppWeb.UserSocket do
  channel "room:*", MyAppWeb.RoomChannel
  def connect(_p, s, _i), do: {:ok, s}
end

defmodule MyAppWeb.RoomChannel do
  def join(_t, _p, s), do: {:ok, s}
end

defmodule MyApp.Schema do
  import_types MyApp.Types
end

defmodule MyApp.Types do
end

defmodule MyApp.Util do
  def run(conn), do: plug(conn, :wrap)
  defp plug(conn, _), do: conn
  def wrap(x), do: register_thing(MyApp.Types)
  def go(x), do: Enum.map(x, MyApp.Types)
end

defmodule MyAppWeb.LiveSocketEndpoint do
  socket "/live",
    # the LiveView socket
    MyAppWeb.UserSocket
end
"""


def _write(tmp_path: Path) -> None:
    (tmp_path / "router.ex").write_text(_ROUTER)
    (tmp_path / "web.ex").write_text(_WEB)


def _contexts(tmp_path: Path) -> dict[tuple[int, str, str], str | None]:
    _write(tmp_path)
    result = analyze_elixir(tmp_path)
    names = {s.id: s.name for s in result.symbols}
    return {
        (u.span.start_line, u.context_name, u.position): names.get(u.symbol_ref or "")
        for u in result.usage_contexts
        if Path(u.path).name in ("router.ex", "web.ex")
        # the route extractor's own contexts (router DSL, position args[0])
        and not (u.context_name in ("get", "live", "resources")
                 and u.position == "args[0]")
    }


class TestTheRowsNameTheMacros:
    def test_reach_the_gate_knows_these_names(self):
        clear_pattern_cache()
        for name in ("plug", "forward", "action_fallback", "socket", "channel",
                     "live_render", "import_types"):
            assert usage_row_names_call(name, "elixir"), name
        assert not usage_row_names_call("register_thing", "elixir")


class TestRegistrationsAreRecorded:
    def test_function_and_module_plugs(self, tmp_path):
        got = _contexts(tmp_path)
        assert got[(6, "plug", "args[0]")] == "MyAppWeb.Router.require_user"
        assert got[(7, "plug", "args[0]")] == "MyAppWeb.Plugs.Auth"
        assert got[(3, "plug", "args[0]")] == "MyAppWeb.PageController.load"

    def test_library_plugs_and_imported_functions_are_not(self, tmp_path):
        got = _contexts(tmp_path)
        # ``:fetch_session`` is Phoenix's, ``Plug.Logger`` a library module.
        assert not [k for k in got if k[0] in (5, 8)]

    def test_forward_action_fallback_socket_channel_live_render_import_types(
        self, tmp_path,
    ):
        got = _contexts(tmp_path)
        assert got[(15, "forward", "kwargs[to]")] == "MyAppWeb.AdminRouter"
        assert got[(16, "forward", "kwargs[schema]")] == "MyApp.Schema"
        assert got[(4, "action_fallback", "args[0]")] == "MyAppWeb.Fallback"
        assert got[(6, "live_render", "args[1]")] == "MyAppWeb.ClockLive"
        assert got[(29, "socket", "args[1]")] == "MyAppWeb.UserSocket"
        assert got[(30, "plug", "args[0]")] == "MyAppWeb.Router"
        assert got[(34, "channel", "args[1]")] == "MyAppWeb.RoomChannel"
        assert got[(43, "import_types", "args[0]")] == "MyApp.Types"

    def test_a_comment_among_the_arguments_is_not_one(self, tmp_path):
        got = _contexts(tmp_path)
        assert got[(57, "socket", "args[1]")] == "MyAppWeb.UserSocket"

    def test_controls(self, tmp_path):
        got = _contexts(tmp_path)
        # A call of the module's own ``plug`` function; a name no row has;
        # a library call passing a module; the route macro of a router.
        assert not [k for k in got if k[0] in (50, 52, 53)]
        assert not [k for k in got if k[1] == "register_thing"]
        assert not [k for k in got if k[1] == "get"]


class TestTheEdge:
    def test_the_calling_scope_references_the_registered_code(self, tmp_path):
        _write(tmp_path)
        result = analyze_elixir(tmp_path)
        names = {s.id: s.name for s in result.symbols}
        got = {
            (names.get(e.src), names.get(e.dst))
            for e in result.edges
            if e.evidence_type == "callback_argument_reference"
        }
        assert ("MyAppWeb.Router", "MyAppWeb.Router.require_user") in got
        assert ("MyAppWeb.Router", "MyAppWeb.Plugs.Auth") in got
        assert ("MyAppWeb.PageController.embed", "MyAppWeb.ClockLive") in got
        assert ("MyAppWeb.UserSocket", "MyAppWeb.RoomChannel") in got
        assert ("MyApp.Util", "MyApp.Types") not in got
        assert ("MyApp.Util.wrap", "MyApp.Types") not in got


def _concepts(symbol: Symbol) -> set[str]:
    return {
        f"{c['concept']}@{c.get('framework')}"
        for c in (symbol.meta or {}).get("concepts", [])
        if c.get("concept") != "library_export"
    }


class TestTheShippedRowsStampTheRegisteredCode:
    def test_concepts(self, tmp_path):
        _write(tmp_path)
        result = analyze_elixir(tmp_path)
        symbols = list(result.symbols)
        for symbol in symbols:
            if Path(symbol.path).is_absolute():
                symbol.path = Path(symbol.path).relative_to(tmp_path).as_posix()
        clear_pattern_cache()
        enrich_symbols(symbols, {"phoenix", "plug", "absinthe"},
                       list(result.usage_contexts))
        by_name = {s.name: s for s in symbols if s.kind in ("module", "function")}
        assert _concepts(by_name["MyAppWeb.Router.require_user"]) == {"middleware@plug"}
        assert _concepts(by_name["MyAppWeb.PageController.load"]) == {"middleware@plug"}
        assert _concepts(by_name["MyAppWeb.Plugs.Auth"]) == {"middleware@plug"}
        assert _concepts(by_name["MyAppWeb.AdminRouter"]) == {"middleware@plug"}
        assert _concepts(by_name["MyAppWeb.Fallback"]) == {"controller@phoenix"}
        assert _concepts(by_name["MyAppWeb.ClockLive"]) == {"liveview@phoenix"}
        assert _concepts(by_name["MyAppWeb.UserSocket"]) == {"websocket_handler@phoenix"}
        assert _concepts(by_name["MyAppWeb.RoomChannel"]) == {"websocket_handler@phoenix"}
        assert _concepts(by_name["MyApp.Types"]) == {"graphql_resolver@absinthe"}
        assert _concepts(by_name["MyApp.Schema"]) == {"graphql_schema@absinthe"}
        assert _concepts(by_name["MyApp.Util"]) == set()
