# SPDX-License-Identifier: AGPL-3.0-or-later
"""The grammar of a Svelte / Vue ``<script>`` block is read from its ``lang``
attribute wherever that attribute sits (WI-zakij).

The old extraction regexes looked for ``lang`` only directly after ``<script``
(Vue: after an optional ``setup``), so ``<script context="module" lang="ts">``,
``<script type="module" lang="ts">`` and ``<script setup generic="T" lang="ts">``
were all parsed with the JavaScript grammar and their symbols came out
``language="javascript"``. Their ``[^>]*`` tag tail also stopped at the first
``>``, so a Vue 3.3 ``generic="T extends Record<string, unknown>"`` attribute
cut the tag short and fed the rest of it to the parser as code.

Invariant: the grammar is a function of the attribute SET, independent of its
order and of HTML's three value spellings (double-, single- and unquoted).
The fixtures discriminate on a TypeScript-only construct: an ``interface``
symbol is produced only by the TypeScript grammar.
"""
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.js_ts import (
    _get_parser_for_lang,
    analyze_javascript,
    extract_svelte_scripts,
    extract_vue_scripts,
    is_tree_sitter_available,
)

pytestmark = pytest.mark.skipif(
    not is_tree_sitter_available(), reason="tree-sitter not available",
)

_TS_BODY = (
    "interface Props { label: string }\n"
    "export function typed(a: string): string { return a }\n"
)

#: Opening tags that declare TypeScript, in orders and spellings the old
#: first-attribute-only regexes missed (and the first-position forms they
#: handled, which must keep working).
_TS_TAGS = [
    '<script lang="ts">',
    '<script context="module" lang="ts">',
    "<script context=module lang=ts>",
    "<script lang='typescript' context=\"module\">",
    '<script type="module" lang="ts">',
    '<script setup lang="ts">',
    '<script setup generic="T" lang="ts">',
    '<script setup generic="T extends Record<string, unknown>" lang="ts">',
    '<script lang = "TS" setup>',
    '<script\n  setup\n  lang="ts"\n>',
]

#: Opening tags that do NOT declare TypeScript.
_JS_TAGS = [
    "<script>",
    '<script context="module">',
    '<script lang="js">',
    '<script data-lang="ts">',
    '<script setup title="lang=ts">',
]


def _write_component(tmp_path: Path, suffix: str, tag: str) -> Path:
    path = tmp_path / f"Comp{suffix}"
    path.write_text(f"{tag}\n{_TS_BODY}</script>\n<div>hi</div>\n")
    return path


@pytest.mark.parametrize("suffix", [".svelte", ".vue"])
@pytest.mark.parametrize("tag", _TS_TAGS)
def test_lang_ts_anywhere_in_the_tag_selects_typescript(
    tmp_path: Path, suffix: str, tag: str,
) -> None:
    path = _write_component(tmp_path, suffix, tag)
    result = analyze_javascript(tmp_path)
    mine = [s for s in result.symbols if Path(s.path).name == path.name]
    by_name = {s.name: s for s in mine if s.kind != "file"}
    # Reach: the block was found and parsed at all.
    assert "typed" in by_name, [(s.name, s.kind) for s in mine]
    assert by_name["typed"].language == "typescript"
    # Only the TypeScript grammar yields an interface declaration.
    assert by_name.get("Props") is not None, sorted(by_name)
    assert by_name["Props"].kind == "interface"
    # The body starts on the line after the (possibly multi-line) tag.
    body_line = tag.count("\n") + 3
    assert by_name["typed"].span.start_line == body_line


@pytest.mark.parametrize("extract", [extract_svelte_scripts, extract_vue_scripts])
@pytest.mark.parametrize("tag", _JS_TAGS)
def test_no_ts_lang_attribute_selects_javascript(extract, tag: str) -> None:
    blocks = extract(f"{tag}\nconst x = 1;\n</script>\n")
    assert len(blocks) == 1
    assert blocks[0].is_typescript is False
    assert blocks[0].is_tsx is False


@pytest.mark.parametrize("extract", [extract_svelte_scripts, extract_vue_scripts])
def test_quoted_gt_does_not_end_the_tag(extract) -> None:
    source = (
        '<script setup generic="T extends Record<string, unknown>" lang="ts">\n'
        "const a = 1\n"
        "</script>\n"
    )
    blocks = extract(source)
    assert len(blocks) == 1
    assert blocks[0].is_typescript is True
    # No leftover tag text (``unknown>" lang="ts">``) leaks into the body.
    assert blocks[0].content == "\nconst a = 1\n"


@pytest.mark.parametrize("extract", [extract_svelte_scripts, extract_vue_scripts])
def test_self_closing_script_does_not_swallow_the_next_block(extract) -> None:
    source = (
        '<script src="./external.ts" lang="ts" />\n'
        '<script setup lang="ts">\n'
        "const a = 1\n"
        "</script>\n"
    )
    blocks = extract(source)
    assert [b.content for b in blocks] == ["\nconst a = 1\n"]
    assert blocks[0].start_line == 2


@pytest.mark.parametrize("extract", [extract_svelte_scripts, extract_vue_scripts])
def test_lang_tsx_selects_the_tsx_grammar(extract) -> None:
    body = (
        "const view = (x: unknown) => <div>{x as string}</div>\n"
        "export function after(a: number): number { return a }\n"
    )
    blocks = extract(f'<script setup lang="tsx">\n{body}</script>\n')
    assert len(blocks) == 1
    assert blocks[0].is_typescript is True
    assert blocks[0].is_tsx is True
    source = blocks[0].content.encode("utf-8")
    tsx_tree = _get_parser_for_lang(True, tsx=True).parse(source)
    assert tsx_tree.root_node.has_error is False
    # The plain TypeScript grammar cannot read the JSX: the grammar choice is
    # load-bearing, not cosmetic.
    assert _get_parser_for_lang(True).parse(source).root_node.has_error is True


def test_lang_tsx_component_symbols_are_typescript(tmp_path: Path) -> None:
    (tmp_path / "View.vue").write_text(
        '<script setup lang="tsx">\n'
        "const view = (x: unknown) => <div>{x as string}</div>\n"
        "export function after(a: number): number { return a }\n"
        "</script>\n"
    )
    result = analyze_javascript(tmp_path)
    after = [s for s in result.symbols if s.name == "after"]
    assert len(after) == 1
    assert after[0].language == "typescript"
    assert after[0].span.start_line == 3
