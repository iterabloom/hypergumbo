# SPDX-License-Identifier: AGPL-3.0-or-later
"""A workspace sibling reached through a module object reaches its source (WI-halol).

WI-rilof made a NAMED binding of an npm workspace sibling resolve
(``const { helper } = require('@ws/b')``, ``import { shout } from '@ws/c'``).
Three other spellings still called a tier-3 ``@ws/`` stand-in, because the
lookup found the sibling's function and the cross-package guard refused it:

* a module object of a require -- ``const b = require('@ws/b'); b.helper()``;
* a namespace import -- ``import * as C from '@ws/c'; C.shout()``;
* a default import -- ``import cDefault from '@ws/c'; cDefault()``.

The first two now take the answer the named binding takes
(``_in_tree_package_candidate``). A default import names the package ENTRY's
default export, whatever the local name: the entry is found the way Node finds
it (``js_package_entry.package_entry``, shared with the js_module linker) and
its ``export default`` declaration is the callee. A default import whose local
name differs from the export still resolves; one whose entry has no
``export default`` declaration names nothing.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/js_package_entry.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _survey(repo: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = repo.parent / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


_B = {
    "packages/b/package.json": '{"name": "@ws/b", "version": "1.0.0", "main": "src/index.js"}',
    "packages/b/src/index.js": (
        "function helper() { return 1; }\n"
        "class Engine { run() { return helper(); } }\n"
        "module.exports = { helper, Engine };\n"
    ),
}
_C = {
    "packages/c/package.json": '{"name": "@ws/c", "version": "1.0.0", "main": "src/index.ts"}',
    "packages/c/src/index.ts": (
        "export function shout(s: string): string { return s.toUpperCase(); }\n"
        "export default function cDefault(): number { return 7; }\n"
    ),
    "packages/c/src/a_other.ts": "export default function otherDefault(): number { return 8; }\n",
}
_ROOT = {"package.json": '{"name": "ws", "private": true, "workspaces": ["packages/*"]}'}
_A_PKG = {"packages/a/package.json": '{"name": "@ws/a", "version": "1.0.0"}'}


def _calls_from(data: dict, path: str) -> dict[str, list[str]]:
    """{caller name: [callee dst]} for calls / instantiates out of ``path``."""
    names = {n["id"]: n["name"] for n in data["nodes"]}
    out: dict[str, list[str]] = {}
    for e in data["edges"]:
        if e["type"] in ("calls", "instantiates") and f":{path}:" in e["src"]:
            out.setdefault(names.get(e["src"], e["src"]), []).append(e["dst"])
    return out


def _ws_stand_ins(data: dict) -> list[str]:
    return [n["id"] for n in data["nodes"] if n["kind"] == "external_symbol" and "@ws/" in n["id"]]


class TestTheThreeSpellings:
    def test_a_require_module_object(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {**_ROOT, **_B, **_A_PKG, "packages/a/src/index.js": (
            "const b = require('@ws/b');\n"
            "function run2() { return b.helper(); }\n"
            "module.exports = { run2 };\n"
        )})
        calls = _calls_from(data, "packages/a/src/index.js")
        assert "javascript:packages/b/src/index.js:1-1:helper:function" in calls["run2"], calls

    def test_a_namespace_import(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {**_ROOT, **_C, **_A_PKG, "packages/a/src/esm.ts": (
            "import * as C from '@ws/c';\n"
            "export function e4(): string { return C.shout('b'); }\n"
        )})
        calls = _calls_from(data, "packages/a/src/esm.ts")
        assert "typescript:packages/c/src/index.ts:1-1:shout:function" in calls["e4"], calls
        assert not any(":shout:" in i for i in _ws_stand_ins(data)), _ws_stand_ins(data)

    def test_a_default_import_names_the_entry_s_default_export(self, tmp_path: Path) -> None:
        """The local name is not the export's: ``x`` is ``cDefault``, and
        ``a_other.ts``'s default export (which sorts first) is not it."""
        data = _survey(tmp_path / "repo", {**_ROOT, **_C, **_A_PKG, "packages/a/src/esm.ts": (
            "import x from '@ws/c';\n"
            "export function e3(): number { return x(); }\n"
        )})
        calls = _calls_from(data, "packages/a/src/esm.ts")
        assert calls["e3"] == ["typescript:packages/c/src/index.ts:2-2:cDefault:function"], calls
        assert not any(":default:" in i for i in _ws_stand_ins(data)), _ws_stand_ins(data)


class TestWhatDoesNotResolve:
    def test_an_entry_without_a_default_export_names_nothing(self, tmp_path: Path) -> None:
        """b's entry is CommonJS: no ``export default`` declaration, no edge."""
        data = _survey(tmp_path / "repo", {**_ROOT, **_B, **_A_PKG, "packages/a/src/esm.ts": (
            "import b from '@ws/b';\n"
            "export function e5(): number { return b(); }\n"
        )})
        calls = _calls_from(data, "packages/a/src/esm.ts")
        assert not any(d.startswith("javascript:packages/b/") for d in calls.get("e5", [])), calls

    def test_a_package_the_tree_does_not_define_keeps_its_stand_in(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {**_ROOT, **_A_PKG, "packages/a/src/esm.ts": (
            "import * as L from 'lodash';\n"
            "export function e6(): number[][] { return L.chunk([1, 2], 1); }\n"
        )})
        calls = _calls_from(data, "packages/a/src/esm.ts")
        assert any(d.startswith("typescript:lodash:") for d in calls["e6"]), calls

    def test_the_relative_import_control_is_unchanged(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {
            "src/c.ts": _C["packages/c/src/index.ts"],
            "src/a.ts": (
                "import * as C from './c';\n"
                "import cDefault from './c';\n"
                "export function e3(): number { return cDefault(); }\n"
                "export function e4(): string { return C.shout('b'); }\n"
            ),
        })
        calls = _calls_from(data, "src/a.ts")
        assert calls["e3"] == ["typescript:src/c.ts:2-2:cDefault:function"], calls
        assert calls["e4"] == ["typescript:src/c.ts:1-1:shout:function"], calls


_B_WITH_CONST = {
    **_B,
    "packages/b/src/index.js": (
        "function helper() { return 1; }\n"
        "class Engine { run() { return helper(); } }\n"
        "const VERSION = '1';\n"
        "module.exports = { helper, Engine, VERSION };\n"
    ),
}


class TestAnAttributeReadOnAnInTreeModule:
    """``new b.Engine()`` / ``b.VERSION`` read an attribute of an in-tree
    module. The shared emitter made each a ``module_attr_ref`` to a stand-in
    (``javascript:@ws/b:0-0:@ws/b.Engine``) beside the real instantiation.
    It is a read of an in-tree symbol: a ``references`` edge to it, with no
    stand-in, the answer python gives the same shape (WI-huhum, WI-lovun).
    ``references`` is not a taint call edge, so no taint frontier moves."""

    def _refs(self, data: dict, path: str) -> list[tuple[str, str]]:
        return sorted(
            (e["type"], e["dst"]) for e in data["edges"]
            if f":{path}:" in e["src"] and e["type"] in ("references", "module_attr_ref")
        )

    def test_a_workspace_sibling_s_class_and_constant(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {**_ROOT, **_B_WITH_CONST, **_A_PKG,
                                           "packages/a/src/index.js": (
            "const b = require('@ws/b');\n"
            "function run3() { const e = new b.Engine(); return e.run() + b.VERSION; }\n"
            "module.exports = { run3 };\n"
        )})
        refs = self._refs(data, "packages/a/src/index.js")
        assert ("references", "javascript:packages/b/src/index.js:2-2:Engine:class") in refs, refs
        assert ("references", "javascript:packages/b/src/index.js:3-3:VERSION:variable") in refs, refs
        assert not any(t == "module_attr_ref" for t, _ in refs), refs
        assert not _ws_stand_ins(data) or all(":module:" in i for i in _ws_stand_ins(data)), (
            _ws_stand_ins(data)
        )

    def test_a_relative_module_object_too(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {
            "src/b.js": "class Engine { run() { return 1; } }\nmodule.exports = { Engine };\n",
            "src/a.js": (
                "const b = require('./b');\n"
                "function run3() { return new b.Engine().run(); }\n"
                "module.exports = { run3 };\n"
            ),
        })
        refs = self._refs(data, "src/a.js")
        assert ("references", "javascript:src/b.js:1-1:Engine:class") in refs, refs
        assert not any(t == "module_attr_ref" for t, _ in refs), refs

    def test_an_external_module_s_attribute_keeps_its_module_attr_ref(self, tmp_path: Path) -> None:
        """Control: ``net`` and ``process`` are not in the tree; their reads
        stay the module_attr_ref io-boundary rows match."""
        data = _survey(tmp_path / "repo", {"src/a.js": (
            "const net = require('net');\n"
            "function f() { const s = new net.Socket(); return [s, process.env.HOME]; }\n"
            "module.exports = { f };\n"
        )})
        refs = self._refs(data, "src/a.js")
        assert ("module_attr_ref", "javascript:net:0-0:net.Socket:attribute") in [
            (t, d.replace(":external_symbol", ":attribute")) for t, d in refs
        ], refs
        assert any(t == "module_attr_ref" and "process.env" in d for t, d in refs), refs

    def test_a_named_import_s_property_is_not_a_module_member(self, tmp_path: Path) -> None:
        """nuclear: ``import { Wrapper as W } from './w'; new W.Builder()``
        reads a property of the exported OBJECT ``Wrapper``; nothing says it
        is ``w.js``'s ``Builder``, so it is left as it was."""
        data = _survey(tmp_path / "repo", {
            "src/w.js": (
                "class Builder { build() { return 1; } }\n"
                "export const Wrapper = { Builder };\n"
            ),
            "src/a.js": (
                "import { Wrapper as W } from './w';\n"
                "export function f() { return new W.Builder().build(); }\n"
            ),
        })
        refs = self._refs(data, "src/a.js")
        assert ("references", "javascript:src/w.js:1-1:Builder:class") not in refs, refs

    def test_a_namespace_binding_of_the_same_module_still_retargets(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {
            "src/w.js": "export class Builder { build() { return 1; } }\n",
            "src/a.js": (
                "import * as w from './w';\n"
                "export function f() { return new w.Builder().build(); }\n"
            ),
        })
        refs = self._refs(data, "src/a.js")
        assert ("references", "javascript:src/w.js:1-1:Builder:class") in refs, refs


class TestTheGuardsAgainstAGuess:
    def test_a_default_import_s_member_call_is_not_guessed(self, tmp_path: Path) -> None:
        """``import cd from '@ws/c'; cd.shout()`` calls a property of c's
        DEFAULT EXPORT; nothing says that is c's module-level ``shout``."""
        data = _survey(tmp_path / "repo", {**_ROOT, **_C, **_A_PKG, "packages/a/src/esm.ts": (
            "import cd from '@ws/c';\n"
            "export function e7(): string { return cd.shout('x'); }\n"
        )})
        calls = _calls_from(data, "packages/a/src/esm.ts")
        assert "typescript:packages/c/src/index.ts:1-1:shout:function" not in calls.get("e7", []), calls

    def test_a_package_name_two_roots_declare_names_nothing(self, tmp_path: Path) -> None:
        data = _survey(tmp_path / "repo", {
            **_ROOT, **_C, **_A_PKG,
            "packages/c2/package.json": '{"name": "@ws/c", "version": "2.0.0", "main": "src/index.ts"}',
            "packages/c2/src/index.ts": "export default function dup(): number { return 9; }\n",
            "packages/a/src/esm.ts": "import x from '@ws/c';\nexport function e3(): number { return x(); }\n",
        })
        calls = _calls_from(data, "packages/a/src/esm.ts")
        assert not any(":cDefault:" in d or ":dup:" in d for d in calls.get("e3", [])), calls


def test_a_relative_default_import_under_another_name(tmp_path: Path) -> None:
    """``import y from './c'; y()`` is c's default export too: the entry rule
    is the same for a relative specifier, whose file is the one it names."""
    data = _survey(tmp_path / "repo", {
        "src/c.ts": _C["packages/c/src/index.ts"],
        "src/a.ts": "import y from './c';\nexport function e3(): number { return y(); }\n",
    })
    assert _calls_from(data, "src/a.ts")["e3"] == ["typescript:src/c.ts:2-2:cDefault:function"]


def test_an_absolute_default_import_names_nothing(tmp_path: Path) -> None:
    """An absolute specifier is the bundler's or runtime's root, not a file
    this analysis can place; it is not guessed."""
    data = _survey(tmp_path / "repo", {
        "src/c.ts": _C["packages/c/src/index.ts"],
        "src/a.ts": "import y from '/src/c';\nexport function e3(): number { return y(); }\n",
    })
    assert not any(":cDefault:" in d for d in _calls_from(data, "src/a.ts").get("e3", []))
