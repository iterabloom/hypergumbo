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
    _extract_module_hint,
    _module_matches,
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

    def test_rust_catalog_has_tokio_framework_entries(self) -> None:
        """Rust catalog includes Tokio/Hyper/Reqwest framework entries."""
        catalog = load_catalog("rust")
        qualified_names = {p.qualified_name for p in catalog.primitives}
        assert "tokio::net::TcpStream.connect" in qualified_names
        assert "tokio::net::TcpListener.bind" in qualified_names
        assert "tokio::fs.read" in qualified_names
        assert "reqwest::Client.get" in qualified_names
        assert "hyper::Client.get" in qualified_names
        assert "axum::Router.route" in qualified_names

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

    def test_java_catalog_has_netty_framework_entries(self) -> None:
        """Java catalog includes Netty framework IO methods."""
        catalog = load_catalog("java")
        qualified_names = {p.qualified_name for p in catalog.primitives}
        assert "io.netty.channel.Channel.write" in qualified_names
        assert "io.netty.channel.Channel.read" in qualified_names
        assert "io.netty.buffer.ByteBuf.writeBytes" in qualified_names
        assert "io.netty.buffer.ByteBuf.readBytes" in qualified_names
        assert "io.netty.bootstrap.ServerBootstrap.bind" in qualified_names

    def test_java_catalog_netty_channel_write_is_net_send(self) -> None:
        """Netty Channel.write is classified as net_send."""
        catalog = load_catalog("java")
        match = catalog.lookup("io.netty.channel.Channel.write")
        assert match is not None
        assert match.boundary == "net_send"

    def test_kotlin_alias_loads_java_catalog(self) -> None:
        """Kotlin uses the Java IO catalog via alias."""
        catalog = load_catalog("kotlin")
        assert len(catalog.primitives) > 0
        assert catalog.lookup("java.io.FileInputStream.read") is not None

    def test_scala_alias_loads_java_catalog(self) -> None:
        """Scala uses the Java IO catalog via alias."""
        catalog = load_catalog("scala")
        assert len(catalog.primitives) > 0

    def test_groovy_alias_loads_java_catalog(self) -> None:
        """Groovy uses the Java IO catalog via alias."""
        catalog = load_catalog("groovy")
        assert len(catalog.primitives) > 0

    def test_load_nonexistent_language_returns_empty(self) -> None:
        catalog = load_catalog("brainfuck")
        assert catalog.language == "brainfuck"
        assert len(catalog.primitives) == 0

    def test_cpp_alias_loads_c_catalog(self) -> None:
        """C++ has no dedicated catalog but falls back to C via alias."""
        catalog = load_catalog("cpp")
        assert len(catalog.primitives) > 0
        boundaries = {p.boundary for p in catalog.primitives}
        assert "fs_read" in boundaries
        assert "fs_write" in boundaries

    def test_typescript_alias_loads_javascript_catalog(self) -> None:
        """TypeScript falls back to JavaScript catalog via alias."""
        catalog = load_catalog("typescript")
        assert len(catalog.primitives) > 0
        boundaries = {p.boundary for p in catalog.primitives}
        assert "fs_read" in boundaries
        assert "net_send" in boundaries

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


class TestModuleMatches:
    """Tests for _module_matches helper."""

    def test_exact_match(self) -> None:
        assert _module_matches("net.Conn", "net.Conn") is True

    def test_catalog_is_prefix(self) -> None:
        assert _module_matches("java.io", "java.io.FileInputStream") is True

    def test_edge_is_prefix(self) -> None:
        assert _module_matches("java.io.FileInputStream", "java.io") is True

    def test_no_match(self) -> None:
        assert _module_matches("net.Conn", "crypto/rand") is False

    def test_rust_double_colon(self) -> None:
        assert _module_matches("std::fs", "std::fs::File") is True

    def test_go_slash_vs_dot(self) -> None:
        assert _module_matches("os/exec", "os.exec.Cmd") is True


class TestExtractModuleHint:
    """Tests for _extract_module_hint helper."""

    def test_unresolved_edge(self) -> None:
        assert _extract_module_hint("go:net.Conn:0-0:Read:unresolved") == "net.Conn"

    def test_external_fallback(self) -> None:
        assert _extract_module_hint("go:external:0-0:Read:unresolved") == "external"

    def test_file_path_returns_none(self) -> None:
        assert _extract_module_hint("python:/path/to/file.py:1-5:func:function") is None

    def test_short_id(self) -> None:
        assert _extract_module_hint("a:b") is None


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


    def test_ffi_edge_types_traced(self) -> None:
        """FFI edge types (wasm_bridge, ipc_calls, etc.) are included in boundary tagging."""
        catalog = load_catalog("python")
        # A wasm_bridge edge where the target is a Python I/O function
        edge = self._make_edge(
            src="typescript:/app/wasm.ts:1:loadWasm:function",
            dst="python:/stdlib/os.py:1:os.listdir:function",
            edge_type="wasm_bridge",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_ipc_calls_edge_traced(self) -> None:
        """ipc_calls edges are traced for I/O boundary tagging."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="rust:/app/main.rs:1:invoke:function",
            dst="python:/handler.py:1:subprocess.run:function",
            edge_type="ipc_calls",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "subprocess"


class TestModuleQualifiedMatching:
    """Tests for module-qualified IO boundary matching.

    Prevents false positives from generic method names like Read/Write
    by checking the module context in the edge's destination ID.
    """

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

    def test_matching_module_tags_edge(self) -> None:
        """Edge with matching module_hint gets tagged."""
        catalog = load_catalog("go")
        # net.Conn.Read is in the catalog — module_hint matches
        edge = self._make_edge(
            src="go:/a.go:1:handler:function",
            dst="go:net.Conn:0-0:Read:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "net_recv"

    def test_mismatched_module_not_tagged(self) -> None:
        """Edge with non-matching module_hint is NOT tagged.

        crypto/rand.Reader.Read() should not match net.Conn.Read because
        crypto/rand != net.Conn.
        """
        catalog = load_catalog("go")
        edge = self._make_edge(
            src="go:/a.go:1:encrypt:function",
            dst="go:crypto/rand:0-0:Read:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 0
        assert edge.meta is None

    def test_external_module_hint_falls_back_to_name_match(self) -> None:
        """When module_hint is 'external' (unknown), fall back to name matching."""
        catalog = load_catalog("go")
        # os.Open is in the Go catalog — module_hint "external" means we
        # don't know the module so allow name-only matching
        edge = self._make_edge(
            src="go:/a.go:1:main:function",
            dst="go:external:0-0:Open:unresolved",
        )
        count = tag_io_boundaries([edge], {"go": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_python_qualified_name_still_works(self) -> None:
        """Python edges with qualified callee names still match."""
        catalog = load_catalog("python")
        edge = self._make_edge(
            src="python:/a.py:1:f:function",
            dst="python:/os.py:1:os.listdir:function",
        )
        count = tag_io_boundaries([edge], {"python": catalog})
        assert count == 1
        assert edge.meta["io_boundary"] == "fs_read"

    def test_rust_write_not_confused_with_net(self) -> None:
        """Rust io::Write.write() shouldn't match net.Conn.Write."""
        catalog = load_catalog("rust")
        # io::Write is a file-like trait, not network
        edge = self._make_edge(
            src="rust:/a.rs:1:save:function",
            dst="rust:std::io::Write:0-0:write:unresolved",
        )
        count = tag_io_boundaries([edge], {"rust": catalog})
        # Should match fs_write (io::Write), not net_send
        if count > 0:
            assert edge.meta["io_boundary"] == "fs_write"


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


class TestEntryPointTracing:
    """Tests for reverse-tracing IO edges back to entrypoints."""

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

    def test_entry_points_populated_when_provided(self) -> None:
        """When entrypoints are provided, IO chains have entry_points populated."""
        catalog = load_catalog("python")
        edges = [
            # main → helper → os.listdir (IO)
            self._make_edge(src="main", dst="helper"),
            self._make_edge(src="helper", dst="python:os:0-0:listdir:function"),
        ]
        entrypoint_ids = {"main"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "main" in fs_entry.entry_points

    def test_entry_points_empty_without_entrypoints(self) -> None:
        """When no entrypoints provided, entry_points remain empty."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="helper", dst="python:os:0-0:listdir:function"),
        ]

        bmap = compute_boundary_map(edges, {"python": catalog})
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert len(fs_entry.entry_points) == 0

    def test_multiple_entry_points_reach_same_io(self) -> None:
        """Multiple entrypoints can reach the same IO chain."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="main1", dst="helper"),
            self._make_edge(src="main2", dst="helper"),
            self._make_edge(src="helper", dst="python:os:0-0:listdir:function"),
        ]
        entrypoint_ids = {"main1", "main2"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        fs_entry = bmap.entries["fs_read"]
        assert "main1" in fs_entry.entry_points
        assert "main2" in fs_entry.entry_points

    def test_cyclic_call_graph_terminates(self) -> None:
        """BFS handles cycles in the call graph without infinite loop."""
        catalog = load_catalog("python")
        edges = [
            # main → a → b → a (cycle) → os.listdir
            self._make_edge(src="main", dst="a"),
            self._make_edge(src="a", dst="b"),
            self._make_edge(src="b", dst="a"),  # cycle
            self._make_edge(src="a", dst="python:os:0-0:listdir:function"),
        ]
        entrypoint_ids = {"main"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        fs_entry = bmap.entries["fs_read"]
        assert "main" in fs_entry.entry_points

    def test_native_bridge_edge_traced_to_entrypoint(self) -> None:
        """Entry-point trace crosses native_bridge edges (Java→C JNI)."""
        catalog = load_catalog("c")
        edges = [
            # Java side: main → nativeRead (native_bridge) → C_impl → fopen
            self._make_edge(src="java_main", dst="native_method"),
            self._make_edge(src="native_method", dst="c_jni_impl", edge_type="native_bridge"),
            self._make_edge(src="c_jni_impl", dst="c:external:0-0:fopen:unresolved"),
        ]
        entrypoint_ids = {"java_main"}

        bmap = compute_boundary_map(edges, {"c": catalog}, entrypoint_ids=entrypoint_ids)
        assert bmap.total_io_edges >= 1
        fs_entry = bmap.entries.get("fs_read")
        assert fs_entry is not None
        assert "java_main" in fs_entry.entry_points

    def test_unreachable_entry_point_excluded(self) -> None:
        """Entrypoints that can't reach IO are not included."""
        catalog = load_catalog("python")
        edges = [
            self._make_edge(src="main_io", dst="python:os:0-0:listdir:function"),
            self._make_edge(src="main_noio", dst="pure_func"),
        ]
        entrypoint_ids = {"main_io", "main_noio"}

        bmap = compute_boundary_map(edges, {"python": catalog}, entrypoint_ids=entrypoint_ids)
        fs_entry = bmap.entries["fs_read"]
        assert "main_io" in fs_entry.entry_points
        assert "main_noio" not in fs_entry.entry_points
