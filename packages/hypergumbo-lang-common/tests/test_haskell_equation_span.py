# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, haskell: a function's span covers ALL of its equations.

A haskell function is written as consecutive equations, one per pattern. The
analyzer emitted one symbol per NAME, from the FIRST equation only, so its span
ended where the second equation began. Every call in a later equation was
anchored to the right function by name, but fell outside that function's span:
119 of 3,012 haskell call edges on a 26-repo run, e.g. xmonad's
``handle`` (287-292) credited with calls at lines 326-414. Here the anchor was
never wrong; the span was.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.haskell import analyze_haskell

_SOURCE = """\
module Main where

one :: Int -> Int
one x = x

two :: Int -> Int
two x = x

handle :: Int -> Int
handle 0 = one 0
handle 1 = two 1
handle n =
  let y = one n
  in two y
"""


def test_the_span_covers_every_equation(tmp_path: Path) -> None:
    (tmp_path / "Main.hs").write_text(_SOURCE)
    result = analyze_haskell(tmp_path)
    [handle] = [s for s in result.symbols if s.name == "handle"]
    assert (handle.span.start_line, handle.span.end_line) == (10, 14)
    assert handle.line_span == 5


def test_every_call_is_inside_its_src(tmp_path: Path) -> None:
    (tmp_path / "Main.hs").write_text(_SOURCE)
    result = analyze_haskell(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    anchors = [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges if e.edge_type == "calls" and e.line and e.src in by_id
    ]
    assert {a[3] for a in anchors} >= {10, 11, 13, 14}, anchors  # reach: calls in all equations
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
