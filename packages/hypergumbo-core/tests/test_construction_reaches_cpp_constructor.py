# SPDX-License-Identifier: AGPL-3.0-or-later
"""A C++ construction reaches the constructor it calls.

The C++ analyzer lands ``C x(1);`` on the CLASS (or struct) node, as py /
java do. Two facts the walk was missing: a C++ constructor is named after its
class (``C::C``), so no fixed initializer name finds it; and C++ constructs a
``struct`` or a ``union`` as it does a ``class``. With them, a constructed
class reaches its constructors. When the argument count chose one, the
analyzer draws the construction a second ``instantiates`` edge straight to
it, at the same caller and line, and that construction then reaches the
chosen constructor alone. A destructor (``C::~C``) is not an initializer, as
Swift's ``deinit`` is not.
"""
from __future__ import annotations

from typing import Any, Optional

from hypergumbo_core.construction import construction_calls, initializer_hops
from hypergumbo_core.symbol_kinds import is_initializer


def _n(nid: str, kind: str, name: str, lang: str = "cpp") -> dict[str, Any]:
    return {"id": nid, "kind": kind, "name": name, "language": lang, "path": "a.cc"}


def _e(src: str, dst: str, etype: str, line: Optional[int] = None,
       call_lines: Optional[list[int]] = None) -> dict[str, Any]:
    edge: dict[str, Any] = {"src": src, "dst": dst, "type": etype, "line": line}
    if call_lines:
        edge["meta"] = {"call_lines": call_lines}
    return edge


def test_a_cpp_constructor_is_the_member_named_after_its_class() -> None:
    assert is_initializer("method", "C::C", "cpp")
    assert is_initializer("method", "OnlineStream::Impl::Impl", "cpp")
    assert is_initializer("method", "V<T>::V", "cpp")
    assert not is_initializer("method", "C::~C", "cpp")
    assert not is_initializer("method", "C::run", "cpp")
    assert not is_initializer("method", "C", "cpp")
    # Scoped to C++: a java method named after its class is not one by name.
    assert not is_initializer("method", "C.C", "java")


NODES = [
    _n("main", "function", "main"),
    _n("S", "struct", "S"),
    _n("S0", "method", "S::S"), _n("S1", "method", "S::S"),
    _n("Sd", "method", "S::~S"), _n("Sr", "method", "S::run"),
    _n("K", "class", "K"), _n("K0", "method", "K::K"), _n("K1", "method", "K::K"),
]
CONTAINS = [_e(c, m, "contains") for c, m in (
    ("S", "S0"), ("S", "S1"), ("S", "Sd"), ("S", "Sr"), ("K", "K0"), ("K", "K1"),
)]


def test_a_constructed_struct_reaches_every_constructor_when_none_was_chosen() -> None:
    edges = [_e("main", "S", "instantiates", 3), *CONTAINS]
    assert initializer_hops(NODES, edges) == [("S", "S0"), ("S", "S1")]
    assert construction_calls(NODES, edges) == [("main", "S0", 3), ("main", "S1", 3)]


def test_a_chosen_constructor_is_the_only_one_reached() -> None:
    edges = [
        _e("main", "K", "instantiates", 4), _e("main", "K1", "instantiates", 4),
        *CONTAINS,
    ]
    assert initializer_hops(NODES, edges) == [("K", "K1")]
    assert construction_calls(NODES, edges) == [("main", "K1", 4)]


def test_each_collapsed_construction_site_is_read_on_its_own() -> None:
    """One edge stands for the sites at lines 4 and 9; only line 4's chose."""
    edges = [
        _e("main", "K", "instantiates", 4, call_lines=[4, 9]),
        _e("main", "K1", "instantiates", 4), *CONTAINS,
    ]
    assert construction_calls(NODES, edges) == [
        ("main", "K0", 9), ("main", "K1", 4), ("main", "K1", 9),
    ]


def test_other_languages_keep_class_only_and_every_initializer() -> None:
    """A python struct-like kind is not a construction target, and a python
    construction with a direct edge to one initializer still reaches all."""
    nodes = [
        _n("main", "function", "main", "python"),
        _n("W", "class", "W", "python"),
        _n("Wi", "method", "W.__init__", "python"),
        _n("R", "struct", "R", "python"),
        _n("Ri", "method", "R.__init__", "python"),
    ]
    edges = [
        _e("main", "W", "instantiates", 2), _e("main", "Wi", "instantiates", 2),
        _e("main", "R", "instantiates", 3),
        _e("W", "Wi", "contains"), _e("R", "Ri", "contains"),
    ]
    assert initializer_hops(nodes, edges) == [("W", "Wi")]
