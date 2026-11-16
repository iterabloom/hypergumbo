# SPDX-License-Identifier: AGPL-3.0-or-later
"""One file pseudo-symbol per path (INV-movor).

Properties of the two passes in ``analyze/file_records.py``: producers' file
nodes for one path become the one in the file's own language with every edge
rewired, and a reference to a file lands on the node its path already has.
The producer shapes are those a Svelte / Vue / Blade file gets: the script
analyzer's ``typescript:`` node beside the component's own ``vue:`` node.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_core.analyze.file_records import (
    file_reference_dst,
    is_file_pseudo_symbol,
    repoint_file_references,
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
        manifest = Symbol(
            id="toml:pyproject.toml:5-5:tool:file", name="tool", kind="file",
            language="toml", path="pyproject.toml",
            span=Span(start_line=5, end_line=5, start_col=0, end_col=0),
            origin="toml", origin_run_id="run", meta={"entry_role": "script"},
        )
        anchor = _file("toml", "pyproject.toml", origin="toml")
        nopath = _file("typescript", "")
        symbols = [a, only_script, manifest, anchor, nopath]
        before = list(symbols)
        edge = _edge(a.id, only_script.id)
        key = edge.edge_key
        assert unify_file_pseudo_symbols(symbols, [edge]) == {}
        assert symbols == before and edge.edge_key == key
        assert only_script.language == "typescript"
        assert not is_file_pseudo_symbol(manifest)


class TestTwoRecordsOneId:
    """An absolute-path id and a repo-relative one that relativization made
    equal are two records of one file all the same."""

    def test_the_second_record_is_folded_into_the_first(self) -> None:
        analyzer = _file("typescript", "pkg/src/index.ts", end=30, meta={"module_system": "esm"})
        synthesized = _file("typescript", "pkg/src/index.ts",
                            origin="orchestrator_file_symbol_synthesis", end=31)
        analyzer.stable_id = synthesized.stable_id = "sha256:0123456789abcdef"
        other = _file("typescript", "pkg/src/other.ts")
        symbols = [analyzer, other, synthesized]
        edge = _edge("json:pkg/package.json:5-5:main:file", analyzer.id, "defines_target")
        key = edge.edge_key
        assert unify_file_pseudo_symbols(symbols, [edge]) == {}  # nothing to rewire
        assert symbols == [analyzer, other]
        assert symbols[0] is analyzer
        assert analyzer.origin == ["javascript", "orchestrator_file_symbol_synthesis"]
        assert analyzer.span.end_line == 31 and analyzer.stable_id == "sha256:0123456789abcdef"
        assert edge.dst == analyzer.id and edge.edge_key == key

    def test_a_same_id_pair_beside_another_language_slot(self) -> None:
        script = _file("typescript", "src/App.vue")
        again = _file("typescript", "src/App.vue", origin="orchestrator_file_symbol_synthesis")
        own = _file("vue", "src/App.vue", origin="orchestrator_file_symbol_synthesis")
        symbols = [script, again, own]
        edge = _edge(script.id, "x")
        assert unify_file_pseudo_symbols(symbols, [edge]) == {script.id: own.id}
        assert symbols == [own] and edge.src == own.id


class TestRepointReferences:
    def test_a_reference_lands_on_the_node_the_path_has(self, tmp_path: Path) -> None:
        # The script analyzer's node, absolute as analyzers emit it.
        kid = _file("javascript", str(tmp_path / "src" / "Kid.vue"))
        app = _file("vue", "src/App.vue")
        ref = _edge(app.id, make_file_id("vue", "src/Kid.vue"))
        twice = _edge(app.id, make_file_id("vue", "src/Kid.vue"), "references")
        remap = repoint_file_references([kid, app], [ref, twice], repo_root=tmp_path)
        assert remap == {"vue:src/Kid.vue:1-1:file:file": kid.id}
        assert ref.dst == kid.id and twice.dst == kid.id
        assert ref.edge_key is None

    def test_what_is_left_for_the_synthesis_pass(self, tmp_path: Path) -> None:
        script = _file("typescript", "src/App.vue")
        util = _file("typescript", "src/util.ts")
        edges = [
            # A producer anchoring on the file is no reference: kept, so the
            # synthesis pass mints the component's own node.
            _edge("vue:src/App.vue:1-1:file:file", "vue:./Kid.vue:0-0:Kid:component"),
            _edge(util.id, "vue:src/App.vue:1-1:file:file"),
            # A path with no file node yet: the synthesis pass mints it.
            _edge(util.id, "vue:src/New.vue:1-1:file:file"),
            # An existing id, and a dst that is no file id.
            _edge(util.id, script.id),
            _edge(util.id, "typescript:./x:0-0:module:module"),
        ]
        before = [(e.src, e.dst) for e in edges]
        assert repoint_file_references([script, util], edges, repo_root=tmp_path) == {}
        assert [(e.src, e.dst) for e in edges] == before

    def test_without_file_symbols_or_root(self) -> None:
        assert repoint_file_references(
            [_member("python", "a.py", "f")],
            [_edge("python:a.py:1-1:file:file", "python:b.py:1-1:file:file")],
        ) == {}
        kid = _file("javascript", "src/Kid.vue")
        ref = _edge("x", "vue:src/Kid.vue:1-1:file:file")
        assert repoint_file_references([kid], [ref]) == {
            "vue:src/Kid.vue:1-1:file:file": kid.id,
        }
        assert ref.dst == kid.id


def test_rewire_endpoints_returns_the_changed_edges() -> None:
    edges = [_edge("a", "b"), _edge("c", "d")]
    assert rewire_endpoints(edges, {"a": "z"}) == [edges[0]]
    assert (edges[0].src, edges[1].src) == ("z", "c")


class TestFoldKeepsProvenance:
    """Two views of one import become one edge that says both (ADR-0057 14.1)."""

    def _graph(self) -> tuple[list[Symbol], Symbol, Symbol, Symbol]:
        script = _file("typescript", "src/App.vue")
        own = _file("vue", "src/App.vue", origin="orchestrator_file_symbol_synthesis")
        kid = _file("javascript", "src/Kid.vue")
        return [script, own, kid], script, own, kid

    def test_the_surviving_edge_takes_the_linkers_origin_and_consumed_stub(self) -> None:
        symbols, script, own, kid = self._graph()
        template = Edge.create(
            src=own.id, dst=kid.id, edge_type="imports", line=1, origin="vue",
            origin_run_id="run", evidence_type="import", meta={"component_name": "Kid"},
        )
        resolved = Edge.create(
            src=script.id, dst=kid.id, edge_type="imports", line=3,
            origin="js-module-linker", origin_run_id="run",
            evidence_type="import_resolution", derived_from=[script.id, "edge:stub"],
        )
        other = _edge(script.id, "typescript:src/util.ts:1-1:file:file")
        other.derived_from = [script.id, "edge:other"]
        unify_file_pseudo_symbols(symbols, [template, resolved, other])
        assert (resolved.src, resolved.dst) == (template.src, template.dst)
        assert template.origin == ["vue", "js-module-linker"]
        assert template.derived_from == [own.id, "edge:stub"]
        assert template.meta["component_name"] == "Kid"  # the survivor's own meta
        # An edge no fold touches is left as it was.
        assert other.origin == ["test"] and other.derived_from == [own.id, "edge:other"]

    def test_a_stated_consumption_is_never_overwritten(self) -> None:
        symbols, script, own, kid = self._graph()
        first = Edge.create(
            src=script.id, dst=kid.id, edge_type="imports", line=3,
            origin="js-module-linker", origin_run_id="run",
            evidence_type="import_resolution", derived_from=[script.id, "edge:js-stub"],
        )
        second = Edge.create(
            src=own.id, dst=kid.id, edge_type="imports", line=1,
            origin="vue-component-linker", origin_run_id="run",
            evidence_type="ast_import", derived_from=[own.id, "edge:vue-stub"],
        )
        unify_file_pseudo_symbols(symbols, [first, second])
        assert first.origin == ["js-module-linker", "vue-component-linker"]
        assert first.derived_from == [own.id, "edge:js-stub"]


class TestFileReferenceDst:
    @staticmethod
    def _dst(root: Path, lang: str, importer: str, import_path: str) -> str | None:
        return file_reference_dst(lang, root / importer, importer, import_path)

    def test_a_relative_import_of_a_file_in_the_tree(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "parts").mkdir(parents=True)
        (tmp_path / "src" / "parts" / "Kid.vue").write_text("<template/>")
        (tmp_path / "src" / "Child.svelte").write_text("<p/>")
        (tmp_path / "src" / "Hero.astro").write_text("<h1/>")
        (tmp_path / "top.vue").write_text("<template/>")
        assert self._dst(tmp_path, "vue", "src/App.vue", "./parts/Kid.vue") == (
            "vue:src/parts/Kid.vue:1-1:file:file"
        )
        # The target's own language, not the importer's.
        assert self._dst(tmp_path, "vue", "src/App.vue", "./Child.svelte") == (
            "svelte:src/Child.svelte:1-1:file:file"
        )
        # A format the taxonomy does not list takes the importer's.
        assert self._dst(tmp_path, "astro", "src/Page.astro", "./Hero.astro") == (
            "astro:src/Hero.astro:1-1:file:file"
        )
        assert self._dst(tmp_path, "vue", "src/parts/Kid.vue", "../../top.vue") == (
            "vue:top.vue:1-1:file:file"
        )
        assert self._dst(tmp_path, "vue", "top.vue", "./src/parts/Kid.vue") == (
            "vue:src/parts/Kid.vue:1-1:file:file"
        )

    def test_what_does_not_resolve(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / "src").mkdir(parents=True)
        (root / "src" / "Kid.vue").write_text("<template/>")
        (tmp_path / "Outside.vue").write_text("<template/>")
        for import_path in (
            "./Missing.vue",          # no such file
            "@/Kid.vue",              # an alias: configuration, not a path
            "Kid.vue",                # a bare specifier is not relative
            "../../Outside.vue",      # above the repo root, though it exists
            "../..",                  # the root's parent
            "./",                     # a directory
        ):
            assert self._dst(root, "vue", "src/App.vue", import_path) is None
