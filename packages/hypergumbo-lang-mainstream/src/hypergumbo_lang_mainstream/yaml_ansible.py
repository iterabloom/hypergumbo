# SPDX-License-Identifier: AGPL-3.0-or-later
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

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Optional

from hypergumbo_core.discovery import is_excluded
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("yaml_ansible")


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

        # Skip excluded directories (node_modules, .venv, __pycache__, etc.)
        if is_excluded(path, root):  # pragma: no cover - test dirs clean
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


def _get_scalar_value(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract scalar value from various YAML scalar types."""
    if node.type in ("plain_scalar", "single_quote_scalar", "double_quote_scalar"):
        for child in node.children:
            if child.type in ("string_scalar", "boolean_scalar", "integer_scalar", "float_scalar"):
                return node_text(child, source)
        return node_text(node, source)  # pragma: no cover - fallback
    elif node.type == "flow_node":
        for child in node.children:
            val = _get_scalar_value(child, source)
            if val:
                return val
    return None  # pragma: no cover - defensive fallback


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
            nested_mapping = find_child_by_type(child, "block_mapping")
            if nested_mapping:
                for nested_pair in nested_mapping.children:
                    if nested_pair.type == "block_mapping_pair":
                        var_key, var_value = _extract_mapping_key_value(nested_pair, source)
                        if var_key:
                            line = nested_pair.start_point[0] + 1
                            symbol_id = make_symbol_id("ansible", rel_path, line, line, var_key, "variable")
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
    file_id = make_file_id("ansible", rel_path)

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
                    symbol_id = make_symbol_id("ansible", rel_path, line, end_line, value, "playbook")
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
                    symbol_id = make_symbol_id("ansible", rel_path, line, end_line, value, kind)
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
        mapping = find_child_by_type(seq_item, "block_node")
        if mapping:
            nested_mapping = find_child_by_type(mapping, "block_mapping")
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


def _resolve_ansible_path(
    raw_dst: str,
    src_file_id: str,
    file_basename_map: dict[str, list[str]],
    file_node_ids: dict[str, str],
) -> str | None:
    """Resolve an Ansible include/import path to a file node ID.

    Tries several strategies:
    1. Exact basename match (e.g., "common.yml" → tasks/common.yml)
    2. Relative path from the source file's directory
    3. Role name resolution ("name=rolename" → roles/rolename/tasks/main.yml)

    Returns the file node ID if resolved, None otherwise. Jinja2 template
    expressions (containing "{{") are always unresolvable.
    """
    # Jinja2 templates are unresolvable at parse time
    if "{{" in raw_dst:
        return None

    # Handle role references: "name=rolename" → roles/rolename/tasks/main.yml
    if raw_dst.startswith("name="):
        role_name = raw_dst[5:]
        for rel_path, node_id in file_node_ids.items():
            if f"roles/{role_name}/tasks/main.yml" in rel_path:
                return node_id
        return None

    # Strip quotes that may have leaked through
    cleaned = raw_dst.strip("'\"")

    # Try exact basename match
    basename = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned
    if basename in file_basename_map:
        candidates = file_basename_map[basename]
        if len(candidates) == 1:
            return file_node_ids[candidates[0]]
        # Multiple candidates: try to pick the one in the same directory
        # Extract source directory from src_file_id
        # src_file_id format: ansible:{path}:1-1:file:file
        parts = src_file_id.split(":")
        if len(parts) >= 2:
            src_path = parts[1]
            src_dir = src_path.rsplit("/", 1)[0] if "/" in src_path else ""
            for cand in candidates:
                cand_dir = cand.rsplit("/", 1)[0] if "/" in cand else ""
                if cand_dir == src_dir:
                    return file_node_ids[cand]
        # Fall back to first candidate
        return file_node_ids[candidates[0]]

    return None


class AnsibleAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based Ansible YAML analyzer.

    Uses tree-sitter-yaml to parse Ansible playbook, task, handler, and
    variable files. Extracts playbooks, tasks, handlers, variables, and
    include/import reference edges.

    Overrides ``analyze`` because Ansible uses a single-pass approach per
    file (combined symbol+edge extraction) and custom file discovery logic
    that searches Ansible-specific directories rather than simple glob patterns.
    """

    lang = "yaml_ansible"
    file_patterns: ClassVar[list[str]] = ["*.yml", "*.yaml"]
    grammar_module = "tree_sitter_yaml"

    def analyze(
        self,
        repo_root: Path,
        max_files: Optional[int] = None,
    ) -> AnalysisResult:
        """Run Ansible analysis with custom file discovery and single-pass extraction.

        Each file is processed with ``_extract_symbols_from_file`` which returns
        both symbols and edges in a single pass.
        """
        import time as _time
        import warnings as _warnings

        start_time = _time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        if not self._check_grammar_available():
            _warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        parser = self._create_parser()

        all_files = find_ansible_files(repo_root)
        if not all_files:
            run.duration_ms = int((_time.time() - start_time) * 1000)
            return AnalysisResult(run=run)

        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []

        # Build file-level nodes and a lookup map for edge resolution.
        # Maps basename → list of relative paths (multiple files may share a name).
        file_basename_map: dict[str, list[str]] = {}
        file_node_ids: dict[str, str] = {}  # rel_path → file node ID

        for ansible_file in all_files:
            rel_path = str(ansible_file)
            fid = make_file_id("ansible", rel_path)
            file_node_ids[rel_path] = fid

            basename = ansible_file.name
            file_basename_map.setdefault(basename, []).append(rel_path)

            # Create file-level node
            all_symbols.append(Symbol(
                id=fid,
                name=basename,
                kind="file",
                language="ansible",
                path=rel_path,
                span=Span(1, 1, 0, 0),
                origin=PASS_ID,
                origin_run_id=run.execution_id,
            ))

        for ansible_file in all_files:
            if max_files is not None and len(all_symbols) >= max_files:
                break  # pragma: no cover

            symbols, edges = _extract_symbols_from_file(ansible_file, parser, run)
            all_symbols.extend(symbols)
            all_edges.extend(edges)

        # Resolve edge destinations: convert raw filenames to file node IDs.
        # include_tasks/import_tasks use relative filenames; try to match
        # them against known Ansible files by basename or relative path.
        for edge in all_edges:
            if edge.edge_type != "imports":
                continue  # pragma: no cover — all current edges are imports
            raw_dst = edge.dst
            # Skip if already a valid node ID (shouldn't happen, but defensive)
            if raw_dst in file_node_ids.values():
                continue  # pragma: no cover — dst is always raw filename
            resolved = _resolve_ansible_path(
                raw_dst, edge.src, file_basename_map, file_node_ids,
            )
            if resolved is not None:
                edge.dst = resolved
            else:
                # Unresolvable (Jinja2 template, missing file, etc.)
                edge.confidence = 0.50

        run.duration_ms = int((_time.time() - start_time) * 1000)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = AnsibleAnalyzer()


def is_yaml_tree_sitter_available() -> bool:
    """Check if tree-sitter and yaml grammar are available."""
    return _analyzer._check_grammar_available()


@register_analyzer("yaml_ansible")
def analyze_ansible(root: Path) -> AnalysisResult:
    """Analyze Ansible YAML files in a directory.

    Uses tree-sitter-yaml for parsing. Falls back gracefully if not available.
    """
    return _analyzer.analyze(root)
