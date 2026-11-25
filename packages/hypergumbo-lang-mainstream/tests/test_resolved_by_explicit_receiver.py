# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call on an explicit receiver is never ``own_type`` unless the receiver is the enclosing object.

Several analyzers send ``obj.m()`` on an untyped receiver down the same
name-lookup path as a bare ``m()``, and bind it when ``m`` turns out to be a
method of the caller's own class. For the bare call that is the language's
implicit-receiver rule, and ``resolved_by`` says ``own_type``. For ``obj.m()``
nothing says ``obj`` is the enclosing object: the method was found by its
name, and the mark says ``name`` (audit-findings 0030; the census counts
``name`` as the population that rests on a name alone, so ``own_type`` here
would hide those edges from it).

C#'s six ``calls`` routes are pinned here too: they were the worked example
the other analyzers followed.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/lua.py
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/csharp.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.js_ts import analyze_javascript
from hypergumbo_lang_mainstream.lua import analyze_lua
from hypergumbo_lang_mainstream.php import analyze_php


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).write_text(text)


def _mark(edges: list[Edge], caller: str, callee: str) -> str | None:
    """The mark of the one resolved call edge from ``caller`` to ``callee`` (name slots of the ids)."""
    found = [
        e for e in edges
        if e.edge_type == "calls"
        and e.src.split(":")[-2] == caller and e.dst.split(":")[-2] == callee
    ]
    assert len(found) == 1, (caller, callee, [(e.src, e.dst) for e in edges if e.edge_type == "calls"])
    return (found[0].meta or {}).get("resolved_by")


def test_javascript_untyped_receiver_is_name_and_bare_this_is_own_type(tmp_path: Path) -> None:
    _write(tmp_path, {"store.js": (
        "class Store {\n"
        "  persist() {}\n"
        "  viaThis() { this.persist(); }\n"
        "  viaOther(other) { other.persist(); }\n"
        "}\n"
    )})
    edges = analyze_javascript(tmp_path).edges
    assert _mark(edges, "Store.viaThis", "Store.persist") == "own_type"
    assert _mark(edges, "Store.viaOther", "Store.persist") == "name"


def test_php_untyped_receiver_is_name_and_this_is_own_type(tmp_path: Path) -> None:
    _write(tmp_path, {"Store.php": (
        "<?php\n"
        "class Store {\n"
        "  function persist() {}\n"
        "  function viaThis() { $this->persist(); }\n"
        "  function viaOther($other) { $other->persist(); }\n"
        "}\n"
    )})
    edges = analyze_php(tmp_path).edges
    assert _mark(edges, "Store.viaThis", "Store.persist") == "own_type"
    assert _mark(edges, "Store.viaOther", "Store.persist") == "name"


def test_lua_untyped_receiver_is_name_and_self_is_own_type(tmp_path: Path) -> None:
    _write(tmp_path, {"store.lua": (
        "Store = {}\n\n"
        "function Store:persist()\nend\n\n"
        "function Store:viaSelf()\n  self:persist()\nend\n\n"
        "function Store:viaOther(other)\n  other:persist()\nend\n"
    )})
    edges = analyze_lua(tmp_path).edges
    assert _mark(edges, "Store.viaSelf", "Store.persist") == "own_type"
    assert _mark(edges, "Store.viaOther", "Store.persist") == "name"


_CSHARP = {
    "Helper.cs": (
        "namespace B {\n"
        "  public class Helper {\n"
        "    public static void Process() {}\n"
        "    public void Run() {}\n"
        "  }\n"
        "  public static class Far { public static void Go() {} }\n"
        "}\n"
    ),
    "Main.cs": (
        "namespace A {\n"
        "  using B;\n"
        "  public class Near { public static void Hit() {} public void Poke() {} }\n"
        "  public class Main {\n"
        "    private Near _near;\n"
        "    public void Own() {}\n"
        "    public void Typed() { Near n = new Near(); n.Poke(); }\n"
        "    public void Chain() { this._near.Poke(); }\n"
        "    public void Bare() { Own(); }\n"
        "    public void QualifiedHere() { Near.Hit(); }\n"
        "    public void QualifiedThere() { Helper.Process(); }\n"
        "  }\n"
        "}\n"
    ),
}


def test_csharp_routes(tmp_path: Path) -> None:
    _write(tmp_path, _CSHARP)
    edges = analyze_csharp(tmp_path).edges
    assert _mark(edges, "Main.Typed", "Near.Poke") == "receiver_type"
    assert _mark(edges, "Main.Chain", "Near.Poke") == "receiver_type"
    assert _mark(edges, "Main.Bare", "Main.Own") == "own_type"
    assert _mark(edges, "Main.QualifiedHere", "Near.Hit") == "qualified"
    # Found through the repository-wide resolver, in another file: the owner is
    # still the one the call site names, not the enclosing type.
    assert _mark(edges, "Main.QualifiedThere", "Helper.Process") == "qualified"
