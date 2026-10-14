# SPDX-License-Identifier: AGPL-3.0-or-later
"""A route marker is minted only for a route macro inside a router module.

``get`` / ``post`` / ``put`` / ``delete`` / ``live`` / ``resources`` are
ordinary function names. Phoenix gives them route meaning only inside a
module that runs ``use Phoenix.Router`` -- directly, or through the
generated web module's dispatcher, ``use AppWeb, :router``, whose
``def router`` quotes ``use Phoenix.Router``. ``use Plug.Router`` gives the
HTTP verbs (and only them) the same meaning. Everywhere else the same names
are plain calls: a cache module's own ``get("user_#{id}")`` and a
``Phoenix.ConnTest`` request ``conn |> get("/pages")`` in a test are not
routes, and before this gate each minted one (361 of the 738 route symbols
on a real Phoenix app were such calls).

The pipe rule holds inside a router too: a route macro is never written as
the right side of ``|>`` (the pipe would hand it the conn as its path), so a
piped ``get`` in a router's plug function is a request helper, not a route.
"""
from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir


def _routes(tmp_path: Path, files: dict[str, str]) -> list[tuple[str, str]]:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    result = analyze_elixir(tmp_path)
    return sorted(
        (Path(s.path).name, s.name) for s in result.symbols
        if (s.meta or {}).get("framework_role") == "route")


def test_piped_conntest_requests_are_not_routes(tmp_path: Path) -> None:
    assert _routes(tmp_path, {"test/page_test.exs": (
        "defmodule AppWeb.PageTest do\n"
        "  use AppWeb.ConnCase\n"
        '  test "pages", %{conn: conn} do\n'
        '    conn |> get("/pages")\n'
        '    conn |> put_req_header("x", "y") |> delete("/gone")\n'
        '    post(conn, "/nothing", %{})\n'
        "  end\n"
        "end\n")}) == []


def test_cache_module_get_put_are_not_routes(tmp_path: Path) -> None:
    assert _routes(tmp_path, {"lib/cache.ex": (
        "defmodule App.Cache do\n"
        "  def get(k), do: :ets.lookup(:c, k)\n"
        "  def put(k, v, _ttl), do: :ets.insert(:c, {k, v})\n"
        '  def user(id), do: get("user_#{id}")\n'
        '  def store(id, d), do: put("post_#{id}", d, 60)\n'
        "  def drop(id), do: delete(\"feed:#{id}\")\n"
        "  def delete(k), do: :ets.delete(:c, k)\n"
        "end\n")}) == []


def test_use_phoenix_router_still_mints(tmp_path: Path) -> None:
    assert _routes(tmp_path, {"lib/router.ex": (
        "defmodule AppWeb.Router do\n"
        "  use Phoenix.Router\n"
        '  scope "/", AppWeb do\n'
        '    get "/", PageController, :home\n'
        '    live "/feed", FeedLive, :index\n'
        "  end\n"
        "end\n")}) == [
        ("router.ex", "GET /"), ("router.ex", "LIVE /feed")]


def test_use_web_module_router_dispatcher_mints(tmp_path: Path) -> None:
    assert _routes(tmp_path, {"lib/router.ex": (
        "defmodule AppWeb.Router do\n"
        "  use AppWeb, :router\n"
        '  scope "/api", AppWeb do\n'
        '    resources "/posts", PostController, only: [:show]\n'
        '    post "/login", SessionController, :create\n'
        "  end\n"
        "end\n")}) == [
        ("router.ex", "GET /api/posts/:id"), ("router.ex", "POST /api/login")]


def test_web_module_dispatcher_quote_is_not_a_router(tmp_path: Path) -> None:
    # ``AppWeb`` itself only QUOTES ``use Phoenix.Router`` for the modules
    # that ``use AppWeb, :router``; its own helper ``get("k")`` is no route.
    assert _routes(tmp_path, {"lib/app_web.ex": (
        "defmodule AppWeb do\n"
        "  def router do\n"
        "    quote do\n"
        "      use Phoenix.Router, helpers: false\n"
        "    end\n"
        "  end\n"
        '  def fetch, do: get("/k")\n'
        "  defmacro __using__(which), do: apply(__MODULE__, which, [])\n"
        "end\n")}) == []


def test_other_web_module_dispatch_kinds_are_not_routers(
    tmp_path: Path,
) -> None:
    assert _routes(tmp_path, {"lib/page_controller.ex": (
        "defmodule AppWeb.PageController do\n"
        "  use AppWeb, :controller\n"
        '  def show(conn, _), do: get("/x")\n'
        "end\n")}) == []


def test_plug_router_verbs_mint_but_phoenix_only_macros_do_not(
    tmp_path: Path,
) -> None:
    assert _routes(tmp_path, {"lib/plug_router.ex": (
        "defmodule App.PlugRouter do\n"
        "  use Plug.Router\n"
        "  plug :match\n"
        "  plug :dispatch\n"
        '  get "/hello" do\n'
        '    send_resp(conn, 200, "world")\n'
        "  end\n"
        '  post "/echo", to: App.EchoPlug\n'
        '  live "/not-plug", App.Live\n'
        '  resources "/nope", App.NopeController\n'
        "end\n")}) == [
        ("plug_router.ex", "GET /hello"), ("plug_router.ex", "POST /echo")]


def test_piped_call_inside_a_router_is_not_a_route(tmp_path: Path) -> None:
    assert _routes(tmp_path, {"lib/router.ex": (
        "defmodule AppWeb.Router do\n"
        "  use Phoenix.Router\n"
        '  get "/real", PageController, :real\n'
        "  defp probe(conn, _opts) do\n"
        '    Logger.info("probe")\n'
        '    conn |> get("/health")\n'
        "  end\n"
        "end\n")}) == [("router.ex", "GET /real")]


def test_router_gate_is_per_module_not_per_file(tmp_path: Path) -> None:
    assert _routes(tmp_path, {"lib/both.ex": (
        "defmodule App.Cache do\n"
        '  def user(id), do: get("user_#{id}")\n'
        "end\n"
        "defmodule AppWeb.Router do\n"
        "  use Phoenix.Router\n"
        '  get "/ok", PageController, :ok\n'
        "end\n")}) == [("both.ex", "GET /ok")]


def test_route_outside_any_module_is_not_minted(tmp_path: Path) -> None:
    assert _routes(tmp_path, {"script.exs": (
        "use Phoenix.Router\n"
        'get "/loose", PageController, :loose\n')}) == []


def test_unreadable_module_bodies_and_use_forms_are_no_router(
    tmp_path: Path,
) -> None:
    # A keyword-form ``defmodule`` has no ``do`` block to read, and a
    # ``use`` whose module is not written as an alias names no router.
    assert _routes(tmp_path, {"lib/odd.ex": (
        'defmodule App.Short, do: def(f, do: get("/a"))\n'
        "defmodule App.Gen do\n"
        "  use unquote(App.Base), :router\n"
        '  get "/b", PageController, :b\n'
        "end\n")}) == []
