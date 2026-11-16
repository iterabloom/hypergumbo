# SPDX-License-Identifier: AGPL-3.0-or-later
"""F# class types and members are symbols (WI-tamib).

The analyzer emitted nothing for ``type Shape(r) = ...`` or for any ``member``
inside a type, so a call made in a member body had no enclosing symbol and was
dropped. These tests pin, form by form, that the declaration is a symbol of
the registered kind, named ``Owner.member`` the way the record fields already
are, with a non-null producer stable_id that a deleted earlier sibling does
not move.

They also pin what must NOT change, because this fix only adds symbols: the
names and ids of the functions the analyzer already emitted from inside a
class (a class-level ``let``, a function local to a member body), and of a
non-generic record beside a generic one of the same name.

Grammar facts the fixtures rely on (bundled ``tree_sitter_language_pack``
"fsharp"): a class is ``type_definition > anon_type_defn``; ``and``-chained
types are further children of ONE ``type_definition``; a generic type's
``type_name`` holds a ``long_identifier`` (a plain one an ``identifier``); a
tupled ``(k: float, j: float)`` parses as one ``typed_pattern`` whose type
belongs to the LAST element; ``member this.R(x: int) : int`` wraps the
argument in a ``typed_pattern`` whose type is the RETURN type; and
``static member val X = 0`` parses as a method named ``val`` taking ``X``.
"""
from pathlib import Path
from typing import Optional

from hypergumbo_core.analyze.base import make_declaration_stable_id
from hypergumbo_core.ir import Symbol
from hypergumbo_core.overload_arity import declared_arity, declared_arity_stable_id
from hypergumbo_lang_common.fsharp import analyze_fsharp


def _analyze(tmp_path: Path, body: str, name: str = "Demo.fs"):
    (tmp_path / name).write_text(body)
    return analyze_fsharp(tmp_path)


def _all(result, name: str, kind: Optional[str] = None) -> list[Symbol]:
    return [
        s for s in result.symbols
        if s.name == name and (kind is None or s.kind == kind)
    ]


def _one(result, name: str, kind: Optional[str] = None) -> Symbol:
    found = _all(result, name, kind)
    assert len(found) == 1, (name, kind, [(s.kind, s.name) for s in result.symbols])
    return found[0]


def _params(sym: Symbol) -> list[tuple[str, Optional[str]]]:
    assert sym.meta is not None
    return [(p["name"], p["type"]) for p in sym.meta["parameters"]]


def _calls(result) -> set[tuple[str, str]]:
    by_id = {s.id: s for s in result.symbols}
    return {
        (by_id[e.src].name, by_id[e.dst].name)
        for e in result.edges
        if e.edge_type == "calls" and e.src in by_id and e.dst in by_id
    }


SHAPE = """module Demo

let outside x = x

type Shape(r: float) =
    member this.Area = r * r
    member this.Scale(k: float) = r * k
    member _.Scale(k: float, j: float) = outside (r * k * j)
"""


class TestClassAndMembers:
    def test_class_is_a_class_symbol(self, tmp_path: Path) -> None:
        shape = _one(_analyze(tmp_path, SHAPE), "Shape")
        assert shape.kind == "class"
        assert shape.language == "fsharp"
        assert shape.stable_id is not None
        # Spans from the ``type`` keyword to the last member.
        assert (shape.span.start_line, shape.span.start_col) == (5, 0)
        assert shape.span.end_line == 8

    def test_class_takes_the_table_formula(self, tmp_path: Path) -> None:
        """``class`` is a table kind: the id is the backstop's own value, so
        the producer and the backstop cannot disagree."""
        shape = _one(_analyze(tmp_path, SHAPE), "Shape")
        assert shape.stable_id == make_declaration_stable_id(
            "class", "fsharp", "Demo.fs", "Shape",
        )

    def test_property_and_overloaded_methods(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, SHAPE)
        area = _one(result, "Shape.Area")
        assert area.kind == "property"
        assert area.stable_id is not None
        scales = _all(result, "Shape.Scale")
        assert [s.kind for s in scales] == ["method", "method"]
        assert _params(scales[0]) == [("k", "float")]
        # One typed_pattern in the grammar; two parameters in the source.
        assert _params(scales[1]) == [("k", "float"), ("j", "float")]
        ids = {area.stable_id, *(s.stable_id for s in scales)}
        assert None not in ids and len(ids) == 3

    def test_deleting_the_first_overload_moves_no_id(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        with_first = _analyze(tmp_path / "a", SHAPE)
        without = _analyze(
            tmp_path / "b",
            SHAPE.replace("    member this.Scale(k: float) = r * k\n", ""),
        )
        assert len(_all(without, "Shape.Scale")) == 1

        def ids(result) -> dict:
            return {
                (s.kind, s.name, str((s.meta or {}).get("parameters"))): s.stable_id
                for s in result.symbols if s.kind != "file"
            }

        before, after = ids(with_first), ids(without)
        assert set(after) < set(before)
        assert {k: before[k] for k in after} == after

    def test_member_span_starts_at_the_member(self, tmp_path: Path) -> None:
        area = _one(_analyze(tmp_path, SHAPE), "Shape.Area")
        assert (area.span.start_line, area.span.start_col) == (6, 4)

    def test_methods_carry_complexity_and_properties_do_not(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    member this.Pick(x: int) = if x > 0 then 1 else 0
    member this.P = 1
""")
        pick = _one(result, "T.Pick")
        assert pick.cyclomatic_complexity == 2
        assert pick.line_span == 1
        assert _one(result, "T.P").cyclomatic_complexity is None

    def test_class_in_a_nested_module_is_module_qualified(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
module M =
    type Inner() =
        member this.Go() = 1
""")
        assert _one(result, "M.Inner").kind == "class"
        go = _one(result, "M.Inner.Go")
        assert go.kind == "method"
        # ``()`` is a method taking nothing.
        assert _params(go) == []

    def test_same_member_in_two_modules_gets_two_ids(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
module A =
    type Inner() =
        member this.Go() = 1
module B =
    type Inner() =
        member this.Go() = 1
""")
        a, b = _one(result, "A.Inner.Go"), _one(result, "B.Inner.Go")
        assert a.stable_id != b.stable_id
        assert _one(result, "A.Inner").stable_id != _one(result, "B.Inner").stable_id


class TestMemberForms:
    def test_static_members(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T(n: int) =
    static member Unit = T(1)
    static member Make (n: int) = T(n)
""")
        unit = _one(result, "T.Unit")
        assert (unit.kind, unit.modifiers) == ("property", ["static"])
        make = _one(result, "T.Make")
        assert (make.kind, make.modifiers) == ("method", ["static"])
        assert _params(make) == [("n", "int")]

    def test_abstract_slot_and_its_default_are_two_symbols(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
[<AbstractClass>]
type T() =
    abstract member Describe : unit -> string
    default this.Describe() = "t"
    override this.ToString() = "s"
""")
        slot, default = _all(result, "T.Describe")
        assert (slot.kind, slot.modifiers) == ("method", ["abstract"])
        assert (default.kind, default.modifiers) == ("method", ["default"])
        # Same name, same arity: the modifier is what tells them apart, so
        # deleting the slot cannot move the default's id.
        assert _params(slot) == _params(default) == []
        assert slot.stable_id != default.stable_id
        assert slot.stable_id is not None and default.stable_id is not None
        assert _one(result, "T.ToString").modifiers == ["override"]

    def test_member_val_is_a_property(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    member val Tag = "x" with get, set
""")
        tag = _one(result, "T.Tag")
        assert tag.kind == "property"
        assert tag.meta is None

    def test_static_member_val_is_a_property_not_a_method_named_val(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    static member val Count = 0
""")
        count = _one(result, "T.Count")
        assert (count.kind, count.modifiers) == ("property", ["static"])
        assert _all(result, "T.val") == []

    def test_additional_constructors(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T(n: int) =
    new() = T(0)
    new(a: int, b: int) as self = T(a + b)
""")
        ctors = _all(result, "T.new")
        assert [c.kind for c in ctors] == ["constructor", "constructor"]
        assert _params(ctors[0]) == []
        assert _params(ctors[1]) == [("a", "int"), ("b", "int")]
        assert ctors[0].stable_id != ctors[1].stable_id
        assert ctors[0].signature == "()"
        assert ctors[1].cyclomatic_complexity == 1

    def test_property_with_accessors_is_one_property(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    let mutable count = 0
    member this.Prop
        with get() = count
        and set (v: int) = count <- v
""")
        assert _one(result, "T.Prop").kind == "property"

    def test_curried_and_untyped_arguments(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    member this.Curried a b = a + b
    member this.Pair (a, b) (c: int) = c
    member this.Wild(_: int) = 1
""")
        assert _params(_one(result, "T.Curried")) == [("a", None), ("b", None)]
        assert _params(_one(result, "T.Pair")) == [("a", None), ("b", None), ("c", "int")]
        assert _params(_one(result, "T.Wild")) == [("_", "int")]

    def test_return_annotation_is_not_a_parameter_type(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    member this.R(x: int) : string = "r"
    member this.Q x : int = x
    member this.P : int = 1
""")
        r = _one(result, "T.R")
        assert _params(r) == [("x", "int")]
        assert r.signature == "(x: int): string"
        q = _one(result, "T.Q")
        assert _params(q) == [("x", None)]
        assert q.signature == "(x): int"
        assert _one(result, "T.P").kind == "property"

    def test_optional_and_param_array_arguments(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    member this.F(?x: int) = 1
    member this.H(x: int, [<System.ParamArray>] rest: obj[]) = 2
""")
        (x,) = _one(result, "T.F").meta["parameters"]
        assert (x["name"], x["type"], x["default"]) == ("x", "int", True)
        first, rest = _one(result, "T.H").meta["parameters"]
        assert (first["name"], first["type"]) == ("x", "int")
        assert (rest["name"], rest["type"], rest.get("variadic")) == ("rest", "obj[]", True)

    def test_operator_generic_and_modified_members(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type Op(n: int) =
    static member (+) (a: Op, b: Op) = Op(1)
    member this.Map<'U>(f: int -> 'U) : 'U = f 1
    member private this.Hidden() = 1
    member inline this.Inl(x) = x
    [<CLIEvent>]
    member this.Ev = 1
""")
        plus = _one(result, "Op.(+)")
        assert plus.kind == "method"
        assert _params(plus) == [("a", "Op"), ("b", "Op")]
        assert _params(_one(result, "Op.Map")) == [("f", "int -> 'U")]
        assert _one(result, "Op.Hidden").modifiers == ["private"]
        assert _one(result, "Op.Inl").kind == "method"
        assert _one(result, "Op.Ev").kind == "property"

    def test_access_modifier_is_not_an_identity_input(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        public = _analyze(tmp_path / "a", "module Demo\ntype T() =\n    member this.M() = 1\n")
        private = _analyze(
            tmp_path / "b", "module Demo\ntype T() =\n    member private this.M() = 1\n",
        )
        assert _one(public, "T.M").stable_id == _one(private, "T.M").stable_id

    def test_explicit_fields(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type K = class
    val X: int
    val mutable Y: int
end
""")
        assert _one(result, "K").kind == "class"
        assert _one(result, "K.X").kind == "field"
        y = _one(result, "K.Y")
        assert y.kind == "field"
        assert y.stable_id is not None and y.stable_id != _one(result, "K.X").stable_id


class TestTypeKinds:
    def test_all_abstract_type_without_constructor_is_an_interface(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, """module Demo
type IFoo =
    abstract member Go : int -> int
    abstract Name : string
    abstract member Two : x: int * y: string -> unit
    abstract member Cur : int -> string -> unit
    abstract member Opt : ?x: int -> unit
    abstract P2 : int with get, set
""")
        ifoo = _one(result, "IFoo")
        assert ifoo.kind == "interface"
        assert ifoo.stable_id is not None
        go = _one(result, "IFoo.Go")
        assert (go.kind, go.modifiers) == ("method", ["abstract"])
        assert _params(go) == [("", "int")]
        assert go.signature == "(int): int"
        assert _one(result, "IFoo.Name").kind == "property"
        two = _one(result, "IFoo.Two")
        assert _params(two) == [("x", "int"), ("y", "string")]
        assert two.signature == "(x: int, y: string): unit"
        assert _params(_one(result, "IFoo.Cur")) == [("", "int"), ("", "string")]
        (opt,) = _one(result, "IFoo.Opt").meta["parameters"]
        assert (opt["name"], opt["default"]) == ("x", True)
        assert _one(result, "IFoo.P2").kind == "property"

    def test_interface_inheriting_an_interface(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type I3 =
    inherit System.IDisposable
    abstract N: unit -> unit
""")
        assert _one(result, "I3").kind == "interface"

    def test_interface_end_form_and_attribute(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type IEmpty = interface end

type I2 =
    interface
        abstract M: int
    end

[<Interface>]
type IMarked =
    abstract M: int
""")
        assert _one(result, "IEmpty").kind == "interface"
        assert _one(result, "I2").kind == "interface"
        assert _one(result, "I2.M").kind == "property"
        assert _one(result, "IMarked").kind == "interface"

    def test_abstract_class_is_a_class_with_the_abstract_modifier(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, """module Demo
[<AbstractClass>]
type Base =
    abstract member Run : unit -> unit
""")
        base = _one(result, "Base")
        assert (base.kind, base.modifiers) == ("class", ["abstract"])
        # The span starts at the attribute, where the type_definition does.
        assert base.span.start_line == 2

    def test_struct_forms(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
[<Struct>]
type V =
    val X: int
    new(x) = { X = x }
    member v.Double = v.X * 2

type S2 =
    struct
        val Y: int
    end
""")
        assert _one(result, "V").kind == "struct"
        assert _one(result, "V.X").kind == "field"
        assert _one(result, "V.new").kind == "constructor"
        assert _one(result, "V.Double").kind == "property"
        assert _one(result, "S2").kind == "struct"
        assert _one(result, "S2.Y").kind == "field"

    def test_type_with_a_concrete_member_and_no_constructor_is_a_class(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, """module Demo
type Util =
    static member Make() = 1
""")
        assert _one(result, "Util").kind == "class"

    def test_class_with_a_let_or_do_is_a_class(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type WithDo =
    do ()
    abstract M: int

type Empty = class end
""")
        assert _one(result, "WithDo").kind == "class"
        assert _one(result, "Empty").kind == "class"


class TestMembersOfOtherOwners:
    def test_interface_implementation_members(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type Bar() =
    interface IFoo<int> with
        member this.Go x = x + 1
        member this.Name = "bar"
    interface System.IDisposable with
        member this.Dispose() = ()
""")
        go = _one(result, "Bar.Go")
        assert go.kind == "method"
        assert _params(go) == [("x", None)]
        assert _one(result, "Bar.Name").kind == "property"
        assert _one(result, "Bar.Dispose").kind == "method"

    def test_same_member_of_two_interfaces_gets_two_ids(self, tmp_path: Path) -> None:
        body = """module Demo
type Bar() =
    interface IA with
        member this.Go() = 1
    interface IB with
        member this.Go() = 2
    member this.Go() = 3
"""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        three = [s.stable_id for s in _all(_analyze(tmp_path / "a", body), "Bar.Go")]
        assert len(three) == 3 and len(set(three)) == 3 and None not in three
        # Deleting the first implementation moves neither of the others.
        two = [s.stable_id for s in _all(_analyze(
            tmp_path / "b",
            body.replace("    interface IA with\n        member this.Go() = 1\n", ""),
        ), "Bar.Go")]
        assert two == three[1:]

    def test_members_on_records_and_unions(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type P = { X: int }
    with member p.Len = p.X
         static member Zero = { X = 0 }

type U =
    | A of int
    | B
    member u.IsA = match u with A _ -> true | _ -> false
""")
        assert _one(result, "P").kind == "record"
        assert _one(result, "P.Len").kind == "property"
        assert _one(result, "P.Zero").modifiers == ["static"]
        assert _one(result, "U").kind == "union"
        assert _one(result, "U.IsA").kind == "property"

    def test_type_extension_members(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type P = { X: int }

type P with
    member p.Twice = p.X * 2

type System.String with
    member s.Shout() = s.ToUpper()
""")
        assert _one(result, "P.Twice").kind == "property"
        assert _one(result, "System.String.Shout").kind == "method"
        # An extension declares no type of its own.
        assert len(_all(result, "P")) == 1
        assert _all(result, "System.String") == []

    def test_members_of_a_type_the_grammar_cannot_parse_emit_nothing(
        self, tmp_path: Path,
    ) -> None:
        """The bundled grammar does not close the last declaration of a file
        that ends without a newline: the type becomes an ERROR node holding
        ``member_defn`` nodes that belong to no type definition. Nothing is
        emitted for them, and nothing raises."""
        result = _analyze(
            tmp_path,
            "type Person(name: string) =\n"
            "    member this.Name = name\n"
            "    member this.Other = name",
        )
        assert [s.kind for s in result.symbols] == ["file"]

    def test_comments_inside_a_member_are_not_its_parts(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type T() =
    member this.A(x: int (* why *)) = x
    // a note the grammar attaches to the member above
    member this.B( (* why *) y: int) = y
    new (* why *) (z: int) = T()
    interface (* why *) System.IDisposable with
        member this.Dispose() = ()
    abstract member S : (* why *) int -> int (* why *)
""")
        assert _params(_one(result, "T.A")) == [("x", "int")]
        assert _params(_one(result, "T.B")) == [("y", "int")]
        assert _params(_one(result, "T.new")) == [("z", "int")]
        assert _one(result, "T.Dispose").kind == "method"
        slot = _one(result, "T.S")
        assert _params(slot) == [("", "int")]
        assert slot.signature == "(int): int"

    def test_object_expression_members_are_not_symbols(self, tmp_path: Path) -> None:
        """An object expression is an anonymous implementation inside a
        function; its members keep crediting that function."""
        result = _analyze(tmp_path, """module Demo
let helper x = x
let mk () =
    { new System.IDisposable with
        member this.Dispose() = helper 1 }
""")
        assert [s for s in result.symbols if s.kind in ("method", "property")] == []
        assert ("mk", "helper") in _calls(result)


class TestAndChainsAndGenerics:
    def test_and_chained_classes(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type A() =
    member this.F() = 1
and B() =
    member this.G() = 2
and [<Struct>] C2 =
    val Z: int
""")
        a, b = _one(result, "A"), _one(result, "B")
        assert (a.kind, b.kind) == ("class", "class")
        # Each spans its own definition, so neither encloses the other.
        assert (a.span.start_line, a.span.end_line) == (2, 3)
        assert (b.span.start_line, b.span.end_line) == (4, 5)
        assert _one(result, "A.F").kind == "method"
        assert _one(result, "B.G").kind == "method"
        assert _one(result, "C2").kind == "struct"
        assert _one(result, "C2.Z").kind == "field"

    def test_and_chained_records_and_unions_are_each_emitted(self, tmp_path: Path) -> None:
        body = """module Demo
type R1 = { A: int }
and R2 = { B: int }
    with member r.Twice = r.B * 2
and U2 =
    | Leaf
    | Node of R1
"""
        result = _analyze(tmp_path, body)
        r1, r2 = _one(result, "R1"), _one(result, "R2")
        assert (r1.kind, r2.kind) == ("record", "record")
        assert (r1.span.start_line, r1.span.end_line) == (2, 2)
        assert (r2.span.start_line, r2.span.end_line) == (3, 4)
        assert _one(result, "R2.B").kind == "field"
        assert _one(result, "R2.Twice").kind == "property"
        assert _one(result, "U2").kind == "union"

    def test_record_chained_after_a_class_keeps_its_id(self, tmp_path: Path) -> None:
        """``and B = {...}`` after a class was already emitted (with the whole
        chain's span): its id is the formula over its name, as before."""
        result = _analyze(tmp_path, """module Demo
type A() =
    member this.F() = 1
and B =
    { X: int }
""")
        b = _one(result, "B")
        assert b.stable_id == declared_arity_stable_id(
            kind="record", lang="fsharp", path="Demo.fs", name="B",
            qualified_name="B", arity=None,
        )
        assert b.span.start_line == 4

    def test_generic_types_are_emitted(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type Gen<'T> = { V: 'T }
type Opt<'T> = | Some2 of 'T | None2
type Box<'T, 'U>(x: 'T) =
    member this.V = x
""")
        assert _one(result, "Gen").kind == "record"
        assert _one(result, "Gen.V").kind == "field"
        assert _one(result, "Opt").kind == "union"
        assert _one(result, "Box").kind == "class"
        assert _one(result, "Box.V").kind == "property"

    def test_generic_twin_does_not_move_the_plain_type(self, tmp_path: Path) -> None:
        """``type Gen<'T>`` was dropped; now it is emitted BEFORE a same-name
        plain ``Gen``. The plain record and its field keep the ids they had
        when they were alone, because the generic one hashes its arity."""
        plain = "type Gen = { V: int }\n"
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        alone = _analyze(tmp_path / "a", "module Demo\n" + plain)
        both = _analyze(tmp_path / "b", "module Demo\ntype Gen<'T> = { V: 'T }\n" + plain)
        generic, twin = _all(both, "Gen")
        assert twin.stable_id == _one(alone, "Gen").stable_id
        assert generic.stable_id not in (None, twin.stable_id)
        generic_field, twin_field = _all(both, "Gen.V")
        assert twin_field.stable_id == _one(alone, "Gen.V").stable_id
        assert generic_field.stable_id not in (None, twin_field.stable_id)

    def test_generic_arity_separates_two_classes(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
type Res<'T>() =
    member this.M() = 1
type Res<'T, 'E>() =
    member this.M() = 1
""")
        one, two = _all(result, "Res")
        assert one.stable_id != two.stable_id
        m1, m2 = _all(result, "Res.M")
        assert m1.stable_id != m2.stable_id


class TestCallsFromMembers:
    def test_call_in_a_member_body_is_credited_to_the_member(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
let helper x = x
type T(n: int) =
    member this.Bump() = helper 1
    member this.Area = helper n
    static member Make (k: int) = helper k
    new() = T(helper 0)
""")
        calls = _calls(result)
        assert ("T.Bump", "helper") in calls
        assert ("T.Area", "helper") in calls
        assert ("T.Make", "helper") in calls
        assert ("T.new", "helper") in calls

    def test_overloads_each_keep_their_own_calls(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
let one x = x
let two x = x
type T() =
    member this.Go(a: int) = one a
    member this.Go(a: int, b: int) = two a
""")
        first, second = _all(result, "T.Go")
        by_id = {s.id: s.name for s in result.symbols}
        targets = {
            e.src: by_id[e.dst] for e in result.edges if e.edge_type == "calls"
        }
        assert targets[first.id] == "one"
        assert targets[second.id] == "two"

    def test_calls_in_interface_record_and_extension_members(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
let helper x = x
type P = { X: int }
    with member p.Len = helper p.X
type P with
    member p.Twice() = helper 2
type Bar() =
    interface System.IDisposable with
        member this.Dispose() = helper 3
""")
        calls = _calls(result)
        assert ("P.Len", "helper") in calls
        assert ("P.Twice", "helper") in calls
        assert ("Bar.Dispose", "helper") in calls

    def test_call_in_the_class_body_is_credited_to_the_class(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
let helper x = x
type T() =
    let cached = helper 1
    do helper 2
and U() =
    do helper 3
""")
        calls = _calls(result)
        assert ("T", "helper") in calls
        assert ("U", "helper") in calls

    def test_class_on_the_first_line_of_a_script(self, tmp_path: Path) -> None:
        """A headerless file: the class starts where the file does."""
        result = _analyze(tmp_path, """type T() =
    member this.Go() = helper 1
and Later() =
    member this.Go() = helper 2
let helper x = x
""", name="script.fsx")
        assert ("T.Go", "helper") in _calls(result)
        assert ("Later.Go", "helper") in _calls(result)

    def test_member_is_not_matched_by_its_name_alone(self, tmp_path: Path) -> None:
        """Members stay out of the name-keyed resolver, like record fields: a
        registered ``Shape.Scale`` would suffix-match every ``Scale`` call in
        the repository. ``x`` and ``y`` have no stated type here, so nothing
        is drawn (``test_fsharp_member_calls.py`` holds the decided forms)."""
        result = _analyze(tmp_path, """module Demo
type Shape(r: float) =
    member this.Scale(k: float) = r * k
let run x = x.Scale 2.0
let bare y = Scale y
""")
        assert _calls(result) == set()


class TestExistingSymbolsDoNotMove:
    def test_class_level_let_keeps_its_name_and_id(self, tmp_path: Path) -> None:
        result = _analyze(tmp_path, """module Demo
let outside x = x
type Shape(r: float) =
    let inner k = outside k
    member this.Area = inner r
""")
        inner = _one(result, "Shape.inner")
        assert inner.kind == "function"
        assert inner.stable_id == declared_arity_stable_id(
            kind="function", lang="fsharp", path="Demo.fs", name="inner",
            qualified_name="Shape.inner", arity=declared_arity(inner.meta),
        )
        assert ("Shape.inner", "outside") in _calls(result)

    def test_function_local_to_a_member_keeps_its_name_and_id(self, tmp_path: Path) -> None:
        """It was named after the class, not the member, and its id hashes that
        chain. Scoping it under the member now would move the id."""
        result = _analyze(tmp_path, """module Demo
let outside x = x
module M =
    type Inner() =
        member this.Go() =
            let local y = outside y
            local 2
""")
        local = _one(result, "M.Inner.local")
        assert local.kind == "function"
        assert local.stable_id == declared_arity_stable_id(
            kind="function", lang="fsharp", path="Demo.fs", name="local",
            qualified_name="M.Inner.local", arity=declared_arity(local.meta),
        )
        calls = _calls(result)
        assert ("M.Inner.local", "outside") in calls
        assert ("M.Inner.Go", "outside") not in calls

    def test_class_level_let_in_a_generic_class_keeps_its_bare_name(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, """module Demo
type Box<'T>(x: 'T) =
    let inner k = k
    member this.V = inner x
""")
        assert _one(result, "inner").kind == "function"

    def test_plain_record_union_and_field_ids_are_the_same_formula(
        self, tmp_path: Path,
    ) -> None:
        result = _analyze(tmp_path, """module Demo
module A =
    type Point = { x: int }
    type Kind = | One | Two
""")
        for name, kind in (("A.Point", "record"), ("A.Point.x", "field"), ("A.Kind", "union")):
            assert _one(result, name).stable_id == declared_arity_stable_id(
                kind=kind, lang="fsharp", path="Demo.fs",
                name=name.rsplit(".", 1)[-1], qualified_name=name, arity=None,
            ), name
