# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Yjs/CRDT reactive linker.

Tests detection of Yjs shared type mutations, Awareness state changes,
and observation patterns across TypeScript/JavaScript files.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Span, Symbol
from hypergumbo_core.linkers.yjs_crdt import (
    YjsSite,
    _scan_file_for_yjs_patterns,
    link_yjs_crdt,
)


def _make_ts_sym(path: str) -> Symbol:
    """Create a minimal TS symbol for testing."""
    return Symbol(
        id=f"typescript:{path}:1-10:test:function",
        name="test", kind="function", language="typescript",
        path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        origin="ts-v1", origin_run_id="uuid:test",
    )


class TestScanFileForYjsPatterns:
    """Tests for Yjs pattern scanning."""

    def test_detects_ymap_set(self, tmp_path: Path) -> None:
        """yMap.set('key', value) should be detected as a write."""
        f = tmp_path / "writer.ts"
        f.write_text("yMap.set('cursor', pos);\n")
        sites = _scan_file_for_yjs_patterns(f, "writer.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "cursor"
        assert writes[0].api == "yjs"

    def test_detects_ymap_delete(self, tmp_path: Path) -> None:
        """yMap.delete('key') should be detected as a write."""
        f = tmp_path / "deleter.ts"
        f.write_text("yMap.delete('cursor');\n")
        sites = _scan_file_for_yjs_patterns(f, "deleter.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "cursor"

    def test_detects_observe(self, tmp_path: Path) -> None:
        """yMap.observe(callback) should be detected as a read."""
        f = tmp_path / "reader.ts"
        f.write_text("yMap.observe((event) => { handle(event); });\n")
        sites = _scan_file_for_yjs_patterns(f, "reader.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].api == "yjs"

    def test_detects_observe_deep(self, tmp_path: Path) -> None:
        """yMap.observeDeep(callback) should be detected as a read."""
        f = tmp_path / "deep.ts"
        f.write_text("yMap.observeDeep(callback);\n")
        sites = _scan_file_for_yjs_patterns(f, "deep.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1

    def test_detects_ydoc_on_update(self, tmp_path: Path) -> None:
        """yDoc.on('update', handler) should be detected as a read."""
        f = tmp_path / "sync.ts"
        f.write_text("yDoc.on('update', handler);\n")
        sites = _scan_file_for_yjs_patterns(f, "sync.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1

    def test_detects_awareness_write(self, tmp_path: Path) -> None:
        """awareness.setLocalStateField should be detected as a write."""
        f = tmp_path / "awareness_w.ts"
        f.write_text("awareness.setLocalStateField('cursor', pos);\n")
        sites = _scan_file_for_yjs_patterns(f, "awareness_w.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "awareness.cursor"
        assert writes[0].api == "awareness"

    def test_detects_awareness_set_local_state(self, tmp_path: Path) -> None:
        """awareness.setLocalState(state) should be detected as a write."""
        f = tmp_path / "awareness_full.ts"
        f.write_text("awareness.setLocalState({ cursor: pos });\n")
        sites = _scan_file_for_yjs_patterns(f, "awareness_full.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].api == "awareness"

    def test_detects_awareness_read(self, tmp_path: Path) -> None:
        """awareness.on('change', callback) should be detected as a read."""
        f = tmp_path / "awareness_r.ts"
        f.write_text("awareness.on('change', (changes) => { update(); });\n")
        sites = _scan_file_for_yjs_patterns(f, "awareness_r.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].api == "awareness"

    def test_skips_non_yjs_files(self, tmp_path: Path) -> None:
        """Files without Yjs patterns should return empty."""
        f = tmp_path / "plain.ts"
        f.write_text("const x = 1;\nconsole.log(x);\n")
        sites = _scan_file_for_yjs_patterns(f, "plain.ts")
        assert sites == []

    def test_multiple_patterns_in_one_file(self, tmp_path: Path) -> None:
        """Multiple patterns in one file should all be detected."""
        f = tmp_path / "multi.ts"
        f.write_text(
            "yMap.set('a', 1);\n"
            "yMap.set('b', 2);\n"
            "yMap.observe(cb);\n"
        )
        sites = _scan_file_for_yjs_patterns(f, "multi.ts")
        writes = [s for s in sites if s.kind == "write"]
        reads = [s for s in sites if s.kind == "read"]
        assert len(writes) == 2
        assert len(reads) >= 1


class TestLinkYjsCrdt:
    """Tests for the Yjs CRDT linking logic."""

    def test_links_writer_to_reader(self, tmp_path: Path) -> None:
        """Yjs write in one file + observe in another creates an edge."""
        w = tmp_path / "src" / "writer.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("yMap.set('cursor', pos);\n")

        r = tmp_path / "src" / "reader.ts"
        r.write_text("yMap.observe(handler);\n")

        syms = [_make_ts_sym("src/writer.ts"), _make_ts_sym("src/reader.ts")]
        result = link_yjs_crdt(tmp_path, syms)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.edge_type == "crdt_publishes"
        assert edge.meta is not None
        assert edge.meta["access_mode"] == "write"
        assert edge.meta["dest_access_mode"] == "read"

    def test_awareness_writer_links_to_awareness_reader(self, tmp_path: Path) -> None:
        """Awareness write + awareness read creates an edge."""
        w = tmp_path / "src" / "cursor.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("awareness.setLocalStateField('cursor', pos);\n")

        r = tmp_path / "src" / "overlay.ts"
        r.write_text("awareness.on('change', handler);\n")

        syms = [_make_ts_sym("src/cursor.ts"), _make_ts_sym("src/overlay.ts")]
        result = link_yjs_crdt(tmp_path, syms)

        assert len(result.edges) >= 1

    def test_no_cross_api_matching(self, tmp_path: Path) -> None:
        """Yjs writes should not match awareness reads."""
        w = tmp_path / "src" / "yjs_writer.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("yMap.set('key', value);\n")

        r = tmp_path / "src" / "awareness_reader.ts"
        r.write_text("awareness.on('change', handler);\n")

        syms = [_make_ts_sym("src/yjs_writer.ts"), _make_ts_sym("src/awareness_reader.ts")]
        result = link_yjs_crdt(tmp_path, syms)

        assert len(result.edges) == 0

    def test_same_file_not_linked(self, tmp_path: Path) -> None:
        """Writes and reads in the same file should not create edges."""
        f = tmp_path / "src" / "self_contained.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("yMap.set('x', 1);\nyMap.observe(cb);\n")

        syms = [_make_ts_sym("src/self_contained.ts")]
        result = link_yjs_crdt(tmp_path, syms)

        assert len(result.edges) == 0

    def test_no_reads_returns_empty(self, tmp_path: Path) -> None:
        """Only writes with no reads should produce no edges."""
        w = tmp_path / "src" / "writer.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("yMap.set('key', val);\n")

        syms = [_make_ts_sym("src/writer.ts")]
        result = link_yjs_crdt(tmp_path, syms)
        assert len(result.edges) == 0

    def test_no_writes_returns_empty(self, tmp_path: Path) -> None:
        """Only reads with no writes should produce no edges."""
        r = tmp_path / "src" / "reader.ts"
        r.parent.mkdir(parents=True, exist_ok=True)
        r.write_text("yMap.observe(handler);\n")

        syms = [_make_ts_sym("src/reader.ts")]
        result = link_yjs_crdt(tmp_path, syms)
        assert len(result.edges) == 0

    def test_creates_synthetic_symbols(self, tmp_path: Path) -> None:
        """Should create synthetic publisher and subscriber symbols."""
        w = tmp_path / "src" / "pub.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("yMap.set('key', val);\n")

        r = tmp_path / "src" / "sub.ts"
        r.write_text("yMap.observe(handler);\n")

        syms = [_make_ts_sym("src/pub.ts"), _make_ts_sym("src/sub.ts")]
        result = link_yjs_crdt(tmp_path, syms)

        assert len(result.symbols) >= 2
        pubs = [s for s in result.symbols if s.kind == "event_publisher"]
        subs = [s for s in result.symbols if s.kind == "event_subscriber"]
        assert len(pubs) >= 1
        assert len(subs) >= 1

    def test_empty_symbols_returns_empty(self, tmp_path: Path) -> None:
        """No symbols should produce empty result."""
        result = link_yjs_crdt(tmp_path, [])
        assert len(result.edges) == 0
        assert result.run is not None

    def test_non_js_ts_symbols_skipped(self, tmp_path: Path) -> None:
        """Python symbols should be ignored."""
        f = tmp_path / "src" / "py_file.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("yMap.set('key', val);\n")

        py_sym = Symbol(
            id="python:src/py_file.py:1-10:test:function",
            name="test", kind="function", language="python",
            path="src/py_file.py",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="py-v1", origin_run_id="uuid:test",
        )
        result = link_yjs_crdt(tmp_path, [py_sym])
        assert len(result.edges) == 0

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Symbols pointing to nonexistent files should be skipped."""
        syms = [_make_ts_sym("src/gone.ts")]
        result = link_yjs_crdt(tmp_path, syms)
        assert len(result.edges) == 0


class TestScanSubDocPatterns:
    """Tests for Yjs sub-document and shared type accessor patterns."""

    def test_detects_doc_get_map(self, tmp_path: Path) -> None:
        """doc.getMap('name') should be detected as a write (named shared type access)."""
        f = tmp_path / "state.ts"
        f.write_text("const yMap = doc.getMap('blocks');\n")
        sites = _scan_file_for_yjs_patterns(f, "state.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "blocks"
        assert writes[0].api == "yjs"

    def test_detects_doc_get_array(self, tmp_path: Path) -> None:
        """doc.getArray('name') should be detected as a write (named shared type access)."""
        f = tmp_path / "list.ts"
        f.write_text("const yArray = doc.getArray('items');\n")
        sites = _scan_file_for_yjs_patterns(f, "list.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "items"

    def test_detects_doc_get_text(self, tmp_path: Path) -> None:
        """doc.getText('name') should be detected as a write (named shared type access)."""
        f = tmp_path / "editor.ts"
        f.write_text("const yText = doc.getText('content');\n")
        sites = _scan_file_for_yjs_patterns(f, "editor.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "content"

    def test_detects_doc_get_xml_fragment(self, tmp_path: Path) -> None:
        """doc.getXmlFragment('name') should be detected as a write."""
        f = tmp_path / "xml.ts"
        f.write_text("const yXml = doc.getXmlFragment('prosemirror');\n")
        sites = _scan_file_for_yjs_patterns(f, "xml.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "prosemirror"

    def test_detects_subdocs_event(self, tmp_path: Path) -> None:
        """doc.on('subdocs', handler) should be detected as a read."""
        f = tmp_path / "sync.ts"
        f.write_text("doc.on('subdocs', ({ added, removed }) => { sync(); });\n")
        sites = _scan_file_for_yjs_patterns(f, "sync.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].api == "yjs"

    def test_links_getmap_to_observer_cross_file(self, tmp_path: Path) -> None:
        """doc.getMap('x') in one file + observe in another creates an edge."""
        w = tmp_path / "src" / "provider.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("const yMap = doc.getMap('state');\nyMap.set('cursor', pos);\n")

        r = tmp_path / "src" / "consumer.ts"
        r.write_text("yMap.observe(handler);\n")

        syms = [_make_ts_sym("src/provider.ts"), _make_ts_sym("src/consumer.ts")]
        result = link_yjs_crdt(tmp_path, syms)
        assert len(result.edges) >= 1


class TestScanBlockSuitePatterns:
    """Tests for BlockSuite framework pattern detection."""

    def test_detects_add_block(self, tmp_path: Path) -> None:
        """store.addBlock('flavour', ...) should be detected as a blocksuite write."""
        f = tmp_path / "editor.ts"
        f.write_text("store.addBlock('affine:paragraph', { type: 'text' }, noteId);\n")
        sites = _scan_file_for_yjs_patterns(f, "editor.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "affine:paragraph"
        assert writes[0].api == "blocksuite"

    def test_detects_block_updated_subscribe(self, tmp_path: Path) -> None:
        """store.slots.blockUpdated.subscribe() should be detected as a blocksuite read."""
        f = tmp_path / "listener.ts"
        f.write_text("store.slots.blockUpdated.subscribe(handler);\n")
        sites = _scan_file_for_yjs_patterns(f, "listener.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].api == "blocksuite"

    def test_detects_props_updated_subscribe(self, tmp_path: Path) -> None:
        """model.propsUpdated.subscribe() should be detected as a blocksuite read."""
        f = tmp_path / "watcher.ts"
        f.write_text("model.propsUpdated.subscribe(({ key }) => { refresh(); });\n")
        sites = _scan_file_for_yjs_patterns(f, "watcher.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].api == "blocksuite"

    def test_detects_define_block_schema(self, tmp_path: Path) -> None:
        """defineBlockSchema({ flavour: 'x' }) should be detected as a blocksuite write."""
        f = tmp_path / "schema.ts"
        f.write_text(
            "export const ParagraphBlockSchema = defineBlockSchema({\n"
            "  flavour: 'affine:paragraph',\n"
            "  props: (internal) => ({ text: internal.Text() }),\n"
            "});\n"
        )
        sites = _scan_file_for_yjs_patterns(f, "schema.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "affine:paragraph"
        assert writes[0].api == "blocksuite"

    def test_detects_slots_root_added(self, tmp_path: Path) -> None:
        """store.slots.rootAdded.subscribe() should be detected as a blocksuite read."""
        f = tmp_path / "init.ts"
        f.write_text("store.slots.rootAdded.subscribe((id) => { load(id); });\n")
        sites = _scan_file_for_yjs_patterns(f, "init.ts")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].api == "blocksuite"

    def test_blocksuite_write_links_to_blocksuite_read(self, tmp_path: Path) -> None:
        """addBlock in one file + slots.blockUpdated in another creates edge."""
        w = tmp_path / "src" / "creator.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("store.addBlock('affine:note', {}, rootId);\n")

        r = tmp_path / "src" / "reactor.ts"
        r.write_text("store.slots.blockUpdated.subscribe(handler);\n")

        syms = [_make_ts_sym("src/creator.ts"), _make_ts_sym("src/reactor.ts")]
        result = link_yjs_crdt(tmp_path, syms)
        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.edge_type == "crdt_publishes"

    def test_no_cross_api_blocksuite_to_yjs(self, tmp_path: Path) -> None:
        """BlockSuite writes should not match raw Yjs reads."""
        w = tmp_path / "src" / "bs_writer.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("store.addBlock('affine:paragraph', {}, noteId);\n")

        r = tmp_path / "src" / "yjs_reader.ts"
        r.write_text("yMap.observe(handler);\n")

        syms = [_make_ts_sym("src/bs_writer.ts"), _make_ts_sym("src/yjs_reader.ts")]
        result = link_yjs_crdt(tmp_path, syms)
        assert len(result.edges) == 0

    def test_detects_delete_block(self, tmp_path: Path) -> None:
        """store.deleteBlock() should be detected as a blocksuite write."""
        f = tmp_path / "cleanup.ts"
        f.write_text("store.deleteBlock(model);\n")
        sites = _scan_file_for_yjs_patterns(f, "cleanup.ts")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].api == "blocksuite"

    def test_detects_transact(self, tmp_path: Path) -> None:
        """store.transact() should be detected as a blocksuite write."""
        f = tmp_path / "batch.ts"
        f.write_text("store.transact(() => {\n  store.addBlock('affine:paragraph', {}, id);\n});\n")
        sites = _scan_file_for_yjs_patterns(f, "batch.ts")
        writes = [s for s in sites if s.kind == "write"]
        # Should detect both transact and addBlock
        assert len(writes) >= 1
        bs_writes = [w for w in writes if w.api == "blocksuite"]
        assert len(bs_writes) >= 1


class TestYjsSite:
    """Tests for the YjsSite dataclass."""

    def test_construction(self) -> None:
        """YjsSite should hold all fields."""
        site = YjsSite(
            kind="write", channel="cursor", file_path="src/a.ts",
            line=5, api="yjs",
        )
        assert site.kind == "write"
        assert site.channel == "cursor"
        assert site.api == "yjs"


class TestYjsCrdtRegistry:
    """Tests for linker registry integration."""

    def test_linker_registered(self) -> None:
        """yjs-crdt linker should be in the registry."""
        from hypergumbo_core.linkers.registry import get_all_linkers
        linkers = {l.name: l for l in get_all_linkers()}
        assert "yjs-crdt" in linkers

    def test_linker_runs_via_registry(self, tmp_path: Path) -> None:
        """Linker should produce results when run via registry dispatch."""
        from hypergumbo_core.linkers.registry import LinkerContext, run_all_linkers

        w = tmp_path / "src" / "writer.ts"
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_text("yMap.set('key', val);\n")

        r = tmp_path / "src" / "reader.ts"
        r.write_text("yMap.observe(handler);\n")

        syms = [_make_ts_sym("src/writer.ts"), _make_ts_sym("src/reader.ts")]
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=syms,
            detected_frameworks=set(),
            detected_languages={"typescript"},
        )
        results = run_all_linkers(ctx)
        yjs_results = [r for name, r in results if name == "yjs-crdt"]
        assert len(yjs_results) == 1
        assert len(yjs_results[0].edges) >= 1
