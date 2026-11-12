# SPDX-License-Identifier: AGPL-3.0-or-later
"""Haskell: a function's span covers ALL of its equations.

A haskell function is written as consecutive equations, one per pattern, and
the analyzer emits one symbol per NAME, from the first equation. Its span must
run to the LAST equation: a span that stops at the first leaves every call in
a later equation anchored to the right function by name but outside that
function's span. The anchor is right either way; these tests pin the span.
(history: INV-midag)
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
