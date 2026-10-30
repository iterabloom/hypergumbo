# SPDX-License-Identifier: AGPL-3.0-or-later
"""``require('.')`` and ``require('..')`` are relative imports (WI-pulid).

``_is_relative_import`` accepted ``./`` and ``../`` prefixes only, so the bare
``.`` and ``..`` fell to the npm route and became a third-party PACKAGE named
``.`` / ``..`` (``javascript:npm:0-0:..:package``): 35 of 7,290 resolutions on
nuclear, pm2 and apollo-server. Node resolves both as the directory: its
``package.json`` entry when it has one, else its ``index``.
"""
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/js_module.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _survey(root: Path, files: dict[str, str]) -> dict:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    out = root.parent / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _import_dsts(data: dict, path: str) -> list[str]:
    return sorted(
        e["dst"] for e in data["edges"]
        if e["type"] == "imports" and e["src"] == f"javascript:{path}:1-1:file:file"
    )


_FILES = {
    "lib/index.js": "module.exports = { a: 1 };\n",
    "lib/sub/index.js": "module.exports = { b: 2 };\n",
    "lib/sub/x.js": "const parent = require('..');\nmodule.exports = parent;\n",
    "lib/sub/y.js": "const here = require('.');\nmodule.exports = here;\n",
}


def test_dot_dot_is_the_parent_directory_s_index(tmp_path: Path) -> None:
    data = _survey(tmp_path / "repo", _FILES)
    assert "javascript:lib/index.js:1-1:file:file" in _import_dsts(data, "lib/sub/x.js")


def test_dot_is_this_directory_s_index(tmp_path: Path) -> None:
    data = _survey(tmp_path / "repo", _FILES)
    assert "javascript:lib/sub/index.js:1-1:file:file" in _import_dsts(data, "lib/sub/y.js")


def test_no_package_is_named_like_a_path(tmp_path: Path) -> None:
    data = _survey(tmp_path / "repo", _FILES)
    assert not [n["id"] for n in data["nodes"] if n["kind"] == "package" and n["name"] in (".", "..")]


def test_a_directory_with_a_manifest_is_its_entry(tmp_path: Path) -> None:
    """``require('..')`` into a directory whose package.json names ``main``
    loads that file, not ``index`` (Node's LOAD_AS_DIRECTORY)."""
    data = _survey(tmp_path / "repo", {
        "pkg/package.json": '{"name": "p", "main": "main.js"}',
        "pkg/main.js": "module.exports = 1;\n",
        "pkg/index.js": "module.exports = 2;\n",
        "pkg/sub/x.js": "const p = require('..');\nmodule.exports = p;\n",
    })
    assert "javascript:pkg/main.js:1-1:file:file" in _import_dsts(data, "pkg/sub/x.js")


def test_a_directory_with_a_broken_manifest_is_its_index(tmp_path: Path) -> None:
    for manifest in ("{not json", "[1, 2]"):
        root = tmp_path / str(len(manifest))
        data = _survey(root / "repo", {
            "pkg/package.json": manifest,
            "pkg/index.js": "module.exports = 2;\n",
            "pkg/sub/x.js": "const p = require('..');\nmodule.exports = p;\n",
        })
        assert "javascript:pkg/index.js:1-1:file:file" in _import_dsts(data, "pkg/sub/x.js"), manifest
