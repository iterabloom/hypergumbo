# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each JavaScript / TypeScript call route stamps what scoped its choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) names the
evidence that chose a call-family edge's target. One fixture per route of the
analyzer, asserting the value on the edge that route produces:

* a declaration the call site sees by position (the scope binder, an
  immediately-invoked function expression) is ``lexical_scope``;
* a named import that picks among same-named functions, and a called default
  binding of an in-tree package, are ``import``;
* the same-npm-package preference and the repository-wide resolver are
  ``name`` (a nearness heuristic scopes nothing), except that a bare call to a
  method of the enclosing class is ``own_type``;
* ``this.m()`` is ``own_type``; ``this.prop.m()`` with a typed constructor
  parameter and a typed variable's method are ``receiver_type``;
* a namespace import ``ns.f()`` is ``import`` where the import path chose the
  file, and ``name`` where the resolver matched the bare name (the hint is
  applied only among candidates);
* ``new K()`` is ``import`` where the import path chose among same-named
  classes, and the class resolver's own value otherwise;
* the untyped ``obj.m()`` fallback is ``name``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return analyze_javascript(root).edges


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
        assert _mark(edges, "run", "far", "ast_call_direct") == "name"
        assert _mark(edges, "A.go", "A.m", "ast_call_direct") == "own_type"
        assert _mark(edges, "A.go", "A.m", "ast_method_this") == "own_type"

    def test_named_import_picks_among_namesakes(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "src/a.js": "import { proc } from './x';\nfunction run() { proc(); }\n",
            "src/x.js": "export function proc() {}\n",
            "src/y.js": "export function proc() {}\n",
        })
        assert _mark(edges, "run", "proc", "ast_call_direct") == "import"

    def test_same_package_preference_is_a_name_match(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "p1/package.json": '{"name": "p1"}',
            "p2/package.json": '{"name": "p2"}',
            "p1/a.js": "function run() { helper(); }\n",
            "p1/h.js": "function helper() {}\n",
            "p2/h.js": "function helper() {}\n",
        })
        assert _mark(edges, "run", "helper", "ast_call_direct") == "name"

    def test_called_default_binding_of_an_in_tree_package(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
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
        assert _mark(edges, "e5", "cDefault", "ast_call_direct") == "import"


class TestMemberCallRoutes:
    def test_typed_receivers(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "src/a.ts": (
                "import { Svc } from './svc';\n"
                "class C {\n"
                "  constructor(private svc: Svc) {}\n"
                "  go() { this.svc.run(); }\n"
                "  go2() { const s: Svc = new Svc(); s.run(); }\n"
                "}\n"
            ),
            "src/svc.ts": "export class Svc { run() {} }\n",
        })
        assert _mark(edges, "C.go", "Svc.run", "ast_method_this_property") == "receiver_type"
        assert _mark(edges, "C.go2", "Svc.run", "ast_method_type_inferred") == "receiver_type"
        # One ``Svc`` in the tree: the class resolver found it by name.
        assert _mark(edges, "C.go2", "Svc", "ast_new", "instantiates") == "name"

    def test_namespace_import(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "src/a.js": (
                "import * as x from './x';\n"
                "import * as z from './z';\n"
                "function run() { x.proc(); z.only(); }\n"
                "function make() { return new x.K(); }\n"
            ),
            "src/x.js": "export function proc() {}\nexport class K {}\n",
            "src/y.js": "export function proc() {}\nexport class K {}\n",
            "src/z.js": "export function only() {}\n",
        })
        # The import path chose x's ``proc`` and ``K`` over y's.
        assert _mark(edges, "run", "proc", "ast_call_namespace") == "import"
        assert _mark(edges, "make", "K", "ast_new", "instantiates") == "import"
        # A lone ``only``: the resolver matched the name, the hint unused.
        assert _mark(edges, "run", "only", "ast_call_namespace") == "name"

    def test_untyped_receiver_fallback(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {
            "src/a.js": "function go(o) { o.frobnicate(); }\n",
            "src/b.js": "class B { frobnicate() {} }\n",
        })
        assert _mark(edges, "go", "B.frobnicate", "ast_method_inferred") == "name"
