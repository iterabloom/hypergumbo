# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each PHP call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route:

* ``$this->m()`` and ``self::m()`` are ``own_type``;
* ``Helper::make()`` under the class the call site names is ``qualified``;
* ``new Helper()``, the untyped ``$h->run()`` fallback and a bare
  ``far_fn()`` take the resolver's own value: found by name, ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.php import analyze_php

_FILES = {
    "Helper.php": (
        "<?php\nclass Helper {\n    public static function make() {}\n"
        "    public function run() {}\n}\nfunction far_fn() {}\n"
    ),
    "Main.php": """<?php
class Main {
    public function helper() {}
    public static function smake() {}
    public function go() {
        $this->helper();
        self::smake();
        Helper::make();
        $h = new Helper();
        $h->run();
        far_fn();
    }
}
""",
}


def _edges(root: Path) -> list[Edge]:
    for rel, text in _FILES.items():
        (root / rel).write_text(text)
    return analyze_php(root).edges


def _mark(
    edges: list[Edge], line: int, dst_name: str, edge_type: str = "calls",
) -> str:
    hits = [
        e for e in edges
        if e.edge_type == edge_type and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 6, "Main.helper") == "own_type"
    assert _mark(edges, 7, "Main.smake") == "own_type"
    assert _mark(edges, 8, "Helper.make") == "qualified"
    assert _mark(edges, 9, "Helper", "instantiates") == "name"
    assert _mark(edges, 10, "Helper.run") == "name"
    assert _mark(edges, 11, "far_fn") == "name"
