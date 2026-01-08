"""YAML/Ansible analyzer using tree-sitter.

This analyzer extracts playbooks, tasks, handlers, and variables from
Ansible YAML files. It uses tree-sitter-yaml for parsing when available,
falling back gracefully when the grammar is not installed.

Constructs detected:
- Playbooks (- name: X, hosts: Y)
- Tasks (- name: X, module: params)
- Handlers (handlers: section)
- Variables (vars: section)
- Include/import references (include_tasks, import_tasks, include_role)

Two-pass analysis:
- Pass 1: Extract symbols (playbooks, tasks, handlers, variables)
- Pass 2: Extract reference edges (includes, imports)
"""

from __future__ import annotations

import importlib.util
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "ansible-v1"
PASS_VERSION = "1.0.0"


def is_yaml_tree_sitter_available() -> bool:
    """Check if tree-sitter and yaml grammar are available."""
    ts_spec = importlib.util.find_spec("tree_sitter")
    if ts_spec is None:
        return False
    yaml_spec = importlib.util.find_spec("tree_sitter_yaml")
    return yaml_spec is not None


def find_ansible_files(root: Path) -> list[Path]:
    """Find Ansible YAML files in a directory tree.

    Identifies files by:
    - .yml or .yaml extension
    - Located in roles/, tasks/, handlers/, playbooks/ directories
    - Or any .yml/.yaml file in the root
    """
    ansible_files: list[Path] = []
    yaml_extensions = (".yml", ".yaml")

    # Ansible-specific directories
    ansible_dirs = ("roles", "tasks", "handlers", "playbooks", "vars", "defaults", "group_vars", "host_vars")

    for path in root.rglob("*"):
        if not path.is_file():  # pragma: no cover - directories skipped
            continue

        # Skip common non-ansible directories
        if any(
            part.startswith(".") or part in ("node_modules", "venv", ".venv", "__pycache__")
            for part in path.parts
        ):  # pragma: no cover - test dirs clean
            continue

        if path.suffix in yaml_extensions:
            # Check if in ansible-related directory or root
            is_ansible = (
                any(d in path.parts for d in ansible_dirs)
                or path.parent == root
            )
            if is_ansible:
                ansible_files.append(path)

    return ansible_files


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first direct child with given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_all_children_by_type(
    node: "tree_sitter.Node", type_name: str
) -> list["tree_sitter.Node"]:
    """Find all children (recursive) with given type.

    Uses iterative traversal to avoid RecursionError on deeply nested code.
    """
    result: list["tree_sitter.Node"] = []
    for n in iter_tree(node):
        if n.type == type_name:
            result.append(n)
    return result


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Get text content of a node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_scalar_value(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract scalar value from various YAML scalar types."""
    if node.type in ("plain_scalar", "single_quote_scalar", "double_quote_scalar"):
        for child in node.children:
            if child.type in ("string_scalar", "boolean_scalar", "integer_scalar", "float_scalar"):
                return _node_text(child, source)
        return _node_text(node, source)  # pragma: no cover - fallback
    elif node.type == "flow_node":
        for child in node.children:
            val = _get_scalar_value(child, source)
            if val:
                return val
    return None  # pragma: no cover - defensive fallback


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"ansible:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for an Ansible file node."""
    return f"ansible:{path}:1-1:file:file"


@dataclass
class AnsibleAnalysisResult:
    """Result of analyzing Ansible files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class FileAnalysis:
    """Intermediate analysis result for a single file."""

    symbols: list[Symbol] = field(default_factory=list)


def _extract_mapping_key_value(
    pair_node: "tree_sitter.Node", source: bytes
) -> tuple[str | None, str | None]:
    """Extract key and value from a block_mapping_pair."""
    key: str | None = None
    value: str | None = None

    children = list(pair_node.children)
    for i, child in enumerate(children):
        if child.type == "flow_node" and key is None:
            key = _get_scalar_value(child, source)
        elif child.type == ":" and key is not None:
            # Value comes after the colon
            for j in range(i + 1, len(children)):
                next_child = children[j]
                if next_child.type == "flow_node":
                    value = _get_scalar_value(next_child, source)
                    break
                elif next_child.type == "block_node":  # pragma: no cover - nested value
                    break
            break

    return key, value


def _extract_vars_from_pair(
    pair_node: "tree_sitter.Node",
    source: bytes,
    symbols: list[Symbol],
    rel_path: str,
    run: AnalysisRun,
) -> None:
    """Extract variable definitions from a vars: block_mapping_pair."""
    # Find the block_node value of the vars: key
    for child in pair_node.children:
        if child.type == "block_node":
            # Look for nested block_mapping
            nested_mapping = _find_child_by_type(child, "block_mapping")
            if nested_mapping:
                for nested_pair in nested_mapping.children:
                    if nested_pair.type == "block_mapping_pair":
                        var_key, var_value = _extract_mapping_key_value(nested_pair, source)
                        if var_key:
                            line = nested_pair.start_point[0] + 1
                            symbol_id = _make_symbol_id(rel_path, line, line, var_key, "variable")
                            symbols.append(Symbol(
                                id=symbol_id,
                                name=var_key,
                                kind="variable",
                                language="ansible",
                                path=rel_path,
                                span=Span(line, line, 0, 0),
                                origin=PASS_ID,
                                origin_run_id=run.execution_id,
                            ))


def _extract_symbols_from_file(
    file_path: Path,
    parser: "tree_sitter.Parser",
    run: AnalysisRun,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract symbols and edges from a single Ansible file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    rel_path = str(file_path)
    file_id = _make_file_id(rel_path)

    try:
        source = file_path.read_bytes()
    except (OSError, IOError):  # pragma: no cover
        return symbols, edges

    tree = parser.parse(source)
    root = tree.root_node

    # Track context
    in_tasks = False
    in_handlers = False
    current_play_name: str | None = None

    def process_mapping_pairs(
        pairs: list["tree_sitter.Node"], context: str
    ) -> None:
        nonlocal in_tasks, in_handlers, current_play_name

        for pair in pairs:
            key, value = _extract_mapping_key_value(pair, source)
            if not key:  # pragma: no cover - malformed YAML
                continue

            line = pair.start_point[0] + 1
            end_line = pair.end_point[0] + 1

            # Detect sections and process nested content
            if key == "tasks":
                in_tasks = True
                in_handlers = False
            elif key == "handlers":
                in_handlers = True
                in_tasks = False
            elif key == "vars":
                # Process nested vars block inline
                _extract_vars_from_pair(pair, source, symbols, rel_path, run)

            # Extract playbook name
            if key == "name" and context == "play":
                current_play_name = value
                if value:
                    symbol_id = _make_symbol_id(rel_path, line, end_line, value, "playbook")
                    symbols.append(Symbol(
                        id=symbol_id,
                        name=value,
                        kind="playbook",
                        language="ansible",
                        path=rel_path,
                        span=Span(line, end_line, 0, 0),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

            # Extract task/handler name
            elif key == "name" and (in_tasks or in_handlers):
                kind = "handler" if in_handlers else "task"
                if value:
                    symbol_id = _make_symbol_id(rel_path, line, end_line, value, kind)
                    symbols.append(Symbol(
                        id=symbol_id,
                        name=value,
                        kind=kind,
                        language="ansible",
                        path=rel_path,
                        span=Span(line, end_line, 0, 0),
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

            # Detect include/import patterns
            if key in ("include_tasks", "import_tasks", "include_role", "import_role"):
                if value:
                    edges.append(Edge.create(
                        src=file_id,
                        dst=value,
                        edge_type="imports",
                        line=line,
                        evidence_type=key,
                        confidence=0.95,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                    ))

    # Find all block_sequence_items (plays or tasks)
    seq_items = _find_all_children_by_type(root, "block_sequence_item")

    for seq_item in seq_items:
        # Get mapping pairs from this item
        mapping = _find_child_by_type(seq_item, "block_node")
        if mapping:
            nested_mapping = _find_child_by_type(mapping, "block_mapping")
            if nested_mapping:
                pairs = [c for c in nested_mapping.children if c.type == "block_mapping_pair"]

                # Determine context (play level or task level)
                is_play = any(
                    _extract_mapping_key_value(p, source)[0] == "hosts"
                    for p in pairs
                )
                context = "play" if is_play else "task"
                process_mapping_pairs(pairs, context)

    return symbols, edges


def analyze_ansible(root: Path) -> AnsibleAnalysisResult:
    """Analyze Ansible YAML files in a directory.

    Uses tree-sitter-yaml for parsing. Falls back gracefully if not available.
    """
    if not is_yaml_tree_sitter_available():
        warnings.warn(
            "tree-sitter-yaml not available. Install with: pip install tree-sitter-yaml"
        )
        return AnsibleAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-yaml not available",
        )

    try:
        import tree_sitter
        import tree_sitter_yaml

        language = tree_sitter.Language(tree_sitter_yaml.language())
        parser = tree_sitter.Parser(language)
    except Exception as e:
        return AnsibleAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to load YAML parser: {e}",
        )

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_files = find_ansible_files(root)
    if not all_files:  # pragma: no cover - no Ansible files in test
        return AnsibleAnalysisResult(run=run)

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for ansible_file in all_files:
        symbols, edges = _extract_symbols_from_file(ansible_file, parser, run)
        all_symbols.extend(symbols)
        all_edges.extend(edges)

    return AnsibleAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
