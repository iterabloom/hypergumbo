# SPDX-License-Identifier: AGPL-3.0-or-later
"""TypeScript ``interface X extends Y`` emits inheritance edges (WI-kalug).

Before this fix the ``interface_declaration`` branch of the JS/TS analyzer
built the interface Symbol with no ``base_classes`` meta, and the inheritance
edge extractor skipped every non-``class`` symbol. ``interface Child extends
Base`` therefore produced ZERO inheritance edges, intra-repo or external,
in the analyzer AND in the full pipeline (the core inheritance linker reads
the same missing meta).

An interface's bases are ``extends`` bases — TypeScript has no
``implements`` for interfaces — so every edge from an interface source is
``extends``, whether the target is another interface, a class (TS lets an
interface extend a class) or an unresolved external.

Two layers are pinned:

* the analyzer (``analyze_javascript``): meta, resolved edges, and the
  module-hinted unresolved-external fallback (``typescript:rxjs:0-0:...``);
* the full pipeline (``run_behavior_map``): exactly ONE edge per relation.
  The core linker would otherwise re-derive the same relation as
  ``implements`` (it labelled any interface target ``implements``) and emit
  it beside the analyzer's ``extends``.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _inheritance(edges: list[Edge]) -> list[Edge]:
    return [e for e in edges if e.edge_type in ("extends", "implements")]


def _by_name(result, name: str, kind: str):
    matches = [s for s in result.symbols if s.name == name and s.kind == kind]
    assert len(matches) == 1, [(s.name, s.kind) for s in result.symbols]
    return matches[0]


class TestInterfaceBaseClassesMeta:
    def test_extends_clause_populates_base_classes(self, tmp_path: Path) -> None:
        (tmp_path / "a.ts").write_text(
            "import * as ns from 'lib';\n"
            "interface Base {}\n"
            "interface Child<T> extends Base, Observer<number>, ns.Qual,"
            " ns.Gen<string> {}\n"
        )
        result = analyze_javascript(tmp_path)
        child = _by_name(result, "Child", "interface")
        assert child.meta is not None
        assert child.meta["base_classes"] == [
            "Base", "Observer<number>", "ns.Qual", "ns.Gen<string>",
        ]

    def test_interface_without_extends_has_no_base_classes(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "a.ts").write_text("interface Base { x: number }\n")
        result = analyze_javascript(tmp_path)
        base = _by_name(result, "Base", "interface")
        assert not (base.meta or {}).get("base_classes")


class TestInterfaceInheritanceEdges:
    def test_intra_repo_interface_base_is_resolved_extends(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "a.ts").write_text(
            "export interface Base { x: number }\n"
            "export interface Child extends Base { y: number }\n"
        )
        result = analyze_javascript(tmp_path)
        base = _by_name(result, "Base", "interface")
        child = _by_name(result, "Child", "interface")
        edges = _inheritance(result.edges)
        assert [(e.src, e.dst, e.edge_type) for e in edges] == [
            (child.id, base.id, "extends"),
        ]
        assert edges[0].is_resolved
        assert edges[0].evidence_type == "ast_extends"

    def test_cross_file_imported_interface_base(self, tmp_path: Path) -> None:
        (tmp_path / "base.ts").write_text("export interface Base {}\n")
        (tmp_path / "child.ts").write_text(
            "import { Base } from './base';\n"
            "export interface Child extends Base {}\n"
        )
        result = analyze_javascript(tmp_path)
        base = _by_name(result, "Base", "interface")
        edges = _inheritance(result.edges)
        assert len(edges) == 1, [(e.dst, e.edge_type) for e in edges]
        assert edges[0].dst == base.id
        assert edges[0].edge_type == "extends"

    def test_interface_extending_a_class_is_extends(self, tmp_path: Path) -> None:
        (tmp_path / "a.ts").write_text(
            "export class Widget { w = 1 }\n"
            "export interface WidgetLike extends Widget {}\n"
        )
        result = analyze_javascript(tmp_path)
        widget = _by_name(result, "Widget", "class")
        edges = _inheritance(result.edges)
        assert [(e.dst, e.edge_type) for e in edges] == [(widget.id, "extends")]

    def test_multiple_bases_each_get_an_edge(self, tmp_path: Path) -> None:
        (tmp_path / "a.ts").write_text(
            "export interface A {}\n"
            "export interface B {}\n"
            "export interface C extends A, B {}\n"
        )
        result = analyze_javascript(tmp_path)
        a = _by_name(result, "A", "interface")
        b = _by_name(result, "B", "interface")
        edges = _inheritance(result.edges)
        assert sorted((e.dst, e.edge_type) for e in edges) == sorted(
            [(a.id, "extends"), (b.id, "extends")]
        )

    def test_external_interface_base_carries_import_module_hint(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "a.ts").write_text(
            'import { Observer } from "rxjs";\n'
            "export interface MyObs extends Observer<number> {}\n"
        )
        result = analyze_javascript(tmp_path)
        edges = _inheritance(result.edges)
        assert [(e.dst, e.edge_type) for e in edges] == [
            ("typescript:rxjs:0-0:Observer:unresolved", "extends"),
        ]
        edge = edges[0]
        assert not edge.is_resolved
        assert edge.dst_ref is not None
        assert edge.dst_ref.module_path == "rxjs"
        assert edge.dst_ref.name == "Observer"

    def test_namespace_qualified_interface_base(self, tmp_path: Path) -> None:
        (tmp_path / "a.ts").write_text(
            "import * as ng from '@angular/core';\n"
            "export interface Hooks extends ng.OnInit {}\n"
        )
        result = analyze_javascript(tmp_path)
        edges = _inheritance(result.edges)
        assert [(e.dst, e.edge_type) for e in edges] == [
            ("typescript:@angular/core:0-0:OnInit:unresolved", "extends"),
        ]

    def test_external_base_is_extends_even_if_a_same_named_class_implements_it(
        self, tmp_path: Path,
    ) -> None:
        """Declaration merging: ``class Foo implements Bar`` and ``interface
        Foo extends Bar`` share a (path, name). The class's ``implements``
        clause must not relabel the interface's external base."""
        (tmp_path / "a.ts").write_text(
            "import { Bar } from 'lib';\n"
            "export interface Foo extends Bar {}\n"
            "export class Foo implements Bar {}\n"
        )
        result = analyze_javascript(tmp_path)
        iface = _by_name(result, "Foo", "interface")
        cls = _by_name(result, "Foo", "class")
        typed = {
            (e.src, e.edge_type)
            for e in _inheritance(result.edges) if not e.is_resolved
        }
        assert typed == {(iface.id, "extends"), (cls.id, "implements")}

    def test_aliased_local_interface_base_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "base.ts").write_text("export interface Base {}\n")
        (tmp_path / "child.ts").write_text(
            "import { Base as B } from './base';\n"
            "export interface Child extends B {}\n"
        )
        result = analyze_javascript(tmp_path)
        base = _by_name(result, "Base", "interface")
        edges = _inheritance(result.edges)
        assert [(e.dst, e.edge_type, e.is_resolved) for e in edges] == [
            (base.id, "extends", True),
        ]

    def test_aliased_local_class_base_of_interface_resolves(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "base.ts").write_text("export class Base {}\n")
        (tmp_path / "child.ts").write_text(
            "import { Base as B } from './base';\n"
            "export interface Child extends B {}\n"
        )
        result = analyze_javascript(tmp_path)
        base = _by_name(result, "Base", "class")
        edges = _inheritance(result.edges)
        assert [(e.dst, e.edge_type, e.is_resolved) for e in edges] == [
            (base.id, "extends", True),
        ]


class TestQualifiedImplementsClause:
    """``implements ns.I`` was dropped: the implements clause read only bare
    and generic names, never ``nested_type_identifier``. Same heritage-clause
    reader as the interface ``extends`` clause, so it is fixed with it."""

    def test_namespace_qualified_implements_is_kept(self, tmp_path: Path) -> None:
        (tmp_path / "a.ts").write_text(
            "import * as ng from '@angular/core';\n"
            "export class Comp implements ng.OnInit { ngOnInit() {} }\n"
        )
        result = analyze_javascript(tmp_path)
        comp = _by_name(result, "Comp", "class")
        assert comp.meta is not None
        assert comp.meta["base_classes"] == ["ng.OnInit"]
        edges = _inheritance(result.edges)
        assert [(e.dst, e.edge_type) for e in edges] == [
            ("typescript:@angular/core:0-0:OnInit:unresolved", "implements"),
        ]


class TestInterfaceInheritancePipeline:
    def test_one_extends_edge_per_relation_end_to_end(self, tmp_path: Path) -> None:
        """Analyzer + core inheritance linker together: ``Child extends Base``
        is ONE ``extends`` edge (no linker-added ``implements`` twin), and the
        external rxjs base keeps the analyzer's module hint (no
        ``typescript:external:`` twin)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.ts").write_text(
            'import { Observer } from "rxjs";\n'
            "export interface Base { x: number }\n"
            "export interface Child extends Base { y: number }\n"
            "export interface MyObs extends Observer<number> {}\n"
            "export class K implements Child { x = 1; y = 2 }\n"
        )
        out = tmp_path / "bm.json"
        run_behavior_map(
            repo_root=repo, out_path=out,
            include_sketch_precomputed=False, progress=False,
        )
        bm = json.loads(out.read_text())
        names = {n["id"]: n["name"] for n in bm["nodes"]}
        # An unresolved target is compared by its full id: the id carries the
        # module hint (``rxjs`` vs the core linker's ``external`` sentinel).
        got = sorted(
            (
                names.get(e["src"]),
                e["type"],
                e["dst"] if e["dst"].endswith(":unresolved") else names[e["dst"]],
            )
            for e in bm["edges"] if e["type"] in ("extends", "implements")
        )
        assert got == [
            ("Child", "extends", "Base"),
            ("K", "implements", "Child"),
            ("MyObs", "extends", "typescript:rxjs:0-0:Observer:unresolved"),
        ]
