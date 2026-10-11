# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phoenix ``resources`` expansion and scope-alias qualification of routes.

The route markers must match what ``mix phx.routes`` lists. Phoenix's
``Phoenix.Router.Resource`` defines the action set as ``[:index, :edit,
:new, :show, :create, :update, :delete]``; ``:update`` is mounted on BOTH
``PATCH`` and ``PUT``, so a plain ``resources`` is eight routes. ``only:`` /
``except:`` filter that set, ``singleton: true`` drops ``:index`` and the
``/:id`` segment, and ``param:`` renames the ``:id`` segment. The controller
named in a route is concatenated onto the alias of every enclosing ``scope``
(``scope "/", AppWeb do get "/", PageController, :home end`` dispatches to
``AppWeb.PageController``), for ``get``/``post``/... , ``live`` and
``resources`` alike.
"""
from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir


def _routes(tmp_path: Path, router: str) -> list[tuple[str, str, str | None]]:
    (tmp_path / "router.ex").write_text(router)
    result = analyze_elixir(tmp_path)
    return [
        (s.name, s.meta.get("action"), s.meta.get("controller"))
        for s in result.symbols
        if (s.meta or {}).get("framework_role") == "route"
    ]


def _router(body: str) -> str:
    return f"defmodule AppWeb.Router do\n  use Phoenix.Router\n{body}\nend\n"


def test_resources_emits_eight_routes_including_put_update(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router('  resources "/posts", PostController'))
    assert sorted((n, a) for n, a, _ in routes) == sorted([
        ("GET /posts", "index"),
        ("GET /posts/:id/edit", "edit"),
        ("GET /posts/new", "new"),
        ("GET /posts/:id", "show"),
        ("POST /posts", "create"),
        ("PATCH /posts/:id", "update"),
        ("PUT /posts/:id", "update"),
        ("DELETE /posts/:id", "delete"),
    ])


def test_resources_only_keeps_listed_actions(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/users", UserController, only: [:index, :show]'))
    assert sorted(n for n, _, _ in routes) == ["GET /users", "GET /users/:id"]


def test_resources_except_drops_listed_actions(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/posts", PostController, except: [:delete, :update]'))
    assert sorted(n for n, _, _ in routes) == sorted([
        "GET /posts", "GET /posts/:id/edit", "GET /posts/new",
        "GET /posts/:id", "POST /posts",
    ])


def test_resources_options_in_explicit_list(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/posts", PostController, [only: [:update]]'))
    assert sorted(n for n, _, _ in routes) == [
        "PATCH /posts/:id", "PUT /posts/:id"]


def test_resources_singleton_has_no_index_and_no_id(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/account", AccountController, singleton: true'))
    assert sorted((n, a) for n, a, _ in routes) == sorted([
        ("GET /account/edit", "edit"),
        ("GET /account/new", "new"),
        ("GET /account", "show"),
        ("POST /account", "create"),
        ("PATCH /account", "update"),
        ("PUT /account", "update"),
        ("DELETE /account", "delete"),
    ])


def test_resources_singleton_with_only(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/account", AccountController, only: [:show], '
        'singleton: true'))
    assert [n for n, _, _ in routes] == ["GET /account"]


def test_resources_param_renames_id_segment(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/posts", PostController, param: "slug", '
        'only: [:show, :edit]'))
    assert sorted(n for n, _, _ in routes) == [
        "GET /posts/:slug", "GET /posts/:slug/edit"]


def test_resources_unreadable_only_falls_back_to_every_action(
    tmp_path: Path,
) -> None:
    # A non-literal option value cannot be evaluated statically; the full
    # action set is the conservative answer (no route is hidden).
    routes = _routes(tmp_path, _router(
        '  resources "/posts", PostController, only: @actions'))
    assert len(routes) == 8


def test_filed_repro_scope_alias_only_except(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  scope "/api", AppWeb do\n'
        '    resources "/users", UserController, only: [:index, :show]\n'
        '    resources "/posts", PostController, except: [:delete]\n'
        '    get "/x", XController, :x\n'
        '  end'))
    users = [r for r in routes if r[2] == "AppWeb.UserController"]
    posts = [r for r in routes if r[2] == "AppWeb.PostController"]
    assert len(users) == 2
    assert len(posts) == 7
    assert {r[0] for r in posts} >= {"PUT /api/posts/:id", "PATCH /api/posts/:id"}
    assert ("GET /api/x", "x", "AppWeb.XController") in routes
    assert len(routes) == 10


def test_scope_alias_qualifies_every_route_macro(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  scope "/", AppWeb do\n'
        '    get "/", PageController, :home\n'
        '    post "/login", SessionController, :create\n'
        '    live "/dash", DashboardLive\n'
        '    live "/users/:id", UserLive.Show, :show\n'
        '  end'))
    assert {(n, c) for n, _, c in routes} == {
        ("GET /", "AppWeb.PageController"),
        ("POST /login", "AppWeb.SessionController"),
        ("LIVE /dash", "AppWeb.DashboardLive"),
        ("LIVE /users/:id", "AppWeb.UserLive.Show"),
    }


def test_nested_scope_aliases_concatenate_and_inherit(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  scope "/", AppWeb do\n'
        '    scope "/admin", Admin, as: :admin do\n'
        '      get "/", DashboardController, :index\n'
        '      scope "/dev" do\n'
        '        get "/tools", ToolsController, :index\n'
        '      end\n'
        '    end\n'
        '    get "/after", AfterController, :index\n'
        '  end\n'
        '  get "/outside", OutsideController, :index'))
    by_name = {n: c for n, _, c in routes}
    assert by_name["GET /admin"] == "AppWeb.Admin.DashboardController"
    assert by_name["GET /admin/dev/tools"] == "AppWeb.Admin.ToolsController"
    assert by_name["GET /after"] == "AppWeb.AfterController"
    assert by_name["GET /outside"] == "OutsideController"


def test_scope_keyword_alias_and_alias_false(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  scope path: "/api", alias: AppWeb.Api do\n'
        '    get "/a", AController, :a\n'
        '    scope "/raw", alias: false do\n'
        '      get "/b", Other.BController, :b\n'
        '    end\n'
        '  end\n'
        '  scope "/v2", AppWeb.V2, as: :v2 do\n'
        '    get "/c", CController, :c\n'
        '  end\n'
        '  scope "/opts", alias: AppWeb.Opts do\n'
        '    get "/d", DController, :d\n'
        '  end'))
    by_name = {n: c for n, _, c in routes}
    assert by_name["GET /api/a"] == "AppWeb.Api.AController"
    assert by_name["GET /api/raw/b"] == "Other.BController"
    assert by_name["GET /v2/c"] == "AppWeb.V2.CController"
    assert by_name["GET /opts/d"] == "AppWeb.Opts.DController"


def test_scope_alias_is_itself_lexically_expanded(tmp_path: Path) -> None:
    routes = _routes(tmp_path, (
        "defmodule AppWeb.Router do\n"
        "  use AppWeb, :router\n"
        "  alias AppWeb.Live\n"
        '  scope "/venues", Live do\n'
        '    live "/", VenueLive\n'
        "  end\n"
        "end\n"))
    assert routes == [("LIVE /venues", "mount", "AppWeb.Live.VenueLive")]


def test_nested_resources_are_found_and_qualified(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  scope "/", AppWeb do\n'
        '    resources "/users", UserController, only: [:show] do\n'
        '      resources "/posts", PostController, only: [:index]\n'
        '    end\n'
        '  end'))
    assert {(a, c) for _, a, c in routes} == {
        ("show", "AppWeb.UserController"),
        ("index", "AppWeb.PostController"),
    }


def test_resources_usage_context_carries_qualified_controller(
    tmp_path: Path,
) -> None:
    (tmp_path / "router.ex").write_text(_router(
        '  scope "/", AppWeb do\n'
        '    resources "/posts", PostController, only: [:index]\n'
        '  end'))
    result = analyze_elixir(tmp_path)
    ctx = next(c for c in result.usage_contexts
               if c.context_name == "resources")
    assert ctx.metadata["controller"] == "AppWeb.PostController"


def test_resources_list_with_a_non_literal_member_is_unreadable(
    tmp_path: Path,
) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/posts", PostController, only: [:index, @extra]'))
    assert len(routes) == 8


def test_resources_empty_option_list_filters_nothing(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  resources "/posts", PostController, []'))
    assert len(routes) == 8


def test_argumentless_scope_inherits_the_outer_alias(tmp_path: Path) -> None:
    routes = _routes(tmp_path, _router(
        '  scope "/", AppWeb do\n'
        '    scope do\n'
        '      get "/", PageController, :home\n'
        '    end\n'
        '  end'))
    assert routes == [("GET /", "home", "AppWeb.PageController")]
