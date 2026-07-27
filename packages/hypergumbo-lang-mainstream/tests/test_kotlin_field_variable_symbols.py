# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): Kotlin field/variable symbols.

The Kotlin analyzer emitted functions/classes/objects but no symbol for a
``val``/``var`` ``property_declaration``, so module constants, module state and
class fields had no anchor in the graph. This slice emits:

  * ``kind="field"`` for a property declared in a class/object/interface body
    (name ``Class.prop``, class-scoped ``qualified_name`` + typed stable_id);
  * ``kind="variable"`` for a top-level (module) property; and
  * NOTHING for a function-local ``val``/``var`` — a local binding is not an API
    surface. This is the swift/go INV-lanaz/INV-sidab regression trap: a naive
    ``property_declaration`` branch leaks every function-local ``val`` as a
    module variable. Scope is decided by the nearest body-defining ancestor.
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

_SAMPLE = """
package com.example

const val topLevelConst = 42
var topLevelVar = "hello"

class Widget {
    val name: String = "x"
    var size = 1
    private val secret = 99

    fun area(): Int {
        val local = size * size
        return local
    }
}

object Config {
    val version = "1.0"
}
"""


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


def _variables(result):
    return [s for s in result.symbols if s.kind == "variable"]


class TestKotlinFieldVariableSymbols:
    def test_class_body_properties_emit_field_kind(self, tmp_path: Path) -> None:
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {
            "Widget.name",
            "Widget.size",
            "Widget.secret",
            "Config.version",
        }, names

    def test_top_level_properties_emit_variable_kind(self, tmp_path: Path) -> None:
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        names = {v.name for v in _variables(result)}
        assert names == {"topLevelConst", "topLevelVar"}, names

    def test_function_local_val_not_emitted(self, tmp_path: Path) -> None:
        """The regression trap: a function-local ``val`` is a local binding,
        not a field or a module variable, and must not be emitted."""
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        assert not any(
            s.name in ("local", "Widget.local", "area.local")
            for s in result.symbols
        ), [s.name for s in result.symbols]
        assert not any(
            s.kind in ("field", "variable") and s.name.endswith("local")
            for s in result.symbols
        )

    def test_field_signature_is_type_annotation(self, tmp_path: Path) -> None:
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        name_field = next(f for f in _fields(result) if f.name == "Widget.name")
        assert name_field.signature == "String", name_field.signature
        # inferred type -> no annotation -> no signature
        size_field = next(f for f in _fields(result) if f.name == "Widget.size")
        assert size_field.signature is None, size_field.signature

    def test_field_canonical_stable_id_and_qualified_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Widget.name")
        assert f.language == "kotlin"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.qualified_name == "com.example.Widget.name", f.qualified_name
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_variable_stable_id_and_qualified_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        v = next(s for s in _variables(result) if s.name == "topLevelConst")
        assert v.language == "kotlin"
        assert v.stable_id and v.stable_id.startswith("sha256:"), v.stable_id
        assert v.qualified_name == "com.example.topLevelConst", v.qualified_name
        assert v.id.rsplit(":", 1)[-1] == "variable", v.id

    def test_exportedness_from_visibility(self, tmp_path: Path) -> None:
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert by["Widget.name"].is_exported is True
        assert by["Widget.secret"].is_exported is False

    def test_interface_body_properties_emit_field_kind(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "Repo.kt",
            "package com.example\ninterface Repo {\n    val name: String\n    val id: Int\n}",
        )
        result = analyze_kotlin(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert {"Repo.name", "Repo.id"} <= set(by), set(by)
        assert by["Repo.name"].qualified_name == "com.example.Repo.name"
        assert by["Repo.name"].signature == "String"

    def test_object_body_property_is_field(self, tmp_path: Path) -> None:
        _write(tmp_path, "Sample.kt", _SAMPLE)
        result = analyze_kotlin(tmp_path)
        version = next(f for f in _fields(result) if f.name == "Config.version")
        assert version.qualified_name == "com.example.Config.version"

    def test_same_variable_name_different_files_distinct_stable_id(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "A.kt", "val shared = 1")
        _write(tmp_path, "B.kt", "val shared = 2")
        result = analyze_kotlin(tmp_path)
        vs = [v for v in _variables(result) if v.name == "shared"]
        assert len(vs) == 2, vs
        assert vs[0].stable_id != vs[1].stable_id
        assert vs[0].id != vs[1].id

    def test_anonymous_object_member_not_emitted_as_field(
        self, tmp_path: Path
    ) -> None:
        """A member of an anonymous ``object { ... }`` literal has no named
        owner and is skipped; the top-level binding it is assigned to is a
        module variable."""
        _write(tmp_path, "H.kt", "val handler = object { val flag = true }")
        result = analyze_kotlin(tmp_path)
        assert not any(s.name == "flag" for s in result.symbols)
        assert any(
            v.name == "handler" for v in _variables(result)
        ), [v.name for v in _variables(result)]

    def test_init_block_local_not_emitted_as_field(self, tmp_path: Path) -> None:
        """Regression guard: a ``val`` inside an ``init { }`` block sits under
        ``block -> anonymous_initializer -> class_body``. Without ``block`` in the
        local-scope set it would wrongly reach ``class_body`` and emit as a field
        — the swift/go INV-lanaz/INV-sidab trap in Kotlin form."""
        _write(
            tmp_path,
            "W.kt",
            "class W {\n    val keep = 1\n    init { val scratch = keep + 1 }\n}",
        )
        result = analyze_kotlin(tmp_path)
        field_names = {f.name for f in _fields(result)}
        assert "W.keep" in field_names, field_names
        assert not any(
            s.name in ("scratch", "W.scratch") for s in result.symbols
        ), [s.name for s in result.symbols]

    def test_field_annotations_flow_into_meta_decorators(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "Svc.kt",
            "class Svc {\n    @JvmField val cfg: Config = load()\n}",
        )
        result = analyze_kotlin(tmp_path)
        cfg = next(f for f in _fields(result) if f.name == "Svc.cfg")
        decorators = (cfg.meta or {}).get("decorators", [])
        assert any("JvmField" in str(d) for d in decorators), cfg.meta
