# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/vue.py
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/svelte.py
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/astro.py
"""Node ids of the single-file-component members (Vue, Svelte, Astro).

ADR-0036 Ruling 1: a node id's name slot is ``sanitize_id_name_segment``
of ``Symbol.name``. A Vue directive, a Svelte event or block and an Astro
client directive mint their ids (and their stable ids) from one string that
names the element too (``button:v-on:click``, ``if:count > 0``); the name
is that string, so the slot and the name agree and neither id moves.
"""

from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id
from hypergumbo_core.ir import Symbol, sanitize_id_name_segment
from hypergumbo_lang_common.astro import analyze_astro
from hypergumbo_lang_common.svelte import analyze_svelte
from hypergumbo_lang_common.vue import analyze_vue

_VUE = """<template>
  <div>
    <button @click="save" :title="t">Save</button>
    <p v-if="loading">x</p>
    <slot name="footer"></slot>
  </div>
</template>
<script>
export default { props: ['title'] }
</script>
<style scoped>
.a { color: red; }
</style>
"""

_SVELTE = """<script>
  let count = 0;
</script>
<button on:click={() => count++}>+</button>
{#if count > 0}
  <p>{count}</p>
{/if}
<slot name="x"></slot>
"""

_ASTRO = """---
import Card from './Card.astro';
const title = "Hi";
---
<main>
  <Card client:load />
  <slot name="side" />
</main>
"""


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _slot(sym: Symbol) -> str:
    return sym.id.rsplit(":", 2)[1]


def _by_kind(symbols: list[Symbol], kind: str) -> list[Symbol]:
    return [s for s in symbols if s.kind == kind]


def _assert_round_trip(symbols: list[Symbol]) -> None:
    """Every member's id is ``make_symbol_id`` of its own name (Ruling 1)."""
    for sym in symbols:
        assert _slot(sym) == sanitize_id_name_segment(sym.name), (sym.id, sym.name)
        assert sym.id == make_symbol_id(
            sym.language or "", sym.path, sym.span.start_line, sym.span.end_line,
            sym.name, sym.kind,
        )


class TestNameSlotRoundTrip:
    def test_vue_directive_name_names_its_element(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/App.vue", _VUE)
        symbols = analyze_vue(tmp_path).symbols
        directives = _by_kind(symbols, "directive")
        assert {d.name for d in directives} == {
            "button:v-on:click", "button:v-bind:title", "p:v-if",
        }
        _assert_round_trip(symbols)
        # The directive alone is still its signature, the element in meta.
        click = next(d for d in directives if d.name == "button:v-on:click")
        assert click.signature == "v-on:click"
        assert click.meta["element"] == "button"

    def test_vue_slot_prop_and_style_names_are_unchanged(self, tmp_path: Path) -> None:
        # Control: these already round-tripped.
        _write(tmp_path, "src/App.vue", _VUE)
        symbols = analyze_vue(tmp_path).symbols
        assert [s.name for s in _by_kind(symbols, "slot")] == ["footer"]
        assert [s.name for s in _by_kind(symbols, "prop")] == ["title"]
        assert [s.name for s in _by_kind(symbols, "style_block")] == ["style"]

    def test_svelte_event_and_block_names(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/App.svelte", _SVELTE)
        symbols = analyze_svelte(tmp_path).symbols
        assert [e.name for e in _by_kind(symbols, "event")] == ["button:click"]
        block = _by_kind(symbols, "block")
        assert [b.name for b in block] == ["if:count > 0"]
        assert block[0].signature == "{#if count > 0}"
        assert block[0].meta["block_type"] == "if"
        # Control: a slot's name was already its slot.
        assert [s.name for s in _by_kind(symbols, "slot")] == ["x"]
        _assert_round_trip(symbols)

    def test_astro_client_directive_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/Page.astro", _ASTRO)
        symbols = analyze_astro(tmp_path).symbols
        assert [d.name for d in _by_kind(symbols, "directive")] == ["Card:client:load"]
        assert _by_kind(symbols, "directive")[0].signature == "client:load"
        # Control: variables and slots were already round-tripping.
        assert {s.name for s in _by_kind(symbols, "variable")} == {"title"}
        assert [s.name for s in _by_kind(symbols, "slot")] == ["side"]
        _assert_round_trip([s for s in symbols if s.kind != "file"])


_VUE_NESTED = """<template>
  <div>
    <template v-if="a">
      <template v-for="x in xs">
        <p :title="x">t</p>
        <Child v-if="x" />
      </template>
    </template>
    <p :title="y">u</p>
  </div>
</template>
"""


class TestOneNodePerMember:
    """Distinct members get distinct node ids; one member is emitted once."""

    def test_vue_nested_template_content_is_emitted_once(self, tmp_path: Path) -> None:
        # A <template> nested in the root <template> was walked again from
        # every enclosing <template>: the <p> two levels down came out three
        # times, with one node id.
        _write(tmp_path, "src/N.vue", _VUE_NESTED)
        result = analyze_vue(tmp_path)
        directives = _by_kind(result.symbols, "directive")
        ids = [d.id for d in directives]
        assert len(ids) == len(set(ids))
        # Reach: the nested <p>, the nested component and the control <p>
        # outside every nested template are each there once.
        assert sorted((d.name, d.span.start_line) for d in directives) == [
            ("Child:v-if", 6), ("p:v-bind:title", 5), ("p:v-bind:title", 9),
        ]
        component_edges = [
            e for e in result.edges if (e.meta or {}).get("component_name") == "Child"
        ]
        assert len(component_edges) == 1

    def test_svelte_repeated_event_on_one_element_is_one_symbol(self, tmp_path: Path) -> None:
        # Two handlers of one event on one element (valid Svelte) are one
        # subscription of the element to the event; the analyzer records no
        # handler, so two nodes carried the same facts under one id.
        _write(tmp_path, "src/B.svelte", (
            "<button\n"
            "  on:click={a}\n"
            "  on:click={b}\n"
            "  on:click|once={c}\n"
            ">x</button>\n"
            "<button on:click={d}>y</button>\n"
        ))
        events = _by_kind(analyze_svelte(tmp_path).symbols, "event")
        ids = [e.id for e in events]
        assert len(ids) == len(set(ids))
        # Control: a modifier makes another event; another element another
        # symbol.
        assert sorted((e.name, e.span.start_line) for e in events) == [
            ("button:click", 1), ("button:click", 6), ("button:click|once", 1),
        ]
