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
    # A file with no ``using`` still hands over a table. B is, like A, in no
    # project, so A's ``global using`` is in scope in it, and nothing else.
    other = result.import_scopes[paths["B"]]
    assert other.wildcard_modules == ("Acme.Text",) and other.named("Svc") is None


# ------------------------------------- the usings in scope in a file

def _scopes(tmp_path: Path, files: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Each file's table, by its path relative to the tree, and the run's warnings."""
    result = analyze_csharp(_write(tmp_path, files))
    return ({str(Path(k).relative_to(tmp_path)): v for k, v in result.import_scopes.items()},
            result.run.warnings)


def test_a_global_using_reaches_every_file_of_its_project_and_no_other(tmp_path: Path) -> None:
    scopes, _ = _scopes(tmp_path, {
        "App/App.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
        "App/GlobalUsings.cs": "global using App.Rendering;\nglobal using R = App.Rendering.Renderer;\n",
        "App/Parsing/Token.cs": "using System;\nnamespace App.Parsing { class Token {} }\n",
        "Lib/Lib.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
        "Lib/Thing.cs": "namespace Lib { class Thing {} }\n",
    })
    token = scopes["App/Parsing/Token.cs"]
    # The file's own usings first, then its project's.
    assert token.wildcard_modules == ("System", "App.Rendering")
    assert (token.named("R").module, token.named("R").name) == ("App.Rendering", "Renderer")
    other = scopes["Lib/Thing.cs"]
    assert other.wildcard_modules == () and other.named("R") is None


def test_a_file_belongs_to_the_nearest_project_above_it(tmp_path: Path) -> None:
    scopes, _ = _scopes(tmp_path, {
        "Outer/Outer.csproj": "<Project><ItemGroup><Using Include=\"Outer.Ns\" /></ItemGroup></Project>\n",
        "Outer/O.cs": "global using OuterGlobal;\nclass O {}\n",
        "Outer/Inner/Inner.csproj": "<Project><ItemGroup><Using Include=\"Inner.Ns\" /></ItemGroup></Project>\n",
        "Outer/Inner/Deep/I.cs": "class I {}\n",
    })
    assert scopes["Outer/Inner/Deep/I.cs"].wildcard_modules == ("Inner.Ns",)
    assert scopes["Outer/O.cs"].wildcard_modules == ("OuterGlobal", "Outer.Ns")


def test_files_in_no_project_share_their_global_usings(tmp_path: Path) -> None:
    scopes, _ = _scopes(tmp_path, {
        "Assets/Scripts/A.cs": "global using Game.Core;\nclass A {}\n",
        "Assets/Other/B.cs": "class B {}\n",
        "Tool/Tool.csproj": "<Project />\n",
        "Tool/T.cs": "class T {}\n",
    })
    assert scopes["Assets/Other/B.cs"].wildcard_modules == ("Game.Core",)
    assert scopes["Tool/T.cs"].wildcard_modules == ()


def test_msbuild_usings_of_the_project_file_and_every_directory_build_file_above(tmp_path: Path) -> None:
    scopes, _ = _scopes(tmp_path, {
        # Two directories above the project: read, its Remove too.
        "Directory.Build.props": (
            "<Project><ItemGroup>\n"
            "  <Using Include=\"Far.Props\" />\n  <Using Include=\"Dropped.ByProject\" />\n"
            "</ItemGroup></Project>\n"
        ),
        "src/Directory.Build.targets": (
            "<Project><ItemGroup Condition=\"'$(IncludeMore)' == 'true'\">\n"
            "  <Using Include=\"Late.Targets\" />\n  <Using Remove=\"Dropped.ByTargets\" />\n"
            "</ItemGroup></Project>\n"
        ),
        "src/Proj/Proj.csproj": (
            "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
            "  <PropertyGroup><ImplicitUsings>enable</ImplicitUsings></PropertyGroup>\n"
            "  <ItemGroup>\n"
            "    <Using Include=\"Proj.Utils;Proj.Extra\" />\n"
            "    <Using Include=\"Proj.Data.Store\" Alias=\"Db\" />\n"
            "    <Using Include=\"Proj.Math.Helpers\" Static=\"true\" />\n"
            "    <Using Include=\"$(RootNamespace).Hidden\" />\n"
            "    <Using Include=\"Dropped.ByTargets\" />\n"
            "    <Using Remove=\"Dropped.ByProject\" />\n"
            "  </ItemGroup>\n"
            "</Project>\n"
        ),
        "src/Proj/Code/C.cs": "class C {}\n",
    })
    scope = scopes["src/Proj/Code/C.cs"]
    # props, then the project file, then targets; a Condition is not
    # evaluated (the using is in scope in some build); a static using, an
    # MSBuild property and the SDK's implicit System usings are not recorded.
    assert scope.wildcard_modules == ("Far.Props", "Proj.Utils", "Proj.Extra", "Late.Targets")
    assert (scope.named("Db").module, scope.named("Db").name) == ("Proj.Data", "Store")


def test_an_old_style_project_file_in_the_msbuild_namespace_is_read(tmp_path: Path) -> None:
    scopes, _ = _scopes(tmp_path, {
        "P/P.csproj": (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<Project xmlns=\"http://schemas.microsoft.com/developer/msbuild/2003\">\n"
            "  <ItemGroup><Using Include=\"Old.Style\" /></ItemGroup>\n</Project>\n"
        ),
        "P/C.cs": "class C {}\n",
    })
    assert scopes["P/C.cs"].wildcard_modules == ("Old.Style",)


def test_two_project_files_in_one_directory_are_one_project(tmp_path: Path) -> None:
    scopes, _ = _scopes(tmp_path, {
        "P/A.csproj": "<Project><ItemGroup><Using Include=\"From.A\" /></ItemGroup></Project>\n",
        "P/B.csproj": "<Project><ItemGroup><Using Include=\"From.B\" /></ItemGroup></Project>\n",
        "P/C.cs": "class C {}\n",
    })
    assert scopes["P/C.cs"].wildcard_modules == ("From.A", "From.B")


def test_a_project_file_that_is_not_xml_is_skipped_and_named(tmp_path: Path) -> None:
    scopes, warnings = _scopes(tmp_path, {
        "Directory.Build.props": "<Project><ItemGroup><Using Include=\"Still.Read\" /></ItemGroup></Project>\n",
        "P/P.csproj": "<Project><ItemGroup><Using Include=\"Never.Read\"",
        "P/C.cs": "class C {}\n",
    })
    assert scopes["P/C.cs"].wildcard_modules == ("Still.Read",)
    assert warnings == [
        "csharp: 1 MSBuild file(s) are not XML; their <Using> items were not read: P/P.csproj"
    ]


# ---------------------------------------------------------- type arity

def test_every_type_symbol_says_how_many_type_parameters_it_takes(tmp_path: Path) -> None:
    """``type_arity`` on every type symbol, 0 written, never omitted: an
    absent key would mean "the producer does not say"."""
    result = analyze_csharp(_write(tmp_path, {
        "Types.cs": (
            "namespace App {\n"
            "    [Serializable] public class Policy : Base { }\n"
            "    public class Policy<TResult> { }\n"
            "    public partial class Map<K, V> where K : class { }\n"
            "    public partial class Map<K, V> : IMap { }\n"
            "    interface IMap { }\n"
            "    interface IStore<T> : IMap { }\n"
            "    struct Cell { }\n"
            "    struct Pair<A, B> { }\n"
            "    record Point(int X);\n"
            "    record struct Box<T>(T Value);\n"
            "    enum Color { Red }\n"
            "    class Outer<T> { class Inner { } }\n"
            "}\n"
        ),
    }))
    types = {"class", "record", "interface", "struct", "enum"}
    arities = sorted((s.name, s.kind, (s.meta or {}).get("type_arity"))
                     for s in result.symbols if s.kind in types)
    assert arities == [
        ("Box", "record", 1), ("Cell", "struct", 0), ("Color", "enum", 0),
        ("IMap", "interface", 0), ("IStore", "interface", 1), ("Inner", "class", 0),
        ("Map", "class", 2), ("Map", "class", 2), ("Outer", "class", 1),
        ("Pair", "struct", 2), ("Point", "record", 0), ("Policy", "class", 0),
        ("Policy", "class", 1),
    ]
    # The other meta a type carried is still there.
    plain = next(s for s in result.symbols if s.name == "Policy" and s.meta["type_arity"] == 0)
    assert plain.meta["base_classes"] == ["Base"] and plain.meta["annotations"]


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


def test_end_to_end_a_global_using_in_another_file_of_the_project(tmp_path: Path) -> None:
    """Serilog's shape: ``global using Serilog.Rendering;`` in GlobalUsings.cs,
    the call in ``Serilog.Parsing`` with no using of its own. The namesake is
    in a namespace that another project's global using brings in: it is not
    in scope here, and a bind by name would reach it."""
    data = _map(tmp_path, {
        "src/Serilog/Serilog.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
        "src/Serilog/GlobalUsings.cs": "global using Serilog.Rendering;\n",
        "src/Serilog/Rendering/MessageTemplateRenderer.cs": (
            "namespace Serilog.Rendering;\n"
            "static class MessageTemplateRenderer { public static void RenderTextToken(object t) {} }\n"
            "class Casing { public void Format(object v) {} }\n"
        ),
        "src/Serilog/Parsing/TextToken.cs": (
            "namespace Serilog.Parsing;\n"
            "class TextToken {\n"
            "    public void Render(Casing casing) {\n"
            "        MessageTemplateRenderer.RenderTextToken(this);\n"
            "        casing.Format(this);\n"
            "    }\n"
            "    public void Made() {\n"
            "        var casing = new Casing();\n"
            "        casing.Format(this);\n"
            "    }\n"
            "}\n"
        ),
        "test/Other/Other.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
        "test/Other/GlobalUsings.cs": "global using Decoy.Rendering;\n",
        "test/Other/Decoy.cs": (
            "namespace Decoy.Rendering;\n"
            "static class MessageTemplateRenderer { public static void RenderTextToken(object t) {} }\n"
            "class Casing { public void Format(object v) {} }\n"
        ),
    })
    assert _resolved(data, "TextToken.Render", "RenderTextToken") == [
        ("Serilog.Rendering.MessageTemplateRenderer.RenderTextToken", "qualified")]
    assert _resolved(data, "TextToken.Render", "Format") == [
        ("Serilog.Rendering.Casing.Format", "receiver_type")]
    # The receiver made by ``new Casing()``: the shared pass binds the type in
    # scope. The second edge is WRONG, and pinned here so that it is seen:
    # ``new T()`` found by name alone (R12) binds the namesake class, and the
    # method-call-recovery linker takes that class as the receiver's (its
    # name agrees with the receiver_type_hint, so its step 3a keeps it).
    assert _resolved(data, "TextToken.Made", "Format") == [
        ("Decoy.Rendering.Casing.Format", "class_hint"),
        ("Serilog.Rendering.Casing.Format", "receiver_type"),
    ]


def test_end_to_end_an_msbuild_using_of_the_project_and_of_a_conditional_targets_file(
    tmp_path: Path,
) -> None:
    """Polly's shapes: ``<Using Include="Polly.Utils" />`` in Polly.Core.csproj,
    and ``<Using Include="Polly.Timeout" />`` in a Directory.Build.targets two
    directories up, inside a conditional ItemGroup. Each callee has a namesake
    in a namespace no using of the caller's project names."""
    data = _map(tmp_path, {
        "Directory.Build.targets": (
            "<Project>\n"
            "  <ItemGroup Condition=\"'$(IncludePollyUsings)' == 'true'\">\n"
            "    <Using Include=\"Polly.Timeout\" />\n"
            "  </ItemGroup>\n"
            "</Project>\n"
        ),
        "src/Polly.Core/Polly.Core.csproj": (
            "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
            "  <ItemGroup><Using Include=\"Polly.Utils\" /></ItemGroup>\n"
            "</Project>\n"
        ),
        "src/Polly.Core/Utils/Guard.cs": (
            "namespace Polly.Utils;\n"
            "static class Guard { public static void NotNull(object v) {} }\n"
        ),
        "src/Polly.Core/Timeout/TimeoutValidator.cs": (
            "namespace Polly.Timeout;\n"
            "static class TimeoutValidator { public static void ValidateSecondsTimeout(int s) {} }\n"
        ),
        "src/Polly.Core/Outcome.cs": (
            "namespace Polly;\n"
            "class Outcome {\n"
            "    public void Check(object e) {\n"
            "        Guard.NotNull(e);\n"
            "        TimeoutValidator.ValidateSecondsTimeout(1);\n"
            "    }\n"
            "}\n"
        ),
        "src/Polly.Testing/Polly.Testing.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
        "src/Polly.Testing/Decoys.cs": (
            "namespace Polly.Testing.Utils {\n"
            "    static class Guard { public static void NotNull(object v) {} }\n"
            "    static class TimeoutValidator { public static void ValidateSecondsTimeout(int s) {} }\n"
            "}\n"
        ),
    })
    assert _resolved(data, "Outcome.Check", "NotNull") == [("Polly.Utils.Guard.NotNull", "qualified")]
    assert _resolved(data, "Outcome.Check", "ValidateSecondsTimeout") == [
        ("Polly.Timeout.TimeoutValidator.ValidateSecondsTimeout", "qualified")]
