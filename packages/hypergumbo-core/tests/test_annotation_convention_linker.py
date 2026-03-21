# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the annotation convention linker (@hg: directives).

Tests the @hg:publishes / @hg:subscribes comment-based annotation system
for declaring pub/sub relationships that automatic detection can't find.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol
from hypergumbo_core.linkers.annotation_convention import (
    AnnotationSite,
    link_annotations,
    scan_file_for_annotations,
)


def _make_sym(path: str, language: str = "typescript") -> Symbol:
    """Create a minimal symbol for testing."""
    return Symbol(
        id=f"{language}:{path}:1-10:test:function",
        name="test",
        kind="function",
        language=language,
        path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        origin="test-v1",
        origin_run_id="uuid:test",
    )


class TestScanFileForAnnotations:
    """Tests for the file scanner."""

    def test_finds_publishes_directive(self, tmp_path: Path) -> None:
        """@hg:publishes should be detected."""
        f = tmp_path / "src" / "a.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// @hg:publishes awareness.cursor\n")
        sites = scan_file_for_annotations(f, "src/a.ts")
        assert len(sites) == 1
        assert sites[0].directive == "publishes"
        assert sites[0].argument == "awareness.cursor"
        assert sites[0].line == 1

    def test_finds_subscribes_directive(self, tmp_path: Path) -> None:
        """@hg:subscribes should be detected."""
        f = tmp_path / "src" / "b.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("function onCursor() {\n  // @hg:subscribes awareness.cursor\n}\n")
        sites = scan_file_for_annotations(f, "src/b.ts")
        assert len(sites) == 1
        assert sites[0].directive == "subscribes"
        assert sites[0].argument == "awareness.cursor"
        assert sites[0].line == 2

    def test_finds_multiple_directives(self, tmp_path: Path) -> None:
        """Multiple directives in one file should all be found."""
        f = tmp_path / "src" / "c.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "// @hg:publishes channel-a\n"
            "emit(data);\n"
            "// @hg:subscribes channel-b\n"
            "listen(handler);\n"
        )
        sites = scan_file_for_annotations(f, "src/c.ts")
        assert len(sites) == 2
        assert sites[0].directive == "publishes"
        assert sites[0].argument == "channel-a"
        assert sites[1].directive == "subscribes"
        assert sites[1].argument == "channel-b"

    def test_hash_comment_style(self, tmp_path: Path) -> None:
        """Python # comment style should work."""
        f = tmp_path / "src" / "d.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# @hg:publishes config.reload\n")
        sites = scan_file_for_annotations(f, "src/d.py")
        assert len(sites) == 1
        assert sites[0].argument == "config.reload"

    def test_no_directives_returns_empty(self, tmp_path: Path) -> None:
        """Files without @hg: should return empty list."""
        f = tmp_path / "src" / "e.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("const x = 1;\n")
        sites = scan_file_for_annotations(f, "src/e.ts")
        assert sites == []

    def test_route_directive(self, tmp_path: Path) -> None:
        """@hg:route should be detected."""
        f = tmp_path / "src" / "f.rs"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// @hg:route POST join\n")
        sites = scan_file_for_annotations(f, "src/f.rs")
        assert len(sites) == 1
        assert sites[0].directive == "route"
        assert sites[0].argument == "POST join"

    def test_dispatches_directive(self, tmp_path: Path) -> None:
        """@hg:dispatches should be detected."""
        f = tmp_path / "src" / "g.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// @hg:dispatches handle_join\n")
        sites = scan_file_for_annotations(f, "src/g.ts")
        assert len(sites) == 1
        assert sites[0].directive == "dispatches"
        assert sites[0].argument == "handle_join"


class TestLinkAnnotations:
    """Tests for the pub/sub matching logic."""

    def test_matches_publisher_to_subscriber(self, tmp_path: Path) -> None:
        """Publisher and subscriber on same channel produce an edge."""
        pub_file = tmp_path / "src" / "writer.ts"
        pub_file.parent.mkdir(parents=True, exist_ok=True)
        pub_file.write_text("// @hg:publishes cursor.position\nfunction write() {}\n")

        sub_file = tmp_path / "src" / "reader.ts"
        sub_file.parent.mkdir(parents=True, exist_ok=True)
        sub_file.write_text("// @hg:subscribes cursor.position\nfunction read() {}\n")

        syms = [
            _make_sym("src/writer.ts"),
            _make_sym("src/reader.ts"),
        ]
        result = link_annotations(tmp_path, syms)

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "annotated_publishes"
        assert edge.confidence == 0.95
        assert edge.meta is not None
        assert edge.meta["access_mode"] == "write"
        assert edge.meta["dest_access_mode"] == "read"
        assert edge.meta["channel"] == "cursor.position"

    def test_no_match_different_channels(self, tmp_path: Path) -> None:
        """Publisher and subscriber on different channels produce no edges."""
        pub_file = tmp_path / "src" / "a.ts"
        pub_file.parent.mkdir(parents=True, exist_ok=True)
        pub_file.write_text("// @hg:publishes channel-a\n")

        sub_file = tmp_path / "src" / "b.ts"
        sub_file.parent.mkdir(parents=True, exist_ok=True)
        sub_file.write_text("// @hg:subscribes channel-b\n")

        syms = [_make_sym("src/a.ts"), _make_sym("src/b.ts")]
        result = link_annotations(tmp_path, syms)
        assert len(result.edges) == 0

    def test_multiple_subscribers_per_channel(self, tmp_path: Path) -> None:
        """One publisher with two subscribers creates two edges."""
        pub_file = tmp_path / "src" / "emitter.ts"
        pub_file.parent.mkdir(parents=True, exist_ok=True)
        pub_file.write_text("// @hg:publishes status.update\n")

        sub1 = tmp_path / "src" / "ui.ts"
        sub1.parent.mkdir(parents=True, exist_ok=True)
        sub1.write_text("// @hg:subscribes status.update\n")

        sub2 = tmp_path / "src" / "logger.ts"
        sub2.parent.mkdir(parents=True, exist_ok=True)
        sub2.write_text("// @hg:subscribes status.update\n")

        syms = [
            _make_sym("src/emitter.ts"),
            _make_sym("src/ui.ts"),
            _make_sym("src/logger.ts"),
        ]
        result = link_annotations(tmp_path, syms)
        assert len(result.edges) == 2

    def test_creates_synthetic_symbols(self, tmp_path: Path) -> None:
        """Publisher and subscriber should create synthetic symbols."""
        pub_file = tmp_path / "src" / "pub.ts"
        pub_file.parent.mkdir(parents=True, exist_ok=True)
        pub_file.write_text("// @hg:publishes events.created\n")

        sub_file = tmp_path / "src" / "sub.ts"
        sub_file.parent.mkdir(parents=True, exist_ok=True)
        sub_file.write_text("// @hg:subscribes events.created\n")

        syms = [_make_sym("src/pub.ts"), _make_sym("src/sub.ts")]
        result = link_annotations(tmp_path, syms)

        assert len(result.symbols) == 2
        pub_sym = next(s for s in result.symbols if s.kind == "event_publisher")
        sub_sym = next(s for s in result.symbols if s.kind == "event_subscriber")
        assert pub_sym.name == "events.created"
        assert sub_sym.name == "events.created"
        assert pub_sym.meta["hg_annotation"] == "publishes"
        assert sub_sym.meta["hg_annotation"] == "subscribes"

    def test_empty_symbols_returns_empty(self, tmp_path: Path) -> None:
        """No symbols should return empty result."""
        result = link_annotations(tmp_path, [])
        assert len(result.edges) == 0
        assert len(result.symbols) == 0
        assert result.run is not None

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Symbols pointing to nonexistent files should be skipped."""
        syms = [_make_sym("src/nonexistent.ts")]
        result = link_annotations(tmp_path, syms)
        assert len(result.edges) == 0

    def test_publisher_without_subscriber_no_edges(self, tmp_path: Path) -> None:
        """A publisher with no matching subscriber creates no edges."""
        f = tmp_path / "src" / "lonely.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// @hg:publishes orphan.channel\n")

        result = link_annotations(tmp_path, [_make_sym("src/lonely.ts")])
        assert len(result.edges) == 0
        # But no synthetic symbols either (no subscribers to match)
        assert len(result.symbols) == 0

    def test_deduplicates_symbol_paths(self, tmp_path: Path) -> None:
        """Multiple symbols with same path should only scan the file once."""
        f = tmp_path / "src" / "shared.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// @hg:publishes dedup.test\n")

        syms = [
            _make_sym("src/shared.ts"),
            Symbol(
                id="typescript:src/shared.ts:20-30:other:function",
                name="other", kind="function", language="typescript",
                path="src/shared.ts",
                span=Span(start_line=20, end_line=30, start_col=0, end_col=0),
                origin="test-v1", origin_run_id="uuid:test",
            ),
        ]
        # Should not crash or produce duplicates
        result = link_annotations(tmp_path, syms)
        # No subscribers, so no edges
        assert len(result.edges) == 0


class TestLinkRouteAnnotations:
    """Tests for @hg:route matching."""

    def test_route_creates_route_symbol(self, tmp_path: Path) -> None:
        """@hg:route POST /api/join should create a route symbol."""
        f = tmp_path / "src" / "handler.rs"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// @hg:route POST join\nfn handle_join() {}\n")

        syms = [_make_sym("src/handler.rs", language="rust")]
        result = link_annotations(tmp_path, syms)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) >= 1
        assert routes[0].name == "POST join"
        assert routes[0].meta["hg_annotation"] == "route"

    def test_multiple_routes_in_one_file(self, tmp_path: Path) -> None:
        """Multiple @hg:route directives create multiple route symbols."""
        f = tmp_path / "src" / "api.rs"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "// @hg:route POST join\n"
            "fn handle_join() {}\n"
            "// @hg:route POST leave\n"
            "fn handle_leave() {}\n"
        )

        syms = [_make_sym("src/api.rs", language="rust")]
        result = link_annotations(tmp_path, syms)

        routes = [s for s in result.symbols if s.kind == "route"]
        assert len(routes) == 2
        names = {r.name for r in routes}
        assert "POST join" in names
        assert "POST leave" in names


class TestLinkDispatchAnnotations:
    """Tests for @hg:dispatches matching."""

    def test_dispatches_creates_edge_to_named_symbol(self, tmp_path: Path) -> None:
        """@hg:dispatches target should create a dispatches_to edge."""
        src = tmp_path / "src" / "router.ts"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("// @hg:dispatches handle_join\nroute(msg);\n")

        dst = tmp_path / "src" / "handler.ts"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("function handle_join(msg) {}\n")

        syms = [
            _make_sym("src/router.ts"),
            Symbol(
                id="typescript:src/handler.ts:1-1:handle_join:function",
                name="handle_join", kind="function", language="typescript",
                path="src/handler.ts",
                span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
                origin="test-v1", origin_run_id="uuid:test",
            ),
        ]
        result = link_annotations(tmp_path, syms)

        dispatch_edges = [e for e in result.edges if e.edge_type == "annotated_dispatches"]
        assert len(dispatch_edges) >= 1
        assert dispatch_edges[0].meta["channel"] == "handle_join"

    def test_dispatches_no_target_no_edge(self, tmp_path: Path) -> None:
        """@hg:dispatches with no matching symbol creates no edge."""
        f = tmp_path / "src" / "router.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// @hg:dispatches nonexistent_handler\n")

        syms = [_make_sym("src/router.ts")]
        result = link_annotations(tmp_path, syms)

        dispatch_edges = [e for e in result.edges if e.edge_type == "annotated_dispatches"]
        assert len(dispatch_edges) == 0

    def test_dispatches_creates_synthetic_symbol(self, tmp_path: Path) -> None:
        """@hg:dispatches should create a synthetic dispatcher symbol."""
        src = tmp_path / "src" / "dispatch.ts"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("// @hg:dispatches process_msg\n")

        dst = tmp_path / "src" / "proc.ts"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("function process_msg() {}\n")

        syms = [
            _make_sym("src/dispatch.ts"),
            Symbol(
                id="typescript:src/proc.ts:1-1:process_msg:function",
                name="process_msg", kind="function", language="typescript",
                path="src/proc.ts",
                span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
                origin="test-v1", origin_run_id="uuid:test",
            ),
        ]
        result = link_annotations(tmp_path, syms)

        dispatchers = [s for s in result.symbols if s.kind == "dispatcher"]
        assert len(dispatchers) >= 1


class TestAnnotationSite:
    """Tests for the AnnotationSite dataclass."""

    def test_basic_construction(self) -> None:
        """AnnotationSite should hold all fields."""
        site = AnnotationSite(
            directive="publishes",
            argument="test.channel",
            file_path="src/a.ts",
            line=42,
        )
        assert site.directive == "publishes"
        assert site.argument == "test.channel"
        assert site.file_path == "src/a.ts"
        assert site.line == 42


class TestAnnotationConventionRegistry:
    """Tests for linker registry integration."""

    def test_linker_registered(self) -> None:
        """annotation-convention linker should be in the registry."""
        from hypergumbo_core.linkers.registry import get_all_linkers
        linkers = {l.name: l for l in get_all_linkers()}
        assert "annotation-convention" in linkers

    def test_linker_runs_via_registry(self, tmp_path: Path) -> None:
        """Linker should produce results when run via registry."""
        from hypergumbo_core.linkers.registry import LinkerContext, run_all_linkers

        pub_file = tmp_path / "src" / "pub.ts"
        pub_file.parent.mkdir(parents=True, exist_ok=True)
        pub_file.write_text("// @hg:publishes registry.test\n")

        sub_file = tmp_path / "src" / "sub.ts"
        sub_file.parent.mkdir(parents=True, exist_ok=True)
        sub_file.write_text("// @hg:subscribes registry.test\n")

        syms = [_make_sym("src/pub.ts"), _make_sym("src/sub.ts")]
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=syms,
            detected_frameworks=set(),
            detected_languages={"typescript"},
        )
        results = run_all_linkers(ctx)
        anno_results = [r for name, r in results if name == "annotation-convention"]
        assert len(anno_results) == 1
        assert len(anno_results[0].edges) >= 1
