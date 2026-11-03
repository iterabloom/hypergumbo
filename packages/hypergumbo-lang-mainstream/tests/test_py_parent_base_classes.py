# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-sorop: a Python method records its class's bases as ``parent_base_classes``.

``Pattern``'s ``parent_base_class`` + ``method_name`` arm reads
``symbol.meta["parent_base_classes"]``, which only java.py and objc.py wrote.
So flask-restful.yaml's ``route_handler`` rule (``get``/``post``/... on a
``Resource``) matched nothing on Python, and a Graphene resolver rule could not
be written. The Python analyzer now stamps the enclosing class's
``base_classes`` on each of its methods, the java pass-1.5 shape; the value is
the same list the class symbol carries (dotted spellings as written, which
NameMatcher's terminal-segment fallback matches against ``^Resource$``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.framework_patterns import enrich_symbols
from hypergumbo_lang_mainstream.py import analyze_python

_SRC = (
    "import graphene\n"
    "from flask_restful import Resource\n\n\n"
    "class Todo(Resource):\n"
    "    def get(self, todo_id):\n"
    "        return {}\n\n"
    "    def helper(self):\n"
    "        return 1\n\n\n"
    "class Query(graphene.ObjectType):\n"
    "    hello = graphene.String()\n\n"
    "    def resolve_hello(self, info):\n"
    "        return 'x'\n\n\n"
    "class Plain:\n"
    "    def get(self):\n"
    "        return 2\n\n\n"
    "def get():\n"
    "    return 3\n"
)


@pytest.fixture(scope="module")
def symbols(tmp_path_factory: pytest.TempPathFactory) -> dict:
    repo = tmp_path_factory.mktemp("sorop")
    (repo / "api.py").write_text(_SRC)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_CACHE_HOME", str(repo / "cache"))
        result = analyze_python(repo)
    syms = [s for s in result.symbols if s.kind in ("method", "function")]
    enrich_symbols(syms, {"flask-restful", "graphql-python"})
    return {s.name: s for s in syms}


def _concepts(sym: object) -> list[str]:
    return [c.get("concept") for c in ((sym.meta or {}).get("concepts") or [])]


def test_a_method_carries_its_classs_bases(symbols: dict) -> None:
    assert symbols["Todo.get"].meta["parent_base_classes"] == ["Resource"]
    assert symbols["Query.resolve_hello"].meta["parent_base_classes"] == ["graphene.ObjectType"]


def test_a_method_of_a_baseless_class_and_a_function_carry_none(symbols: dict) -> None:
    assert "parent_base_classes" not in (symbols["Plain.get"].meta or {})
    assert "parent_base_classes" not in (symbols["get"].meta or {})


def test_flask_restful_route_handler_now_fires(symbols: dict) -> None:
    assert "route_handler" in _concepts(symbols["Todo.get"])
    assert "route_handler" not in _concepts(symbols["Todo.helper"])
    assert "route_handler" not in _concepts(symbols["Plain.get"])
    assert "route_handler" not in _concepts(symbols["get"])


def test_graphene_resolver_rule_fires(symbols: dict) -> None:
    assert "graphql_resolver" in _concepts(symbols["Query.resolve_hello"])
