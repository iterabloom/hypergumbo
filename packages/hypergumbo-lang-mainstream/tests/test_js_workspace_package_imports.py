# SPDX-License-Identifier: AGPL-3.0-or-later
"""An import of a sibling workspace package reaches its source (WI-rilof, npm).

``require('@ws/b')`` in an npm workspace names a package whose source is in
the tree. Two things treated it as a third-party dependency:

* the call ``helper()`` -- the name lookup found b's ``helper`` and the
  cross-package guard then refused it (it is in another ``package.json``
  root), so the call landed on ``javascript:@ws/b:0-0:helper:external_symbol``
  and b's real ``helper`` read as uncalled;
* the js_module linker -- a bare specifier no alias matched became the
  ``javascript:npm:0-0:@ws/b:package`` stand-in, tier 3 "third-party".

Both now resolve a specifier whose package name a ``package.json`` in the
tree declares to that package's source. A package the tree does not define
keeps both behaviours.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/js_module.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _survey(repo: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = repo / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _workspace(b_package_json: str, b_files: dict[str, str], a_index: str) -> dict[str, str]:
    files = {
        "package.json": '{"name": "ws", "private": true, "workspaces": ["packages/*"]}',
        "packages/a/package.json": (
            '{"name": "@ws/a", "version": "1.0.0", '
            '"dependencies": {"@ws/b": "*", "lodash": "^4.0.0"}}'
        ),
        "packages/b/package.json": b_package_json,
        "packages/a/index.js": a_index,
    }
    files.update({f"packages/b/{rel}": text for rel, text in b_files.items()})
    return files


_B_INDEX = {"index.js": "function helper() { return 1; }\nmodule.exports = { helper };\n"}


def _call_dsts(data: dict, caller: str) -> set[str]:
    src = next(n["id"] for n in data["nodes"] if n["name"] == caller)
    return {e["dst"] for e in data["edges"] if e["type"] == "calls" and e["src"] == src}


def _imports_from(data: dict, path: str) -> set[str]:
    src = f"javascript:{path}:1-1:file:file"
    return {e["dst"] for e in data["edges"] if e["type"] == "imports" and e["src"] == src}


def test_require_of_a_sibling_package_calls_its_function(tmp_path: Path) -> None:
    data = _survey(tmp_path, _workspace(
        '{"name": "@ws/b", "version": "1.0.0", "main": "index.js"}', _B_INDEX,
        "const { helper } = require('@ws/b');\n"
        "function run() { return helper(); }\n"
        "module.exports = { run };\n",
    ))
    assert _call_dsts(data, "run") == {"javascript:packages/b/index.js:1-1:helper:function"}
    assert "javascript:packages/b/index.js:1-1:file:file" in _imports_from(
        data, "packages/a/index.js")
    ids = {n["id"] for n in data["nodes"]}
    assert "javascript:npm:0-0:@ws/b:package" not in ids
    assert not [i for i in ids if i.startswith("javascript:@ws/b:0-0:helper")]


def test_esm_import_of_a_sibling_package_without_main(tmp_path: Path) -> None:
    """No ``main``: the package's index file is its entry, as Node resolves it."""
    data = _survey(tmp_path, _workspace(
        '{"name": "@ws/b", "version": "1.0.0"}',
        {"index.js": "export function helper() { return 1; }\n"},
        "import { helper } from '@ws/b';\n"
        "export function run() { return helper(); }\n",
    ))
    assert _call_dsts(data, "run") == {"javascript:packages/b/index.js:1-1:helper:function"}
    assert "javascript:packages/b/index.js:1-1:file:file" in _imports_from(
        data, "packages/a/index.js")


def test_main_field_and_subpath_resolve_inside_the_package(tmp_path: Path) -> None:
    data = _survey(tmp_path, _workspace(
        '{"name": "@ws/b", "version": "1.0.0", "main": "lib/main.js"}',
        {
            "lib/main.js": "function helper() { return 1; }\nmodule.exports = { helper };\n",
            "util.js": "function tool() { return 2; }\nmodule.exports = { tool };\n",
        },
        "const { helper } = require('@ws/b');\n"
        "const { tool } = require('@ws/b/util');\n"
        "function run() { return helper() + tool(); }\n"
        "module.exports = { run };\n",
    ))
    assert _call_dsts(data, "run") == {
        "javascript:packages/b/lib/main.js:1-1:helper:function",
        "javascript:packages/b/util.js:1-1:tool:function",
    }
    assert {
        "javascript:packages/b/lib/main.js:1-1:file:file",
        "javascript:packages/b/util.js:1-1:file:file",
    } <= _imports_from(data, "packages/a/index.js")


def test_same_name_in_two_siblings_binds_the_imported_one(tmp_path: Path) -> None:
    files = _workspace(
        '{"name": "@ws/b", "version": "1.0.0"}', _B_INDEX,
        "const { helper } = require('@ws/b');\n"
        "function run() { return helper(); }\n"
        "module.exports = { run };\n",
    )
    files["packages/c/package.json"] = '{"name": "@ws/c", "version": "1.0.0"}'
    files["packages/c/index.js"] = (
        "function helper() { return 3; }\nmodule.exports = { helper };\n"
    )
    data = _survey(tmp_path, files)
    assert _call_dsts(data, "run") == {"javascript:packages/b/index.js:1-1:helper:function"}


def test_a_package_the_tree_does_not_define_stays_third_party(tmp_path: Path) -> None:
    """Control: ``lodash`` is declared but not in the tree."""
    data = _survey(tmp_path, _workspace(
        '{"name": "@ws/b", "version": "1.0.0"}', _B_INDEX,
        "const { chunk } = require('lodash');\n"
        "function run() { return chunk([1], 1); }\n"
        "module.exports = { run };\n",
    ))
    pkg = next(
        n for n in data["nodes"] if n["id"] == "javascript:npm:0-0:lodash:package"
    )
    assert pkg["supply_chain"]["tier"] == 3
    assert not [d for d in _call_dsts(data, "run") if "packages/" in d]


def test_entry_naming_build_output_falls_back_to_the_source_entry(tmp_path: Path) -> None:
    """``main`` names ``dist/`` output that is not in the tree; the package's
    ``src/index`` is its source entry, and a subpath resolves under ``src/``."""
    data = _survey(tmp_path, _workspace(
        '{"name": "@ws/b", "version": "1.0.0", "main": "dist/cjs/index.js"}',
        {
            "src/index.js": "export function helper() { return 1; }\n",
            "src/standalone.js": "export function start() { return 2; }\n",
        },
        "import { helper } from '@ws/b';\n"
        "import { start } from '@ws/b/standalone';\n"
        "export function run() { return helper() + start(); }\n",
    ))
    assert {
        "javascript:packages/b/src/index.js:1-1:file:file",
        "javascript:packages/b/src/standalone.js:1-1:file:file",
    } <= _imports_from(data, "packages/a/index.js")
    assert "javascript:npm:0-0:@ws/b:package" not in {n["id"] for n in data["nodes"]}
    assert _call_dsts(data, "run") == {
        "javascript:packages/b/src/index.js:1-1:helper:function",
        "javascript:packages/b/src/standalone.js:1-1:start:function",
    }
