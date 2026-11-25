# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Kotlin analyzer binds itself, and what it hands the shared pass (ADR-0064).

The analyzer binds a call only on evidence Kotlin itself uses and that it has
in hand: the enclosing type's own member, found by the call's position
(``own_type``), and a member of an object or companion the file imports by
name, at the path the import names (``import``). Every other call is emitted
unresolved with the facts the shared linkers read, and they decide from the
caller's file's import table (``AnalysisResult.import_scopes``) and the
caller's package:

* a bare call, which in Kotlin may be a construction (``Request(url)``): no
  fact, or ``enclosing_class`` when written inside a type; a construction of a
  nested or qualified type is the type as written (``Request.Builder``);
* ``receiver_type_hint``: the receiver's type as written (a parameter, a
  declared or constructed local, a property, a callee's declared return type);
* ``qualifier``: a receiver that names a type (``Util.helper()``);
* ``enclosing_class`` for ``this.m()`` the type does not declare, with
  ``super_receiver`` for ``super.m()``;
* ``stdlib_member_name`` on a call whose name the Kotlin standard library
  defines on its types, so that the name step does not bind it.

The first half pins what the analyzer emits, route by route. The second half
runs the whole pipeline for the cases the shared pass exists for, each with a
namesake in another package that a bind by name would reach or tie with.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.kotlin import _KOTLIN_LIBRARY_MEMBER_NAMES, analyze_kotlin


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _analyze(tmp_path: Path, files: dict[str, str]) -> AnalysisResult:
    return analyze_kotlin(_write(tmp_path, files))


def _calls(result: AnalysisResult, caller: str) -> list[Edge]:
    """The ``calls`` edges the analyzer emits from the symbol named ``caller``."""
    src = next(s.id for s in result.symbols if s.name == caller)
    return [e for e in result.edges if e.edge_type == "calls" and e.src == src]


def _one(edges: list[Edge], callee: str) -> Edge:
    found = [e for e in edges if (e.meta or {}).get("callee_name") == callee
             or e.dst.split(":")[-2] == callee or e.dst.split(":")[-2].endswith("." + callee)]
    assert len(found) == 1, (callee, [(e.dst, e.meta) for e in edges])
    return found[0]


_FACTS = ("receiver_type_hint", "qualifier", "enclosing_class", "super_receiver",
          "call_construct", "stdlib_member_name")


def _facts(edge: Edge) -> dict[str, Any]:
    assert not edge.is_resolved, edge.dst
    meta = edge.meta or {}
    return {k: meta[k] for k in _FACTS if k in meta}


# --------------------------------------------------------------- the table

def test_every_file_hands_over_its_import_table(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "src/a/Main.kt": (
            "package a\n\n"
            "import b.Svc\n"
            "import b.Other as Renamed\n"
            "import c.*\n"
            "import okhttp3.Headers.Companion.headersOf\n"
            "import Top\n\n"
            "fun main() {}\n"
        ),
        "src/b/Svc.kt": "package b\n\nclass Svc\n",
    })
    paths = {s.name: s.path for s in result.symbols}
    main = result.import_scopes[paths["main"]]
    assert main.library_names_screened
    assert (main.named("Svc").module, main.named("Svc").name) == ("b", "Svc")
    assert (main.named("Renamed").module, main.named("Renamed").name) == ("b", "Other")
    assert main.named("Other") is None
    # A companion's member is named without ``Companion``.
    assert (main.named("headersOf").module, main.named("headersOf").name) == (
        "okhttp3.Headers", "headersOf")
    assert (main.named("Top").module, main.named("Top").name) == ("", "Top")
    assert main.wildcard_modules == ("c",)
    # A file with no import still hands over a table: it has adopted the pass.
    svc = result.import_scopes[paths["Svc"]]
    assert svc.wildcard_modules == () and svc.named("Svc") is None
    assert svc.library_names_screened


# ---------------------------------------------------- bound by the analyzer

def test_the_enclosing_types_own_member_is_bound_by_position(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "class Point {\n"
        "    fun helper(): Int = 1\n"
        "    val handler: () -> Unit = {}\n"
        "    fun m() {\n        helper()\n        this.helper()\n        handler()\n    }\n"
        "}\n"
    )})
    by_id = {s.id: s for s in result.symbols}
    bound = sorted(
        (e.line, by_id[e.dst].name, e.evidence_type, e.meta["resolved_by"])
        for e in _calls(result, "Point.m") if e.is_resolved
    )
    assert bound == [
        (7, "Point.helper", "ast_call", "own_type"),
        (8, "Point.helper", "ast_call_this", "own_type"),
        (9, "Point.handler", "ast_call", "own_type"),
    ]


def test_an_imported_member_of_an_object_or_companion_is_reported_and_bound_at_its_path(
    tmp_path: Path,
) -> None:
    """``import okhttp3.Headers.Companion.headersOf``: the analyzer reports the
    bare call, and the shared lookup binds the member the import names
    (``okhttp3.Headers.headersOf``), not the namesake of another package."""
    files = {
        "okhttp3/Headers.kt": (
            "package okhttp3\n\nclass Headers {\n    companion object {\n"
            "        fun headersOf(vararg s: String): Headers = Headers()\n    }\n}\n"
        ),
        "fake/Headers.kt": (
            "package fake\n\nobject Headers {\n"
            "    fun headersOf(vararg s: String): Int = 1\n}\n"
        ),
        "app/Use.kt": (
            "package app\n\nimport okhttp3.Headers.Companion.headersOf\nimport fake.helper\n\n"
            "fun go(): Any = headersOf(\"a\")\nfun other(): Any = helper()\n"
        ),
        "fake/Util.kt": "package fake\n\nfun helper(): Int = 1\n",
    }
    result = _analyze(tmp_path, files)
    assert [e.is_resolved for e in _calls(result, "go")] == [False]
    # An imported top-level function is reported the same way, named by the import.
    stub = _one(_calls(result, "other"), "helper")
    assert _facts(stub) == {}
    assert stub.dst == "kotlin:fake.helper:0-0:helper:unresolved"
    data = _map(tmp_path / "whole", files)
    assert _resolved(data, "go", "headersOf") == [("okhttp3.Headers.headersOf", "import")]
    assert _resolved(data, "other", "helper") == [("fake.helper", "import")]


# ------------------------------------------------------- reported, with facts

def test_a_bare_call_and_a_construction_are_bare_stubs(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "fun helper() {}\n"
        "class Conn\n"
        "fun top() {\n    helper()\n    Conn()\n}\n"
        "class Main {\n    fun run() {\n        helper()\n        Conn()\n    }\n}\n"
    )})
    top = _calls(result, "top")
    assert _facts(_one(top, "helper")) == {}
    assert _facts(_one(top, "Conn")) == {}
    run = _calls(result, "Main.run")
    assert _facts(_one(run, "helper")) == {"enclosing_class": "Main"}
    assert _facts(_one(run, "Conn")) == {"enclosing_class": "Main"}
    assert not [e for e in top + run if e.is_resolved]


def test_a_construction_of_a_nested_or_qualified_type_is_the_type_as_written(
    tmp_path: Path,
) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "class Request {\n    class Builder\n}\n"
        "class Main {\n    fun run() {\n"
        "        Request.Builder()\n        a.Request()\n        a.Request.Builder()\n"
        "    }\n}\n"
    )})
    names = sorted((e.meta or {}).get("callee_name") for e in _calls(result, "Main.run"))
    assert names == ["Request.Builder", "a.Request", "a.Request.Builder"]
    for edge in _calls(result, "Main.run"):
        assert _facts(edge) == {}, edge.meta


def test_a_call_on_a_type_names_its_owner(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "import b.Util\n\n"
        "fun go() {\n    Util.helper()\n    b.Util.helper2()\n    Outer.Inner.ping()\n}\n"
    )})
    go = _calls(result, "go")
    assert _facts(_one(go, "helper")) == {"qualifier": "Util", "call_construct": "method"}
    assert _one(go, "helper").dst == "kotlin:b.Util:0-0:helper:unresolved"
    assert _facts(_one(go, "helper2")) == {"qualifier": "b.Util", "call_construct": "method"}
    assert _facts(_one(go, "ping")) == {"qualifier": "Outer.Inner", "call_construct": "method"}


def test_a_typed_receiver_carries_its_type_as_written(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "class Conn {\n    fun open() {}\n    fun close() {}\n    fun ping() {}\n}\n"
        "class Request {\n    class Builder {\n        fun url() {}\n    }\n}\n"
        "class Main(private val conn: Conn) {\n"
        "    fun run(p: Conn) {\n"
        "        p.open()\n"
        "        val c: Conn = make()\n        c.close()\n"
        "        val b = Request.Builder()\n        b.url()\n"
        "        this.conn.ping()\n"
        "    }\n"
        "}\n"
        "fun make(): Conn = Conn()\n"
    )})
    run = _calls(result, "Main.run")
    assert _facts(_one(run, "open")) == {"receiver_type_hint": "Conn", "call_construct": "method"}
    assert _facts(_one(run, "close")) == {
        "receiver_type_hint": "Conn", "call_construct": "method", "stdlib_member_name": True}
    assert _facts(_one(run, "url")) == {
        "receiver_type_hint": "Request.Builder", "call_construct": "method"}
    assert _facts(_one(run, "ping")) == {"receiver_type_hint": "Conn", "call_construct": "method"}


def test_this_and_super_calls_carry_the_enclosing_type(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "import java.io.OutputStream\n\n"
        "open class Base {\n    open fun m() {}\n    fun inherited() {}\n}\n"
        "class Kid : Base() {\n"
        "    override fun m() {\n        super.m()\n        this.inherited()\n    }\n"
        "}\n"
        "class Sink : OutputStream() {\n"
        "    override fun flush() {\n        super.flush()\n    }\n"
        "    override fun write(b: Int) {}\n"
        "}\n"
        "fun Kid.ext() {\n    this.m()\n}\n"
    )})
    kid = _calls(result, "Kid.m")
    assert _facts(_one(kid, "m")) == {
        "enclosing_class": "Kid", "super_receiver": True, "call_construct": "method"}
    assert _facts(_one(kid, "inherited")) == {"enclosing_class": "Kid", "call_construct": "method"}
    # The module slot still names the first declared base, for the taint gates.
    flush = _one(_calls(result, "Sink.flush"), "flush")
    assert flush.dst == "kotlin:java.io.OutputStream:0-0:flush:unresolved"
    assert _facts(flush)["super_receiver"] is True
    # ``this`` in an extension function: no enclosing type to walk.
    assert _facts(_one(_calls(result, "ext"), "m")) == {"call_construct": "method"}


def test_a_name_three_types_declare_is_still_reported(tmp_path: Path) -> None:
    """The analyzer used to DROP a bare call whose name three or more methods
    of the run declared (the AMB-METHOD guard), with no edge at all, even when
    the enclosing type was one of them."""
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "class A {\n    fun go() {}\n    fun run() {\n        go()\n        stop()\n    }\n}\n"
        "class B {\n    fun go() {}\n    fun stop() {}\n}\n"
        "class C {\n    fun go() {}\n    fun stop() {}\n}\n"
        "class D {\n    fun stop() {}\n}\n"
    )})
    run = _calls(result, "A.run")
    by_id = {s.id: s for s in result.symbols}
    assert [by_id[e.dst].name for e in run if e.is_resolved] == ["A.go"]
    assert _facts(_one(run, "stop")) == {"enclosing_class": "A"}


def test_an_untyped_receiver_has_no_fact_and_a_library_name_is_marked(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "fun obtain(): Any = 1\n"
        "fun go(xs: List<Int>) {\n"
        "    val any = obtain()\n"
        "    any.drain()\n    any.let { }\n    \"x\".trim()\n    repeat(3) { }\n"
        "    xs.map { it }\n"
        "}\n"
    )})
    go = _calls(result, "go")
    assert _facts(_one(go, "drain")) == {"call_construct": "method"}
    assert _facts(_one(go, "let")) == {"call_construct": "method", "stdlib_member_name": True}
    assert _facts(_one(go, "trim")) == {"call_construct": "method", "stdlib_member_name": True}
    assert _facts(_one(go, "repeat")) == {"stdlib_member_name": True}
    assert _facts(_one(go, "map")) == {
        "receiver_type_hint": "List", "call_construct": "method", "stdlib_member_name": True}


def test_the_screen_holds_the_scope_functions_and_none_of_okhttps_own_names() -> None:
    assert {"let", "run", "apply", "also", "with", "use", "map", "filter", "forEach",
            "trim", "equals", "hashCode", "toString"} <= _KOTLIN_LIBRARY_MEMBER_NAMES
    assert not {"newCall", "execute", "enqueue", "toHttpUrl", "writeUtf8"} & (
        _KOTLIN_LIBRARY_MEMBER_NAMES)


def test_a_callees_return_type_is_read_where_the_callee_is_declared(tmp_path: Path) -> None:
    """``val q = runner.newQueue()``: ``TaskQueue`` is the return type written
    in ``TaskRunner``'s file, which imports it; the caller's file does not,
    and Kotlin needs no import for an inferred type. The hint is then the
    qualified name. A caller that imports the type gets its own name for it."""
    result = _analyze(tmp_path, {
        "conc/TaskQueue.kt": "package conc\n\nclass TaskQueue {\n    fun schedule() {}\n}\n",
        "run/TaskRunner.kt": (
            "package run\n\nimport conc.TaskQueue\n\n"
            "class TaskRunner {\n    fun newQueue(): TaskQueue = TaskQueue()\n}\n"
            "fun runner(): TaskRunner = TaskRunner()\n"
        ),
        "cache/Cache.kt": (
            "package cache\n\nimport run.TaskRunner\nimport run.runner\n\n"
            "fun clean(r: TaskRunner) {\n    val q = r.newQueue()\n    q.schedule()\n}\n"
            "fun viaBare() {\n    val r = runner()\n    r.newQueue()\n}\n"
            "fun viaOwner() {\n    val q = Factory.make()\n    q.schedule()\n}\n"
        ),
        "cache/Factory.kt": (
            "package cache\n\nimport conc.TaskQueue\n\n"
            "object Factory {\n    fun make(): TaskQueue = TaskQueue()\n}\n"
        ),
        "conc/Use.kt": (
            "package conc\n\nimport run.TaskRunner\n\n"
            "fun use(r: TaskRunner) {\n    val q = r.newQueue()\n    q.schedule()\n}\n"
        ),
    })
    assert _facts(_one(_calls(result, "clean"), "schedule"))["receiver_type_hint"] == (
        "conc.TaskQueue")
    assert _facts(_one(_calls(result, "viaBare"), "newQueue"))["receiver_type_hint"] == (
        "TaskRunner")
    assert _facts(_one(_calls(result, "viaOwner"), "schedule"))["receiver_type_hint"] == (
        "conc.TaskQueue")
    assert _facts(_one(_calls(result, "use"), "schedule"))["receiver_type_hint"] == "TaskQueue"


def test_a_declared_type_is_not_replaced_by_the_callees_return_type(tmp_path: Path) -> None:
    """``val w: WebSocket = newWebSocket()`` where ``newWebSocket`` returns a
    ``RealWebSocket``: a variable's type is the type its declaration writes."""
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "interface WebSocket {\n    fun send()\n}\n"
        "class RealWebSocket : WebSocket {\n    override fun send() {}\n}\n"
        "fun newWebSocket(): RealWebSocket = RealWebSocket()\n"
        "fun go() {\n    val w: WebSocket = newWebSocket()\n    w.send()\n"
        "    val r = newWebSocket()\n    r.send()\n}\n"
    )})
    hints = sorted(_facts(e)["receiver_type_hint"] for e in _calls(result, "go")
                   if (e.meta or {}).get("callee_name") == "send")
    assert hints == ["RealWebSocket", "WebSocket"]


def test_a_call_drawn_from_the_file_anchor_types_through_the_files_package(
    tmp_path: Path,
) -> None:
    """A top-level ``val`` initialiser is drawn from the file anchor, which has
    no qualified name: the callee's owner is looked up in the file's package
    (and, with no package, among the global names)."""
    for package, rel in (("p", "p/"), (None, "")):
        header = f"package {package}\n\n" if package else ""
        root = tmp_path / (package or "nopkg")
        result = _analyze(root, {
            f"{rel}Maker.kt": header + (
                "class Product {\n    fun go() {}\n}\n"
                "class Maker {\n    fun make(): Product = Product()\n}\n"
            ),
            f"{rel}Use.kt": header + (
                "val maker = Maker()\nval made = maker.make()\n"
                "fun use() {\n    made.go()\n}\n"
            ),
        })
        assert _facts(_one(_calls(result, "use"), "go"))["receiver_type_hint"] == "Product"


def test_a_return_type_no_declaration_answers_types_nothing(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {"A.kt": (
        "package a\n\n"
        "fun make(): StringBuilder = StringBuilder()\n"
        "fun go() {\n    val s = make()\n    s.flip()\n}\n"
    )})
    assert _facts(_one(_calls(result, "go"), "flip")) == {"call_construct": "method"}


# ---------------------------------------------- through the shared pass

def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = _write(tmp_path / "repo", files)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _resolved(data: dict[str, Any], caller: str, callee: str) -> list[tuple[str, str]]:
    """(target's qualified name, mark) of each resolved call from ``caller``
    to a declaration whose short name is ``callee``."""
    nodes = {n["id"]: n for n in data["nodes"]}
    return sorted(
        (nodes[e["dst"]].get("qualified_name") or "", (e.get("meta") or {}).get("resolved_by"))
        for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved")
        and not (e.get("meta") or {}).get("superseded_by")
        and nodes.get(e["src"], {}).get("name") == caller
        and e["dst"] in nodes and nodes[e["dst"]]["name"].rsplit(".", 1)[-1] == callee
    )


_CONNS = {
    "model/Conn.kt": "package model\n\nclass Conn {\n    fun open() {}\n}\n",
    "legacy/Conn.kt": "package legacy\n\nclass Conn {\n    fun open() {}\n}\n",
}


def test_end_to_end_a_typed_receiver_reaches_its_imported_type_not_a_namesake(
    tmp_path: Path,
) -> None:
    data = _map(tmp_path, {**_CONNS, "app/Main.kt": (
        "package app\n\nimport model.Conn\n\n"
        "class Main {\n    fun run(c: Conn) {\n        c.open()\n    }\n}\n"
    )})
    assert _resolved(data, "Main.run", "open") == [("model.Conn.open", "receiver_type")]


def test_end_to_end_a_construction_of_an_imported_and_of_a_same_package_type(
    tmp_path: Path,
) -> None:
    """okhttp's case: ``Request(url)`` is ``okhttp3.Request``, not an interface
    ``Request`` nested in a test helper; ``Conn()`` in package ``legacy`` is
    ``legacy.Conn``."""
    data = _map(tmp_path, {
        **_CONNS,
        "okhttp3/Request.kt": "package okhttp3\n\nclass Request(val url: String)\n",
        "fake/FakeDns.kt": "package fake\n\ninterface Request\n",
        "legacy/Use.kt": (
            "package legacy\n\nimport okhttp3.Request\n\n"
            "class Use {\n    fun make() {\n        Request(\"u\")\n        Conn()\n    }\n}\n"
        ),
    })
    assert _resolved(data, "Use.make", "Request") == [("okhttp3.Request", "import")]
    assert _resolved(data, "Use.make", "Conn") == [("legacy.Conn", "same_module")]


def test_end_to_end_a_call_on_a_type_reaches_that_types_member(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "util/Util.kt": "package util\n\nobject Util {\n    fun helper(): Int = 1\n}\n",
        "other/Util.kt": "package other\n\nobject Util {\n    fun helper(): Int = 2\n}\n",
        "app/Main.kt": (
            "package app\n\nimport util.Util\n\n"
            "fun go(): Int = Util.helper()\n"
            "fun qualified(): Int = other.Util.helper()\n"
        ),
    })
    assert _resolved(data, "go", "helper") == [("util.Util.helper", "qualified")]
    assert _resolved(data, "qualified", "helper") == [("other.Util.helper", "qualified")]


def test_end_to_end_a_super_call_reaches_the_parent(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "base/Base.kt": "package base\n\nopen class Base {\n    open fun save() {}\n}\n",
        "decoy/Base.kt": "package decoy\n\nopen class Base {\n    open fun save() {}\n}\n",
        "app/Repo.kt": (
            "package app\n\nimport base.Base\n\n"
            "class Repo : Base() {\n    override fun save() {\n        super.save()\n    }\n}\n"
        ),
    })
    assert _resolved(data, "Repo.save", "save") == [("base.Base.save", "own_type")]


def test_end_to_end_an_untyped_receiver_binds_one_holder_and_never_a_library_name(
    tmp_path: Path,
) -> None:
    """The name step: ``x.drain()`` with ONE ``drain`` in the repository is
    bound by name; ``x.flush()`` with two (a namesake in another package) is
    tied; ``x.map { }`` is the standard library's whatever the repository
    declares."""
    data = _map(tmp_path, {
        "io/Pipe.kt": (
            "package io\n\nclass Pipe {\n    fun drain() {}\n    fun flush() {}\n"
            "    fun map(f: (Int) -> Int) {}\n}\n"
        ),
        "net/Sock.kt": "package net\n\nclass Sock {\n    fun flush() {}\n}\n",
        "app/Main.kt": (
            "package app\n\nfun obtain(): Any = 1\n\n"
            "fun go() {\n    val x = obtain()\n"
            "    x.drain()\n    x.flush()\n    x.map { it }\n}\n"
        ),
    })
    assert _resolved(data, "go", "drain") == [("io.Pipe.drain", "name")]
    assert _resolved(data, "go", "flush") == []
    assert _resolved(data, "go", "map") == []


def test_end_to_end_a_call_in_a_class_body_and_at_the_top_of_a_file(tmp_path: Path) -> None:
    """A property initialiser's caller is its class, which sees its own nested
    type; a top-level property's caller is the file, which is in the file's
    package. Neither is a namesake of another package."""
    data = _map(tmp_path, {
        "app/Widget.kt": "package app\n\nclass Widget {\n    private val log = Recorder()\n\n"
                         "    class Recorder\n}\n",
        "other/Recorder.kt": "package other\n\nclass Recorder\n",
        "app/Top.kt": "package app\n\nfun helper(): Int = 1\n\nval ready = helper()\n",
        "other/Helper.kt": "package other\n\nfun helper(): Int = 2\n",
    })
    assert _resolved(data, "Widget", "Recorder") == [("app.Widget.Recorder", "same_module")]
    assert _resolved(data, "app/Top.kt", "helper") == [("app.helper", "same_module")]


def test_end_to_end_a_parent_package_is_no_scope(tmp_path: Path) -> None:
    """Kotlin packages do not nest: ``Pool()`` written in ``net.http`` is not
    ``net.Pool`` without an import."""
    data = _map(tmp_path, {
        "net/Pool.kt": "package net\n\nclass Pool\n",
        "net/http/Client.kt": "package net.http\n\nfun open() {\n    Pool()\n}\n",
        "net/http/Server.kt": "package net.http\n\nimport net.Pool\n\nfun serve() {\n    Pool()\n}\n",
    })
    assert _resolved(data, "open", "Pool") == []
    assert _resolved(data, "serve", "Pool") == [("net.Pool", "import")]
