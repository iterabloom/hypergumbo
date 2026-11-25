# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Python call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. ``_process_call`` has one
resolved ``calls`` and one ``instantiates`` creation site, fed by every route
below; each fixture line pins the value its route carries to the site:

* a nested function of the caller, or of a function around it, is
  ``lexical_scope``; a module-level function of the file, found in its
  bare-name table, is ``same_file``;
* a name, a module, a submodule, a dotted module path or a class the file
  imports is ``import``, and so is an alias ``f = g`` of an imported ``g``
  (an alias of the file's own function is ``same_file``);
* ``self.m()`` is ``own_type``; a typed local's and a typed ``self.field``'s
  method are ``receiver_type``, as is a method of the class a receiver call
  constructs (``Item().go()``); ``Outer.Inner.make()`` is ``qualified``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import analyze_python

_MAIN = """import app.deep.inner
from app import models
from app.models import Item, helper
from app.crud import ops

alias = helper


def local_fn():
    return 5


own_alias = local_fn


class Outer:
    class Inner:
        @staticmethod
        def make():
            return 6

    def __init__(self, item: Item):
        self.item = item

    def own(self):
        return 7

    def run(self):
        self.own()
        self.item.go()
        Outer.Inner.make()
        Outer.Inner()
        Item().go()


def main():
    def nested():
        return 8

    def outer2():
        def inner2():
            return nested()
        return inner2()

    nested()
    local_fn()
    helper()
    alias()
    models.helper()
    Item.make()
    ops.create()
    app.deep.inner.deep_fn()
    it = Item()
    it.go()
    own_alias()
    return outer2()
"""

_FILES = {
    "app/__init__.py": "",
    "app/models.py": (
        "class Item:\n    @classmethod\n    def make(cls):\n        return cls()\n\n"
        "    def go(self):\n        return 1\n\n\ndef helper():\n    return 2\n"
    ),
    "app/crud/__init__.py": "",
    "app/crud/ops.py": "def create():\n    return 3\n",
    "app/deep/__init__.py": "",
    "app/deep/inner.py": "def deep_fn():\n    return 4\n",
    "app/main.py": _MAIN,
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_python(root).edges


def _mark(
    edges: list[Edge], line: int, dst_name: str, edge_type: str = "calls",
) -> str:
    hits = [
        e for e in edges
        if e.edge_type == edge_type and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name and "main.py" in e.src
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges if "main.py" in e.src]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    # Methods of Outer.
    assert _mark(edges, 29, "Outer.own") == "own_type"
    assert _mark(edges, 30, "Item.go") == "receiver_type"
    assert _mark(edges, 31, "Inner.make") == "qualified"
    assert _mark(edges, 32, "Inner", "instantiates") == "qualified"
    assert _mark(edges, 33, "Item", "instantiates") == "import"
    assert _mark(edges, 33, "Item.go") == "receiver_type"
    # main and its nested functions.
    assert _mark(edges, 42, "main.nested") == "lexical_scope"
    assert _mark(edges, 43, "main.outer2.inner2") == "lexical_scope"
    assert _mark(edges, 45, "main.nested") == "lexical_scope"
    assert _mark(edges, 46, "local_fn") == "same_file"
    assert _mark(edges, 47, "helper") == "import"
    assert _mark(edges, 48, "helper") == "import"
    assert _mark(edges, 49, "helper") == "import"
    assert _mark(edges, 50, "Item.make") == "import"
    assert _mark(edges, 51, "create") == "import"
    assert _mark(edges, 52, "deep_fn") == "import"
    assert _mark(edges, 53, "Item", "instantiates") == "import"
    assert _mark(edges, 54, "Item.go") == "receiver_type"
    assert _mark(edges, 55, "local_fn") == "same_file"
    assert _mark(edges, 56, "main.outer2") == "lexical_scope"
