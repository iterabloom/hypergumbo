# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Zig field/variable symbol emission (WI-jusus emission-parity tail).

The Zig analyzer emitted functions/methods/structs/enums/unions but ZERO
``kind="field"`` (struct members) and ``kind="variable"`` (module-level
const/var) symbols. This slice adds them while keeping them out of the call
graph: field/variable are data anchors, never call targets, so they must not
enter the resolution registry (``register_symbol`` override) — otherwise a
bare-name variable could clobber a same-named function's slot.
"""
from pathlib import Path

from hypergumbo_lang_extended1.zig import analyze_zig


def _write(tmp_path: Path, body: str, name: str = "mod.zig") -> None:
    (tmp_path / name).write_text(body)


def _names(result, kind: str) -> set[str]:
    return {s.name for s in result.symbols if s.kind == kind}


class TestZigStructFields:
    def test_struct_fields_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
const Point = struct {
    x: f32,
    y: f32,
};
''')
        fields = _names(analyze_zig(tmp_path), "field")
        assert "Point.x" in fields
        assert "Point.y" in fields

    def test_enum_variants_emitted_as_fields(self, tmp_path: Path) -> None:
        # Enum variants are type members accessed via `.` (Color.red); emit
        # them as fields, consistent with the dart enum-body-value precedent.
        _write(tmp_path, '''
const Color = enum { red, green, blue };
const Tagged = enum(u8) { a = 1, b = 2 };
''')
        fields = _names(analyze_zig(tmp_path), "field")
        assert "Color.red" in fields
        assert "Color.blue" in fields
        assert "Tagged.a" in fields

    def test_nested_struct_field_owners(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
const Outer = struct {
    const Inner = struct { x: f32 };
    y: i32,
};
''')
        fields = _names(analyze_zig(tmp_path), "field")
        assert "Inner.x" in fields
        assert "Outer.y" in fields

    def test_union_fields_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
const Value = union(enum) {
    int: i64,
    float: f64,
};
''')
        fields = _names(analyze_zig(tmp_path), "field")
        assert "Value.int" in fields
        assert "Value.float" in fields


class TestZigModuleVariables:
    def test_module_const_and_var_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
const MAX_SIZE: u32 = 100;
var counter: i32 = 0;
''')
        variables = _names(analyze_zig(tmp_path), "variable")
        assert "MAX_SIZE" in variables
        assert "counter" in variables

    def test_type_declaration_is_not_a_variable(self, tmp_path: Path) -> None:
        # `const Point = struct {}` declares a TYPE, not a variable.
        _write(tmp_path, '''
const Point = struct { x: f32 };
const Color = enum { red, green };
''')
        result = analyze_zig(tmp_path)
        variables = _names(result, "variable")
        assert "Point" not in variables
        assert "Color" not in variables
        assert "Point" in _names(result, "struct")

    def test_import_alias_is_not_a_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
const std = @import("std");
const MAX: u32 = 5;
''')
        variables = _names(analyze_zig(tmp_path), "variable")
        assert "std" not in variables
        assert "MAX" in variables

    def test_locals_are_not_module_variables(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
fn compute(a: i32) i32 {
    const localConst = a + 1;
    var localVar: i32 = 2;
    return localConst + localVar;
}
''')
        variables = _names(analyze_zig(tmp_path), "variable")
        assert "localConst" not in variables
        assert "localVar" not in variables

    def test_struct_associated_const_deferred(self, tmp_path: Path) -> None:
        # A const inside a struct body is an associated constant, not a
        # module-level variable — deferred (fail-safe: not mis-emitted).
        _write(tmp_path, '''
const Point = struct {
    x: f32,
    const origin = Point{ .x = 0 };
};
''')
        result = analyze_zig(tmp_path)
        assert "origin" not in _names(result, "variable")
        assert "Point.origin" not in _names(result, "field")


class TestZigFieldVariableCallGraphIntegrity:
    def test_field_and_variable_not_call_targets(self, tmp_path: Path) -> None:
        # A struct field / module variable sharing a name with a function must
        # NOT steal the call: a call to the name resolves to the function.
        _write(tmp_path, '''
var value: i32 = 0;

fn value() i32 {
    return 1;
}

fn caller() i32 {
    return value();
}
''')
        result = analyze_zig(tmp_path)
        # The variable and function are both emitted...
        assert "value" in _names(result, "variable")
        assert "value" in _names(result, "function")
        # ...but no calls edge resolves to the variable symbol.
        var_ids = {s.id for s in result.symbols if s.kind == "variable"}
        field_ids = {s.id for s in result.symbols if s.kind == "field"}
        anchor_ids = var_ids | field_ids
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in anchor_ids]
        assert bad == [], f"a call resolved to a data anchor: {bad}"
