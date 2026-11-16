# SPDX-License-Identifier: AGPL-3.0-or-later
"""F# calls reach members and class-like types where the source decides the target.

A member nothing calls is reported by ``dead-code-maybe``, so emitting members
without edges to them turned every F# method into a dead-code candidate. These
tests pin the four call forms whose target the source itself names:

* a static call through the type (``Shape.Make 1.0``), the type found by its
  module-qualified name through the file's ``open``s and enclosing scopes;
* a construction (``Shape(1.0)``, ``new Shape(1.0)``);
* a call on a member's self identifier (``this.Scale 2.0``);
* a call on a value whose type the file states: ``let x = Shape(...)``,
  ``let x = Shape.Make ...`` when ``Make`` declares its return type, or an
  annotation ``(x: Shape)``.

and, as firmly, what must NOT happen: a receiver whose type the source does not
state is never matched by member name alone.
"""
from pathlib import Path
from typing import Optional

from hypergumbo_lang_common.fsharp import analyze_fsharp


def _analyze(tmp_path: Path, *files: tuple[str, str]):
    for name, body in files:
        (tmp_path / name).write_text(body)
    return analyze_fsharp(tmp_path)


def _one_file(tmp_path: Path, body: str, name: str = "Demo.fs"):
    return _analyze(tmp_path, (name, body))


def _edges(result, edge_type: Optional[str] = None) -> set[tuple[str, str, str, str]]:
    """``(src name, edge type, dst kind, dst name)`` for edges between symbols."""
    by_id = {s.id: s for s in result.symbols}
    return {
        (by_id[e.src].name, e.edge_type, by_id[e.dst].kind, by_id[e.dst].name)
        for e in result.edges
        if e.src in by_id and e.dst in by_id and e.edge_type != "imports"
        and (edge_type is None or e.edge_type == edge_type)
    }


def _edge(result, src: str, dst: str):
    by_id = {s.id: s for s in result.symbols}
    found = [
        e for e in result.edges
        if e.src in by_id and e.dst in by_id
        and by_id[e.src].name == src and by_id[e.dst].name == dst
    ]
    assert len(found) == 1, (src, dst, sorted(_edges(result)))
    return found[0], by_id[found[0].dst]


SHAPE = """module Geo

type Shape(r: float) =
    member this.Area = r * r
    member this.Scale(k: float) = r * k
    static member Make (r: float) : Shape = Shape(r)
    static member Loose (r: float) = Shape(r)
    new() = Shape(0.0)
"""


class TestStaticCallsThroughTheType:
    def test_static_member_called_through_its_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + "let build () = Shape.Make 1.0\n")
        edge, target = _edge(result, "build", "Shape.Make")
        assert (edge.edge_type, target.kind) == ("calls", "method")
        assert edge.meta["call_construct"] == "method"

    def test_type_in_a_nested_module_seen_from_inside_and_outside(
        self, tmp_path: Path,
    ) -> None:
        result = _one_file(tmp_path, """module Demo
module M =
    type T() =
        static member Make () = T()
    let inside () = T.Make()
let qualified () = M.T.Make()
let unqualified () = T.Make()
""")
        calls = _edges(result, "calls")
        assert ("M.inside", "calls", "method", "M.T.Make") in calls
        assert ("qualified", "calls", "method", "M.T.Make") in calls
        # Outside ``M`` and without ``open M``, a bare ``T`` names nothing here.
        assert not any(src == "unqualified" for src, *_ in calls)

    def test_open_of_a_nested_module_brings_its_types_into_scope(
        self, tmp_path: Path,
    ) -> None:
        result = _one_file(tmp_path, """module Demo
module M =
    type T() =
        static member Make () = T()
open M
let opened () = T.Make()
""")
        assert ("opened", "calls", "method", "M.T.Make") in _edges(result, "calls")

    def test_type_of_another_file_needs_its_module_opened_or_written(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(
            tmp_path,
            ("Geo.fs", SHAPE),
            ("Opened.fs", "module Opened\nopen Geo\nlet a () = Shape.Make 1.0\n"),
            ("Written.fs", "module Written\nlet b () = Geo.Shape.Make 1.0\n"),
            ("Neither.fs", "module Neither\nlet c () = Shape.Make 1.0\n"),
        )
        calls = _edges(result, "calls")
        assert ("a", "calls", "method", "Shape.Make") in calls
        assert ("b", "calls", "method", "Shape.Make") in calls
        assert not any(src == "c" for src, *_ in calls)

    def test_namespace_siblings_and_children_see_each_other(self, tmp_path: Path) -> None:
        result = _analyze(
            tmp_path,
            ("A.fs", "namespace Geo.Lib\n\ntype T() =\n    static member Make () = T()\n"),
            ("B.fs", "namespace Geo.Lib\n\nmodule Use =\n    let same () = T.Make()\n"),
            ("C.fs", "namespace Geo.Lib.Deep\n\nmodule Use2 =\n    let child () = T.Make()\n"),
            ("D.fs", "namespace Other\n\nmodule Use3 =\n    let stranger () = T.Make()\n"),
        )
        calls = _edges(result, "calls")
        assert ("Use.same", "calls", "method", "T.Make") in calls
        assert ("Use2.child", "calls", "method", "T.Make") in calls
        assert not any(src == "Use3.stranger" for src, *_ in calls)

    def test_two_visible_types_of_one_name_decide_nothing(self, tmp_path: Path) -> None:
        result = _analyze(
            tmp_path,
            ("A.fs", "module A\ntype T() =\n    static member Make () = T()\n"),
            ("B.fs", "module B\ntype T() =\n    static member Make () = T()\n"),
            ("C.fs", "module C\nopen A\nopen B\nlet both () = T.Make()\n"),
            ("D.fs", "module D\nopen A\nlet one () = T.Make()\n"),
        )
        calls = _edges(result, "calls")
        assert not any(src == "both" for src, *_ in calls)
        edge, target = _edge(result, "one", "T.Make")
        assert target.path == "A.fs"

    def test_the_type_of_the_same_file_wins_over_an_opened_one(self, tmp_path: Path) -> None:
        result = _analyze(
            tmp_path,
            ("A.fs", "module A\ntype T() =\n    static member Make () = T()\n"),
            ("B.fs", "module A\ntype T() =\n    static member Make () = T()\n"
                     "let here () = T.Make()\n"),
        )
        _, target = _edge(result, "here", "T.Make")
        assert target.path == "B.fs"

    def test_instance_member_is_not_reached_through_the_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + "let wrong () = Shape.Scale 1.0\n")
        assert not any(src == "wrong" for src, *_ in _edges(result))

    def test_static_member_of_a_record_and_a_static_property(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type P = { X: int }
    with static member Zero = { X = 0 }
         static member Of (x: int) = { X = x }
let a () = P.Of 1
let b () = P.Zero
""")
        calls = _edges(result, "calls")
        assert ("a", "calls", "method", "P.Of") in calls
        assert ("b", "calls", "property", "P.Zero") in calls

    def test_member_wins_over_a_bare_function_of_the_same_name(self, tmp_path: Path) -> None:
        """``Shape.Make`` used to fall back to any function named ``Make``."""
        result = _one_file(
            tmp_path, SHAPE + "let Make (x: float) = x\nlet build () = Shape.Make 1.0\n",
        )
        assert _edges(result, "calls") >= {("build", "calls", "method", "Shape.Make")}
        assert ("build", "calls", "function", "Make") not in _edges(result)

    def test_a_function_spelled_with_the_same_dotted_name_keeps_the_call(
        self, tmp_path: Path,
    ) -> None:
        """A companion module's ``Shape.make`` is a registered function: the
        dotted lookup the analyzer already did still answers first."""
        result = _one_file(tmp_path, """module Demo
type Shape(r: float) =
    static member make (r: float) = Shape(r)
module Shape =
    let make (r: float) = r
let build () = Shape.make 1.0
""")
        assert ("build", "calls", "function", "Shape.make") in _edges(result)
        assert ("build", "calls", "method", "Shape.make") not in _edges(result)


class TestConstruction:
    def test_construction_reaches_the_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + """let a () = Shape(1.0)
let b () = new Shape(2.0)
""")
        for src in ("a", "b"):
            edge, target = _edge(result, src, "Shape")
            assert (edge.edge_type, target.kind) == ("instantiates", "class")
            assert edge.meta["call_construct"] == "constructor"

    def test_primary_constructor_parameters_are_on_the_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + """type Bare =
    static member X = 1
type Hidden private (a: int, b: int) =
    member this.A = a
""")
        by_name = {s.name: s for s in result.symbols if s.kind == "class"}
        assert [p["name"] for p in by_name["Shape"].meta["parameters"]] == ["r"]
        assert by_name["Bare"].meta is None
        assert [p["name"] for p in by_name["Hidden"].meta["parameters"]] == ["a", "b"]

    def test_additional_constructor_when_the_argument_count_picks_it(
        self, tmp_path: Path,
    ) -> None:
        result = _one_file(tmp_path, SHAPE + "let empty () = Shape()\n")
        edge, target = _edge(result, "empty", "Shape.new")
        assert (edge.edge_type, target.kind) == ("instantiates", "constructor")
        assert not any(
            src == "empty" and kind == "class" for src, _, kind, _ in _edges(result)
        )

    def test_type_without_a_primary_constructor(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
[<Struct>]
type V =
    val X: int
    new(x) = { X = x }
    new(x, y) = { X = x + y }
let one () = V(1)
let two () = V(1, 2)
let three () = V(1, 2, 3)
""")
        one, _ = _edge(result, "one", "V.new")
        two, _ = _edge(result, "two", "V.new")
        assert one.dst != two.dst
        # No constructor takes three: the type stands for the construction.
        _, target = _edge(result, "three", "V")
        assert target.kind == "struct"

    def test_count_both_constructors_take_goes_to_the_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T(a: int) =
    new(s: string) = T(0)
let make () = T("x")
""")
        _, target = _edge(result, "make", "T")
        assert target.kind == "class"

    def test_generic_and_qualified_construction(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
module M =
    type Box<'T>(x: 'T) =
        member this.V = x
let a () = M.Box<int>(1)
let b () = M.Box(1)
""")
        assert ("a", "instantiates", "class", "M.Box") in _edges(result)
        assert ("b", "instantiates", "class", "M.Box") in _edges(result)

    def test_record_union_and_interface_are_not_constructed_by_application(
        self, tmp_path: Path,
    ) -> None:
        result = _one_file(tmp_path, """module Demo
type I =
    abstract M: int
let a () = I(1)
""")
        assert not any(src == "a" for src, *_ in _edges(result))

    def test_module_named_like_its_class_does_not_take_the_construction(
        self, tmp_path: Path,
    ) -> None:
        """``module Shape`` registers a symbol named ``Shape``; ``Shape(1.0)``
        used to be drawn as a call of the module."""
        result = _one_file(tmp_path, """module Shape

type Shape(r: float) =
    member this.R = r
let make () = Shape(1.0)
""")
        assert ("make", "instantiates", "class", "Shape") in _edges(result)
        assert ("make", "calls", "module", "Shape") not in _edges(result)

    def test_a_function_named_like_a_class_keeps_its_call(self, tmp_path: Path) -> None:
        result = _analyze(
            tmp_path,
            ("A.fs", "module A\ntype Widget(n: int) =\n    member this.N = n\n"),
            ("B.fs", "module B\nopen A\nlet Widget (n: int) = n\nlet use () = Widget 1\n"),
        )
        assert ("use", "calls", "function", "Widget") in _edges(result)
        assert not any(kind == "class" for _, _, kind, _ in _edges(result))


class TestCallsOnTheSelfIdentifier:
    def test_self_call_whatever_the_identifier(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T() =
    member this.A() = 1
    member self.B() = self.A() + 1
    member x.C() = x.B() + x.A()
""")
        calls = _edges(result, "calls")
        assert ("T.B", "calls", "method", "T.A") in calls
        assert ("T.C", "calls", "method", "T.B") in calls
        assert ("T.C", "calls", "method", "T.A") in calls

    def test_overload_is_picked_by_argument_count(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T() =
    member this.Go(a: int) = a
    member this.Go(a: int, b: int) = a + b
    member this.Zero() = 0
    member this.One() = this.Go(1)
    member this.Two() = this.Go(1, 2)
    member this.Spaced() = this.Go (1, 2)
    member this.None() = this.Zero()
""")
        first, second = [s for s in result.symbols if s.name == "T.Go"]
        by_src = {
            s.name: e for e in result.edges for s in result.symbols
            if s.id == e.src and e.edge_type == "calls"
        }
        assert by_src["T.One"].dst == first.id
        assert by_src["T.Two"].dst == second.id
        assert by_src["T.Spaced"].dst == second.id
        assert "resolution_quality" not in by_src["T.Two"].meta
        assert ("T.None", "calls", "method", "T.Zero") in _edges(result)

    def test_same_arity_overloads_are_an_ambiguous_record(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T() =
    member this.Go(a: int) = a
    member this.Go(a: string) = 0
    member this.One(a: int) = a
    member this.Use() = this.Go(1) + this.One(2)
""")
        first = next(s for s in result.symbols if s.name == "T.Go")
        ambiguous, _ = _edge(result, "T.Use", "T.Go")
        exact, _ = _edge(result, "T.Use", "T.One")
        # One edge, to the first by position, marked as a choice among two:
        # the convention of the C++ and Nim analyzers (choose_overload).
        assert ambiguous.dst == first.id
        assert ambiguous.meta["resolution_quality"] == "ambiguous"
        assert "resolution_quality" not in exact.meta
        assert ambiguous.confidence < exact.confidence

    def test_count_no_overload_takes_keeps_the_name(self, tmp_path: Path) -> None:
        """A tuple passed as one value reads as one argument."""
        result = _one_file(tmp_path, """module Demo
type T() =
    member this.Pair(a: int, b: int) = a + b
    member this.Use(t) = this.Pair t
""")
        assert ("T.Use", "calls", "method", "T.Pair") in _edges(result)

    def test_abstract_slot_with_a_default_calls_the_default(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
[<AbstractClass>]
type T() =
    abstract member Describe : unit -> string
    default this.Describe() = "t"
    abstract member Bare : unit -> string
    member this.Use() = this.Describe() + this.Bare()
""")
        slot, default = [s for s in result.symbols if s.name == "T.Describe"]
        dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert default.id in dsts and slot.id not in dsts
        # A slot with no body in the type is the target itself.
        assert ("T.Use", "calls", "method", "T.Bare") in _edges(result)

    def test_property_read_and_applied_property(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T(r: float) =
    member this.Area = r * r
    member this.Fn = fun (x: int) -> x
    member this.Use() = this.Area + 1.0
    member this.Call() = this.Fn 1
    member this.Set() = this.Tag <- 2
    member val Tag = 0 with get, set
""")
        calls = _edges(result, "calls")
        assert ("T.Use", "calls", "property", "T.Area") in calls
        assert ("T.Call", "calls", "property", "T.Fn") in calls
        assert ("T.Set", "calls", "property", "T.Tag") in calls

    def test_method_named_without_being_applied_is_no_call(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T() =
    member this.Go(a: int) = a
    member this.Ref() = List.map this.Go [1]
""")
        assert not any(dst == "T.Go" for *_, dst in _edges(result))

    def test_static_member_is_not_reached_through_self(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T() =
    static member Make () = T()
    member this.Use() = this.Make()
""")
        assert not any(dst == "T.Make" for *_, dst in _edges(result))

    def test_self_call_from_interface_record_and_extension_members(
        self, tmp_path: Path,
    ) -> None:
        result = _one_file(tmp_path, """module Demo
type P = { X: int }
    with member p.Len() = p.X
         member p.Twice() = p.Len() * 2
type P with
    member p.Thrice() = p.Len() * 3
type Bar() =
    member this.Helper() = 1
    interface System.IDisposable with
        member this.Dispose() = this.Helper() |> ignore
and Later() =
    member this.A() = 1
    member this.B() = this.A()
""")
        calls = _edges(result, "calls")
        assert ("P.Twice", "calls", "method", "P.Len") in calls
        assert ("P.Thrice", "calls", "method", "P.Len") in calls
        assert ("Bar.Dispose", "calls", "method", "Bar.Helper") in calls
        assert ("Later.B", "calls", "method", "Later.A") in calls

    def test_extension_of_a_type_declared_nowhere(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type System.String with
    member s.Shout() = s.ToUpper()
    member s.Twice() = s.Shout()
""")
        assert not any(dst == "System.String.Shout" for *_, dst in _edges(result))

    def test_extension_member_reached_from_the_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type P = { X: int }
    with member p.Use() = p.Twice()
type P with
    member p.Twice() = p.X * 2
""")
        assert ("P.Use", "calls", "method", "P.Twice") in _edges(result)

    def test_generic_twin_members_stay_with_their_own_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type Res<'T>() =
    member this.M() = 1
    member this.Use() = this.M()
type Res<'T, 'E>() =
    member this.M() = 2
    member this.Only() = 3
""")
        first = next(s for s in result.symbols if s.name == "Res.M")
        (edge,) = [e for e in result.edges if e.edge_type == "calls"]
        assert edge.dst == first.id
        assert "resolution_quality" not in edge.meta

    def test_self_of_an_object_expression_is_no_type(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type T() =
    member this.Go() = 1
let mk () =
    { new System.Object() with
        member this.ToString() = string (this.Go()) }
""")
        assert not any(dst == "T.Go" for *_, dst in _edges(result))


class TestCallsOnATypedValue:
    def test_value_bound_to_a_construction(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + """let a () =
    let s = Shape(1.0)
    s.Scale 2.0
let b () =
    let s = new Shape(1.0)
    let mutable t = (Shape(2.0))
    s.Scale 2.0 + t.Scale(3.0) + s.Area
let c () =
    use d = Shape(1.0)
    d.Scale 1.0
let d (x: Shape) =
    use other = Shape(1.0)
    x.Scale 1.0
""")
        calls = _edges(result, "calls")
        assert ("a", "calls", "method", "Shape.Scale") in calls
        assert ("b", "calls", "method", "Shape.Scale") in calls
        assert ("b", "calls", "property", "Shape.Area") in calls
        assert ("c", "calls", "method", "Shape.Scale") in calls
        # The ``use`` of another name is walked past to the typed parameter.
        assert ("d", "calls", "method", "Shape.Scale") in calls

    def test_value_bound_to_a_static_call_needs_a_declared_return_type(
        self, tmp_path: Path,
    ) -> None:
        result = _one_file(tmp_path, SHAPE + """let declared () =
    let s = Shape.Make 1.0
    s.Scale 2.0
let inferred () =
    let s = Shape.Loose 1.0
    s.Scale 2.0
""")
        calls = _edges(result, "calls")
        assert ("declared", "calls", "method", "Shape.Scale") in calls
        assert ("inferred", "calls", "method", "Shape.Scale") not in calls

    def test_overloads_returning_different_types_declare_none(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + """type Maker() =
    static member Of (x: int) : Shape = Shape(1.0)
    static member Of (x: string) : Maker = Maker()
let use () =
    let s = Maker.Of 1
    s.Scale 2.0
""")
        assert ("use", "calls", "method", "Shape.Scale") not in _edges(result)

    def test_annotated_values_and_parameters(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + """let get () = failwith "x"
let p (x: Shape) = x.Scale 2.0
let q (a: int, x: Shape) (y: Geo.Shape) = x.Scale 1.0 + y.Scale 2.0
let r () =
    let (v: Shape) = get ()
    let w : Shape = get ()
    v.Scale 1.0 + w.Scale 2.0
let lam = fun (z: Shape) -> z.Scale 1.0
type User(dep: Shape) =
    let cached = Shape(1.0)
    member this.A(other: Shape) = other.Scale 1.0
    member this.B() = dep.Scale 1.0 + cached.Scale 2.0
    member this.C(n: int) : Shape = dep
    member this.P
        with get () = cached.Area
        and set (v: Shape) = v.Scale 1.0 |> ignore
    new(a: Shape, b: int) = User(a.Scale 1.0 |> ignore; a)
""")
        calls = _edges(result, "calls")
        for src in ("p", "q", "r", "lam", "User.A", "User.B", "User.P", "User.new"):
            assert (src, "calls", "method", "Shape.Scale") in calls, src
        assert ("User.P", "calls", "property", "Shape.Area") in calls

    def test_module_level_value(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + """let shared = Shape(1.0)
module Inner =
    let use () = shared.Scale 2.0
""")
        assert ("Inner.use", "calls", "method", "Shape.Scale") in _edges(result)

    def test_receiver_expressions(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + """let a () = Shape(1.0).Scale(2.0)
let b () = (Shape.Make 1.0).Scale 2.0
let c () = (new Shape(1.0)).Area
""")
        calls = _edges(result, "calls")
        assert ("a", "calls", "method", "Shape.Scale") in calls
        assert ("b", "calls", "method", "Shape.Scale") in calls
        assert ("c", "calls", "property", "Shape.Area") in calls

    def test_instance_call_reaches_an_interface_slot(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type IFoo =
    abstract member Go : int -> int
let run (f: IFoo) = f.Go 1
""")
        assert ("run", "calls", "method", "IFoo.Go") in _edges(result)

    def test_static_member_is_not_reached_through_a_value(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + "let p (x: Shape) = x.Make 1.0\n")
        assert not any(src == "p" for src, *_ in _edges(result))


class TestFunctionsOfAnEnclosingScope:
    """A bare name a ``let`` around the call site binds as a function is that
    function. The name-keyed registry holds one symbol per name, so two
    classes' ``let inner``, two bodies' ``let local`` and two modules' ``run``
    used to resolve to one of them, or to neither."""

    def _targets(self, result) -> dict[str, tuple[str, int]]:
        by_id = {s.id: s for s in result.symbols}
        return {
            by_id[e.src].name: (by_id[e.dst].name, by_id[e.dst].span.start_line)
            for e in result.edges if e.edge_type == "calls"
        }

    def test_class_level_let_called_from_a_member(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type A(r: int) =
    let inner k = k
    member this.Go() = inner r
type B(r: int) =
    let inner k = k + 1
    member this.Go() = inner r
type G<'T>(r: 'T) =
    let inner k = k
    member this.Go() = inner r
""")
        assert self._targets(result) == {
            "A.Go": ("A.inner", 3), "B.Go": ("B.inner", 6), "G.Go": ("inner", 9),
        }

    def test_local_function_called_in_the_body_that_binds_it(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
let helper x = x
type T() =
    member this.A() =
        let local y = y
        local 1
    member this.B() =
        let local y = y + 1
        local 2
let outer () =
    let helper z = z + 1
    helper 3
let plain () = helper 4
""")
        targets = self._targets(result)
        assert targets["T.A"] == ("T.local", 5)
        assert targets["T.B"] == ("T.local", 8)
        # The local ``helper`` hides the module's; outside it, the module's.
        assert targets["outer"] == ("helper", 11)
        assert targets["plain"] == ("helper", 2)

    def test_recursive_local_function_calls_itself(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
let a () =
    let rec loop n = if n = 0 then 0 else loop (n - 1)
    1
let b () =
    let rec loop n = if n = 0 then 1 else loop (n - 2)
    2
""")
        loops = sorted(
            (s.span.start_line, s.id) for s in result.symbols if s.name == "loop"
        )
        calls = {(e.src, e.dst) for e in result.edges if e.edge_type == "calls"}
        assert calls == {(sid, sid) for _, sid in loops}

    def test_function_of_the_enclosing_module_by_its_bare_name(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
module A =
    let run x = x
    let go () = run 1
module B =
    let run x = x + 1
    let go () = run 2
""")
        assert self._targets(result) == {"A.go": ("A.run", 3), "B.go": ("B.run", 6)}

    def test_parameter_called_like_a_function_keeps_todays_answer(
        self, tmp_path: Path,
    ) -> None:
        """``helper`` is a parameter here, bound to nothing the source names.
        The registry's answer (the module's ``helper``) is what the analyzer
        emitted before and still emits."""
        result = _one_file(tmp_path, """module Demo
let helper x = x
let apply helper x = helper x
""")
        assert self._targets(result) == {"apply": ("helper", 2)}


class TestUndecidableReceivers:
    """No type in the source, no member edge: never the member name alone."""

    def _no_scale(self, tmp_path: Path, body: str) -> None:
        result = _one_file(tmp_path, SHAPE + body)
        assert not any(dst == "Shape.Scale" for *_, dst in _edges(result)), body

    def test_untyped_parameter(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, "let run x = x.Scale 2.0\n")

    def test_untyped_member_parameter_and_lambda(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, """type U() =
    member this.Go(x) = x.Scale 2.0
    member this.Lam() = fun y -> y.Scale 1.0
""")

    def test_value_of_an_unknown_expression(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, """let get () = failwith "x"
let run () =
    let s = get ()
    let t = s
    s.Scale 2.0 + t.Scale 1.0
let neg (y: float) =
    let v = -y
    v.Scale 1.0
let chained () =
    let w = Shape(1.0).Clone()
    w.Scale 1.0
""")

    def test_inner_binding_shadows_a_typed_outer_one(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, """let run (o: obj option) (xs: obj list) =
    let x = Shape(1.0)
    match o with
    | Some x -> x.Scale 2.0
    | None -> 0.0
let loop (xs: obj list) =
    let x = Shape(1.0)
    for x in xs do x.Scale 1.0 |> ignore
    for x = 0 to 3 do x.Scale 1.0 |> ignore
let lam () =
    let x = Shape(1.0)
    fun x -> x.Scale 1.0
let fn () =
    let x = Shape(1.0)
    let x () = 1
    x.Scale 1.0
""")

    def test_awaited_and_destructured_values(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, """let h () = async {
    let! a = async { return Shape(1.0) }
    return a.Scale 1.0 }
let d () =
    let a, b = Shape(1.0), Shape(2.0)
    a.Scale 1.0
""")

    def test_annotation_that_is_not_the_type_itself(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, """let a (x: Shape list) = x.Scale 1.0
let b (x: Shape option) = x.Scale 1.0
let c (x: 'T) = x.Scale 1.0
let d (x: Unknown) = x.Scale 1.0
""")

    def test_longer_paths_and_unknown_receivers(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, """let a (x: Shape) = x.Inner.Scale 1.0
let b () = (get ()).Scale 1.0
let c () = (1 + 2).Scale 1.0
let d () = Shape(1.0).Other.Scale 1.0
let e () = (+) 1 2
let f () = Missing.Scale 1.0
""")

    def test_values_that_refer_to_each_other_do_not_loop(self, tmp_path: Path) -> None:
        self._no_scale(tmp_path, """type A() =
    static member Of (x: obj) : Shape = Shape(1.0)
let p = q.Of 1
let q = p.Of 1
let use () = p.Scale 1.0
""")

    def test_typed_receiver_without_such_a_member_keeps_todays_answer(
        self, tmp_path: Path,
    ) -> None:
        """``x.Missing 1`` on a typed ``x``: the analyzer's bare-name lookup
        still runs, exactly as before the members existed."""
        result = _one_file(tmp_path, SHAPE + """let Missing (n: int) = n
let run (x: Shape) = x.Missing 1
""")
        assert ("run", "calls", "function", "Missing") in _edges(result)


class TestVisibility:
    """The analyzer sets ``is_exported`` on no symbol; it records the access
    modifier, from which finalize derives ``visibility`` and a False
    ``is_exported`` for anything not public."""

    def test_access_modifiers_of_types_and_members(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, """module Demo
type private Hidden() =
    member this.M() = 1
    member public this.Loud() = 2
type internal Inter() =
    member this.M() = 1
    member private this.Own() = 2
type Open() =
    member this.M() = 1
    member internal this.Half() = 2
type private Rec = { X: int }
    with member r.M() = 1
""")
        by_name = {s.name: s for s in result.symbols}
        assert by_name["Hidden"].modifiers == ["private"]
        assert by_name["Hidden.M"].modifiers == ["private"]
        # A member is no more visible than its type.
        assert by_name["Hidden.Loud"].modifiers == ["private"]
        assert by_name["Inter"].modifiers == ["internal"]
        assert by_name["Inter.M"].modifiers == ["internal"]
        assert by_name["Inter.Own"].modifiers == ["private"]
        assert by_name["Open"].modifiers == []
        assert by_name["Open.M"].modifiers == []
        assert by_name["Open.Half"].modifiers == ["internal"]
        assert by_name["Rec"].modifiers == ["private"]
        assert by_name["Rec.M"].modifiers == ["private"]

    def test_no_symbol_is_marked_exported(self, tmp_path: Path) -> None:
        result = _one_file(tmp_path, SHAPE + "let top x = x\n")
        assert {s.is_exported for s in result.symbols} == {None}


class TestRunsAreIndependent:
    def test_one_run_does_not_see_the_types_of_the_run_before(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        first = _one_file(tmp_path / "a", SHAPE + "let build () = Shape.Make 1.0\n")
        assert ("build", "calls", "method", "Shape.Make") in _edges(first)
        second = _one_file(tmp_path / "b", "module Geo\nlet build () = Shape.Make 1.0\n")
        assert _edges(second) == set()
