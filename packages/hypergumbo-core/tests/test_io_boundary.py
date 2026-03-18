# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for I/O boundary analysis (ADR-0016).

Covers the Python I/O primitive catalog loading, edge matching,
and boundary map generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    IoBoundaryCatalog,
    IoPrimitive,
    load_catalog,
    match_edge_to_primitive,
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
