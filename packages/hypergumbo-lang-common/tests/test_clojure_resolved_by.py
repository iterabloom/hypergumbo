# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Clojure's call edges (audit 0030, WI-hirol).

One fixture per route of the one creation site: an unqualified name found in
the caller's own file's table is ``same_file``; anything the run-wide
resolver finds takes the lookup's own value (``name`` here: an exact key).
"""
from pathlib import Path

from hypergumbo_lang_common.clojure import analyze_clojure


def _edge(result, src: str, dst: str, edge_type: str = "calls"):
    found = [
        e for e in result.edges
        if e.edge_type == edge_type and f":{src}:" in e.src and f":{dst}:" in e.dst
    ]
    assert len(found) == 1, f"{edge_type} {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


def test_same_file_and_resolver_routes(tmp_path: Path) -> None:
    (tmp_path / "util.clj").write_text(
        "(ns util)\n(defn helper [x] x)\n"
        "(defn local-a [] (local-b))\n(defn local-b [] 1)\n"
    )
    (tmp_path / "main.clj").write_text("(ns main)\n(defn run [] (helper 1))\n")
    result = analyze_clojure(tmp_path)
    assert _edge(result, "local-a", "local-b").meta["resolved_by"] == "same_file"
    assert _edge(result, "run", "helper").meta["resolved_by"] == "name"
