# SPDX-License-Identifier: AGPL-3.0-or-later
"""A linker stand-in inside a minified bundle leaves with the bundle (WI-zuzud).

The tier filter drops a DERIVED (tier 4) file's symbols by default. Linker
stand-ins were never tiered -- ``_classify_symbols`` skipped every symbol with
a ``protocol_origin`` -- so they kept the default tier 1 and stayed in the
output while the analyzer symbols around them were removed. Measured on
livebook before the fix: 249 of 450 stand-ins sat in static/assets/ bundles,
reported as first-party, on 836 edges.

Through the production ``run_behavior_map``: the same Electron send, once in a
source file and once in a bundle carrying a source-map reference (the
``detected as minified/generated`` rule).
"""

from __future__ import annotations

import json
from pathlib import Path


def _survey(tmp_path: Path) -> dict:
    from hypergumbo_core.cli import run_behavior_map

    (tmp_path / "package.json").write_text('{"name": "app", "dependencies": {"electron": "1"}}\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "renderer.js").write_text(
        "const { ipcRenderer } = require('electron');\n"
        "function save() {\n"
        "  ipcRenderer.send('save', 1);\n"
        "}\n"
    )
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "bundle.js").write_text(
        "const { ipcRenderer } = require('electron');\n"
        "function b() { ipcRenderer.send('load', 2); }\n"
        "//# sourceMappingURL=bundle.js.map\n"
    )
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def test_the_bundled_stand_in_is_filtered_with_its_file(tmp_path: Path) -> None:
    data = _survey(tmp_path)
    assert "static/bundle.js" in data["limits"]["tier_filtered_files"]
    ipc = {n["path"]: n for n in data["nodes"]
           if (n.get("meta") or {}).get("framework_role") == "ipc_send"}
    assert "src/renderer.js" in ipc, "the source-file send must survive"
    assert "static/bundle.js" not in ipc


def test_no_stand_in_survives_in_a_tier_filtered_file(tmp_path: Path) -> None:
    data = _survey(tmp_path)
    filtered = set(data["limits"]["tier_filtered_files"])
    leaked = [n["id"] for n in data["nodes"]
              if n.get("protocol_origin") and n.get("path") in filtered]
    assert leaked == []
