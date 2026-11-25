# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Haskell's call edges (audit 0030, WI-hirol).

Both call positions share one builder, which binds through the run-wide
resolver (a qualified ``U.helper`` with its import as the path hint): the
lookup's own value when resolved, ``name`` here (an exact key is returned
before the hint is read). An external callee carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.haskell import analyze_haskell


def _edge(edges, src: str, dst: str):
    found = [
        e for e in edges
        if e.edge_type == "calls" and f":{src}:" in e.src and f":{dst}:" in e.dst
    ]
    assert len(found) == 1, f"calls {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


def test_haskell_resolver_routes(tmp_path: Path) -> None:
    (tmp_path / "Util.hs").write_text(
        "module Util (helper) where\n\nhelper :: Int -> Int\nhelper x = x\n"
    )
    (tmp_path / "Main.hs").write_text(
        "module Main where\n\nimport qualified Util as U\n\n"
        "run :: Int -> Int\nrun x = U.helper x\n\n"
        "go :: Int -> Int\ngo y = run y\n\n"
        "main :: IO ()\nmain = putStrLn (show (go 1))\n"
    )
    edges = analyze_haskell(tmp_path).edges
    assert _edge(edges, "run", "helper").meta["resolved_by"] == "name"
    assert _edge(edges, "go", "run").meta["resolved_by"] == "name"
    external = _edge(edges, "main", "putStrLn")
    assert not external.is_resolved
    assert "resolved_by" not in (external.meta or {})
