# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phoenix route paths carry their enclosing ``scope`` and ``resources`` prefix.

Phoenix builds a route's path as ``"/" <> Enum.join(scope_segments ++
String.split(path, "/", trim: true), "/")``: every enclosing ``scope`` path
(positional, or ``path:``) contributes its segments, so ``scope "/admin"`` +
``get "/users"`` is ``/admin/users`` and ``scope "/admin"`` + ``live "/"`` is
``/admin`` (empty and trailing segments vanish). A ``resources ... do`` block
is a scope too: its routes sit under ``<path>/:<name>_<param>``, where
``name`` is the controller's last segment without ``Controller``,
underscored (``name:`` overrides it), ``param`` defaults to ``id``, and a
singleton resource nests under its bare path.
"""
from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir


def _names(tmp_path: Path, body: str) -> list[str]:
    (tmp_path / "router.ex").write_text(
        f"defmodule AppWeb.Router do\n  use Phoenix.Router\n{body}\nend\n")
    result = analyze_elixir(tmp_path)
    return sorted(
        s.name for s in result.symbols
        if (s.meta or {}).get("framework_role") == "route")


def test_filed_repro_scope_prefix(tmp_path: Path) -> None:
    assert _names(tmp_path, (
        '  scope "/admin", AppWeb do\n'
        '    live "/", Admin.DashboardLive\n'
        '    get "/users", Admin.UserController, :index\n'
        '  end')) == ["GET /admin/users", "LIVE /admin"]


def test_nested_scopes_and_root_scope(tmp_path: Path) -> None:
    assert _names(tmp_path, (
        '  scope "/", AppWeb do\n'
        '    get "/", PageController, :home\n'
        '    scope "/api" do\n'
        '      scope "/v1/", V1, as: :v1 do\n'
        '        get "/things/", ThingController, :index\n'
        '      end\n'
        '    end\n'
        '  end\n'
        '  get "/vid/", VidController, :index')) == [
        "GET /", "GET /api/v1/things", "GET /vid"]


def test_keyword_scope_path_and_alias_only_scope(tmp_path: Path) -> None:
    assert _names(tmp_path, (
        '  scope path: "/p", alias: AppWeb do\n'
        '    get "/a", AController, :a\n'
        '    scope alias: Inner do\n'
        '      get "/b", BController, :b\n'
        '    end\n'
        '  end')) == ["GET /p/a", "GET /p/b"]


def test_resources_take_the_scope_prefix(tmp_path: Path) -> None:
    assert _names(tmp_path, (
        '  scope "/admin", AppWeb do\n'
        '    resources "/posts", PostController, only: [:index, :show]\n'
        '  end')) == ["GET /admin/posts", "GET /admin/posts/:id"]


def test_nested_resources_sit_under_the_member_path(tmp_path: Path) -> None:
    assert _names(tmp_path, (
        '  scope "/", AppWeb do\n'
        '    resources "/users", UserController, only: [:show] do\n'
        '      resources "/posts", PostController, only: [:index]\n'
        '      get "/stats", StatsController, :index\n'
        '    end\n'
        '  end')) == [
        "GET /users/:id", "GET /users/:user_id/posts",
        "GET /users/:user_id/stats"]


def test_nested_member_segment_name_param_and_singleton(
    tmp_path: Path,
) -> None:
    assert _names(tmp_path, (
        '  resources "/a", Admin.UserPostController, only: [] do\n'
        '    get "/x", XController, :x\n'
        '  end\n'
        '  resources "/b", BController, only: [], param: "slug" do\n'
        '    get "/x", XController, :x\n'
        '  end\n'
        '  resources "/c", CController, only: [], name: "author" do\n'
        '    get "/x", XController, :x\n'
        '  end\n'
        '  resources "/account", AccountController, only: [], '
        'singleton: true do\n'
        '    get "/x", XController, :x\n'
        '  end\n'
        '  resources "/h", HTTPClientController, only: [] do\n'
        '    get "/x", XController, :x\n'
        '  end')) == [
        "GET /a/:user_post_id/x", "GET /account/x", "GET /b/:b_slug/x",
        "GET /c/:author_id/x", "GET /h/:http_client_id/x"]
