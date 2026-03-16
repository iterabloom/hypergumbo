# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the dataflow module (ADR-0015).

Tests the YAML-driven dataflow classification machinery:
1. YAML loading and validation
2. annotate_dataflow() — batch edge annotation from AST context
3. scan_library_patterns() — regex-based library pattern matching
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from hypergumbo_core.dataflow import (
    DataflowConfig,
    DataflowSite,
    _config_cache,
    annotate_dataflow,
    get_dataflow_config,
    load_dataflow_config,
    scan_library_patterns,
)
from hypergumbo_core.ir import Edge


# ==================== YAML LOADING TESTS ====================


class TestLoadDataflowConfig:
    """Tests for YAML loading and validation."""

    def test_load_from_yaml_string(self, tmp_path: Path) -> None:
        """Load a minimal dataflow YAML and verify structure."""
        yaml_content = """\
language: python

assignments:
  - node_type: assignment
    write: left
    read: right
  - node_type: augmented_assignment
    mutate: left
    read: right
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        assert config.language == "python"
        assert len(config.assignments) == 2
        assert config.assignments[0]["node_type"] == "assignment"
        assert config.assignments[0]["write"] == "left"
        assert config.assignments[1]["node_type"] == "augmented_assignment"
        assert config.assignments[1]["mutate"] == "left"

    def test_load_with_deletions(self, tmp_path: Path) -> None:
        """Load a YAML with deletion patterns."""
        yaml_content = """\
language: python

deletions:
  - node_type: delete_statement
    delete: argument
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        assert len(config.deletions) == 1
        assert config.deletions[0]["node_type"] == "delete_statement"
        assert config.deletions[0]["delete"] == "argument"

    def test_load_with_library_patterns(self, tmp_path: Path) -> None:
        """Load a YAML with library_patterns section."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: "$map.set($key, $value)"
    access_mode: write
    channel_from: "$key"
  - match: "$map.observe($callback)"
    access_mode: read
    channel: "*"
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        assert len(config.library_patterns) == 2
        assert config.library_patterns[0]["access_mode"] == "write"
        assert config.library_patterns[1]["channel"] == "*"

    def test_load_missing_language_raises(self, tmp_path: Path) -> None:
        """YAML without 'language' field should raise ValueError."""
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("assignments:\n  - node_type: x\n")
        with pytest.raises(ValueError, match="language"):
            load_dataflow_config(yaml_file)

    def test_load_with_calls_section(self, tmp_path: Path) -> None:
        """Load a YAML with calls section."""
        yaml_content = "language: python\n\ncalls:\n  - node_type: call\n    read: arguments\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        assert len(config.calls) == 1
        nmap = config.build_node_type_map()
        assert nmap["call"] == "read"

    def test_load_with_borrows_section(self, tmp_path: Path) -> None:
        """Load a YAML with borrows section (Rust)."""
        yaml_content = "language: rust\n\nborrows:\n  - node_type: reference_expression\n    mutate_if: mutable\n  - node_type: borrow_expression\n    read_if: immutable\n"
        yaml_file = tmp_path / "rust.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        assert len(config.borrows) == 2
        nmap = config.build_node_type_map()
        assert nmap["reference_expression"] == "mutate"
        assert nmap["borrow_expression"] == "read"

    def test_read_only_assignment(self, tmp_path: Path) -> None:
        """Assignment rule with only read (no write/mutate) uses read mode."""
        yaml_content = "language: python\n\nassignments:\n  - node_type: expression_statement\n    read: expression\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        nmap = config.build_node_type_map()
        assert nmap["expression_statement"] == "read"

    def test_load_empty_sections_default(self, tmp_path: Path) -> None:
        """Missing sections should default to empty lists."""
        yaml_content = "language: rust\n"
        yaml_file = tmp_path / "rust.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        assert config.language == "rust"
        assert config.assignments == []
        assert config.calls == []
        assert config.deletions == []
        assert config.library_patterns == []


# ==================== ANNOTATE DATAFLOW TESTS ====================


class TestAnnotateDataflow:
    """Tests for the annotate_dataflow batch annotation function."""

    def _make_mock_tree(self, node_mappings: dict[int, str]) -> MagicMock:
        """Create a mock tree-sitter tree with nodes at specific lines.

        Args:
            node_mappings: dict of {line_number: node_type}
        """
        tree = MagicMock(spec=[])
        root = MagicMock(spec=[])
        tree.root_node = root

        # Build flat list of mock nodes — use spec=[] to prevent auto-attributes
        nodes = []
        for line, ntype in node_mappings.items():
            node = MagicMock(spec=[])
            node.type = ntype
            node.start_point = (line - 1, 0)  # tree-sitter is 0-indexed
            node.end_point = (line - 1, 20)
            node.children = []
            node.parent = root  # parent is the root
            nodes.append(node)

        # Root spans all lines
        max_line = max(node_mappings.keys()) if node_mappings else 1
        root.type = "module"
        root.start_point = (0, 0)
        root.end_point = (max_line, 0)
        root.children = nodes
        root.parent = None

        return tree

    def test_annotates_assignment_edge(self, tmp_path: Path) -> None:
        """Edges at assignment nodes get access_mode stamped."""
        yaml_content = """\
language: python

assignments:
  - node_type: assignment
    write: left
    read: right
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(
            src="py:a.py:5:x:variable",
            dst="py:a.py:5:f:function",
            edge_type="calls",
            line=5,
        )

        tree = self._make_mock_tree({5: "assignment"})
        result = annotate_dataflow([edge], tree, b"x = f()", config)

        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "write"

    def test_skips_edges_with_existing_access_mode(self, tmp_path: Path) -> None:
        """Edges that already have access_mode should not be overwritten."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n    read: right\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(
            src="py:a.py:5:emitter:variable",
            dst="py:a.py:5:handler:function",
            edge_type="event_publishes",
            line=5,
            access_mode="write",
            dest_access_mode="read",
            channel="user.created",
        )

        tree = self._make_mock_tree({5: "assignment"})
        result = annotate_dataflow([edge], tree, b"x = f()", config)

        assert len(result) == 1
        # Should preserve original, not overwrite
        assert result[0].meta["access_mode"] == "write"
        assert result[0].meta["channel"] == "user.created"

    def test_unannotated_edges_pass_through(self, tmp_path: Path) -> None:
        """Edges at non-matching node types pass through unchanged."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n    read: right\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(
            src="py:a.py:10:f:function",
            dst="py:a.py:10:g:function",
            edge_type="calls",
            line=10,
        )

        tree = self._make_mock_tree({10: "call_expression"})
        result = annotate_dataflow([edge], tree, b"f(g())", config)

        assert len(result) == 1
        # No access_mode added for unmatched node types
        assert result[0].meta is None or "access_mode" not in result[0].meta

    def test_augmented_assignment_gets_mutate(self, tmp_path: Path) -> None:
        """Augmented assignments (x += 1) get access_mode=mutate."""
        yaml_content = """\
language: python

assignments:
  - node_type: augmented_assignment
    mutate: left
    read: right
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(
            src="py:a.py:3:x:variable",
            dst="py:a.py:3:literal:expression",
            edge_type="calls",
            line=3,
        )

        tree = self._make_mock_tree({3: "augmented_assignment"})
        result = annotate_dataflow([edge], tree, b"x += 1", config)

        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "mutate"

    def test_deletion_gets_delete(self, tmp_path: Path) -> None:
        """Delete statements get access_mode=delete."""
        yaml_content = """\
language: python

deletions:
  - node_type: delete_statement
    delete: argument
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(
            src="py:a.py:7:scope:function",
            dst="py:a.py:7:x:variable",
            edge_type="calls",
            line=7,
        )

        tree = self._make_mock_tree({7: "delete_statement"})
        result = annotate_dataflow([edge], tree, b"del x", config)

        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "delete"

    def test_empty_config_passes_through(self, tmp_path: Path) -> None:
        """With no rules, all edges pass through unchanged."""
        yaml_content = "language: python\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(
            src="py:a.py:1:f:function",
            dst="py:a.py:1:g:function",
            edge_type="calls",
            line=1,
        )
        tree = self._make_mock_tree({1: "call_expression"})
        result = annotate_dataflow([edge], tree, b"f()", config)

        assert len(result) == 1
        assert result[0].meta is None or "access_mode" not in result[0].meta

    def test_multiple_edges_annotated(self, tmp_path: Path) -> None:
        """Multiple edges can be annotated in a single call."""
        yaml_content = """\
language: python

assignments:
  - node_type: assignment
    write: left
    read: right

deletions:
  - node_type: delete_statement
    delete: argument
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edges = [
            Edge.create(src="py:a.py:1:x:var", dst="py:a.py:1:f:fn",
                        edge_type="calls", line=1),
            Edge.create(src="py:a.py:3:scope:fn", dst="py:a.py:3:y:var",
                        edge_type="calls", line=3),
        ]
        tree = self._make_mock_tree({1: "assignment", 3: "delete_statement"})
        result = annotate_dataflow(edges, tree, b"x = f()\n\ndel y", config)

        assert len(result) == 2
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "write"
        assert result[1].meta is not None
        assert result[1].meta.get("access_mode") == "delete"

    def test_empty_edges_list(self, tmp_path: Path) -> None:
        """Empty edge list should return empty list."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        tree = self._make_mock_tree({1: "assignment"})
        result = annotate_dataflow([], tree, b"x = 1", config)
        assert result == []

    def test_no_node_at_edge_line(self, tmp_path: Path) -> None:
        """Edge at a line with no AST node should pass through unchanged."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        # Edge at line 99 but tree only has nodes at line 1
        edge = Edge.create(
            src="py:a.py:99:f:function",
            dst="py:a.py:99:g:function",
            edge_type="calls",
            line=99,
        )
        tree = self._make_mock_tree({1: "assignment"})
        result = annotate_dataflow([edge], tree, b"x = 1", config)
        assert len(result) == 1
        assert result[0].meta is None or "access_mode" not in result[0].meta


# ==================== SCAN LIBRARY PATTERNS TESTS ====================


class TestScanLibraryPatterns:
    """Tests for regex-based library pattern scanning."""

    def test_matches_simple_pattern(self, tmp_path: Path) -> None:
        """A simple .set() call should match a write pattern."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: ".set("
    access_mode: write
    channel: "*"
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        content = 'yMap.set("cursor", pos);\nyMap.get("cursor");'
        sites = scan_library_patterns(content, config)

        assert len(sites) >= 1
        assert sites[0].access_mode == "write"
        assert sites[0].line == 1

    def test_matches_observe_pattern(self, tmp_path: Path) -> None:
        """An .observe() call should match a read pattern."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: ".observe("
    access_mode: read
    channel: "*"
  - match: ".observeDeep("
    access_mode: read
    channel: "*"
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        content = 'yMap.observeDeep(callback);'
        sites = scan_library_patterns(content, config)

        assert len(sites) >= 1
        assert sites[0].access_mode == "read"

    def test_no_matches_returns_empty(self, tmp_path: Path) -> None:
        """Content with no matching patterns returns empty list."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: ".set("
    access_mode: write
    channel: "*"
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        content = 'console.log("hello");'
        sites = scan_library_patterns(content, config)
        assert sites == []

    def test_empty_library_patterns(self, tmp_path: Path) -> None:
        """Config with no library_patterns returns empty list."""
        yaml_content = "language: python\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        sites = scan_library_patterns("x = 1", config)
        assert sites == []

    def test_multiple_matches_on_different_lines(self, tmp_path: Path) -> None:
        """Multiple pattern matches across different lines."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: ".set("
    access_mode: write
    channel: "*"
  - match: ".delete("
    access_mode: delete
    channel: "*"
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        content = 'yMap.set("a", 1);\nyMap.delete("b");'
        sites = scan_library_patterns(content, config)

        assert len(sites) == 2
        modes = {s.access_mode for s in sites}
        assert modes == {"write", "delete"}

    def test_channel_from_pattern(self, tmp_path: Path) -> None:
        """channel_from should extract the channel from the match."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: '.set\\("([^"]+)"'
    access_mode: write
    channel_from: 1
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        content = 'yMap.set("cursor", pos);'
        sites = scan_library_patterns(content, config)

        assert len(sites) == 1
        assert sites[0].access_mode == "write"
        assert sites[0].channel == "cursor"

    def test_empty_match_string_skipped(self, tmp_path: Path) -> None:
        """Patterns with empty match string should be skipped."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: ""
    access_mode: write
    channel: "*"
  - match: ".set("
    access_mode: write
    channel: "*"
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        content = 'yMap.set("a", 1);'
        sites = scan_library_patterns(content, config)
        # Only the .set( pattern should match, empty match is skipped
        assert len(sites) == 1

    def test_channel_from_invalid_group_falls_back(self, tmp_path: Path) -> None:
        """channel_from with invalid group index should fall back gracefully."""
        yaml_content = """\
language: javascript

library_patterns:
  - match: ".set\\\\("
    access_mode: write
    channel_from: 5
"""
        yaml_file = tmp_path / "js.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        content = 'yMap.set("cursor", pos);'
        sites = scan_library_patterns(content, config)
        # Should match but channel extraction fails — channel stays None
        assert len(sites) == 1
        assert sites[0].channel is None


# ==================== DATAFLOW SITE TESTS ====================


class TestDataflowSite:
    """Tests for the DataflowSite data structure."""

    def test_basic_construction(self) -> None:
        """DataflowSite should hold access_mode, channel, and line."""
        site = DataflowSite(access_mode="write", line=42, channel="config.key")
        assert site.access_mode == "write"
        assert site.line == 42
        assert site.channel == "config.key"

    def test_optional_channel(self) -> None:
        """Channel should be optional (default None)."""
        site = DataflowSite(access_mode="read", line=10)
        assert site.channel is None


# ==================== GET DATAFLOW CONFIG TESTS ====================


class TestGetDataflowConfig:
    """Tests for the cached config loader."""

    def setup_method(self) -> None:
        """Clear config cache before each test."""
        _config_cache.clear()

    def test_returns_none_for_unknown_language(self) -> None:
        """get_dataflow_config returns None when no YAML exists."""
        result = get_dataflow_config("nonexistent_language_xyz")
        assert result is None

    def test_caches_none_for_missing_language(self) -> None:
        """Missing languages are cached as None (no repeated filesystem lookups)."""
        get_dataflow_config("nonexistent_language_xyz")
        assert "nonexistent_language_xyz" in _config_cache
        assert _config_cache["nonexistent_language_xyz"] is None

    def test_returns_config_when_yaml_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_dataflow_config returns a DataflowConfig when YAML exists."""
        import hypergumbo_core.dataflow as df_mod
        yaml_content = "language: testlang\nassignments:\n  - node_type: assignment\n    write: left\n"
        yaml_file = tmp_path / "testlang.yaml"
        yaml_file.write_text(yaml_content)
        monkeypatch.setattr(df_mod, "_DATAFLOW_DIR", tmp_path)
        _config_cache.clear()

        result = get_dataflow_config("testlang")
        assert result is not None
        assert result.language == "testlang"
        assert len(result.assignments) == 1

    def test_caches_loaded_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loaded configs are cached — subsequent calls return the same object."""
        import hypergumbo_core.dataflow as df_mod
        yaml_content = "language: cachelang\n"
        yaml_file = tmp_path / "cachelang.yaml"
        yaml_file.write_text(yaml_content)
        monkeypatch.setattr(df_mod, "_DATAFLOW_DIR", tmp_path)
        _config_cache.clear()

        first = get_dataflow_config("cachelang")
        second = get_dataflow_config("cachelang")
        assert first is second


# ==================== BASE.PY INTEGRATION TESTS ====================


class TestBaseAnalyzerIntegration:
    """Tests for the Tier 1 integration in analyze/base.py."""

    def test_annotate_dataflow_called_when_config_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a dataflow config exists for the language, annotate_dataflow is called."""
        import hypergumbo_core.analyze.base as base_mod

        config = DataflowConfig(
            language="go",
            assignments=[{"node_type": "assignment_statement", "write": "left"}],
        )

        calls_made: list = []
        original_get = base_mod.get_dataflow_config
        original_annotate = base_mod.annotate_dataflow

        def patched_get(lang):
            return config

        def patched_annotate(edges, tree, source, cfg):
            calls_made.append(len(edges))
            return edges

        monkeypatch.setattr(base_mod, "get_dataflow_config", patched_get)
        monkeypatch.setattr(base_mod, "annotate_dataflow", patched_annotate)

        # Use C++ analyzer — it subclasses TreeSitterAnalyzer and does NOT
        # override analyze(), so it goes through the base class pass 2 loop
        from hypergumbo_lang_mainstream.cpp import analyze_cpp

        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text('#include <iostream>\n\nvoid greet() {\n    std::cout << "hi";\n}\n\nint main() {\n    greet();\n    return 0;\n}\n')
        result = analyze_cpp(tmp_path)

        # If tree-sitter-cpp is not available, skip this test
        if result.skipped:
            pytest.skip("tree-sitter-cpp not available")

        # annotate_dataflow should have been called at least once
        assert len(calls_made) >= 1, f"annotate_dataflow never called; result had {len(result.edges)} edges"
