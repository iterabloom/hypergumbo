# SPDX-License-Identifier: AGPL-3.0-or-later
"""``anchor_execution``: when code anchored on a file or class runs (WI-fakuh).

The production-path half (dead-code, test-coverage and sketch on a surveyed
fixture) is hypergumbo-lang-mainstream's ``test_anchored_code_reachability``.
"""
from __future__ import annotations

from types import SimpleNamespace

from hypergumbo_core.anchor_execution import anchor_execution_edges
from hypergumbo_core.cli import _bfs_reachable


_NODES = [
    {"id": "F", "kind": "file", "path": "lib.py"},
    {"id": "G", "kind": "file", "path": "other.py"},
    {"id": "C", "kind": "class", "path": "lib.py"},
    {"id": "run", "kind": "function", "path": "lib.py"},
    {"id": "meth", "kind": "method", "path": "lib.py"},
    {"id": "helper", "kind": "function", "path": "lib.py"},
    {"id": "v", "kind": "variable", "path": "lib.py"},
]
_EDGES = [
    {"type": "contains", "src": "F", "dst": "C"},
    {"type": "contains", "src": "F", "dst": "run"},
    {"type": "contains", "src": "F", "dst": "helper"},
    {"type": "contains", "src": "C", "dst": "meth"},
    {"type": "contains", "src": "F", "dst": "v"},
    {"type": "imports", "src": "G", "dst": "F"},
    {"type": "imports", "src": "G", "dst": "external:mod"},
    {"type": "calls", "src": "F", "dst": "x"},
    {"type": "contains", "src": "", "dst": "C"},
]


def test_members_reach_their_container_and_files_their_types() -> None:
    pairs = set(anchor_execution_edges(_NODES, _EDGES))
    assert {("C", "F"), ("run", "F"), ("helper", "F"), ("meth", "C"), ("v", "F")} <= pairs
    assert ("F", "C") in pairs  # loading a file runs its class bodies
    assert ("G", "F") in pairs  # importing a file loads it
    assert ("G", "external:mod") not in pairs  # only file-to-file imports


def test_a_loaded_file_never_makes_its_functions_reachable() -> None:
    """INV-rolok's precision rule: containing is not calling."""
    graph: dict[str, list[str]] = {}
    for src, dst in anchor_execution_edges(_NODES, _EDGES):
        graph.setdefault(src, []).append(dst)
    reached = _bfs_reachable({"G"}, graph)
    assert {"F", "C"} <= reached
    assert not {"run", "meth", "helper"} & reached


def test_live_objects_are_read_like_dicts() -> None:
    nodes = [SimpleNamespace(id="F", kind="file", path="lib.py"),
             SimpleNamespace(id="C", kind="class", path="lib.py")]
    edges = [SimpleNamespace(edge_type="contains", src="F", dst="C")]
    assert set(anchor_execution_edges(nodes, edges)) == {("C", "F"), ("F", "C")}
