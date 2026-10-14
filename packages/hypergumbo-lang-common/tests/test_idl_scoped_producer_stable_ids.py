# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producer stable_ids for proto, thrift, QML, Nix and Dart members (WI-tusad).

These analyzers computed no ``stable_id`` for the kinds below, which rode the
ADR-0035 backstop floor key ``(kind, language, path, name)``. The floor key has
no scope, so a name repeated in two scopes of one file -- proto ``rpc Get`` in
services ``S`` and ``T``, a nested ``message Inner`` in ``A`` and ``B``, thrift
``run`` in two services, QML ``run`` in the root ``Item`` and a nested
``Rectangle``, Nix ``run`` in two attribute sets -- shared it, and both members
were null. Each producer now mints the ADR-0035 formula over its scope chain:
proto/thrift with ``overload_arity.declared_arity_stable_id`` over the
qualified name; QML over the chain of enclosing object declarations; Nix over
the attribute-path chain; Dart constructors, getters, setters, mixins and
extensions with ``make_typed_stable_id`` over the owner-qualified name.

What is pinned, per language, on the analyzer's own output: every same-name
member holds its own non-null id (checked symbol by symbol, never through a
dict keyed on name); the exact value (so a producer that drifted onto other
inputs fails here); and the delete-an-earlier-sibling property (the deleted
lines are kept as blank lines so each survivor matches itself by line).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from hypergumbo_core.analyze.base import (
    AnalysisResult, floor_stable_id_key, make_file_stable_id, make_typed_stable_id,
)
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import declared_arity_stable_id
from hypergumbo_lang_common.dart import analyze_dart
from hypergumbo_lang_common.nix import analyze_nix_files
from hypergumbo_lang_common.proto import analyze_proto
from hypergumbo_lang_common.qml import analyze_qml
from hypergumbo_lang_common.thrift import analyze_thrift

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


def _scoped(kind: str, lang: str, path: str, name: str, qualified: str) -> str:
    return declared_arity_stable_id(
        kind=kind, lang=lang, path=path, name=name, qualified_name=qualified,
        arity=None,
    )


def _assert_pinned(
    by_line: dict[int, Symbol], lang: str, path: str,
    expected: dict[int, tuple[str, str]],
) -> None:
    """``expected``: line -> (name, qualified chain hashed). Every id is the
    formula's value, none is the floor key, and no two are equal."""
    assert set(by_line) == set(expected)  # reach
    for line, (name, qualified) in expected.items():
        sym = by_line[line]
        assert sym.name == name, line
        assert sym.stable_id == _scoped(sym.kind, lang, path, name, qualified), line
        assert sym.stable_id != floor_stable_id_key(sym.kind, lang, path, name)
    ids = [s.stable_id for s in by_line.values()]
    assert len(set(ids)) == len(ids)


# -- proto ---------------------------------------------------------------------

_PROTO = (
    'syntax = "proto3";\n'
    "package demo;\n"
    "message A {\n"
    "  message B {\n"
    "    message Inner { int32 x = 1; }\n"
    "  }\n"
    "}\n"
    "message C {\n"
    "  message B {\n"
    "    message Inner { int32 y = 1; }\n"
    "  }\n"
    "}\n"
    "service S {\n"
    "  rpc Get (A) returns (C);\n"
    "}\n"
    "service T {\n"
    "  rpc Get (C) returns (A);\n"
    "}\n"
)


def test_proto_messages_and_rpcs_hash_the_full_scope_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_proto, tmp_path, "a.proto", _PROTO)
    by_line = _by_line(syms, {"message", "method"})
    _assert_pinned(by_line, "proto", "a.proto", {
        3: ("A", "demo.A"), 4: ("B", "demo.A.B"), 5: ("Inner", "demo.A.B.Inner"),
        8: ("C", "demo.C"), 9: ("B", "demo.C.B"), 10: ("Inner", "demo.C.B.Inner"),
        14: ("Get", "demo.S.Get"), 17: ("Get", "demo.T.Get"),
    })
    # The immediate parent alone named both ``Inner``s ``demo.B.Inner``.
    assert by_line[5].qualified_name == "demo.A.B.Inner"
    assert by_line[10].qualified_name == "demo.C.B.Inner"
    # Services keep the backstop's table formula.
    assert all(s.stable_id is None for s in syms if s.kind == "interface")


def test_proto_deleting_an_earlier_scope_moves_no_id(tmp_path: Path) -> None:
    _assert_deletion_moves_nothing(
        analyze_proto, tmp_path, "a.proto", _PROTO,
        "message A {\n  message B {\n    message Inner { int32 x = 1; }\n  }\n}\n",
        {"message", "method"}, survivors=5,
    )
    _assert_deletion_moves_nothing(
        analyze_proto, tmp_path / "rpc", "a.proto", _PROTO,
        "service S {\n  rpc Get (A) returns (C);\n}\n",
        {"message", "method"}, survivors=7,
    )


# -- thrift --------------------------------------------------------------------

_THRIFT = (
    "namespace py demo\n"
    "typedef i32 Size\n"
    "const i32 LIMIT = 3\n"
    "service A {\n"
    "  i32 run(1: i32 x)\n"
    "}\n"
    "service B {\n"
    "  i32 run(1: i32 x, 2: i32 y)\n"
    "}\n"
)


def test_thrift_functions_typedefs_and_consts_get_producer_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_thrift, tmp_path, "a.thrift", _THRIFT)
    by_line = _by_line(syms, {"function", "typedef", "const"})
    _assert_pinned(by_line, "thrift", "a.thrift", {
        2: ("Size", "demo.Size"), 3: ("LIMIT", "demo.LIMIT"),
        5: ("run", "demo.A.run"), 8: ("run", "demo.B.run"),
    })
    _assert_deletion_moves_nothing(
        analyze_thrift, tmp_path / "del", "a.thrift", _THRIFT,
        "service A {\n  i32 run(1: i32 x)\n}\n", {"function", "typedef", "const"},
        survivors=3,
    )


# -- qml -----------------------------------------------------------------------

_QML = (
    "import QtQuick 2.0\n"
    "Item {\n"
    "    function run() { return 1 }\n"
    "    signal done()\n"
    "    Rectangle {\n"
    "        id: box\n"
    "        function run(x) { return x }\n"
    '        property string label: "}{"\n'
    "    }\n"
    "    contentItem: Rectangle {\n"
    "        function run(x) { return x } // }\n"
    "    }\n"
    "    function helper(x) { return x }\n"
    "}\n"
)


def test_qml_symbols_hash_the_enclosing_object_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_qml, tmp_path, "Demo.qml", _QML)
    by_line = _by_line(syms, {"component", "function", "signal", "id"})
    # Braces in a string or a ``//`` comment do not close an element, an
    # element's label takes its ``id:`` (seen after the element opened), and
    # a property-bound element is labelled by its property.
    _assert_pinned(by_line, "qml", "Demo.qml", {
        2: ("Item", "Item"),
        3: ("run", "Item.run"),
        4: ("done", "Item.done"),
        6: ("box", "Item.Rectangle#box.box"),
        7: ("run", "Item.Rectangle#box.run"),
        11: ("run", "Item.contentItem:Rectangle.run"),
        13: ("helper", "Item.helper"),
    })
    _assert_deletion_moves_nothing(
        analyze_qml, tmp_path / "del", "Demo.qml", _QML,
        "    function run() { return 1 }\n",
        {"component", "function", "signal", "id"}, survivors=6,
    )


# -- nix -----------------------------------------------------------------------

_NIX = (
    "{ pkgs }:\n"
    "let\n"
    "  run = x: x;\n"
    "in {\n"
    "  a = { run = x: x; };\n"
    "  b = { run = x: y: x; };\n"
    "  c.d = { run = x: x; };\n"
    "  run = run;\n"
    "}\n"
)


def test_nix_bindings_hash_the_attribute_path_chain(tmp_path: Path) -> None:
    syms = _symbols(analyze_nix_files, tmp_path, "default.nix", _NIX)
    funcs = [s for s in syms if s.kind == "function"]
    by_pos = {(s.span.start_line, s.span.start_col): s for s in funcs}  # type: ignore[union-attr]
    assert len(by_pos) == len(funcs)
    expected = {
        (1, 0): ("default", "default"),          # the file-level function
        (3, 2): ("run", "default.let:run"),      # let-bound: never ties an attr
        (5, 8): ("run", "default.a.run"),
        (6, 8): ("run", "default.b.run"),
        (7, 10): ("run", "default.c.d.run"),     # the full attrpath, not ``c``
    }
    assert set(by_pos) == set(expected)  # reach
    for pos, (name, qualified) in expected.items():
        assert by_pos[pos].name == name
        assert by_pos[pos].stable_id == _scoped(
            "function", "nix", "default.nix", name, qualified), pos
    bindings = [s for s in syms if s.kind == "binding"]
    assert {s.name for s in bindings} == {"a", "b", "c", "run"}
    ids = [s.stable_id for s in syms]
    assert None not in ids and len(set(ids)) == len(ids)
    _assert_deletion_moves_nothing(
        analyze_nix_files, tmp_path / "del", "default.nix", _NIX,
        "  a = { run = x: x; };\n", {"function"}, survivors=4,
    )


# -- dart ----------------------------------------------------------------------

_DART = (
    "class Mod {\n"
    "  Mod();\n"
    "  Mod.named(int x);\n"
    "  int get size => 0;\n"
    "  set size(int v) {}\n"
    "  int run(int x) => x;\n"
    "}\n"
    "mixin Walks {}\n"
    "extension Twice on int {}\n"
)


def test_dart_unsigned_members_get_producer_ids(tmp_path: Path) -> None:
    syms = _symbols(analyze_dart, tmp_path, "mod.dart", _DART)
    kinds = {"constructor", "getter", "setter", "mixin", "extension"}
    by_line = _by_line(syms, kinds)
    anchor = make_file_stable_id("dart", "mod.dart")
    expected = {
        2: ("constructor", "Mod", "Mod.Mod"),
        3: ("constructor", "Mod.named", "Mod.Mod.named"),
        4: ("getter", "size", "Mod.size"),
        5: ("setter", "size", "Mod.size"),
        8: ("mixin", "Walks", "Walks"),
        9: ("extension", "Twice", "Twice"),
    }
    assert set(by_line) == set(expected)  # reach
    for line, (kind, name, full_name) in expected.items():
        sym = by_line[line]
        assert (sym.kind, sym.name) == (kind, full_name), line
        assert sym.stable_id == make_typed_stable_id(
            kind, "", "", name=name, qualified_name=full_name,
            file_stable_id=anchor,
        ), line
    ids = [s.stable_id for s in by_line.values()]
    assert len(set(ids)) == len(ids)
    # The method keeps its signature-typed id (not the empty-signature one).
    method = next(s for s in syms if s.kind == "method")
    assert method.stable_id != make_typed_stable_id(
        "method", "", "", name="run", qualified_name="Mod.run",
        file_stable_id=anchor,
    )
    _assert_deletion_moves_nothing(
        analyze_dart, tmp_path / "del", "mod.dart", _DART, "  Mod();\n",
        kinds, survivors=5,
    )
