# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Svelte / Vue file with several ``<script>`` blocks yields ONE file symbol
(WI-rolub).

The SFC loops called ``_extract_symbols`` once per block, and each call minted
the file pseudo-node ``make_file_id(lang, path)``. ``<script lang="ts">`` plus
``<script setup lang="ts">`` gave two symbols with the same id; a Svelte file
mixing a JavaScript ``context="module"`` block with a TypeScript instance
block gave a ``javascript:`` and a ``typescript:`` file node for one file.

Invariants pinned here, for every multi-block shape:
- exactly one ``kind="file"`` symbol per source file, and no duplicate ids;
- its language is ``typescript`` when any block is TypeScript (the superset),
  else ``javascript``; every block's member symbols keep their own block's
  language;
- its span covers every block's members;
- every file-anchored edge (``imports``) of every block starts at that one
  file symbol, so no block's imports hang off a file id that does not exist.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun
from hypergumbo_lang_mainstream.js_ts import (
    _analyze_svelte_file,
    _analyze_vue_file,
    analyze_javascript,
    is_tree_sitter_available,
)

pytestmark = pytest.mark.skipif(
    not is_tree_sitter_available(), reason="tree-sitter not available",
)

# name -> (file name, content, expected file-symbol language,
#          {member name: expected member language})
_CASES = {
    "vue_ts_plus_setup_ts": (
        "B.vue",
        '<script lang="ts">\n'
        "import { defineComponent } from 'vue'\n"
        "export function legacy(n: number): number { return n }\n"
        "</script>\n"
        '<script setup lang="ts">\n'
        "import { ref } from 'vue'\n"
        "function inc(n: number): number { return n + 1 }\n"
        "</script>\n"
        "<template><div/></template>\n",
        "typescript",
        {"legacy": "typescript", "inc": "typescript"},
    ),
    "svelte_js_module_plus_ts_instance": (
        "C.svelte",
        '<script context="module">\n'
        "import { tick } from 'svelte'\n"
        "export function helper() { return 1 }\n"
        "</script>\n"
        '<script lang="ts">\n'
        "import { onMount } from 'svelte'\n"
        "function bump(): void { helper() }\n"
        "</script>\n"
        "<h1>hi</h1>\n",
        "typescript",
        {"helper": "javascript", "bump": "typescript"},
    ),
    "vue_js_plus_setup_js": (
        "J.vue",
        "<script>\n"
        "import a from './a.js'\n"
        "export function one() { return 1 }\n"
        "</script>\n"
        "<script setup>\n"
        "import b from './b.js'\n"
        "function two() { return one() }\n"
        "</script>\n",
        "javascript",
        {"one": "javascript", "two": "javascript"},
    ),
}


def _check_one_file_symbol(symbols, edges, file_name, file_lang, members):
    mine = [s for s in symbols if Path(s.path).name == file_name]
    file_syms = [s for s in mine if s.kind == "file"]
    assert len(file_syms) == 1, [(s.id, s.language) for s in file_syms]
    file_sym = file_syms[0]
    assert file_sym.language == file_lang
    assert file_sym.id.startswith(f"{file_lang}:")
    ids = [s.id for s in symbols]
    assert len(ids) == len(set(ids)), "duplicate symbol ids"
    by_name = {s.name: s for s in mine if s.kind != "file"}
    # Reach: every block's members were extracted.
    for name, lang in members.items():
        assert name in by_name, sorted(by_name)
        assert by_name[name].language == lang
        assert file_sym.span.start_line <= by_name[name].span.start_line
        assert by_name[name].span.end_line <= file_sym.span.end_line
    imports = [
        e for e in edges
        if e.edge_type == "imports" and Path(e.src.split(":")[1]).name == file_name
    ]
    # Reach: both blocks contribute an import.
    assert len(imports) == 2, [(e.src, e.dst) for e in imports]
    assert {e.src for e in imports} == {file_sym.id}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_multi_block_component_has_one_file_symbol(tmp_path: Path, case: str) -> None:
    file_name, content, file_lang, members = _CASES[case]
    (tmp_path / file_name).write_text(content)
    result = analyze_javascript(tmp_path)
    _check_one_file_symbol(result.symbols, result.edges, file_name, file_lang, members)


@pytest.mark.parametrize("case", sorted(_CASES))
def test_single_file_helpers_have_one_file_symbol(tmp_path: Path, case: str) -> None:
    file_name, content, file_lang, members = _CASES[case]
    path = tmp_path / file_name
    path.write_text(content)
    helper = _analyze_vue_file if file_name.endswith(".vue") else _analyze_svelte_file
    run = AnalysisRun.create(pass_id="test", version="test")
    symbols, edges, success = helper(path, run)
    assert success is True
    _check_one_file_symbol(symbols, edges, file_name, file_lang, members)


def test_member_identity_anchors_on_the_one_file_symbol(tmp_path: Path) -> None:
    """Members fold the ONE file symbol's identity anchor (its language), so a
    JavaScript block's ``helper`` in a TypeScript component hashes exactly as
    the same ``helper`` does when that block is itself ``lang="ts"``: the
    member's stable id names the file it is in, not a ``javascript`` file
    symbol that no longer exists."""
    _, content, _, _ = _CASES["svelte_js_module_plus_ts_instance"]
    mixed, all_ts = tmp_path / "mixed", tmp_path / "all_ts"
    mixed.mkdir()
    all_ts.mkdir()
    (mixed / "C.svelte").write_text(content)
    (all_ts / "C.svelte").write_text(
        content.replace('<script context="module">', '<script context="module" lang="ts">'),
    )

    def helper_of(repo: Path):
        result = analyze_javascript(repo)
        return next(s for s in result.symbols if s.name == "helper")

    mixed_helper, ts_helper = helper_of(mixed), helper_of(all_ts)
    # Reach: the mutation really changed the block's grammar.
    assert (mixed_helper.language, ts_helper.language) == ("javascript", "typescript")
    assert mixed_helper.stable_id is not None
    assert mixed_helper.stable_id == ts_helper.stable_id


def test_edges_without_a_file_symbol_anchor_on_the_minted_file_id(tmp_path: Path) -> None:
    """A direct ``_extract_edges`` caller that hands in no file symbol keeps
    the old anchor: the file id minted from the tree's own language."""
    from hypergumbo_core.analyze.base import make_file_id
    from hypergumbo_lang_mainstream.js_ts import _extract_edges, _get_parser_for_lang

    source = b"import x from './x.js'\nconst y = require('./y.js')\n"
    path = tmp_path / "a.js"
    tree = _get_parser_for_lang(False).parse(source)
    run = AnalysisRun.create(pass_id="test", version="test")
    edges = _extract_edges(tree, source, path, "javascript", run, {}, {}, {})
    imports = [e for e in edges if e.edge_type == "imports"]
    assert len(imports) == 2
    assert {e.src for e in imports} == {make_file_id("javascript", str(path))}
