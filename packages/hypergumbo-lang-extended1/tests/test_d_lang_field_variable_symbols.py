# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for D field/variable symbol emission (WI-jusus emission-parity tail).

The D analyzer emitted module/function/method/struct/class/interface symbols but
ZERO ``kind="field"`` (struct/class members, enum members) and ``kind="variable"``
(module-level globals) symbols. This slice adds them while keeping them out of the
call graph via the established ``register_symbol`` chokepoint: field/variable are
DATA anchors, never call targets, so a bare-named module ``variable`` sharing a
name with a ``function`` must not clobber the resolver's flat key.

Grammar facts (verified against the bundled ``tree_sitter_language_pack`` "d"
grammar the analyzer loads): both a struct/class field and a module global and a
function-body local are the SAME ``variable_declaration`` node (``auto x = …`` is a
distinct ``auto_declaration``), so the classifier keys on the IMMEDIATE parent —
``aggregate_body`` → field (owner = enclosing struct/class/interface), ``module_def``
→ variable, anything else (``block_statement``/``function_body``) → skipped local.
Enum members are ``enum_member`` nodes. Multi-name (``int a, b;``) lists one
``declarator`` per name.
"""
from pathlib import Path

from hypergumbo_lang_extended1.d_lang import analyze_d


def _write(tmp_path: Path, body: str, name: str = "mod.d") -> None:
    (tmp_path / name).write_text(body)


def _names(result, kind: str) -> set[str]:
    return {s.name for s in result.symbols if s.kind == kind}


class TestDStructFields:
    def test_struct_fields_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
struct Point {
    float x;
    float y;
}
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "Point.x" in fields
        assert "Point.y" in fields

    def test_class_fields_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
class Widget {
    string label;
    int count;
}
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "Widget.label" in fields
        assert "Widget.count" in fields

    def test_auto_field_emitted(self, tmp_path: Path) -> None:
        # `auto tag = 7;` is a distinct auto_declaration node.
        _write(tmp_path, '''
struct S {
    auto tag = 7;
}
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "S.tag" in fields

    def test_multi_name_field_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
struct P {
    int a, b;
}
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "P.a" in fields
        assert "P.b" in fields

    def test_nested_struct_field_owner(self, tmp_path: Path) -> None:
        # A field of a nested struct is owned by the NEAREST enclosing type.
        _write(tmp_path, '''
struct Outer {
    struct Inner {
        int x;
    }
    int y;
}
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "Inner.x" in fields
        assert "Outer.y" in fields
        assert "Outer.x" not in fields

    def test_enum_members_emitted_as_fields(self, tmp_path: Path) -> None:
        # Enum members are type members accessed via `.` (Color.red) — emit as
        # fields, consistent with the dart/zig/nim enum-body-value precedent.
        _write(tmp_path, '''
enum Color { red, green = 2, blue }
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "Color.red" in fields
        assert "Color.green" in fields
        assert "Color.blue" in fields


class TestDUnionFields:
    """A NAMED nested union declares a nested TYPE — its members belong to the
    union, not the enclosing struct/class (`U.a`, not `Outer.a`). An ANONYMOUS
    union hoists its members into the enclosing aggregate (D semantics), so those
    ARE members of the enclosing type. The field-owner walk must stop at a named
    union but pass through an anonymous one. Regression for the adversarial-review
    NO-GO (a wrong-owner phantom hidden by 100% line coverage — the union input
    flows through the same lines the struct-field tests cover).
    """

    def test_named_nested_union_owns_its_members(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
struct Outer {
    union U { int a; float b; }
    int c;
}
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "U.a" in fields
        assert "U.b" in fields
        assert "Outer.c" in fields
        # The union's members are NOT fields of Outer.
        assert "Outer.a" not in fields
        assert "Outer.b" not in fields

    def test_anonymous_union_members_hoisted(self, tmp_path: Path) -> None:
        # D injects anonymous-union members into the enclosing aggregate's scope,
        # so `anon.x` IS valid — the owner is the enclosing struct.
        _write(tmp_path, '''
struct Anon {
    union { int x; int y; }
    int z;
}
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "Anon.x" in fields
        assert "Anon.y" in fields
        assert "Anon.z" in fields

    def test_top_level_named_union_members(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
union TopU { int p; int q; }
''')
        fields = _names(analyze_d(tmp_path), "field")
        assert "TopU.p" in fields
        assert "TopU.q" in fields


class TestDModuleVariables:
    def test_module_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
int globalVar = 5;
string name;
''')
        variables = _names(analyze_d(tmp_path), "variable")
        assert "globalVar" in variables
        assert "name" in variables

    def test_auto_module_variable_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
auto answer = 42;
''')
        variables = _names(analyze_d(tmp_path), "variable")
        assert "answer" in variables

    def test_multi_name_module_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
int a = 1, b = 2;
''')
        variables = _names(analyze_d(tmp_path), "variable")
        assert "a" in variables
        assert "b" in variables

    def test_struct_declaration_is_not_a_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
struct Point {
    int x;
}
''')
        result = analyze_d(tmp_path)
        assert "Point" not in _names(result, "variable")
        assert "Point" in _names(result, "struct")

    def test_locals_are_not_module_variables(self, tmp_path: Path) -> None:
        # Function-body locals (variable_declaration under block_statement, or
        # auto_declaration there) must not be emitted as module variables.
        _write(tmp_path, '''
int compute(int a) {
    int localInt = 1;
    auto autoLocal = 9;
    return localInt + autoLocal + a;
}
''')
        variables = _names(analyze_d(tmp_path), "variable")
        assert "localInt" not in variables
        assert "autoLocal" not in variables


class TestDFieldVariableCallGraphIntegrity:
    def test_field_and_variable_not_call_targets(self, tmp_path: Path) -> None:
        # A module variable / field sharing a name with a function must NOT steal
        # the call: a call to the name resolves to the function.
        _write(tmp_path, '''
int value = 0;

struct Box {
    string label;
}

int value_fn() {
    return 1;
}

int caller() {
    return value_fn();
}
''')
        result = analyze_d(tmp_path)
        assert "value" in _names(result, "variable")
        assert "Box.label" in _names(result, "field")
        var_ids = {s.id for s in result.symbols if s.kind == "variable"}
        field_ids = {s.id for s in result.symbols if s.kind == "field"}
        anchor_ids = var_ids | field_ids
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in anchor_ids]
        assert bad == [], f"a call resolved to a data anchor: {bad}"


class TestDFieldVariableFailsafe:
    def test_anonymous_enum_members_skipped(self, tmp_path: Path) -> None:
        # An anonymous enum (`enum { A, B }`) has no owner name — its members are
        # skipped (a fails-safe recall miss, never a wrong-owner phantom).
        _write(tmp_path, '''
enum { ALPHA, BETA }
''')
        result = analyze_d(tmp_path)
        fields = _names(result, "field")
        assert not any(f.endswith(".ALPHA") or f.endswith(".BETA") for f in fields)
