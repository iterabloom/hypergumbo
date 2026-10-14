# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lahub: Dart inheritance edges — ``extends``, ``implements``, ``with``, ``on``.

Before this item the Dart analyzer emitted every type and member correctly and
NO inheritance edge at all, so ``type_hierarchy`` had nothing to build its maps
from and Dart got no ``dispatches_to`` edge (the parity column's ``dart`` cell).

The analyzer, not the shared ``inheritance`` linker, labels these edges: Dart
has no ``interface`` kind (every class declares an implicit interface), so a
label keyed on the TARGET's kind — the linker's rule — would turn
``class Square implements Shape`` into ``extends`` whenever ``Shape`` is a class,
which in Dart it always is. Only the clause the base is written in tells the
two apart. These tests pin the clause-to-label mapping, the resolution cascade
(same file, unique, flagged fallback), the external-base edge, and the
declarations that must move with a new producer.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_common.dart import analyze_dart


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _inheritance(edges: list[Edge]) -> list[Edge]:
    return [e for e in edges if e.edge_type in ("extends", "implements", "includes")]


def _named(result, name: str):
    matches = [s for s in result.symbols if s.name == name]
    assert len(matches) == 1, f"{name}: {[(s.kind, s.path) for s in matches]}"
    return matches[0]


def _edge(result, src: str, dst_id: str) -> Edge:
    src_id = _named(result, src).id
    found = [e for e in _inheritance(result.edges) if e.src == src_id and e.dst == dst_id]
    assert len(found) == 1, (
        f"{src} -> {dst_id}: {[(e.edge_type, e.dst) for e in _inheritance(result.edges)]}"
    )
    return found[0]


class TestClauseLabels:
    """The clause a base is written in decides the edge label."""

    def test_implements_an_abstract_class_is_implements(self, tmp_path: Path) -> None:
        """The parity column's own fixture: the label is ``implements`` even
        though ``Shape`` is (necessarily) a class."""
        _write(tmp_path, "s.dart",
               "abstract class Shape { int area(); }\n"
               "class Square implements Shape { int area() => 1; }\n")
        result = analyze_dart(tmp_path)
        edge = _edge(result, "Square", _named(result, "Shape").id)
        assert edge.edge_type == "implements"
        assert edge.evidence_type == "ast_implements"
        assert edge.is_resolved is True
        assert edge.confidence == 0.95
        assert edge.line == 2
        assert not (edge.meta or {}).get("disambiguation_fallback")

    def test_extends_with_and_implements_on_one_class(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.dart",
               "class Base {}\n"
               "mixin M1 {}\n"
               "mixin M2 {}\n"
               "abstract class Shape {}\n"
               "class Square extends Base with M1, M2 implements Shape {}\n")
        result = analyze_dart(tmp_path)
        got = {
            (e.edge_type, e.evidence_type, e.dst)
            for e in _inheritance(result.edges)
            if e.src == _named(result, "Square").id
        }
        assert got == {
            ("extends", "ast_extends", _named(result, "Base").id),
            ("includes", "ast_includes", _named(result, "M1").id),
            ("includes", "ast_includes", _named(result, "M2").id),
            ("implements", "ast_implements", _named(result, "Shape").id),
        }

    def test_mixin_on_clause_extends_and_implements_clause_implements(
        self, tmp_path: Path,
    ) -> None:
        """``mixin M on Base`` makes ``Base`` the mixin's superclass constraint
        (``super`` inside ``M`` is ``Base``), so it is ``extends``; the mixin's
        own ``implements`` clause stays ``implements``."""
        _write(tmp_path, "m.dart",
               "class Base {}\n"
               "class Other {}\n"
               "abstract class I {}\n"
               "mixin M on Base, Other implements I {}\n")
        result = analyze_dart(tmp_path)
        got = {
            (e.edge_type, e.dst)
            for e in _inheritance(result.edges)
            if e.src == _named(result, "M").id
        }
        assert got == {
            ("extends", _named(result, "Base").id),
            ("extends", _named(result, "Other").id),
            ("implements", _named(result, "I").id),
        }

    def test_enum_with_and_implements(self, tmp_path: Path) -> None:
        _write(tmp_path, "e.dart",
               "mixin Named {}\n"
               "abstract class Shape { int area(); }\n"
               "enum Kind with Named implements Shape { a; int area() => 1; }\n")
        result = analyze_dart(tmp_path)
        got = {
            (e.edge_type, e.dst)
            for e in _inheritance(result.edges)
            if e.src == _named(result, "Kind").id
        }
        assert got == {
            ("includes", _named(result, "Named").id),
            ("implements", _named(result, "Shape").id),
        }

    def test_generic_and_prefixed_bases_resolve_on_the_type_name(
        self, tmp_path: Path,
    ) -> None:
        """``Base<int>`` and ``p.Mx<T>`` resolve on ``Base`` / ``Mx``; the type
        arguments and a type-parameter bound name no base at all."""
        _write(tmp_path, "g.dart",
               "import 'mx.dart' as p;\n"
               "class Base<T> {}\n"
               "class Num {}\n"
               "class G<T extends Num> extends Base<T> with p.Mx<T> {}\n")
        _write(tmp_path, "mx.dart", "mixin Mx<T> {}\n")
        result = analyze_dart(tmp_path)
        got = {
            (e.edge_type, e.dst)
            for e in _inheritance(result.edges)
            if e.src == _named(result, "G").id
        }
        assert got == {
            ("extends", _named(result, "Base").id),
            ("includes", _named(result, "Mx").id),
        }

    def test_extension_on_is_not_inheritance(self, tmp_path: Path) -> None:
        """``extension X on T`` adds statically-dispatched members to ``T``;
        nothing inherits from anything."""
        _write(tmp_path, "x.dart",
               "class T {}\n"
               "extension X on T { int twice() => 2; }\n")
        result = analyze_dart(tmp_path)
        assert _inheritance(result.edges) == []

    def test_mixin_application_class_has_no_source_symbol(
        self, tmp_path: Path,
    ) -> None:
        """``class A = Base with M;`` gets no symbol from Pass 1 (the name sits
        inside ``mixin_application_class``), so its clauses have no source and
        emit nothing — recorded here so the gap stays visible, not inferred."""
        _write(tmp_path, "a.dart",
               "class Base {}\nmixin M {}\nclass A = Base with M;\n")
        result = analyze_dart(tmp_path)
        assert [s.name for s in result.symbols if s.name == "A"] == []
        assert _inheritance(result.edges) == []


class TestResolution:
    def test_cross_file_base(self, tmp_path: Path) -> None:
        _write(tmp_path, "lib/shape.dart", "abstract class Shape { int area(); }\n")
        _write(tmp_path, "lib/square.dart",
               "import 'shape.dart';\n"
               "class Square implements Shape { int area() => 1; }\n")
        result = analyze_dart(tmp_path)
        edge = _edge(result, "Square", _named(result, "Shape").id)
        assert edge.edge_type == "implements"
        assert edge.confidence == 0.95

    def test_same_file_candidate_wins_a_name_collision(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.dart", "class Base {}\nclass Child extends Base {}\n")
        _write(tmp_path, "b.dart", "class Base {}\n")
        result = analyze_dart(tmp_path)
        bases = {s.path: s for s in result.symbols if s.name == "Base"}
        edge = _edge(result, "Child", bases["a.dart"].id)
        assert edge.confidence == 0.95
        assert not (edge.meta or {}).get("disambiguation_fallback")

    def test_ambiguous_cross_file_base_is_a_flagged_fallback(
        self, tmp_path: Path,
    ) -> None:
        """INV-zuhub: two out-of-file candidates and no same-file one — the
        deterministic pick carries confidence <= 0.5 and the provenance flag."""
        _write(tmp_path, "a.dart", "class Child extends Base {}\n")
        _write(tmp_path, "b.dart", "class Base {}\n")
        _write(tmp_path, "c.dart", "class Base {}\n")
        result = analyze_dart(tmp_path)
        child = _named(result, "Child")
        edges = [e for e in _inheritance(result.edges) if e.src == child.id]
        assert len(edges) == 1
        first = min((s for s in result.symbols if s.name == "Base"), key=lambda s: s.id)
        assert edges[0].dst == first.id
        assert edges[0].confidence == 0.5
        assert edges[0].meta == {"disambiguation_fallback": True}

    def test_a_function_is_not_a_supertype(self, tmp_path: Path) -> None:
        """Candidates are classes and mixins only. A same-named function is
        not a target, and — being an in-tree name — not a false external
        either."""
        _write(tmp_path, "f.dart", "void Thing() {}\nclass X implements Thing {}\n")
        result = analyze_dart(tmp_path)
        assert _inheritance(result.edges) == []

    def test_no_self_edge(self, tmp_path: Path) -> None:
        _write(tmp_path, "s.dart", "class A extends A {}\n")
        result = analyze_dart(tmp_path)
        assert _inheritance(result.edges) == []


class TestExternalBases:
    """A base with no in-tree definition keeps its relationship, as an
    unresolved edge to the ``external`` sentinel module (the shape the shared
    linker mints for every other OO language, WI-jubag Approach C)."""

    def test_flutter_widget_bases(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.dart",
               "import 'package:flutter/material.dart' as m;\n"
               "class Counter extends m.StatefulWidget {}\n"
               "class _CounterState extends State<Counter>\n"
               "    with SingleTickerProviderStateMixin implements Comparable<int> {}\n")
        result = analyze_dart(tmp_path)
        assert _edge(
            result, "Counter", "dart:external:0-0:StatefulWidget:unresolved",
        ).edge_type == "extends"
        state = _named(result, "_CounterState").id
        got = {
            (e.edge_type, e.dst, e.is_resolved, e.confidence, e.line)
            for e in _inheritance(result.edges)
            if e.src == state
        }
        assert got == {
            ("extends", "dart:external:0-0:State:unresolved", False, 0.95, 3),
            ("includes",
             "dart:external:0-0:SingleTickerProviderStateMixin:unresolved",
             False, 0.95, 4),
            ("implements", "dart:external:0-0:Comparable:unresolved", False, 0.95, 4),
        }


class TestRunState:
    def test_a_second_run_does_not_replay_the_first(self, tmp_path: Path) -> None:
        """The clause records gathered in Pass 1 live for one ``analyze()``."""
        _write(tmp_path, "s.dart",
               "abstract class Shape {}\nclass Square implements Shape {}\n")
        first = _inheritance(analyze_dart(tmp_path).edges)
        second = _inheritance(analyze_dart(tmp_path).edges)
        assert len(first) == len(second) == 1

    def test_pass_one_outside_analyze_records_nothing(self, tmp_path: Path) -> None:
        """A caller driving Pass 1 directly (no ``analyze()`` around it) gets
        its symbols and leaves no clause records behind."""
        from hypergumbo_core.ir import AnalysisRun
        from hypergumbo_lang_common.dart import DartAnalyzer

        analyzer = DartAnalyzer()
        parser = analyzer._create_parser()
        source = b"class Base {}\nclass Child extends Base {}\n"
        tree = parser.parse(source)
        run = AnalysisRun.create(pass_id="dart", version="test")
        analysis = analyzer.extract_symbols_from_file(
            tree, source, tmp_path / "c.dart", "c.dart", run,
        )
        assert {s.name for s in analysis.symbols} == {"Base", "Child"}
        assert analyzer._heritage_refs is None


class TestPipeline:
    def test_dispatches_to_from_abstract_method_to_implementation(
        self, tmp_path: Path,
    ) -> None:
        """Production path: the edge reaches ``type_hierarchy`` and the
        abstract method dispatches to the implementation."""
        from hypergumbo_core.cli import run_behavior_map

        _write(tmp_path, "s.dart",
               "abstract class Shape { int area(); }\n"
               "class Square implements Shape { int area() => 1; }\n")
        out = tmp_path / "bm.json"
        run_behavior_map(
            repo_root=tmp_path, out_path=out,
            include_sketch_precomputed=False, progress=False,
        )
        bm = json.loads(out.read_text())
        nodes = {n["id"]: n for n in bm["nodes"]}
        dispatch = {
            (nodes[e["src"]]["name"], nodes[e["dst"]]["name"])
            for e in bm["edges"]
            if e["type"] == "dispatches_to" and e["src"] in nodes and e["dst"] in nodes
        }
        assert ("Shape.area", "Square.area") in dispatch
        inheritance = [
            (nodes[e["src"]]["name"], e["type"], nodes[e["dst"]]["name"])
            for e in bm["edges"]
            if e["type"] in ("extends", "implements")
        ]
        assert inheritance == [("Square", "implements", "Shape")]

    def test_type_hierarchy_declares_dart_as_an_edge_source(self) -> None:
        """``type-hierarchy-linker.depends_on`` names the analyzers that emit
        ``extends``/``implements`` edges themselves; Dart now does."""
        import hypergumbo_core.cli  # linkers register on import
        from hypergumbo_core.catalog import get_default_catalog
        from hypergumbo_core.edge_types import INHERITANCE_EDGE_TYPE_LANGUAGES

        passes = {p.id: p for p in get_default_catalog().passes}
        clauses = passes["type-hierarchy-linker"].depends_on
        assert any("dart" in clause for clause in clauses)
        assert "dart" in INHERITANCE_EDGE_TYPE_LANGUAGES["extends"]
        assert "dart" in INHERITANCE_EDGE_TYPE_LANGUAGES["implements"]
