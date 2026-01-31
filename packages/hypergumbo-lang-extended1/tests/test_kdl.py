"""Tests for the KDL configuration analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import kdl as kdl_module
from hypergumbo_lang_extended1.kdl import (
    KdlAnalysisResult,
    analyze_kdl,
    find_kdl_files,
    is_kdl_tree_sitter_available,
)


def make_kdl_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a KDL file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindKdlFiles:
    """Tests for find_kdl_files function."""

    def test_finds_kdl_files(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "node")
        make_kdl_file(tmp_path, "subdir/settings.kdl", "setting")
        files = find_kdl_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"config.kdl", "settings.kdl"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_kdl_files(tmp_path)
        assert files == []


class TestIsKdlTreeSitterAvailable:
    """Tests for is_kdl_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_kdl_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(kdl_module, "is_kdl_tree_sitter_available", return_value=False):
            assert kdl_module.is_kdl_tree_sitter_available() is False


class TestAnalyzeKdl:
    """Tests for analyze_kdl function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "node")
        with patch.object(kdl_module, "is_kdl_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="KDL analysis skipped"):
                result = kdl_module.analyze_kdl(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_kdl(tmp_path)
        assert result.symbols == []
        assert result.run is None

    def test_extracts_simple_node(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", 'name "my-project"')
        result = analyze_kdl(tmp_path)
        assert not result.skipped
        node = next((s for s in result.symbols if s.kind == "node"), None)
        assert node is not None
        assert node.name == "name"
        assert node.meta.get("arg_count") == 1
        assert "my-project" in node.meta.get("arguments", [])

    def test_extracts_node_with_properties(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", 'dependency version="1.0" optional=true')
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "dependency"), None)
        assert node is not None
        assert node.meta.get("prop_count") == 2
        props = node.meta.get("properties", {})
        assert props.get("version") == "1.0"
        assert props.get("optional") == "true"

    def test_extracts_section_with_children(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", """package {
    name "my-package"
    version "1.0.0"
}""")
        result = analyze_kdl(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "package"
        assert section.meta.get("has_children") is True
        # Child nodes should also be extracted
        name_node = next((s for s in result.symbols if s.name == "name"), None)
        assert name_node is not None
        assert name_node.meta.get("depth") == 1

    def test_extracts_nested_sections(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", """root {
    level1 {
        level2 "value"
    }
}""")
        result = analyze_kdl(tmp_path)
        symbols = {s.name: s for s in result.symbols}
        assert symbols["root"].meta.get("depth") == 0
        assert symbols["level1"].meta.get("depth") == 1
        assert symbols["level2"].meta.get("depth") == 2

    def test_extracts_multiple_arguments(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", 'authors "Alice" "Bob" "Charlie"')
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "authors"), None)
        assert node is not None
        assert node.meta.get("arg_count") == 3
        assert "Alice" in node.meta.get("arguments", [])
        assert "Bob" in node.meta.get("arguments", [])
        assert "Charlie" in node.meta.get("arguments", [])

    def test_extracts_numeric_values(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "port 8080")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "port"), None)
        assert node is not None
        assert "8080" in node.meta.get("arguments", [])

    def test_extracts_boolean_values(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "enabled true")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "enabled"), None)
        assert node is not None
        assert "true" in node.meta.get("arguments", [])

    def test_extracts_null_value(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "value null")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "value"), None)
        assert node is not None
        assert "null" in node.meta.get("arguments", [])

    def test_signature_with_arguments(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", 'name "test"')
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "name"), None)
        assert node is not None
        assert '"test"' in node.signature

    def test_signature_with_properties(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "dep version=1")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "dep"), None)
        assert node is not None
        assert "version=1" in node.signature

    def test_signature_with_children(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "section { child }")
        result = analyze_kdl(tmp_path)
        section = next((s for s in result.symbols if s.name == "section"), None)
        assert section is not None
        assert "{ ... }" in section.signature

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "node")
        result = analyze_kdl(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "kdl.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config1.kdl", "node1")
        make_kdl_file(tmp_path, "config2.kdl", "node2")
        result = analyze_kdl(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "node")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.kind == "node"), None)
        assert node is not None
        assert node.origin == "kdl.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "node")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.kind == "node"), None)
        assert node is not None
        assert node.id == node.stable_id
        assert "kdl:" in node.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "node")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.kind == "node"), None)
        assert node is not None
        assert node.span is not None
        assert node.span.start_line >= 1

    def test_edges_empty(self, tmp_path: Path) -> None:
        make_kdl_file(tmp_path, "config.kdl", "node")
        result = analyze_kdl(tmp_path)
        assert result.edges == []

    def test_complete_kdl_document(self, tmp_path: Path) -> None:
        """Test a complete KDL configuration document."""
        make_kdl_file(tmp_path, "config.kdl", """// Package configuration
package {
    name "my-app"
    version "2.0.0"
    authors "Alice" "Bob"
}

dependencies {
    lodash "^4.17.0"
    express version="4.18" optional=true
}

server {
    host "localhost"
    port 3000
    ssl enabled=true
}

features {
    logging true
    caching false
}
""")
        result = analyze_kdl(tmp_path)

        # Check sections
        sections = [s for s in result.symbols if s.kind == "section"]
        section_names = {s.name for s in sections}
        assert section_names == {"package", "dependencies", "server", "features"}

        # Check package section
        package = next((s for s in result.symbols if s.name == "package"), None)
        assert package is not None
        assert package.meta.get("depth") == 0

        # Check nested nodes
        name_node = next((s for s in result.symbols if s.name == "name"), None)
        assert name_node is not None
        assert name_node.meta.get("depth") == 1
        assert "my-app" in name_node.meta.get("arguments", [])

        # Check properties
        express = next((s for s in result.symbols if s.name == "express"), None)
        assert express is not None
        assert express.meta.get("properties", {}).get("version") == "4.18"
        assert express.meta.get("properties", {}).get("optional") == "true"

    def test_many_arguments_truncated_in_meta(self, tmp_path: Path) -> None:
        """Test that many arguments are truncated in meta."""
        make_kdl_file(tmp_path, "config.kdl", 'list "a" "b" "c" "d" "e" "f" "g"')
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "list"), None)
        assert node is not None
        # Should have all 7 arguments counted
        assert node.meta.get("arg_count") == 7
        # But only first 5 stored in meta
        assert len(node.meta.get("arguments", [])) <= 5

    def test_many_properties_truncated_in_meta(self, tmp_path: Path) -> None:
        """Test that many properties are truncated in meta."""
        make_kdl_file(tmp_path, "config.kdl", "item a=1 b=2 c=3 d=4 e=5 f=6")
        result = analyze_kdl(tmp_path)
        node = next((s for s in result.symbols if s.name == "item"), None)
        assert node is not None
        # Should have all 6 properties counted
        assert node.meta.get("prop_count") == 6
        # But only first 5 stored in meta
        assert len(node.meta.get("properties", {})) <= 5
