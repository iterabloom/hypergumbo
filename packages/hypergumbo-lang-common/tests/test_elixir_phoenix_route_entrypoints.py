# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phoenix router routes are HTTP-route entrypoints (INV-liraj).

Production-path test: a real Phoenix router file goes through the full
``run_behavior_map`` pipeline (Elixir analyzer -> framework enrichment ->
entrypoint detection), and every route marker the analyzer mints must come
out as an ``http_route`` entrypoint, never as a ``library_export``.

Before the fix the route markers (kind="function", meta.framework_role=
"route") also matched the Elixir "public function" library-export pattern;
the concept pass emitted LIBRARY_EXPORT first, the per-symbol dedup kept the
first-emitted entry, and the sketch's HTTP Routes section showed none of
the app's routes.
"""
import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map

ROUTER = """\
defmodule AppWeb.Router do
  use AppWeb, :router

  pipeline :browser do
    plug :accepts, ["html"]
  end

  scope "/", AppWeb do
    pipe_through :browser

    get "/", PageController, :home
    post "/login", SessionController, :create
    live "/dashboard", DashboardLive, :index
    resources "/posts", PostController
  end
end
"""

CONTROLLER = """\
defmodule AppWeb.PageController do
  use AppWeb, :controller
  def home(conn, _params), do: render(conn, :home)
end
"""


def _analyze(tmp_path: Path) -> dict:
    (tmp_path / "mix.exs").write_text(
        'defmodule App.MixProject do\n  use Mix.Project\n'
        '  def project, do: [app: :app, deps: [{:phoenix, "~> 1.7"}]]\nend\n'
    )
    web = tmp_path / "lib" / "app_web"
    web.mkdir(parents=True)
    (web / "router.ex").write_text(ROUTER)
    (web / "page_controller.ex").write_text(CONTROLLER)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out,
                     include_sketch_precomputed=False)
    return json.loads(out.read_text())


def test_every_phoenix_route_marker_is_an_http_route_entrypoint(
    tmp_path: Path,
) -> None:
    data = _analyze(tmp_path)
    markers = {
        n["id"]: n for n in data["nodes"]
        if (n.get("meta") or {}).get("framework_role") == "route"
    }
    # get + post + live + 7 resources actions
    assert len(markers) == 10, sorted(n["name"] for n in markers.values())
    kind_by_symbol = {e["symbol_id"]: e["kind"] for e in data["entrypoints"]}
    kinds = {markers[sid]["name"]: kind_by_symbol.get(sid) for sid in markers}
    assert set(kinds.values()) == {"http_route"}, kinds
    labels = {e["label"] for e in data["entrypoints"]
              if e["kind"] == "http_route"}
    assert {"HTTP GET /", "HTTP POST /login", "HTTP GET /posts/:id"} <= labels


def test_phoenix_route_marker_carries_no_library_export_concept(
    tmp_path: Path,
) -> None:
    data = _analyze(tmp_path)
    for node in data["nodes"]:
        meta = node.get("meta") or {}
        if meta.get("framework_role") != "route":
            continue
        concepts = [c.get("concept") for c in meta.get("concepts", [])]
        assert "library_export" not in concepts, (node["name"], concepts)
    # The real public API (controller action) is still a library export.
    home = next(n for n in data["nodes"]
                if n["name"] == "AppWeb.PageController.home")
    assert "library_export" in [
        c.get("concept") for c in home["meta"].get("concepts", [])
    ]
