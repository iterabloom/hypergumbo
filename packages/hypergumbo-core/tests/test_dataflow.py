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
    annotate_dataflow_ast,
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

    def test_load_with_returns_section(self, tmp_path: Path) -> None:
        """Load a YAML with returns section."""
        yaml_content = "language: python\n\nreturns:\n  - node_type: return_statement\n    read: expression\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        assert len(config.returns) == 1
        assert config.returns[0]["node_type"] == "return_statement"
        nmap = config.build_node_type_map()
        assert nmap["return_statement"] == "read"

    def test_positional_map_assignments(self, tmp_path: Path) -> None:
        """build_positional_map preserves child-field → access_mode mapping."""
        yaml_content = """\
language: python

assignments:
  - node_type: assignment
    write: left
    read: right
  - node_type: augmented_assignment
    mutate: left
    read: right

deletions:
  - node_type: delete_statement
    delete: argument

returns:
  - node_type: return_statement
    read: expression
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        pmap = config.build_positional_map()

        assert pmap["assignment"] == {"left": "write", "right": "read"}
        assert pmap["augmented_assignment"] == {"left": "mutate", "right": "read"}
        assert pmap["delete_statement"] == {"_default": "delete"}
        assert pmap["return_statement"] == {"_default": "read"}

    def test_positional_map_empty_node_type_skipped(self, tmp_path: Path) -> None:
        """Assignment rule with empty node_type is skipped in positional map."""
        yaml_content = "language: python\nassignments:\n  - node_type: ''\n    write: left\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        pmap = config.build_positional_map()
        assert pmap == {}

    def test_positional_map_calls_section(self, tmp_path: Path) -> None:
        """Calls section produces _default read in positional map."""
        yaml_content = "language: python\ncalls:\n  - node_type: call\n    read: arguments\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        pmap = config.build_positional_map()
        assert pmap["call"] == {"_default": "read"}

    def test_positional_map_borrows_section(self, tmp_path: Path) -> None:
        """Borrows section produces _default mutate/read in positional map."""
        yaml_content = "language: rust\nborrows:\n  - node_type: reference_expression\n    mutate_if: mutable\n  - node_type: borrow_expression\n    read_if: immutable\n"
        yaml_file = tmp_path / "rust.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        pmap = config.build_positional_map()
        assert pmap["reference_expression"] == {"_default": "mutate"}
        assert pmap["borrow_expression"] == {"_default": "read"}

    def test_positional_map_borrows_empty_node_type(self, tmp_path: Path) -> None:
        """Borrows rule with empty node_type is skipped."""
        yaml_content = "language: rust\nborrows:\n  - node_type: ''\n    mutate_if: mutable\n"
        yaml_file = tmp_path / "rust.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        pmap = config.build_positional_map()
        assert pmap == {}

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
        assert config.returns == []
        assert config.library_patterns == []


# ==================== ANNOTATE DATAFLOW TESTS ====================


class TestAnnotateDataflow:
    """Tests for the annotate_dataflow batch annotation function."""

    def _make_mock_tree(self, node_mappings: dict[int, str]) -> MagicMock:
        """Create a mock tree-sitter tree with simple (non-positional) nodes.

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

    def _make_positional_tree(
        self,
        line: int,
        parent_type: str,
        children: dict[str, tuple[str, int, int]],
    ) -> MagicMock:
        """Create a mock tree with a parent node that has named children.

        Supports position-aware classification: the parent node has
        ``child_by_field_name()`` and children have byte ranges so that
        ``_classify_by_position()`` can resolve which child field an
        edge's node falls in.

        Args:
            line: 1-indexed line number for the parent node.
            parent_type: node type of the parent (e.g., ``"assignment"``).
            children: ``{field_name: (node_type, start_col, end_col)}``.
                The LAST child in iteration order becomes the deepest
                node in the line index (matching tree-sitter DFS behavior).
        """
        tree = MagicMock(spec=[])
        root = MagicMock(spec=[])
        tree.root_node = root

        line0 = line - 1  # 0-indexed
        base_byte = line0 * 100

        parent = MagicMock(spec=[])
        parent.type = parent_type
        parent.start_point = (line0, 0)
        parent.end_point = (line0, 20)
        parent.start_byte = base_byte
        parent.end_byte = base_byte + 20
        parent.parent = root

        child_by_name: dict[str, MagicMock] = {}
        child_list = []
        for field_name, (ntype, start_col, end_col) in children.items():
            child = MagicMock(spec=[])
            child.type = ntype
            child.start_point = (line0, start_col)
            child.end_point = (line0, end_col)
            child.start_byte = base_byte + start_col
            child.end_byte = base_byte + end_col
            child.children = []
            child.parent = parent
            child_by_name[field_name] = child
            child_list.append(child)

        parent.children = child_list
        parent.child_by_field_name = lambda name: child_by_name.get(name)

        root.type = "module"
        root.start_point = (0, 0)
        root.end_point = (line0 + 1, 0)
        root.start_byte = 0
        root.end_byte = (line0 + 1) * 100
        root.children = [parent]
        root.parent = None

        return tree

    def test_assignment_rhs_gets_read(self, tmp_path: Path) -> None:
        """Edge on RHS of assignment (e.g., the call in x = f()) gets read."""
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

        # RHS call_expression at cols 4-7 falls inside "right" child
        tree = self._make_positional_tree(
            line=5,
            parent_type="assignment",
            children={
                "left": ("identifier", 0, 1),
                "right": ("call_expression", 4, 7),
            },
        )
        result = annotate_dataflow([edge], tree, b"x = f()", config)

        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "read"

    def test_assignment_lhs_gets_write(self, tmp_path: Path) -> None:
        """Edge on LHS of assignment gets write."""
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
            src="py:a.py:5:scope:function",
            dst="py:a.py:5:x:variable",
            edge_type="assigns",
            line=5,
        )

        # LHS identifier at cols 0-1; make it the deepest node by listing it last
        tree = self._make_positional_tree(
            line=5,
            parent_type="assignment",
            children={
                "right": ("call_expression", 4, 7),
                "left": ("identifier", 0, 1),
            },
        )
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

    def test_augmented_assignment_lhs_gets_mutate(self, tmp_path: Path) -> None:
        """LHS of augmented assignment (x += 1) gets access_mode=mutate."""
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
            src="py:a.py:3:scope:function",
            dst="py:a.py:3:x:variable",
            edge_type="assigns",
            line=3,
        )

        # LHS identifier is the deepest node (listed last)
        tree = self._make_positional_tree(
            line=3,
            parent_type="augmented_assignment",
            children={
                "right": ("integer", 5, 6),
                "left": ("identifier", 0, 1),
            },
        )
        result = annotate_dataflow([edge], tree, b"x += 1", config)

        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "mutate"

    def test_augmented_assignment_rhs_gets_read(self, tmp_path: Path) -> None:
        """RHS of augmented assignment (x += f()) gets access_mode=read."""
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
            src="py:a.py:3:scope:function",
            dst="py:a.py:3:f:function",
            edge_type="calls",
            line=3,
        )

        # RHS call is the deepest node (listed last)
        tree = self._make_positional_tree(
            line=3,
            parent_type="augmented_assignment",
            children={
                "left": ("identifier", 0, 1),
                "right": ("call_expression", 5, 8),
            },
        )
        result = annotate_dataflow([edge], tree, b"x += f()", config)

        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "read"

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

deletions:
  - node_type: delete_statement
    delete: argument

returns:
  - node_type: return_statement
    read: expression
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edges = [
            Edge.create(src="py:a.py:1:scope:fn", dst="py:a.py:1:f:fn",
                        edge_type="calls", line=1),
            Edge.create(src="py:a.py:3:scope:fn", dst="py:a.py:3:y:var",
                        edge_type="calls", line=3),
        ]
        tree = self._make_mock_tree({1: "return_statement", 3: "delete_statement"})
        result = annotate_dataflow(edges, tree, b"return f()\n\ndel y", config)

        assert len(result) == 2
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "read"
        assert result[1].meta is not None
        assert result[1].meta.get("access_mode") == "delete"

    def test_return_statement_gets_read(self, tmp_path: Path) -> None:
        """Edge at return statement should get access_mode=read via returns section."""
        yaml_content = """\
language: python

returns:
  - node_type: return_statement
    read: expression
"""
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(
            src="py:a.py:5:scope:function",
            dst="py:a.py:5:f:function",
            edge_type="calls",
            line=5,
        )

        tree = self._make_mock_tree({5: "return_statement"})
        result = annotate_dataflow([edge], tree, b"return f()", config)

        assert len(result) == 1
        assert result[0].meta is not None
        assert result[0].meta.get("access_mode") == "read"

    def test_no_annotation_when_position_unresolvable(self, tmp_path: Path) -> None:
        """Flat mock without child_by_field_name leaves edge unannotated."""
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
            src="py:a.py:5:x:var",
            dst="py:a.py:5:f:fn",
            edge_type="calls",
            line=5,
        )

        # Flat mock without child_by_field_name — position can't be resolved
        tree = self._make_mock_tree({5: "assignment"})
        result = annotate_dataflow([edge], tree, b"x = f()", config)

        assert len(result) == 1
        # No annotation because position couldn't be determined
        assert result[0].meta is None or "access_mode" not in result[0].meta

    def test_empty_edges_list(self, tmp_path: Path) -> None:
        """Empty edge list should return empty list."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)
        tree = self._make_mock_tree({1: "assignment"})
        result = annotate_dataflow([], tree, b"x = 1", config)
        assert result == []

    def test_position_unresolvable_missing_bytes(self, tmp_path: Path) -> None:
        """Edge node without start_byte/end_byte leaves edge unannotated."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n    read: right\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(src="py:a.py:5:x:var", dst="py:a.py:5:f:fn",
                           edge_type="calls", line=5)

        # Create tree with child_by_field_name but no start_byte on nodes
        tree = MagicMock(spec=[])
        root = MagicMock(spec=[])
        tree.root_node = root

        parent = MagicMock(spec=[])
        parent.type = "assignment"
        parent.start_point = (4, 0)
        parent.end_point = (4, 10)
        parent.children = []
        parent.parent = root
        parent.child_by_field_name = lambda name: None
        # Deepest node has no start_byte
        child = MagicMock(spec=[])
        child.type = "call_expression"
        child.start_point = (4, 4)
        child.end_point = (4, 7)
        child.children = []
        child.parent = parent
        parent.children = [child]

        root.type = "module"
        root.start_point = (0, 0)
        root.end_point = (5, 0)
        root.children = [parent]
        root.parent = None

        result = annotate_dataflow([edge], tree, b"x = f()", config)
        assert result[0].meta is None or "access_mode" not in result[0].meta

    def test_position_child_missing_bytes(self, tmp_path: Path) -> None:
        """When child field node lacks byte info, that field is skipped."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n    read: right\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(src="py:a.py:5:x:var", dst="py:a.py:5:f:fn",
                           edge_type="calls", line=5)

        # Tree with child_by_field_name returning child without start_byte
        tree = MagicMock(spec=[])
        root = MagicMock(spec=[])
        tree.root_node = root

        parent = MagicMock(spec=[])
        parent.type = "assignment"
        parent.start_point = (4, 0)
        parent.end_point = (4, 10)
        parent.start_byte = 400
        parent.end_byte = 410
        parent.parent = root

        # Child node without start_byte
        bad_child = MagicMock(spec=[])
        bad_child.type = "identifier"
        bad_child.start_point = (4, 0)
        bad_child.end_point = (4, 1)
        bad_child.children = []
        bad_child.parent = parent

        # Deepest node with byte info
        deep = MagicMock(spec=[])
        deep.type = "call_expression"
        deep.start_point = (4, 4)
        deep.end_point = (4, 7)
        deep.start_byte = 404
        deep.end_byte = 407
        deep.children = []
        deep.parent = parent

        parent.children = [bad_child, deep]
        parent.child_by_field_name = lambda name: {"left": bad_child, "right": None}.get(name)

        root.type = "module"
        root.start_point = (0, 0)
        root.end_point = (5, 0)
        root.start_byte = 0
        root.end_byte = 500
        root.children = [parent]
        root.parent = None

        result = annotate_dataflow([edge], tree, b"x = f()", config)
        # left child has no start_byte, right child is None → no match → no annotation
        assert result[0].meta is None or "access_mode" not in result[0].meta

    def test_position_no_field_match(self, tmp_path: Path) -> None:
        """When edge node is outside all child byte ranges, no annotation."""
        yaml_content = "language: python\nassignments:\n  - node_type: assignment\n    write: left\n    read: right\n"
        yaml_file = tmp_path / "python.yaml"
        yaml_file.write_text(yaml_content)
        config = load_dataflow_config(yaml_file)

        edge = Edge.create(src="py:a.py:5:x:var", dst="py:a.py:5:f:fn",
                           edge_type="calls", line=5)

        # Tree where deepest node is outside both children's byte ranges
        tree = MagicMock(spec=[])
        root = MagicMock(spec=[])
        tree.root_node = root

        parent = MagicMock(spec=[])
        parent.type = "assignment"
        parent.start_point = (4, 0)
        parent.end_point = (4, 20)
        parent.start_byte = 400
        parent.end_byte = 420
        parent.parent = root

        left = MagicMock(spec=[])
        left.type = "identifier"
        left.start_point = (4, 0)
        left.end_point = (4, 1)
        left.start_byte = 400
        left.end_byte = 401
        left.children = []
        left.parent = parent

        right = MagicMock(spec=[])
        right.type = "call_expression"
        right.start_point = (4, 4)
        right.end_point = (4, 7)
        right.start_byte = 404
        right.end_byte = 407
        right.children = []
        right.parent = parent

        # Deepest node at bytes 410-415, outside both left [400-401] and right [404-407]
        deep = MagicMock(spec=[])
        deep.type = "comment"
        deep.start_point = (4, 10)
        deep.end_point = (4, 15)
        deep.start_byte = 410
        deep.end_byte = 415
        deep.children = []
        deep.parent = parent

        parent.children = [left, right, deep]
        parent.child_by_field_name = lambda name: {"left": left, "right": right}.get(name)

        root.type = "module"
        root.start_point = (0, 0)
        root.end_point = (5, 0)
        root.start_byte = 0
        root.end_byte = 500
        root.children = [parent]
        root.parent = None

        result = annotate_dataflow([edge], tree, b"x = f() # comment", config)
        assert result[0].meta is None or "access_mode" not in result[0].meta

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


# ==================== ERLANG LIBRARY PATTERNS TESTS ====================


class TestErlangLibraryPatterns:
    """Tests for Erlang name-based dataflow heuristics."""

    def test_erlang_config_loads(self) -> None:
        """Erlang dataflow config loads with library_patterns."""
        from pathlib import Path
        erlang_yaml = (
            Path(__file__).parent.parent
            / "src" / "hypergumbo_core" / "dataflow_patterns" / "erlang.yaml"
        )
        config = load_dataflow_config(erlang_yaml)
        assert config.language == "erlang"
        assert len(config.library_patterns) > 0

    def test_read_patterns(self) -> None:
        """Erlang read heuristics match get_/fetch_/read_ functions."""
        from pathlib import Path
        erlang_yaml = (
            Path(__file__).parent.parent
            / "src" / "hypergumbo_core" / "dataflow_patterns" / "erlang.yaml"
        )
        config = load_dataflow_config(erlang_yaml)

        content = (
            "get_user(UserId) ->\n"
            "    fetch_data(UserId),\n"
            "    read_config(app).\n"
        )
        sites = scan_library_patterns(content, config)
        read_sites = [s for s in sites if s.access_mode == "read"]
        assert len(read_sites) == 3

    def test_write_patterns(self) -> None:
        """Erlang write heuristics match set_/put_/write_/store_ functions."""
        from pathlib import Path
        erlang_yaml = (
            Path(__file__).parent.parent
            / "src" / "hypergumbo_core" / "dataflow_patterns" / "erlang.yaml"
        )
        config = load_dataflow_config(erlang_yaml)

        content = (
            "set_config(Key, Value) ->\n"
            "    put_state(Key, Value),\n"
            "    store_data(Value).\n"
        )
        sites = scan_library_patterns(content, config)
        write_sites = [s for s in sites if s.access_mode == "write"]
        assert len(write_sites) == 3

    def test_ets_patterns(self) -> None:
        """ETS-specific patterns match correctly."""
        from pathlib import Path
        erlang_yaml = (
            Path(__file__).parent.parent
            / "src" / "hypergumbo_core" / "dataflow_patterns" / "erlang.yaml"
        )
        config = load_dataflow_config(erlang_yaml)

        content = (
            "handle_call(get, _From, State) ->\n"
            "    Result = ets:lookup(my_table, key),\n"
            "    ets:insert(my_table, {key, value}),\n"
            "    ets:delete(my_table, old_key).\n"
        )
        sites = scan_library_patterns(content, config)
        assert any(s.access_mode == "read" for s in sites)
        assert any(s.access_mode == "write" for s in sites)
        assert any(s.access_mode == "delete" for s in sites)

    def test_otp_patterns(self) -> None:
        """OTP gen_server patterns match correctly."""
        from pathlib import Path
        erlang_yaml = (
            Path(__file__).parent.parent
            / "src" / "hypergumbo_core" / "dataflow_patterns" / "erlang.yaml"
        )
        config = load_dataflow_config(erlang_yaml)

        content = (
            "start() ->\n"
            "    gen_server:call(Pid, get_state),\n"
            "    gen_server:cast(Pid, {set_state, Value}).\n"
        )
        sites = scan_library_patterns(content, config)
        read_sites = [s for s in sites if s.access_mode == "read"]
        write_sites = [s for s in sites if s.access_mode == "write"]
        assert len(read_sites) >= 1  # gen_server:call
        assert len(write_sites) >= 1  # gen_server:cast


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


# ==================== BUILT-IN YAML TESTS ====================


_EXPECTED_LANGUAGES = ["python", "javascript", "typescript", "rust", "go", "java", "ruby", "csharp", "kotlin", "erlang", "haskell"]


class TestBuiltInYamlFiles:
    """Tests for the shipped dataflow YAML pattern files."""

    @pytest.mark.parametrize("language", _EXPECTED_LANGUAGES)
    def test_yaml_loads_successfully(self, language: str) -> None:
        """Each built-in YAML should load without errors."""
        from hypergumbo_core.dataflow import _DATAFLOW_DIR
        yaml_path = _DATAFLOW_DIR / f"{language}.yaml"
        assert yaml_path.is_file(), f"Missing: {yaml_path}"
        config = load_dataflow_config(yaml_path)
        assert config.language == language

    @pytest.mark.parametrize("language", _EXPECTED_LANGUAGES)
    def test_yaml_has_assignments(self, language: str) -> None:
        """Each built-in YAML should have at least one assignment rule."""
        from hypergumbo_core.dataflow import _DATAFLOW_DIR
        config = load_dataflow_config(_DATAFLOW_DIR / f"{language}.yaml")
        assert len(config.assignments) >= 1, f"{language} has no assignment rules"

    @pytest.mark.parametrize("language", _EXPECTED_LANGUAGES)
    def test_yaml_builds_node_type_map(self, language: str) -> None:
        """Each built-in YAML should produce a non-empty node type map."""
        from hypergumbo_core.dataflow import _DATAFLOW_DIR
        config = load_dataflow_config(_DATAFLOW_DIR / f"{language}.yaml")
        nmap = config.build_node_type_map()
        assert len(nmap) >= 1, f"{language} produces empty node type map"
        # All values should be valid access modes
        from hypergumbo_core.ir import VALID_ACCESS_MODES
        for node_type, mode in nmap.items():
            assert mode in VALID_ACCESS_MODES, f"{language}: {node_type} -> {mode} not valid"

    def test_python_has_delete(self) -> None:
        """Python YAML should include delete_statement."""
        from hypergumbo_core.dataflow import _DATAFLOW_DIR
        config = load_dataflow_config(_DATAFLOW_DIR / "python.yaml")
        assert len(config.deletions) >= 1
        nmap = config.build_node_type_map()
        assert "delete_statement" in nmap
        assert nmap["delete_statement"] == "delete"

    def test_javascript_has_delete(self) -> None:
        """JavaScript YAML should include delete_expression."""
        from hypergumbo_core.dataflow import _DATAFLOW_DIR
        config = load_dataflow_config(_DATAFLOW_DIR / "javascript.yaml")
        nmap = config.build_node_type_map()
        assert "delete_expression" in nmap
        assert nmap["delete_expression"] == "delete"

    def test_rust_has_borrows(self) -> None:
        """Rust YAML should include borrow patterns."""
        from hypergumbo_core.dataflow import _DATAFLOW_DIR
        config = load_dataflow_config(_DATAFLOW_DIR / "rust.yaml")
        assert len(config.borrows) >= 1
        nmap = config.build_node_type_map()
        assert "reference_expression" in nmap

    def test_go_has_short_var_declaration(self) -> None:
        """Go YAML should include short_var_declaration (:= operator)."""
        from hypergumbo_core.dataflow import _DATAFLOW_DIR
        config = load_dataflow_config(_DATAFLOW_DIR / "go.yaml")
        nmap = config.build_node_type_map()
        assert "short_var_declaration" in nmap
        assert nmap["short_var_declaration"] == "write"

    @pytest.mark.parametrize("language", _EXPECTED_LANGUAGES)
    def test_get_dataflow_config_finds_builtin(self, language: str) -> None:
        """get_dataflow_config should find and return built-in configs."""
        _config_cache.clear()
        config = get_dataflow_config(language)
        assert config is not None, f"get_dataflow_config({language!r}) returned None"
        assert config.language == language


# ==================== PYTHON AST DATAFLOW TESTS ====================


class TestAnnotateDataflowAst:
    """Tests for the Python ast-based dataflow annotation."""

    def test_call_on_assignment_gets_read(self) -> None:
        """Call edge on assignment line gets read (RHS consumes value)."""
        import ast
        tree = ast.parse("x = f()")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:f:function",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "read"

    def test_non_call_on_assignment_gets_write(self) -> None:
        """Non-call edge on assignment line keeps write (target binding)."""
        import ast
        tree = ast.parse("x = f()")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:x:variable",
            edge_type="assigns",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "write"

    def test_call_on_augmented_assignment_gets_read(self) -> None:
        """Call edge on augmented assignment line gets read."""
        import ast
        tree = ast.parse("x += f()")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:f:function",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "read"

    def test_non_call_on_augmented_assignment_gets_mutate(self) -> None:
        """Non-call edge on augmented assignment line keeps mutate."""
        import ast
        tree = ast.parse("x += 1")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:x:variable",
            edge_type="assigns",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "mutate"

    def test_call_on_annotated_assignment_gets_read(self) -> None:
        """Call edge on annotated assignment gets read."""
        import ast
        tree = ast.parse("x: int = f()")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:f:function",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "read"

    def test_return_gets_read(self) -> None:
        """ast.Return node should produce access_mode=read."""
        import ast
        tree = ast.parse("def f():\n    return g()")
        edge = Edge.create(
            src="py:a.py:2:f:function",
            dst="py:a.py:2:g:function",
            edge_type="calls",
            line=2,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "read"

    def test_delete_gets_delete(self) -> None:
        """ast.Delete node should produce access_mode=delete."""
        import ast
        tree = ast.parse("del x")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:x:variable",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "delete"

    def test_skips_existing_access_mode(self) -> None:
        """Edges with existing access_mode should not be overwritten."""
        import ast
        tree = ast.parse("x = f()")
        edge = Edge.create(
            src="py:a.py:1:emitter:variable",
            dst="py:a.py:1:handler:function",
            edge_type="event_publishes",
            line=1,
            access_mode="write",
            dest_access_mode="read",
        )
        result = annotate_dataflow_ast([edge], tree)
        # Should preserve original
        assert result[0].meta["access_mode"] == "write"
        assert result[0].meta["dest_access_mode"] == "read"

    def test_empty_edges_returns_empty(self) -> None:
        """Empty edge list should return empty."""
        import ast
        tree = ast.parse("x = 1")
        assert annotate_dataflow_ast([], tree) == []

    def test_none_tree_returns_edges(self) -> None:
        """None tree should return edges unchanged."""
        edge = Edge.create(
            src="py:a.py:1:f:function",
            dst="py:a.py:1:g:function",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], None)
        assert len(result) == 1
        assert result[0].meta is None

    def test_no_matching_lines(self) -> None:
        """Edges at lines without assignments pass through unchanged."""
        import ast
        tree = ast.parse("f()\ng()")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:f:function",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is None or "access_mode" not in result[0].meta

    def test_call_on_for_loop_gets_read(self) -> None:
        """Call edge on for-loop line gets read (iterating over call result)."""
        import ast
        tree = ast.parse("for item in get_items():\n    pass")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:get_items:function",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "read"

    def test_non_call_on_for_loop_gets_write(self) -> None:
        """Non-call edge on for-loop line keeps write (iteration variable)."""
        import ast
        tree = ast.parse("for item in collection:\n    pass")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:collection:variable",
            edge_type="references",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "write"

    def test_call_on_with_statement_gets_read(self) -> None:
        """Call edge on with-statement line gets read (context manager call)."""
        import ast
        tree = ast.parse("with open('f') as fp:\n    pass")
        edge = Edge.create(
            src="py:a.py:1:scope:function",
            dst="py:a.py:1:open:function",
            edge_type="calls",
            line=1,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "read"

    def test_yield_gets_read(self) -> None:
        """ast.Yield node should produce access_mode=read."""
        import ast
        tree = ast.parse("def gen():\n    yield f()")
        edge = Edge.create(
            src="py:a.py:2:gen:function",
            dst="py:a.py:2:f:function",
            edge_type="calls",
            line=2,
        )
        result = annotate_dataflow_ast([edge], tree)
        assert result[0].meta is not None
        assert result[0].meta["access_mode"] == "read"
