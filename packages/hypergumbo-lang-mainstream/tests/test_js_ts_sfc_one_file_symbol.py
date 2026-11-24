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
- it is in the component's own language slot (``vue:`` / ``svelte:``,
  WI-vavag), whatever language its blocks are in; every block's member
  symbols keep their own block's language;
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
        "vue",
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
        "svelte",
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
        "vue",
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
    member's stable id names the file it is in, not a ``javascript`` or
    ``typescript`` file symbol that does not exist."""
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


# WI-vavag: a component with ONE script block and nothing else. The JavaScript
# analyzer is then the only producer of the file's node, so the slot it picks
# is the slot the graph keeps.
_SCRIPT_ONLY = {
    "vue_setup_ts": ("Only.vue", '<script setup lang="ts">\nfunction hi(): number { return 1 }\n</script>\n'),
    "vue_js": ("Only.vue", "<script>\nfunction hi() { return 1 }\n</script>\n"),
    "svelte_js": ("Only.svelte", "<script>\nfunction hi() { return 1 }\n</script>\n"),
    "svelte_ts": ("Only.svelte", '<script lang="ts">\nfunction hi(): number { return 1 }\n</script>\n'),
}


@pytest.mark.parametrize("case", sorted(_SCRIPT_ONLY))
def test_a_script_only_component_file_symbol_is_in_its_own_slot(
    tmp_path: Path, case: str,
) -> None:
    from hypergumbo_core.analyze.base import make_file_id

    file_name, content = _SCRIPT_ONLY[case]
    (tmp_path / file_name).write_text(content)
    (tmp_path / "plain.ts").write_text("export function hi(): number { return 1 }\n")
    symbols = analyze_javascript(tmp_path).symbols
    host = file_name.rsplit(".", 1)[1]
    file_syms = {s.path.rsplit("/", 1)[-1]: s for s in symbols if s.kind == "file"}
    # Reach: both files were analysed and the member was extracted.
    assert set(file_syms) == {file_name, "plain.ts"}
    (member,) = [s for s in symbols if s.name == "hi" and s.path.endswith(file_name)]
    assert file_syms[file_name].language == host
    assert file_syms[file_name].id == make_file_id(host, file_syms[file_name].path)
    # The member keeps its script's language and its id's language slot.
    script_lang = "typescript" if 'lang="ts"' in content else "javascript"
    assert member.language == script_lang
    assert member.id.startswith(f"{script_lang}:")
    # Control: a plain TypeScript file keeps its own slot.
    assert file_syms["plain.ts"].language == "typescript"


@pytest.mark.parametrize("host", ["vue", "svelte"])
def test_a_members_identity_does_not_depend_on_the_script_language(
    tmp_path: Path, host: str,
) -> None:
    """A member is anchored on the component's file, whose slot is the
    component's own language: changing ``<script>`` to ``<script lang="ts">``
    around a body that both grammars read alike moves no member id. Before
    WI-vavag the anchor was ``make_file_stable_id("javascript", path)`` in the
    first file and ``("typescript", path)`` in the second."""
    body = "export function hi() { return 1 }\n"
    as_js, as_ts = tmp_path / "js", tmp_path / "ts"
    as_js.mkdir()
    as_ts.mkdir()
    (as_js / f"C.{host}").write_text(f"<script>\n{body}</script>\n")
    (as_ts / f"C.{host}").write_text(f'<script lang="ts">\n{body}</script>\n')
    # Control: the same body in a plain .js and .ts file DOES move, because
    # there the file's own language is the script's.
    (as_js / "p.js").write_text(body)
    (as_ts / "p.js").write_text(body)
    (as_ts / "p.js").rename(as_ts / "p.ts")

    def hi_of(repo: Path, suffix: str):
        return next(
            s for s in analyze_javascript(repo).symbols
            if s.name == "hi" and s.path.endswith(suffix)
        )

    js_member, ts_member = hi_of(as_js, f".{host}"), hi_of(as_ts, f".{host}")
    # Reach: the two members really were read by two grammars.
    assert (js_member.language, ts_member.language) == ("javascript", "typescript")
    assert js_member.stable_id is not None
    assert js_member.stable_id == ts_member.stable_id
    assert hi_of(as_js, ".js").stable_id != hi_of(as_ts, ".ts").stable_id


def test_a_member_is_anchored_on_the_file_symbol_that_exists(tmp_path: Path) -> None:
    """Each member's stable id is the one ``_extract_symbols`` mints with the
    file symbol's own language as the anchor, and not the one it mints with
    the member's language (the anchor WI-vavag retired)."""
    from hypergumbo_lang_mainstream.js_ts import _extract_symbols, _get_parser_for_lang

    content = '<script setup lang="ts">\nfunction hi(): number { return 1 }\n</script>\n'
    path = tmp_path / "Only.vue"
    path.write_text(content)
    symbols = analyze_javascript(tmp_path).symbols
    (file_sym,) = [s for s in symbols if s.kind == "file"]
    (member,) = [s for s in symbols if s.name == "hi"]
    source = b"function hi(): number { return 1 }\n"
    tree = _get_parser_for_lang(True).parse(source)
    run = AnalysisRun.create(pass_id="test", version="test")

    def anchored_on(file_lang: str):
        return next(
            s for s in _extract_symbols(
                tree, source, path, "typescript", run, 1,
                repo_root=tmp_path, file_lang=file_lang,
            ) if s.name == "hi"
        ).stable_id

    assert member.stable_id == anchored_on(file_sym.language)
    # Control: the anchor is a real hash input.
    assert member.stable_id != anchored_on("typescript")
