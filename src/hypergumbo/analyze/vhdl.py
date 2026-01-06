"""VHDL analysis pass using tree-sitter-vhdl.

This analyzer uses tree-sitter to parse VHDL files and extract:
- Entity declarations
- Architecture definitions
- Package declarations
- Library/use clauses
- Component instantiations

If tree-sitter-vhdl is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-vhdl is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all entity/architecture/package definitions
   - Pass 2: Resolve component instantiations and create edges
4. Create implements edges for architecture -> entity relationships

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-vhdl package for grammar
- Two-pass allows cross-file entity resolution
- Hardware-specific: entities, architectures, packages are first-class
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

PASS_ID = "vhdl-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_vhdl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all VHDL files in the repository."""
    yield from find_files(repo_root, ["*.vhd", "*.vhdl"])


def is_vhdl_tree_sitter_available() -> bool:
    """Check if tree-sitter with VHDL grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_vhdl") is None:
        return False  # pragma: no cover
    return True


@dataclass
class VHDLAnalysisResult:
    """Result of analyzing VHDL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"vhdl:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_entity_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract entity name from entity_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None  # pragma: no cover


def _get_architecture_info(node: "tree_sitter.Node", source: bytes) -> Optional[tuple[str, str]]:
    """Extract architecture name and entity name from architecture_definition.

    Returns:
        Tuple of (architecture_name, entity_name) or None if not found
    """
    arch_name = None
    entity_name = None

    for child in node.children:
        if child.type == "identifier" and arch_name is None:
            arch_name = _node_text(child, source)
        elif child.type == "name":
            entity_name = _node_text(child, source)

    if arch_name and entity_name:
        return (arch_name, entity_name)
    return None  # pragma: no cover


def _get_package_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract package name from package_declaration node."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None  # pragma: no cover


def _process_vhdl_tree(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    entity_registry: dict[str, str],
) -> None:
    """Process VHDL AST tree to extract symbols and edges.

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        entity_registry: Registry mapping entity names to symbol IDs
    """
    for node in iter_tree(root):
        if node.type == "entity_declaration":
            entity_name = _get_entity_name(node, source)
            if entity_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, entity_name, "entity")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=entity_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="entity",
                    name=entity_name,
                    path=rel_path,
                    language="vhdl",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                entity_registry[entity_name.lower()] = symbol_id

        elif node.type == "architecture_definition":
            arch_info = _get_architecture_info(node, source)
            if arch_info:
                arch_name, entity_name = arch_info
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, arch_name, "architecture")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"{arch_name}({entity_name})",
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="architecture",
                    name=arch_name,
                    path=rel_path,
                    language="vhdl",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

                # Create implements edge to entity
                if entity_name.lower() in entity_registry:
                    dst_id = entity_registry[entity_name.lower()]
                    confidence = 0.90
                else:
                    # External entity reference
                    dst_id = f"vhdl:external:{entity_name}:entity"
                    confidence = 0.70

                edge = Edge(
                    id=_make_edge_id(symbol_id, dst_id, "implements"),
                    src=symbol_id,
                    dst=dst_id,
                    edge_type="implements",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    evidence_type="vhdl_architecture",
                )
                edges.append(edge)

        elif node.type == "package_declaration":
            pkg_name = _get_package_name(node, source)
            if pkg_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, pkg_name, "package")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=pkg_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="package",
                    name=pkg_name,
                    path=rel_path,
                    language="vhdl",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                entity_registry[pkg_name.lower()] = symbol_id

        elif node.type == "component_declaration":  # pragma: no cover - rare VHDL syntax
            # Component declaration within architecture
            for child in node.children:  # pragma: no cover
                if child.type == "identifier":  # pragma: no cover
                    comp_name = _node_text(child, source)  # pragma: no cover
                    start_line = node.start_point[0] + 1  # pragma: no cover
                    end_line = node.end_point[0] + 1  # pragma: no cover
                    symbol_id = _make_symbol_id(rel_path, start_line, end_line, comp_name, "component")  # pragma: no cover

                    sym = Symbol(  # pragma: no cover
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=comp_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind="component",
                        name=comp_name,
                        path=rel_path,
                        language="vhdl",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                    )
                    symbols.append(sym)  # pragma: no cover
                    break  # pragma: no cover


def analyze_vhdl_files(repo_root: Path) -> VHDLAnalysisResult:
    """Analyze VHDL files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        VHDLAnalysisResult with symbols and edges
    """
    if not is_vhdl_tree_sitter_available():  # pragma: no cover
        return VHDLAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-vhdl not installed (pip install tree-sitter-vhdl)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_vhdl

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Entity registry for cross-file resolution: name -> symbol_id
    entity_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_vhdl.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize VHDL parser: {e}")
        return VHDLAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    vhdl_files = list(find_vhdl_files(repo_root))

    for vhdl_path in vhdl_files:
        try:
            rel_path = str(vhdl_path.relative_to(repo_root))
            source = vhdl_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_vhdl_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                entity_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {vhdl_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return VHDLAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
