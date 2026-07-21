# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Laravel Blade view-template linker (WI-hokaj).

Laravel controllers render templates via the ``view()`` helper or the
``View::make()`` facade. The string argument is a dotted view name; Laravel
maps dots to directory separators under ``resources/views/``:

* ``view('users.show')`` → ``resources/views/users/show.blade.php``.
* ``View::make('users.show')`` → same.

Action gating: classes whose file path matches ``app/Http/Controllers/``
(or an analogous controller directory) AND that extend ``Controller``.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.registry import LinkerContext
from hypergumbo_core.linkers.view_template_laravel import (
    LaravelStrategy,
    link_view_template_laravel,
)


def _php_class(
    name: str,
    *,
    path: str,
    base_classes: list[str] | None = None,
    start: int = 1,
    end: int = 30,
) -> Symbol:
    meta: dict[str, object] = {}
    if base_classes is not None:
        meta["base_classes"] = base_classes
    return Symbol(
        id=f"php:{path}:{start}-{end}:{name}:class",
        name=name,
        kind="class",
        language="php",
        path=path,
        span=Span(start_line=start, end_line=end, start_col=0, end_col=0),
        meta=meta or None,
        origin="php",
    )


def _php_method(
    name: str, *, path: str, start: int = 5, end: int = 10
) -> Symbol:
    return Symbol(
        id=f"php:{path}:{start}-{end}:{name}:method",
        name=name,
        kind="method",
        language="php",
        path=path,
        span=Span(start_line=start, end_line=end, start_col=2, end_col=2),
        origin="php",
    )


def _write(tmp_path: Path, rel: str, body: str = "tpl") -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


class TestViewHelperCall:
    """``view('users.show', $data)`` produces a renders edge."""

    def test_basic_view_call(self, tmp_path: Path) -> None:
        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show($id) {\n"
            "        return view('users.show', ['user' => $user]);\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=7,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.src == method.id
        assert edge.edge_type == "references"
        assert (edge.meta or {}).get("ref_construct") == "view_render"
        assert (edge.meta or {}).get("detection_pattern") == "view_helper_call"
        assert edge.dst.endswith(
            "resources/views/users/show.blade.php:1-1:show.blade.php:template"
        )

    def test_view_call_without_data_arg(self, tmp_path: Path) -> None:
        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() {\n"
            "        return view('users.show');\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=7,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert len(result.edges) == 1

    def test_plain_php_fallback(self, tmp_path: Path) -> None:
        """``.php`` (without ``.blade.``) is a recognised template extension."""
        _write(tmp_path, "resources/views/users/show.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() { return view('users.show'); }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=5,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith("show.php:1-1:show.php:template")


class TestViewFacade:
    """``View::make('users.show')`` produces a renders edge."""

    def test_view_make(self, tmp_path: Path) -> None:
        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() {\n"
            "        return View::make('users.show', ['user' => $user]);\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=7,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert (edge.meta or {}).get("detection_pattern") == "view_facade_make"


class TestDotToSlashMapping:
    """``view('a.b.c.d')`` resolves to ``resources/views/a/b/c/d.blade.php``."""

    def test_nested_directories(self, tmp_path: Path) -> None:
        _write(tmp_path, "resources/views/admin/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class AdminUserController extends Controller {\n"
            "    public function show() { return view('admin.users.show'); }\n"
            "}\n"
        )
        _write(
            tmp_path,
            "app/Http/Controllers/Admin/UserController.php",
            php_source,
        )

        klass = _php_class(
            "AdminUserController",
            path="app/Http/Controllers/Admin/UserController.php",
            base_classes=["Controller"],
            end=5,
        )
        method = _php_method(
            "AdminUserController.show",
            path="app/Http/Controllers/Admin/UserController.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith(
            "resources/views/admin/users/show.blade.php:1-1:"
            "show.blade.php:template"
        )

    def test_no_dots_top_level(self, tmp_path: Path) -> None:
        _write(tmp_path, "resources/views/welcome.blade.php")
        php_source = (
            "<?php\n"
            "class WelcomeController extends Controller {\n"
            "    public function show() { return view('welcome'); }\n"
            "}\n"
        )
        _write(
            tmp_path, "app/Http/Controllers/WelcomeController.php", php_source
        )

        klass = _php_class(
            "WelcomeController",
            path="app/Http/Controllers/WelcomeController.php",
            base_classes=["Controller"],
            end=5,
        )
        method = _php_method(
            "WelcomeController.show",
            path="app/Http/Controllers/WelcomeController.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert len(result.edges) == 1
        assert result.edges[0].dst.endswith(
            "resources/views/welcome.blade.php:1-1:welcome.blade.php:template"
        )


class TestActionGating:
    """Only controllers under ``app/Http/Controllers/`` with ``Controller`` base are scanned."""

    def test_non_controller_path_skipped(self, tmp_path: Path) -> None:
        """``view()`` call in a non-controller file is ignored."""
        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class Helper {\n"
            "    public function build() { return view('users.show'); }\n"
            "}\n"
        )
        _write(tmp_path, "app/Helpers/Helper.php", php_source)

        klass = _php_class(
            "Helper",
            path="app/Helpers/Helper.php",
            base_classes=["Controller"],  # base class, but wrong directory
            end=5,
        )
        method = _php_method(
            "Helper.build",
            path="app/Helpers/Helper.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert result.edges == []

    def test_no_controller_base_skipped(self, tmp_path: Path) -> None:
        """Class under controllers dir but no ``Controller`` base."""
        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class StrangeClass {\n"
            "    public function show() { return view('users.show'); }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/StrangeClass.php", php_source)

        klass = _php_class(
            "StrangeClass",
            path="app/Http/Controllers/StrangeClass.php",
            base_classes=["Model"],  # not a Controller subclass
            end=5,
        )
        method = _php_method(
            "StrangeClass.show",
            path="app/Http/Controllers/StrangeClass.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)

        assert result.edges == []


class TestEdgeCases:
    def test_no_template_no_edge(self, tmp_path: Path) -> None:
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() { return view('users.missing'); }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=5,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)
        assert result.edges == []

    def test_non_string_view_arg_skipped(self, tmp_path: Path) -> None:
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() { return view($name); }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)
        _write(tmp_path, "resources/views/something.blade.php")

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=5,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)
        assert result.edges == []

    def test_view_call_outside_method_skipped(self, tmp_path: Path) -> None:
        """A ``view(...)`` call not inside any tracked method is skipped."""
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "}\n"
            "$x = view('top.level');\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)
        _write(tmp_path, "resources/views/top/level.blade.php")

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=3,
        )
        # No method symbols on this controller.
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])
        result = link_view_template_laravel(ctx)
        assert result.edges == []

    def test_no_symbols_returns_empty(self, tmp_path: Path) -> None:
        ctx = LinkerContext(repo_root=tmp_path, symbols=[])
        result = link_view_template_laravel(ctx)
        assert result.edges == []
        assert result.symbols == []
        assert result.run is not None

    def test_non_php_symbol_skipped(self, tmp_path: Path) -> None:
        ruby_sym = Symbol(
            id="ruby:app/controllers/users.rb:1-10:UsersController:class",
            name="UsersController",
            kind="class",
            language="ruby",
            path="app/controllers/users.rb",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="ruby",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[ruby_sym])
        result = link_view_template_laravel(ctx)
        assert result.edges == []

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass])
        result = link_view_template_laravel(ctx)
        assert result.edges == []

    def test_multi_method_class_picks_matching_method(
        self, tmp_path: Path
    ) -> None:
        """Multiple methods in same class; only the one with the matching
        symbol is searched for view() calls (covers
        ``_find_method_by_short_name_and_line`` name-mismatch continue).

        Targets ``index`` (first in source order); the LIFO walk reaches
        ``show`` first, mismatches the requested name, and continues past
        it before finding ``index``.
        """
        _write(tmp_path, "resources/views/users/index.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function index() { return view('users.index'); }\n"
            "    public function show() { return view('users.show'); }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=6,
        )
        index_method = _php_method(
            "UserController.index",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, index_method])
        result = link_view_template_laravel(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == index_method.id

    def test_unrelated_function_call_skipped(self, tmp_path: Path) -> None:
        """``auth()`` (not ``view()``) in a method body is ignored."""
        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() {\n"
            "        $user = auth()->user();\n"
            "        return view('users.show');\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=7,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=6,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)
        assert len(result.edges) == 1

    def test_unrelated_scoped_call_skipped(self, tmp_path: Path) -> None:
        """``DB::table(...)`` (not ``View::make``) is ignored."""
        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() {\n"
            "        DB::table('users')->get();\n"
            "        return view('users.show');\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=7,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=6,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)
        assert len(result.edges) == 1

    def test_view_facade_make_with_variable_arg_skipped(
        self, tmp_path: Path
    ) -> None:
        """``View::make($name)`` cannot be statically resolved."""
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() {\n"
            "        return View::make($name, $data);\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=6,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=5,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)
        assert result.edges == []

    def test_method_on_non_controller_class_skipped(
        self, tmp_path: Path
    ) -> None:
        """Method on a non-controller class in same project is ignored
        (covers the ``class_part not in controller_classes`` continue when
        controller_classes is non-empty)."""
        _write(tmp_path, "resources/views/users/show.blade.php")
        controller_php = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function show() { return view('users.show'); }\n"
            "}\n"
        )
        helper_php = (
            "<?php\n"
            "class Helper {\n"
            "    public function build() { return view('other.tpl'); }\n"
            "}\n"
        )
        _write(
            tmp_path, "app/Http/Controllers/UserController.php", controller_php
        )
        _write(tmp_path, "app/Helpers/Helper.php", helper_php)

        controller = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=5,
        )
        controller_method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=3,
        )
        helper_method = _php_method(
            "Helper.build",
            path="app/Helpers/Helper.php",
            start=3,
            end=3,
        )
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[controller, controller_method, helper_method],
        )
        result = link_view_template_laravel(ctx)
        assert len(result.edges) == 1
        assert result.edges[0].src == controller_method.id

    def test_method_with_missing_source_file_skipped(
        self, tmp_path: Path
    ) -> None:
        """Symbol references a file that doesn't exist on disk."""
        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)
        assert result.edges == []

    def test_multiple_view_calls_in_method(self, tmp_path: Path) -> None:
        """A method that returns one of two views (e.g. conditional render)."""
        _write(tmp_path, "resources/views/users/show.blade.php")
        _write(tmp_path, "resources/views/users/edit.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends Controller {\n"
            "    public function dispatch($mode) {\n"
            "        if ($mode == 'show') return view('users.show');\n"
            "        return view('users.edit');\n"
            "    }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["Controller"],
            end=7,
        )
        method = _php_method(
            "UserController.dispatch",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=6,
        )
        ctx = LinkerContext(repo_root=tmp_path, symbols=[klass, method])
        result = link_view_template_laravel(ctx)
        assert len(result.edges) == 2

    def test_transitive_controller_base(self, tmp_path: Path) -> None:
        """``UserController extends BaseController extends Controller``."""
        from hypergumbo_core.ir import Edge

        _write(tmp_path, "resources/views/users/show.blade.php")
        php_source = (
            "<?php\n"
            "class UserController extends BaseController {\n"
            "    public function show() { return view('users.show'); }\n"
            "}\n"
        )
        _write(tmp_path, "app/Http/Controllers/UserController.php", php_source)

        base = _php_class(
            "BaseController",
            path="app/Http/Controllers/BaseController.php",
            base_classes=["Controller"],
            start=1,
            end=10,
        )
        klass = _php_class(
            "UserController",
            path="app/Http/Controllers/UserController.php",
            base_classes=["BaseController"],
            end=5,
        )
        method = _php_method(
            "UserController.show",
            path="app/Http/Controllers/UserController.php",
            start=3,
            end=3,
        )
        edges = [
            Edge.create(src=klass.id, dst=base.id, edge_type="extends", line=1, origin="test", origin_run_id="test"),
        ]
        ctx = LinkerContext(
            repo_root=tmp_path, symbols=[klass, base, method], edges=edges
        )
        result = link_view_template_laravel(ctx)
        assert len(result.edges) == 1


class TestStrategyExposure:
    def test_laravel_strategy_class_exposed(self) -> None:
        from hypergumbo_core.linkers._view_template_core import (
            ExplicitStringStrategy,
            TemplateStrategy,
        )

        assert issubclass(LaravelStrategy, ExplicitStringStrategy)
        assert issubclass(LaravelStrategy, TemplateStrategy)
