# SPDX-License-Identifier: AGPL-3.0-or-later
"""``Symbol.is_exported`` for the JS/TS export shapes the analyzer used to misread.

Since INV-kubup the field is three-valued and the JS/TS analyzer writes
``False`` as a MEASURED "not exported". That made every declaration the export
scanner did not recognise a positive false claim. WI-sinol gates the
js-module-linker's ``module_exports`` edges on ``is_exported is True``, so each
shape pinned here is also a module_exports edge that would otherwise vanish.

- WI-tavag: CommonJS exports (``module.exports = { f }``, ``exports.h = h``,
  ``module.exports.m = m``, ``module.exports = f``,
  ``exports = module.exports = f``) set nothing, so every CommonJS export read
  False.
- ``export { local as alias }`` marked the ALIAS name, which names no local
  declaration, and left the local declaration False.
- ``export { x } from './y'`` re-exports a binding of ANOTHER module; it marked
  any same-named local declaration True.
- ``export abstract class`` / ``export interface`` / ``export enum`` /
  ``export type`` were not among the recognised declaration node types.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _exported(tmp_path: Path, name: str, content: str) -> dict[str, bool | None]:
    (tmp_path / name).write_text(content)
    result = analyze_javascript(tmp_path)
    return {
        s.name: s.is_exported
        for s in result.symbols
        if s.kind != "file" and s.path.endswith(name)
    }


class TestCommonJsExports:
    """WI-tavag: a CommonJS export is a POSITIVE export signal."""

    def test_object_literal_shorthand_and_pair(self, tmp_path: Path) -> None:
        got = _exported(tmp_path, "lib.js", (
            "function f() { return 1; }\n"
            "function g() { return 2; }\n"
            "function priv() { return 3; }\n"
            "module.exports = { f, renamed: g };\n"
        ))
        assert got["f"] is True
        assert got["g"] is True
        assert got["priv"] is False

    def test_object_literal_method_is_exported(self, tmp_path: Path) -> None:
        got = _exported(tmp_path, "lib.js", (
            "function bar() { return 0; }\n"
            "module.exports = {\n"
            "  foo() { return 1; },\n"
            "  [Symbol.iterator]() { return 2; },\n"
            "  bar: function () { return 3; },\n"
            "  ...other,\n"
            "};\n"
        ))
        assert got["foo"] is True
        # ``bar:`` names a function EXPRESSION, not the local ``bar``.
        assert got["bar"] is False

    def test_exports_and_module_exports_member(self, tmp_path: Path) -> None:
        got = _exported(tmp_path, "lib.js", (
            "function h() { return 1; }\n"
            "function m() { return 2; }\n"
            "function priv() { return 3; }\n"
            "exports.h = h;\n"
            "module.exports.alias = m;\n"
        ))
        assert got["h"] is True
        assert got["m"] is True
        assert got["priv"] is False

    def test_whole_module_is_one_binding(self, tmp_path: Path) -> None:
        got = _exported(tmp_path, "svc.js", (
            "class Svc { run() { return 1; } }\n"
            "function helper() { return 2; }\n"
            "module.exports = Svc;\n"
        ))
        assert got["Svc"] is True
        # A member of the exported class is reached through the class, not
        # exported by name (same rule as ESM ``export class``).
        assert got["Svc.run"] is False
        assert got["helper"] is False

    def test_chained_assignment(self, tmp_path: Path) -> None:
        """express's ``exports = module.exports = createApplication``."""
        got = _exported(tmp_path, "express.js", (
            "function createApplication() { return 1; }\n"
            "function helper() { return 2; }\n"
            "exports = module.exports = createApplication;\n"
        ))
        assert got["createApplication"] is True
        assert got["helper"] is False

    def test_reexport_by_require_marks_no_local(self, tmp_path: Path) -> None:
        got = _exported(tmp_path, "index.js", (
            "function local() { return 1; }\n"
            "module.exports = require('./other');\n"
        ))
        assert got["local"] is False

    @pytest.mark.parametrize("stmt", [
        "exports = local;\n",          # rebinding the local alias exports nothing
        "foo.exports = local;\n",      # not the module object
        "module.other = local;\n",     # not module.exports
        "exports[key] = local;\n",     # computed member: no static name
        "if (x) { module.exports = local; }\n",  # not a top-level statement
        "local();\n",                  # not an assignment at all
        "exports.cfg = { local };\n",  # a property OF an export, not one
    ])
    def test_non_export_assignments_mark_nothing(
        self, tmp_path: Path, stmt: str,
    ) -> None:
        got = _exported(tmp_path, "lib.js", (
            "function local() { return 1; }\n" + stmt
        ))
        assert got["local"] is False

    def test_commonjs_in_typescript(self, tmp_path: Path) -> None:
        got = _exported(tmp_path, "lib.ts", (
            "function f(): number { return 1; }\n"
            "module.exports = { f };\n"
        ))
        assert got["f"] is True


class TestEsmExportShapes:
    """Sibling gaps in the ESM half of the same scanner."""

    def test_aliased_export_marks_the_local_declaration(
        self, tmp_path: Path,
    ) -> None:
        got = _exported(tmp_path, "b.ts", (
            "function aliased() { return 1; }\n"
            "function pub() { return 2; }\n"
            "export { aliased as pub };\n"
        ))
        assert got["aliased"] is True
        # ``pub`` is the PUBLIC name of ``aliased``; the local ``pub`` is
        # not exported.
        assert got["pub"] is False

    def test_reexport_from_another_module_marks_no_local(
        self, tmp_path: Path,
    ) -> None:
        got = _exported(tmp_path, "b.ts", (
            "function reexp() { return 1; }\n"
            "export { reexp } from './other';\n"
        ))
        assert got["reexp"] is False

    def test_typescript_type_declarations(self, tmp_path: Path) -> None:
        got = _exported(tmp_path, "t.ts", (
            "export abstract class Base { run() { return 1; } }\n"
            "export interface I { x: number }\n"
            "export enum Color { Red }\n"
            "export type Alias = number;\n"
            "abstract class PrivBase {}\n"
            "interface PrivI { y: number }\n"
            "type PrivAlias = string;\n"
        ))
        assert got["Alias"] is True
        assert got["PrivAlias"] is False
        assert got["Base"] is True
        assert got["Base.run"] is False
        assert got["I"] is True
        assert got["Color"] is True
        assert got["PrivBase"] is False
        assert got["PrivI"] is False


class TestModuleExportsOnTheProductionPath:
    """WI-sinol: a survey's ``module_exports`` edges reach exactly the
    declarations the module exports -- no class members, no private helpers --
    and CommonJS exports keep theirs."""

    def test_survey_emits_module_exports_only_to_exports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        from hypergumbo_core.cli import main

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "b.ts").write_text(
            "export class A { render() { return 1; } get x() { return 2; } }\n"
            "export function g() { return 3; }\n"
            "function priv() { return 4; }\n"
            "function aliased() { return 5; }\n"
            "export { aliased as pub };\n"
        )
        (repo / "a.ts").write_text(
            'import { A, g } from "./b";\n'
            "export function main() { return g() + new A().render(); }\n"
        )
        (repo / "lib.js").write_text(
            "function f() { return 1; }\n"
            "function h() { return 2; }\n"
            "function hidden() { return 3; }\n"
            "module.exports = { f };\n"
            "exports.h = h;\n"
        )
        (repo / "c.js").write_text('const lib = require("./lib");\nlib.f();\n')
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        out = tmp_path / "out.json"
        main(["survey", str(repo), "--out", str(out)])
        survey = json.loads(out.read_text())
        by_id = {n["id"]: n for n in survey["nodes"]}
        got = sorted(
            (by_id[e["src"]]["path"], by_id[e["dst"]]["name"])
            for e in survey["edges"] if e["type"] == "module_exports"
        )
        assert got == [
            ("b.ts", "A"), ("b.ts", "aliased"), ("b.ts", "g"),
            ("lib.js", "f"), ("lib.js", "h"),
        ]
