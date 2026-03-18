# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for I/O boundary analysis (ADR-0016).

Covers the Python I/O primitive catalog loading, edge matching,
and boundary map generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    BoundaryMap,
    IoBoundaryCatalog,
    IoChain,
    IoPrimitive,
    _extract_callee_name,
    compute_boundary_map,
    load_catalog,
    match_edge_to_primitive,
    tag_io_boundaries,
)


class TestIoPrimitive:
    """Tests for the IoPrimitive dataclass."""

    def test_basic_creation(self) -> None:
        p = IoPrimitive(
            boundary="fs_read",
            module="os",
            name="listdir",
            kind="function",
        )
        assert p.boundary == "fs_read"
        assert p.qualified_name == "os.listdir"

    def test_method_qualified_name(self) -> None:
        p = IoPrimitive(
            boundary="fs_read",
            module="pathlib.Path",
            name="read_text",
            kind="method",
        )
        assert p.qualified_name == "pathlib.Path.read_text"


class TestLoadCatalog:
    """Tests for loading YAML I/O primitive catalogs."""

    def test_load_python_catalog(self) -> None:
        catalog = load_catalog("python")
        assert catalog.language == "python"
        assert len(catalog.primitives) > 0

    def test_python_catalog_has_fs_read(self) -> None:
        catalog = load_catalog("python")
        fs_reads = [p for p in catalog.primitives if p.boundary == "fs_read"]
        assert len(fs_reads) > 0
        names = {p.qualified_name for p in fs_reads}
        assert "os.listdir" in names
        assert "pathlib.Path.read_text" in names

    def test_python_catalog_has_fs_write(self) -> None:
        catalog = load_catalog("python")
        fs_writes = [p for p in catalog.primitives if p.boundary == "fs_write"]
        names = {p.qualified_name for p in fs_writes}
        assert "pathlib.Path.write_text" in names
        assert "shutil.rmtree" in names

    def test_python_catalog_has_net_send(self) -> None:
        catalog = load_catalog("python")
        net_sends = [p for p in catalog.primitives if p.boundary == "net_send"]
        names = {p.qualified_name for p in net_sends}
        assert "socket.socket.send" in names

    def test_python_catalog_has_subprocess(self) -> None:
        catalog = load_catalog("python")
        subprocs = [p for p in catalog.primitives if p.boundary == "subprocess"]
        names = {p.qualified_name for p in subprocs}
        assert "subprocess.run" in names

    def test_python_catalog_has_env_read(self) -> None:
        catalog = load_catalog("python")
        env_reads = [p for p in catalog.primitives if p.boundary == "env_read"]
        names = {p.qualified_name for p in env_reads}
        assert "os.getenv" in names

    def test_python_catalog_all_boundaries_present(self) -> None:
        catalog = load_catalog("python")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_catalog_builds_lookup(self) -> None:
        catalog = load_catalog("python")
        # The lookup should enable O(1) matching by qualified name
        assert catalog.lookup("os.listdir") is not None
        assert catalog.lookup("os.listdir").boundary == "fs_read"
        assert catalog.lookup("nonexistent.function") is None

    def test_load_rust_catalog(self) -> None:
        catalog = load_catalog("rust")
        assert catalog.language == "rust"
        assert len(catalog.primitives) > 0
        names = {p.qualified_name for p in catalog.primitives}
        assert "std::fs.read" in names or "std::fs.read_to_string" in names
        assert catalog.lookup("std::fs.read_to_string") is not None
        assert catalog.lookup("std::fs.read_to_string").boundary == "fs_read"

    def test_load_javascript_catalog(self) -> None:
        catalog = load_catalog("javascript")
        assert catalog.language == "javascript"
        assert len(catalog.primitives) > 0
        names = {p.qualified_name for p in catalog.primitives}
        assert "fs.readFileSync" in names
        assert "child_process.spawn" in names
        assert catalog.lookup("fs.readFileSync").boundary == "fs_read"
        assert catalog.lookup("child_process.spawn").boundary == "subprocess"

    def test_rust_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("rust")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_javascript_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("javascript")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_load_go_catalog(self) -> None:
        catalog = load_catalog("go")
        assert catalog.language == "go"
        assert len(catalog.primitives) > 0
        assert catalog.lookup("os.ReadFile").boundary == "fs_read"
        assert catalog.lookup("net/http.Get").boundary == "net_send"
        assert catalog.lookup("os/exec.Command").boundary == "subprocess"

    def test_go_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("go")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_load_c_catalog(self) -> None:
        catalog = load_catalog("c")
        assert catalog.language == "c"
        assert len(catalog.primitives) > 0
        assert catalog.lookup("stdio.fopen").boundary == "fs_read"
        assert catalog.lookup("unistd.fork").boundary == "subprocess"

    def test_c_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("c")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_load_java_catalog(self) -> None:
        catalog = load_catalog("java")
        assert catalog.language == "java"
        assert len(catalog.primitives) > 0
        assert catalog.lookup("java.nio.file.Files.readAllBytes").boundary == "fs_read"
        assert catalog.lookup("java.lang.ProcessBuilder.start").boundary == "subprocess"

    def test_java_catalog_has_all_boundary_types(self) -> None:
        catalog = load_catalog("java")
        boundaries = {p.boundary for p in catalog.primitives}
        expected = {"fs_read", "fs_write", "net_send", "net_recv", "subprocess", "env_read"}
        assert expected.issubset(boundaries)

    def test_load_nonexistent_language_returns_empty(self) -> None:
        catalog = load_catalog("brainfuck")
        assert catalog.language == "brainfuck"
        assert len(catalog.primitives) == 0

    def test_catalog_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = """\
language: testlang

fs_read:
  - module: io
    functions: [read_file, load]
  - module: io.Path
    methods: [read_text]

net_send:
  - module: net
    functions: [send]
"""
        yaml_file = tmp_path / "testlang.yaml"
        yaml_file.write_text(yaml_content)
        catalog = IoBoundaryCatalog.from_yaml(yaml_file)
        assert catalog.language == "testlang"
        assert len(catalog.primitives) == 4
        assert catalog.lookup("io.read_file").boundary == "fs_read"
        assert catalog.lookup("io.Path.read_text").boundary == "fs_read"
        assert catalog.lookup("net.send").boundary == "net_send"


class TestMatchEdgeToPrimitive:
    """Tests for matching call edges to I/O primitives."""

    def test_match_simple_function_call(self) -> None:
        catalog = load_catalog("python")
        # Simulate a call edge where the target is "os.listdir"
        result = match_edge_to_primitive(catalog, "os.listdir")
        assert result is not None
        assert result.boundary == "fs_read"

    def test_match_method_call(self) -> None:
        catalog = load_catalog("python")
        result = match_edge_to_primitive(catalog, "pathlib.Path.read_text")
        assert result is not None
        assert result.boundary == "fs_read"

    def test_no_match_for_non_io(self) -> None:
        catalog = load_catalog("python")
        result = match_edge_to_primitive(catalog, "math.sqrt")
        assert result is None

    def test_match_unqualified_name(self) -> None:
        catalog = load_catalog("python")
        # Short name matching: "listdir" should match "os.listdir"
        result = match_edge_to_primitive(catalog, "listdir")
        assert result is not None
        assert result.boundary == "fs_read"

    def test_open_matches_both_fs_read_and_fs_write(self) -> None:
        catalog = load_catalog("python")
        results = catalog.lookup_all("open")
        boundaries = {r.boundary for r in results}
        assert "fs_read" in boundaries
        assert "fs_write" in boundaries

    def test_lookup_all_qualified_name(self) -> None:
        catalog = load_catalog("python")
        results = catalog.lookup_all("os.listdir")
        assert len(results) == 1
        assert results[0].boundary == "fs_read"

    def test_catalog_ignores_malformed_yaml_entries(self, tmp_path: Path) -> None:
        """Non-list boundary values and non-dict entries are skipped."""
        yaml_content = """\
language: broken

fs_read: "not a list"

fs_write:
  - "not a dict"
  - module: ok
    functions: [write_file]
"""
        yaml_file = tmp_path / "broken.yaml"
        yaml_file.write_text(yaml_content)
        catalog = IoBoundaryCatalog.from_yaml(yaml_file)
        assert catalog.language == "broken"
        assert len(catalog.primitives) == 1
        assert catalog.primitives[0].name == "write_file"


class TestExtractCalleeName:
    """Tests for _extract_callee_name."""

    def test_standard_symbol_id(self) -> None:
        sid = "python:/path/to/file.py:10-12:os.listdir:function"
        assert _extract_callee_name(sid) == "os.listdir"

    def test_method_symbol_id(self) -> None:
        sid = "python:/path/file.py:5-7:pathlib.Path.read_text:method"
        assert _extract_callee_name(sid) == "pathlib.Path.read_text"

    def test_minimal_id(self) -> None:
        sid = "a:b"
        assert _extract_callee_name(sid) == "a"

    def test_bare_name(self) -> None:
        sid = "nodelimiters"
        assert _extract_callee_name(sid) == "nodelimiters"


class TestTagIoBoundaries:
    """Tests for the boundary-tagging pass."""

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        """Create a minimal Edge-like object for testing."""
        from dataclasses import dataclass, field as dc_field
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_tags_call_to_io_primitive(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/main.py:10-12:main:function",
            dst="python:/stdlib/os.py:100-102:os.listdir:function",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta is not None
        assert edge.meta["io_boundary"] == "fs_read"
        assert edge.meta["io_primitive"] == "os.listdir"

    def test_skips_non_call_edges(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/main.py:1:mod:module",
            dst="python:/stdlib/os.py:1:os:module",
            edge_type="contains",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0
        assert edge.meta is None

    def test_skips_unknown_language(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="cobol:/app/main.cbl:1:MAIN:paragraph",
            dst="cobol:/stdlib/io.cbl:1:OPEN-FILE:paragraph",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0

    def test_tags_subprocess_call(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/app/deploy.py:50-52:deploy:function",
            dst="python:/stdlib/subprocess.py:200-210:subprocess.run:function",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "subprocess"

    def test_multiple_edges_mixed(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
            self._make_edge(
                src="python:/a.py:2:f:function",
                dst="python:/math.py:1:math.sqrt:function",
            ),
            self._make_edge(
                src="python:/a.py:3:f:function",
                dst="python:/sub.py:1:subprocess.run:function",
            ),
        ]
        count = tag_io_boundaries(edges, {"python": catalog})
        assert count == 2  # listdir + subprocess.run
        assert edges[0].meta is not None
        assert edges[1].meta is None
        assert edges[2].meta is not None

    def test_preserves_existing_meta(self) -> None:
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/a.py:1:f:function",
            dst="python:/os.py:1:os.listdir:function",
        )
        edge.meta = {"existing_key": "value"}
        tag_io_boundaries([edge], {"python": catalog})
        assert edge.meta["existing_key"] == "value"
        assert edge.meta["io_boundary"] == "fs_read"

    def test_empty_dst_parts(self) -> None:
        """Edge with empty dst is skipped gracefully."""
        catalog = load_catalog("python")
        edge = self._make_edge(src="python:a", dst="", edge_type="calls")
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 0


class TestComputeBoundaryMap:
    """Tests for the full boundary map computation."""

    def _make_edge(self, src: str, dst: str, edge_type: str = "calls"):
        from dataclasses import dataclass
        from typing import Optional, Dict, Any

        @dataclass
        class MockEdge:
            src: str
            dst: str
            edge_type: str
            meta: Optional[Dict[str, Any]] = None

        return MockEdge(src=src, dst=dst, edge_type=edge_type, meta=None)

    def test_basic_boundary_map(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/app/main.py:10:main:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
            self._make_edge(
                src="python:/app/main.py:20:main:function",
                dst="python:/sub.py:1:subprocess.run:function",
            ),
            self._make_edge(
                src="python:/app/main.py:30:main:function",
                dst="python:/math.py:1:math.sqrt:function",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        assert bmap.total_io_edges == 2
        assert "fs_read" in bmap.entries
        assert "subprocess" in bmap.entries
        assert len(bmap.entries["fs_read"].chains) == 1
        assert bmap.entries["fs_read"].primitives_used == ["os.listdir"]

    def test_boundary_map_to_dict(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        d = bmap.to_dict()
        assert d["total_io_edges"] == 1
        assert "fs_read" in d["boundaries"]
        assert d["boundaries"]["fs_read"]["chain_count"] == 1

    def test_empty_edges(self) -> None:
        bmap = compute_boundary_map([], {"python": load_catalog("python")})
        assert bmap.total_io_edges == 0
        assert len(bmap.entries) == 0

    def test_io_chain_dataclass(self) -> None:
        chain = IoChain(
            boundary="fs_read",
            primitive="os.listdir",
            io_edge_src="python:/a.py:1:f:function",
            io_edge_dst="python:/os.py:1:os.listdir:function",
            entry_points=["main"],
        )
        assert chain.boundary == "fs_read"
        assert chain.entry_points == ["main"]

    def test_edges_with_non_io_meta_skipped(self) -> None:
        """Edges with meta but no io_boundary are not counted."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/a.py:1:f:function",
            dst="python:/b.py:1:g:function",
        )
        edge.meta = {"access_mode": "read"}  # non-IO meta
        bmap = compute_boundary_map([edge], {"python": catalog})
        assert bmap.total_io_edges == 0
        assert len(bmap.entries) == 0

    def test_chains_with_entry_points(self) -> None:
        """IoChain entry_points are aggregated into BoundaryMapEntry."""
        from hypergumbo_core.io_boundary import BoundaryMapEntry
        chain = IoChain(
            boundary="fs_read",
            primitive="os.listdir",
            io_edge_src="python:/a.py:1:f:function",
            io_edge_dst="python:/os.py:1:os.listdir:function",
            entry_points=["main", "cli_handler"],
        )
        entry = BoundaryMapEntry(boundary="fs_read")
        entry.chains.append(chain)
        ep_set: set[str] = set()
        for c in entry.chains:
            for ep in c.entry_points:
                ep_set.add(ep)
        entry.entry_points = sorted(ep_set)
        assert entry.entry_points == ["cli_handler", "main"]

    def test_multiple_primitives_same_boundary(self) -> None:
        catalog = load_catalog("python")
        edges = [
            self._make_edge(
                src="python:/a.py:1:f:function",
                dst="python:/os.py:1:os.listdir:function",
            ),
            self._make_edge(
                src="python:/b.py:1:g:function",
                dst="python:/os.py:2:os.walk:function",
            ),
        ]
        bmap = compute_boundary_map(edges, {"python": catalog})
        assert bmap.total_io_edges == 2
        assert len(bmap.entries["fs_read"].chains) == 2
        assert sorted(bmap.entries["fs_read"].primitives_used) == [
            "os.listdir", "os.walk",
        ]
