# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bare JS/TS identifier is resolved by the SCOPE it appears in, never by its
name repo-wide (WI-sofuh), and an identifier no scope binds is still recorded
as a call (WI-fahod).

WI-sofuh. ``helper(p)`` in a file that declares ``helper`` bound to ANOTHER
file's ``helper`` at 0.85: the bare-identifier arm tried named-import
disambiguation, then ``_same_package_candidate`` (the FIRST candidate under the
caller's package.json), then the repo-wide resolver, and none of the three
consulted the caller's own file. Under every JavaScript module system the
innermost binding wins: a parameter or local, then the enclosing function's
declarations, then the file's own module-scope declarations, then an import,
then a global. The fix walks those scopes from the call site outward and
resolves a declaration by its POSITION (INV-mozas / INV-midag), so only a name
no scope in the file binds reaches a repo-wide lookup.

WI-fahod. A call to a name nothing in the file binds, that is not imported and
that no in-repo symbol carries, emitted NO EDGE of any type, so the ADR-0017
walk saw an escape site where a call stood. It now emits the ``external``
placeholder, the same residual python's INV-foluz arm emits. A called ESM
default import or CommonJS module binding (``import def from 'lib'; def(p)``,
``const debug = require('debug'); debug(x)``) emits an edge into that module's
``default`` export.

STILL REFUSED, as python refuses it: a callee bound to a parameter or a local
VALUE. The call is real but its callee is a value the analyzer cannot name, and
writing the bare name into an ``external`` placeholder would let the catalogue's
short-name gate match it (``function f(exec) { exec(c) }`` would read as
``child_process.exec``). Pinned below.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Symbol
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _write(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)


def _by_id(symbols: list[Symbol]) -> dict[str, Symbol]:
    return {s.id: s for s in symbols}


def _calls_from(
    edges: list[Edge], symbols: list[Symbol], caller: str, caller_file: str,
) -> list[tuple[str, str]]:
    """``(dst file name, dst symbol name)`` of each resolved call from caller."""
    by_id = _by_id(symbols)
    out = []
    for e in edges:
        if e.edge_type != "calls":
            continue
        src = by_id.get(e.src)
        if src is None or src.name != caller or not src.path.endswith(caller_file):
            continue
        dst = by_id.get(e.dst)
        if dst is not None:
            out.append((Path(dst.path).name, dst.name))
    return out


def _unresolved_from(
    edges: list[Edge], symbols: list[Symbol], caller: str,
) -> list[str]:
    by_id = _by_id(symbols)
    return [
        e.dst for e in edges
        if e.edge_type == "calls"
        and e.dst.endswith(":unresolved")
        and (src := by_id.get(e.src)) is not None
        and src.name == caller
    ]


_HELPER_A = "function helper(p) { return p; }\nfunction f(x) { return helper(x); }\n"
_HELPER_B = "function helper(p) { return p; }\nfunction g(x) { return helper(x); }\n"


class TestOwnFileDeclarationWins:
    @pytest.mark.parametrize("with_package_json", [False, True])
    def test_each_caller_binds_its_own_files_helper(
        self, tmp_path: Path, with_package_json: bool,
    ) -> None:
        files = {"a.js": _HELPER_A, "b.js": _HELPER_B}
        if with_package_json:
            files["package.json"] = "{}"
        _write(tmp_path, files)
        result = analyze_javascript(tmp_path)
        # BOTH sides are asserted: a name-keyed registry keeps one symbol per
        # name, so a one-sided check can pass on whichever file sorts last.
        assert _calls_from(result.edges, result.symbols, "f", "a.js") == [
            ("a.js", "helper"),
        ]
        assert _calls_from(result.edges, result.symbols, "g", "b.js") == [
            ("b.js", "helper"),
        ]

    def test_six_extensions_each_bind_their_own(self, tmp_path: Path) -> None:
        exts = ["js", "mjs", "cjs", "jsx", "ts", "tsx"]
        _write(tmp_path, {
            f"m{i}.{ext}": (
                "function helper(p) { return p; }\n"
                f"function caller{i}(x) {{ return helper(x); }}\n"
            )
            for i, ext in enumerate(exts)
        })
        result = analyze_javascript(tmp_path)
        for i, ext in enumerate(exts):
            assert _calls_from(
                result.edges, result.symbols, f"caller{i}", f"m{i}.{ext}",
            ) == [(f"m{i}.{ext}", "helper")], ext

    def test_binding_is_the_position_proven_one(self, tmp_path: Path) -> None:
        """A same-file binding is stronger evidence than the name heuristics."""
        _write(tmp_path, {"a.js": _HELPER_A, "b.js": _HELPER_B})
        result = analyze_javascript(tmp_path)
        by_id = _by_id(result.symbols)
        conf = [
            e.confidence for e in result.edges
            if e.edge_type == "calls" and by_id.get(e.src) is not None
            and by_id[e.src].name == "f"
        ]
        assert conf == [0.90], conf

    def test_export_and_arrow_declarations_bind(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.ts": (
                "export function helper(p: number) { return p; }\n"
                "export const other = (p: number) => p;\n"
                "export function f(x: number) { return helper(x) + other(x); }\n"
            ),
            "b.ts": (
                "export function helper(p: number) { return p; }\n"
                "export const other = (p: number) => p;\n"
            ),
        })
        result = analyze_javascript(tmp_path)
        assert sorted(_calls_from(result.edges, result.symbols, "f", "a.ts")) == [
            ("a.ts", "helper"), ("a.ts", "other"),
        ]

    def test_nested_declaration_shadows_module_scope(self, tmp_path: Path) -> None:
        _write(tmp_path, {"a.js": (
            "function helper(p) { return p; }\n"
            "function outer(x) {\n"
            "  function helper(q) { return q + 1; }\n"
            "  return helper(x);\n"
            "}\n"
            "function top(x) { return helper(x); }\n"
        )})
        result = analyze_javascript(tmp_path)
        by_id = _by_id(result.symbols)
        lines = {}
        for e in result.edges:
            if e.edge_type == "calls" and e.dst in by_id:
                lines[by_id[e.src].name] = by_id[e.dst].span.start_line
        assert lines == {"outer": 3, "top": 1}, lines

    def test_hoisted_declaration_after_the_call_binds(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.js": "function f(x) { return helper(x); }\nfunction helper(p) { return p; }\n",
            "b.js": "function helper(p) { return p; }\n",
        })
        result = analyze_javascript(tmp_path)
        assert _calls_from(result.edges, result.symbols, "f", "a.js") == [
            ("a.js", "helper"),
        ]

    def test_var_hoisted_out_of_a_block_binds(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "a.js": (
                "function f(x) {\n"
                "  if (x) { var helper = function () { return 1; }; }\n"
                "  return helper(x);\n"
                "}\n"
            ),
            "b.js": "function helper(p) { return p; }\n",
        })
        result = analyze_javascript(tmp_path)
        assert _calls_from(result.edges, result.symbols, "f", "a.js") == [
            ("a.js", "helper"),
        ]

    def test_caller_without_a_declaration_keeps_the_repo_lookup(
        self, tmp_path: Path,
    ) -> None:
        """A file that does not declare the name falls through to today's
        lookup: a non-module browser script shares top-level names."""
        _write(tmp_path, {
            "a.js": "function f(x) { return helper(x); }\n",
            "b.js": "function helper(p) { return p; }\n",
        })
        result = analyze_javascript(tmp_path)
        assert _calls_from(result.edges, result.symbols, "f", "a.js") == [
            ("b.js", "helper"),
        ]

    def test_callback_argument_binds_its_own_files_function(
        self, tmp_path: Path,
    ) -> None:
        """The callback-argument arm had the same name-first resolution."""
        _write(tmp_path, {
            "a.js": "function helper(p) { return p; }\nfunction f(xs) { return xs.map(helper); }\n",
            "b.js": "function helper(p) { return p; }\nfunction g(xs) { return xs.map(helper); }\n",
            "package.json": "{}",
        })
        result = analyze_javascript(tmp_path)
        by_id = _by_id(result.symbols)
        refs = sorted(
            (Path(by_id[e.src].path).name, Path(by_id[e.dst].path).name)
            for e in result.edges
            if e.edge_type == "references" and e.dst in by_id
            and by_id[e.dst].name == "helper"
        )
        assert refs == [("a.js", "a.js"), ("b.js", "b.js")], refs


class TestLocalBindingsAreNotResolvedByName:
    def test_local_value_shadows_another_files_function(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {
            "a.js": "function f(x) { const helper = make(x); return helper(x); }\n",
            "b.js": "function helper(p) { return p; }\n",
        })
        result = analyze_javascript(tmp_path)
        assert _calls_from(result.edges, result.symbols, "f", "a.js") == []
        assert all(
            "helper" not in d
            for d in _unresolved_from(result.edges, result.symbols, "f")
        )

    @pytest.mark.parametrize("binding", [
        "function f(exec) { exec(c); }",
        "function f() { const exec = make(); exec(c); }",
        "function f({exec}) { exec(c); }",
        "function f() { for (const exec of xs) { exec(c); } }",
        "function f() { try { g(); } catch (exec) { exec(c); } }",
    ])
    def test_local_shadow_of_an_import_is_not_the_import(
        self, tmp_path: Path, binding: str,
    ) -> None:
        """The bare-call half of WI-dagig: a local named after a destructured
        import is the local, not ``child_process.exec``."""
        _write(tmp_path, {"a.js": (
            "const { exec } = require('child_process');\n" + binding + "\n"
        )})
        result = analyze_javascript(tmp_path)
        got = _unresolved_from(result.edges, result.symbols, "f")
        assert not [d for d in got if ":exec:" in d], got

    def test_module_level_import_still_reaches_the_row(
        self, tmp_path: Path,
    ) -> None:
        """Control for the shadow tests: the unshadowed call still emits."""
        _write(tmp_path, {"a.js": (
            "const { exec } = require('child_process');\n"
            "function f(c) { exec(c); }\n"
        )})
        result = analyze_javascript(tmp_path)
        assert _unresolved_from(result.edges, result.symbols, "f") == [
            "javascript:child_process:0-0:exec:unresolved",
        ]


class TestUnboundNamesAreRecorded:
    @pytest.mark.parametrize("ext", ["js", "mjs", "cjs", "jsx", "ts", "tsx"])
    def test_unbound_bare_names_emit_the_external_placeholder(
        self, tmp_path: Path, ext: str,
    ) -> None:
        _write(tmp_path, {f"a.{ext}": (
            "function bareUnresolvedNames(p) {\n"
            "  const a = join(p, 'x');\n"
            "  const b = readFileSync(p);\n"
            "  const c = normalize(p);\n"
            "  return [a, b, c];\n"
            "}\n"
        )})
        result = analyze_javascript(tmp_path)
        lang = "typescript" if ext in ("ts", "tsx") else "javascript"
        got = sorted(_unresolved_from(
            result.edges, result.symbols, "bareUnresolvedNames",
        ))
        assert got == [
            f"{lang}:external:0-0:{n}:unresolved"
            for n in ("join", "normalize", "readFileSync")
        ], got
        edge = next(e for e in result.edges if e.dst.endswith(":join:unresolved"))
        assert edge.is_resolved is False
        assert (edge.meta or {}).get("callee_name") == "join"

    @pytest.mark.parametrize("decl, module", [
        ("import def from 'lib';", "lib"),
        ("import exec from 'child_process';", "child_process"),
        ("const debug = require('debug');", "debug"),
        ("import * as ns from 'node:path';", "path"),
    ])
    def test_called_module_binding_reaches_its_default_export(
        self, tmp_path: Path, decl: str, module: str,
    ) -> None:
        name = decl.split()[1] if not decl.startswith("import * as") else "ns"
        _write(tmp_path, {"a.js": f"{decl}\nfunction f(p) {{ return {name}(p); }}\n"})
        result = analyze_javascript(tmp_path)
        assert _unresolved_from(result.edges, result.symbols, "f") == [
            f"javascript:{module}:0-0:default:unresolved",
        ]
        # Not a call into the binding's own variable symbol.
        assert _calls_from(result.edges, result.symbols, "f", "a.js") == []

    def test_resolved_call_emits_no_placeholder(self, tmp_path: Path) -> None:
        _write(tmp_path, {"a.js": _HELPER_A})
        result = analyze_javascript(tmp_path)
        assert _unresolved_from(result.edges, result.symbols, "f") == []
