# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the C# analyzer binds itself, and what it hands the shared pass (ADR-0064).

The analyzer binds a call only on evidence C# itself uses and that it has in
its own file: the enclosing type's method (``own_type``) and a method of the
receiver's known type (``receiver_type``). Every other call is emitted
unresolved with the facts the ``inherited_calls`` linker reads, and the
linker decides from the caller's file's ``using`` table
(``AnalysisResult.import_scopes``) and the caller's namespaces:

* ``receiver_type_hint``: the receiver's type as written (a parameter, a
  ``new``, a field, the declared return type of a call this file resolves);
* ``qualifier``: a receiver that can name a type or namespace path and is no
  variable or member this file knows;
* ``enclosing_class``: a bare, ``this.`` or ``base.`` call, unless a local
  function or a local variable of that name is in scope.

The first half pins what the analyzer emits, route by route. The second half
runs the whole pipeline for the four cases the shared pass exists for.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.csharp import analyze_csharp


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _calls(tmp_path: Path, files: dict[str, str], caller: str) -> list[Edge]:
    """The ``calls`` edges the analyzer emits from the member named ``caller``."""
    result = analyze_csharp(_write(tmp_path, files))
    src = next(s.id for s in result.symbols if s.name == caller)
    return [e for e in result.edges if e.edge_type == "calls" and e.src == src]


def _one(edges: list[Edge], callee: str) -> Edge:
    found = [e for e in edges if (e.meta or {}).get("callee_name", e.dst.split(":")[-2]) == callee
             or e.dst.split(":")[-2].endswith("." + callee)]
    assert len(found) == 1, (callee, [(e.dst, e.meta) for e in edges])
    return found[0]


def _facts(edge: Edge) -> dict[str, Any]:
    meta = edge.meta or {}
    return {k: meta[k] for k in ("receiver_type_hint", "qualifier", "enclosing_class", "call_construct")
            if k in meta}


# --------------------------------------------------------------- the table

def test_every_file_hands_over_its_using_table(tmp_path: Path) -> None:
    result = analyze_csharp(_write(tmp_path, {
        "A.cs": (
            "using System;\n"
            "global using Acme.Text;\n"
            "using static System.Math;\n"
            "using Svc = Acme.Services;\n"
            "using Bare = Thing;\n"
            "using Ints = System.Collections.Generic.List<int>;\n"
            "using Root = global::Acme.Root;\n"
            "using Pair = (int, int);\n"
            "using global::Acme.Global;\n"
            "class A {}\n"
        ),
        "B.cs": "class B {}\n",
    }))
    paths = {s.name: s.path for s in result.symbols}
    assert set(result.import_scopes) == {paths["A"], paths["B"]}
    scope = result.import_scopes[paths["A"]]
    # A namespace using, global or not; ``using static`` names members, which
    # the table cannot say, so it records nothing.
    assert scope.wildcard_modules == ("System", "Acme.Text", "Acme.Global")
    named = {alias: (scope.named(alias).module, scope.named(alias).name)
             for alias in ("Svc", "Bare", "Ints", "Root", "Pair")}
    assert named == {
        "Svc": ("Acme", "Services"), "Bare": ("", "Thing"),
        "Ints": ("System.Collections.Generic", "List"), "Root": ("Acme", "Root"),
        "Pair": ("", "(int,int)"),
    }
    # A file with no ``using`` still hands over a table: an empty one.
    empty = result.import_scopes[paths["B"]]
    assert empty.wildcard_modules == () and empty.named("B") is None


# --------------------------------------------------- explicit receivers

def test_a_receiver_of_known_type_in_another_file_carries_the_type(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    void Run(Acme.Data.Conn<int> c) { c.Open(); }\n"
            "}\n"
        ),
    }, "Main.Run")
    stub = _one(edges, "Open")
    assert not stub.is_resolved
    assert _facts(stub) == {"receiver_type_hint": "Acme.Data.Conn", "call_construct": "method"}


def test_a_receiver_of_known_type_in_this_file_is_bound_here(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Conn { public void Open() {} }\n"
            "class Main {\n"
            "    void Run(Conn c) { c.Open(); new Conn().Open(); }\n"
            "}\n"
        ),
    }, "Main.Run")
    bound = [e for e in edges if e.is_resolved]
    assert len(bound) == 2
    assert {(e.meta or {}).get("resolved_by") for e in bound} == {"receiver_type"}


def test_a_constructed_receiver_carries_its_type(tmp_path: Path) -> None:
    stub = _one(_calls(tmp_path, {
        "Main.cs": "class Main { void Run() { new Builder().Build(); } }\n",
    }, "Main.Run"), "Build")
    assert _facts(stub) == {"receiver_type_hint": "Builder", "call_construct": "method"}


def test_a_call_result_is_typed_by_the_declared_return_of_a_call_this_file_binds(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    static Conn GetDb() { return null; }\n"
            "    static int Count() { return 0; }\n"
            "    void Chain() { GetDb().Execute(); }\n"
            "    void Var() { var db = GetDb(); db.Execute(); Count().ToString(); Missing().Go(); Make<int>().Go(); }\n"
            "}\n"
        ),
    }, "Main.Var") + _calls(tmp_path / "again", {
        "Main.cs": (
            "class Main {\n"
            "    static Conn GetDb() { return null; }\n"
            "    void Chain() { GetDb().Execute(); }\n"
            "}\n"
        ),
    }, "Main.Chain")
    hints = [((e.meta or {}).get("callee_name"), _facts(e)) for e in edges if not e.is_resolved]
    # ``var db = GetDb()`` and ``GetDb().Execute()``: GetDb is this file's,
    # and the type it returns is carried, wherever Conn is declared.
    assert hints.count(("Execute", {"receiver_type_hint": "Conn", "call_construct": "method"})) == 2
    # ``int`` names no type a hint can carry; a call nothing binds types nothing.
    assert ("ToString", {"call_construct": "method"}) in hints
    assert hints.count(("Go", {"call_construct": "method"})) == 2


def test_a_receiver_that_can_name_a_type_is_handed_over_as_written(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Other { public static void Helper() {} }\n"
            "class Main {\n"
            "    void Run() {\n"
            "        Util.Helper();\n"
            "        Ns.Util.Assist();\n"
            "        List<int>.Create();\n"
            "        Other.Helper();\n"
            "        string.Join(\",\", null);\n"
            "        Outer.Gen<int>.Make();\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run")
    assert all(not e.is_resolved for e in edges)
    # ``Other`` is a type of this file: still the shared pass's to bind, by
    # the namespace walk, not by the file's table of short names. ``string``
    # is a keyword: no type of the repository.
    assert sorted(((e.meta or {})["callee_name"], _facts(e).get("qualifier", "-")) for e in edges) == [
        ("Assist", "Ns.Util"), ("Create", "List"), ("Helper", "Other"), ("Helper", "Util"), ("Join", "-"),
        ("Make", "-"),
    ]
    assert {_facts(e)["call_construct"] for e in edges} == {"method"}


def test_a_variable_or_member_is_no_qualifier(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    Conn Prop { get; }\n"
            "    void Run(Conn typed) {\n"
            "        var made = Factory.Make();\n"
            "        made.Go();\n"
            "        typed.Inner.Go();\n"
            "        Prop.Go();\n"
            "        Items.Select(x => x.Go());\n"
            "        foreach (var item in made) { item.Go(); }\n"
            "        this.Prop.Go();\n"
            "        made[0].Go();\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run")
    goes = [e for e in edges if (e.meta or {}).get("callee_name") == "Go"]
    assert len(goes) == 7
    assert all(_facts(e) == {"call_construct": "method"} for e in goes), [_facts(e) for e in goes]


def test_a_generic_method_is_called_by_its_name(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": "class Main { void Run(Conn c) { c.Get<int>(); c?.Skip(); } }\n",
    }, "Main.Run")
    assert [(e.meta or {}).get("callee_name") for e in edges] == ["Get"]
    assert _facts(edges[0]) == {"receiver_type_hint": "Conn", "call_construct": "method"}


def test_a_field_chain_whose_method_is_elsewhere_carries_the_chain_type(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Inner.cs": "public class Inner { public Db _db; }\n",
        "Outer.cs": (
            "public class Outer {\n"
            "    private Inner _inner;\n"
            "    private Cache _cache;\n"
            "    public void Run() { this._inner._db.Save(); this._cache.Save(); }\n"
            "}\n"
        ),
    }, "Outer.Run")
    assert sorted(_facts(e)["receiver_type_hint"] for e in edges) == ["Cache", "Db"]
    assert not any(e.is_resolved for e in edges)


# ------------------------------------------------------------ bare calls

def test_a_bare_call_binds_only_the_enclosing_types_own_method(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Other { public void Elsewhere() {} }\n"
            "class Main : Base {\n"
            "    void Own() {}\n"
            "    void Run() { Own(); this.Own(); Elsewhere(); this.Inherited(); base.Up(); }\n"
            "}\n"
        ),
    }, "Main.Run")
    own = [e for e in edges if e.is_resolved]
    assert len(own) == 2 and {(e.meta or {})["resolved_by"] for e in own} == {"own_type"}
    stubs = {(e.meta or {})["callee_name"]: _facts(e) for e in edges if not e.is_resolved}
    # ``Elsewhere`` is Other's: not this type's, so the shared pass walks
    # Main's ancestors, never the file's table of short names. ``base.Up()``
    # with no ``Up`` of Main's own: the walk from Main is the walk from Base.
    assert stubs == {
        "Elsewhere": {"enclosing_class": "Main"}, "Inherited": {"enclosing_class": "Main"},
        "Up": {"enclosing_class": "Main"},
    }


def test_base_call_from_its_own_override_is_not_the_override(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Main : Base {\n"
            "    override Conn Twice() { base.Twice(); base.Twice().Go(); return null; }\n"
            "}\n"
        ),
    }, "Main.Twice")
    # The own ``Twice`` is the caller itself; ``base.Twice()`` is Base's. No
    # fact says "start at the parent": unresolved, and no type for its result.
    assert not any(e.is_resolved for e in edges)
    assert sorted(((e.meta or {})["callee_name"], tuple(_facts(e).items())) for e in edges) == [
        ("Go", (("call_construct", "method"),)), ("Twice", ()), ("Twice", ()),
    ]


def test_a_local_function_or_variable_hides_every_member(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    void Helper() {}\n"
            "    void Run(System.Action callback) {\n"
            "        Helper();\n"
            "        callback();\n"
            "        void Helper() {}\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run")
    assert not any(e.is_resolved for e in edges)
    assert all(_facts(e) == {} for e in edges), [_facts(e) for e in edges]


def test_a_top_level_statement_call_has_no_enclosing_type(tmp_path: Path) -> None:
    result = analyze_csharp(_write(tmp_path, {
        "Program.cs": "Run();\nstatic void Run() { Go(); }\nclass Keep {}\n",
    }))
    stubs = [e for e in result.edges if e.edge_type == "calls"]
    assert stubs and all("enclosing_class" not in (e.meta or {}) for e in stubs)


# ---------------------------------------------------------- new T(), R12

def test_new_of_a_type_with_a_namesake_found_by_name_alone_is_not_bound(tmp_path: Path) -> None:
    result = analyze_csharp(_write(tmp_path, {
        "A.cs": "namespace A { public class Shared {} public class Only {} }\n",
        "B.cs": "namespace B { public class Shared {} }\n",
        "Main.cs": "class Main { void Run() { new Shared(); new Only(); } }\n",
    }))
    names = {s.id: s.name for s in result.symbols}
    made = [names.get(e.dst) for e in result.edges if e.edge_type == "instantiates"]
    assert made == ["Only"]


# ---------------------------------------------- through the shared pass

def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = _write(tmp_path / "repo", files)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _resolved(data: dict[str, Any], caller: str, callee: str) -> list[tuple[str, str]]:
    """(target's qualified name, mark) of each resolved call from ``caller``
    to a method named ``callee``."""
    nodes = {n["id"]: n for n in data["nodes"]}
    return sorted(
        (nodes[e["dst"]].get("qualified_name") or "", (e.get("meta") or {}).get("resolved_by"))
        for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved")
        and not (e.get("meta") or {}).get("superseded_by")
        and nodes.get(e["src"], {}).get("name") == caller
        and e["dst"] in nodes and nodes[e["dst"]]["name"].endswith("." + callee)
    )


def test_end_to_end_a_typed_receiver_reaches_its_type_not_a_namesake(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "Data/Conn.cs": "namespace Data { public class Conn { public void Open() {} } }\n",
        "Legacy/Conn.cs": "namespace Legacy { public class Conn { public void Open() {} } }\n",
        "App/Main.cs": (
            "using Data;\n"
            "namespace App { public class Main { public void Run(Conn c) { c.Open(); } } }\n"
        ),
    })
    assert _resolved(data, "Main.Run", "Open") == [("Data.Conn.Open", "receiver_type")]


def test_end_to_end_a_static_call_through_a_using(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "Tools/Util.cs": "namespace Tools { public static class Util { public static void Help() {} } }\n",
        "Decoy/Util.cs": "namespace Decoy { public static class Util { public static void Help() {} } }\n",
        "App/Main.cs": (
            "using Tools;\n"
            "namespace App { public class Main { public void Run() { Util.Help(); } } }\n"
        ),
    })
    assert _resolved(data, "Main.Run", "Help") == [("Tools.Util.Help", "qualified")]


def test_end_to_end_a_bare_call_to_an_inherited_method(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "App/Base.cs": "namespace App { public class Base { public void Ping() {} } }\n",
        "App/Stray.cs": "namespace App { public class Stray { public void Ping() {} } }\n",
        "App/Child.cs": "namespace App { public class Child : Base { public void Run() { Ping(); } } }\n",
    })
    assert _resolved(data, "Child.Run", "Ping") == [("App.Base.Ping", "own_type")]


def test_end_to_end_an_untyped_local_stays_unresolved(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "App/Store.cs": "namespace App { public class Store { public void Execute() {} } }\n",
        "App/Other.cs": "namespace App { public class Other { public void Execute() {} } }\n",
        "App/Main.cs": (
            "namespace App { public class Main {\n"
            "    public void Run() { var x = Factory.Make(); x.Execute(); }\n"
            "} }\n"
        ),
    })
    assert _resolved(data, "Main.Run", "Execute") == []
