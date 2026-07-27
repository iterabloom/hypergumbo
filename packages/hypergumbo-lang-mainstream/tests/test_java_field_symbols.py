# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): Java class-field symbols.

Before this slice the Java analyzer emitted no ``field``-kind Symbols, so class
fields (and their constants) had no node in the symbol graph and field-level
annotations — Spring ``@Autowired``/``@Inject`` (DI), JPA ``@Column`` (ORM) —
had nothing to attach a ``decorated_by`` edge to. This slice emits
``kind="field"`` for every ``field_declaration`` declarator, mirroring the
``method_declaration`` path, and the field's annotation metadata flows through
the existing ``_extract_annotation_edges`` decorated_by wiring.
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.java import analyze_java


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


class TestJavaFieldSymbols:
    def test_fields_emit_field_kind_symbols(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.java", """
public class Svc {
    private Repo repo;
    public String label = "x";
    void m() {}
}
""")
        result = analyze_java(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"Svc.repo", "Svc.label"}, names

    def test_field_canonical_stable_id_and_qualified_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.java", "public class Svc { private int value; }")
        result = analyze_java(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Svc.value")
        assert f.language == "java"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.qualified_name.endswith("Svc.value"), f.qualified_name
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_field_signature_is_type(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.java", "public class Svc { private Repo repo; }")
        result = analyze_java(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Svc.repo")
        assert f.signature == "Repo", f.signature

    def test_field_modifiers_and_exportedness(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.java", """
public class Svc {
    static final int MAX = 5;
    private int hidden = 0;
    public String shown = "y";
}
""")
        result = analyze_java(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert set(by["Svc.MAX"].modifiers) >= {"static", "final"}
        assert by["Svc.hidden"].is_exported is False
        assert by["Svc.shown"].is_exported is True

    def test_multi_declarator_field(self, tmp_path: Path) -> None:
        _write(tmp_path, "Svc.java", "public class Svc { int a, b; }")
        result = analyze_java(tmp_path)
        names = {f.name for f in _fields(result)}
        assert {"Svc.a", "Svc.b"} <= names, names

    def test_interface_constants_emit_and_are_exported(self, tmp_path: Path) -> None:
        # Interface constants parse as `constant_declaration` (not
        # field_declaration) and are implicitly public static final.
        _write(tmp_path, "Cfg.java", """
public interface Cfg {
    int MAX = 5;
    String NAME = "x";
}
""")
        result = analyze_java(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert {"Cfg.MAX", "Cfg.NAME"} <= set(by), set(by)
        assert by["Cfg.MAX"].is_exported is True
        assert _CANONICAL_STABLE_ID_PATTERN.match(by["Cfg.MAX"].stable_id)

    def test_enum_body_field_emits(self, tmp_path: Path) -> None:
        _write(tmp_path, "E.java", "public enum E { A, B; private int count = 0; }")
        result = analyze_java(tmp_path)
        assert any(f.name == "E.count" for f in _fields(result))

    def test_same_field_name_different_class_distinct_stable_id(self, tmp_path: Path) -> None:
        _write(tmp_path, "A.java", "public class A { int x; }")
        _write(tmp_path, "B.java", "class B { int x; }")
        result = analyze_java(tmp_path)
        a = next(f for f in _fields(result) if f.name == "A.x")
        b = next(f for f in _fields(result) if f.name == "B.x")
        assert a.stable_id != b.stable_id
        assert a.id != b.id


class TestJavaFieldAnnotationEdges:
    def test_field_annotations_emit_decorated_by(self, tmp_path: Path) -> None:
        # Spring @Autowired (DI) / JPA @Column (ORM) on fields are non-standard
        # annotations: they emit unresolved decorated_by edges anchored on the
        # field symbol (previously there was no field symbol to anchor).
        _write(tmp_path, "Svc.java", """
public class Svc {
    @Autowired
    private Repo repo;

    @Column(name = "n")
    public String label;
}
""")
        result = analyze_java(tmp_path)
        fields = {f.name: f for f in _fields(result)}
        deco = [e for e in result.edges if e.edge_type == "decorated_by"]
        repo_edges = [e for e in deco if e.src == fields["Svc.repo"].id]
        label_edges = [e for e in deco if e.src == fields["Svc.label"].id]
        assert any("Autowired" in e.dst for e in repo_edges), repo_edges
        assert any("Column" in e.dst for e in label_edges), label_edges
        # and recorded in the field meta
        assert any(d.get("name") == "Autowired"
                   for d in (fields["Svc.repo"].meta or {}).get("decorators", []))
