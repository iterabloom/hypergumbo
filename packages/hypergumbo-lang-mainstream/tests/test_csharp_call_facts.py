# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the C# analyzer binds itself, and what it hands the shared pass (ADR-0064).

The analyzer binds a call only on evidence C# itself uses and that it has in
its own file: the enclosing type's method (``own_type``) and a method of the
receiver's known type (``receiver_type``). Every other call is emitted
unresolved with the facts the ``inherited_calls`` linker reads, and the
linker decides from the caller's file's ``using`` table
(``AnalysisResult.import_scopes``) and the caller's namespaces:

* ``receiver_type_hint``: the receiver's type as written, type arguments
  kept (a parameter, a ``new``, a field, the declared return type of a call
  this file resolves);
* ``qualifier``: a receiver that can name a type or namespace path and is no
  variable or member this file knows, type arguments kept;
* ``enclosing_class``: a bare or ``this.`` call, unless a local function or
  a local variable of that name is in scope; a ``base.`` call, always, with
  ``super_receiver`` (the walk starts at the parent).

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


# ------------------------------------------------------ extension methods

def test_an_extension_method_records_its_receivers_type_as_written(tmp_path: Path) -> None:
    result = analyze_csharp(_write(tmp_path, {
        "Ext.cs": (
            "static class Ext {\n"
            "    [Pure] public static void Open(this IDbConnection cnn, int timeout) {}\n"
            "    public static void Fill<T>(this ref Span<T> span) {}\n"
            "    public static void Name(this Acme.Data.Conn? conn) {}\n"
            "    public static void Plain(IDbConnection cnn) {}\n"
            "    public static void None() {}\n"
            "    public void Instance(int x, IDbConnection cnn) {}\n"
            "}\n"
        ),
    }))
    receivers = {s.name: (s.meta or {}).get("extension_receiver")
                  for s in result.symbols if s.kind == "method"}
    assert receivers == {
        "Ext.Open": "IDbConnection", "Ext.Fill": "Span<T>", "Ext.Name": "Acme.Data.Conn?",
        "Ext.Plain": None, "Ext.None": None, "Ext.Instance": None,
    }
    opened = next(s for s in result.symbols if s.name == "Ext.Open")
    assert opened.meta["annotations"]


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
    # As written, type arguments kept: ``Conn`` and ``Conn<T>`` are two types.
    assert _facts(stub) == {"receiver_type_hint": "Acme.Data.Conn<int>", "call_construct": "method"}


def test_a_hint_keeps_the_type_arguments_the_source_wrote(tmp_path: Path) -> None:
    """A parameter, a field, a field chain, a ``new``: the type as written,
    type arguments kept on the named type, those of an enclosing type and a
    leading ``global::`` dropped, a nullable's ``?`` dropped. An array is no
    type a hint carries: ``cells.C()`` calls a method of ``Cell<int>[]``,
    not of ``Cell<int>``."""
    edges = _calls(tmp_path, {
        "Inner.cs": "public class Inner<T> { public Db<T> _db; }\n",
        "Main.cs": (
            "class Main {\n"
            "    private Box<int> _box;\n"
            "    private Inner<string> _inner;\n"
            "    void Run(Policy<TResult> generic, Policy plain, Outer<int>.Nested nested,\n"
            "             global::Acme.Map<string, List<int>> map, Cell<int>[] cells, Cell<long>? maybe) {\n"
            "        generic.Execute(); plain.Execute(); nested.A(); map.B(); cells.C(); maybe.D();\n"
            "        _box.E(); this._box.F(); this._inner._db.Save(); new Cache<int>().G();\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run")
    assert sorted(((e.meta or {})["callee_name"], _facts(e).get("receiver_type_hint")) for e in edges
                  if (e.meta or {})["callee_name"] != "C") == [
        ("A", "Outer.Nested"), ("B", "Acme.Map<string,List<int>>"),
        ("D", "Cell<long>"), ("E", "Box<int>"), ("Execute", "Policy"), ("Execute", "Policy<TResult>"),
        ("F", "Box<int>"), ("G", "Cache<int>"), ("Save", "Db<T>"),
    ]
    assert _facts(_one(edges, "C")) == {"call_construct": "method"}
    assert not any(e.is_resolved for e in edges)


def test_a_type_of_this_file_binds_only_with_the_arity_written(tmp_path: Path) -> None:
    """The file's table is keyed by the bare name (``Box.Get``); the call is
    bound there only when the file's type of that name takes as many type
    parameters as the receiver's type is written with."""
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Box<T> { public void Get() {} }\n"
            "class Policy { public void Execute() {} }\n"
            "class Main {\n"
            "    void Run(Box<int> box, Policy<int> generic, Policy plain) {\n"
            "        box.Get(); new Box<string>().Get(); generic.Execute(); plain.Execute();\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run")
    bound = sorted((e.dst.split(":")[-2], (e.meta or {})["resolved_by"]) for e in edges if e.is_resolved)
    assert bound == [("Box.Get", "receiver_type"), ("Box.Get", "receiver_type"),
                     ("Policy.Execute", "receiver_type")]
    (stub,) = [e for e in edges if not e.is_resolved]
    assert _facts(stub) == {"receiver_type_hint": "Policy<int>", "call_construct": "method"}


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
    # is a keyword: no type of the repository. Type arguments as written.
    assert sorted(((e.meta or {})["callee_name"], _facts(e).get("qualifier", "-")) for e in edges) == [
        ("Assist", "Ns.Util"), ("Create", "List<int>"), ("Helper", "Other"), ("Helper", "Util"), ("Join", "-"),
        ("Make", "Outer.Gen<int>"),
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


# ------------------------------------------ a variable's declared type

def _hints(edges: list[Edge], skip: tuple[str, ...] = ()) -> dict[str, Any]:
    """callee name -> the stub's ``receiver_type_hint`` (None: no hint), one
    call per callee name, the names in ``skip`` left out."""
    stubs = [e for e in edges if not e.is_resolved and (e.meta or {}).get("callee_name") not in skip]
    names = [(e.meta or {}).get("callee_name") for e in stubs]
    assert len(names) == len(set(names)), names
    return {(e.meta or {}).get("callee_name"): _facts(e).get("receiver_type_hint") for e in stubs}


def test_every_declaration_that_writes_a_type_types_its_variable(tmp_path: Path) -> None:
    """THE RULE: a variable's type is the type its declaration writes, as
    written with its type arguments, ``?`` and ``ref`` removed: a local (with
    or without a value, ``new()``, several declarators), ``using``,
    ``using (...)``, ``for``, ``foreach``, ``out``, a declaration pattern, a
    recursive pattern, a ``case`` label, a switch arm, ``catch``, a lambda's
    and a local function's explicitly typed parameters."""
    hints = _hints(_calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    void Run(object e, Foo[] xs) {\n"
            "        Foo<int> a = new(), b;\n"
            "        a.A(); b.B();\n"
            "        Acme.Conn<int>? c = null; c.C();\n"
            "        global::Acme.Db d = Open(); d.D();\n"
            "        ref Foo r = ref xs[0]; r.R();\n"
            "        using Foo u = Open(); u.U();\n"
            "        using (Foo s = Open()) { s.S(); }\n"
            "        for (Foo f = null; ;) { f.F(); }\n"
            "        foreach (Foo j in xs) { j.J(); }\n"
            "        Take(out Foo o); o.O();\n"
            "        if (e is Foo k) { k.K(); }\n"
            "        if (e is Foo { Size: 1 } p) { p.P(); }\n"
            "        switch (e) { case Foo l: l.L(); break; }\n"
            "        var n = e switch { Foo w => w.W(), _ => 0 };\n"
            "        try { } catch (Oops ex) { ex.X(); }\n"
            "        Each((Foo m) => m.M());\n"
            "        void Local(Foo q) { q.Q(); }\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run"), skip=("Open", "Take", "Each"))
    assert hints == {
        "A": "Foo<int>", "B": "Foo<int>", "C": "Acme.Conn<int>", "D": "Acme.Db", "R": "Foo",
        "U": "Foo", "S": "Foo", "F": "Foo", "J": "Foo", "O": "Foo", "K": "Foo", "P": "Foo",
        "L": "Foo", "W": "Foo", "X": "Oops", "M": "Foo", "Q": "Foo",
    }


def test_a_declaration_of_no_type_a_hint_can_carry_types_nothing(tmp_path: Path) -> None:
    """``var`` from a call, ``dynamic``, an array, a tuple, a pointer, ``var
    (a, b)``, a lambda's implicit parameter, a ``params`` array: no hint, and
    no qualifier (each is a variable). A predefined type is kept: a
    repository declares extension methods on ``string``, and the
    receiver-type-dispatch linker matches that spelling."""
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    unsafe void Run(params Foo[] ps) {\n"
            "        var v = Factory.Make(); v.V();\n"
            "        dynamic d = Factory.Make(); d.D();\n"
            "        Foo[] arr = null; arr.A();\n"
            "        (Foo, Bar) t = default; t.T();\n"
            "        Foo* ptr = null; ptr.P();\n"
            "        var (left, right) = Factory.Pair(); left.L();\n"
            "        Each(x => x.X());\n"
            "        ps.S();\n"
            "        string text = null; text.Shout();\n"
            "        int? count = null; count.Twice();\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run")
    facts = {(e.meta or {}).get("callee_name"): _facts(e) for e in edges}
    for name in ("V", "D", "A", "T", "P", "L", "X", "S"):
        assert facts[name] == {"call_construct": "method"}, (name, facts[name])
    assert facts["Shout"]["receiver_type_hint"] == "string"
    assert facts["Twice"]["receiver_type_hint"] == "int"


def test_var_is_typed_by_an_initializer_that_writes_its_type(tmp_path: Path) -> None:
    """``var x = new Foo<int>()``, ``new A.B()``, ``(T)e``, ``(T?)e``, ``e as
    T``: the type the initializer writes. Not a call's result (a call this
    file does not bind types nothing)."""
    hints = _hints(_calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    void Run(object e) {\n"
            "        var g = new Policy<int>(); g.G();\n"
            "        var q = new Acme.Conn(); q.Q();\n"
            "        var c = (Cache)e; c.C();\n"
            "        var n = (TypeDeserializerCache?)e; n.N();\n"
            "        var a = e as Acme.Db<string>; a.A();\n"
            "        var k = Factory.Make(); k.K();\n"
            "        var arr = new Foo[3]; arr.R();\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run"))
    assert {k: v for k, v in hints.items() if k != "Make"} == {
        "G": "Policy<int>", "Q": "Acme.Conn", "C": "Cache", "N": "TypeDeserializerCache",
        "A": "Acme.Db<string>", "K": None, "R": None,
    }


def test_the_declared_type_wins_over_the_value_assigned(tmp_path: Path) -> None:
    """``IConn c = new Conn()``: C# looks the method up on ``IConn``. Same for
    a call this file binds: the declaration's type, not the callee's return
    type."""
    hints = _hints(_calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    static Conn GetDb() { return null; }\n"
            "    void Run() {\n"
            "        IConn c = new Conn(); c.Open();\n"
            "        IStore s = GetDb(); s.Save();\n"
            "    }\n"
            "}\n"
        ),
    }, "Main.Run"))
    assert hints["Open"] == "IConn" and hints["Save"] == "IStore"


def test_a_declaration_in_a_block_or_statement_does_not_leak_out_of_it(tmp_path: Path) -> None:
    """A field ``Bar x``; a local ``Foo x`` in a block, a ``foreach``, a
    ``using (...)``, a ``catch``, a ``for``, a ``case`` section, a lambda:
    after each, ``x`` is the field again."""
    hints = _hints(_calls(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    Bar x;\n"
            "    void Run(Foo[] xs, object e) {\n"
            "        { Foo x = null; x.InBlock(); }\n"
            "        x.AfterBlock();\n"
            "        foreach (Foo x in xs) { }\n"
            "        x.AfterForeach();\n"
            "        using (Foo x = null) { }\n"
            "        x.AfterUsing();\n"
            "        try { } catch (Foo x) { }\n"
            "        x.AfterCatch();\n"
            "        for (Foo x = null; ;) { break; }\n"
            "        x.AfterFor();\n"
            "        switch (e) { case Foo x: break; }\n"
            "        x.AfterCase();\n"
            "        Each((Foo x) => 0);\n"
            "        x.AfterLambda();\n"
            "    }\n"
            "    void Other() { x.InOther(); }\n"
            "}\n"
        ),
    }, "Main.Run") + _calls(tmp_path / "other", {
        "Main.cs": (
            "class Main {\n"
            "    Bar x;\n"
            "    void Run() { Foo x = null; }\n"
            "    void Other() { x.InOther(); }\n"
            "}\n"
        ),
    }, "Main.Other"))
    assert hints.pop("InBlock") == "Foo"
    assert set(hints.values()) - {None} == {"Bar"}
    assert all(hints[name] == "Bar" for name in hints if name.startswith(("After", "InOther"))), hints


def test_a_shadowing_declaration_wins_inside_its_scope(tmp_path: Path) -> None:
    """A field ``Bar x``. A local ``Foo x`` types ``x`` as ``Foo``; a local
    of no type (``var x = Make()``, a lambda's ``x =>``) hides the field: the
    call carries no type rather than the field's. A sibling block's type does
    not reach the next block's untyped ``y``."""
    edges = analyze_csharp(_write(tmp_path, {
        "Main.cs": (
            "class Main {\n"
            "    Bar x;\n"
            "    void Typed() { Foo x = null; x.Typed(); }\n"
            "    void Untyped() { var x = Factory.Make(); x.Untyped(); }\n"
            "    void Lambda() { Each(x => x.Lambda()); }\n"
            "    void Siblings() { { Foo y = null; y.First(); } { var y = Factory.Make(); y.Second(); } }\n"
            "    void Field() { x.Field(); }\n"
            "}\n"
        ),
    })).edges
    hints = _hints([e for e in edges if e.edge_type == "calls"
                    and (e.meta or {}).get("callee_name") not in ("Make", "Each")])
    assert hints == {"Typed": "Foo", "Untyped": None, "Lambda": None, "First": "Foo",
                     "Second": None, "Field": "Bar"}


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
    # Main's ancestors, never the file's table of short names. ``base.Up()``:
    # the walk starts at Main's parent.
    assert stubs == {
        "Elsewhere": {"enclosing_class": "Main"}, "Inherited": {"enclosing_class": "Main"},
        "Up": {"enclosing_class": "Main"},
    }
    assert [e.meta.get("super_receiver") for e in edges if not e.is_resolved] == [None, None, True]


def test_base_call_from_its_own_override_is_not_the_override(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "Main.cs": (
            "class Main : Base {\n"
            "    override Conn Twice() { base.Twice(); base.Twice().Go(); return null; }\n"
            "}\n"
        ),
    }, "Main.Twice")
    # The own ``Twice`` is the caller itself; ``base.Twice()`` is Base's:
    # handed over to start at the parent. Its result has no known type.
    assert not any(e.is_resolved for e in edges)
    assert sorted(((e.meta or {})["callee_name"], tuple(_facts(e).items()),
                   (e.meta or {}).get("super_receiver")) for e in edges) == [
        ("Go", (("call_construct", "method"),), None),
        ("Twice", (("enclosing_class", "Main"),), True),
        ("Twice", (("enclosing_class", "Main"),), True),
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


def test_end_to_end_a_generic_type_and_its_plain_namesake_each_get_their_calls(tmp_path: Path) -> None:
    """Polly's shape: ``Policy`` and ``Policy<TResult>`` in one namespace, in
    two files, each with an ``Execute``; a receiver of each, a qualifier of
    each, a ``new`` of each. Each call reaches its own type's method."""
    data = _map(tmp_path, {
        "Polly/Policy.cs": (
            "namespace Polly { public class Policy {\n"
            "    public void Execute() {}\n"
            "    public static Policy Handle() { return null; }\n"
            "} }\n"
        ),
        "Polly/Policy.TResult.cs": (
            "namespace Polly { public class Policy<TResult> {\n"
            "    public void Execute() {}\n"
            "    public static Policy<TResult> Handle() { return null; }\n"
            "} }\n"
        ),
        "App/Main.cs": (
            "using Polly;\n"
            "namespace App { public class Main {\n"
            "    public void Plain(Policy policy) { policy.Execute(); Policy.Handle(); new Policy().Execute(); }\n"
            "    public void Generic(Policy<int> policy) {\n"
            "        policy.Execute(); Policy<int>.Handle(); new Policy<int>().Execute();\n"
            "    }\n"
            "} }\n"
        ),
    })
    nodes = {n["id"]: n for n in data["nodes"]}

    def reached(caller: str) -> list[tuple[str, int, str]]:
        return sorted(
            (nodes[e["dst"]]["name"], nodes[e["dst"]]["span"]["start_line"], e["meta"]["resolved_by"])
            for e in data["edges"]
            if e["type"] == "calls" and e.get("is_resolved") and e["dst"] in nodes
            and not e["meta"].get("superseded_by")
            and nodes.get(e["src"], {}).get("name") == caller
            and nodes[e["dst"]]["path"].startswith("Polly/")
            and nodes[e["dst"]]["kind"] == "method"
        )
    paths = {n["path"] for n in nodes.values() if n.get("name") == "Policy.Execute"}
    assert len(paths) == 2
    plain = [(name, line) for name, line, _ in reached("Main.Plain")]
    generic = [(name, line) for name, line, _ in reached("Main.Generic")]
    # One Execute per receiver and the static Handle, all of the right type:
    # in Policy.cs, Execute is line 2 and Handle line 3; likewise in the other file.
    assert plain == [("Policy.Execute", 2), ("Policy.Handle", 3)]
    assert generic == [("Policy.Execute", 2), ("Policy.Handle", 3)]
    file_of = {
        caller: {nodes[e["dst"]]["path"] for e in data["edges"]
                 if e["type"] == "calls" and e.get("is_resolved") and e["dst"] in nodes
                 and nodes.get(e["src"], {}).get("name") == caller
                 and nodes[e["dst"]]["kind"] == "method"}
        for caller in ("Main.Plain", "Main.Generic")
    }
    assert {p.rsplit("/", 1)[-1] for p in file_of["Main.Plain"]} == {"Policy.cs"}
    assert {p.rsplit("/", 1)[-1] for p in file_of["Main.Generic"]} == {"Policy.TResult.cs"}


def _conn_files(main: str) -> dict[str, str]:
    """``Data.Conn`` and its namesake ``Legacy.Conn`` in two files, a factory
    in a fourth (none in the caller's file), and the caller ``main``."""
    return {
        "Data/Conn.cs": "namespace Data { public class Conn { public void Open() {} } }\n",
        "Legacy/Conn.cs": "namespace Legacy { public class Conn { public void Open() {} } }\n",
        "Data/Factory.cs": (
            "namespace Data { public static class Factory { public static object Make() { return null; } } }\n"
        ),
        "App/Main.cs": main,
    }


def test_end_to_end_a_local_declared_with_its_type_reaches_its_type_not_a_namesake(tmp_path: Path) -> None:
    data = _map(tmp_path, _conn_files(
        "using Data;\n"
        "namespace App { public class Main {\n"
        "    public void Run() { Conn c = (Conn)Factory.Make(); c.Open(); }\n"
        "} }\n"
    ))
    assert _resolved(data, "Main.Run", "Open") == [("Data.Conn.Open", "receiver_type")]


def test_end_to_end_a_local_declared_with_its_type_and_new(tmp_path: Path) -> None:
    data = _map(tmp_path, _conn_files(
        "using Legacy;\n"
        "namespace App { public class Main {\n"
        "    public void Run() { Conn c = new(); c.Open(); }\n"
        "} }\n"
    ))
    assert _resolved(data, "Main.Run", "Open") == [("Legacy.Conn.Open", "receiver_type")]


def test_end_to_end_a_local_typed_by_a_cast(tmp_path: Path) -> None:
    data = _map(tmp_path, _conn_files(
        "using Data;\n"
        "namespace App { public class Main {\n"
        "    public void Run(object o) { var c = (Conn?)o; c.Open(); }\n"
        "    public void RunAs(object o) { var d = o as Legacy.Conn; d.Open(); }\n"
        "} }\n"
    ))
    assert _resolved(data, "Main.Run", "Open") == [("Data.Conn.Open", "receiver_type")]
    assert _resolved(data, "Main.RunAs", "Open") == [("Legacy.Conn.Open", "receiver_type")]


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


def test_end_to_end_base_call_in_an_override_reaches_the_parents_method(tmp_path: Path) -> None:
    """``base.Run()`` inside ``Child.Run``, the parent in another file: the
    parent's Run, never the override itself. ``base.Stop()`` where Child
    declares no Stop and neither does any ancestor of the repository (the
    parent's own parent is a library's): unresolved."""
    data = _map(tmp_path, {
        "App/Base.cs": "namespace App { public class Base : Lib.Thing { public virtual void Run() {} } }\n",
        "App/Child.cs": (
            "namespace App { public class Child : Base {\n"
            "    public override void Run() { base.Run(); base.Stop(); }\n"
            "} }\n"
        ),
        "App/Stray.cs": "namespace App { public class Stray { public void Stop() {} } }\n",
    })
    assert _resolved(data, "Child.Run", "Run") == [("App.Base.Run", "own_type")]
    assert _resolved(data, "Child.Run", "Stop") == []


def test_end_to_end_an_extension_method_on_a_typed_receiver(tmp_path: Path) -> None:
    """Dapper's shape: ``cnn.TryOpenAsync()`` on an ``IDbConnection``
    parameter reaches ``static TryOpenAsync(this IDbConnection cnn)`` of
    another class and file; the same name declared as an instance method of
    an unrelated type, and an extension on another type, are not reached. A
    receiver of no known type reaches nothing."""
    data = _map(tmp_path, {
        "Data/IDbConnection.cs": "namespace Data { public interface IDbConnection { } }\n",
        "Data/Extensions.cs": (
            "namespace Data { public static class Extensions {\n"
            "    public static void TryOpenAsync(this IDbConnection cnn) {}\n"
            "    public static void TryOpenAsync(this Stray s, int x) {}\n"
            "} }\n"
        ),
        "Data/Stray.cs": "namespace Data { public class Stray { public void Close() {} } }\n",
        "Data/Other.cs": "namespace Data { public class Other { public void TryOpenAsync() {} } }\n",
        "App/Main.cs": (
            "using Data;\n"
            "namespace App { public class Main {\n"
            "    public void Run(IDbConnection cnn) { cnn.TryOpenAsync(); }\n"
            "    public void Untyped() { var cnn = Factory.Make(); cnn.TryOpenAsync(); }\n"
            "} }\n"
        ),
    })
    nodes = {n["id"]: n for n in data["nodes"]}
    reached = [
        (nodes[e["dst"]]["qualified_name"], nodes[e["dst"]]["span"]["start_line"],
         e["meta"].get("resolved_by"), e["meta"].get("evidence_type"))
        for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved") and e["dst"] in nodes
        and nodes.get(e["src"], {}).get("name") in ("Main.Run", "Main.Untyped")
    ]
    assert reached == [("Data.Extensions.TryOpenAsync", 2, "receiver_type", "ast_call_extension")]


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
