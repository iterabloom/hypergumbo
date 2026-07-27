# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for F# field/variable symbol emission (WI-jusus emission-parity tail).

The F# analyzer emitted module/function/record/union symbols and `kind="value"`
for module `let` bindings, but ZERO `kind="field"` (record members) — and the
`value` emission had two defects: (1) it LEAKED function-body local `let`
bindings (no scope guard), and (2) `value` is an F#-only registered kind that
duplicates the cross-language `variable` convention every other analyzer
(scala/nim/d/zig/kotlin/dart/solidity) uses for module-level value declarations.

This slice: emits `record_field` members as `kind="field"` (owner = the record
type); reclassifies module `let` value bindings from `kind="value"` to
`kind="variable"` (`value` had no cross-package consumer and no test asserted
it); scope-guards the value emission to module scope so function-body locals are
excluded; and routes both data-anchor kinds through the `register_symbol`
chokepoint so they cannot clobber a same-named function.

Grammar facts (bundled `tree_sitter_language_pack` "fsharp"): a `record_field`'s
name is its first `identifier` child (a `mutable` modifier precedes it); a
module-level value's `function_or_value_defn` sits under
`declaration_expression > named_module|module_defn`, while a function-body local
sits under `declaration_expression > function_or_value_defn` (nested in the
enclosing binding).
"""
from pathlib import Path

from hypergumbo_lang_common.fsharp import analyze_fsharp


def _write(tmp_path: Path, body: str, name: str = "m.fs") -> None:
    (tmp_path / name).write_text(body)


def _names(result, kind: str) -> set[str]:
    return {s.name for s in result.symbols if s.kind == kind}


class TestFSharpRecordFields:
    def test_record_fields_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''module M
type Rec = { name: string; age: int }
''')
        fields = _names(analyze_fsharp(tmp_path), "field")
        assert "Rec.name" in fields
        assert "Rec.age" in fields

    def test_mutable_field_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''module M
type Counter = { mutable count: int }
''')
        fields = _names(analyze_fsharp(tmp_path), "field")
        assert "Counter.count" in fields


class TestFSharpModuleVariables:
    def test_module_value_emitted_as_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, '''module M
let topLevelVal = 30
''')
        result = analyze_fsharp(tmp_path)
        assert "topLevelVal" in _names(result, "variable")

    def test_no_value_kind_emitted(self, tmp_path: Path) -> None:
        # `value` is reclassified to `variable` — no symbol should carry the
        # F#-only `value` kind anymore.
        _write(tmp_path, '''module M
let topLevelVal = 30
''')
        result = analyze_fsharp(tmp_path)
        assert _names(result, "value") == set()

    def test_nested_module_value_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, '''module M
module Nested =
    let nestedVal = 5
''')
        result = analyze_fsharp(tmp_path)
        assert "nestedVal" in _names(result, "variable")

    def test_local_binding_not_emitted(self, tmp_path: Path) -> None:
        # A function-body `let` is the SAME function_or_value_defn node, but
        # nested under the enclosing binding — it must not leak as a variable.
        _write(tmp_path, '''module M
let compute x =
    let localBinding = x + 1
    localBinding
''')
        result = analyze_fsharp(tmp_path)
        assert "localBinding" not in _names(result, "variable")
        assert "localBinding" not in _names(result, "value")


class TestFSharpFieldCallGraphIntegrity:
    def test_field_not_a_call_target(self, tmp_path: Path) -> None:
        # A record field must NEVER be a calls-edge target: the NameResolver
        # suffix index would otherwise suffix-match a bare call to `Rec.name`.
        _write(tmp_path, '''module M
type Box = { label: string }

let helper x = x + 1

let caller y = helper y
''')
        result = analyze_fsharp(tmp_path)
        assert "Box.label" in _names(result, "field")
        field_ids = {s.id for s in result.symbols if s.kind == "field"}
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in field_ids]
        assert bad == [], f"a call resolved to a field: {bad}"

    def test_callable_value_binding_resolves(self, tmp_path: Path) -> None:
        # Unlike fields, an F# module `let` value binding IS legitimately
        # callable (lambda / partial application), so a call to it must resolve —
        # variables are kept in the resolution registry (F# has no same-name
        # value+function clobber).
        _write(tmp_path, '''module M
let add = fun a b -> a + b
let inc = (+) 1

let caller x =
    let r = add x 1
    inc r
''')
        result = analyze_fsharp(tmp_path)
        assert "add" in _names(result, "variable")
        assert "inc" in _names(result, "variable")
        by_name = {s.name: s.id for s in result.symbols}
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert by_name["add"] in call_dsts, "caller->add should resolve to the value binding"
        assert by_name["inc"] in call_dsts, "caller->inc should resolve to the value binding"


class TestFSharpHeaderlessFile:
    def test_headerless_top_level_value_emitted(self, tmp_path: Path) -> None:
        # A headerless `.fsx` script's top-level `let` is a module-level value:
        # the bundled grammar wraps it directly in `file` (no module node).
        _write(tmp_path, '''let scriptTop = 42
let apiKey = "secret"
''', name="script.fsx")
        variables = _names(analyze_fsharp(tmp_path), "variable")
        assert "scriptTop" in variables
        assert "apiKey" in variables

    def test_headerless_local_not_emitted(self, tmp_path: Path) -> None:
        # A function-body local in a headerless file has grandparent
        # `function_or_value_defn`, so it is still excluded (no leak).
        _write(tmp_path, '''let compute x =
    let localBinding = x + 1
    localBinding
''', name="script.fsx")
        result = analyze_fsharp(tmp_path)
        assert "localBinding" not in _names(result, "variable")
        assert "localBinding" not in _names(result, "value")
