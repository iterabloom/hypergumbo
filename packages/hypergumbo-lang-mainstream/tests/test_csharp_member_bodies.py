# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-binap: a call in a C# member body is credited to that member.

C# has seven member forms with a body that holds calls. The analyzer
recognised two of them, ``method_declaration`` and ``constructor_declaration``,
as an enclosing callable, so a call in any of the other five went to the
enclosing class (INV-bamij's type-body fallback), and an object creation or a
method-group reference there was dropped (those arms have no type fallback):

- a property's accessors (``get`` / ``set`` / ``init`` and the expression body
  ``P => ...``), and its initialiser (``{ get; } = Make();``). The property
  symbol existed; nothing was credited to it.
- an indexer (``this[int i]``), an operator (``operator +``), a conversion
  operator (``implicit operator int``), a destructor (``~A``) and an event with
  ``add`` / ``remove`` accessors. These had no symbol at all.

The enclosing member is found by the declaration's POSITION (INV-midag), so two
indexers of one class are told apart. The kinds come from the registry:
``subscript`` for an indexer (Swift's ``subscript`` is the same construct),
``method`` for operators and the destructor (as C++ emits ``operator==`` and
``~S``), ``event`` for an event.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.csharp import analyze_csharp

_SOURCE = """\
using System;
using System.Collections.Generic;
public class A {
    private int _v;
    public int Getter { get { return Helper(); } }
    public int Setter { set { _v = Helper(); } }
    public int Twice => Helper() * 2;
    public int AutoInit { get; } = Helper2();
    public int this[int i] { get { return Helper(); } set { Helper2(); } }
    public int this[string s] => Helper2();
    public static A operator +(A x, A y) { Helper2(); return x; }
    public static implicit operator int(A a) { return Helper2(); }
    ~A() { Helper2(); }
    public event EventHandler Changed { add { Helper(); } remove { Helper2(); } }
    public object Made { get { return new B(); } }
    public int Grouped { get { new List<int>().ForEach(Consume); return 0; } }
    public void Run() { int Loc() => Helper(); Loc(); }
    static int Helper() { return 1; }
    static int Helper2() { return 2; }
    static void Consume(int x) { }
}
public class B { public B() {} }
"""


def _analyze(tmp_path: Path):
    (tmp_path / "A.cs").write_text(_SOURCE)
    return analyze_csharp(tmp_path)


def _edges_by_line(result, edge_type: str) -> dict[int, list[tuple[str, str, str]]]:
    """line -> [(src kind, src name, dst name or id)] for one edge type."""
    by_id = {s.id: s for s in result.symbols}
    out: dict[int, list[tuple[str, str, str]]] = {}
    for e in result.edges:
        if e.edge_type != edge_type or e.line is None:
            continue
        src = by_id.get(e.src)
        dst = by_id[e.dst].name if e.dst in by_id else e.dst
        out.setdefault(e.line, []).append(
            (src.kind if src else "?", src.name if src else e.src, dst))
    return out


def _sym(result, name: str):
    [sym] = [s for s in result.symbols if s.name == name]
    return sym


def test_a_property_accessor_call_is_credited_to_the_property(tmp_path: Path) -> None:
    calls = _edges_by_line(_analyze(tmp_path), "calls")
    assert calls[5] == [("property", "A.Getter", "A.Helper")], calls
    assert calls[6] == [("property", "A.Setter", "A.Helper")], calls
    assert calls[7] == [("property", "A.Twice", "A.Helper")], calls
    # The initialiser is part of the property declaration, so it is the
    # property's call too (a FIELD initialiser stays on its class: INV-bamij).
    assert calls[8] == [("property", "A.AutoInit", "A.Helper2")], calls


def test_indexer_operator_destructor_and_event_are_symbols(tmp_path: Path) -> None:
    result = _analyze(tmp_path)
    indexers = sorted(
        (s.span.start_line, s.kind, s.name, s.signature)
        for s in result.symbols if s.name == "A.this[]")
    assert indexers == [
        (9, "subscript", "A.this[]", "(int i) int"),
        (10, "subscript", "A.this[]", "(string s) int"),
    ], indexers
    plus = _sym(result, "A.operator+")
    assert (plus.kind, plus.signature, plus.qualified_name) == (
        "method", "(A x, A y) A", "A.operator+")
    assert plus.modifiers == ["public", "static"], plus.modifiers
    assert plus.stable_id is not None
    conv = _sym(result, "A.operator int")
    assert (conv.kind, conv.signature) == ("method", "(A a) int")
    dtor = _sym(result, "A.~A")
    assert (dtor.kind, dtor.signature, dtor.is_exported) == ("method", "()", False)
    event = _sym(result, "A.Changed")
    assert (event.kind, event.signature, event.is_exported) == (
        "event", "EventHandler", True)
    # Each spans its whole declaration, so the position lookup reaches it.
    assert (event.span.start_line, event.span.end_line) == (14, 14)
    assert (dtor.span.start_line, dtor.span.start_col) == (13, 4)


def test_their_calls_are_credited_to_them(tmp_path: Path) -> None:
    calls = _edges_by_line(_analyze(tmp_path), "calls")
    # Two indexers in one class: told apart by position, not by name.
    assert sorted(calls[9]) == [
        ("subscript", "A.this[]", "A.Helper"),
        ("subscript", "A.this[]", "A.Helper2"),
    ], calls
    assert calls[10] == [("subscript", "A.this[]", "A.Helper2")], calls
    assert calls[11] == [("method", "A.operator+", "A.Helper2")], calls
    assert calls[12] == [("method", "A.operator int", "A.Helper2")], calls
    assert calls[13] == [("method", "A.~A", "A.Helper2")], calls
    assert sorted(calls[14]) == [
        ("event", "A.Changed", "A.Helper"),
        ("event", "A.Changed", "A.Helper2"),
    ], calls


def test_every_member_call_lies_inside_its_src(tmp_path: Path) -> None:
    """INV-midag's statement on this fixture: reach first, then containment."""
    result = _analyze(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    anchored = [
        (by_id[e.src], e.line) for e in result.edges
        if e.edge_type in ("calls", "instantiates", "references")
        and e.line and e.src in by_id
    ]
    assert {line for _, line in anchored} >= set(range(5, 18)), anchored  # reach
    for src, line in anchored:
        assert src.kind != "class", (src.name, line)
        assert src.span.start_line <= line <= src.span.end_line, (src.name, line)
    # The two indexers are distinct symbols, each holding its own line.
    srcs_9 = {by_id[e.src].id for e in result.edges if e.line == 9 and e.edge_type == "calls"}
    srcs_10 = {by_id[e.src].id for e in result.edges if e.line == 10 and e.edge_type == "calls"}
    assert len(srcs_9) == 1 and len(srcs_10) == 1 and srcs_9 != srcs_10


def test_an_object_creation_in_an_accessor_is_emitted(tmp_path: Path) -> None:
    """``new B()`` in a getter: the instantiates arm needs an enclosing member
    and found none, so the edge was dropped."""
    made = _edges_by_line(_analyze(tmp_path), "instantiates")
    assert made[15] == [("property", "A.Made", "B.B")], made


def test_a_method_group_in_an_accessor_is_emitted(tmp_path: Path) -> None:
    refs = _edges_by_line(_analyze(tmp_path), "references")
    assert refs[16] == [("property", "A.Grouped", "A.Consume")], refs


def test_a_local_function_is_walked_past_to_its_method(tmp_path: Path) -> None:
    """A local function has no symbol: its body's call stays on the method
    that declares it, not on the class (the walk passes a symbol-less node)."""
    calls = _edges_by_line(_analyze(tmp_path), "calls")
    assert ("method", "A.Run", "A.Helper") in calls[17], calls
