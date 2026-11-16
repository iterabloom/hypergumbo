# SPDX-License-Identifier: AGPL-3.0-or-later
"""A route registration names its handler through the registering file's
imports, and the route marker carries the handler's id.

``path("login/", views.LoginView.as_view())`` says which ``LoginView`` it
means: the one the module ``views`` binds. The analyzer used to keep only the
last segment (``view_name: "LoginView"``), and everything downstream had to
guess among the project's ``LoginView`` classes by name: the urlconf's own
class of that name, a nested ``Outer.LoginView``, another app's view.

The handler expression is a dotted name; it is walked from what its root
denotes, here an import, exactly as a call's receiver is. The result goes on
the UsageContext (``symbol_ref``) and on the route marker
(``meta["handler_ref"]``), where the route-handler linker reads it as an id.

Every fixture declares a same-named decoy next to the right answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.py import analyze_python

_VIEWS = (
    "class LoginView:\n"
    "    def get(self, request):\n        return None\n"
    "def index(request):\n    return None\n"
    "class Item:\n    def get(self):\n        return {}\n"
    "CONSTANT = 1\n"
)
# A second app with the same names: the decoy a name-only lookup can pick.
_OTHER = (
    "class LoginView:\n    pass\n"
    "def index(request):\n    return None\n"
    "class Item:\n    pass\n"
)


def _analyze(tmp_path: Path, registering: str, rel: str = "app/urls.py"):
    files = {
        "app/__init__.py": "",
        "app/views.py": _VIEWS,
        "other/__init__.py": "",
        "other/views.py": _OTHER,
        rel: registering,
    }
    for name, src in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)
    return analyze_python(tmp_path)


def _sym_id(res, path_suffix: str, name: str) -> str:
    [sym] = [
        s for s in res.symbols
        if s.name == name and s.path.endswith(path_suffix)
        and (s.meta or {}).get("framework_role") != "route"
    ]
    return sym.id


def _refs(res, route_path: str) -> tuple[set[str | None], set[str | None]]:
    """``(symbol_refs of the UsageContexts, handler_refs of the markers)``."""
    contexts = {
        ctx.symbol_ref for ctx in res.usage_contexts
        if (ctx.metadata or {}).get("route_path") == route_path
    }
    markers = {
        (s.meta or {}).get("handler_ref") for s in res.symbols
        if (s.meta or {}).get("framework_role") == "route"
        and (s.meta or {}).get("route_path") == route_path
    }
    return contexts, markers


_DJANGO = "from django.urls import path\n"


class TestDjango:
    @pytest.mark.parametrize(
        ("imports", "view"),
        [
            ("from app import views\n", "views.LoginView.as_view()"),
            ("from . import views\n", "views.LoginView.as_view()"),
            ("import app.views\n", "app.views.LoginView.as_view()"),
            ("import app.views as v\n", "v.LoginView.as_view()"),
            ("from app.views import LoginView\n", "LoginView.as_view()"),
            ("from app.views import LoginView as Login\n", "Login.as_view()"),
        ],
    )
    def test_a_class_based_view_is_the_one_the_import_binds(
        self, tmp_path: Path, imports: str, view: str,
    ) -> None:
        res = _analyze(
            tmp_path, _DJANGO + imports + f"urlpatterns = [path('login/', {view})]\n",
        )
        view_id = _sym_id(res, "app/views.py", "LoginView")
        assert _refs(res, "/login/") == ({view_id}, {view_id})

    def test_a_function_view_is_the_one_the_import_binds(self, tmp_path: Path) -> None:
        res = _analyze(
            tmp_path,
            _DJANGO + "from app import views\n"
            "from other.views import index as other_index\n"
            "urlpatterns = [path('', views.index), path('o/', other_index)]\n",
        )
        ours = _sym_id(res, "app/views.py", "index")
        theirs = _sym_id(res, "other/views.py", "index")
        assert _refs(res, "/") == ({ours}, {ours})
        assert _refs(res, "/o/") == ({theirs}, {theirs})

    def test_the_urlconfs_own_class_is_not_the_imported_modules(
        self, tmp_path: Path,
    ) -> None:
        res = _analyze(
            tmp_path,
            _DJANGO + "from app import views\n"
            "class LoginView:\n    pass\n"
            "class Outer:\n    class LoginView:\n        pass\n"
            "urlpatterns = [\n"
            "    path('theirs/', views.LoginView.as_view()),\n"
            "    path('ours/', LoginView.as_view()),\n"
            "    path('nested/', Outer.LoginView.as_view()),\n"
            "]\n",
        )
        imported = _sym_id(res, "app/views.py", "LoginView")
        [own] = [
            s.id for s in res.symbols
            if s.qualified_name == "LoginView" and s.path.endswith("app/urls.py")
        ]
        [nested] = [s.id for s in res.symbols if s.qualified_name == "Outer.LoginView"]
        assert _refs(res, "/theirs/") == ({imported}, {imported})
        assert _refs(res, "/ours/") == ({own}, {own})
        assert _refs(res, "/nested/") == ({nested}, {nested})

    def test_a_view_outside_the_tree_stays_unresolved(self, tmp_path: Path) -> None:
        # ``django.contrib.auth.views.LoginView`` is not ``app.views.LoginView``.
        res = _analyze(
            tmp_path,
            _DJANGO + "from django.contrib.auth import views as auth_views\n"
            "urlpatterns = [path('login/', auth_views.LoginView.as_view())]\n",
        )
        assert _refs(res, "/login/") == ({None}, {None})

    def test_a_name_that_is_not_a_handler_is_not_a_handler_ref(
        self, tmp_path: Path,
    ) -> None:
        res = _analyze(
            tmp_path,
            _DJANGO + "from app import views\n"
            "from app.views import LoginView\n"
            "view = LoginView.as_view()\n"
            "urlpatterns = [path('const/', views.CONSTANT), path('var/', view)]\n",
        )
        # an imported module constant: no handler at all
        assert _refs(res, "/const/") == ({None}, {None})
        # a module variable of this file: the context still names it, the
        # marker does not offer a variable as the route's handler
        contexts, markers = _refs(res, "/var/")
        assert markers == {None}
        assert contexts == {_sym_id(res, "app/urls.py", "view")}


class TestOtherFrameworks:
    def test_starlette_endpoint_through_a_module_import(self, tmp_path: Path) -> None:
        res = _analyze(
            tmp_path,
            "from starlette.routing import Route\n"
            "from app import views\n"
            "routes = [Route('/', views.index)]\n",
            rel="app/routes.py",
        )
        view_id = _sym_id(res, "app/views.py", "index")
        assert _refs(res, "/") == ({view_id}, {view_id})

    def test_flask_restful_resource_through_a_module_import(self, tmp_path: Path) -> None:
        res = _analyze(
            tmp_path,
            "from flask_restful import Api\n"
            "from app import views\n"
            "api = Api()\n"
            "api.add_resource(views.Item, '/item')\n",
            rel="app/api.py",
        )
        item = _sym_id(res, "app/views.py", "Item")
        assert _refs(res, "/item") == ({item}, {item})

    def test_flask_url_rule_names_the_imported_view_on_its_context(
        self, tmp_path: Path,
    ) -> None:
        # ``add_url_rule`` mints no marker (the YAML-only path); the context
        # carries the reference.
        res = _analyze(
            tmp_path,
            "from app.views import index\n"
            "app = object()\n"
            "app.add_url_rule('/x', view_func=index)\n",
            rel="app/web.py",
        )
        assert _refs(res, "/x") == ({_sym_id(res, "app/views.py", "index")}, set())


def test_an_attribute_of_a_view_function_is_not_that_view(tmp_path: Path) -> None:
    """The dotted walk stops at a declaration that is not a class.
    ``views.index.wrapped`` names an attribute of the function object
    ``views.index``, which the analyzer does not know; it is NOT ``index``.
    Answering with the declaration the walk had reached would make ``index``
    the handler of a route whose handler is something else. The sibling route
    registered with ``views.index`` itself shows the fixture reaches it."""
    res = _analyze(
        tmp_path,
        _DJANGO + "from app import views\n"
        "urlpatterns = [path('', views.index), path('w/', views.index.wrapped)]\n",
    )
    ours = _sym_id(res, "app/views.py", "index")
    assert _refs(res, "/") == ({ours}, {ours})
    contexts, markers = _refs(res, "/w/")
    assert ours not in contexts and ours not in markers, (contexts, markers)
