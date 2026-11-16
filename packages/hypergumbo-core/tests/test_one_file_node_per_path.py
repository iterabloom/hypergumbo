# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: every path has one file pseudo-symbol (INV-movor).

A Svelte or Vue component is read by two analyzers. The JavaScript analyzer
parses its ``<script>`` and mints ``typescript:src/App.vue`` (the script's
language); the component analyzer anchors the template's edges on
``vue:src/App.vue``. Both nodes used to persist, with the file's edges split
between them, and the Vue component linker added a ``vue:`` node for an
imported ``.svelte`` file. These tests run the whole pipeline on components
that import each other and pin, for every path of the tree:

- exactly one file pseudo-symbol;
- it is the component's own-language node wherever a producer anchored on it;
- every edge of both former nodes starts or ends at it, and none is lost;
- the symbols declared in a ``<script>`` keep the ``stable_id`` the JavaScript
  analyzer gives them on its own.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.js_ts import analyze_javascript, is_tree_sitter_available

pytestmark = pytest.mark.skipif(
    not is_tree_sitter_available(), reason="tree-sitter not available",
)

_FILES = {
    "src/main.ts": (
        'import App from "./App.svelte";\nimport VApp from "./App.vue";\n'
        'import { helper } from "./util";\n'
        "export function boot() { helper(); return [App, VApp]; }\n"
    ),
    "src/util.ts": "export function helper() { return 1; }\n",
    "src/App.svelte": (
        '<script lang="ts">\n  import Child from "./Child.svelte";\n'
        '  import { helper } from "./util";\n  export let name: string = "x";\n'
        "  function go() { helper(); }\n</script>\n<Child on:click={go} />\n"
    ),
    "src/Child.svelte": '<script>\n  export let label = "c";\n</script>\n<button>{label}</button>\n',
    "src/App.vue": (
        '<template><VChild :label="name" /></template>\n<script setup lang="ts">\n'
        'import VChild from "./VChild.vue";\nimport { helper } from "./util";\n'
        'const name = "x";\nfunction go() { helper(); }\n</script>\n'
    ),
    "src/VChild.vue": (
        "<template><button>{{ label }}</button></template>\n<script>\n"
        'export default { props: ["label"] };\n</script>\n'
    ),
}


@pytest.fixture(scope="module")
def survey(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    root = tmp_path_factory.mktemp("sfc") / "repo"
    for rel, body in _FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    out = root.parent / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return root, json.loads(out.read_text())


def _file_nodes(data: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for node in data["nodes"]:
        if node["kind"] == "file" and node["id"].endswith(":1-1:file:file"):
            out.setdefault(node["path"], []).append(node)
    return out


def test_every_path_has_exactly_one_file_pseudo_symbol(survey: tuple[Path, dict]) -> None:
    _, data = survey
    per_path = _file_nodes(data)
    assert set(per_path) == set(_FILES)
    assert {p: [n["id"] for n in nodes] for p, nodes in per_path.items() if len(nodes) != 1} == {}


def test_a_component_a_producer_anchors_on_keeps_its_own_language(
    survey: tuple[Path, dict],
) -> None:
    _, data = survey
    per_path = _file_nodes(data)
    for path, language in (("src/App.svelte", "svelte"), ("src/App.vue", "vue")):
        (node,) = per_path[path]
        assert node["language"] == language
        assert node["id"] == f"{language}:{path}:1-1:file:file"
        # Both producers stay on the record.
        assert "javascript" in node["origin"]
        assert "orchestrator_file_symbol_synthesis" in node["origin"]
    # No file node carries another component format's language.
    for path, (node,) in per_path.items():
        assert not (path.endswith(".svelte") and node["language"] == "vue"), node["id"]


def test_the_files_edges_meet_on_the_one_node(survey: tuple[Path, dict]) -> None:
    _, data = survey
    node_ids = {n["id"] for n in data["nodes"]}
    assert [e for e in data["edges"] if e["src"] not in node_ids or e["dst"] not in node_ids] == []

    per_path = _file_nodes(data)
    edges = Counter((e["src"], e["type"], e["dst"]) for e in data["edges"])
    for path in ("src/App.svelte", "src/App.vue"):
        file_id = per_path[path][0]["id"]
        # The script's side: its import of ./util, and containment of its members.
        assert edges[(file_id, "imports", "typescript:src/util.ts:1-1:file:file")] == 1
        members = {
            n["id"] for n in data["nodes"]
            if n["path"] == path and n["kind"] in ("function", "variable")
        }
        assert len(members) == 2
        assert all(edges[(file_id, "contains", member)] == 1 for member in members)
        # The template's side: the component import the template analyzer found.
        language = per_path[path][0]["language"]
        assert any(
            e["src"] == file_id and e["type"] == "imports" and language in e["origin"]
            for e in data["edges"]
        )
        # An importer of the component lands on the same node.
        assert edges[("typescript:src/main.ts:1-1:file:file", "imports", file_id)] == 1
    # The imported child components are reached at their one node.
    for importer, child in (("src/App.svelte", "src/Child.svelte"), ("src/App.vue", "src/VChild.vue")):
        assert edges[(per_path[importer][0]["id"], "imports", per_path[child][0]["id"])] == 1


def test_script_members_keep_the_script_analyzers_stable_ids(
    survey: tuple[Path, dict],
) -> None:
    root, data = survey
    alone = {
        (str(Path(s.path).relative_to(root)) if Path(s.path).is_absolute() else s.path,
         s.kind, s.name): s.stable_id
        for s in analyze_javascript(root).symbols
        if s.kind != "file" and s.path.endswith((".svelte", ".vue"))
    }
    assert len(alone) == 5  # name + go in each App, label in Child.svelte
    piped = {
        (n["path"], n["kind"], n["name"]): n["stable_id"]
        for n in data["nodes"]
        if n["kind"] != "file" and n["language"] in ("typescript", "javascript")
        and n["path"].endswith((".svelte", ".vue"))
    }
    assert piped == alone
    assert all(alone.values())


def test_a_component_import_ends_on_the_imported_files_node(
    survey: tuple[Path, dict],
) -> None:
    """The template analyzers resolve ``./Child.svelte`` / ``./VChild.vue``
    themselves: no stand-in for a file that is in the tree (WI-modan)."""
    _, data = survey
    per_path = _file_nodes(data)
    for importer, child, language in (
        ("src/App.svelte", "src/Child.svelte", "svelte"),
        ("src/App.vue", "src/VChild.vue", "vue"),
    ):
        (edge,) = [
            e for e in data["edges"]
            if e["type"] == "imports" and e["src"] == per_path[importer][0]["id"]
            and e["dst"] == per_path[child][0]["id"]
        ]
        # One edge says both passes found it: the template analyzer and the
        # linker that resolved the script's import statement.
        assert language in edge["origin"] and "js-module-linker" in edge["origin"]
        assert edge["is_resolved"] is True
        assert edge["meta"]["component_name"] in ("Child", "VChild")
    # No component analyzer's edge ends on a stand-in for an in-tree file.
    stand_ins = [
        n["id"] for n in data["nodes"]
        if n["kind"] == "external_symbol" and n["id"].split(":")[0] in ("svelte", "vue")
    ]
    assert stand_ins == []


def test_a_reference_does_not_re_slot_the_imported_files_node(
    survey: tuple[Path, dict],
) -> None:
    """``Child.svelte`` and ``VChild.vue`` are only referred to; each keeps the
    one node, and the stable id, the script analyzer gave it."""
    from hypergumbo_core.analyze.base import make_file_stable_id

    _, data = survey
    per_path = _file_nodes(data)
    for path in ("src/Child.svelte", "src/VChild.vue"):
        (node,) = per_path[path]
        assert node["id"] == f"javascript:{path}:1-1:file:file"
        assert node["stable_id"] == make_file_stable_id("javascript", path)


def test_the_scripts_import_placeholder_is_still_demoted(
    survey: tuple[Path, dict],
) -> None:
    """Folding the template analyzer's edge with the linker's must keep the
    linker's statement of which placeholder it resolved (ADR-0057 14.1)."""
    _, data = survey
    by_id = {e["id"]: e for e in data["edges"]}
    stubs = [
        e for e in data["edges"]
        if e["type"] == "imports" and e["dst"].endswith(":0-0:module:external_symbol")
        and ("Child.svelte" in e["dst"] or "VChild.vue" in e["dst"])
    ]
    assert len(stubs) == 2
    for stub in stubs:
        resolved = by_id[stub["meta"]["superseded_by"]]
        assert resolved["src"] == stub["src"] and resolved["is_resolved"] is True
        assert resolved["dst"].endswith(":1-1:file:file")

