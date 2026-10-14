# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-fukaf on the production path: a survey's JS/TS module links do not
depend on the directory the survey was run from.

Bakeoffs run ``hypergumbo`` against a repository from OUTSIDE it, and on koel
that silently cost 29% of module_exports. This runs ``survey`` on a two-file
TypeScript repo (a relative import and a tsconfig ``paths`` alias) once from
inside the repository and once from a sibling directory, and requires the
same resolved links from both. The core file
``test_js_module_repo_relative_paths.py`` pins the linker directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main


def _links(repo: Path, cwd: Path, out: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("XDG_CACHE_HOME", str(out.parent / f"cache-{cwd.name}"))
    main(["survey", str(repo), "--out", str(out)])
    survey = json.loads(out.read_text())
    by_id = {n["id"]: n for n in survey["nodes"]}

    def _where(node_id: str) -> str:
        node = by_id.get(node_id)
        return node["path"] if node else node_id

    imports = sorted(
        (_where(e["src"]), _where(e["dst"])) for e in survey["edges"]
        if e["type"] == "imports" and e["src"].startswith("typescript:")
    )
    exports = sorted(
        (_where(e["src"]), by_id.get(e["dst"], {}).get("name"))
        for e in survey["edges"] if e["type"] == "module_exports"
    )
    phantoms = sorted(n["id"] for n in survey["nodes"] if ":npm:" in n["id"])
    return {"imports": imports, "exports": exports, "phantoms": phantoms}


def test_links_are_the_same_from_inside_and_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "lib").mkdir(parents=True)
    (repo / "src" / "a.ts").write_text(
        'import { b } from "./lib/b";\nimport { c } from "@/lib/c";\n'
        "export function a() { b(); c(); }\n"
    )
    (repo / "src" / "lib" / "b.ts").write_text("export function b() {}\n")
    (repo / "src" / "lib" / "c.ts").write_text("export function c() {}\n")
    (repo / "tsconfig.json").write_text(json.dumps(
        {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}},
    ))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    inside = _links(repo, repo, tmp_path / "in.json", monkeypatch)
    outside = _links(repo, elsewhere, tmp_path / "out.json", monkeypatch)

    assert ("src/a.ts", "src/lib/b.ts") in inside["imports"]
    assert ("src/a.ts", "src/lib/c.ts") in inside["imports"], inside
    assert inside["phantoms"] == []
    assert outside == inside
