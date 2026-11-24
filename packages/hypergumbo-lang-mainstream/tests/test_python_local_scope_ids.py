# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/jupyter.py
"""A Python local declaration is scoped by the ``def`` that holds it, by what
identifies that ``def`` among its siblings.

Every Python stable_id hashes the chain of classes and functions enclosing it
(``qualified_name``, ADR-0035 §1, WI-gitun). Until scheme v19 a function was a
label in that chain by its NAME alone, so two same-named locals of a
``@property`` getter and its ``@p.setter``, of a ``@staticmethod`` and a method
of one name, or of two redefinitions of ``def f`` with different parameters
tied (WI-finam, the defect WI-podor fixed for Swift); the run's occurrence
split told them apart by order, so deleting the first moved the second's id.

The properties pinned here: each pair of holders gives its locals two ids;
deleting the first holder's local moves no id of the second's through the
occurrence split; a local of a ``def`` whose hashed text is ``()`` with no
decorator keeps the formula it had; the Symbol's ``qualified_name`` field
(display, registry key) does not change; and what is not the holder's
identity -- layout, a default value, a decorator's arguments -- moves no
local's id.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import (
    make_file_stable_id,
    make_typed_stable_id,
    split_within_file_stable_id_collisions,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.jupyter import analyze_jupyter
from hypergumbo_lang_mainstream.py import analyze_python

#: The local a holder declares, at the holder body's indentation.
_LOCAL = "def h(): pass"
_LOCAL_TYPE = "class L:\n    def m(self): pass"


def _symbols(root: Path, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.py").write_text(text)
    return [s for s in analyze_python(root).symbols if s.kind != "file"]


def _short(sym: Symbol) -> str:
    return sym.name.rsplit(".", 1)[-1]


def _local_ids(root: Path, text: str, local: str) -> list[str]:
    """The stable ids of the symbols whose last name part is ``local``, in
    source order."""
    found = sorted(
        (s for s in _symbols(root, text) if _short(s) == local),
        key=lambda s: (s.span.start_line, s.span.start_col),
    )
    assert found, local
    return [s.stable_id or "" for s in found]


def _holder(head: str, local: str) -> str:
    """``head`` (decorators and ``def`` line) with ``local`` as its body."""
    return head + "\n" + textwrap.indent(local, "    ")


#: Two holders of a same-named local: each is ``(first, second)`` member head
#: of ``class T``; the local is the body of each.
_HOLDERS: dict[str, tuple[str, str]] = {
    "property-getter-and-setter": ("@property\ndef p(self):", "@p.setter\ndef p(self, v):"),
    "property-getter-and-deleter": ("@property\ndef p(self):", "@p.deleter\ndef p(self):"),
    "redefinitions-by-annotation": ("def f(self, x: int):", "def f(self, x: str):"),
    "redefinitions-by-arity": ("def f(self, x):", "def f(self, x, y):"),
    "redefinitions-by-return": ("def f(self) -> int:", "def f(self) -> str:"),
    "staticmethod-and-method": ("@staticmethod\ndef f():", "def f(self):"),
    "async-and-sync": ("async def f(self):", "def f(self):"),
    # Over 60 characters: the signature does not normalize, so the def's id
    # is the untyped tier's (parameter count and flags), and so is its label.
    "untyped-redefinitions-by-arity": (
        "def f(self, a_rather_long_parameter_name, another_long_parameter_name):",
        "def f(self, a_rather_long_parameter_name, another_long_parameter_name, c):"),
}


def _class_text(first: str, second: str, local: str = _LOCAL) -> str:
    body = _holder(first, local) + "\n" + _holder(second, local)
    return "class T:\n" + textwrap.indent(body, "    ") + "\n"


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_the_locals_of_two_holders_get_two_ids(tmp_path: Path, shape: str) -> None:
    ids = _local_ids(tmp_path / "r", _class_text(*_HOLDERS[shape]), "h")
    assert len(ids) == 2
    assert ids[0] != ids[1], shape


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_a_local_class_of_two_holders_gets_two_ids(tmp_path: Path, shape: str) -> None:
    """The same for a local CLASS and its method: the method's id is
    anchored on the class's id."""
    text = _class_text(*_HOLDERS[shape], local=_LOCAL_TYPE)
    for local in ("L", "m"):
        ids = _local_ids(tmp_path / local, text, local)
        assert len(ids) == 2
        assert ids[0] != ids[1], (shape, local)


@pytest.mark.parametrize("shape", sorted(_HOLDERS))
def test_deleting_the_first_holders_local_moves_no_id_of_the_second(
    tmp_path: Path, shape: str,
) -> None:
    first, second = _HOLDERS[shape]
    both = _symbols(tmp_path / "both", _class_text(first, second))
    alone_body = _holder(first, "pass") + "\n" + _holder(second, _LOCAL)
    alone = _symbols(tmp_path / "alone", "class T:\n" + textwrap.indent(alone_body, "    ") + "\n")
    split_within_file_stable_id_collisions(both)
    split_within_file_stable_id_collisions(alone)
    last = max((s for s in both if _short(s) == "h"), key=lambda s: s.span.start_line)
    survivor = [s for s in alone if _short(s) == "h"]
    assert len(survivor) == 1
    assert survivor[0].stable_id == last.stable_id, shape


def test_the_items_repro(tmp_path: Path) -> None:
    """A getter's and its setter's ``def g()``; ``def helper()`` in two
    module-level redefinitions of ``def f``."""
    src = (
        "class C:\n"
        "    @property\n"
        "    def p(self):\n"
        "        def g(): pass\n"
        "    @p.setter\n"
        "    def p(self, v):\n"
        "        def g(): pass\n"
        "def f(x: int):\n"
        "    def helper(): pass\n"
        "def f(x: str):\n"
        "    def helper(): pass\n"
    )
    for local in ("g", "helper"):
        ids = _local_ids(tmp_path / local, src, local)
        assert len(ids) == 2
        assert ids[0] != ids[1], local


def test_a_local_of_an_empty_signature_def_keeps_its_formula(tmp_path: Path) -> None:
    """``def f(self)`` / ``def top()`` with no decorator label the chain by
    their bare name, as before: what they declare keeps its id, and every
    ``qualified_name`` field stays the in-module name it was."""
    src = (
        "class T:\n"
        "    def f(self):\n"
        "        def h(): pass\n"
        "    def g(self, x: int):\n"
        "        def k(): pass\n"
        "        class L: pass\n"
        "def top():\n"
        "    def t(): pass\n"
    )
    fsid = make_file_stable_id("python", "a.py")
    got = {_short(s): s for s in _symbols(tmp_path / "r", src)}
    for local, chain in {"h": "T.f.h", "t": "top.t"}.items():
        assert got[local].stable_id == make_typed_stable_id(
            "function", "()", "", fsid, "", name=local, qualified_name=chain,
        ), local
    assert got["k"].stable_id != make_typed_stable_id(
        "function", "()", "", fsid, "", name="k", qualified_name="T.g.k")
    assert got["k"].qualified_name == "g.k"
    assert got["L"].qualified_name == "T.g.L"


#: Edits to a holder that are not a change of its identity: the local ``h``
#: keeps its id.
_SAME_HOLDER: dict[str, tuple[str, str]] = {
    "annotation-layout": ("def f(self, x: Dict[str,\n        int]):",
                          "def f(self, x: Dict[str, int]):"),
    "default-value": ("def f(self, x=1):", "def f(self, x=2):"),
    "decorator-arguments": ("@app.route('/a')\ndef f(self, x):",
                            "@app.route('/b')\ndef f(self, x):"),
    "comment-in-signature": ("def f(self, x: int,  # the x\n        y: int):",
                             "def f(self, x: int, y: int):"),
}


@pytest.mark.parametrize("local", ["h", "L"])
@pytest.mark.parametrize("shape", sorted(_SAME_HOLDER))
def test_an_edit_that_is_not_the_holders_identity_moves_no_local(
    tmp_path: Path, shape: str, local: str,
) -> None:
    body = _LOCAL if local == "h" else "class L: pass"
    one, other = (
        "class T:\n" + textwrap.indent(_holder(head, body), "    ") + "\n"
        for head in _SAME_HOLDER[shape]
    )
    a = _local_ids(tmp_path / "a", one, local)
    b = _local_ids(tmp_path / "b", other, local)
    assert a == b, shape


def test_a_notebooks_redefinitions_scope_their_locals(tmp_path: Path) -> None:
    """A notebook takes the Python chain (``jupyter.py`` calls
    ``python_scope_labels``): ``def f(x: int)`` in one cell and its
    redefinition ``def f(x: str)`` in the next give their ``helper`` two
    ids, as do a local class in each."""
    cells = [
        "def f(x: int):\n    def helper(): pass\n    class L: pass\n",
        "def f(x: str):\n    def helper(): pass\n    class L: pass\n",
    ]
    path = tmp_path / "nb" / "demo.ipynb"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {"cell_type": "code", "metadata": {}, "outputs": [],
             "execution_count": None, "source": src.splitlines(keepends=True)}
            for src in cells
        ],
    }))
    symbols = analyze_jupyter(tmp_path).symbols
    for local in ("helper", "L"):
        ids = [s.stable_id for s in symbols if s.name == local]
        assert len(ids) == 2, local
        assert ids[0] != ids[1], local
