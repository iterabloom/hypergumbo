# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): Rust struct-field AND module-variable symbols.

The Rust analyzer emitted no field-kind symbols (struct fields invisible) and no
variable-kind symbols (module-level `const`/`static` invisible). This slice — a
double gate strip — emits:

* ``kind="field"`` per NAMED struct field (tuple structs have positional fields,
  no names, so no field symbols), and
* ``kind="variable"`` per module-level ``const``/``static`` (top-level or inside
  a ``mod`` block; function-body locals and impl-associated consts are excluded,
  mirroring the module-level-only contract of the other variable emitters).
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.rust import analyze_rust


def _write(tmp_path: Path, content: str) -> None:
    (tmp_path / "m.rs").write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


def _vars(result):
    return [s for s in result.symbols if s.kind == "variable"]


class TestRustStructFields:
    def test_struct_fields_emit(self, tmp_path: Path) -> None:
        _write(tmp_path, """
pub struct Service {
    pub name: String,
    count: i32,
}
""")
        result = analyze_rust(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"Service::name", "Service::count"}, names

    def test_field_canonical_stable_id_signature_qualified(self, tmp_path: Path) -> None:
        _write(tmp_path, "pub struct Service {\n    repo: Repo,\n}\n")
        result = analyze_rust(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Service::repo")
        assert f.language == "rust"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.signature == "Repo", f.signature
        assert f.qualified_name.endswith("Service::repo"), f.qualified_name
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_field_exportedness(self, tmp_path: Path) -> None:
        _write(tmp_path, """
pub struct Service {
    pub shown: i32,
    hidden: i32,
}
""")
        result = analyze_rust(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert by["Service::shown"].is_exported is True
        assert by["Service::hidden"].is_exported is False

    def test_tuple_struct_has_no_field_symbols(self, tmp_path: Path) -> None:
        _write(tmp_path, "pub struct Wrapper(i32, String);\n")
        result = analyze_rust(tmp_path)
        assert _fields(result) == []

    def test_same_field_name_different_struct_distinct(self, tmp_path: Path) -> None:
        _write(tmp_path, """
pub struct A { x: i32 }
pub struct B { x: i32 }
""")
        result = analyze_rust(tmp_path)
        a = next(f for f in _fields(result) if f.name == "A::x")
        b = next(f for f in _fields(result) if f.name == "B::x")
        assert a.stable_id != b.stable_id
        assert a.id != b.id


class TestRustModuleVariables:
    def test_const_and_static_emit_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, """
const MAX: i32 = 5;
static GREETING: &str = "hi";
""")
        result = analyze_rust(tmp_path)
        names = {v.name for v in _vars(result)}
        assert {"MAX", "GREETING"} <= names, names
        mx = next(v for v in _vars(result) if v.name == "MAX")
        assert mx.language == "rust"
        assert _CANONICAL_STABLE_ID_PATTERN.match(mx.stable_id), mx.stable_id
        assert mx.signature == "i32", mx.signature
        assert mx.id.rsplit(":", 1)[-1] == "variable", mx.id

    def test_const_exportedness(self, tmp_path: Path) -> None:
        _write(tmp_path, """
pub const SHOWN: i32 = 1;
const HIDDEN: i32 = 2;
""")
        result = analyze_rust(tmp_path)
        by = {v.name: v for v in _vars(result)}
        assert by["SHOWN"].is_exported is True
        assert by["HIDDEN"].is_exported is False

    def test_mod_level_const_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
mod config {
    pub const TIMEOUT: i32 = 30;
}
""")
        result = analyze_rust(tmp_path)
        assert any(v.name == "TIMEOUT" for v in _vars(result))

    def test_function_local_const_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
fn run() -> i32 {
    const LOCAL: i32 = 9;
    LOCAL
}
""")
        result = analyze_rust(tmp_path)
        assert not any(v.name == "LOCAL" for v in _vars(result))

    def test_impl_associated_const_not_a_module_variable(self, tmp_path: Path) -> None:
        # An impl-associated const is a type member, not a module-level binding.
        _write(tmp_path, """
pub struct S;
impl S {
    const ASSOC: i32 = 7;
}
""")
        result = analyze_rust(tmp_path)
        assert not any(v.name == "ASSOC" for v in _vars(result))
