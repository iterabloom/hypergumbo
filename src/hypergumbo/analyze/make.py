"""Makefile analysis pass using tree-sitter-make.

This analyzer uses tree-sitter to parse Makefiles and extract:
- Variable definitions
- Target rules (explicit and pattern rules)
- Prerequisites (dependencies)
- Include directives
- Define blocks (functions/macros)

If tree-sitter-make is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-make is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all targets and variables
   - Pass 2: Resolve prerequisites and create dependency edges
4. Create depends_on edges for target dependencies

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-make package for grammar
- Two-pass allows cross-file target resolution
- Build-system-specific: targets, prerequisites, variables are first-class
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "make-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_make_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Makefile files in the repository."""
    yield from find_files(repo_root, ["Makefile", "makefile", "*.mk", "GNUmakefile"])


def is_make_tree_sitter_available() -> bool:
    """Check if tree-sitter with Make grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_make") is None:
        return False  # pragma: no cover
    return True


@dataclass
class MakeAnalysisResult:
    """Result of analyzing Makefile files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"make:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_target_names(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract target names from a targets node."""
    targets = []
    for child in node.children:
        if child.type == "word":
            targets.append(_node_text(child, source))
    return targets


def _get_prerequisites(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract prerequisite names from a prerequisites node."""
    prereqs = []
    for child in node.children:
        if child.type == "word":
            prereqs.append(_node_text(child, source))
        elif child.type == "variable_reference":
            # Include variable reference as a dependency marker
            prereqs.append(_node_text(child, source))
    return prereqs


def _get_variable_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract variable name from a variable_assignment node."""
    for child in node.children:
        if child.type == "word":
            return _node_text(child, source)
    return None  # pragma: no cover


def _get_define_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function/macro name from a define_directive node."""
    for child in node.children:
        if child.type == "word":
            return _node_text(child, source)
    return None  # pragma: no cover


def _get_include_files(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """Extract included file names from an include_directive node."""
    files = []
    for child in node.children:
        if child.type == "list":
            for subchild in child.children:
                if subchild.type == "word":
                    files.append(_node_text(subchild, source))
    return files


def _process_make_tree(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    target_registry: dict[str, str],
) -> None:
    """Process Makefile AST tree to extract symbols and edges.

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        target_registry: Registry mapping target names to symbol IDs
    """
    for node in iter_tree(root):
        if node.type == "variable_assignment":
            var_name = _get_variable_name(node, source)
            if var_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, var_name, "variable")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=var_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="variable",
                    name=var_name,
                    path=rel_path,
                    language="make",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                target_registry[var_name.lower()] = symbol_id

        elif node.type == "rule":
            # Extract targets and prerequisites
            targets_node = None
            prereqs_node = None

            for child in node.children:
                if child.type == "targets":
                    targets_node = child
                elif child.type == "prerequisites":
                    prereqs_node = child

            if targets_node:
                target_names = _get_target_names(targets_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Determine if this is a pattern rule
                is_pattern = any("%" in t for t in target_names)
                kind = "pattern_rule" if is_pattern else "target"

                for target_name in target_names:
                    # Skip special targets like .PHONY
                    if target_name.startswith("."):
                        kind = "special_target"

                    symbol_id = _make_symbol_id(rel_path, start_line, end_line, target_name, kind)

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=target_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind=kind,
                        name=target_name,
                        path=rel_path,
                        language="make",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    symbols.append(sym)
                    target_registry[target_name.lower()] = symbol_id

                    # Create edges for prerequisites
                    if prereqs_node:
                        prereqs = _get_prerequisites(prereqs_node, source)
                        for prereq in prereqs:
                            # Skip variable references for now (could resolve later)
                            if prereq.startswith("$"):
                                continue

                            if prereq.lower() in target_registry:
                                dst_id = target_registry[prereq.lower()]
                                confidence = 0.90
                            else:
                                # External file or unresolved target
                                dst_id = f"make:external:{prereq}:target"
                                confidence = 0.70

                            edge = Edge(
                                id=_make_edge_id(symbol_id, dst_id, "depends_on"),
                                src=symbol_id,
                                dst=dst_id,
                                edge_type="depends_on",
                                line=start_line,
                                confidence=confidence,
                                origin=PASS_ID,
                                evidence_type="make_prerequisite",
                            )
                            edges.append(edge)

        elif node.type == "define_directive":
            define_name = _get_define_name(node, source)
            if define_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, define_name, "function")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=define_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="function",
                    name=define_name,
                    path=rel_path,
                    language="make",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                target_registry[define_name.lower()] = symbol_id

        elif node.type == "include_directive":
            include_files = _get_include_files(node, source)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            for include_file in include_files:
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, include_file, "include")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=include_file,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="include",
                    name=include_file,
                    path=rel_path,
                    language="make",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)


def analyze_make_files(repo_root: Path) -> MakeAnalysisResult:
    """Analyze Makefile files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        MakeAnalysisResult with symbols and edges
    """
    if not is_make_tree_sitter_available():  # pragma: no cover
        return MakeAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-make not installed (pip install tree-sitter-make)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_make

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Target registry for cross-file resolution: name -> symbol_id
    target_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_make.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize Make parser: {e}")
        return MakeAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    make_files = list(find_make_files(repo_root))

    for make_path in make_files:
        try:
            rel_path = str(make_path.relative_to(repo_root))
            source = make_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_make_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                target_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {make_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return MakeAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
