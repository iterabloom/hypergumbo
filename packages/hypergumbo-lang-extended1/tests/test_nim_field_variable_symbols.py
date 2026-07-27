# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Nim field/variable symbol emission (WI-jusus emission-parity tail).

The Nim analyzer emitted proc/func/method/type symbols but ZERO
``kind="field"`` (object/enum members) and ``kind="variable"`` (module-level
``const``/``var``/``let``) symbols. This slice adds them while keeping them out
of the call graph via the established ``register_symbol`` chokepoint: field and
variable are DATA anchors, never call targets, so registering a bare-named
module ``variable`` under the same flat key as a same-named ``proc`` would
clobber the real call (a false-negative the edge site cannot recover).

Grammar facts (verified against the bundled ``tree_sitter_language_pack`` "nim"
grammar the analyzer loads):

* Object fields are ``field_declaration`` nodes inside an ``object_declaration``;
  enum members are ``enum_field_declaration`` nodes — both DISTINCT nodes no
  local construct ever uses, so the field half is structurally leak-proof (no
  scope-walk needed, unlike kotlin/swift/go).
* Module ``const``/``var``/``let`` are ``variable_declaration`` nodes whose
  section (``const_section``/``var_section``/``let_section``) sits DIRECTLY
  under ``source_file``; a function-body local uses the SAME
  ``variable_declaration`` but its section sits under a ``statement_list`` — so
  the module-variable half needs a "section is a direct child of source_file"
  guard.
* The ``*`` export marker (``name*``/``Pi*``) wraps the identifier in an
  ``exported_symbol`` node — already handled by ``_declared_name_node``
  (the INV-bisom fix), reused here.
"""
from pathlib import Path

from hypergumbo_lang_extended1.nim import analyze_nim


def _write(tmp_path: Path, body: str, name: str = "mod.nim") -> None:
    (tmp_path / name).write_text(body)


def _names(result, kind: str) -> set[str]:
    return {s.name for s in result.symbols if s.kind == kind}


def _syms(result, kind: str) -> list:
    return [s for s in result.symbols if s.kind == kind]


class TestNimObjectFields:
    def test_object_fields_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
type
  Person = object
    name: string
    age: int
''')
        fields = _names(analyze_nim(tmp_path), "field")
        assert "Person.name" in fields
        assert "Person.age" in fields

    def test_multi_name_fields_emitted(self, tmp_path: Path) -> None:
        # `age, weight: int` declares two fields sharing one type — both emit.
        _write(tmp_path, '''
type
  Person = object
    age, weight: int
''')
        fields = _names(analyze_nim(tmp_path), "field")
        assert "Person.age" in fields
        assert "Person.weight" in fields

    def test_exported_field_marker(self, tmp_path: Path) -> None:
        # `name*` is an EXPORTED field: the `*` wraps the identifier in an
        # exported_symbol node. The field emits with is_exported=True.
        _write(tmp_path, '''
type
  Person* = object
    name*: string
''')
        fields = _syms(analyze_nim(tmp_path), "field")
        by_name = {s.name: s for s in fields}
        assert "Person.name" in by_name
        assert by_name["Person.name"].is_exported is True

    def test_enum_variants_emitted_as_fields(self, tmp_path: Path) -> None:
        # Enum variants are type members accessed via `.` (Color.red); emit
        # them as fields, consistent with the dart/zig enum-body-value precedent.
        _write(tmp_path, '''
type
  Color = enum
    red
    green = 2
    blue
''')
        fields = _names(analyze_nim(tmp_path), "field")
        assert "Color.red" in fields
        assert "Color.green" in fields
        assert "Color.blue" in fields


class TestNimModuleVariables:
    def test_module_const_var_let_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
const Pi = 3.14
var counter: int = 0
let MAX = 100
''')
        variables = _names(analyze_nim(tmp_path), "variable")
        assert "Pi" in variables
        assert "counter" in variables
        assert "MAX" in variables

    def test_multi_name_module_var(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
var a, b = 0
''')
        variables = _names(analyze_nim(tmp_path), "variable")
        assert "a" in variables
        assert "b" in variables

    def test_exported_module_var_marker(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
const Pi* = 3.14
''')
        variables = _syms(analyze_nim(tmp_path), "variable")
        by_name = {s.name: s for s in variables}
        assert "Pi" in by_name
        assert by_name["Pi"].is_exported is True

    def test_type_declaration_is_not_a_variable(self, tmp_path: Path) -> None:
        # `type Point = object` declares a TYPE, not a variable.
        _write(tmp_path, '''
type
  Point = object
    x: int
''')
        result = analyze_nim(tmp_path)
        variables = _names(result, "variable")
        assert "Point" not in variables
        assert "Point" in _names(result, "type")

    def test_locals_are_not_module_variables(self, tmp_path: Path) -> None:
        # A proc-body `var`/`let` is the SAME variable_declaration node, but its
        # section sits under statement_list (not source_file) — excluded.
        _write(tmp_path, '''
proc compute(a: int): int =
  var localVar = 1
  let localLet = 2
  return localVar + localLet + a
''')
        variables = _names(analyze_nim(tmp_path), "variable")
        assert "localVar" not in variables
        assert "localLet" not in variables


class TestNimFieldVariableCallGraphIntegrity:
    def test_field_and_variable_not_call_targets(self, tmp_path: Path) -> None:
        # A module variable / object field sharing a name with a proc must NOT
        # steal the call: a call to the name resolves to the proc.
        _write(tmp_path, '''
var value = 0

type
  Box = object
    label: string

proc value(): int =
  return 1

proc caller(): int =
  return value()
''')
        result = analyze_nim(tmp_path)
        # The variable, field, and function are all emitted...
        assert "value" in _names(result, "variable")
        assert "Box.label" in _names(result, "field")
        assert "value" in _names(result, "function")
        # ...but no calls edge resolves to a data anchor.
        var_ids = {s.id for s in result.symbols if s.kind == "variable"}
        field_ids = {s.id for s in result.symbols if s.kind == "field"}
        anchor_ids = var_ids | field_ids
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in anchor_ids]
        assert bad == [], f"a call resolved to a data anchor: {bad}"


class TestNimNestedTupleFieldAttribution:
    """An inline anonymous tuple used as a field TYPE (`coord: tuple[x, y: int]`)
    is modelled by the bundled grammar with the tuple's OWN nested
    field_declaration nodes. Those inner members belong to the tuple, not to the
    enclosing named object — attributing them to the object mints phantom,
    wrong-owner fields (`Outer.x`) and, on a name collision, a duplicate
    `Owner.member`. The owner walk must abort when it crosses an intervening
    field_declaration before the type_declaration (a fails-safe skip). Regression
    for the adversarial-review NO-GO (a confidently-wrong symbol hidden by 100%
    line coverage — the failsafe test only covered a nested *object*, which
    mis-parses to ERROR; a nested *tuple* parses cleanly).
    """

    def test_inline_tuple_object_field_no_phantom_members(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
type
  Outer = object
    coord: tuple[x, y: int]
    name: string
''')
        fields = _names(analyze_nim(tmp_path), "field")
        # The tuple-typed field itself and the scalar field emit correctly...
        assert "Outer.coord" in fields
        assert "Outer.name" in fields
        # ...but the tuple's inner members are NOT fields of Outer.
        assert "Outer.x" not in fields
        assert "Outer.y" not in fields

    def test_inline_tuple_field_name_collision(self, tmp_path: Path) -> None:
        # A tuple member named identically to a real field must not mint a
        # second `Config.name` symbol.
        _write(tmp_path, '''
type
  Config = object
    name: string
    meta: tuple[name: string, version: int]
''')
        fields = [s for s in analyze_nim(tmp_path).symbols if s.kind == "field"]
        assert sorted(s.name for s in fields) == ["Config.meta", "Config.name"]

    def test_standalone_named_tuple_members_emitted(self, tmp_path: Path) -> None:
        # A standalone named tuple TYPE genuinely owns its members (`c.x` where
        # `c: Coord`), so those must still emit — the fix must not over-suppress.
        _write(tmp_path, '''
type
  Coord = tuple[x, y: int]
''')
        fields = _names(analyze_nim(tmp_path), "field")
        assert "Coord.x" in fields
        assert "Coord.y" in fields

    def test_object_variant_branch_fields_emitted(self, tmp_path: Path) -> None:
        # Variant (`case`) branch fields ARE object members (reached via
        # of_branch/variant_declaration, no intervening field_declaration) — the
        # fix must preserve them.
        _write(tmp_path, '''
type
  Shape = object
    case kind: bool
    of true:
      radius: float
    of false:
      side: int
''')
        fields = _names(analyze_nim(tmp_path), "field")
        assert "Shape.radius" in fields
        assert "Shape.side" in fields

    def test_proc_return_tuple_emits_no_field(self, tmp_path: Path) -> None:
        # A tuple in a proc RETURN type has no enclosing named type — its members
        # must not be emitted as fields (owner walk exhausts to the root).
        _write(tmp_path, '''
proc makePair(): tuple[a, b: int] =
  result = (a: 1, b: 2)
''')
        fields = _names(analyze_nim(tmp_path), "field")
        assert not any(f.endswith(".a") or f.endswith(".b") for f in fields)


class TestNimFieldVariableFailsafe:
    def test_nested_anonymous_object_does_not_crash(self, tmp_path: Path) -> None:
        # The bundled grammar mis-parses a nested anonymous object type into an
        # ERROR/call subtree (not a field_declaration), so the inner field is
        # naturally skipped — a MISS, never a wrong symbol — and the outer
        # field still emits. Fails-safe: no crash, no phantom symbol.
        _write(tmp_path, '''
type
  Nested = object
    inner: object
      deep: int
''')
        result = analyze_nim(tmp_path)
        fields = _names(result, "field")
        assert "Nested.inner" in fields
        # `deep` mis-parsed under an ERROR node; never emitted as a field.
        assert "Nested.deep" not in fields
        assert not any(s.name.endswith(".deep") for s in result.symbols)
