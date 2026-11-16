# SPDX-License-Identifier: AGPL-3.0-or-later
"""The js_module linker resolves an in-tree package's bare specifier (WI-rilof).

A bare specifier no alias matched used to become a ``javascript:npm:0-0:<name>:
package`` stand-in, tier 3 "third-party", even when a ``package.json`` in the
tree declares that name (an npm workspace sibling). These tests pin the
package index, the entry rules and the end-to-end edge; the analyzer half and
the production path are in hypergumbo-lang-mainstream's
``test_js_workspace_package_imports.py``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_core.linkers.js_module import (
    _build_workspace_package_index,
    _package_entry,
    _resolve_workspace_import,
    link_js_modules,
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_index_maps_declared_names_and_skips_node_modules(tmp_path: Path) -> None:
    _write(tmp_path, {
        "packages/b/package.json": '{"name": "@ws/b"}',
        "packages/c/package.json": '{"version": "1.0.0"}',
        "node_modules/lodash/package.json": '{"name": "lodash"}',
        "packages/dup/package.json": '{"name": "@ws/b"}',
    })
    index = _build_workspace_package_index(tmp_path)
    assert index == {"@ws/b": (tmp_path / "packages/b").resolve()}


def test_entry_prefers_exports_then_module_then_main(tmp_path: Path) -> None:
    _write(tmp_path, {
        "exp/package.json": '{"exports": "./e.js", "module": "m.js", "main": "x.js"}',
        "exp/e.js": "", "exp/m.js": "", "exp/x.js": "",
        "dot/package.json": '{"exports": {".": "./d.js", "./sub": "./s.js"}}',
        "dot/d.js": "",
        "cond/package.json": '{"exports": {".": {"types": "./t.d.ts", "require": "./r.js"}}}',
        "cond/r.js": "",
        "mod/package.json": '{"module": "m.js", "main": "x.js"}',
        "mod/m.js": "",
        "none/package.json": '{"exports": {".": {"types": "./t.d.ts"}}}',
        "none/index.js": "",
    })
    assert _package_entry(tmp_path / "exp") == tmp_path / "exp/e.js"
    assert _package_entry(tmp_path / "dot") == (tmp_path / "dot/d.js").resolve()
    assert _package_entry(tmp_path / "cond") == (tmp_path / "cond/r.js").resolve()
    assert _package_entry(tmp_path / "mod") == tmp_path / "mod/m.js"
    assert _package_entry(tmp_path / "none") == tmp_path / "none/index.js"


def test_entry_naming_missing_build_output_falls_back_to_src(tmp_path: Path) -> None:
    _write(tmp_path, {
        "b/package.json": '{"main": "dist/cjs/index.js"}',
        "b/src/index.ts": "",
    })
    assert _package_entry(tmp_path / "b") == tmp_path / "b/src/index.ts"


def test_subpath_resolves_at_root_or_under_src(tmp_path: Path) -> None:
    _write(tmp_path, {
        "b/package.json": '{"name": "@ws/b"}',
        "b/util.js": "",
        "b/src/standalone.ts": "",
    })
    index = {"@ws/b": tmp_path / "b"}
    assert _resolve_workspace_import("@ws/b/util", index) == tmp_path / "b/util.js"
    assert _resolve_workspace_import("@ws/b/standalone", index) == (
        tmp_path / "b/src/standalone.ts")
    assert _resolve_workspace_import("@ws/b/missing", index) is None
    assert _resolve_workspace_import("lodash", index) is None


def test_link_emits_an_imports_edge_to_the_sibling_entry(tmp_path: Path) -> None:
    _write(tmp_path, {
        "packages/a/package.json": '{"name": "@ws/a"}',
        "packages/a/index.js": "",
        "packages/b/package.json": '{"name": "@ws/b", "main": "lib/main.js"}',
        "packages/b/lib/main.js": "",
    })
    edge = Edge.create(
        src="javascript:packages/a/index.js:1-1:file:file",
        dst="javascript:@ws/b:0-0:module:module", edge_type="imports", line=1,
        evidence_type="ast_import", origin="test", origin_run_id="r",
    )
    result = link_js_modules(repo_root=tmp_path, symbols=[], edges=[edge])
    dsts = {e.dst for e in result.edges if e.edge_type == "imports"}
    assert dsts == {"javascript:packages/b/lib/main.js:1-1:file:file"}
    assert not [s for s in result.symbols if s.kind == "package"]


def test_manifest_reads_are_counted(tmp_path: Path) -> None:
    """WI-finij: the package index and the entry lookup read package.json
    files, and a linker's reads must reach the active read log."""
    from hypergumbo_core.linkers._text_filters import (
        reset_active_read_log,
        set_active_read_log,
    )

    _write(tmp_path, {
        "packages/a/package.json": '{"name": "@ws/a"}',
        "packages/a/index.js": "",
        "packages/b/package.json": '{"name": "@ws/b", "main": "index.js"}',
        "packages/b/index.js": "",
    })
    edge = Edge.create(
        src="javascript:packages/a/index.js:1-1:file:file",
        dst="javascript:@ws/b:0-0:module:module", edge_type="imports", line=1,
        evidence_type="ast_import", origin="test", origin_run_id="r",
    )
    log: set[str] = set()
    token = set_active_read_log(log)
    try:
        link_js_modules(repo_root=tmp_path, symbols=[], edges=[edge])
    finally:
        reset_active_read_log(token)
    read = {Path(p).resolve().relative_to(tmp_path.resolve()).as_posix() for p in log}
    assert {"packages/a/package.json", "packages/b/package.json"} <= read, read
