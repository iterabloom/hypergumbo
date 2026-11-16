# SPDX-License-Identifier: AGPL-3.0-or-later
"""A construction of a function-local type reaches the type its call site can see.

``T(...)`` resolved ``T`` through the file's name-keyed registry
(``local_symbols``), which keeps ONE symbol per name: the last declared. With a
``struct Inner`` local to ``func f`` and another local to ``func g``, f's
``Inner(x: 1)`` resolved to g's ``Inner`` and landed on g's ``init``; and a call
to a top-level ``Shadow`` outside a function that declares its own local
``Shadow`` landed on the local one when that was declared later.

A construction now resolves its type by Swift's lexical scoping: the innermost
block, type body or file around the call that declares a type of that name.
A type declared inside a function, closure or accessor is never reached from
outside it -- not through the file registry, not through the run-wide
resolver, and not as an ``init`` candidate of ``T.init`` / ``super.init``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_lang_mainstream.swift import analyze_swift


def _run(root: Path, files: dict[str, str]) -> tuple[list[Symbol], list[Edge]]:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    result = analyze_swift(root)
    return list(result.symbols), list(result.edges)


def _calls(symbols: list[Symbol], edges: list[Edge]) -> dict[tuple[str, int], list[str]]:
    """``(source name, call line)`` -> the targets, as ``name@path:line`` (or the raw dst)."""
    by_id = {s.id: s for s in symbols}
    out: dict[tuple[str, int], list[str]] = {}
    for e in edges:
        if e.edge_type != "calls":
            continue
        src = by_id.get(e.src)
        dst = by_id.get(e.dst)
        target = f"{dst.name}@{dst.path}:{dst.span.start_line}" if dst else e.dst
        out.setdefault((src.name if src else e.src, e.line), []).append(target)
    return out


def test_the_items_repro(tmp_path: Path) -> None:
    """f's construction reaches the init at line 2, g's the init at line 6."""
    symbols, edges = _run(tmp_path, {"a.swift": (
        "func f() {\n"
        "    struct Inner { init(x: Int) {} ; func go() {} }\n"
        "    _ = Inner(x: 1)\n"
        "}\n"
        "func g() {\n"
        "    struct Inner { init(x: Int) {} ; func go() {} }\n"
        "    _ = Inner(x: 2)\n"
        "}\n"
    )})
    calls = _calls(symbols, edges)
    assert calls[("f", 3)] == ["Inner.init@a.swift:2"]
    assert calls[("g", 7)] == ["Inner.init@a.swift:6"]


_FORMS = (
    "        struct Inner {\n"
    "            init(x: Int) {}\n"
    "            init?(s: String) { return nil }\n"
    "            struct Deep { init(d: Int) {} }\n"
    "        }\n"
    "        class K {\n"
    "            init() {}\n"
    "            convenience init(a: Int) { self.init() }\n"
    "        }\n"
    "        class Base { init(b: Int) {} }\n"
    "        class Derived: Base { init() { super.init(b: 1) } }\n"
    "        _ = Inner(x: 1)\n"
    "        _ = Inner(s: \"\")\n"
    "        _ = Inner.init(x: 2)\n"
    "        _ = K(a: 3)\n"
    "        _ = Inner.Deep.init(d: 4)\n"
)

#: Where the twin copies of ``_FORMS`` live: (text before f's copy, text
#: between the copies, text after g's copy, the caller each copy's calls are
#: credited to).
_CONTAINERS: dict[str, tuple[str, str, str, tuple[str, str]]] = {
    "function": ("func f() {\n", "}\nfunc g() {\n", "}\n", ("f", "g")),
    "closure-in-function": (
        "func f() {\n    _ = {\n", "    }\n}\nfunc g() {\n    _ = {\n", "    }\n}\n",
        ("f", "g"),
    ),
    "method-of-another-type": (
        "struct Host {\n    func f() {\n", "    }\n    func g() {\n", "    }\n}\n",
        ("Host.f", "Host.g"),
    ),
    "computed-property-getter": (
        "struct Host {\n    var f: Int {\n", "        return 0\n    }\n    var g: Int {\n",
        "        return 0\n    }\n}\n",
        ("Host.f", "Host.g"),
    ),
    "method-of-a-local-type": (
        "func top() {\n    struct L {\n        func f() {\n",
        "        }\n        func g() {\n", "        }\n    }\n}\n",
        ("L.f", "L.g"),
    ),
}


@pytest.mark.parametrize("container", sorted(_CONTAINERS))
def test_each_construction_reaches_its_own_copys_init(tmp_path: Path, container: str) -> None:
    """``T(...)``, a failable init, ``T.init``, a convenience init, a nested
    ``T.U.init`` and ``super.init``: in both twin copies, every one lands on an
    init declared inside that copy, and never as an ambiguous record."""
    before, between, after, (src_f, src_g) = _CONTAINERS[container]
    text = before + _FORMS + between + _FORMS + after
    symbols, edges = _run(tmp_path, {"a.swift": text})
    first_line = before.count("\n") + 1
    f_lines = range(first_line, first_line + _FORMS.count("\n"))
    g_first = (before + _FORMS + between).count("\n") + 1
    g_lines = range(g_first, g_first + _FORMS.count("\n"))
    by_id = {s.id: s for s in symbols}
    inits = [
        e for e in edges
        if e.edge_type == "calls" and e.dst in by_id and by_id[e.dst].kind == "constructor"
    ]
    checked = 0
    for edge in inits:
        target = by_id[edge.dst]
        own = f_lines if edge.line in f_lines else g_lines
        assert target.span.start_line in own, (edge.line, target.name, target.span.start_line)
        assert (edge.meta or {}).get("resolution_quality") != "ambiguous", edge.line
        checked += 1
    # Per copy: Inner(x:), Inner(s:), Inner.init(x:), K(a:), Deep.init(d:),
    # super.init(b:), and K's delegating self.init().
    assert checked == 2 * 7, sorted((e.line, by_id[e.dst].name) for e in inits)
    sources = {by_id[e.src].name for e in inits if e.src in by_id}
    assert {src_f, src_g} <= sources


@pytest.mark.parametrize("local_first", [True, False])
def test_a_local_type_shadows_a_top_level_one_only_inside_its_function(
    tmp_path: Path, local_first: bool,
) -> None:
    """Inside ``shadowing`` the local ``Shadow`` answers; anywhere else the
    top-level one does, whichever of the two the file declares last."""
    top = "struct Shadow {\n    init(x: Int) {}\n}\n"
    local = (
        "func shadowing() {\n"
        "    struct Shadow { init(x: Int) {} }\n"
        "    _ = Shadow(x: 1)\n"
        "}\n"
    )
    use = "func elsewhere() {\n    _ = Shadow(x: 2)\n    _ = Shadow.init(x: 3)\n}\n"
    text = local + top + use if local_first else top + local + use
    symbols, edges = _run(tmp_path, {"a.swift": text})
    lines = text.splitlines()
    top_init = lines.index("    init(x: Int) {}") + 1
    local_init = lines.index("    struct Shadow { init(x: Int) {} }") + 1
    calls = _calls(symbols, edges)
    assert calls[("shadowing", lines.index("    _ = Shadow(x: 1)") + 1)] == [
        f"Shadow.init@a.swift:{local_init}"
    ]
    assert calls[("elsewhere", lines.index("    _ = Shadow(x: 2)") + 1)] == [
        f"Shadow.init@a.swift:{top_init}"
    ]
    assert calls[("elsewhere", lines.index("    _ = Shadow.init(x: 3)") + 1)] == [
        f"Shadow.init@a.swift:{top_init}"
    ]


@pytest.mark.parametrize("other_file", ["a_top.swift", "z_top.swift"])
def test_a_local_type_never_answers_for_a_type_of_another_file(
    tmp_path: Path, other_file: str,
) -> None:
    """The run-wide registry keeps one symbol per name, the last registered.
    When that is a function-local type, a call outside its function reaches
    the other file's top-level type instead."""
    symbols, edges = _run(tmp_path, {
        "m.swift": (
            "func shadowing() {\n"
            "    struct Shadow { init(x: Int) {} }\n"
            "    _ = Shadow(x: 1)\n"
            "}\n"
            "func elsewhere() {\n"
            "    _ = Shadow(x: 2)\n"
            "    _ = Shadow.init(x: 3)\n"
            "}\n"
        ),
        other_file: "struct Shadow {\n    init(x: Int) {}\n}\n",
    })
    calls = _calls(symbols, edges)
    assert calls[("shadowing", 3)] == ["Shadow.init@m.swift:2"]
    assert calls[("elsewhere", 6)] == [f"Shadow.init@{other_file}:2"]
    assert calls[("elsewhere", 7)] == [f"Shadow.init@{other_file}:2"]


def test_a_local_type_is_not_reached_from_outside_its_function(tmp_path: Path) -> None:
    """With no other ``Hidden`` to reach, a call outside ``f`` binds nothing
    of f's: neither the type nor its init (Swift would not compile it)."""
    symbols, edges = _run(tmp_path, {"a.swift": (
        "func f() {\n"
        "    struct Hidden { init() {} }\n"
        "    _ = Hidden()\n"
        "}\n"
        "func g() {\n"
        "    _ = Hidden()\n"
        "    _ = Hidden.init()\n"
        "}\n"
    )})
    calls = _calls(symbols, edges)
    assert calls[("f", 3)] == ["Hidden.init@a.swift:2"]
    for line in (6, 7):
        assert all("@a.swift" not in t for t in calls[("g", line)]), calls[("g", line)]


def test_a_metatype_value_init_binds_no_local_init(tmp_path: Path) -> None:
    """``let t = Meta.self; t.init()``: the receiver is a VALUE, which this pass
    does not type to a declaration; the call stays unresolved rather than
    reaching another function's ``Meta.init``."""
    symbols, edges = _run(tmp_path, {"a.swift": (
        "func f() {\n"
        "    struct Meta { init() {} }\n"
        "    let t = Meta.self\n"
        "    _ = t.init()\n"
        "}\n"
        "func g() {\n"
        "    struct Meta { init() {} }\n"
        "}\n"
    )})
    calls = _calls(symbols, edges)
    assert all("Meta.init@" not in t for t in calls[("f", 4)]), calls[("f", 4)]


def test_a_local_type_is_not_replaced_by_a_nested_one(tmp_path: Path) -> None:
    """When the registry's answer is a local type out of scope, its stand-in is
    a TOP-LEVEL type of that name only: a nested ``Outer.Alert`` is not
    reached by a bare ``Alert`` outside ``Outer`` (on
    swift-composable-architecture a SwiftUI ``Alert(...)`` call was bound to a
    test's local enum). One file, so the local ``Alert`` is the last one
    registered whatever order files are read in."""
    symbols, edges = _run(tmp_path, {"a.swift": (
        "struct Outer {\n    enum Alert { case x\n        init() { self = .x } }\n}\n"
        "func show() {\n    _ = Alert()\n}\n"
        "func t() {\n    enum Alert { case y\n        init() { self = .y } }\n}\n"
    )})
    calls = _calls(symbols, edges)
    assert all("@" not in t for t in calls[("show", 6)]), calls[("show", 6)]



def test_a_segment_a_local_types_body_does_not_declare_names_nothing(tmp_path: Path) -> None:
    """``Outer.Real`` resolves through the local ``Outer``'s body.
    ``Outer.Missing``, which that body does not declare and which no
    ``extension`` can add to a local type, names no type: neither a top-level
    ``Missing``'s init (an owner-suffix match) nor one in its extension, nor
    ``Outer`` itself, is reached by ``Outer.Missing.init(...)`` or
    ``Outer.Missing(...)``."""
    symbols, edges = _run(tmp_path, {"a.swift": (
        "struct Missing { init(x: Int) {} }\n"
        "extension Missing { init(y: Int) { self.init(x: y) } }\n"
        "func f() {\n"
        "    struct Outer {\n"
        "        init(x: Int) {}\n"
        "        struct Real { init(x: Int) {} }\n"
        "    }\n"
        "    _ = Outer.Real.init(x: 1)\n"
        "    _ = Outer.Missing.init(x: 2)\n"
        "    _ = Outer.Missing.init(y: 3)\n"
        "    _ = Outer.Missing(x: 4)\n"
        "}\n"
    )})
    calls = _calls(symbols, edges)
    assert calls[("f", 8)] == ["Real.init@a.swift:6"]
    for line in (9, 10, 11):
        assert all("@a.swift" not in t for t in calls.get(("f", line), [])), (
            line, calls.get(("f", line)),
        )
