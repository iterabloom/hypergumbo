# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bash call routes stamp what scoped their choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`): a function
of the same file's table is ``same_file``; one the resolver found is the
lookup's own value. ``source`` does not scope the lookup, so a sourced
file's function is ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/bash.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.bash import analyze_bash


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).write_text(text)
    return root


def _mark(edges: list[Edge], line: int, dst_name: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_bash(tmp_path: Path) -> None:
    edges = analyze_bash(_write(tmp_path, {
        "lib.sh": "far_fn() {\n  echo hi\n}\n",
        "main.sh": (
            "#!/bin/bash\nsource ./lib.sh\nhelper() {\n  echo x\n}\n"
            "run() {\n  helper\n  far_fn\n}\n"
        ),
    })).edges
    assert _mark(edges, 7, "helper") == "same_file"
    assert _mark(edges, 8, "far_fn") == "name"
