# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Java analyzer binds itself, and what it hands the shared pass (ADR-0064).

The analyzer binds a call only on evidence Java itself uses and that it has
in its own file: the enclosing type's own member, found by the call's
position (``own_type``). Every other call is emitted unresolved with the
facts the ``inherited_calls`` linker reads, and the linker decides through
the caller's file's import table (``AnalysisResult.import_scopes``) and the
caller's package which declaration a name means:

* ``receiver_type_hint``: the type a variable's declaration writes;
* ``qualifier``: a receiver that names a project type (``Util.helper()``,
  ``util.Util.helper()``), as written;
* ``enclosing_class``: a bare or ``this.`` call the type does not declare; a
  ``super.`` call, always, with ``super_receiver``;
* a construction (``new T()``, ``T::new``) is an unresolved ``instantiates``
  stub whose callee is the type as written.

The first half pins what the analyzer emits, route by route. The second half
runs the whole pipeline, each case with a namesake in another package, so
that a bind by name would show as wrong or as tied.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.java import analyze_java


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _edges(tmp_path: Path, files: dict[str, str], caller: str, edge_type: str = "calls") -> list[Edge]:
    """The edges of ``edge_type`` the analyzer emits from the member named ``caller``."""
    result = analyze_java(_write(tmp_path, files))
    src = next(s.id for s in result.symbols if s.name == caller)
    return [e for e in result.edges if e.edge_type == edge_type and e.src == src]


def _one(edges: list[Edge], callee: str) -> Edge:
    found = [e for e in edges if (e.meta or {}).get("callee_name") == callee
             or e.dst.split(":")[-2].endswith("." + callee)]
    assert len(found) == 1, (callee, [(e.dst, e.meta) for e in edges])
    return found[0]


def _facts(edge: Edge) -> dict[str, Any]:
    meta = edge.meta or {}
    return {k: meta[k] for k in ("receiver_type_hint", "qualifier", "enclosing_class",
                                 "super_receiver", "inherited_field_receiver")
            if k in meta}


# --------------------------------------------------------------- the table


def test_every_file_hands_over_its_import_table(tmp_path: Path) -> None:
    """Single-type, on-demand and static imports, keyed by the file's path as
    its symbols spell it. ``java.lang`` and the file's own package are not
    recorded; a file with no import hands over an empty table."""
    result = analyze_java(_write(tmp_path, {
        "app/A.java": (
            "package app;\n"
            "import data.Conn;\n"
            "import util.*;\n"
            "import static util.Strings.trim;\n"
            "import static util.Numbers.*;\n"
            "import java.util.Map.Entry;\n"
            "public class A {}\n"
        ),
        "app/B.java": "package app;\npublic class B {}\n",
    }))
    paths = {s.name: s.path for s in result.symbols}
    a, b = result.import_scopes[paths["A"]], result.import_scopes[paths["B"]]
    assert (a.named("Conn").module, a.named("Conn").name) == ("data", "Conn")
    assert (a.named("trim").module, a.named("trim").name) == ("util.Strings", "trim")
    assert (a.named("Entry").module, a.named("Entry").name) == ("java.util.Map", "Entry")
    assert a.wildcard_modules == ("util", "util.Numbers")
    assert a.named("String") is None and a.named("A") is None
    assert b.wildcard_modules == () and b.named("B") is None
    # The name step is not for Java: it has no screen of its library's names.
    assert not a.library_names_screened and not b.library_names_screened


def test_an_import_of_the_unnamed_package_is_not_recorded(tmp_path: Path) -> None:
    """``import Foo;`` names a type of the unnamed package, which no file of
    a named package can import (JLS 7.5)."""
    result = analyze_java(_write(tmp_path, {"A.java": "import Foo;\nclass A {}\n"}))
    (scope,) = result.import_scopes.values()
    assert scope.named("Foo") is None and scope.wildcard_modules == ()


# ----------------------------------------------- the type's own member (R1)


def test_a_bare_call_binds_the_own_member_of_the_type_around_it(tmp_path: Path) -> None:
    """Two local classes ``L`` in one file share the name ``Outer.L``: each
    call reaches the ``m`` of the ``L`` it is written in, found by position."""
    result = analyze_java(_write(tmp_path, {"p/Outer.java": (
        "package p;\n"
        "class Outer {\n"
        "    void a() {\n"
        "        class L { int m() { return 1; } int go() { return m(); } }\n"
        "    }\n"
        "    void b() {\n"
        "        class L { int m() { return 2; } int go() { return this.m(); } }\n"
        "    }\n"
        "}\n"
    )}))
    by_id = {s.id: s for s in result.symbols}
    calls = sorted(
        (by_id[e.src].span.start_line, by_id[e.dst].span.start_line, e.meta.get("resolved_by"))
        for e in result.edges if e.edge_type == "calls" and e.dst in by_id
    )
    assert calls == [(4, 4, "own_type"), (7, 7, "own_type")]


def test_of_several_overloads_the_one_with_as_many_parameters_as_arguments(tmp_path: Path) -> None:
    """``getPet(name, false)`` inside ``getPet(String)`` is the two-parameter
    overload, not the method it is written in; a variable-arity overload
    takes what no fixed one does; of overloads of one arity, told apart by
    their types only, the last declared (the hierarchy linker's choice)."""
    result = analyze_java(_write(tmp_path, {"p/Owner.java": (
        "package p;\n"
        "class Owner {\n"
        "    Object getPet(String name) { return getPet(name, false); }\n"
        "    Object getPet(Integer id) { return null; }\n"
        "    Object getPet(String name, boolean ignoreNew) { return null; }\n"
        "    Object all(String... names) { return null; }\n"
        "    Object all(int one, int two) { return null; }\n"
        "    void use() { getPet(3); all(\"a\", \"b\", \"c\"); all(1, /* two */ 2); }\n"
        "}\n"
    )}))
    by_id = {s.id: s for s in result.symbols}
    calls = sorted(
        (by_id[e.src].name, e.line, by_id[e.dst].span.start_line)
        for e in result.edges if e.edge_type == "calls" and e.dst in by_id
    )
    assert calls == [
        ("Owner.getPet", 3, 5),
        ("Owner.use", 8, 4),   # getPet(3): two one-parameter overloads, the last
        ("Owner.use", 8, 6),   # all("a", "b", "c"): only the variable-arity one
        ("Owner.use", 8, 7),   # all(1, 2): the two-parameter one (a comment is no argument)
    ]


def test_a_member_the_type_does_not_declare_is_reported_with_the_type(tmp_path: Path) -> None:
    edges = _edges(tmp_path, {"p/Child.java": (
        "package p;\n"
        "class Child extends Base {\n"
        "    void run() { save(); this.load(); }\n"
        "}\n"
    )}, "Child.run")
    for callee in ("save", "load"):
        stub = _one(edges, callee)
        assert not stub.is_resolved
        assert _facts(stub) == {"enclosing_class": "Child"}
        assert stub.meta["enclosing_class_id"].endswith(":Child:class")


def test_a_call_on_super_is_the_parents_never_the_own_member(tmp_path: Path) -> None:
    edges = _edges(tmp_path, {"p/Child.java": (
        "package p;\n"
        "class Child extends Base {\n"
        "    void save() { super.save(); }\n"
        "}\n"
    )}, "Child.save")
    (stub,) = edges
    assert not stub.is_resolved
    assert _facts(stub) == {"enclosing_class": "Child", "super_receiver": True}


# ------------------------------------------------------ a call on a receiver


_UTIL = "package util;\npublic class Util { public static int helper() { return 1; } }\n"


def test_a_receiver_that_names_a_type_is_the_qualifier_as_written(tmp_path: Path) -> None:
    edges = _edges(tmp_path, {
        "util/Util.java": _UTIL,
        "util/Outer.java": "package util;\npublic class Outer { public static class Inner {} }\n",
        "app/Main.java": (
            "package app;\n"
            "import util.Util;\n"
            "class Main {\n"
            "    void run() { Util.helper(); util.Util.helper(); util.Outer.Inner.make(); Math.max(1, 2); }\n"
            "}\n"
        ),
    }, "Main.run")
    stubs = [e for e in edges if e.meta.get("callee_name") == "helper"]
    assert all(not e.is_resolved for e in stubs)
    assert sorted(e.meta.get("qualifier") for e in stubs) == ["Util", "util.Util"]
    assert _one(edges, "make").meta.get("qualifier") == "util.Outer.Inner"
    # A type no file of the repository declares is a library's: no qualifier.
    assert "qualifier" not in _one(edges, "max").meta


def test_a_variable_named_like_a_type_is_the_variable(tmp_path: Path) -> None:
    """In an expression a simple name means the variable (JLS 6.4.2): a
    parameter ``Conn Util`` makes ``Util.open()`` a call on a ``Conn``."""
    edges = _edges(tmp_path, {
        "util/Util.java": _UTIL,
        "app/Main.java": (
            "package app;\n"
            "class Main {\n"
            "    void run(Conn Util) { Util.open(); }\n"
            "}\n"
        ),
    }, "Main.run")
    assert _facts(_one(edges, "open")) == {"receiver_type_hint": "Conn"}


def test_a_typed_receiver_is_reported_with_its_declared_type(tmp_path: Path) -> None:
    edges = _edges(tmp_path, {"app/Main.java": (
        "package app;\n"
        "import data.Conn;\n"
        "class Main {\n"
        "    void run(Conn c) { c.open(); }\n"
        "}\n"
    )}, "Main.run")
    stub = _one(edges, "open")
    assert not stub.is_resolved and _facts(stub) == {"receiver_type_hint": "Conn"}


def test_a_return_type_is_read_through_the_class_the_file_imports(tmp_path: Path) -> None:
    """``var c = f.make(); c.open()`` types ``c`` from the return type of the
    ``make`` of ``f``'s class (the return-type cascade, which binds no call).
    The method is read when the file's import of that class names it by its
    last two segments too (a source root that does not spell the whole
    package)."""
    stub = _one(_edges(tmp_path, {
        "src/logging/Factory.java": (
            "package com.example.logging;\n"
            "public class Factory { public Conn make() { return null; } }\n"
        ),
        "src/logging/Conn.java": "package com.example.logging;\npublic class Conn {}\n",
        "src/app/App.java": (
            "package com.example.app;\n"
            "import com.example.logging.Factory;\n"
            "class App { void run(Factory f) { var c = f.make(); c.open(); } }\n"
        ),
    }, "App.run"), "open")
    assert _facts(stub) == {"receiver_type_hint": "Conn"}


# -------------------------------------------------------------- constructions


def test_a_construction_is_reported_with_the_type_as_written(tmp_path: Path) -> None:
    """Plain, generic, package-qualified and nested: each an unresolved
    ``instantiates`` stub whose callee is the type, type arguments removed.
    ``T::new`` too; an array constructor reference constructs no type."""
    edges = _edges(tmp_path, {"app/Main.java": (
        "package app;\n"
        "import java.util.function.Supplier;\n"
        "class Main {\n"
        "    void run() {\n"
        "        new Conn();\n"
        "        new Box<String>();\n"
        "        new data.Conn();\n"
        "        new Outer.Inner();\n"
        "        Supplier<Conn> s = Conn::new;\n"
        "        Supplier<Box<String>> b = Box<String>::new;\n"
        "        java.util.function.IntFunction<int[]> a = int[]::new;\n"
        "    }\n"
        "}\n"
    )}, "Main.run", edge_type="instantiates")
    assert all(not e.is_resolved and e.evidence_type == "ast_new" for e in edges)
    assert sorted(e.meta["callee_name"] for e in edges) == [
        "Box", "Box", "Conn", "Conn", "Outer.Inner", "data.Conn",
    ]


# ---------------------------------------------- through the shared pass


def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = _write(tmp_path / "repo", files)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _resolved(
    data: dict[str, Any], caller: str, callee: str, edge_type: str = "calls",
) -> list[tuple[str, str]]:
    """(target's qualified name, mark) of each resolved edge of ``edge_type``
    from ``caller`` to a symbol named ``callee`` (a method by its last
    segment, a type by its name)."""
    nodes = {n["id"]: n for n in data["nodes"]}
    return sorted(
        (nodes[e["dst"]].get("qualified_name") or "", (e.get("meta") or {}).get("resolved_by"))
        for e in data["edges"]
        if e["type"] == edge_type and e.get("is_resolved")
        and not (e.get("meta") or {}).get("superseded_by")
        and nodes.get(e["src"], {}).get("name") == caller
        and e["dst"] in nodes
        and (nodes[e["dst"]]["name"] == callee or nodes[e["dst"]]["name"].endswith("." + callee))
    )


def _conn(package: str) -> str:
    return f"package {package};\npublic class Conn {{ public void open() {{}} }}\n"


def test_end_to_end_a_typed_receiver_reaches_the_imported_class_not_a_namesake(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "data/Conn.java": _conn("data"),
        "legacy/Conn.java": _conn("legacy"),
        "app/Main.java": (
            "package app;\nimport data.Conn;\n"
            "public class Main { public void run(Conn c) { c.open(); } }\n"
        ),
    })
    assert _resolved(data, "Main.run", "open") == [("data.Conn.open", "receiver_type")]


def test_end_to_end_new_reaches_the_imported_and_the_same_package_type(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "data/Conn.java": _conn("data"),
        "legacy/Conn.java": _conn("legacy"),
        "app/Pool.java": "package app;\npublic class Pool {}\n",
        "legacy/Pool.java": "package legacy;\npublic class Pool {}\n",
        "app/Main.java": (
            "package app;\nimport data.Conn;\n"
            "public class Main { public void run() { new Conn(); new Pool(); new legacy.Pool(); } }\n"
        ),
    })
    assert _resolved(data, "Main.run", "Conn", "instantiates") == [("data.Conn", "import")]
    assert _resolved(data, "Main.run", "Pool", "instantiates") == [
        ("app.Pool", "same_module"), ("legacy.Pool", "qualified"),
    ]


def test_end_to_end_a_static_call_reaches_the_type_it_names(tmp_path: Path) -> None:
    """``Util.helper()`` under ``import util.Util`` and ``util.Util.helper()``:
    util's ``Util``, never the namesake of another package. A file that does
    not see any ``Util`` binds none."""
    data = _map(tmp_path, {
        "util/Util.java": _UTIL,
        "decoy/Util.java": _UTIL.replace("package util;", "package decoy;"),
        "app/Main.java": (
            "package app;\nimport util.Util;\n"
            "public class Main { public void run() { Util.helper(); } "
            "public void qual() { util.Util.helper(); } }\n"
        ),
        "web/Page.java": "package web;\npublic class Page { public void run() { Util.helper(); } }\n",
    })
    assert _resolved(data, "Main.run", "helper") == [("util.Util.helper", "qualified")]
    assert _resolved(data, "Main.qual", "helper") == [("util.Util.helper", "qualified")]
    assert _resolved(data, "Page.run", "helper") == []


def test_end_to_end_super_reaches_the_parent_not_the_override(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "app/Base.java": "package app;\npublic class Base { public void save() {} }\n",
        "legacy/Base.java": "package legacy;\npublic class Base { public void save() {} }\n",
        "app/Child.java": (
            "package app;\n"
            "public class Child extends Base { public void save() { super.save(); } }\n"
        ),
    })
    assert _resolved(data, "Child.save", "save") == [("app.Base.save", "own_type")]


def test_end_to_end_an_untyped_receiver_stays_unresolved(tmp_path: Path) -> None:
    """``var x = Lib.make()`` with ``Lib`` a library's: nothing says what
    ``x`` is, and the repository's one ``execute`` is not bound to it."""
    data = _map(tmp_path, {
        "data/Job.java": "package data;\npublic class Job { public void execute() {} }\n",
        "app/Main.java": (
            "package app;\nimport ext.Lib;\n"
            "public class Main { public void run() { var x = Lib.make(); x.execute(); } }\n"
        ),
    })
    assert _resolved(data, "Main.run", "execute") == []


def test_end_to_end_a_parent_package_is_no_scope(tmp_path: Path) -> None:
    """Java packages do not nest (JLS 7.1): ``Builder`` written in
    ``jedis.search`` is not ``jedis.Builder`` unless the file imports it."""
    builder = "package jedis;\npublic class Builder { public void build() {} }\n"
    body = "{\n  void run(Builder b) { b.build(); new Builder(); }\n}\n"
    data = _map(tmp_path, {
        "jedis/Builder.java": builder,
        "jedis/search/Query.java": "package jedis.search;\npublic class Query " + body,
        "jedis/search/Other.java": "package jedis.search;\nimport jedis.Builder;\npublic class Other " + body,
    })
    assert _resolved(data, "Query.run", "build") == []
    assert _resolved(data, "Query.run", "Builder", "instantiates") == []
    assert _resolved(data, "Other.run", "build") == [("jedis.Builder.build", "receiver_type")]
    assert _resolved(data, "Other.run", "Builder", "instantiates") == [("jedis.Builder", "import")]


def test_end_to_end_a_nested_type_and_a_statically_imported_method(tmp_path: Path) -> None:
    """A nested type is found as ``Outer.Inner`` from outside and ``Inner``
    inside its outer type, never a namesake nested elsewhere; ``import static
    util.Text.pad;`` makes a bare ``pad(..)`` that method, not ``ui.Box.pad``."""
    data = _map(tmp_path, {
        "db/Config.java": "package db;\npublic class Config {\n"
                          "  public static class Builder { public Config build() { return null; } }\n"
                          "  static Builder fresh() { return new Builder(); }\n}\n",
        "web/Request.java": "package web;\npublic class Request {\n"
                            "  public static class Builder { public void build() {} }\n}\n",
        "util/Text.java": "package util;\npublic class Text { public static String pad(String s) { return s; } }\n",
        "ui/Box.java": "package ui;\npublic class Box { public static String pad(String s) { return s; } }\n",
        "app/Main.java": "package app;\nimport db.Config;\nimport static util.Text.pad;\n"
                         "public class Main {\n  void run() {\n"
                         "    Config.Builder b = new Config.Builder();\n    b.build();\n    pad(\"x\");\n  }\n}\n",
    })
    assert _resolved(data, "Main.run", "Builder", "instantiates") == [("db.Config.Builder", "import")]
    assert _resolved(data, "Main.run", "build") == [("db.Config.Builder.build", "receiver_type")]
    assert _resolved(data, "Main.run", "pad") == [("util.Text.pad", "import")]
    assert _resolved(data, "Config.fresh", "Builder", "instantiates") == [
        ("db.Config.Builder", "same_module"),
    ]


# ------------------------------- the parameters declared, the arguments written (WI-tosut)


def test_every_method_and_constructor_declares_its_parameters(tmp_path: Path) -> None:
    """One ``parameter_entry`` per declared parameter, in order: a plain, a
    generic, an array written after the name and a varargs one (its element
    type, ``variadic``); a final varargs; a varargs the grammar cannot parse
    (an annotation before ``...``) is variadic too. A receiver parameter
    (``Shape this``) is not listed: no call writes it. A callable with none
    declares the empty list, never an absent key. A record's compact
    constructor takes the record's components."""
    result = analyze_java(_write(tmp_path, {"p/Shape.java": (
        "package p;\n"
        "import java.util.List;\n"
        "class Shape {\n"
        "    Shape() {}\n"
        "    Shape(int w, List<String> tags, int grid[], String... names) {}\n"
        "    void none() {}\n"
        "    void self(Shape this, int a) {}\n"
        "    void last(final int... xs) {}\n"
        "    void annotated(String @Deprecated ... keys) {}\n"
        "    record Point(int x, int y) { Point { } }\n"
        "}\n"
        "interface Api { int size(); void put(String k, Object v); }\n"
    )}))
    params = {
        (s.name, s.span.start_line): s.meta["parameters"]
        for s in result.symbols if s.kind in ("method", "constructor")
    }

    def entry(name: str, type_: str, variadic: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"name": name, "type": type_, "default": False}
        if variadic:
            out["variadic"] = True
        return out

    assert params == {
        ("Shape.Shape", 4): [],
        ("Shape.Shape", 5): [
            entry("w", "int"), entry("tags", "List<String>"), entry("grid", "int[]"),
            entry("names", "String", variadic=True),
        ],
        ("Shape.none", 6): [],
        ("Shape.self", 7): [entry("a", "int")],
        ("Shape.last", 8): [entry("xs", "int", variadic=True)],
        ("Shape.annotated", 9): [entry("keys", "String @Deprecated", variadic=True)],
        ("Shape.Point.Point", 10): [entry("x", "int"), entry("y", "int")],
        ("Api.size", 12): [],
        ("Api.put", 12): [entry("k", "String"), entry("v", "Object")],
    }


def test_every_call_and_construction_says_how_many_arguments_it_writes(tmp_path: Path) -> None:
    """Bare, on ``this``, on ``super``, on a typed receiver, on a type, on a
    chain and on a construction: the receiver is never counted, a comment in
    the list is no argument. ``new T(..)`` counts its arguments, an anonymous
    class body included; ``T::new`` writes no argument list and says nothing."""
    edges = _edges(tmp_path, {"app/Main.java": (
        "package app;\n"
        "import java.util.function.Supplier;\n"
        "class Main extends Base {\n"
        "    void run(Conn c) {\n"
        "        inherited();\n"
        "        this.later(1, 2);\n"
        "        super.save(\"a\");\n"
        "        c.open(1, /* port */ 2, 3);\n"
        "        Util.helper(x, y);\n"
        "        c.next().close();\n"
        "        new Conn(1, 2);\n"
        "        new Box() { };\n"
        "        Supplier<Conn> s = Conn::new;\n"
        "    }\n"
        "}\n"
    )}, "Main.run")
    counts = sorted(
        (e.meta["callee_name"], e.meta.get("argument_count")) for e in edges
        if not e.is_resolved
    )
    assert counts == [
        ("close", 0), ("helper", 2), ("inherited", 0), ("later", 2),
        ("next", 0), ("open", 3), ("save", 1),
    ]
    created = _edges(tmp_path, {}, "Main.run", edge_type="instantiates")
    assert sorted(
        ((e.meta["callee_name"], e.meta.get("argument_count")) for e in created), key=repr,
    ) == [("Box", 0), ("Conn", 2), ("Conn", None)]


def test_an_own_member_that_cannot_take_the_call_is_not_bound(tmp_path: Path) -> None:
    """``send(a, b)`` in a type that declares only ``send(a)`` is the parent's
    overload: no own member is bound, the call is reported with the type. Of
    overloads none of which takes the count, none is bound either."""
    edges = _edges(tmp_path, {"p/Child.java": (
        "package p;\n"
        "class Child extends Base {\n"
        "    void send(String a) {}\n"
        "    void log(int a) {}\n"
        "    void log(int a, int b) {}\n"
        "    void run() { send(\"a\", \"b\"); log(1, 2, 3); send(\"c\"); }\n"
        "}\n"
    )}, "Child.run")
    stubs = sorted(
        (e.meta["callee_name"], e.meta.get("argument_count"), _facts(e)["enclosing_class"])
        for e in edges if not e.is_resolved
    )
    assert stubs == [("log", 3, "Child"), ("send", 2, "Child")]
    assert [e.meta.get("resolved_by") for e in edges if e.is_resolved] == ["own_type"]


# The LAST declared overload is the wrong one in each case below: the method
# index of the hierarchy linker keeps the last of a name, so a bind that does
# not read the argument count lands there.


def test_end_to_end_overloads_that_differ_in_count_on_a_typed_receiver(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "net/Server.java": (
            "package net;\npublic class Server {\n"
            "  public void takeRequest() {}\n"
            "  public void takeRequest(long timeout, Object unit) {}\n"
            "  public void start() {}\n"
            "  public void start(int port) {}\n}\n"
        ),
        "app/Main.java": (
            "package app;\nimport net.Server;\n"
            "public class Main {\n"
            "  void take(Server s) { s.takeRequest(); }\n"
            "  void begin(Server s) { s.start(); }\n"
            "  void bind(Server s) { s.start(8080); }\n}\n"
        ),
    })
    nodes = {n["id"]: n for n in data["nodes"]}

    def lines(caller: str, callee: str) -> list[int]:
        return sorted(
            nodes[e["dst"]]["span"]["start_line"] for e in data["edges"]
            if e["type"] == "calls" and e.get("is_resolved") and e["dst"] in nodes
            and nodes.get(e["src"], {}).get("name") == caller
            and nodes[e["dst"]]["name"] == callee
        )

    assert lines("Main.take", "Server.takeRequest") == [3]
    assert lines("Main.begin", "Server.start") == [5]
    assert lines("Main.bind", "Server.start") == [6]


def _bound_lines(data: dict[str, Any], caller: str, callee: str) -> list[tuple[int, str]]:
    """(start line of the target, mark) of each resolved call from ``caller``
    to a method named ``callee``."""
    nodes = {n["id"]: n for n in data["nodes"]}
    return sorted(
        (nodes[e["dst"]]["span"]["start_line"], (e.get("meta") or {}).get("resolved_by"))
        for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved") and e["dst"] in nodes
        and not (e.get("meta") or {}).get("superseded_by")
        and nodes.get(e["src"], {}).get("name") == caller
        and nodes[e["dst"]]["name"] == callee
    )


def test_end_to_end_a_variadic_overload(tmp_path: Path) -> None:
    """``f.format("a", 1, 2)`` takes ``format(String, Object...)``, not the
    two-parameter overload declared after it; ``f.format("a")`` takes it too
    (zero varargs); ``f.format("a", 1)`` has two candidates, the last kept."""
    data = _map(tmp_path, {
        "util/Fmt.java": (
            "package util;\npublic class Fmt {\n"
            "  public String format(String s, Object... args) { return s; }\n"
            "  public String format(String s, int width) { return s; }\n}\n"
        ),
        "app/Main.java": (
            "package app;\nimport util.Fmt;\npublic class Main {\n"
            "  void many(Fmt f) { f.format(\"a\", 1, 2); }\n"
            "  void one(Fmt f) { f.format(\"a\"); }\n"
            "  void two(Fmt f) { f.format(\"a\", 1); }\n}\n"
        ),
    })
    assert _bound_lines(data, "Main.many", "Fmt.format") == [(3, "receiver_type")]
    assert _bound_lines(data, "Main.one", "Fmt.format") == [(3, "receiver_type")]
    assert _bound_lines(data, "Main.two", "Fmt.format") == [(4, "receiver_type")]


def test_end_to_end_an_inherited_overload_beside_the_types_own(tmp_path: Path) -> None:
    """``Tls`` declares ``send(a)``, its parent ``Channel`` ``send(a, b)``.
    Two arguments on a ``Tls`` receiver, and a bare two-argument call inside
    ``Tls``, are the parent's overload; one argument is the own."""
    data = _map(tmp_path, {
        "net/Channel.java": (
            "package net;\npublic class Channel {\n"
            "  public void send(String a, String b) {}\n}\n"
        ),
        "net/Tls.java": (
            "package net;\npublic class Tls extends Channel {\n"
            "  public void send(String a) {}\n"
            "  void relay() { send(\"a\", \"b\"); send(\"c\"); }\n}\n"
        ),
        "app/Main.java": (
            "package app;\nimport net.Tls;\npublic class Main {\n"
            "  void two(Tls t) { t.send(\"a\", \"b\"); }\n"
            "  void one(Tls t) { t.send(\"a\"); }\n}\n"
        ),
    })
    assert _bound_lines(data, "Main.two", "Channel.send") == [(3, "receiver_type")]
    assert _bound_lines(data, "Main.two", "Tls.send") == []
    assert _bound_lines(data, "Main.one", "Tls.send") == [(3, "receiver_type")]
    assert _bound_lines(data, "Tls.relay", "Channel.send") == [(3, "own_type")]
    assert _bound_lines(data, "Tls.relay", "Tls.send") == [(3, "own_type")]


def test_end_to_end_a_bare_call_of_an_own_member_with_overloads(tmp_path: Path) -> None:
    """``log("x")`` inside the type is the one-parameter overload, not the
    two-parameter one declared after it; ``log("x", e)`` is that one."""
    data = _map(tmp_path, {
        "app/Svc.java": (
            "package app;\npublic class Svc {\n"
            "  void log(String m) {}\n"
            "  void log(String m, Throwable t) {}\n"
            "  void one() { log(\"x\"); }\n"
            "  void two(Throwable e) { this.log(\"x\", e); }\n}\n"
        ),
    })
    assert _bound_lines(data, "Svc.one", "Svc.log") == [(3, "own_type")]
    assert _bound_lines(data, "Svc.two", "Svc.log") == [(4, "own_type")]


def test_end_to_end_a_statically_imported_overload(tmp_path: Path) -> None:
    """``import static util.Text.pad;`` and ``pad("x")``: the one-parameter
    overload, not the two-parameter one declared after it."""
    data = _map(tmp_path, {
        "util/Text.java": (
            "package util;\npublic class Text {\n"
            "  public static String pad(String s) { return s; }\n"
            "  public static String pad(String s, int n) { return s; }\n}\n"
        ),
        "app/Main.java": (
            "package app;\nimport static util.Text.pad;\npublic class Main {\n"
            "  void one() { pad(\"x\"); }\n  void two() { pad(\"x\", 2); }\n}\n"
        ),
    })
    assert _bound_lines(data, "Main.one", "Text.pad") == [(3, "import")]
    assert _bound_lines(data, "Main.two", "Text.pad") == [(4, "import")]
