# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): Go struct-field symbols.

The Go analyzer emitted package-level variables but no ``field``-kind Symbols,
so struct fields — Go's primary data-modeling construct — had no node in the
symbol graph. This slice emits ``kind="field"`` for every NAMED struct field
(embeddings carry no field name and remain ``base_classes``, not fields),
mirroring the func/method emission path (class-scoped qualified_name, canonical
``make_typed_stable_id``, declared type as ``signature``, capitalization-derived
``is_exported``).
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.go import analyze_go


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


class TestGoStructFieldSymbols:
    def test_struct_fields_emit_field_symbols(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.go", """package main

type Service struct {
    Name  string
    count int
}
""")
        result = analyze_go(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"Service.Name", "Service.count"}, names

    def test_field_canonical_stable_id_and_qualified_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.go", "package main\ntype Service struct {\n  value int\n}\n")
        result = analyze_go(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Service.value")
        assert f.language == "go"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.qualified_name.endswith("Service.value"), f.qualified_name
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_field_signature_is_type(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.go", "package main\ntype Service struct {\n  repo *Repo\n}\n")
        result = analyze_go(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Service.repo")
        assert f.signature is not None and "Repo" in f.signature

    def test_multi_name_field(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.go", "package main\ntype P struct {\n  a, b int\n}\n")
        result = analyze_go(tmp_path)
        names = {f.name for f in _fields(result)}
        assert {"P.a", "P.b"} <= names, names

    def test_exportedness_by_capitalization(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.go", """package main

type Service struct {
    Public  string
    private int
}
""")
        result = analyze_go(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert by["Service.Public"].is_exported is True
        assert by["Service.private"].is_exported is False

    def test_embedding_is_not_a_field(self, tmp_path: Path) -> None:
        # An embedded type (no field name) is base_classes, not a field symbol.
        _write(tmp_path, "m.go", """package main

type Base struct{}
type Service struct {
    Base
    Name string
}
""")
        result = analyze_go(tmp_path)
        names = {f.name for f in _fields(result)}
        assert "Service.Name" in names
        assert "Service.Base" not in names, names

    def test_same_field_name_different_struct_distinct_stable_id(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.go", """package main

type A struct {
    x int
}
type B struct {
    x int
}
""")
        result = analyze_go(tmp_path)
        a = next(f for f in _fields(result) if f.name == "A.x")
        b = next(f for f in _fields(result) if f.name == "B.x")
        assert a.stable_id != b.stable_id
        assert a.id != b.id
