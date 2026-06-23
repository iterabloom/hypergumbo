# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): Python class-attribute field symbols.

py.py emitted module-level variables (kind="variable") but no field-kind
symbols, so class attributes — including dataclass fields (`x: int`) — had no
node in the symbol graph. This slice emits kind="field" for class-body
Assign/AnnAssign targets (NOT instance attributes set in methods), class-scoped
via the class's file-anchored stable_id. It is the final F5 gate strip (every
emission-parity cell becomes a hard lock).
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.py import analyze_python


def _write(tmp_path: Path, content: str) -> None:
    (tmp_path / "m.py").write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


class TestPythonClassFields:
    def test_class_attributes_emit_field(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
class Service:
    count = 0
    name = "x"

    def run(self):
        return self.count
''')
        result = analyze_python(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"Service.count", "Service.name"}, names

    def test_annotated_and_bare_annotation_fields(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
class Config:
    timeout: int = 30
    label: str
''')
        result = analyze_python(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert {"Config.timeout", "Config.label"} <= set(by), set(by)
        assert by["Config.timeout"].signature == "int", by["Config.timeout"].signature

    def test_dataclass_fields_emit(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
from dataclasses import dataclass


@dataclass
class Point:
    x: int
    y: int = 0
''')
        result = analyze_python(tmp_path)
        names = {f.name for f in _fields(result)}
        assert {"Point.x", "Point.y"} <= names, names

    def test_field_canonical_stable_id_and_qualified(self, tmp_path: Path) -> None:
        _write(tmp_path, "class Service:\n    value = 1\n")
        result = analyze_python(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Service.value")
        assert f.language == "python"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.qualified_name.endswith("Service.value"), f.qualified_name
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_field_exportedness(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
class Service:
    public = 1
    _private = 2
''')
        result = analyze_python(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert by["Service.public"].is_exported is True
        assert by["Service._private"].is_exported is False

    def test_same_field_name_different_class_distinct(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
class A:
    x = 1


class B:
    x = 1
''')
        result = analyze_python(tmp_path)
        a = next(f for f in _fields(result) if f.name == "A.x")
        b = next(f for f in _fields(result) if f.name == "B.x")
        assert a.stable_id != b.stable_id
        assert a.id != b.id


class TestPythonClassFieldExclusions:
    def test_instance_attribute_not_emitted(self, tmp_path: Path) -> None:
        # self.x = ... inside __init__ is an instance attribute, not a class-body
        # field; it must NOT emit a field symbol.
        _write(tmp_path, '''
class Service:
    def __init__(self):
        self.instance_attr = 0
''')
        result = analyze_python(tmp_path)
        assert not any(f.name.endswith("instance_attr") for f in _fields(result))

    def test_method_is_not_a_field(self, tmp_path: Path) -> None:
        _write(tmp_path, '''
class Service:
    def run(self):
        return 1
''')
        result = analyze_python(tmp_path)
        assert _fields(result) == []

    def test_module_variable_is_not_a_field(self, tmp_path: Path) -> None:
        _write(tmp_path, "MAX = 5\n")
        result = analyze_python(tmp_path)
        assert _fields(result) == []
        assert any(s.kind == "variable" and s.name == "MAX" for s in result.symbols)
