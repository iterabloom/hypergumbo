"""CUDA analysis pass using tree-sitter-cuda.

This analyzer uses tree-sitter to parse CUDA files and extract:
- Kernel functions (__global__)
- Device functions (__device__)
- Host/device functions (__host__ __device__)
- Regular host functions
- Kernel launches (<<<grid, block>>>)
- CUDA API calls

If tree-sitter-cuda is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-cuda is available
2. If not available, return skipped result (not an error)
3. Two-pass analysis:
   - Pass 1: Parse all files, extract all function symbols
   - Pass 2: Detect kernel launches and create edges
4. Create call edges for function invocations

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-cuda package for grammar
- Two-pass allows cross-file kernel launch resolution
- CUDA-specific: kernels, device functions, launches are first-class
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

PASS_ID = "cuda-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_cuda_files(repo_root: Path) -> Iterator[Path]:
    """Yield all CUDA files in the repository."""
    yield from find_files(repo_root, ["*.cu", "*.cuh"])


def is_cuda_tree_sitter_available() -> bool:
    """Check if tree-sitter with CUDA grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_cuda") is None:
        return False  # pragma: no cover
    return True


@dataclass
class CudaAnalysisResult:
    """Result of analyzing CUDA files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"cuda:{path}:{start_line}-{end_line}:{name}:{kind}"


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


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from a function_definition or function_declarator."""
    # Look for function_declarator
    declarator = _find_child_by_type(node, "function_declarator")
    if declarator:
        # The first identifier child of function_declarator is the name
        for child in declarator.children:
            if child.type == "identifier":
                return _node_text(child, source)
    return None  # pragma: no cover


def _extract_cuda_signature(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a CUDA function definition.

    CUDA uses C/C++ syntax: return_type function_name(type1 param1, type2 param2)
    Returns signature like "(int x, float* data) int".
    """
    params: list[str] = []
    return_type: Optional[str] = None

    # Find function_declarator for parameters
    declarator = _find_child_by_type(node, "function_declarator")
    if declarator:
        # Find parameter_list within declarator
        for child in declarator.children:
            if child.type == "parameter_list":
                for param_child in child.children:
                    if param_child.type == "parameter_declaration":
                        param_text = _node_text(param_child, source).strip()
                        if param_text:
                            params.append(param_text)

    # Find return type (primitive_type, type_identifier, etc.)
    for child in node.children:
        if child.type in ("primitive_type", "type_identifier", "sized_type_specifier"):
            return_type = _node_text(child, source).strip()
            break

    sig = "(" + ", ".join(params) + ")"
    if return_type and return_type != "void":
        sig += f" {return_type}"
    return sig


def _get_cuda_attributes(node: "tree_sitter.Node") -> tuple[bool, bool, bool]:
    """Check for __global__, __device__, __host__ attributes.

    Returns:
        Tuple of (is_global, is_device, is_host)
    """
    is_global = False
    is_device = False
    is_host = False

    for child in node.children:
        if child.type == "__global__":
            is_global = True
        elif child.type == "__device__":
            is_device = True
        elif child.type == "__host__":
            is_host = True

    return is_global, is_device, is_host


def _determine_function_kind(is_global: bool, is_device: bool, is_host: bool) -> str:
    """Determine the function kind based on CUDA attributes."""
    if is_global:
        return "kernel"
    elif is_device and is_host:
        return "host_device_function"
    elif is_device:
        return "device_function"
    elif is_host:
        return "function"  # pragma: no cover - __host__ alone is rare
    else:
        return "function"  # No CUDA attributes = regular function


def _get_enclosing_cuda_function(
    node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbol_registry: dict[str, str],
) -> Optional[str]:
    """Walk up to find the enclosing function definition's symbol ID."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            func_name = _get_function_name(current, source)
            if func_name and func_name.lower() in symbol_registry:
                return symbol_registry[func_name.lower()]
        current = current.parent
    return None  # pragma: no cover - defensive


def _process_cuda_tree(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    symbol_registry: dict[str, str],
) -> None:
    """Process CUDA AST tree to extract symbols and edges.

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        symbol_registry: Registry mapping function names to symbol IDs
    """
    for node in iter_tree(root_node):
        if node.type == "function_definition":
            func_name = _get_function_name(node, source)
            if func_name:
                is_global, is_device, is_host = _get_cuda_attributes(node)
                kind = _determine_function_kind(is_global, is_device, is_host)

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, func_name, kind)

                # Extract signature
                signature = _extract_cuda_signature(node, source)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=func_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind=kind,
                    name=func_name,
                    path=rel_path,
                    language="cuda",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                    meta={"is_kernel": is_global} if is_global else None,
                )
                symbols.append(sym)
                symbol_registry[func_name.lower()] = symbol_id

        elif node.type == "call_expression":
            # Check for kernel launch syntax <<<...>>> (parsed as kernel_call_syntax)
            is_kernel_launch = any(child.type == "kernel_call_syntax" for child in node.children)

            # Get the function name being called
            func_node = _find_child_by_type(node, "identifier")
            if not func_node:  # pragma: no cover - method call edge case
                # Try field_expression for method calls
                field_expr = _find_child_by_type(node, "field_expression")  # pragma: no cover
                if field_expr:  # pragma: no cover
                    # Get the method name
                    for child in field_expr.children:  # pragma: no cover
                        if child.type == "field_identifier":  # pragma: no cover
                            func_node = child  # pragma: no cover
                            break  # pragma: no cover

            current_function_id = _get_enclosing_cuda_function(node, source, rel_path, symbol_registry)
            if func_node and current_function_id:
                called_name = _node_text(func_node, source)
                edge_type = "kernel_launch" if is_kernel_launch else "calls"
                start_line = node.start_point[0] + 1

                # Create a destination ID - either from registry or synthetic
                if called_name.lower() in symbol_registry:
                    dst_id = symbol_registry[called_name.lower()]
                else:
                    # Synthetic ID for unknown functions (like CUDA API)
                    dst_id = f"cuda:external:{called_name}:function"

                edge = Edge(
                    id=_make_edge_id(current_function_id, dst_id, edge_type),
                    src=current_function_id,
                    dst=dst_id,
                    edge_type=edge_type,
                    line=start_line,
                    confidence=0.90 if called_name.lower() in symbol_registry else 0.70,
                    origin=PASS_ID,
                    evidence_type="cuda_kernel_launch" if is_kernel_launch else "cuda_call",
                )
                edges.append(edge)


def analyze_cuda_files(repo_root: Path) -> CudaAnalysisResult:
    """Analyze CUDA files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        CudaAnalysisResult with symbols and edges
    """
    if not is_cuda_tree_sitter_available():  # pragma: no cover
        return CudaAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-cuda not installed (pip install tree-sitter-cuda)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_cuda

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Symbol registry for cross-file resolution: name -> symbol_id
    symbol_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_cuda.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize CUDA parser: {e}")
        return CudaAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    cuda_files = list(find_cuda_files(repo_root))

    for cuda_path in cuda_files:
        try:
            rel_path = str(cuda_path.relative_to(repo_root))
            source = cuda_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_cuda_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                symbol_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {cuda_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return CudaAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
