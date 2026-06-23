# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): C# class/struct field symbols.

The C# analyzer emitted properties (kind="property") but no field-kind symbols,
so plain fields and their attributes had no node in the symbol graph. This slice
emits kind="field" per field_declaration declarator, mirroring the
method_declaration path, and the field's C# attribute metadata flows through the
existing _extract_attribute_edges decorated_by wiring (DI [Inject], EF ORM
[Column]/[Key]). Properties remain a separate kind="property".
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.csharp import analyze_csharp


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


class TestCsharpFieldSymbols:
    def test_fields_emit_field_kind_symbols(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.cs", """
public class Svc {
    private Repo repo;
    public string label = "x";
    public int Prop { get; set; }
}
""")
        result = analyze_csharp(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"Svc.repo", "Svc.label"}, names
        # the property stays kind="property", not field
        assert not any(f.name == "Svc.Prop" for f in _fields(result))

    def test_field_canonical_stable_id_and_qualified_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.cs", "public class Svc { private int value; }")
        result = analyze_csharp(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Svc.value")
        assert f.language == "csharp"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.qualified_name.endswith("Svc.value"), f.qualified_name
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_field_signature_is_type(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.cs", "public class Svc { private Repo repo; }")
        result = analyze_csharp(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Svc.repo")
        assert f.signature == "Repo", f.signature

    def test_generic_field_type(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.cs", "public class Svc { private System.Collections.Generic.List<string> names; }")
        result = analyze_csharp(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Svc.names")
        assert f.signature is not None and "List" in f.signature

    def test_exportedness(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.cs", """
public class Svc {
    public int shown;
    private int hidden;
}
""")
        result = analyze_csharp(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert by["Svc.shown"].is_exported is True
        assert by["Svc.hidden"].is_exported is False

    def test_multi_declarator(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.cs", "public class Svc { private int a, b; }")
        result = analyze_csharp(tmp_path)
        names = {f.name for f in _fields(result)}
        assert {"Svc.a", "Svc.b"} <= names, names

    def test_same_field_name_different_class_distinct_stable_id(self, tmp_path: Path) -> None:
        _write(tmp_path, "A.cs", "public class A { private int x; }")
        _write(tmp_path, "B.cs", "public class B { private int x; }")
        result = analyze_csharp(tmp_path)
        a = next(f for f in _fields(result) if f.name == "A.x")
        b = next(f for f in _fields(result) if f.name == "B.x")
        assert a.stable_id != b.stable_id
        assert a.id != b.id


class TestCsharpFieldAttributeEdges:
    def test_field_attributes_emit_decorated_by(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.cs", """
public class Svc {
    [Inject]
    private Repo repo;

    [Column("n")]
    public string Label;
}
""")
        result = analyze_csharp(tmp_path)
        fields = {f.name: f for f in _fields(result)}
        deco = [e for e in result.edges if e.edge_type == "decorated_by"]
        repo_edges = [e for e in deco if e.src == fields["Svc.repo"].id]
        label_edges = [e for e in deco if e.src == fields["Svc.Label"].id]
        assert any("Inject" in e.dst for e in repo_edges), repo_edges
        assert any("Column" in e.dst for e in label_edges), label_edges
        assert any(a.get("name") == "Inject"
                   for a in (fields["Svc.repo"].meta or {}).get("annotations", []))
