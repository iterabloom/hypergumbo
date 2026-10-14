# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for the mainstream member kinds that rode the floor (WI-tusad).

Scala, Kotlin, C#, Swift and PHP compute the ADR-0035 producer formula
(``make_typed_stable_id``) for their classes' methods and fields, but a few
sibling kinds computed nothing and rode the backstop floor key ``(kind,
language, path, name)``: Scala ``object`` and named ``given`` (``instance``),
Kotlin ``object``, C# ``property``, Swift computed ``property`` and
``subscript``, PHP properties and class constants (``field``). The floor key
has no scope, so two nested ``object Inner`` in classes ``A`` and ``B`` (both
named ``Inner``) shared it and both were null; two Swift subscript overloads
of one label tied the same way. Each now mints ``make_typed_stable_id`` over
its declared type (a subscript: its parameter types) and its full scope
chain, like its producer's fields.

What is pinned, per language, on the analyzer's own output: the exact value,
that it is not the floor key, that same-name members hold distinct ids
(symbol by symbol), and the delete-an-earlier-sibling property (the deleted
lines are kept as blank lines so each survivor matches itself by line). The
methods and fields beside them keep the ids they had.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from hypergumbo_core.analyze.base import (
    AnalysisResult, floor_stable_id_key, make_file_stable_id, make_typed_stable_id,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_mainstream.csharp import analyze_csharp
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin
from hypergumbo_lang_mainstream.php import analyze_php
from hypergumbo_lang_mainstream.scala import analyze_scala
from hypergumbo_lang_mainstream.swift import analyze_swift, normalize_swift_signature

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


def _assert_pinned(
    by_line: dict[int, Symbol], lang: str, path: str,
    expected: dict[int, tuple[str, str, str, str]],
) -> None:
    """``expected``: line -> (bare name, typed signature, visibility, qualified)."""
    assert set(by_line) == set(expected)  # reach
    anchor = make_file_stable_id(lang, path)
    for line, (name, sig, visibility, qualified) in expected.items():
        sym = by_line[line]
        assert sym.stable_id == make_typed_stable_id(
            sym.kind, sig, visibility, name=name, qualified_name=qualified,
            file_stable_id=anchor,
        ), (line, sym.name)
        assert sym.stable_id != floor_stable_id_key(sym.kind, lang, path, sym.name)
    ids = [s.stable_id for s in by_line.values()]
    assert len(set(ids)) == len(ids)


# -- scala ---------------------------------------------------------------------

_SCALA = (
    "class A {\n"
    "  object Inner { def go(): Int = 1 }\n"
    "}\n"
    "class B {\n"
    "  object Inner { def go(): Int = 2 }\n"
    "}\n"
    "private object Helper {\n"
    "  given intOrd: Ordering[Int] = Ordering.Int\n"
    "}\n"
)


def test_scala_objects_and_givens_hash_the_full_scope_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_scala, tmp_path, "M.scala", _SCALA)
    by_line = _by_line(syms, {"object", "instance"})
    _assert_pinned(by_line, "scala", "M.scala", {
        2: ("Inner", "", "", "A.Inner"),
        5: ("Inner", "", "", "B.Inner"),
        7: ("Helper", "", "private", "Helper"),
        8: ("intOrd", "", "", "Helper.intOrd"),
    })
    assert by_line[2].name == by_line[5].name == "Inner"  # names stay bare
    _assert_deletion_moves_nothing(
        analyze_scala, tmp_path / "del", "M.scala", _SCALA,
        "class A {\n  object Inner { def go(): Int = 1 }\n}\n",
        {"object", "instance"}, survivors=3,
    )


# -- kotlin --------------------------------------------------------------------

_KOTLIN = (
    "package demo\n"
    "class Mod {\n"
    "    object Registry {\n"
    "        fun go(): Int = 1\n"
    "    }\n"
    "}\n"
    "internal object Registry {\n"
    "    fun go(): Int = 2\n"
    "}\n"
)


def test_kotlin_objects_hash_the_package_and_class_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_kotlin, tmp_path, "Mod.kt", _KOTLIN)
    by_line = _by_line(syms, {"object"})
    _assert_pinned(by_line, "kotlin", "Mod.kt", {
        3: ("Registry", "", "", "demo.Mod.Registry"),
        7: ("Registry", "", "internal", "demo.Registry"),
    })
    _assert_deletion_moves_nothing(
        analyze_kotlin, tmp_path / "del", "Mod.kt", _KOTLIN,
        "    object Registry {\n        fun go(): Int = 1\n    }\n",
        {"object"}, survivors=1,
    )


# -- csharp --------------------------------------------------------------------

_CSHARP = (
    "namespace Demo {\n"
    "  class A {\n"
    "    class Inner { public int Size { get; set; } }\n"
    "  }\n"
    "  class B {\n"
    "    class Inner { private string Size => \"x\"; }\n"
    "  }\n"
    "}\n"
)


def test_csharp_properties_hash_type_and_full_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_csharp, tmp_path, "M.cs", _CSHARP)
    by_line = _by_line(syms, {"property"})
    _assert_pinned(by_line, "csharp", "M.cs", {
        3: ("Size", "int", "public", "Demo.A.Inner.Size"),
        6: ("Size", "string", "private", "Demo.B.Inner.Size"),
    })
    assert by_line[3].name == by_line[6].name == "Inner.Size"
    _assert_deletion_moves_nothing(
        analyze_csharp, tmp_path / "del", "M.cs", _CSHARP,
        "  class A {\n    class Inner { public int Size { get; set; } }\n  }\n",
        {"property"}, survivors=1,
    )


# -- swift ---------------------------------------------------------------------

_SWIFT = (
    "class Mod {\n"
    "    public var area: Int { return 0 }\n"
    "    subscript(i: Int) -> Int { return i }\n"
    "    subscript(i: String) -> Int { return 0 }\n"
    "    var bare { return 0 }\n"
    "}\n"
)


def test_swift_computed_properties_and_subscripts_get_producer_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_swift, tmp_path, "Mod.swift", _SWIFT)
    by_line = _by_line(syms, {"property", "subscript"})
    sig_int = normalize_swift_signature("(i: Int) -> Int") or ""
    sig_str = normalize_swift_signature("(i: String) -> Int") or ""
    assert sig_int != sig_str  # the overloads differ only in the parameter type
    _assert_pinned(by_line, "swift", "Mod.swift", {
        2: ("area", "() -> Int", "public", "Mod.area"),
        3: ("subscript(i:)", sig_int, "", "Mod.subscript(i:)"),
        4: ("subscript(i:)", sig_str, "", "Mod.subscript(i:)"),
        5: ("bare", "", "", "Mod.bare"),  # no declared type: the empty signature
    })
    _assert_deletion_moves_nothing(
        analyze_swift, tmp_path / "del", "Mod.swift", _SWIFT,
        "    subscript(i: Int) -> Int { return i }\n",
        {"property", "subscript"}, survivors=3,
    )


# -- php -----------------------------------------------------------------------

_PHP = (
    "<?php\n"
    "namespace App;\n"
    "class A {\n"
    "    private $size = 0;\n"
    "    const LIMIT = 3;\n"
    "}\n"
    "class B {\n"
    "    public $size = 1;\n"
    "}\n"
    "const TOP = 1;\n"
)


def test_php_fields_hash_the_namespace_and_type(tmp_path: Path) -> None:
    syms = _symbols(analyze_php, tmp_path, "M.php", _PHP)
    by_line = _by_line(syms, {"field"})
    _assert_pinned(by_line, "php", "M.php", {
        4: ("size", "", "private", "App\\A::size"),
        5: ("LIMIT", "", "", "App\\A::LIMIT"),
        8: ("size", "", "public", "App\\B::size"),
    })
    # A global constant keeps the backstop's variable table formula.
    top = next(s for s in syms if s.kind == "variable" and s.name == "TOP")
    assert top.stable_id is None
    _assert_deletion_moves_nothing(
        analyze_php, tmp_path / "del", "M.php", _PHP,
        "class A {\n    private $size = 0;\n    const LIMIT = 3;\n}\n",
        {"field"}, survivors=1,
    )
