# SPDX-License-Identifier: AGPL-3.0-or-later
"""One file pseudo-symbol per path (INV-movor).

Properties of the pass in ``analyze/file_records.py``: producers' file nodes
for one path become the one in the file's own language with every edge rewired.
The producer shapes are those a Svelte / Vue / Blade file gets: the script
analyzer's ``typescript:`` node beside the component's own ``vue:`` node.
"""
from __future__ import annotations

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_core.analyze.file_records import (
    is_file_pseudo_symbol,
    rewire_endpoints,
    unify_file_pseudo_symbols,
)
from hypergumbo_core.ir import Edge, Span, Symbol, UsageContext


def _file(lang: str, path: str, origin: str = "javascript", end: int = 1,
          meta: dict | None = None, language: str | None = "") -> Symbol:
    return Symbol(
        id=make_file_id(lang, path), name=path, kind="file",
        language=lang if language == "" else language, path=path,
        span=Span(start_line=1, end_line=end, start_col=0, end_col=0),
        origin=origin, origin_run_id="run", meta=meta,
    )


def _member(lang: str, path: str, name: str) -> Symbol:
    return Symbol(
        id=f"{lang}:{path}:3-4:{name}:function", name=name, kind="function",
        language=lang, path=path,
        span=Span(start_line=3, end_line=4, start_col=0, end_col=0),
        origin="javascript", origin_run_id="run",
    )


def _edge(src: str, dst: str, edge_type: str = "imports") -> Edge:
    return Edge.create(
        src=src, dst=dst, edge_type=edge_type, line=1,
        origin="test", origin_run_id="run", evidence_type="import",
    )


def _file_nodes_per_path(symbols: list[Symbol]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for symbol in symbols:
        if is_file_pseudo_symbol(symbol):
            out.setdefault(symbol.path, []).append(symbol.id)
    return out


class TestUnify:
    def test_the_files_own_language_survives_and_edges_follow(self) -> None:
        script = _file("typescript", "src/App.vue", end=7, meta={"module_system": "esm"})
        own = _file("vue", "src/App.vue", origin="orchestrator_file_symbol_synthesis", end=9)
        go = _member("typescript", "src/App.vue", "go")
        util = _file("typescript", "src/util.ts")
        symbols = [script, go, own, util]
        edges = [
            _edge(script.id, util.id),                 # the script's import
            _edge(script.id, go.id, "contains"),       # containment rooted at it
            _edge(own.id, "vue:./Kid.vue:0-0:Kid:component"),
            _edge(util.id, script.id),                 # someone importing the component
        ]
        derived = _edge(util.id, go.id, "calls")
        derived.derived_from = [script.id, "edge:x"]
        edges.append(derived)
        context = UsageContext.create(
            kind="call", context_name="x", position="args[0]", path="src/App.vue",
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            symbol_ref=script.id,
        )

        remap = unify_file_pseudo_symbols(symbols, edges, [context])

        assert remap == {script.id: own.id}
        assert _file_nodes_per_path(symbols) == {
            "src/App.vue": [own.id], "src/util.ts": [util.id],
        }
        assert go in symbols  # only the duplicate file node left
        # No edge is dropped, and none still names the dropped node.
        assert len(edges) == 5
        assert all(script.id not in (e.src, e.dst) for e in edges)
        assert [(e.src, e.dst) for e in edges[:4]] == [
            (own.id, util.id), (own.id, go.id),
            (own.id, "vue:./Kid.vue:0-0:Kid:component"), (util.id, own.id),
        ]
        assert derived.derived_from == [own.id, "edge:x"]
        assert context.symbol_ref == own.id
        # Rewired edges recompute their key; untouched ones keep theirs.
        assert edges[0].edge_key is None and edges[2].edge_key is not None

    def test_the_survivor_carries_both_producers(self) -> None:
        script = _file("typescript", "a/B.svelte", end=40, meta={"k": "script", "s": 1})
        own = _file("svelte", "a/B.svelte", origin="orchestrator_file_symbol_synthesis",
                    end=12, meta={"k": "own"})
        symbols = [script, own]
        unify_file_pseudo_symbols(symbols, [])
        assert symbols == [own]
        assert own.origin == ["orchestrator_file_symbol_synthesis", "javascript"]
        assert own.meta == {"k": "own", "s": 1}  # its own value wins a shared key
        assert (own.span.start_line, own.span.end_line, own.line_span) == (1, 40, 40)
        assert own.language == "svelte" and own.id == "svelte:a/B.svelte:1-1:file:file"

    def test_first_emitted_survives_when_none_is_in_the_files_language(self) -> None:
        # A Svelte file given a `vue:` node by a linker: neither node is
        # `svelte`, so the analyzer's (emitted first) stays.
        script = _file("javascript", "src/Child.svelte")
        wrong = _file("vue", "src/Child.svelte", origin="vue-component-linker")
        # An extension the taxonomy does not list has no own language at all.
        first = _file("astro", "p/X.astro")
        second = _file("javascript", "p/X.astro")
        symbols = [script, wrong, first, second]
        edge = _edge(wrong.id, second.id)
        remap = unify_file_pseudo_symbols(symbols, [edge])
        assert remap == {wrong.id: script.id, second.id: first.id}
        assert symbols == [script, first]
        assert (edge.src, edge.dst) == (script.id, first.id)

    def test_spanless_and_metaless_members(self) -> None:
        own = _file("vue", "a.vue")
        own.span = None
        other = _file("typescript", "a.vue", end=5)
        other.origin = ["javascript", "x"]
        bare = _file("javascript", "a.vue")
        bare.span = None
        bare.origin = ""
        symbols = [other, bare, own]
        unify_file_pseudo_symbols(symbols, [])
        assert symbols == [own]
        assert own.span is not None and own.span.end_line == 5
        assert own.origin == ["javascript", "x"] and own.meta is None

    def test_one_node_per_path_is_left_untouched(self) -> None:
        a = _file("typescript", "src/a.ts")
        only_script = _file("typescript", "src/Solo.vue")  # its slot is NOT changed
        again = _file("typescript", "src/a.ts")            # one id emitted twice
        manifest = Symbol(
            id="toml:pyproject.toml:5-5:tool:file", name="tool", kind="file",
            language="toml", path="pyproject.toml",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=0),
            origin="toml", origin_run_id="run", meta={"entry_role": "script"},
        )
        anchor = _file("toml", "pyproject.toml", origin="toml")
        nopath = _file("typescript", "")
        symbols = [a, only_script, again, manifest, anchor, nopath]
        before = list(symbols)
        edge = _edge(a.id, only_script.id)
        key = edge.edge_key
        assert unify_file_pseudo_symbols(symbols, [edge]) == {}
        assert symbols == before and edge.edge_key == key
        assert only_script.language == "typescript"
        assert not is_file_pseudo_symbol(manifest)


def test_rewire_endpoints_counts_changed_edges() -> None:
    edges = [_edge("a", "b"), _edge("c", "d")]
    assert rewire_endpoints(edges, {"a": "z"}) == 1
    assert (edges[0].src, edges[1].src) == ("z", "c")
