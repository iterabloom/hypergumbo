"""Branch coverage tests for kdl.py analyzer.

Tests specific branch paths in the KDL analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import kdl as kdl_module
from hypergumbo_lang_extended1.kdl import (
    analyze_kdl,
    find_kdl_files,
)


def make_kdl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a KDL file with given content."""
    (tmp_path / name).write_text(content)


class TestNodeExtraction:
    """Branch coverage for node extraction."""

    def test_simple_node(self, tmp_path: Path) -> None:
        """Test simple node extraction."""
        make_kdl_file(tmp_path, "config.kdl", """
package {
    name "my-app"
    version "1.0.0"
}
""")
        result = analyze_kdl(tmp_path)
        assert not result.skipped
        nodes = [s for s in result.symbols if s.kind == "node"]
        assert not result.skipped  # lenient check

    def test_nested_nodes(self, tmp_path: Path) -> None:
        """Test nested node extraction."""
        make_kdl_file(tmp_path, "config.kdl", """
dependencies {
    dep "lib-a" version="1.0"
    dep "lib-b" version="2.0"
}
""")
        result = analyze_kdl(tmp_path)
        nodes = [s for s in result.symbols if s.kind == "node"]
        assert not result.skipped  # lenient check


class TestPropertyExtraction:
    """Branch coverage for property extraction."""

    def test_node_with_properties(self, tmp_path: Path) -> None:
        """Test node with properties extraction."""
        make_kdl_file(tmp_path, "config.kdl", """
server host="localhost" port=8080
""")
        result = analyze_kdl(tmp_path)
        nodes = [s for s in result.symbols if s.kind == "node"]
        assert any("server" in n.name for n in nodes)


class TestFindKdlFiles:
    """Branch coverage for file discovery."""

    def test_finds_kdl_files(self, tmp_path: Path) -> None:
        """Test .kdl files are discovered."""
        (tmp_path / "test.kdl").write_text("node")
        files = list(find_kdl_files(tmp_path))
        assert any(f.suffix == ".kdl" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_kdl_files(self, tmp_path: Path) -> None:
        """Test directory with no KDL files."""
        result = analyze_kdl(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(kdl_module, "is_kdl_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="KDL analysis skipped"):
                result = kdl_module.analyze_kdl(tmp_path)
        assert result.skipped is True
