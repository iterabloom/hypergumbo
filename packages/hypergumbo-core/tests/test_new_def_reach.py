# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a new method overrides, and whether a new def's decorator may register it.

Each test writes a small tree laid out like this repository
(``packages/<pkg>/src/<module>``) and asks :class:`NewDefs` about one class of
one file. What is pinned is the property the selector relies on: every base
written in a way this module follows leads to the ``class`` statement that
defines the method, and every base it cannot follow is reported as such
(``unresolved`` not empty), never silently dropped.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core.new_def_reach import NewDefs, Overrides, _bindings

A = "class A:\n    def m(self):\n        return 1\n\n    class Inner:\n        def m(self):\n            return 2\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _ask(root: Path, files: dict[str, str], rel: str, owner: str = "B",
         method: str = "m") -> Overrides:
    """Write ``files`` (with ``p`` a package holding ``a.py``) and ask what
    ``owner.method`` in ``rel`` would override."""
    tree = {"packages/p/src/p/__init__.py": "", "packages/p/src/p/a.py": A, **files}
    _write(root, tree)
    return NewDefs(str(root / rel), tree[rel]).overrides(owner, method)


def _definers(root: Path, found: Overrides) -> list[tuple[str, str]]:
    assert found.unresolved == "", found.unresolved
    return [(str(Path(path).relative_to(root)), cls) for path, cls in found.definers]


B = "packages/p/src/p/b.py"
A_PATH = "packages/p/src/p/a.py"


@pytest.mark.parametrize("header", [
    "from .a import A\n\n\nclass B(A):",
    "from p.a import A\n\n\nclass B(A):",
    "from p.a import A as Alias\n\n\nclass B(Alias):",
    "import p.a\n\n\nclass B(p.a.A):",
    "import p.a\nimport p.b2\n\n\nclass B(p.a.A):",
    "import p.a as pa\n\n\nclass B(pa.A):",
    "from p import a\n\n\nclass B(a.A):",
    "from . import a\n\n\nclass B(a.A):",
    "from .a import A\n\n\nclass B(A[int]):",
])
def test_every_way_of_naming_a_base_leads_to_its_class(tmp_path: Path, header: str) -> None:
    found = _ask(tmp_path, {B: header + "\n    X = 1\n", "packages/p/src/p/b2.py": ""}, B)
    assert _definers(tmp_path, found) == [(A_PATH, "A")]


def test_a_re_export_and_another_package_are_followed(tmp_path: Path) -> None:
    files = {"packages/p/src/p/__init__.py": "from .a import A\n",
             "packages/q/src/q/__init__.py": "",
             "packages/q/src/q/c.py": "from p import A\n\n\nclass C(A):\n    X = 1\n"}
    _write(tmp_path, {A_PATH: A, **files})
    found = NewDefs(str(tmp_path / "packages/q/src/q/c.py"),
                    files["packages/q/src/q/c.py"]).overrides("C", "m")
    assert _definers(tmp_path, found) == [(A_PATH, "A")]


def test_a_nested_class_and_a_class_of_the_enclosing_body(tmp_path: Path) -> None:
    nested = _ask(tmp_path / "1", {B: "from .a import A\n\n\nclass B(A.Inner):\n    X = 1\n"}, B)
    assert _definers(tmp_path / "1", nested) == [(A_PATH, "A.Inner")]
    body = ("class Outer:\n    class Base:\n        def m(self):\n            return 1\n\n"
            "    class B(Base):\n        X = 1\n")
    found = _ask(tmp_path / "2", {B: body}, B, owner="Outer.B")
    assert _definers(tmp_path / "2", found) == [(B, "Outer.Base")]


def test_up_every_branch_to_the_nearest_definer_once(tmp_path: Path) -> None:
    """A diamond: ``D(B, C)``, both of ``A``, which defines ``m``. ``A`` once.
    ``object`` is no base."""
    src = ("from .a import A\n\n\nclass B(A):\n    pass\n\n\nclass C(A, object):\n    pass\n\n\n"
           "class D(B, C):\n    X = 1\n")
    found = _ask(tmp_path, {B: src}, B, owner="D")
    assert _definers(tmp_path, found) == [(A_PATH, "A")]


def test_a_class_with_no_definer_overrides_nothing(tmp_path: Path) -> None:
    found = _ask(tmp_path, {B: "from .a import A\n\n\nclass B(A):\n    X = 1\n"}, B,
                 method="fresh")
    assert found == Overrides()


def test_a_relative_import_from_two_levels_down(tmp_path: Path) -> None:
    rel = "packages/p/src/p/sub/b.py"
    found = _ask(tmp_path, {"packages/p/src/p/sub/__init__.py": "",
                            rel: "from ..a import A\n\n\nclass B(A):\n    X = 1\n"}, rel)
    assert _definers(tmp_path, found) == [(A_PATH, "A")]


def test_a_namespace_package_without_an_init(tmp_path: Path) -> None:
    files = {"packages/p/src/ns/a.py": A}
    good = _ask(tmp_path / "1", {**files, B: "import ns.a\n\n\nclass B(ns.a.A):\n    X = 1\n"}, B)
    assert _definers(tmp_path / "1", good) == [("packages/p/src/ns/a.py", "A")]
    bad = _ask(tmp_path / "2", {**files, B: "import ns\n\n\nclass B(ns.A):\n    X = 1\n"}, B)
    assert "namespace package" in bad.unresolved


@pytest.mark.parametrize(("source", "why"), [
    ("class B(Exception):\n    X = 1\n", "builtin Exception"),
    ("from collections import OrderedDict\n\n\nclass B(OrderedDict):\n    X = 1\n",
     "not one file of this repository"),
    ("def make():\n    return object\n\n\nclass B(make()):\n    X = 1\n", "computed"),
    ("from .a import A\nX = [A]\n\n\nclass B(X[0].A):\n    pass\n", "computed"),
    ("class B(Nowhere):\n    X = 1\n", "not bound"),
    ("from .a import A\nA = 1\n\n\nclass B(A):\n    X = 1\n", "bound 2 times"),
    ("A = type('A', (), {})\n\n\nclass B(A):\n    X = 1\n", "not a class or an import"),
    ("from .a import *\n\n\nclass B(A):\n    X = 1\n", "* import"),
    ("from .missing import A\n\n\nclass B(A):\n    X = 1\n", "is not there"),
    ("import p.a as pa\n\n\nclass B(pa):\n    X = 1\n", "is a module"),
    ("from .a import A\n\n\nclass B(A.Missing):\n    X = 1\n", "A.Missing is not a class"),
    ("from .a import A\n\n\nclass B(A, metaclass=type):\n    X = 1\n", "metaclass"),
    ("from .bad import A\n\n\nclass B(A):\n    X = 1\n", "cannot be read or parsed"),
    ("from .loop import K\n\n\nclass B(K):\n    X = 1\n", "cycle"),
    ("from .assigns import A\n\n\nclass B(A):\n    X = 1\n", "binds m other than by def"),
    ("from .dup import D\n\n\nclass B(D):\n    X = 1\n", "in 2 places"),
    ("class Outer:\n    Base = int\n\n    class B(Base):\n        X = 1\n", "not one class"),
])
def test_a_base_that_cannot_be_followed_is_reported(tmp_path: Path, source: str,
                                                   why: str) -> None:
    files = {B: source,
             "packages/p/src/p/bad.py": "class A(:\n",
             "packages/p/src/p/loop.py": "from .b import K\n",
             "packages/p/src/p/assigns.py": "class A:\n    m = print\n",
             "packages/p/src/p/dup.py": "from dup import D\n",
             "packages/p/src/dup/__init__.py": "class D:\n    pass\n",
             "packages/q/src/dup/__init__.py": "class D:\n    pass\n"}
    owner = "Outer.B" if source.startswith("class Outer") else "B"
    found = _ask(tmp_path, files, B, owner=owner)
    assert why in found.unresolved
    assert found.definers == ()


def test_a_class_inside_a_function_is_not_followed(tmp_path: Path) -> None:
    src = "from .a import A\n\n\ndef f():\n    class B(A):\n        X = 1\n\n    return B\n"
    assert "inside a function" in _ask(tmp_path, {B: src}, B, owner="f.B").unresolved


def test_a_file_outside_any_package_has_no_absolute_imports(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("from p.a import A\n\n\nclass B(A):\n    X = 1\n")
    found = NewDefs(str(tmp_path / "m.py"), (tmp_path / "m.py").read_text()).overrides("B", "m")
    assert "not one file of this repository" in found.unresolved


def test_the_bindings_of_a_scope() -> None:
    tree = ast.parse(
        "import os.path\nimport json as j\nfrom x import y as z\nA, B = 1, 2\nC += 1\n"
        "D: int = 1\ndel E\nfor F in []:\n    G = 1\nelse:\n    H = 1\n"
        "with open('x') as I:\n    pass\nwith open('x'):\n    pass\n"
        "try:\n    J = 1\nexcept OSError as K:\n    L = 1\nexcept ValueError:\n    pass\n"
        "finally:\n    M = 1\nx[N] = 1\n\n\ndef O():\n    P = 1\n")
    assert set(_bindings(tree.body)) == set("ABCDEFGHIJKLMO") | {"os", "j", "z"}


@pytest.mark.parametrize(("decorator", "registers"), [
    ("property", False), ("staticmethod", False), ("m.setter", False),
    ("functools.lru_cache(maxsize=1)", False), ("ft.cache", False), ("cached_property", False),
    ("contextmanager", False),
    ("register", True), ("hooks['x']", True), ("local", True), ("relative", True),
    ("twice", True), ("functools.partial", True), ("call()()", True),
])
def test_which_decorators_may_register_the_def(tmp_path: Path, decorator: str,
                                              registers: bool) -> None:
    source = ("import functools\nimport functools as ft\nfrom functools import cached_property\n"
              "from contextlib import contextmanager\n"
              "from .deco import relative\ntwice = 1\ntwice = 2\n\n\ndef local(fn):\n    return fn\n\n\n"
              f"@{decorator}\ndef new():\n    return 1\n")
    (tmp_path / "m.py").write_text(source)
    found = NewDefs(str(tmp_path / "m.py"), source).registering("new")
    assert found == ((decorator,) if registers else ())
