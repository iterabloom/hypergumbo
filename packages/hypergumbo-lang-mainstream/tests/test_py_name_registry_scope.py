# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Python name registry keys a declaration by its full scope chain, and a
bare name means only what Python lets it mean.

A class nested in another class, or declared in a function, used to be
registered in the file's ``symbol_by_name`` under its BARE name, last write
wins. A later ``Outer.LoginView`` therefore took over every bare
``LoginView`` in the file: a route registration naming the module-level view
recorded the nested class as its handler, and a module-level ``Thing()``
constructed a class that only exists inside some other function.

The rule the registry now follows is Python's own:

* a bare name used at module level or in a function is a binding of an
  enclosing FUNCTION or of the module -- a class body is not an enclosing
  scope for the functions in it;
* a nested class is reached through its owner (``Outer.LoginView``), and a
  dotted name is walked from what its ROOT resolves to, never answered by its
  last segment;
* a function-local class is visible by bare name inside that function only.

Each fixture declares BOTH candidates and asserts which one is bound: a
name-keyed table keeps one symbol per name, so a one-candidate fixture cannot
tell the registries apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.py import (
    _extract_file_analysis,
    analyze_python,
    extract_nodes,
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, src in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)


def _sym(symbols, name: str, line: int, kind: str | None = None):
    found = [
        s for s in symbols
        if s.name == name and s.span is not None and s.span.start_line == line
        and (kind is None or s.kind == kind)
    ]
    assert len(found) == 1, (name, line, [(s.name, s.kind) for s in symbols])
    return found[0]


def _route_refs(result, route_path: str) -> list[str | None]:
    """``symbol_ref`` of every UsageContext registered for ``route_path``."""
    return [
        ctx.symbol_ref for ctx in result.usage_contexts
        if (ctx.metadata or {}).get("route_path") == route_path
    ]


# ---------------------------------------------------------------------------
# The registry itself.
# ---------------------------------------------------------------------------


class TestRegistryKeys:
    SRC = (
        "class A:\n"
        "    x = 1\n"
        "    class B:\n"
        "        y = 2\n"
        "        def go(self):\n"
        "            return 1\n"
        "    if x:\n"
        "        def cond(self):\n"
        "            return 2\n"
        "def outer():\n"
        "    class Local:\n"
        "        def m(self):\n"
        "            return 3\n"
        "    def inner():\n"
        "        return 4\n"
        "    return Local, inner\n"
        "def top():\n"
        "    return 5\n"
    )

    def _registry(self, tmp_path: Path):
        f = tmp_path / "m.py"
        f.write_text(self.SRC)
        analysis, reason = _extract_file_analysis(f, tmp_path)
        assert analysis is not None, reason
        return analysis.symbol_by_name

    def test_every_declaration_is_keyed_by_its_full_chain(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        expected = {
            "A": "class", "A.x": "field", "A.B": "class", "A.B.y": "field",
            "A.B.go": "method", "A.cond": "function", "outer": "function",
            "outer.Local": "class", "outer.Local.m": "method",
            "outer.inner": "function", "top": "function",
        }
        assert {k: registry[k].kind for k in expected} == expected

    def test_a_bare_key_is_a_module_level_binding(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        bare = {k for k in registry if "." not in k and k != "<module>"}
        assert bare == {"A", "outer", "top"}

    def test_name_and_qualified_name_are_not_changed(self, tmp_path: Path) -> None:
        # Both are stable-id inputs: the registry key is read off them, they
        # are not rewritten to fit it.
        registry = self._registry(tmp_path)
        assert (registry["A.B"].name, registry["A.B"].qualified_name) == ("B", "A.B")
        assert (registry["A.B.go"].name, registry["A.B.go"].qualified_name) == (
            "B.go", "A.B.go",
        )
        assert registry["outer.Local"].name == "Local"


# ---------------------------------------------------------------------------
# Route registrations: the handler reference.
# ---------------------------------------------------------------------------


_DJANGO_HEAD = "from django.urls import path, re_path\nfrom django.conf.urls import url\n"


class TestRouteHandlerReference:
    @pytest.mark.parametrize("register", ["path", "re_path", "url"])
    @pytest.mark.parametrize(
        "decoy",
        [
            # a class nested in another class
            "class Outer:\n    class LoginView:\n        pass\n",
            # a class local to a function
            "def factory():\n    class LoginView:\n        pass\n    return LoginView\n",
        ],
        ids=["nested-in-class", "function-local"],
    )
    def test_django_cbv_binds_the_module_level_view(
        self, tmp_path: Path, register: str, decoy: str,
    ) -> None:
        src = (
            _DJANGO_HEAD
            + "class LoginView:\n    pass\n"          # line 3
            + decoy                                     # declared LATER
            + f"urlpatterns = [{register}('login/', LoginView.as_view())]\n"
        )
        f = tmp_path / "urls.py"
        f.write_text(src)
        res = extract_nodes(f)
        view = _sym(res.symbols, "LoginView", 3)
        assert _route_refs(res, "/login/") == [view.id]

    def test_django_function_view_is_not_taken_by_a_function_local_class(
        self, tmp_path: Path,
    ) -> None:
        src = (
            _DJANGO_HEAD
            + "def index(request):\n    return None\n"   # line 3
            + "def factory():\n    class index:\n        pass\n    return index\n"
            + "urlpatterns = [path('', index)]\n"
        )
        f = tmp_path / "urls.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _route_refs(res, "/") == [_sym(res.symbols, "index", 3).id]

    def test_django_dotted_view_walks_to_the_nested_class(self, tmp_path: Path) -> None:
        src = (
            _DJANGO_HEAD
            + "class LoginView:\n    pass\n"                       # line 3
            + "class Outer:\n    class LoginView:\n        pass\n"  # nested: line 6
            + "urlpatterns = [\n"
            + "    path('top/', LoginView.as_view()),\n"
            + "    path('nested/', Outer.LoginView.as_view()),\n"
            + "]\n"
        )
        f = tmp_path / "urls.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _route_refs(res, "/top/") == [_sym(res.symbols, "LoginView", 3).id]
        assert _route_refs(res, "/nested/") == [_sym(res.symbols, "LoginView", 6).id]

    def test_django_view_rooted_at_an_import_is_not_a_local_class(
        self, tmp_path: Path,
    ) -> None:
        # ``views.LoginView`` names a class of the imported module. A local
        # class that shares its last segment is a different class.
        src = (
            _DJANGO_HEAD
            + "from app import views\n"
            + "class LoginView:\n    pass\n"
            + "urlpatterns = [\n"
            + "    path('theirs/', views.LoginView.as_view()),\n"
            + "    path('ours/', LoginView.as_view()),\n"
            + "]\n"
        )
        f = tmp_path / "urls.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _route_refs(res, "/theirs/") == [None]
        assert _route_refs(res, "/ours/") == [_sym(res.symbols, "LoginView", 4).id]

    def test_flask_restful_resource_is_not_taken_by_a_function_local_class(
        self, tmp_path: Path,
    ) -> None:
        src = (
            "from flask_restful import Api, Resource\n"
            "api = Api()\n"
            "class Item(Resource):\n"                      # line 3
            "    def get(self):\n        return {}\n"
            "def helper():\n    class Item:\n        pass\n    return Item\n"
            "api.add_resource(Item, '/item')\n"
        )
        f = tmp_path / "app.py"
        f.write_text(src)
        res = extract_nodes(f)
        item = _sym(res.symbols, "Item", 3)
        assert _route_refs(res, "/item") == [item.id]
        # The route marker carries the same reference for the linker.
        markers = [
            s for s in res.symbols
            if (s.meta or {}).get("framework_role") == "route"
        ]
        assert [(m.meta or {}).get("handler_ref") for m in markers] == [item.id]

    def test_flask_restful_dotted_resource_is_walked_not_matched_by_last_segment(
        self, tmp_path: Path,
    ) -> None:
        src = (
            "from flask_restful import Api\n"
            "from app import resources\n"
            "api = Api()\n"
            "class Item:\n    pass\n"                               # line 4
            "class Admin:\n    class Item:\n        pass\n"          # nested: line 7
            "api.add_resource(resources.Item, '/theirs')\n"
            "api.add_resource(Admin.Item, '/nested')\n"
            "api.add_resource(Item, '/ours')\n"
            # rooted at a call: no name to walk from
            "api.add_resource(build().Item, '/built')\n"
        )
        f = tmp_path / "app.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _route_refs(res, "/theirs") == [None]
        assert _route_refs(res, "/built") == [None]
        assert _route_refs(res, "/nested") == [_sym(res.symbols, "Item", 7).id]
        assert _route_refs(res, "/ours") == [_sym(res.symbols, "Item", 4).id]

    @pytest.mark.parametrize(
        "registration",
        [
            "app.add_url_rule('/x', 'x', handler)\n",
            "app.add_url_rule('/x', view_func=handler)\n",
            "app.add_api_route('/x', handler)\n",
        ],
        ids=["add_url_rule-positional", "add_url_rule-view_func", "add_api_route"],
    )
    def test_flask_view_is_not_taken_by_a_function_local_class(
        self, tmp_path: Path, registration: str,
    ) -> None:
        src = (
            "app = object()\n"
            "def handler():\n    return 'ok'\n"            # line 2
            "def factory():\n    class handler:\n        pass\n    return handler\n"
            + registration
        )
        f = tmp_path / "app.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _route_refs(res, "/x") == [_sym(res.symbols, "handler", 2).id]

    def test_starlette_endpoint_is_not_taken_by_a_function_local_class(
        self, tmp_path: Path,
    ) -> None:
        src = (
            "from starlette.routing import Route\n"
            "def homepage(request):\n    return None\n"    # line 2
            "def factory():\n    class homepage:\n        pass\n    return homepage\n"
            "routes = [Route('/', homepage)]\n"
        )
        f = tmp_path / "app.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _route_refs(res, "/") == [_sym(res.symbols, "homepage", 2).id]

    def test_an_app_factory_registers_the_class_it_declares(self, tmp_path: Path) -> None:
        # The other direction: inside the function the local class IS what the
        # bare name means, and it hides a module-level class of the same name.
        src = (
            "from flask_restful import Api, Resource\n"
            "class Item(Resource):\n    pass\n"            # line 2
            "def create_app():\n"
            "    api = Api()\n"
            "    class Item(Resource):\n"                  # line 6
            "        pass\n"
            "    def view():\n        return 'ok'\n"       # line 8
            "    api.add_resource(Item, '/item')\n"
            "    api.add_url_rule('/view', view_func=view)\n"
            "    return api\n"
        )
        f = tmp_path / "app.py"
        f.write_text(src)
        res = extract_nodes(f)
        assert _route_refs(res, "/item") == [_sym(res.symbols, "Item", 6).id]
        assert _route_refs(res, "/view") == [_sym(res.symbols, "create_app.view", 8).id]


# ---------------------------------------------------------------------------
# Bare-name calls.
# ---------------------------------------------------------------------------


def _edges_from(result, src_id: str, dst_id: str) -> list[str]:
    return [e.edge_type for e in result.edges if e.src == src_id and e.dst == dst_id]


class TestBareNameCalls:
    SRC = (
        "class Thing:\n"                      # line 1: module level
        "    pass\n"
        "def user():\n"                       # line 3
        "    return Thing()\n"
        "def factory():\n"                    # line 5
        "    class Thing:\n"                  # line 6: local to factory
        "        pass\n"
        "    return Thing()\n"
        "class Holder:\n"                     # line 9
        "    class Part:\n"                   # line 10: nested, no bare binding
        "        pass\n"
        "    spare = Part()\n"
        "def reach():\n"                      # line 13
        "    return Part()\n"
    )

    def _res(self, tmp_path: Path):
        f = tmp_path / "m.py"
        f.write_text(self.SRC)
        return extract_nodes(f)

    def test_a_bare_call_outside_the_function_is_the_module_level_class(
        self, tmp_path: Path,
    ) -> None:
        res = self._res(tmp_path)
        user = _sym(res.symbols, "user", 3)
        assert _edges_from(res, user.id, _sym(res.symbols, "Thing", 1).id) == ["instantiates"]
        assert _edges_from(res, user.id, _sym(res.symbols, "Thing", 6).id) == []

    def test_a_bare_call_inside_the_function_is_its_local_class(
        self, tmp_path: Path,
    ) -> None:
        res = self._res(tmp_path)
        factory = _sym(res.symbols, "factory", 5)
        assert _edges_from(res, factory.id, _sym(res.symbols, "Thing", 6).id) == ["instantiates"]
        assert _edges_from(res, factory.id, _sym(res.symbols, "Thing", 1).id) == []

    def test_a_bare_name_does_not_reach_a_class_nested_in_a_class(
        self, tmp_path: Path,
    ) -> None:
        res = self._res(tmp_path)
        part = _sym(res.symbols, "Part", 10)
        reach = _sym(res.symbols, "reach", 13)
        assert _edges_from(res, reach.id, part.id) == []

    def test_a_class_body_statement_reaches_the_bodys_own_nested_class(
        self, tmp_path: Path,
    ) -> None:
        res = self._res(tmp_path)
        part = _sym(res.symbols, "Part", 10)
        holder = _sym(res.symbols, "Holder", 9)
        assert _edges_from(res, holder.id, part.id) == ["instantiates"]

    def test_a_closure_reaches_the_enclosing_functions_local_class(
        self, tmp_path: Path,
    ) -> None:
        src = (
            "def outer():\n"
            "    class Local:\n"              # line 2
            "        pass\n"
            "    def make():\n"               # line 4
            "        return Local()\n"
            "    return make\n"
        )
        f = tmp_path / "m.py"
        f.write_text(src)
        res = extract_nodes(f)
        make = _sym(res.symbols, "outer.make", 4)
        assert _edges_from(res, make.id, _sym(res.symbols, "Local", 2).id) == ["instantiates"]


class TestCrossFile:
    def test_an_import_of_a_bare_name_is_the_module_level_class(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {
            "pkg/__init__.py": "",
            "pkg/m.py": (
                "class B:\n"                  # line 1: what ``from pkg.m import B`` binds
                "    def go(self):\n        return 1\n"
                "class A:\n"
                "    class B:\n"              # line 5: declared later, same short name
                "        def go(self):\n            return 2\n"
            ),
            "pkg/user.py": (
                "from pkg.m import B\n"
                "def use():\n"
                "    b = B()\n"
                "    return b.go()\n"
            ),
        })
        res = analyze_python(tmp_path)
        use = _sym(res.symbols, "use", 2)
        top_b = _sym(res.symbols, "B", 1)
        nested_b = _sym(res.symbols, "B", 5)
        assert _edges_from(res, use.id, top_b.id) == ["instantiates"]
        assert _edges_from(res, use.id, nested_b.id) == []
        assert _edges_from(res, use.id, _sym(res.symbols, "B.go", 2).id) == ["calls"]
        assert _edges_from(res, use.id, _sym(res.symbols, "B.go", 6).id) == []
