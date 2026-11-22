# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Haskell top-level declaration's ``qualified_name`` is ``<Module>.<name>``.

The module is the one the file's ``module X.Y.Z (...) where`` header names, as
written; a file with no header is module ``Main`` (Haskell 2010 report §5.1:
the header is then ``module Main (main) where``). So ``main`` in such a file
is ``Main.main``, which is what tells a program's ``main`` from a ``main``
defined in some other module: GHC starts a program only at ``Main.main``.

Every named declaration symbol gets one (function, data, class, instance),
so no Haskell declaration has a ``None`` qualified name that the containment
linker could read as "the owner named by a child's qualified-name prefix".
The stable id does NOT change: its formula's ``qualified_name`` input stays the
bare name.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.haskell import analyze_haskell


def _symbols(tmp_path: Path, files: dict[str, str]) -> dict[tuple[str, str], Symbol]:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    result = analyze_haskell(tmp_path)
    return {(s.path, s.name): s for s in result.symbols if s.kind != "file"}


def _qn(syms: dict[tuple[str, str], Symbol], name: str) -> str | None:
    return next(s.qualified_name for (_, n), s in syms.items() if n == name)


def test_module_main_where(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, {"Main.hs": "module Main where\nmain = pure ()\n"})
    assert _qn(syms, "main") == "Main.main"


def test_headerless_file_is_module_main(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, {"Script.hs": "main :: IO ()\nmain = print 1\n"})
    assert _qn(syms, "main") == "Main.main"


def test_hierarchical_module(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, {
        "src/Lib/Thing.hs": "module Lib.Thing where\nmain = pure ()\nhelper x = x\n",
    })
    assert _qn(syms, "main") == "Lib.Thing.main"
    assert _qn(syms, "helper") == "Lib.Thing.helper"


def test_export_list_split_over_lines(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, {
        "Main.hs": "module Main\n  ( main\n  ) where\nmain = pure ()\n",
    })
    assert _qn(syms, "main") == "Main.main"


def test_shebang_pragmas_and_comments_before_header(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, {
        "Tool.hs": (
            "#!/usr/bin/env stack\n"
            "-- stack --resolver lts script\n"
            "{-# LANGUAGE OverloadedStrings #-}\n"
            "{- a block\n   comment -}\n"
            "module Tool.Cli (main) where\n"
            "main = pure ()\n"
        ),
    })
    assert _qn(syms, "main") == "Tool.Cli.main"


def test_type_class_and_instance_symbols(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, {
        "Shape.hs": (
            "module Shape where\n"
            "data Sq = Sq\n"
            "class Shape a where\n"
            "  area :: a -> Double\n"
            "instance Shape Sq where\n"
            "  area _ = 1\n"
        ),
    })
    assert _qn(syms, "Sq") == "Shape.Sq"
    assert _qn(syms, "Shape") == "Shape.Shape"
    assert _qn(syms, "Shape Sq") == "Shape.Shape Sq"


def test_stable_id_input_is_unchanged(tmp_path: Path) -> None:
    """The same function in two modules of one path keeps one stable id: the
    module is in ``qualified_name``, not in the stable-id formula's input."""
    a = _symbols(tmp_path / "a", {"F.hs": "module A where\nf x = x\n"})
    b = _symbols(tmp_path / "b", {"F.hs": "module B where\nf x = x\n"})
    fa = next(s for s in a.values() if s.name == "f")
    fb = next(s for s in b.values() if s.name == "f")
    assert (fa.qualified_name, fb.qualified_name) == ("A.f", "B.f")
    assert fa.stable_id == fb.stable_id


def test_a_module_named_like_a_declaration_gains_no_contains_edge(
    tmp_path: Path,
) -> None:
    """The containment linker reads a qualified name's prefix as an owner in
    the same file whose own qualified name is that prefix (or absent). For
    Haskell the prefix is the MODULE. A class named like its module, and an
    instance whose applied type leaves it named by its class alone, carry
    ``<Module>.<name>`` too, so neither may be taken for the module."""
    from hypergumbo_core.linkers.containment import link_containment
    from hypergumbo_core.linkers.registry import LinkerContext

    (tmp_path / "Shape.hs").write_text(
        "module Shape where\n"
        "class Shape a where\n"
        "  area :: a -> Double\n"
        "describe :: Shape a => a -> String\n"
        "describe = show . area\n"
    )
    (tmp_path / "Pretty.hs").write_text(
        "module Pretty where\n"
        "import Pretty.Class\n"
        "instance Pretty (Maybe a) where\n"
        "  pp _ = \"\"\n"
        "render :: Pretty a => a -> String\n"
        "render = pp\n"
    )
    result = analyze_haskell(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    assert {by_id[i].name for i in by_id if by_id[i].kind == "instance"} == {"Pretty"}
    linked = link_containment(LinkerContext(
        repo_root=tmp_path, symbols=list(result.symbols), edges=list(result.edges),
    ))
    owners = sorted(
        (by_id[e.dst].name, by_id[e.src].kind) for e in linked.edges
        if e.edge_type == "contains" and e.dst in by_id and e.src in by_id
        and by_id[e.dst].name in ("describe", "render")
    )
    assert [kind for _, kind in owners if kind in ("class", "instance")] == []


def test_damaged_header_leaves_the_module_unknown(tmp_path: Path) -> None:
    """A header the parser cannot read is not "no header": calling the file
    ``Main`` would make its ``main`` a program start."""
    syms = _symbols(tmp_path, {"Bad.hs": "modul Foo where\nmain = pure ()\n"})
    assert _qn(syms, "main") is None


def test_parse_damage_after_the_header_keeps_the_module(tmp_path: Path) -> None:
    syms = _symbols(tmp_path, {
        "Half.hs": (
            "module Half.Way where\nmain = pure ()\n\n"
            "broken x = case x of\n  1 ->\n"
        ),
    })
    assert _qn(syms, "main") == "Half.Way.main"
