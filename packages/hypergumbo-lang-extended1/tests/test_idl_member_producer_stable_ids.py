# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for Cap'n Proto, Zig and GDScript members (WI-tusad).

These analyzers computed no ``stable_id`` for the kinds below, which rode the
ADR-0035 backstop floor key ``(kind, language, path, name)``. The floor key has
no scope, so a name repeated in two scopes of one file -- Cap'n Proto ``run``
in interfaces ``A`` and ``B``, GDScript ``run`` in inner classes ``A`` and
``B`` -- shared it, and both members were null (Zig names carry their
container, so its floor was exact until two nested containers shared a name).
Each producer now mints the ADR-0035 formula over its full scope chain: Cap'n
Proto methods and consts with ``overload_arity.declared_arity_stable_id`` over
the enclosing structs and interfaces; Zig and GDScript functions through
``compute_stable_id`` (parameter count and flags), their other kinds
(Zig fields, unions, error sets, tests; GDScript signals) through
``declared_arity_stable_id`` with no arity.

What is pinned, per language, on the analyzer's own output: every same-name
member holds its own non-null id (checked symbol by symbol, never through a
dict keyed on name); the exact value (so a producer that drifted onto other
inputs fails here); and the delete-an-earlier-sibling property (the deleted
lines are kept as blank lines so each survivor matches itself by line).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence

from hypergumbo_core.analyze.base import (
    AnalysisResult, floor_stable_id_key,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import (
    declared_arity_of_entries, declared_arity_stable_id, parameter_entry,
)
from hypergumbo_lang_extended1.capnp import analyze_capnp
from hypergumbo_lang_extended1.gdscript import analyze_gdscript
from hypergumbo_lang_extended1.zig import analyze_zig

Analyze = Callable[[Path], AnalysisResult]


def _symbols(analyze: Analyze, root: Path, name: str, text: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return list(analyze(root).symbols)


def _by_line(symbols: list[Symbol], kinds: set[str]) -> dict[int, Symbol]:
    picked = [s for s in symbols if s.kind in kinds and s.span is not None]
    by_line = {s.span.start_line: s for s in picked}  # type: ignore[union-attr]
    assert len(by_line) == len(picked), "two symbols on one line"
    return by_line


def _assert_deletion_moves_nothing(
    analyze: Analyze, tmp_path: Path, name: str, text: str, deleted: str,
    kinds: set[str], survivors: int,
) -> None:
    """Delete ``deleted`` (replaced by as many blank lines) and compare."""
    assert deleted in text
    edited = text.replace(deleted, "\n" * deleted.count("\n"))
    before = _by_line(_symbols(analyze, tmp_path / "r1", name, text), kinds)
    after = _by_line(_symbols(analyze, tmp_path / "r2", name, edited), kinds)
    assert len(after) == survivors < len(before)  # reach
    for line, sym in after.items():
        assert sym.stable_id is not None, (line, sym.name)
        assert sym.stable_id == before[line].stable_id, (line, sym.name)


def _expected(
    kind: str, lang: str, path: str, name: str, qualified: str,
    params: Optional[Sequence[str]] = None,
) -> str:
    """The formula's value; ``params`` (untyped names) for a function, whose
    ``compute_stable_id`` value is the untyped tier over the same count."""
    arity = (
        declared_arity_of_entries([parameter_entry(p) for p in params])
        if params is not None else None
    )
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name, qualified_name=qualified,
        arity=arity,
    )


def _assert_pinned(
    by_line: dict[int, Symbol], lang: str, path: str,
    expected: dict[int, tuple[str, str, Optional[Sequence[str]]]],
) -> None:
    """``expected``: line -> (bare name, qualified chain hashed, params)."""
    assert set(by_line) == set(expected)  # reach
    for line, (name, qualified, params) in expected.items():
        sym = by_line[line]
        assert sym.stable_id == _expected(
            sym.kind, lang, path, name, qualified, params), (line, sym.name)
        assert sym.stable_id != floor_stable_id_key(sym.kind, lang, path, sym.name)
    ids = [s.stable_id for s in by_line.values()]
    assert len(set(ids)) == len(ids)


# -- capnp ---------------------------------------------------------------------

_CAPNP = (
    "@0xdbb9ad1f14bf0b36;\n"
    "interface A {\n"
    "  run @0 (x :Int32) -> (y :Int32);\n"
    "}\n"
    "struct Outer {\n"
    "  const limit :Int32 = 3;\n"
    "  interface A {\n"
    "    run @0 (x :Int32) -> (y :Int32);\n"
    "  }\n"
    "}\n"
    "const limit :Int32 = 4;\n"
)


def test_capnp_methods_and_consts_hash_the_full_scope_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_capnp, tmp_path, "demo.capnp", _CAPNP)
    by_line = _by_line(syms, {"method", "const"})
    _assert_pinned(by_line, "capnp", "demo.capnp", {
        3: ("run", "A.run", None),
        6: ("limit", "Outer.limit", None),
        8: ("run", "Outer.A.run", None),   # the immediate interface named it A.run
        11: ("limit", "limit", None),
    })
    assert by_line[8].qualified_name == "Outer.A.run"
    _assert_deletion_moves_nothing(
        analyze_capnp, tmp_path / "del", "demo.capnp", _CAPNP,
        "interface A {\n  run @0 (x :Int32) -> (y :Int32);\n}\n",
        {"method", "const"}, survivors=3,
    )


# -- zig -----------------------------------------------------------------------

_ZIG = (
    "const A = struct {\n"
    "    const Inner = struct {\n"
    "        v: i32,\n"
    "        fn run(self: Inner) i32 { return self.v; }\n"
    "    };\n"
    "};\n"
    "const B = struct {\n"
    "    const Inner = struct {\n"
    "        v: i32,\n"
    "        fn run(self: Inner, x: i32) i32 { return x; }\n"
    "    };\n"
    "};\n"
    "const U = union {\n"
    "    a: i32,\n"
    "};\n"
    "const E = error { Bad };\n"
    "fn helper() i32 {\n"
    "    const Local = struct { w: i32 };\n"
    "    _ = Local;\n"
    "    return 2;\n"
    "}\n"
    'test "helper works" { _ = helper(); }\n'
)


def test_zig_symbols_hash_the_full_container_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_zig, tmp_path, "demo.zig", _ZIG)
    kinds = {"function", "method", "field", "union", "error_set", "test"}
    by_line = _by_line(syms, kinds)
    _assert_pinned(by_line, "zig", "demo.zig", {
        3: ("v", "A.Inner.v", None),
        4: ("run", "A.Inner.run", ["self"]),
        9: ("v", "B.Inner.v", None),
        10: ("run", "B.Inner.run", ["self", "x"]),
        13: ("U", "U", None),
        14: ("a", "U.a", None),
        16: ("E", "E", None),
        17: ("helper", "helper", []),
        18: ("w", "helper.Local.w", None),   # a local container sits under its function
        22: ('test "helper works"', 'test "helper works"', None),
    })
    # The emitted names keep the immediate container only.
    assert by_line[4].name == by_line[10].name == "Inner.run"
    # Structs keep the backstop's table formula.
    assert all(s.stable_id is None for s in syms if s.kind == "struct")
    _assert_deletion_moves_nothing(
        analyze_zig, tmp_path / "del", "demo.zig", _ZIG,
        "const A = struct {\n    const Inner = struct {\n        v: i32,\n"
        "        fn run(self: Inner) i32 { return self.v; }\n    };\n};\n",
        kinds, survivors=8,
    )


# -- gdscript ------------------------------------------------------------------

_GD = (
    "extends Node\n"
    "signal hit(damage)\n"
    "class A:\n"
    "\tfunc run():\n"
    "\t\treturn 1\n"
    "\n"
    "class B:\n"
    "\tsignal hit\n"
    "\tfunc run(x, y = 2):\n"
    "\t\treturn x\n"
    "\n"
    "func helper():\n"
    "\treturn 2\n"
)


def test_gdscript_functions_and_signals_hash_the_inner_class_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_gdscript, tmp_path, "demo.gd", _GD)
    by_line = _by_line(syms, {"function", "signal"})
    assert set(by_line) == {2, 4, 8, 9, 12}  # reach
    assert by_line[2].stable_id == _expected("signal", "gdscript", "demo.gd", "hit", "hit")
    assert by_line[8].stable_id == _expected("signal", "gdscript", "demo.gd", "hit", "B.hit")
    assert by_line[4].stable_id == _expected(
        "function", "gdscript", "demo.gd", "run", "A.run", [])
    assert by_line[12].stable_id == _expected(
        "function", "gdscript", "demo.gd", "helper", "helper", [])
    # ``y = 2`` is a defaulted parameter: the flags carry it.
    defaulted = declared_arity_of_entries(
        [parameter_entry("x"), parameter_entry("y", default=True)])
    assert by_line[9].stable_id == declared_arity_stable_id(
        kind="function", lang="gdscript", path="demo.gd", name="run",
        qualified_name="B.run", arity=defaulted,
    )
    ids = [s.stable_id for s in by_line.values()]
    assert len(set(ids)) == len(ids)
    _assert_deletion_moves_nothing(
        analyze_gdscript, tmp_path / "del", "demo.gd", _GD,
        "class A:\n\tfunc run():\n\t\treturn 1\n", {"function", "signal"},
        survivors=4,
    )
