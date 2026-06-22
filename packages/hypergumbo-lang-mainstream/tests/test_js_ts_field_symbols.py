# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5, slice 1): JS/TS class-field symbols.

Before this slice the JS/TS analyzer emitted ZERO ``variable``/``field``
symbols, so class fields (``public_field_definition`` in TS,
``field_definition`` in JS) had no anchor and field-level decorators
(lit's ``@property``/``@state`` reactive-property decorators) had nothing
to attach a ``decorated_by`` edge to. These tests lock the field-symbol
emission and the field-decorator edge wiring.
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content)


def _fields(result):
    return [s for s in result.symbols if s.kind == "field"]


class TestTsClassFieldSymbols:
    def test_ts_fields_emit_field_kind_symbols(self, tmp_path: Path) -> None:
        _write(tmp_path, "el.ts", """
export class MyEl {
  count: number = 0;
  name: string = "x";
  private handlers: Map<string, number> = new Map();
}
""")
        result = analyze_javascript(tmp_path)
        fields = _fields(result)
        names = {f.name for f in fields}
        assert names == {"MyEl.count", "MyEl.name", "MyEl.handlers"}, names

    def test_field_symbol_has_canonical_stable_id_and_qualified_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "el.ts", "export class C { value: number = 1; }")
        result = analyze_javascript(tmp_path)
        f = next(s for s in _fields(result) if s.name == "C.value")
        assert f.language == "typescript"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id
        assert f.qualified_name == "C.value"
        # node.id carries the 'field' kind slot
        assert f.id.rsplit(":", 1)[-1] == "field", f.id

    def test_field_signature_is_type_annotation(self, tmp_path: Path) -> None:
        _write(tmp_path, "el.ts", "export class C { items: string[] = []; }")
        result = analyze_javascript(tmp_path)
        f = next(s for s in _fields(result) if s.name == "C.items")
        assert f.signature is not None and "string[]" in f.signature

    def test_field_modifiers_captured(self, tmp_path: Path) -> None:
        _write(tmp_path, "el.ts", """
export class C {
  static readonly MAX = 100;
  private secret = 1;
  plain = 2;
}
""")
        result = analyze_javascript(tmp_path)
        by_name = {f.name: f for f in _fields(result)}
        assert set(by_name["C.MAX"].modifiers) >= {"static", "readonly"}
        assert "private" in by_name["C.secret"].modifiers
        assert by_name["C.plain"].modifiers == []

    def test_computed_field_name_is_skipped(self, tmp_path: Path) -> None:
        # A computed field name ([Symbol.iterator] = ...) has no stable string
        # identity (computed_property_name, not property_identifier), so it emits
        # no field symbol; the sibling named field still does.
        _write(tmp_path, "el.ts", """
export class C {
  [Symbol.iterator] = 1;
  normal = 2;
}
""")
        result = analyze_javascript(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"C.normal"}, names

    def test_same_field_name_different_class_distinct_stable_id(self, tmp_path: Path) -> None:
        _write(tmp_path, "el.ts", """
export class A { x: number = 1; }
export class B { x: number = 1; }
""")
        result = analyze_javascript(tmp_path)
        a = next(s for s in _fields(result) if s.name == "A.x")
        b = next(s for s in _fields(result) if s.name == "B.x")
        assert a.stable_id != b.stable_id
        assert a.id != b.id


class TestJsClassFieldSymbols:
    def test_js_field_definition_emits_field_symbol(self, tmp_path: Path) -> None:
        _write(tmp_path, "c.js", """
export class Counter {
  count = 0;
  static MAX = 9;
}
""")
        result = analyze_javascript(tmp_path)
        names = {f.name for f in _fields(result)}
        assert names == {"Counter.count", "Counter.MAX"}, names
        f = next(s for s in _fields(result) if s.name == "Counter.count")
        assert f.language == "javascript"
        assert _CANONICAL_STABLE_ID_PATTERN.match(f.stable_id), f.stable_id

    def test_js_private_field(self, tmp_path: Path) -> None:
        _write(tmp_path, "c.js", "export class C { #secret = 1; }")
        result = analyze_javascript(tmp_path)
        names = {f.name for f in _fields(result)}
        assert "C.#secret" in names, names


class TestFieldDecoratorEdges:
    def test_lit_field_decorators_emit_decorated_by_edges(self, tmp_path: Path) -> None:
        # @property / @state are imported from lit (not defined in-file), so the
        # decorated_by edge resolves UNRESOLVED — but it must now exist, anchored
        # on the field symbol (previously there was no field symbol to anchor).
        _write(tmp_path, "el.ts", """
import { LitElement } from 'lit';
import { property, state } from 'lit/decorators.js';

export class MyEl extends LitElement {
  @property({ type: Number }) count = 0;
  @state() private _open = false;
}
""")
        result = analyze_javascript(tmp_path)
        fields = {f.name: f for f in _fields(result)}
        count_id = fields["MyEl.count"].id
        open_id = fields["MyEl._open"].id
        deco_edges = [e for e in result.edges if e.edge_type == "decorated_by"]
        # field 'count' decorated by @property; '_open' by @state
        count_edges = [e for e in deco_edges if e.src == count_id]
        open_edges = [e for e in deco_edges if e.src == open_id]
        assert any("property" in e.dst for e in count_edges), count_edges
        assert any("state" in e.dst for e in open_edges), open_edges
        # decorators are recorded in the field symbol meta too
        assert fields["MyEl.count"].meta is not None
        assert any(d.get("name") == "property"
                   for d in fields["MyEl.count"].meta.get("decorators", []))
