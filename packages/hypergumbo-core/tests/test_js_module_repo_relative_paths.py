# SPDX-License-Identifier: AGPL-3.0-or-later
"""The JS module linker anchors repo-relative symbol paths on the repo root.

WI-fukaf. The production pipeline hands the linker REPO-RELATIVE
``Symbol.path`` values (``src/a.ts``), and the linker treated them as
absolute:

(a) a relative import was resolved against the process working directory,
    so the same repository surveyed from outside its own directory lost
    imports (koel: 4,078 -> 3,680, module_exports 2,085 -> 1,471);
(b) a tsconfig ``paths`` alias never resolved from any directory, because the
    ancestor walk compared ``src`` with the absolute keys of the tsconfig
    index, and the import minted a phantom npm package instead.

Every existing test in ``test_js_module_linker.py`` builds ABSOLUTE symbol
paths, which is why none of them saw this. These build repo-relative ones and
run from a working directory that is not the repository.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.js_module import link_js_modules


def _file(path: str) -> Symbol:
    return Symbol(
        id=f"typescript:{path}:1-1:{Path(path).name}:file", name=Path(path).name,
        kind="file", language="typescript", path=path,
        span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
        origin="ts", origin_run_id="r",
    )


def _fn(path: str, name: str) -> Symbol:
    return Symbol(
        id=f"typescript:{path}:1-3:{name}:function", name=name, kind="function",
        language="typescript", path=path,
        span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
        origin="ts", origin_run_id="r",
    )


def _import(src: str, spec: str) -> Edge:
    return Edge.create(
        src=src, dst=f"typescript:{spec}:0-0:module:module", edge_type="imports",
        line=1, origin="ts", origin_run_id="r",
    )


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "lib").mkdir(parents=True)
    (root / "src" / "a.ts").write_text('import { b } from "./lib/b";\n')
    (root / "src" / "lib" / "b.ts").write_text("export function b() {}\n")
    (root / "tsconfig.json").write_text(json.dumps(
        {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}},
    ))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return root


def _link(repo: Path, spec: str):
    a = _file("src/a.ts")
    b = _fn("src/lib/b.ts", "b")
    return a, b, link_js_modules(repo_root=repo, symbols=[a, b], edges=[_import(a.id, spec)])


@pytest.mark.parametrize("spec", ["./lib/b", "@/lib/b"], ids=["relative", "tsconfig_alias"])
def test_the_import_resolves_from_a_foreign_cwd(repo: Path, spec: str) -> None:
    a, b, result = _link(repo, spec)
    [module] = [s for s in result.symbols if s.kind == "file"]
    assert module.path == "src/lib/b.ts"
    imports = [e for e in result.edges if e.edge_type == "imports"]
    assert [(e.src, e.dst) for e in imports] == [(a.id, module.id)]
    exports = [e for e in result.edges if e.edge_type == "module_exports"]
    assert [(e.src, e.dst) for e in exports] == [(module.id, b.id)]


def test_an_alias_mints_no_phantom_npm_package(repo: Path) -> None:
    _a, _b, result = _link(repo, "@/lib/b")
    assert not any(":npm:" in s.id for s in result.symbols), [s.id for s in result.symbols]
