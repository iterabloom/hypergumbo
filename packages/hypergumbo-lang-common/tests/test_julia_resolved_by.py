# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Julia's call edges (audit 0030, WI-hirol).

A name found in the caller's file's table is ``same_file``; anything else
goes to the run-wide resolver and takes the lookup's value (``name``).
"""
from pathlib import Path

from hypergumbo_lang_common.julia import analyze_julia


def _edge(edges, src: str, dst: str):
    found = [
        e for e in edges
        if e.edge_type == "calls" and f":{src}:" in e.src and f":{dst}:" in e.dst
    ]
    assert len(found) == 1, f"calls {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


def test_julia_routes(tmp_path: Path) -> None:
    (tmp_path / "util.jl").write_text("function helper(x)\n    x\nend\n")
    (tmp_path / "main.jl").write_text(
        "function run(x)\n    helper(x)\nend\nfunction go(y)\n    run(y)\nend\n"
    )
    edges = analyze_julia(tmp_path).edges
    assert _edge(edges, "go", "run").meta["resolved_by"] == "same_file"
    assert _edge(edges, "run", "helper").meta["resolved_by"] == "name"
