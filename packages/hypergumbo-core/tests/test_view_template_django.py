# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Django view-template linker (WI-mifif).

Three Django patterns produce ``renders`` edges:

* ``render(request, "<template>", ...)`` calls — explicit-string strategy
  parsing the Python AST inside view files.
* ``TemplateView.template_name = "<template>"`` class attribute — explicit
  string strategy reading a class-body assignment.
* ``DetailView`` / ``ListView`` / etc. with ``model = <Name>`` — method/
  class-name strategy deriving ``<app>/templates/<app>/<model>_detail.html``.

All three resolve template paths via filesystem probing in the shared core
(see ``_view_template_core.py``).
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.view_template_django import (
    DjangoCBVDefaultStrategy,
    DjangoExplicitStringStrategy,
    link_view_template_django,
)


def _py_function(
    path: str, name: str, start: int = 5, *, sym_kind: str = "function"
) -> Symbol:
    return Symbol(
        id=f"python:{path}:{start}-{start + 5}:{name}:{sym_kind}",
        name=name,
        kind=sym_kind,
        language="python",
        path=path,
        span=Span(start_line=start, end_line=start + 5, start_col=0, end_col=0),
        origin="python-v1",
    )


def _py_class(
    path: str,
    name: str,
    base_classes: list[str],
    start: int = 1,
    end: int = 30,
) -> Symbol:
    return Symbol(
        id=f"python:{path}:{start}-{end}:{name}:class",
        name=name,
        kind="class",
        language="python",
        path=path,
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
        meta={"base_classes": base_classes},
        origin="python-v1",
    )


def _write_views(tmp_path: Path, rel: str, source: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source)
    return p


def _write_template(tmp_path: Path, rel: str, content: str = "<h1>tpl</h1>") -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class TestRenderCallEdges:
    """``render(request, "<tpl>", ctx)`` produces a renders edge."""

    def test_render_call_with_app_template(self, tmp_path: Path) -> None:
        source = (
            "def user_view(request):\n"
            "    return render(request, 'users/show.html', {})\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/show.html")

        view_fn = _py_function("users/views.py", "user_view", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[view_fn])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == view_fn.id
        assert edge.edge_type == "renders"
        assert edge.evidence_type == "naming_convention"
        assert (edge.meta or {}).get("detection_pattern") == "render_call"
        assert edge.dst == (
            "html:users/templates/users/show.html:1-1:show.html:template"
        )

    def test_render_call_with_project_template(self, tmp_path: Path) -> None:
        source = (
            "def user_view(request):\n"
            "    return render(request, 'show.html', {})\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "templates/show.html")

        view_fn = _py_function("users/views.py", "user_view", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[view_fn])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith("templates/show.html:1-1:show.html:template")

    def test_render_call_missing_template_no_edge(self, tmp_path: Path) -> None:
        source = (
            "def user_view(request):\n"
            "    return render(request, 'users/missing.html', {})\n"
        )
        _write_views(tmp_path, "users/views.py", source)

        view_fn = _py_function("users/views.py", "user_view", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[view_fn])

        result = link_view_template_django(ctx)

        assert result.edges == []
        assert result.symbols == []

    def test_render_call_in_method(self, tmp_path: Path) -> None:
        source = (
            "class UserView(View):\n"
            "    def get(self, request):\n"
            "        return render(request, 'users/show.html', {})\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/show.html")

        klass = _py_class(
            "users/views.py", "UserView", ["View"], start=1, end=4
        )
        method = _py_function(
            "users/views.py",
            "UserView.get",
            start=2,
            sym_kind="method",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        # Edge attributed to the enclosing method symbol, not the class.
        assert result.edges[0].src == method.id

    def test_non_view_file_skipped(self, tmp_path: Path) -> None:
        """``render`` outside a Django views file should not create an edge.

        Heuristic: the linker only scans Python files that look like Django
        views (path matches ``views.py`` or ``views/``). Random utility
        modules that happen to call a function named ``render`` are skipped.
        """
        source = (
            "def helper(request):\n"
            "    return render(request, 'show.html', {})\n"
        )
        _write_views(tmp_path, "utils/helpers.py", source)
        _write_template(tmp_path, "templates/show.html")

        fn = _py_function("utils/helpers.py", "helper", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)

        assert result.edges == []

    def test_render_in_subdir_views_package(self, tmp_path: Path) -> None:
        """``users/views/details.py`` is recognized as a Django views file."""
        source = (
            "def detail(request):\n"
            "    return render(request, 'users/detail.html', {})\n"
        )
        _write_views(tmp_path, "users/views/details.py", source)
        _write_template(tmp_path, "users/templates/users/detail.html")

        fn = _py_function("users/views/details.py", "detail", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1


class TestTemplateNameAttribute:
    """``class Foo(TemplateView): template_name = "..."`` produces an edge."""

    def test_template_name_string_attr(self, tmp_path: Path) -> None:
        source = (
            "class HomeView(TemplateView):\n"
            "    template_name = 'home/index.html'\n"
        )
        _write_views(tmp_path, "home/views.py", source)
        _write_template(tmp_path, "home/templates/home/index.html")

        klass = _py_class("home/views.py", "HomeView", ["TemplateView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == klass.id
        assert (edge.meta or {}).get("detection_pattern") == "template_name_attribute"
        assert edge.dst.endswith("home/index.html:1-1:index.html:template")

    def test_template_name_non_string_skipped(self, tmp_path: Path) -> None:
        """``template_name = some_var`` cannot be statically resolved."""
        source = (
            "class HomeView(TemplateView):\n"
            "    template_name = SETTINGS_TEMPLATE\n"
        )
        _write_views(tmp_path, "home/views.py", source)

        klass = _py_class("home/views.py", "HomeView", ["TemplateView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert result.edges == []


class TestModelDerivedDefaults:
    """DetailView / ListView default templates derived from ``model = X``."""

    def test_detail_view_default(self, tmp_path: Path) -> None:
        source = (
            "class UserDetailView(DetailView):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/user_detail.html")

        klass = _py_class(
            "users/views.py", "UserDetailView", ["DetailView"]
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == klass.id
        assert (edge.meta or {}).get("detection_pattern") == "cbv_default_template"
        assert edge.dst.endswith("user_detail.html:1-1:user_detail.html:template")

    def test_list_view_default(self, tmp_path: Path) -> None:
        source = (
            "class UserListView(ListView):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/user_list.html")

        klass = _py_class("users/views.py", "UserListView", ["ListView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith("user_list.html:1-1:user_list.html:template")

    def test_create_view_form_template(self, tmp_path: Path) -> None:
        source = (
            "class UserCreateView(CreateView):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/user_form.html")

        klass = _py_class("users/views.py", "UserCreateView", ["CreateView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith("user_form.html:1-1:user_form.html:template")

    def test_delete_view_confirm_template(self, tmp_path: Path) -> None:
        source = (
            "class UserDeleteView(DeleteView):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(
            tmp_path, "users/templates/users/user_confirm_delete.html"
        )

        klass = _py_class("users/views.py", "UserDeleteView", ["DeleteView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith(
            "user_confirm_delete.html:1-1:user_confirm_delete.html:template"
        )

    def test_cbv_without_model_skipped(self, tmp_path: Path) -> None:
        source = (
            "class UserDetailView(DetailView):\n"
            "    queryset = User.objects.all()\n"
        )
        _write_views(tmp_path, "users/views.py", source)

        klass = _py_class("users/views.py", "UserDetailView", ["DetailView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert result.edges == []

    def test_non_cbv_class_skipped(self, tmp_path: Path) -> None:
        source = (
            "class NotAView(object):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)

        klass = _py_class("users/views.py", "NotAView", ["object"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)

        assert result.edges == []

    def test_transitive_cbv_base_detected(self, tmp_path: Path) -> None:
        """``BaseDetail(DetailView)`` then ``MyDetail(BaseDetail)`` still resolves."""
        source = (
            "class MyDetail(BaseDetail):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/user_detail.html")

        base_detail = _py_class(
            "users/base.py", "BaseDetail", ["DetailView"], start=1, end=10
        )
        my_detail = _py_class(
            "users/views.py", "MyDetail", ["BaseDetail"], start=1, end=10
        )
        edges = [
            Edge.create(src=my_detail.id, dst=base_detail.id, edge_type="extends", line=1),
        ]
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[base_detail, my_detail],
            edges=edges,
        )

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == my_detail.id


class TestDjangoLinkerIntegration:
    """End-to-end via ``link_view_template_django``."""

    def test_no_django_views_returns_empty(self, tmp_path: Path) -> None:
        ctx = LinkerContext(repo_root=tmp_path, symbols=[])
        result = link_view_template_django(ctx)
        assert result.edges == []
        assert result.symbols == []
        assert result.run is not None

    def test_combination_render_and_template_name(self, tmp_path: Path) -> None:
        source = (
            "def home(request):\n"
            "    return render(request, 'home/index.html', {})\n"
            "\n"
            "class AboutView(TemplateView):\n"
            "    template_name = 'home/about.html'\n"
        )
        _write_views(tmp_path, "home/views.py", source)
        _write_template(tmp_path, "home/templates/home/index.html")
        _write_template(tmp_path, "home/templates/home/about.html")

        home_fn = _py_function("home/views.py", "home", start=1)
        about_klass = _py_class(
            "home/views.py", "AboutView", ["TemplateView"], start=4, end=5
        )
        ctx = LinkerContext(
            repo_root=tmp_path, symbols=[home_fn, about_klass]
        )

        result = link_view_template_django(ctx)

        # Two distinct edges, two distinct template symbols.
        assert len(result.edges) == 2
        assert len(result.symbols) == 2
        patterns = {(e.meta or {}).get("detection_pattern") for e in result.edges}
        assert patterns == {"render_call", "template_name_attribute"}

    def test_strategy_classes_exposed(self) -> None:
        """Strategy classes are public for downstream and sister-item reuse."""
        from hypergumbo_core.linkers._view_template_core import (
            ExplicitStringStrategy,
            TemplateStrategy,
        )

        assert issubclass(DjangoExplicitStringStrategy, ExplicitStringStrategy)
        assert issubclass(DjangoCBVDefaultStrategy, TemplateStrategy)


class TestRenderCallEdgeCases:
    """Edge cases in render-call detection."""

    def test_module_attribute_render(self, tmp_path: Path) -> None:
        """``shortcuts.render(request, "x.html", ctx)`` works."""
        source = (
            "def view_fn(request):\n"
            "    return shortcuts.render(request, 'show.html', {})\n"
        )
        _write_views(tmp_path, "app/views.py", source)
        _write_template(tmp_path, "app/templates/show.html")

        fn = _py_function("app/views.py", "view_fn", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert len(result.edges) == 1

    def test_render_with_one_arg_skipped(self, tmp_path: Path) -> None:
        """``render(request)`` is invalid; no template arg to extract."""
        source = (
            "def view_fn(request):\n"
            "    return render(request)\n"
        )
        _write_views(tmp_path, "app/views.py", source)

        fn = _py_function("app/views.py", "view_fn", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_render_with_non_string_template_arg_skipped(
        self, tmp_path: Path
    ) -> None:
        """``render(request, some_var, ctx)`` cannot be statically resolved."""
        source = (
            "def view_fn(request):\n"
            "    return render(request, get_template_path(), {})\n"
        )
        _write_views(tmp_path, "app/views.py", source)

        fn = _py_function("app/views.py", "view_fn", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_module_level_render_skipped(self, tmp_path: Path) -> None:
        """``render()`` at module scope has no enclosing function symbol.

        A function symbol exists in the file but its span (lines 1-3) doesn't
        contain the line-5 module-level render call.
        """
        source = (
            "def helper(x):\n"
            "    return x\n"
            "\n"
            "\n"
            "y = render(request, 'show.html', {})\n"
        )
        _write_views(tmp_path, "app/views.py", source)
        _write_template(tmp_path, "app/templates/show.html")

        fn = Symbol(
            id="python:app/views.py:1-3:helper:function",
            name="helper",
            kind="function",
            language="python",
            path="app/views.py",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
            origin="python-v1",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_unrelated_call_skipped(self, tmp_path: Path) -> None:
        """Calls to non-``render`` names don't trigger."""
        source = (
            "def view_fn(request):\n"
            "    return JsonResponse({'ok': True})\n"
        )
        _write_views(tmp_path, "app/views.py", source)

        fn = _py_function("app/views.py", "view_fn", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert result.edges == []


class TestTemplateNameEdgeCases:
    """Edge cases in ``template_name = "..."`` detection."""

    def test_multi_target_assign_skipped(self, tmp_path: Path) -> None:
        """``a = template_name = "..."`` has multiple targets; conservative skip."""
        source = (
            "class HomeView(TemplateView):\n"
            "    a = template_name = 'home/index.html'\n"
        )
        _write_views(tmp_path, "home/views.py", source)
        _write_template(tmp_path, "home/templates/home/index.html")

        klass = _py_class("home/views.py", "HomeView", ["TemplateView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_class_body_attribute_target_skipped(self, tmp_path: Path) -> None:
        """``OtherClass.template_name = "..."`` at class body has an Attribute
        target (not a Name); ``_template_name_sites`` conservatively skips."""
        source = (
            "class HomeView(TemplateView):\n"
            "    Other.template_name = 'home/index.html'\n"
        )
        _write_views(tmp_path, "home/views.py", source)

        klass = _py_class("home/views.py", "HomeView", ["TemplateView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_template_name_without_class_symbol_skipped(
        self, tmp_path: Path
    ) -> None:
        """A ``template_name`` class-body attr without a matching class Symbol
        in the linker context is skipped (covers ``_class_symbol_for_lineno``
        returning None)."""
        source = (
            "class HomeView(TemplateView):\n"
            "    template_name = 'home/index.html'\n"
        )
        _write_views(tmp_path, "home/views.py", source)
        _write_template(tmp_path, "home/templates/home/index.html")

        # A function symbol exists in the file (so the file gets scanned)
        # but the HomeView class symbol is absent — so the linker can't attribute
        # the renders edge.
        fn = _py_function("home/views.py", "helper", start=4)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_other_class_attr_ignored(self, tmp_path: Path) -> None:
        """``foo = "..."`` is not ``template_name``."""
        source = (
            "class HomeView(TemplateView):\n"
            "    foo = 'home/index.html'\n"
        )
        _write_views(tmp_path, "home/views.py", source)

        klass = _py_class("home/views.py", "HomeView", ["TemplateView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert result.edges == []


class TestCBVDefaultEdgeCases:
    """Edge cases for the model-derived CBV default template strategy."""

    def test_nested_function_picks_innermost_enclosing(
        self, tmp_path: Path
    ) -> None:
        """When an outer + inner function both contain a render call line,
        the innermost (shorter span) wins."""
        source = (
            "def outer(request):\n"
            "    def inner(req2):\n"
            "        return render(req2, 'inner.html', {})\n"
            "    return inner(request)\n"
        )
        _write_views(tmp_path, "app/views.py", source)
        _write_template(tmp_path, "app/templates/inner.html")

        outer = Symbol(
            id="python:app/views.py:1-4:outer:function",
            name="outer",
            kind="function",
            language="python",
            path="app/views.py",
            span=Span(start_line=1, end_line=4, start_col=0, end_col=0),
            origin="python-v1",
        )
        inner = Symbol(
            id="python:app/views.py:2-3:inner:function",
            name="inner",
            kind="function",
            language="python",
            path="app/views.py",
            span=Span(start_line=2, end_line=3, start_col=4, end_col=0),
            origin="python-v1",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[outer, inner])

        result = link_view_template_django(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].src == inner.id

    def test_template_view_without_defaults_skipped(
        self, tmp_path: Path
    ) -> None:
        """``TemplateView`` itself has no model-derived default; only
        DetailView/ListView/Create/Update/Delete/FormView do."""
        source = (
            "class HomeView(TemplateView):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "home/views.py", source)

        klass = _py_class("home/views.py", "HomeView", ["TemplateView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_unparseable_cbv_source_skipped(self, tmp_path: Path) -> None:
        """A CBV class whose source file is malformed produces no edges."""
        _write_views(tmp_path, "broken/views.py", "class X(DetailView):\n    model = (\n")

        klass = _py_class("broken/views.py", "BrokenView", ["DetailView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_multi_class_file_skips_non_matching_classdef(
        self, tmp_path: Path
    ) -> None:
        """``_find_model_attr`` walks all ClassDefs but skips ones that don't
        match the target class name (line 402 of view_template_django.py)."""
        source = (
            "class Other:\n"
            "    model = SomeOtherModel\n"
            "\n"
            "class UserDetailView(DetailView):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/user_detail.html")

        klass = _py_class(
            "users/views.py",
            "UserDetailView",
            ["DetailView"],
            start=4,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert len(result.edges) == 1

    def test_class_body_with_method_def_walks_past(
        self, tmp_path: Path
    ) -> None:
        """``def`` statements inside a CBV body are skipped by the model lookup."""
        source = (
            "class UserDetailView(DetailView):\n"
            "    def get_object(self):\n"
            "        return None\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)
        _write_template(tmp_path, "users/templates/users/user_detail.html")

        klass = _py_class(
            "users/views.py", "UserDetailView", ["DetailView"], end=4
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert len(result.edges) == 1

    def test_multi_target_model_assign_skipped(self, tmp_path: Path) -> None:
        """``a = model = User`` is multi-target; ``_find_model_attr`` skips it."""
        source = (
            "class UserDetailView(DetailView):\n"
            "    a = model = User\n"
        )
        _write_views(tmp_path, "users/views.py", source)

        klass = _py_class("users/views.py", "UserDetailView", ["DetailView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_cbv_in_non_views_file_uses_project_templates(
        self, tmp_path: Path
    ) -> None:
        """A CBV defined outside the views.py convention still gets a
        project-level template probe (covers ``_app_dir_name`` → None)."""
        source = (
            "class UserDetailView(DetailView):\n"
            "    model = User\n"
        )
        _write_views(tmp_path, "services.py", source)
        # CBV defaults always include the project-level templates/ probe.
        _write_template(tmp_path, "templates/user_detail.html")

        klass = _py_class("services.py", "UserDetailView", ["DetailView"])
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith(
            "templates/user_detail.html:1-1:user_detail.html:template"
        )

    def test_multiword_camelcase_model(self, tmp_path: Path) -> None:
        source = (
            "class BlogPostDetailView(DetailView):\n"
            "    model = BlogPost\n"
        )
        _write_views(tmp_path, "blog/views.py", source)
        _write_template(tmp_path, "blog/templates/blog/blog_post_detail.html")

        klass = _py_class(
            "blog/views.py", "BlogPostDetailView", ["DetailView"]
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])

        result = link_view_template_django(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith(
            "blog_post_detail.html:1-1:blog_post_detail.html:template"
        )


class TestPathHeuristicEdgeCases:
    """Edge cases in the Django-view-path heuristic + app-dir derivation."""

    def test_view_file_at_repo_root_views_py(self, tmp_path: Path) -> None:
        """``views.py`` at the repo root has no parent app directory."""
        source = (
            "def view_fn(request):\n"
            "    return render(request, 'show.html', {})\n"
        )
        _write_views(tmp_path, "views.py", source)
        _write_template(tmp_path, "templates/show.html")

        fn = _py_function("views.py", "view_fn", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        # Project-level templates/ resolution still works even without an app dir.
        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith("templates/show.html:1-1:show.html:template")

    def test_view_file_under_views_at_repo_root(self, tmp_path: Path) -> None:
        """``views/foo.py`` at repo root has no parent app directory."""
        source = (
            "def view_fn(request):\n"
            "    return render(request, 'show.html', {})\n"
        )
        _write_views(tmp_path, "views/foo.py", source)
        _write_template(tmp_path, "templates/show.html")

        fn = _py_function("views/foo.py", "view_fn", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith("templates/show.html:1-1:show.html:template")

    def test_non_python_symbol_skipped(self, tmp_path: Path) -> None:
        """Non-Python symbols in the linker context are skipped."""
        ruby_sym = Symbol(
            id="ruby:app/controllers/users.rb:1-10:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/controllers/users.rb",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="ruby-v1",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[ruby_sym])
        result = link_view_template_django(ctx)
        assert result.edges == []


class TestUnparseableSourceTolerated:
    """Linker must not crash on syntax errors in a candidate views file."""

    def test_syntax_error_skipped(self, tmp_path: Path) -> None:
        _write_views(tmp_path, "broken/views.py", "def broken( \n")

        fn = _py_function("broken/views.py", "anything", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        # Should produce no edge but also not raise.
        result = link_view_template_django(ctx)
        assert result.edges == []

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        """Symbol claims a path that doesn't exist on disk — tolerate."""
        fn = _py_function("absent/views.py", "anything", start=1)
        ctx = LinkerContext(repo_root=tmp_path, symbols=[fn])

        result = link_view_template_django(ctx)
        assert result.edges == []
