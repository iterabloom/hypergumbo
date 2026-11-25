# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on PureScript's call edges (audit 0030, WI-hirol).

``Util.helper`` found in the run-wide table under the module the call site
writes is ``qualified``; a bare name found under the caller's own module
(``Main.run``) is ``same_module``; a bare name found as it stands (a symbol
of a file with no module header) is ``name``; a miss carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.purescript import analyze_purescript


def _edge(edges, src: str, dst: str):
    found = [
        e for e in edges
        if e.edge_type == "calls" and f":{src}:" in e.src and f":{dst}:" in e.dst
    ]
    assert len(found) == 1, f"calls {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


def test_purescript_routes(tmp_path: Path) -> None:
    (tmp_path / "Util.purs").write_text(
        "module Util where\n\nhelper :: Int -> Int\nhelper x = x\n"
    )
    (tmp_path / "Main.purs").write_text(
        "module Main where\n\nimport Util as U\n\n"
        "run :: Int -> Int\nrun x = Util.helper x\n\n"
        "go :: Int -> Int\ngo y = run y\n\n"
        "ext :: Int -> Int\next z = unknownFn z\n"
    )
    edges = analyze_purescript(tmp_path).edges
    assert _edge(edges, "Main.run", "Util.helper").meta["resolved_by"] == "qualified"
    assert _edge(edges, "Main.go", "Main.run").meta["resolved_by"] == "same_module"
    external = _edge(edges, "Main.ext", "unknownFn")
    assert "resolved_by" not in (external.meta or {})


def test_bare_name_in_the_table_is_name(tmp_path: Path) -> None:
    (tmp_path / "Bare.purs").write_text("helper :: Int -> Int\nhelper x = x\n")
    (tmp_path / "Main.purs").write_text(
        "module Main where\n\ngo :: Int -> Int\ngo y = helper y\n"
    )
    edges = analyze_purescript(tmp_path).edges
    assert _edge(edges, "Main.go", "helper").meta["resolved_by"] == "name"
