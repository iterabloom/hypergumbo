# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): Swift stored-property field + top-level variable symbols.

The Swift analyzer emitted computed properties (kind="property") but not STORED
properties or top-level value bindings — both are bare `property_declaration`
nodes (no `computed_property` child). This slice — a double gate strip — emits:

* ``kind="field"`` for a stored property inside a type body (class/struct/enum),
* ``kind="variable"`` for a top-level (`source_file`-scope) ``let``/``var``.

Computed properties stay ``kind="property"``; function-/closure-local bindings
are excluded (module-level-only contract, like the other variable emitters).
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.swift import analyze_swift


def _write(tmp_path: Path, content: str) -> None:
    (tmp_path / "m.swift").write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


def _vars(result):
    return [s for s in result.symbols if s.kind == "variable"]


class TestSwiftStoredPropertyFields:
    def test_stored_properties_emit_field(self, tmp_path: Path) -> None:
        _write(tmp_path, """
public class Service {
    public var count: Int = 0
    let name: String = "x"
    var computed: Int { return 1 }
}
""")
        result = analyze_swift(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"Service.count", "Service.name"}, names
        # the computed property stays kind="property", not field/variable
        assert not any(s.kind in ("field", "variable") and s.name == "Service.computed"
                       for s in result.symbols)

    def test_field_canonical_stable_id_signature_qualified(self, tmp_path: Path) -> None:
        _write(tmp_path, "class Service {\n    var repo: Repo = Repo()\n}\n")
        result = analyze_swift(tmp_path)
        f = next(s for s in _fields(result) if s.name == "Service.repo")
        assert f.language == "swift"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.signature == "Repo", f.signature
        assert f.qualified_name.endswith("Service.repo"), f.qualified_name
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_field_exportedness(self, tmp_path: Path) -> None:
        _write(tmp_path, """
public class Service {
    public var shown: Int = 0
    var hidden: Int = 0
}
""")
        result = analyze_swift(tmp_path)
        by = {f.name: f for f in _fields(result)}
        assert by["Service.shown"].is_exported is True
        assert by["Service.hidden"].is_exported is False

    def test_struct_stored_property_emits_field(self, tmp_path: Path) -> None:
        _write(tmp_path, "struct Point {\n    var x: Int = 0\n}\n")
        result = analyze_swift(tmp_path)
        assert any(f.name == "Point.x" for f in _fields(result))


class TestSwiftTopLevelVariables:
    def test_top_level_let_var_emit_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, """
let MaxItems = 5
var counter = 0
""")
        result = analyze_swift(tmp_path)
        names = {v.name for v in _vars(result)}
        assert {"MaxItems", "counter"} <= names, names
        mx = next(v for v in _vars(result) if v.name == "MaxItems")
        assert _CANONICAL_STABLE_ID_PATTERN.match(mx.stable_id), mx.stable_id
        assert mx.id.rsplit(":", 1)[-1] == "variable", mx.id

    def test_top_level_typed_variable_signature(self, tmp_path: Path) -> None:
        _write(tmp_path, 'let greeting: String = "hi"\n')
        result = analyze_swift(tmp_path)
        v = next(x for x in _vars(result) if x.name == "greeting")
        assert v.signature == "String", v.signature

    def test_top_level_exportedness(self, tmp_path: Path) -> None:
        _write(tmp_path, """
public let SHOWN = 1
let HIDDEN = 2
""")
        result = analyze_swift(tmp_path)
        by = {v.name: v for v in _vars(result)}
        assert by["SHOWN"].is_exported is True
        assert by["HIDDEN"].is_exported is False

    def test_function_local_let_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, """
func run() -> Int {
    let local = 9
    return local
}
""")
        result = analyze_swift(tmp_path)
        assert not any(v.name == "local" for v in _vars(result))


class TestSwiftMethodLocalExclusion:
    """INV-lanaz regression: a function-local ``let``/``var`` inside a *method*
    of a type parses as a ``property_declaration`` and (unlike a top-level
    function's local) DOES have an enclosing type, so the original
    ``_get_enclosing_type``-only gate misclassified it as ``kind="field"``.

    Ground truth: on SwiftyJSON, 383 of 407 emitted swift "fields" were XCTest
    method-body locals. A stored property is a field ONLY when it is a DIRECT
    member of the type body (parent ``class_body``/``enum_class_body``); a
    binding nested in a method/init/closure body (parent ``statements``) is a
    local and must be excluded.
    """

    def test_method_local_let_not_emitted_as_field(self, tmp_path: Path) -> None:
        _write(tmp_path, """
class ArrayTests {
    let stored = 1
    func testGetter() {
        let local = [1, 2, 3]
        var localVar = 0
        localVar += 1
        print(local, localVar)
    }
}
""")
        result = analyze_swift(tmp_path)
        fnames = {f.name for f in _fields(result)}
        assert "ArrayTests.stored" in fnames, fnames
        assert "ArrayTests.local" not in fnames, fnames
        assert "ArrayTests.localVar" not in fnames, fnames
        # and they must not leak in as top-level variables either
        vnames = {v.name for v in _vars(result)}
        assert "local" not in vnames and "localVar" not in vnames, vnames

    def test_init_local_not_emitted_as_field(self, tmp_path: Path) -> None:
        _write(tmp_path, """
struct S {
    let kept = 1
    init() {
        let tmp = 2
        print(tmp)
    }
}
""")
        result = analyze_swift(tmp_path)
        fnames = {f.name for f in _fields(result)}
        assert "S.kept" in fnames, fnames
        assert "S.tmp" not in fnames, fnames

    def test_closure_local_not_emitted_as_field(self, tmp_path: Path) -> None:
        _write(tmp_path, """
class C {
    let kept = 1
    func work() -> Int {
        let f = { () -> Int in
            let closureLocal = 6
            return closureLocal
        }
        return f()
    }
}
""")
        result = analyze_swift(tmp_path)
        fnames = {f.name for f in _fields(result)}
        assert "C.kept" in fnames, fnames
        assert "C.closureLocal" not in fnames and "C.f" not in fnames, fnames

    def test_stored_property_kept_when_type_has_method_locals(self, tmp_path: Path) -> None:
        # Mirrors the SwiftyJSON failure: stored props kept; method-body locals
        # and computed properties dropped from the field set.
        _write(tmp_path, """
struct JSON {
    var rawArray: [Any] = []
    var string: String? { return nil }
    func describe() -> String {
        let buffer = "x"
        return buffer
    }
}
""")
        result = analyze_swift(tmp_path)
        fnames = {f.name for f in _fields(result)}
        assert "JSON.rawArray" in fnames, fnames
        assert "JSON.buffer" not in fnames, fnames
        assert "JSON.string" not in fnames, fnames  # computed -> property, not field
