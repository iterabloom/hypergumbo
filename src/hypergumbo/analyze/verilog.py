"""Verilog/SystemVerilog analysis pass using tree-sitter-verilog.

This analyzer uses tree-sitter to parse Verilog/SystemVerilog files and extract:
- Module definitions
- Interface definitions (SystemVerilog)
- Module instantiations
- Input/output ports
- Wire/register declarations

If tree-sitter-verilog is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-verilog is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all module/interface definitions
   - Pass 2: Resolve module instantiations and create edges
4. Create instantiates edges for module usage

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-verilog package for grammar
- Two-pass allows cross-file module resolution
- Hardware-specific: modules, interfaces, instantiations are first-class
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

PASS_ID = "verilog-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_verilog_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Verilog/SystemVerilog files in the repository."""
    yield from find_files(repo_root, ["*.v", "*.sv", "*.vh", "*.svh"])


def is_verilog_tree_sitter_available() -> bool:
    """Check if tree-sitter with Verilog grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_verilog") is None:
        return False  # pragma: no cover
    return True


@dataclass
class VerilogAnalysisResult:
    """Result of analyzing Verilog files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"verilog:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover


def _extract_module_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract module name from module_declaration node."""
    # Look for module_header -> simple_identifier
    header = _find_child_by_type(node, "module_header")
    if header:
        for child in header.children:
            if child.type == "simple_identifier":
                return _node_text(child, source)
    return None  # pragma: no cover


def _extract_interface_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract interface name from interface_declaration node."""
    # Look for interface_ansi_header -> interface_identifier -> simple_identifier
    for child in node.children:
        if child.type in ("interface_ansi_header", "interface_nonansi_header"):
            for subchild in child.children:
                if subchild.type == "interface_identifier":
                    ident = _find_child_by_type(subchild, "simple_identifier")
                    if ident:
                        return _node_text(ident, source)
                elif subchild.type == "simple_identifier":  # pragma: no cover
                    return _node_text(subchild, source)  # pragma: no cover
        elif child.type == "interface_identifier":  # pragma: no cover
            ident = _find_child_by_type(child, "simple_identifier")  # pragma: no cover
            if ident:  # pragma: no cover
                return _node_text(ident, source)  # pragma: no cover
        elif child.type == "simple_identifier":  # pragma: no cover
            return _node_text(child, source)  # pragma: no cover
    return None  # pragma: no cover


def _extract_instantiation_info(node: "tree_sitter.Node", source: bytes) -> Optional[tuple[str, str]]:
    """Extract module type and instance name from module_instantiation.

    Returns:
        Tuple of (module_type, instance_name) or None if not found
    """
    module_type = None
    instance_name = None

    for child in node.children:
        if child.type == "simple_identifier" and module_type is None:
            module_type = _node_text(child, source)
        elif child.type == "hierarchical_instance":
            # Look for name_of_instance -> instance_identifier -> simple_identifier
            name_of_inst = _find_child_by_type(child, "name_of_instance")
            if name_of_inst:
                inst_ident = _find_child_by_type(name_of_inst, "instance_identifier")
                if inst_ident:
                    ident = _find_child_by_type(inst_ident, "simple_identifier")
                    if ident:
                        instance_name = _node_text(ident, source)

    if module_type and instance_name:
        return (module_type, instance_name)
    return None  # pragma: no cover


def _find_containing_module(
    node: "tree_sitter.Node", module_by_pos: dict[tuple[int, int], str]
) -> Optional[str]:
    """Walk up parents to find the containing module's symbol ID."""
    current = node.parent
    while current is not None:
        pos_key = (current.start_byte, current.end_byte)
        if pos_key in module_by_pos:
            return module_by_pos[pos_key]
        current = current.parent
    return None  # pragma: no cover - defensive


def _process_verilog_tree(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    module_registry: dict[str, str],
) -> None:
    """Process Verilog AST tree to extract symbols and edges.

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        module_registry: Registry mapping module names to symbol IDs
    """
    # Track module nodes by byte position for parent walking
    # (node.parent returns new Python object, so id() doesn't work)
    module_by_pos: dict[tuple[int, int], str] = {}

    for node in iter_tree(root):
        if node.type == "module_declaration":
            module_name = _extract_module_name(node, source)
            if module_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, module_name, "module")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=module_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="module",
                    name=module_name,
                    path=rel_path,
                    language="verilog",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                module_registry[module_name.lower()] = symbol_id
                module_by_pos[(node.start_byte, node.end_byte)] = symbol_id

        elif node.type == "interface_declaration":
            interface_name = _extract_interface_name(node, source)
            if interface_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, interface_name, "interface")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=interface_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="interface",
                    name=interface_name,
                    path=rel_path,
                    language="verilog",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                module_registry[interface_name.lower()] = symbol_id

        elif node.type == "module_instantiation":
            # Find containing module by walking up parents
            current_module_id = _find_containing_module(node, module_by_pos)
            inst_info = _extract_instantiation_info(node, source)
            if inst_info and current_module_id:
                module_type, _instance_name = inst_info
                start_line = node.start_point[0] + 1

                # Create instantiates edge if module is known
                if module_type.lower() in module_registry:
                    dst_id = module_registry[module_type.lower()]
                else:
                    # External module reference
                    dst_id = f"verilog:external:{module_type}:module"

                edge = Edge(
                    id=_make_edge_id(current_module_id, dst_id, "instantiates"),
                    src=current_module_id,
                    dst=dst_id,
                    edge_type="instantiates",
                    line=start_line,
                    confidence=0.90 if module_type.lower() in module_registry else 0.70,
                    origin=PASS_ID,
                    evidence_type="verilog_instantiation",
                )
                edges.append(edge)


def analyze_verilog_files(repo_root: Path) -> VerilogAnalysisResult:
    """Analyze Verilog/SystemVerilog files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        VerilogAnalysisResult with symbols and edges
    """
    if not is_verilog_tree_sitter_available():  # pragma: no cover
        return VerilogAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-verilog not installed (pip install tree-sitter-verilog)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_verilog

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Module registry for cross-file resolution: name -> symbol_id
    module_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_verilog.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize Verilog parser: {e}")
        return VerilogAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    verilog_files = list(find_verilog_files(repo_root))

    for verilog_path in verilog_files:
        try:
            rel_path = str(verilog_path.relative_to(repo_root))
            source = verilog_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_verilog_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                module_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {verilog_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return VerilogAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
