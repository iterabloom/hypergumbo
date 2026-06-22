# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5, slice 2): JS/TS module-level variable symbols.

Before this slice the JS/TS analyzer emitted no ``variable`` symbols, so
module constants and module state (``const MAX = ...``, ``let ws``) had no
node in the symbol graph — invisible to search, centrality, and
io-boundaries. This slice emits ``kind="variable"`` symbols for top-level
(module-scope) ``const``/``let``/``var`` declarations, mirroring the Python
analyzer's module-level-only contract. Class fields (slice 1) and
function-valued declarations (the existing arrow/function path) are handled
elsewhere; destructuring patterns and function-body locals are out of scope.
"""

from pathlib import Path

from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content)


def _vars(result):
    return [s for s in result.symbols if s.kind == "variable"]


class TestModuleVariableEmission:
    def test_module_const_emits_variable(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.ts", "const MAX = 100;\n")
        result = analyze_javascript(tmp_path)
        v = next((s for s in _vars(result) if s.name == "MAX"), None)
        assert v is not None
        assert v.language == "typescript"
        assert _CANONICAL_STABLE_ID_PATTERN.match(v.stable_id), v.stable_id
        assert v.id.rsplit(":", 1)[-1] == "variable", v.id
        assert v.is_exported is False

    def test_exported_const_is_exported(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.ts", "export const PORT = 8080;\n")
        result = analyze_javascript(tmp_path)
        v = next(s for s in _vars(result) if s.name == "PORT")
        assert v.is_exported is True

    def test_typed_variable_signature(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.ts", "const ratio: number = 1.5;\n")
        result = analyze_javascript(tmp_path)
        v = next(s for s in _vars(result) if s.name == "ratio")
        assert v.signature is not None and "number" in v.signature

    def test_let_without_initializer(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.ts", "let ws;\n")
        result = analyze_javascript(tmp_path)
        assert any(s.name == "ws" for s in _vars(result))

    def test_var_declaration(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.js", "var counter = 0;\n")
        result = analyze_javascript(tmp_path)
        v = next((s for s in _vars(result) if s.name == "counter"), None)
        assert v is not None
        assert v.language == "javascript"

    def test_multi_declarator(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.ts", "const a = 1, b = 2;\n")
        result = analyze_javascript(tmp_path)
        names = {s.name for s in _vars(result)}
        assert {"a", "b"} <= names, names

    def test_same_name_var_different_file_distinct_stable_id(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.ts", "const SHARED = 1;\n")
        _write(tmp_path, "b.ts", "const SHARED = 1;\n")
        result = analyze_javascript(tmp_path)
        shared = [s for s in _vars(result) if s.name == "SHARED"]
        assert len(shared) == 2
        assert shared[0].stable_id != shared[1].stable_id


class TestModuleVariableExclusions:
    def test_destructuring_is_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.ts", "const { a, b } = obj;\nconst [x, y] = arr;\n")
        result = analyze_javascript(tmp_path)
        names = {s.name for s in _vars(result)}
        assert names.isdisjoint({"a", "b", "x", "y"}), names

    def test_function_body_local_not_emitted(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.ts", "function f() {\n  const local = 1;\n  return local;\n}\n")
        result = analyze_javascript(tmp_path)
        assert not any(s.name == "local" for s in _vars(result))

    def test_arrow_const_not_emitted_as_variable(self, tmp_path: Path) -> None:
        # const f = () => 1 stays a 'function' symbol — no duplicate 'variable'.
        _write(tmp_path, "m.ts", "const f = () => 1;\n")
        result = analyze_javascript(tmp_path)
        assert not any(s.name == "f" for s in _vars(result))
        assert any(s.name == "f" and s.kind == "function" for s in result.symbols)
