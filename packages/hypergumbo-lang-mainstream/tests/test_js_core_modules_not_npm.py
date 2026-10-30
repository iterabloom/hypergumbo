# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Node core module is not an npm package (WI-tufob).

The js_module linker minted ``javascript:npm:0-0:fs:package`` -- kind
``package``, ``package_ecosystem: npm``, tier 3 "third-party dependency" -- for
``require('fs')``: 540 of 2,771 package-node resolutions on 7 repositories were
``path``, ``fs``, ``http``, ``child_process`` ... claimed as dependencies. The
runtime's own modules get no package node; the import stays the analyzer's
external stand-in. One core-module list (``js_package_entry.NODE_CORE_MODULES``)
serves the linker and js_ts.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/js_module.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _packages(root: Path, source: str) -> list[str]:
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.js").write_text(source)
    (root / "package.json").write_text(
        '{"name": "x", "dependencies": {"lodash": "^4", "fs-extra": "^11"}}'
    )
    out = root.parent / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    return sorted(n["name"] for n in data["nodes"] if n["kind"] == "package")


def test_core_modules_get_no_package_node(tmp_path: Path) -> None:
    names = _packages(tmp_path / "repo", (
        "const fs = require('fs');\nconst path = require('path');\n"
        "const fsp = require('fs/promises');\nconst cp = require('node:child_process');\n"
        "module.exports = [fs, path, fsp, cp];\n"
    ))
    assert not set(names) & {"fs", "path", "fs/promises", "node:child_process", "child_process"}, names


def test_npm_packages_keep_theirs(tmp_path: Path) -> None:
    """Control: ``fs-extra`` starts like ``fs`` and is an npm package."""
    names = _packages(tmp_path / "repo", (
        "const _ = require('lodash');\nconst fse = require('fs-extra');\nmodule.exports = [_, fse];\n"
    ))
    assert {"fs-extra", "lodash"} <= set(names), names
