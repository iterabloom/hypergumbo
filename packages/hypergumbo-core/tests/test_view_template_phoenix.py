# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Phoenix view-template linker (WI-dajom).

Phoenix binds controller actions to templates by convention. Two layout
shapes are covered:

* **Phoenix 1.x**: ``MyAppWeb.UserController.show`` →
  ``lib/my_app_web/templates/user/show.html.{eex,heex,leex}``.
* **Phoenix 1.7+**: ``MyAppWeb.UserController.show`` and the parallel
  ``MyAppWeb.UserHTML.show`` →
  ``lib/my_app_web/controllers/user_html/show.html.heex``.

Out of scope: LiveView (``*_live.ex``) — separate linker if ever demanded.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.view_template_phoenix import (
    PhoenixStrategy,
    link_view_template_phoenix,
)


def _ex_module(name: str, path: str | None = None) -> Symbol:
    path = path or f"lib/{name.lower().replace('.', '/')}.ex"
    return Symbol(
        id=f"elixir:{path}:1-50:{name}:module",
        name=name,
        kind="module",
        language="elixir",
        path=path,
        span=Span(start_line=1, end_line=50, start_col=0, end_col=0),
        origin="elixir",
    )


def _ex_function(
    name: str,
    *,
    path: str | None = None,
    start: int = 5,
    modifiers: list[str] | None = None,
) -> Symbol:
    container = name.rsplit(".", 1)[0]
    path = path or f"lib/{container.lower().replace('.', '/')}.ex"
    return Symbol(
        id=f"elixir:{path}:{start}-{start + 3}:{name}:function",
        name=name,
        kind="function",
        language="elixir",
        path=path,
        span=Span(start_line=start, end_line=start + 3, start_col=0, end_col=0),
        modifiers=modifiers or [],
        origin="elixir",
    )


def _write(tmp_path: Path, rel: str, body: str = "tpl") -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


class TestPhoenix1xLayout:
    """Phoenix 1.x: ``Controller#action`` → ``templates/<resource>/<action>.html.eex``."""

    def test_eex_template(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/show.html.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")

        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == action.id
        assert edge.edge_type == "renders"
        assert (edge.meta or {}).get("detection_pattern") == "implicit_convention"
        assert edge.dst.endswith(
            "lib/my_app_web/templates/user/show.html.eex:1-1:"
            "show.html.eex:template"
        )

    def test_heex_template(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/show.html.heex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst.startswith("heex:")

    def test_leex_template(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/show.html.leex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst.startswith("leex:")

    def test_text_eex_template(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/show.text.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1

    def test_json_eex_template(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/show.json.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1

    def test_xml_eex_template(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/show.xml.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1


class TestPhoenix17Layout:
    """Phoenix 1.7+: co-located ``controllers/<resource>_html/<action>.html.heex``."""

    def test_controller_action_heex(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "lib/my_app_web/controllers/user_html/show.html.heex",
        )
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith(
            "controllers/user_html/show.html.heex:1-1:show.html.heex:template"
        )

    def test_html_module_function_component(self, tmp_path: Path) -> None:
        """``MyAppWeb.UserHTML.show`` (function-component shape) → same template."""
        _write(
            tmp_path,
            "lib/my_app_web/controllers/user_html/show.html.heex",
        )
        html_module = _ex_module(
            "MyAppWeb.UserHTML",
            path="lib/my_app_web/controllers/user_html.ex",
        )
        action = _ex_function(
            "MyAppWeb.UserHTML.show",
            path="lib/my_app_web/controllers/user_html.ex",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[html_module, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == action.id


class TestNamespacedControllers:
    """Sub-namespaces under the web app prefix get nested directories."""

    def test_admin_namespace_1x(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "lib/my_app_web/templates/admin/user/show.html.eex",
        )
        controller = _ex_module("MyAppWeb.Admin.UserController")
        action = _ex_function("MyAppWeb.Admin.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith(
            "templates/admin/user/show.html.eex:1-1:show.html.eex:template"
        )

    def test_admin_namespace_1_7(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "lib/my_app_web/controllers/admin/user_html/show.html.heex",
        )
        controller = _ex_module("MyAppWeb.Admin.UserController")
        action = _ex_function("MyAppWeb.Admin.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 1


class TestNonControllerSkipped:
    """Modules without a recognised suffix produce no edges."""

    def test_liveview_module_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/show.html.eex")
        live_module = _ex_module("MyAppWeb.UserLive")
        action = _ex_function("MyAppWeb.UserLive.mount")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[live_module, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []

    def test_genserver_module_skipped(self, tmp_path: Path) -> None:
        gen_module = _ex_module("MyApp.Worker")
        action = _ex_function("MyApp.Worker.handle_call")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[gen_module, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []

    def test_no_controllers_returns_empty(self, tmp_path: Path) -> None:
        ctx = LinkerContext(repo_root=tmp_path, symbols=[])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []
        assert result.symbols == []
        assert result.run is not None


class TestActionMethodFilters:
    """Specific function names are not Phoenix actions."""

    def test_init_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/init.html.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.init")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []

    def test_call_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/call.html.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.call")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []

    def test_action_function_skipped(self, tmp_path: Path) -> None:
        """Phoenix's ``action/2`` is the plug dispatcher, not a render target."""
        _write(tmp_path, "lib/my_app_web/templates/user/action.html.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.action")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []

    def test_underscore_prefixed_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/_helper.html.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController._helper")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []

    def test_private_function_skipped(self, tmp_path: Path) -> None:
        """``defp`` (private) functions can't be render targets."""
        _write(tmp_path, "lib/my_app_web/templates/user/show.html.eex")
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function(
            "MyAppWeb.UserController.show", modifiers=["private"]
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []


class TestMissingTemplate:
    """No template file → no edge."""

    def test_no_template_no_edge(self, tmp_path: Path) -> None:
        controller = _ex_module("MyAppWeb.UserController")
        action = _ex_function("MyAppWeb.UserController.show")
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, action])
        result = link_view_template_phoenix(ctx)
        assert result.edges == []


class TestMultipleActionsSameController:
    """Multiple actions on the same controller each get their own edge."""

    def test_index_and_show(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/my_app_web/templates/user/index.html.eex")
        _write(tmp_path, "lib/my_app_web/templates/user/show.html.heex")
        controller = _ex_module("MyAppWeb.UserController")
        idx = _ex_function("MyAppWeb.UserController.index", start=5)
        show = _ex_function("MyAppWeb.UserController.show", start=15)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[controller, idx, show])
        result = link_view_template_phoenix(ctx)
        assert len(result.edges) == 2


class TestPhoenixStrategyExposure:
    """The strategy class is public for downstream reuse."""

    def test_phoenix_strategy_class_exposed(self) -> None:
        from hypergumbo_core.linkers._view_template_core import (
            MethodNameStrategy,
            TemplateStrategy,
        )

        assert issubclass(PhoenixStrategy, MethodNameStrategy)
        assert issubclass(PhoenixStrategy, TemplateStrategy)
