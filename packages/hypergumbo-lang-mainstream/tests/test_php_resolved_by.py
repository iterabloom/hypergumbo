# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each PHP call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture line per route,
through the whole pipeline (ADR-0064: the analyzer binds only the first two
itself; the inherited-calls linker binds the rest through the file's table):

* ``$this->m()`` and ``self::m()`` are ``own_type`` (the analyzer);
* ``Helper::make()`` under the class the call site names is ``qualified``;
* ``new Helper()`` and a bare ``far_fn()`` are ``same_module``: both files
  are in the global namespace, which encloses the caller;
* ``$h->run()``, ``$h`` written ``new Helper()``, is ``receiver_type``.

Every one of the last four was ``name`` when the analyzer looked the name up
in a run-wide table.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/php.py
from __future__ import annotations

from pathlib import Path

import json

from hypergumbo_core.cli import run_behavior_map

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


def _edges(root: Path) -> list[dict]:
    repo = root / "repo"
    repo.mkdir()
    for rel, text in _FILES.items():
        (repo / rel).write_text(text)
    out = root / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())["edges"]


def _mark(
    edges: list[dict], line: int, dst_name: str, edge_type: str = "calls",
) -> str:
    hits = [
        e for e in edges
        if e["type"] == edge_type and e.get("is_resolved") and e["line"] == line
        and e["dst"].split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e["line"], e["dst"]) for e in edges]
    return hits[0]["meta"]["resolved_by"]


def test_every_route_of_one_file(tmp_path: Path) -> None:
    edges = _edges(tmp_path)
    assert _mark(edges, 6, "Main.helper") == "own_type"
    assert _mark(edges, 7, "Main.smake") == "own_type"
    assert _mark(edges, 8, "Helper.make") == "qualified"
    assert _mark(edges, 9, "Helper", "instantiates") == "same_module"
    assert _mark(edges, 10, "Helper.run") == "receiver_type"
    assert _mark(edges, 11, "far_fn") == "same_module"
