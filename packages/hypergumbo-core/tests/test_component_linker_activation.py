# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/vue_component.py
"""The component-import linker runs on Svelte-only and Astro-only trees.

The Vue, Svelte and Astro analyzers each emit an ``imports`` edge per
component tag with the import path in ``meta['import_path']``; a relative
path to a file in the tree already ends on that file's node, and the rest
(``@/components/Ali.svelte``) keep a placeholder for this linker. It ran only
when Vue or Nuxt was detected, so a Svelte-only or Astro-only tree kept every
aliased component import on its placeholder. It now runs on any tree; with no
edge carrying an import path it returns at once.
"""

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.linkers.registry import get_linker


def _run(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return data


def _imports(data: dict[str, Any], src_path: str) -> list[tuple[str, list[str]]]:
    return sorted(
        (e["dst"], e["origin"]) for e in data["edges"]
        if e["type"] == "imports" and e["src"].endswith(f":{src_path}:1-1:file:file")
    )


def test_activation_needs_no_framework_or_language() -> None:
    linker = get_linker("vue-component-linker")
    assert linker is not None
    for frameworks, languages in ((set(), {"svelte"}), (set(), set()), ({"astro"}, set())):
        assert linker.activation.should_run(frameworks, languages)


def test_svelte_only_tree_resolves_an_aliased_component(tmp_path: Path) -> None:
    data = _run(tmp_path, {
        "src/App.svelte": (
            "<script>\n"
            "  import Rel from './components/Rel.svelte';\n"
            "  import Ali from '@/components/Ali.svelte';\n"
            "</script>\n"
            "<Rel /><Ali />\n"
        ),
        "src/components/Rel.svelte": "<p>rel</p>\n",
        "src/components/Ali.svelte": "<p>ali</p>\n",
    })
    edges = _imports(data, "src/App.svelte")
    ali = "svelte:src/components/Ali.svelte:1-1:file:file"
    rel = "svelte:src/components/Rel.svelte:1-1:file:file"
    # The aliased import now reaches its file, from this linker.
    assert (ali, ["vue-component-linker"]) in edges
    # Control: the relative one the analyzer resolved itself gets no second
    # edge from the linker.
    assert [origin for dst, origin in edges if dst == rel] == [["svelte"]]


def test_astro_only_tree_resolves_an_aliased_component(tmp_path: Path) -> None:
    data = _run(tmp_path, {
        "src/pages/index.astro": (
            "---\n"
            "import Rel from '../components/Rel.astro';\n"
            "import Ali from '@/components/Ali.astro';\n"
            "---\n"
            "<Rel /><Ali />\n"
        ),
        "src/components/Rel.astro": "<p>rel</p>\n",
        "src/components/Ali.astro": "<p>ali</p>\n",
    })
    edges = _imports(data, "src/pages/index.astro")
    ali = "astro:src/components/Ali.astro:1-1:file:file"
    rel = "astro:src/components/Rel.astro:1-1:file:file"
    assert (ali, ["vue-component-linker"]) in edges
    assert [origin for dst, origin in edges if dst == rel] == [["astro"]]
