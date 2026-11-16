# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Java class's inheritance facts belong to THAT class, found by position (WI-gikuk).

The analyzer took an ``extends`` / ``implements`` edge's SOURCE, the
inherited-field walk's chain and each class's ``meta["fields"]`` from tables
keyed by the class's dotted name WITHOUT its package (``Foo``, ``Outer.Inner``).
Two classes of one name in two packages, or two local classes of one name in
two methods, shared one entry: whichever registered last answered for both. So
``package b; class Foo extends Base`` came out as an edge from ``a.Foo``, the
base resolver then read ``a``'s imports and picked ``x.Base``, and a field
inherited from ``y.Base`` was typed from ``x.Base``.

THE RULE (WI-kosar / WI-jusah, the LIVE rule on ``symbols_at``): an enclosing
or source symbol is found by its declaration's POSITION. These tests assert the
property on every edge and symbol, not a golden output: each inheritance edge
starts at a class of the file whose import it was resolved through, each field
table holds only that class's own fields.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.java import analyze_java


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _two_packages(root: Path) -> None:
    """``a.Foo extends x.Base`` and ``b.Foo extends y.Base``; same simple names."""
    _write(root, "x/Base.java",
           "package x;\npublic class Base {\n    protected java.sql.Connection conn;\n}\n")
    _write(root, "y/Base.java",
           "package y;\npublic class Base {\n    protected java.io.File conn;\n}\n")
    _write(root, "x/Api.java", "package x;\npublic interface Api {}\n")
    _write(root, "y/Api.java", "package y;\npublic interface Api {}\n")
    _write(root, "a/Foo.java",
           "package a;\nimport x.Base;\nimport x.Api;\n"
           "public class Foo extends Base implements Api {\n"
           "    java.util.List la;\n"
           "    void m() throws Exception { conn.commit(); }\n}\n")
    # Annotated, and shifted down a few lines: the class symbol's span starts
    # at the annotation, which is where the declaration node starts too.
    _write(root, "b/Foo.java",
           "package b;\nimport y.Base;\nimport y.Api;\n\n\n\n\n\n"
           "@Deprecated\npublic class Foo extends Base implements Api {\n"
           "    java.util.Map lb;\n"
           "    void m() throws Exception { conn.commit(); }\n}\n")


def _dir(path: str) -> str:
    return Path(path).parent.name


def _inheritance_pairs(result, edge_type: str) -> set[tuple[str, str]]:
    by_id = {s.id: s for s in result.symbols}
    pairs = set()
    for e in result.edges:
        if e.edge_type != edge_type:
            continue
        src, dst = by_id[e.src], by_id[e.dst]
        pairs.add((f"{_dir(src.path)}.{src.name}", f"{_dir(dst.path)}.{dst.name}"))
    return pairs


def test_each_extends_and_implements_edge_starts_at_its_own_class(tmp_path: Path) -> None:
    _two_packages(tmp_path)
    result = analyze_java(tmp_path)
    assert _inheritance_pairs(result, "extends") == {("a.Foo", "x.Base"), ("b.Foo", "y.Base")}
    assert _inheritance_pairs(result, "implements") == {("a.Foo", "x.Api"), ("b.Foo", "y.Api")}


def test_an_edge_line_lies_inside_its_source_class(tmp_path: Path) -> None:
    """The edge's line is in its source's span: a.Foo has no line 7."""
    _two_packages(tmp_path)
    result = analyze_java(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    edges = [e for e in result.edges if e.edge_type in ("extends", "implements")]
    assert len(edges) == 4
    for e in edges:
        span = by_id[e.src].span
        assert span.start_line <= e.line <= span.end_line, (e.src, e.line)


def test_an_inherited_field_is_typed_from_the_parent_this_class_extends(
    tmp_path: Path,
) -> None:
    _two_packages(tmp_path)
    result = analyze_java(tmp_path)
    commits = {
        _dir(e.src.split(":")[1]): e.dst
        for e in result.edges
        if e.edge_type == "calls" and e.dst.endswith(":commit:unresolved")
    }
    assert commits == {
        "a": "java:java.sql.Connection:0-0:commit:unresolved",
        "b": "java:java.io.File:0-0:commit:unresolved",
    }


def test_each_class_carries_only_its_own_fields(tmp_path: Path) -> None:
    _two_packages(tmp_path)
    result = analyze_java(tmp_path)
    fields = {
        f"{_dir(s.path)}.{s.name}": set(((s.meta or {}).get("fields") or {}).items())
        for s in result.symbols if s.kind == "class"
    }
    assert fields["x.Base"] == {("conn", "java.sql.Connection")}
    assert fields["y.Base"] == {("conn", "java.io.File")}
    assert {name for name, _ in fields["a.Foo"]} == {"la"}
    assert {name for name, _ in fields["b.Foo"]} == {"lb"}


def test_two_local_classes_of_one_name_keep_their_own_bases(tmp_path: Path) -> None:
    """Two methods' local ``class L`` share the emitted name ``N.L``."""
    _write(tmp_path, "N.java",
           "public class N {\n"
           "    void a() { class L extends P {} }\n"
           "    void b() { class L extends Q {} }\n"
           "}\n"
           "class P {}\n"
           "class Q {}\n")
    result = analyze_java(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    pairs = {
        (by_id[e.src].span.start_line, by_id[e.dst].name)
        for e in result.edges if e.edge_type == "extends"
    }
    assert pairs == {(2, "P"), (3, "Q")}


def test_a_call_on_this_binds_the_enclosing_class_own_member(tmp_path: Path) -> None:
    """``helper()`` / ``this.helper()`` in ``b.Foo`` is ``b.Foo.helper``."""
    _write(tmp_path, "a/Foo.java",
           "package a;\npublic class Foo {\n    void helper() {}\n"
           "    void m() { helper(); this.helper(); }\n}\n")
    _write(tmp_path, "b/Foo.java",
           "package b;\n\n\npublic class Foo {\n    void helper() {}\n"
           "    void m() { helper(); this.helper(); }\n}\n")
    result = analyze_java(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    calls = [e for e in result.edges if e.edge_type == "calls" and e.dst in by_id]
    assert len(calls) == 4
    for e in calls:
        assert by_id[e.dst].path == by_id[e.src].path, (e.src, e.dst)


def test_another_package_namesake_member_is_not_this_class_own(tmp_path: Path) -> None:
    """``b.Foo`` declares no ``helper``: its call is handed to the inherited walk."""
    _write(tmp_path, "a/Foo.java",
           "package a;\npublic class Foo {\n    void helper() {}\n}\n")
    _write(tmp_path, "b/Foo.java",
           "package b;\npublic class Foo extends Base {\n    void m() { helper(); }\n}\n")
    _write(tmp_path, "b/Base.java",
           "package b;\npublic class Base {\n    void helper() {}\n}\n")
    result = analyze_java(tmp_path)
    (call,) = [e for e in result.edges if e.edge_type == "calls" and "Foo.m" in e.src]
    assert "a/Foo.java" not in call.dst, call.dst
    assert (call.meta or {}).get("enclosing_class") == "Foo"
    # The class itself rides beside its package-less name, so the linker's
    # walk starts at b.Foo and not at every ``Foo``.
    (b_foo,) = [s for s in result.symbols
                if s.kind == "class" and s.name == "Foo" and "/b/" in s.path]
    assert (call.meta or {}).get("enclosing_class_id") == b_foo.id


def test_the_inherited_walk_resolves_from_this_class_not_a_namesake(
    tmp_path: Path,
) -> None:
    """End to end: b.Foo's ``helper()`` lands on b.Base's, through the linker."""
    _write(tmp_path, "a/Foo.java",
           "package a;\npublic class Foo extends Base {\n    void m() { helper(); }\n}\n")
    _write(tmp_path, "a/Base.java",
           "package a;\npublic class Base {\n    void helper() {}\n}\n")
    _write(tmp_path, "b/Foo.java",
           "package b;\npublic class Foo extends Base {\n    void m() { helper(); }\n}\n")
    _write(tmp_path, "b/Base.java",
           "package b;\npublic class Base {\n    void helper() {}\n}\n")
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    paths = {n["id"]: n["path"] for n in data["nodes"]}
    calls = [
        (paths[e["src"]], paths[e["dst"]]) for e in data["edges"]
        if e["type"] == "calls" and e["src"] in paths and e["dst"] in paths
        and e["dst"].endswith(":Base.helper:method")
    ]
    assert sorted(_dir(src) for src, _ in calls) == ["a", "b"], calls
    for src, dst in calls:
        assert _dir(src) == _dir(dst), (src, dst)


def test_a_this_method_reference_binds_the_own_class_member(tmp_path: Path) -> None:
    """``this::helper`` in ``b.Foo`` references ``b.Foo.helper``."""
    for pkg, pad in (("a", ""), ("b", "\n\n\n")):
        _write(tmp_path, f"{pkg}/Foo.java",
               f"package {pkg};{pad}\npublic class Foo {{\n    void helper() {{}}\n"
               "    Runnable m() { return this::helper; }\n}\n")
    result = analyze_java(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    refs = [e for e in result.edges if e.evidence_type == "method_reference"]
    assert len(refs) == 2
    for e in refs:
        assert by_id[e.dst].path == by_id[e.src].path, (e.src, e.dst)


def test_a_base_in_the_child_package_needs_no_import(tmp_path: Path) -> None:
    """``a.Base`` and ``b.Base``: each package's ``Foo extends Base`` is its own."""
    for pkg in ("a", "b"):
        _write(tmp_path, f"{pkg}/Foo.java", f"package {pkg};\npublic class Foo extends Base {{}}\n")
        _write(tmp_path, f"{pkg}/Base.java", f"package {pkg};\npublic class Base {{}}\n")
    result = analyze_java(tmp_path)
    assert _inheritance_pairs(result, "extends") == {("a.Foo", "a.Base"), ("b.Foo", "b.Base")}
