# SPDX-License-Identifier: AGPL-3.0-or-later
"""A point read of a Yjs shared-type entry links from its writer (INV-munok).

The yjs-crdt linker linked a writer to an OBSERVER (``.observe``), never to a
function that READS an entry: ``chunk.getMap('objects').get(id)``. PlazaFlow's
flow -- create a SpatialObject in the chunk's ``objects`` map, read it back
elsewhere and open the Y.Doc its ``yjsDocId`` names -- had no edge from the
creator to the reader, so neither slice direction crossed it.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.yjs_crdt import _scan_file_for_yjs_patterns, link_yjs_crdt


def _ts(path: str) -> Symbol:
    return Symbol(
        id=f"typescript:{path}:1-10:test:function", name="test",
        kind="function", language="typescript", path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        origin="ts", origin_run_id="uuid:test",
    )


_YJS_DEP = Symbol(
    id="json:package.json:1-1:yjs:dependency", name="yjs", kind="dependency",
    language="json", path="package.json",
    span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
    origin="json", origin_run_id="uuid:test", meta={"package": "yjs"},
)


def _link(tmp_path: Path, files: dict[str, str]) -> list[tuple[str, int, str, int]]:
    """(writer file, line, reader file, line) of each entry edge."""
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    result = link_yjs_crdt(tmp_path, [_ts(rel) for rel in files] + [_YJS_DEP])
    by_id = {s.id: s for s in result.symbols}
    out = []
    for e in result.edges:
        src, dst = by_id[e.src], by_id[e.dst]
        if src.meta["yjs_api"] == "yjs_entry":
            out.append((src.path, src.span.start_line, dst.path, dst.span.start_line))
    return sorted(out)


class TestAPointReadLinksFromTheWriter:
    def test_the_plazaflow_spatial_object_flow(self, tmp_path: Path) -> None:
        edges = _link(tmp_path, {
            "src/world.ts": (
                "export function startCollab(chunk, id) {\n"
                "  chunk.getMap('objects').set(id, { id, yjsDocId: `sess-${id}` });\n"
                "}\n"
            ),
            "src/session.ts": (
                "export function joinCollab(chunk, id) {\n"
                "  const objects = chunk.getMap<SpatialObject>('objects');\n"
                "  const obj = objects.get(id);\n"
                "  return new Y.Doc({ guid: obj.yjsDocId });\n"
                "}\n"
            ),
        })
        assert edges == [("src/world.ts", 2, "src/session.ts", 3)]

    def test_a_chained_read_of_a_literal_key(self, tmp_path: Path) -> None:
        edges = _link(tmp_path, {
            "a.ts": "doc.getMap('meta').set('pages', pages);\n",
            "b.ts": "const pages = rootDoc.getMap('meta').get('pages');\n",
        })
        assert edges == [("a.ts", 1, "b.ts", 1)]

    def test_an_array_bound_to_this_is_read_by_to_array(self, tmp_path: Path) -> None:
        edges = _link(tmp_path, {
            "a.ts": "this.log = doc.getArray('log');\nthis.log.push([entry]);\n",
            "b.ts": "const all = doc.getArray('log').toArray();\n",
        })
        assert edges == [("a.ts", 2, "b.ts", 1)]

    def test_a_computed_key_matches_any_key_of_its_map(self, tmp_path: Path) -> None:
        edges = _link(tmp_path, {
            "a.ts": "doc.getMap('objects').set('lobby', o);\n",
            "b.ts": "const m = doc.getMap('objects') as Y.Map<any>;\nm.has(id);\n",
        })
        assert edges == [("a.ts", 1, "b.ts", 2)]


class TestAPointReadLinksOnlyFromItsOwnEntry:
    def test_another_key_of_the_same_map_is_not_linked(self, tmp_path: Path) -> None:
        assert _link(tmp_path, {
            "a.ts": "doc.getMap('meta').set('name', n);\n",
            "b.ts": "doc.getMap('meta').get('pages');\n",
        }) == []

    def test_another_map_is_not_linked(self, tmp_path: Path) -> None:
        assert _link(tmp_path, {
            "a.ts": "doc.getMap('settings').set(k, v);\n",
            "b.ts": "doc.getMap('objects').get(k);\n",
        }) == []

    def test_a_get_on_something_that_is_not_a_shared_type_is_not_a_read(
        self, tmp_path: Path,
    ) -> None:
        assert _link(tmp_path, {
            "a.ts": "doc.getMap('objects').set(k, v);\n",
            "b.ts": "const objects = new Map();\nobjects.get(k);\ncache.get(k);\n",
        }) == []

    def test_a_name_bound_to_two_shared_types_is_not_trusted(self, tmp_path: Path) -> None:
        assert _link(tmp_path, {
            "a.ts": "doc.getMap('objects').set(k, v);\n",
            "b.ts": (
                "let m = doc.getMap('objects');\n"
                "m = doc.getMap('settings');\n"
                "m.get(k);\n"
            ),
        }) == []

    def test_a_name_also_bound_to_something_else_is_not_trusted(
        self, tmp_path: Path,
    ) -> None:
        assert _link(tmp_path, {
            "a.ts": "doc.getMap('objects').set(k, v);\n",
            "b.ts": "let m = doc.getMap('objects');\nm = new Map();\nm.get(k);\n",
        }) == []

    def test_entries_in_test_files_are_not_linked(self, tmp_path: Path) -> None:
        """Each test builds its own Y.Doc; a shared map name is not a shared map."""
        assert _link(tmp_path, {
            "tests/a.test.ts": "doc.getMap('foo').set('k', 1);\n",
            "tests/b.test.ts": "doc.getMap('foo').get('k');\n",
            "src/c.ts": "doc.getMap('foo').get('k');\n",
        }) == []

    def test_a_read_in_the_writer_s_own_file_is_not_linked(self, tmp_path: Path) -> None:
        assert _link(tmp_path, {
            "a.ts": "doc.getMap('objects').set(k, v);\ndoc.getMap('objects').get(k);\n",
        }) == []


class TestTheStandInsNameTheEntry:
    def test_labels_and_channels(self, tmp_path: Path) -> None:
        for rel, body in {
            "a.ts": "doc.getMap('meta').set('pages', p);\n",
            "b.ts": "doc.getMap('meta').get('pages');\n",
        }.items():
            (tmp_path / rel).write_text(body)
        result = link_yjs_crdt(tmp_path, [_ts("a.ts"), _ts("b.ts"), _YJS_DEP])
        entry = [s for s in result.symbols if s.meta["yjs_api"] == "yjs_entry"]
        assert sorted(s.display_label for s in entry) == [
            "yjs.get(meta.pages)", "yjs.set(meta.pages)",
        ]
        edge = next(
            e for e in result.edges
            if e.src in {s.id for s in entry}
        )
        assert edge.edge_type == "event_publishes"
        assert edge.meta["channel"] == "meta"
        assert edge.meta["channel_kind"] == "crdt"


class TestAGenericAccessorIsAnAccessor:
    def test_get_map_with_a_type_argument_is_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "a.ts"
        f.write_text("const blocks = ydoc.getMap<any>('blocks');\n")
        sites = _scan_file_for_yjs_patterns(f, "a.ts")
        assert [(s.kind, s.channel, s.api) for s in sites] == [("write", "blocks", "yjs")]


def test_the_survey_links_the_creator_to_the_reader(tmp_path: Path) -> None:
    """Production path: through ``run_behavior_map``, the function that creates
    the SpatialObject reaches the one that reads it (via the stand-ins)."""
    from hypergumbo_core.cli import run_behavior_map

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        '{"name": "pf", "version": "0.0.0", "dependencies": {"yjs": "^13.6.0"}}\n'
    )
    (root / "src/world.ts").write_text(
        "import * as Y from 'yjs';\n"
        "export function startCollab(chunk: Y.Doc, id: string): void {\n"
        "  chunk.getMap('objects').set(id, { id, yjsDocId: `sess-${id}` });\n"
        "}\n"
    )
    (root / "src/session.ts").write_text(
        "import * as Y from 'yjs';\n"
        "export function joinCollab(chunk: Y.Doc, id: string): Y.Doc {\n"
        "  const obj = chunk.getMap('objects').get(id) as { yjsDocId: string };\n"
        "  return new Y.Doc({ guid: obj.yjsDocId });\n"
        "}\n"
    )
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    names = {n["id"]: n["name"] for n in data["nodes"]}
    uses = {(e["dst"]): names.get(e["src"]) for e in data["edges"] if e["type"] == "uses"}
    pairs = {
        (uses.get(e["src"]), uses.get(e["dst"]))
        for e in data["edges"] if e["type"] == "event_publishes"
    }
    assert ("startCollab", "joinCollab") in pairs, pairs
