# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each JavaScript / TypeScript call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture per route,
asserting the value on the edge that route produces. Since ADR-0064 the
analyzer binds a call only on evidence in hand, and hands every other call to
the shared pass (``inherited_calls``) with facts; those routes are read from
the whole pipeline's map:

* a declaration the call site sees by position (the scope binder, an
  immediately-invoked function expression) is ``lexical_scope``;
* ``this.m()`` with ``m`` in the enclosing class is ``own_type``; a typed
  receiver (``this.prop`` with a typed constructor parameter, a variable)
  whose class this file declares is ``receiver_type``;
* the shared pass: a named import, a called default import and a namespace
  binding's function are ``import``; a typed receiver whose class another file
  declares is ``receiver_type``;
* ``new K()`` is ``lexical_scope`` for a class the call site sees, ``import``
  where the file's import table names the class's file, and the class
  resolver's own value otherwise (the shared pass reads no ``instantiates``);
* a name the file neither declares nor imports, an untyped receiver's method
  and a bare call to a method name are unresolved: no route looks a callee up
  by its name among the repository's declarations any more.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
from __future__ import annotations

from pathlib import Path

import json

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    return analyze_javascript(_write(root, files)).edges


def _pipeline_marks(root: Path, files: dict[str, str]) -> set[tuple[str, str, str, str]]:
    """(caller, callee, callee's path, mark) of each resolved ``calls`` edge of
    the whole pipeline's map."""
    out = root.parent / f"{root.name}.json"
    run_behavior_map(repo_root=_write(root, files), out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    nodes = {n["id"]: n for n in data["nodes"]}
    return {
        (nodes[e["src"]]["name"], nodes[e["dst"]]["name"], nodes[e["dst"]]["path"],
         e["meta"]["resolved_by"])
        for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved") and e["dst"] in nodes
        and not (e.get("meta") or {}).get("superseded_by")
    }


def _unresolved(edges: list[Edge], src_name: str) -> list[str]:
    return sorted(
        e.dst.split(":")[-2] for e in edges
        if e.edge_type == "calls" and not e.is_resolved and e.src.split(":")[-2] == src_name
    )


def _mark(
    edges: list[Edge], src_name: str, dst_name: str,
    evidence: str, edge_type: str = "calls",
) -> str:
    hits = [
        e for e in edges
        if e.edge_type == edge_type and e.is_resolved
        and e.src.split(":")[-2] == src_name and e.dst.split(":")[-2] == dst_name
        and e.evidence_type == evidence
    ]
    assert len(hits) == 1, [(e.src, e.dst, e.evidence_type) for e in edges]
    return hits[0].meta["resolved_by"]


class TestBareCallRoutes:
    def test_routes_of_one_file(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "src/a.js": (
                "function helper() { return 1; }\n"
                "function run() { helper(); far(); }\n"
                "(function () { return 2; })();\n"
                "class A { m() { return 1; } go() { m(); this.m(); } }\n"
            ),
            "src/b.js": "function far() { return 3; }\n",
        })
        assert _mark(edges, "run", "helper", "ast_call_direct") == "lexical_scope"
        assert _mark(edges, "file", "_iife@1", "ast_call_direct") == "lexical_scope"
        assert _mark(edges, "A.go", "A.m", "ast_method_this") == "own_type"
        # ``far`` is neither declared nor imported here; a bare ``m()`` is no
        # call of ``A.m`` (no implicit ``this``). Both were bound by name.
        assert _unresolved(edges, "run") == ["far"]
        assert _unresolved(edges, "A.go") == ["m"]

    def test_named_import_picks_among_namesakes(self, tmp_path: Path) -> None:
        files = {
            "src/a.js": "import { proc } from './x';\nfunction run() { proc(); }\n",
            "src/x.js": "export function proc() {}\n",
            "src/y.js": "export function proc() {}\n",
        }
        assert _unresolved(_edges(tmp_path / "a", files), "run") == ["proc"]
        assert _pipeline_marks(tmp_path / "p", files) >= {("run", "proc", "src/x.js", "import")}

    def test_same_package_preference_is_gone(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "p1/package.json": '{"name": "p1"}',
            "p2/package.json": '{"name": "p2"}',
            "p1/a.js": "function run() { helper(); }\n",
            "p1/h.js": "function helper() {}\n",
            "p2/h.js": "function helper() {}\n",
        })
        assert _unresolved(edges, "run") == ["helper"]

    def test_called_default_binding_of_an_in_tree_package(self, tmp_path: Path) -> None:
        marks = _pipeline_marks(tmp_path, {
            "package.json": '{"name": "ws", "private": true, "workspaces": ["packages/*"]}',
            "packages/c/package.json": (
                '{"name": "@ws/c", "version": "1.0.0", "main": "src/index.ts"}'
            ),
            "packages/c/src/index.ts": (
                "export function shout(s: string): string { return s; }\n"
                "export default function cDefault(): number { return 7; }\n"
            ),
            "packages/a/package.json": '{"name": "@ws/a", "version": "1.0.0"}',
            "packages/a/src/esm.ts": (
                "import c from '@ws/c';\n"
                "export function e5(): number { return c(); }\n"
            ),
        })
        assert ("e5", "cDefault", "packages/c/src/index.ts", "import") in marks


class TestMemberCallRoutes:
    def test_typed_receivers(self, tmp_path: Path) -> None:
        files = {
            "src/a.ts": (
                "import { Svc } from './svc';\n"
                "class Own { run() {} }\n"
                "class C {\n"
                "  constructor(private svc: Svc, private own: Own) {}\n"
                "  go() { this.svc.run(); this.own.run(); }\n"
                "  go2() { const s: Svc = new Svc(); s.run(); }\n"
                "  go3() { const o = new Own(); o.run(); }\n"
                "}\n"
            ),
            "src/svc.ts": "export class Svc { run() {} }\n",
        }
        edges = _edges(tmp_path / "a", files)
        # This file's own class: bound here.
        assert _mark(edges, "C.go", "Own.run", "ast_method_this_property") == "receiver_type"
        assert _mark(edges, "C.go3", "Own.run", "ast_method_type_inferred") == "receiver_type"
        assert _mark(edges, "C.go3", "Own", "ast_new", "instantiates") == "lexical_scope"
        # The imported class: the table names its file.
        assert _mark(edges, "C.go2", "Svc", "ast_new", "instantiates") == "import"
        marks = _pipeline_marks(tmp_path / "p", files)
        assert ("C.go", "Svc.run", "src/svc.ts", "receiver_type") in marks
        assert ("C.go2", "Svc.run", "src/svc.ts", "receiver_type") in marks

    def test_namespace_import(self, tmp_path: Path) -> None:
        files = {
            "src/a.js": (
                "import * as x from './x';\n"
                "import * as z from './z';\n"
                "function run() { x.proc(); z.only(); }\n"
                "function make() { return new x.K(); }\n"
            ),
            "src/x.js": "export function proc() {}\nexport class K {}\n",
            "src/y.js": "export function proc() {}\nexport class K {}\n",
            "src/z.js": "export function only() {}\n",
        }
        edges = _edges(tmp_path / "a", files)
        assert _mark(edges, "make", "K", "ast_new", "instantiates") == "import"
        marks = _pipeline_marks(tmp_path / "p", files)
        # The namespace binding names the file: x's ``proc``, z's ``only``
        # (a lone ``only`` was a name match before).
        assert ("run", "proc", "src/x.js", "import") in marks
        assert ("run", "only", "src/z.js", "import") in marks

    def test_untyped_receiver_is_not_looked_up_by_name(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "src/a.js": "function go(o) { o.frobnicate(); }\n",
            "src/b.js": "class B { frobnicate() {} }\n",
        })
        assert _unresolved(edges, "go") == ["frobnicate"]
