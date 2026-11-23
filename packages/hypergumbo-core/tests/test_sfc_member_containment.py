# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/linkers/containment.py
"""Component and template members are contained by their file.

A Vue directive, slot, prop or style block, a Svelte event, block or slot and
an Astro client directive or slot sit in their file's span outside any code
scope; the containment linker's Phase 2 roots them at the file node once their
kinds are containable. The same kinds of other producers are rooted too: a
Solidity event at its contract, a GraphQL directive definition, a Prisma
datasource block and a Twig block at their file. Run through the whole
pipeline, so the file nodes are the ones the run keeps.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map

_FILES = {
    "src/App.vue": (
        "<template>\n"
        "  <div>\n"
        "    <button @click=\"save\">Save</button>\n"
        "    <slot name=\"footer\"></slot>\n"
        "  </div>\n"
        "</template>\n"
        "<script>\n"
        "export default {\n"
        "  props: ['title'],\n"
        "  methods: { save() {} }\n"
        "}\n"
        "</script>\n"
        "<style scoped>\n.a { color: red; }\n</style>\n"
    ),
    "src/Comp.svelte": (
        "<script>\n  let count = 0;\n</script>\n"
        "<button on:click={() => count++}>+</button>\n"
        "{#if count > 0}\n  <p>{count}</p>\n{/if}\n"
        "<slot name=\"x\"></slot>\n"
    ),
    "src/Page.astro": (
        "---\nimport Card from './Card.astro';\nconst title = \"Hi\";\n---\n"
        "<main>\n  <Card client:load />\n  <slot name=\"side\" />\n</main>\n"
    ),
    "src/Card.astro": "<div><slot /></div>\n",
    "src/Token.sol": (
        "pragma solidity ^0.8.0;\n"
        "contract Token {\n"
        "    event Transfer(address from, address to, uint256 value);\n"
        "    function send(address to) public { emit Transfer(msg.sender, to, 1); }\n"
        "}\n"
    ),
    "src/schema.graphql": "directive @auth(role: String) on FIELD_DEFINITION\n"
                          "type Query {\n  me: String @auth(role: \"user\")\n}\n",
    "src/schema.prisma": "datasource db {\n  provider = \"postgresql\"\n}\n",
    "src/page.html.twig": "{% block body %}\n  <p>hi</p>\n{% endblock %}\n",
}

_MEMBER_KINDS = {"directive", "slot", "prop", "event", "block", "style_block"}


def _run(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    for rel, text in _FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return data


def _containers(data: dict[str, Any]) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = defaultdict(list)
    for edge in data["edges"]:
        if edge["type"] == "contains":
            parents[edge["dst"]].append(edge["src"])
    return parents


def test_every_member_kind_is_contained_once(tmp_path: Path) -> None:
    data = _run(tmp_path)
    parents = _containers(data)
    nodes = {n["id"]: n for n in data["nodes"]}
    members = [n for n in data["nodes"] if n["kind"] in _MEMBER_KINDS]
    # Reach: every kind of every producer named in the docstring is there.
    assert {(n["language"], n["kind"]) for n in members} >= {
        ("vue", "directive"), ("vue", "slot"), ("vue", "prop"), ("vue", "style_block"),
        ("svelte", "event"), ("svelte", "block"), ("svelte", "slot"),
        ("astro", "directive"), ("astro", "slot"),
        ("solidity", "event"), ("graphql", "directive"),
        ("prisma", "block"), ("twig", "block"),
    }
    for member in members:
        (parent_id,) = parents[member["id"]]
        parent = nodes[parent_id]
        assert parent["path"] == member["path"], (member["id"], parent_id)
        if member["language"] == "solidity":
            assert (parent["kind"], parent["name"]) == ("contract", "Token")
        else:
            assert parent["kind"] == "file", (member["id"], parent_id)


def test_files_and_already_contained_members_are_unchanged(tmp_path: Path) -> None:
    # Control: a file node is no member; an Astro frontmatter variable and the
    # Solidity function were contained before, each by one parent.
    data = _run(tmp_path)
    parents = _containers(data)
    for node in data["nodes"]:
        if node["kind"] == "file":
            assert node["id"] not in parents
    variable = next(n for n in data["nodes"] if n["kind"] == "variable" and n["name"] == "title")
    assert len(parents[variable["id"]]) == 1
    send = next(n for n in data["nodes"] if n["name"] == "Token.send")
    assert len(parents[send["id"]]) == 1


def _symbol(sym_id: str, kind: str, start: int, end: int) -> Any:
    from hypergumbo_core.ir import Span, Symbol

    return Symbol(
        id=sym_id, name=sym_id.rsplit(":", 2)[1], kind=kind, language="javascript",
        path="src/App.vue", span=Span(start, end, 0, 0),
    )


def test_a_file_node_encloses_its_whole_file_and_is_the_loosest_container() -> None:
    # The JavaScript analyzer's file node of a component spans the <script>
    # block (7-12 here). A template member above it is still in the file; a
    # member inside a class keeps the class, the tighter container.
    from hypergumbo_core.linkers.containment import link_containment
    from hypergumbo_core.linkers.registry import LinkerContext

    file_node = _symbol("javascript:src/App.vue:1-1:file:file", "file", 7, 12)
    # A class whose span the file node's recorded span does not enclose (a
    # second <script> block): its member is the class's, not the file's.
    klass = _symbol("javascript:src/App.vue:20-30:K:class", "class", 20, 30)
    in_class = _symbol("javascript:src/App.vue:25-25:v:variable", "variable", 25, 25)
    template_member = _symbol("vue:src/App.vue:3-3:p.v-if:directive", "directive", 3, 3)
    elsewhere = _symbol("vue:src/B.vue:3-3:x:slot", "slot", 3, 3)
    elsewhere.path = "src/B.vue"
    edges = link_containment(LinkerContext(
        repo_root=Path("/repo"),
        symbols=[file_node, klass, in_class, template_member, elsewhere],
        edges=[],
    )).edges
    pairs = {(e.src, e.dst) for e in edges}
    assert (file_node.id, template_member.id) in pairs
    assert (klass.id, in_class.id) in pairs
    assert (file_node.id, in_class.id) not in pairs
    # Control: another path's member is not this file's.
    assert not any(dst == elsewhere.id for _, dst in pairs)
