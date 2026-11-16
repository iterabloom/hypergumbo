# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which symbol a route's handler name binds is a function of the source.

The route-handler linker kept one flat ``name -> Symbol`` table over every
symbol of every language, first write wins. ``symbols`` reaches a linker in
the order the analyzers' results were collected (thread completion order),
so the table's content changed from run to run. Measured on pretix: the
Django route ``^locale/set$`` (view ``LocaleSet``) was bound to the Python
view in one run and to ``class LocaleSet`` of a vendored ``pdf.worker.js``
in the next, on unchanged code.

Two defects in one: a route registered in a Python file could bind a
JavaScript class at all, and which candidate won was decided by list order.
A hash- or timing-order defect is invisible to a single run, so every test
here hands the linker the SAME symbols in BOTH orders and asserts one answer.
"""

from __future__ import annotations

import itertools

import pytest

from hypergumbo_core.analyze.base import make_route_symbol
from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.route_handler import link_routes_to_handlers


def _decl(
    language: str, path: str, line: int, name: str, kind: str,
    qualified_name: str | None = None, end_line: int | None = None,
) -> Symbol:
    end = end_line if end_line is not None else line + 2
    return Symbol(
        id=f"{language}:{path}:{line}-{end}:{name}:{kind}",
        name=name,
        qualified_name=qualified_name if qualified_name is not None else name,
        kind=kind,
        language=language,
        path=path,
        span=Span(start_line=line, end_line=end, start_col=0, end_col=0),
        origin="test",
        origin_run_id="test-run",
    )


def _route(language: str, path: str, **meta: str) -> Symbol:
    """A route marker as an analyzer mints it: ``language`` None, the host
    language in ``discovery_language`` (ADR-0031)."""
    return make_route_symbol(
        language=language,
        path=path,
        span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
        method="GET",
        route_path="/x",
        origin="test",
        origin_run_id="test-run",
        handler_ref=meta.pop("handler_ref", None),
        extra_meta=meta,
    )


def _link_every_order(route: Symbol, *declarations: Symbol) -> list[list[tuple]]:
    """``[(dst, confidence, fallback), ...]`` of the linker's edges, once per
    ordering of the symbols."""
    outcomes = []
    for order in itertools.permutations([route, *declarations]):
        result = link_routes_to_handlers(list(order), [])
        outcomes.append([
            (e.dst, e.confidence, (e.meta or {}).get("disambiguation_fallback"))
            for e in result.edges
        ])
    return outcomes


def _one_outcome(route: Symbol, *declarations: Symbol) -> list[tuple]:
    """The edges the linker emits, asserted identical for every ordering."""
    outcomes = _link_every_order(route, *declarations)
    assert all(o == outcomes[0] for o in outcomes), outcomes
    return outcomes[0]


class TestHostLanguage:
    PY_VIEW = ("python", "presale/views/locale.py", 47, "LocaleSet", "class")
    JS_CLASS = ("javascript", "static/pdfjs/pdf.worker.js", 60627, "LocaleSet", "class")

    def test_a_python_route_binds_the_python_view_whatever_the_order(self) -> None:
        route = _route("python", "presale/urls.py", view_name="LocaleSet")
        view = _decl(*self.PY_VIEW)
        js = _decl(*self.JS_CLASS)
        assert _one_outcome(route, view, js) == [(view.id, 0.9, None)]

    def test_a_python_route_never_binds_a_javascript_class(self) -> None:
        # The view lives outside the repository; a same-named class of
        # another language is not it.
        route = _route("python", "presale/urls.py", view_name="LocaleSet")
        assert _one_outcome(route, _decl(*self.JS_CLASS)) == []

    def test_javascript_and_typescript_are_one_candidate_set(self) -> None:
        route = _route("typescript", "src/routes.ts", handler_ref="listUsers")
        handler = _decl("javascript", "src/handlers.js", 3, "listUsers", "function")
        decoy = _decl("python", "tools/gen.py", 9, "listUsers", "function")
        assert _one_outcome(route, handler, decoy) == [(handler.id, 0.9, None)]

    def test_a_route_with_no_host_language_keeps_every_symbol(self) -> None:
        # Fabricated by a linker from a protocol definition (ADR-0031): no
        # host file, so no host language to restrict by.
        route = make_route_symbol(
            language="proto",
            path="api/users.proto",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=10),
            method="RPC",
            route_path="/users.Users/List",
            origin="test",
            origin_run_id="test-run",
            handler_ref="ListUsers",
            protocol_origin="grpc",
        )
        assert route.host_language is None
        handler = _decl("go", "server/users.go", 12, "ListUsers", "function")
        assert _one_outcome(route, handler) == [(handler.id, 0.9, None)]


class TestFullNameOutranksShortName:
    def test_a_bare_view_name_is_the_module_level_class_not_a_nested_one(self) -> None:
        # ``Outer.LoginView`` is declared FIRST in the file, so position alone
        # would pick it. The route says ``LoginView``: that is the full name
        # of the module-level class and only the short name of the nested one.
        route = _route("python", "app/urls.py", view_name="LoginView")
        nested = _decl(
            "python", "app/urls.py", 5, "LoginView", "class",
            qualified_name="Outer.LoginView",
        )
        top = _decl("python", "app/urls.py", 20, "LoginView", "class")
        assert _one_outcome(route, nested, top) == [(top.id, 0.9, None)]

    def test_a_rails_action_is_the_top_level_controllers(self) -> None:
        route = _route("ruby", "config/routes.rb", controller_action="users#index")
        namespaced = _decl(
            "ruby", "app/controllers/admin/users_controller.rb", 3,
            "UsersController#index", "method",
            qualified_name="Admin::UsersController#index",
        )
        top = _decl(
            "ruby", "app/controllers/users_controller.rb", 3,
            "UsersController#index", "method",
        )
        assert _one_outcome(route, namespaced, top) == [(top.id, 0.9, None)]

    def test_a_short_name_still_answers_when_no_full_name_does(self) -> None:
        route = _route("python", "app/urls.py", view_name="LoginView")
        nested = _decl(
            "python", "app/views.py", 5, "LoginView", "class",
            qualified_name="Outer.LoginView",
        )
        assert _one_outcome(route, nested) == [(nested.id, 0.9, None)]


class TestTotalOrderAmongEquals:
    def test_two_views_of_one_name_tie_by_path_and_are_flagged(self) -> None:
        route = _route("python", "urls.py", view_name="list_users")
        a = _decl("python", "accounts/views.py", 10, "list_users", "function")
        b = _decl("python", "admin/views.py", 10, "list_users", "function")
        assert _one_outcome(route, a, b) == [(a.id, 0.5, True)]

    def test_two_declarations_in_one_file_tie_by_span(self) -> None:
        route = _route("python", "urls.py", view_name="handler")
        first = _decl("python", "views.py", 10, "handler", "function")
        second = _decl("python", "views.py", 40, "handler", "function")
        assert _one_outcome(route, first, second) == [(first.id, 0.5, True)]

    def test_a_suffix_scan_takes_the_first_in_source_order(self) -> None:
        # Laravel's namespace match scans the table; the scan order is the
        # table's, which is the source's.
        route = _route("php", "routes/web.php", controller_action="UserController@index")
        a = _decl("php", "app/A/UserController.php", 5, "App\\A\\UserController.index", "method")
        b = _decl("php", "app/B/UserController.php", 5, "App\\B\\UserController.index", "method")
        [(dst, _confidence, _flag)] = _one_outcome(route, a, b)
        assert dst == a.id


class TestFallbackFlag:
    def test_a_unique_handler_is_not_its_own_collision(self) -> None:
        # ``name`` and ``qualified_name`` are the same string on every
        # module-level Python symbol: one candidate, not two.
        route = _route("python", "urls.py", view_name="only_view")
        view = _decl("python", "views.py", 4, "only_view", "function")
        assert view.name == view.qualified_name
        assert _one_outcome(route, view) == [(view.id, 0.9, None)]

    def test_a_handler_named_by_id_is_never_a_pick(self) -> None:
        resource = _decl("python", "web/app.py", 8, "Item", "class")
        twin = _decl("python", "web/other.py", 8, "Item", "class")
        route = _route("python", "web/app.py", handler_ref=resource.id, view_name="Item")
        assert _one_outcome(route, resource, twin) == [(resource.id, 0.9, None)]

    def test_an_id_of_a_root_level_file_is_an_id(self) -> None:
        # ``python:app.py:8-10:Item:class`` has no ``/``: it was read as a
        # NAME, matched nothing, and the route got no edge.
        resource = _decl("python", "app.py", 8, "Item", "class")
        route = _route("python", "app.py", handler_ref=resource.id, view_name="Item")
        assert "/" not in resource.id
        assert _one_outcome(route, resource) == [(resource.id, 0.9, None)]


class TestPhoenixLiveFallbackSkipsNonModules:
    def test_the_live_module_is_found_past_a_function_of_the_same_suffix(self) -> None:
        route = _route(
            "elixir", "lib/app_web/router.ex", controller="HomeLive",
            action="index", route_protocol="liveview",
        )
        helper = _decl("elixir", "lib/app_web/a.ex", 1, "AppWeb.Util.HomeLive", "function")
        module = _decl("elixir", "lib/app_web/live/home_live.ex", 1, "AppWeb.HomeLive", "module")
        [(dst, _confidence, _flag)] = _one_outcome(route, helper, module)
        assert dst == module.id


@pytest.mark.parametrize("seed_order", [0, 1])
def test_the_edge_set_does_not_depend_on_symbol_order(seed_order: int) -> None:
    """The whole pretix shape at once: several routes, same-named candidates
    in two languages and two files; the edge set is one set."""
    routes = [
        _route("python", "presale/urls.py", view_name="LocaleSet"),
        _route("python", "control/urls.py", view_name="index"),
    ]
    declarations = [
        _decl("python", "presale/views/locale.py", 47, "LocaleSet", "class"),
        _decl("javascript", "static/pdfjs/pdf.worker.js", 60627, "LocaleSet", "class"),
        _decl("python", "control/views/main.py", 5, "index", "function"),
        _decl("python", "presale/views/main.py", 5, "index", "function"),
        _decl("javascript", "static/js/ui.js", 1, "index", "function"),
    ]
    symbols = [*routes, *declarations]
    if seed_order:
        symbols.reverse()
    edges = {
        (e.src, e.dst) for e in link_routes_to_handlers(symbols, []).edges
    }
    assert edges == {
        (routes[0].id, declarations[0].id),
        (routes[1].id, declarations[2].id),
    }
