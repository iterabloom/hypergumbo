# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/js_module.py
"""An npm package stand-in's ``Symbol.path`` is ADR-0036's no-anchor sentinel.

The JS module linker mints one ``package`` node per third-party npm package a
file imports (``lit``, ``@vue/test-utils``). The package has no file in the
tree, and ADR-0036 Ruling 3 declares the sentinel for exactly that:
``<external>`` (``ir.NO_ANCHOR_PATH``), which every boundary node already
carries. The package node wrote ``''`` instead, a second, undeclared spelling
of "no anchor" in the same map.

What did not change: the stable id. Its formula
(``make_dependency_stable_id``) takes the declaring MANIFEST's path, whose
documented value for a stand-in with no manifest is ``''``; ``Symbol.path`` is
the filesystem anchor, another field. The boundary factory keeps the same
split (its stable id hashes the key as read, its path is ``<external>``). The
node id's path slot (``npm``) does not change either.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_dependency_stable_id
from hypergumbo_core.ir import NO_ANCHOR_PATH, Edge, Span, Symbol
from hypergumbo_core.linkers.js_module import link_js_modules


def _file(root: Path, rel: str, lang: str) -> Symbol:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    return Symbol(
        id=f"{lang}:{rel}:1-1:{p.name}:file", name=p.name, kind="file",
        language=lang, path=rel,
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        origin="js", origin_run_id="test-run",
    )


def _import(src: Symbol, spec: str) -> Edge:
    return Edge.create(
        src=src.id, dst=f"{src.language}:{spec}:0-0:module:module",
        edge_type="imports", line=1, origin="js", origin_run_id="test-run",
        evidence_type="import_static", confidence=0.95,
    )


def _run(tmp_path: Path):
    app = _file(tmp_path, "src/app.ts", "typescript")
    util = _file(tmp_path, "src/util.ts", "typescript")
    result = link_js_modules(
        repo_root=tmp_path, symbols=[app, util],
        edges=[_import(app, "lit"), _import(app, "@vue/test-utils"),
               _import(app, "./util")],
    )
    packages = {s.name: s for s in result.symbols if s.kind == "package"}
    assert set(packages) == {"lit", "@vue/test-utils"}, "reach"
    return result, packages


def test_npm_package_path_is_the_no_anchor_sentinel(tmp_path: Path) -> None:
    _, packages = _run(tmp_path)
    for pkg in packages.values():
        assert pkg.path == NO_ANCHOR_PATH == "<external>", pkg.id
        assert pkg.meta["package_ecosystem"] == "npm"


def test_stable_id_and_node_id_do_not_move(tmp_path: Path) -> None:
    """Control: the id's ``npm`` path slot and the stable id stay."""
    _, packages = _run(tmp_path)
    for name, pkg in packages.items():
        assert pkg.id == f"typescript:npm:0-0:{name}:package"
        assert pkg.stable_id == make_dependency_stable_id("typescript", "", name)


def test_in_tree_import_keeps_its_file_path(tmp_path: Path) -> None:
    """Control: a relative import resolves to its file, which keeps its path."""
    result, packages = _run(tmp_path)
    targets = {e.dst for e in result.edges if e.edge_type == "imports"}
    assert "typescript:src/util.ts:1-1:util.ts:file" in targets
    assert targets >= {p.id for p in packages.values()}
