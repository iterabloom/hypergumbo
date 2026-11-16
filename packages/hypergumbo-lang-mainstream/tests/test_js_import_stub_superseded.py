# SPDX-License-Identifier: AGPL-3.0-or-later
"""The JS analyzer's import stub is demoted beside the linker's resolution (WI-halol shape 5).

Every JS/TS import is emitted by the analyzer as an ``imports`` edge to a
``<spec>:0-0:module`` placeholder; the js-module linker resolves the specifier
and emits its own edge to the file (or npm package) it names, citing the
placeholder edge in ``derived_from``. For a workspace sibling the placeholder
became a workspace-prefixed ``external_symbol`` (``javascript:@ws/b:0-0:module``)
beside the real file edge -- an INV-nuzas counterexample. js-module-linker now
holds ADR-0057 section 14.1's permission (``docs/audits/0025``): finalize stamps
the placeholder ``superseded_by`` with the resolving edge and halves its rank.
Nothing is deleted.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _survey(root: Path) -> dict:
    files = {
        "package.json": '{"name": "ws", "private": true, "workspaces": ["packages/*"]}',
        "packages/b/package.json": '{"name": "@ws/b", "version": "1.0.0", "main": "src/index.js"}',
        "packages/b/src/index.js": "function helper() { return 1; }\nmodule.exports = { helper };\n",
        "packages/a/package.json": '{"name": "@ws/a", "dependencies": {"@ws/b": "workspace:*", "lodash": "^4"}}',
        "packages/a/src/util.js": "module.exports = { u: 1 };\n",
        "packages/a/src/index.js": (
            "const b = require('@ws/b');\nconst u = require('./util');\n"
            "const _ = require('lodash');\nconst fs = require('fs');\n"
            "module.exports = [b, u, _, fs];\n"
        ),
    }
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    out = root.parent / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _stub(data: dict, spec: str) -> dict:
    [stub] = [
        e for e in data["edges"]
        if e["type"] == "imports" and "packages/a/src/index.js" in e["src"]
        and e["dst"].startswith(f"javascript:{spec}:0-0:module")
    ]
    return stub


def test_each_resolved_import_s_stub_names_its_resolution(tmp_path: Path) -> None:
    data = _survey(tmp_path / "repo")
    by_id = {e["id"]: e for e in data["edges"]}
    for spec, target in (
        ("@ws/b", "javascript:packages/b/src/index.js:1-1:file:file"),
        ("./util", "javascript:packages/a/src/util.js:1-1:file:file"),
        ("lodash", "javascript:npm:0-0:lodash:package"),
    ):
        meta = _stub(data, spec).get("meta") or {}
        assert by_id[meta["superseded_by"]]["dst"] == target, (spec, meta)
        assert "js-module-linker" in meta["superseded_by_origin"]


def test_a_core_module_s_stub_is_not_demoted(tmp_path: Path) -> None:
    """Control: ``fs`` gets no linker edge (WI-tufob), so nothing supersedes it."""
    data = _survey(tmp_path / "repo")
    assert "superseded_by" not in (_stub(data, "fs").get("meta") or {})
