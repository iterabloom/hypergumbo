# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/vue.py
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/svelte.py
"""A template directive's bound expression is code, so its layout is not an
id input (WI-radim).

A Vue directive's stable id hashes its bound expression in its scope
(``=tab === 'a'``) to tell two same directives on sibling elements apart. It
was hashed as written, so ``v-if="tab === 'a'"`` and ``v-if="tab  ===  'a'"``
(or the same split over lines) were two ids. It now enters the hash in
canonical spacing (``layout_free``), which keeps a string literal's inner
spaces (``'a b'`` is not ``'a  b'``). The same holds for a Svelte block's
expression in the scope label of what the block holds.

tree-sitter-vue also reads a valueless same-name shorthand (``:album``, Vue
3.4) and the attributes after it as ONE directive attribute; the analyzer
named it ``v-bind:album\\n    :layout``, with the newline. Each name before
the ``=`` is now its own directive, and only the last has the value.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import make_doc_stable_id
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.svelte import analyze_svelte
from hypergumbo_lang_common.vue import analyze_vue


def _vue(root: Path, template: str) -> list[Symbol]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "C.vue").write_text(f"<template>\n  <div>\n{template}\n  </div>\n</template>\n")
    return [s for s in analyze_vue(root).symbols if s.kind == "directive"]


def _one(symbols: list[Symbol], name: str) -> Symbol:
    found = [s for s in symbols if s.name == name]
    assert len(found) == 1, (name, [s.name for s in symbols])
    return found[0]


_SAME = {
    "spaced-operator": ('<Item v-if="tab === \'a\'" />', '<Item v-if="tab  ===  \'a\'" />'),
    "line-broken": ('<Item v-if="tab === \'a\'" />', '<Item v-if="tab ===\n      \'a\'" />'),
    "unspaced-operator": ('<Other v-bind:val="x + 1" />', '<Other v-bind:val="x+1" />'),
    "shorthand-unspaced": ('<Other :val="f(a, b)" />', '<Other :val="f( a,b )" />'),
}


@pytest.mark.parametrize("shape", sorted(_SAME))
def test_a_bound_expression_laid_out_otherwise_keeps_its_id(tmp_path: Path, shape: str) -> None:
    one, other = _SAME[shape]
    a, b = _vue(tmp_path / "a", one), _vue(tmp_path / "b", other)
    assert len(a) == len(b) == 1
    assert a[0].name == b[0].name
    assert a[0].stable_id == b[0].stable_id, shape


def test_a_string_literal_keeps_its_inner_spaces(tmp_path: Path) -> None:
    a = _vue(tmp_path / "a", '<Item v-if="tab === \'a b\'" />')
    b = _vue(tmp_path / "b", '<Item v-if="tab === \'a  b\'" />')
    assert a[0].stable_id != b[0].stable_id


def test_two_expressions_stay_two_ids(tmp_path: Path) -> None:
    found = _vue(tmp_path / "r", '<p v-if="loading" />\n<p v-if="error" />')
    assert len({s.stable_id for s in found}) == 2


def test_a_canonically_spaced_expression_hashes_what_it_did(tmp_path: Path) -> None:
    """The scope entry of an expression already in canonical spacing
    (``count > 0``, a bare name) is the text as written, as before; one
    spelled otherwise enters in the canonical spacing (``f(a,b)``)."""
    found = _vue(tmp_path / "r", '<p v-if="count > 0" />\n<i v-if="ready" />\n<b :v="f(a, b)" />')
    for name, bound in (("p:v-if", "count > 0"), ("i:v-if", "ready"), ("b:v-bind:v", "f(a,b)")):
        assert _one(found, name).stable_id == make_doc_stable_id(
            "vue", "C.vue", "directive", name, 0, 0,
            scope=["template", "div", f"={bound}"],
        ), name


def test_a_valueless_shorthand_is_its_own_directive(tmp_path: Path) -> None:
    found = _vue(
        tmp_path / "r",
        "<AlbumCard\n  :album\n  :layout=\"itemLayout\"\n/>\n"
        "<Controls\n  :config\n  @filter=\"applyFilter\"\n/>",
    )
    assert sorted(s.name for s in found) == [
        "AlbumCard:v-bind:album", "AlbumCard:v-bind:layout",
        "Controls:v-bind:config", "Controls:v-on:filter",
    ]
    assert all("\n" not in s.name for s in found)
    assert (_one(found, "Controls:v-on:filter").meta or {}).get("handler_expression") == "applyFilter"
    assert "handler_expression" not in (_one(found, "Controls:v-bind:config").meta or {})
    assert _one(found, "AlbumCard:v-bind:layout").stable_id == make_doc_stable_id(
        "vue", "C.vue", "directive", "AlbumCard:v-bind:layout", 0, 0,
        scope=["template", "div", "=itemLayout"],
    )
    assert _one(found, "AlbumCard:v-bind:album").stable_id == make_doc_stable_id(
        "vue", "C.vue", "directive", "AlbumCard:v-bind:album", 0, 0,
        scope=["template", "div"],
    )


def _svelte_slot(root: Path, block: str) -> Symbol:
    root.mkdir(parents=True, exist_ok=True)
    (root / "C.svelte").write_text(f"{block}\n  <slot name=\"x\" />\n{{/if}}\n")
    slots = [s for s in analyze_svelte(root).symbols if s.kind == "slot"]
    assert len(slots) == 1
    return slots[0]


def test_a_svelte_block_expression_laid_out_otherwise_keeps_the_ids_inside(
    tmp_path: Path,
) -> None:
    """A slot inside ``{#if count > 0}`` hashes the block as its scope label,
    in canonical spacing."""
    a = _svelte_slot(tmp_path / "a", "{#if count > 0}")
    b = _svelte_slot(tmp_path / "b", "{#if count  >\n  0}")
    c = _svelte_slot(tmp_path / "c", "{#if count > 1}")
    assert a.stable_id == b.stable_id
    assert a.stable_id != c.stable_id
