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
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Parse all files, extract all function symbols
2. Pass 2: Detect kernel launches and create edges

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the CUDA-specific
extraction logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-cuda package for grammar
- Two-pass allows cross-file kernel launch resolution
- CUDA-specific: kernels, device functions, launches are first-class
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("cuda")


def find_cuda_files(repo_root: Path) -> Iterator[Path]:
    """Yield all CUDA files in the repository."""
    yield from find_files(repo_root, ["*.cu", "*.cuh"])


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function name from a function_definition or function_declarator."""
    # Look for function_declarator
    declarator = find_child_by_type(node, "function_declarator")
    if declarator:
        # The first identifier child of function_declarator is the name
        for child in declarator.children:
            if child.type == "identifier":
                return node_text(child, source)
    return None  # pragma: no cover


def _extract_cuda_signature(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a CUDA function definition.

    CUDA uses C/C++ syntax: return_type function_name(type1 param1, type2 param2)
    Returns signature like "(int x, float* data) int".
    """
    params: list[str] = []
    return_type: Optional[str] = None

    # Find function_declarator for parameters
    declarator = find_child_by_type(node, "function_declarator")
    if declarator:
        # Find parameter_list within declarator
        for child in declarator.children:
            if child.type == "parameter_list":
                for param_child in child.children:
                    if param_child.type == "parameter_declaration":
                        param_text = node_text(param_child, source).strip()
                        if param_text:
                            params.append(param_text)

    # Find return type (primitive_type, type_identifier, etc.)
    for child in node.children:
        if child.type in ("primitive_type", "type_identifier", "sized_type_specifier"):
            return_type = node_text(child, source).strip()
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
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Walk up to find the enclosing function definition's Symbol."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            func_name = _get_function_name(current, source)
            if func_name:
                sym = local_symbols.get(func_name.lower())
                if sym:
                    return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_cuda_symbols(
    root_node: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    symbol_registry: dict[str, Symbol],
) -> None:
    """Extract symbols from CUDA AST tree (pass 1).

    Uses iterative traversal to avoid RecursionError on deeply nested code.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        symbol_registry: Registry mapping function names to Symbol objects
    """
    for node in iter_tree(root_node):
        if node.type == "function_definition":
            func_name = _get_function_name(node, source)
            if func_name:
                is_global, is_device, is_host = _get_cuda_attributes(node)
                kind = _determine_function_kind(is_global, is_device, is_host)

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("cuda", rel_path, start_line, end_line, func_name, kind)

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
                symbol_registry[func_name.lower()] = sym


def _extract_cuda_edges(
    root_node: "tree_sitter.Node",
    source: bytes,
    edges: list[Edge],
    local_symbols: dict[str, Symbol],
    resolver: NameResolver,
) -> None:
    """Extract edges from CUDA AST tree (pass 2).

    Uses NameResolver for callee resolution to enable cross-file symbol lookup.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        edges: List to append edges to
        local_symbols: Local symbol registry for finding enclosing functions
        resolver: NameResolver for callee resolution
    """
    for node in iter_tree(root_node):
        if node.type == "call_expression":
            # Check for kernel launch syntax <<<...>>> (parsed as kernel_call_syntax)
            is_kernel_launch = any(child.type == "kernel_call_syntax" for child in node.children)

            # Get the function name being called
            func_node = find_child_by_type(node, "identifier")
            if not func_node:  # pragma: no cover - method call edge case
                # Try field_expression for method calls
                field_expr = find_child_by_type(node, "field_expression")  # pragma: no cover
                if field_expr:  # pragma: no cover
                    # Get the method name
                    for child in field_expr.children:  # pragma: no cover
                        if child.type == "field_identifier":  # pragma: no cover
                            func_node = child  # pragma: no cover
                            break  # pragma: no cover

            caller = _get_enclosing_cuda_function(node, source, local_symbols)
            if func_node and caller:
                called_name = node_text(func_node, source)
                edge_type = "kernel_launch" if is_kernel_launch else "calls"
                start_line = node.start_point[0] + 1

                # Use resolver for callee resolution
                lookup_result = resolver.lookup(called_name.lower())
                if lookup_result.found and lookup_result.symbol:
                    dst_id = lookup_result.symbol.id
                    confidence = 0.90 * lookup_result.confidence
                else:
                    # Synthetic ID for unknown functions (like CUDA API)
                    dst_id = f"cuda:external:{called_name}:function"
                    confidence = 0.70

                edge = Edge.create(
                    src=caller.id,
                    dst=dst_id,
                    edge_type=edge_type,
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    evidence_type="cuda_kernel_launch" if is_kernel_launch else "cuda_call",
                )
                edges.append(edge)


class CudaAnalyzer(TreeSitterAnalyzer):
    """CUDA language analyzer using tree-sitter-cuda."""

    lang = "cuda"
    file_patterns: ClassVar[list[str]] = ["*.cu", "*.cuh"]
    grammar_module = "tree_sitter_cuda"
    create_file_symbols = False

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict,
    ) -> None:
        """Register symbol with case-insensitive lookup key."""
        global_symbols[symbol.name.lower()] = symbol

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract symbols from a CUDA file."""
        analysis = FileAnalysis()
        symbols: list[Symbol] = []
        symbol_registry: dict[str, Symbol] = {}
        _extract_cuda_symbols(tree.root_node, source, rel_path, symbols, symbol_registry)
        analysis.symbols = symbols
        # Store symbols by lowercase name for local lookups in pass 2
        for sym in symbols:
            analysis.symbol_by_name[sym.name.lower()] = sym
        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and kernel launch edges from a CUDA file."""
        edges: list[Edge] = []
        _extract_cuda_edges(tree.root_node, source, edges, local_symbols, resolver)
        return edges


_analyzer = CudaAnalyzer()


def is_cuda_tree_sitter_available() -> bool:
    """Check if tree-sitter with CUDA grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("cuda")
def analyze_cuda_files(repo_root: Path) -> AnalysisResult:
    """Analyze CUDA files in the repository.

    Returns an AnalysisResult with symbols, edges, and provenance.
    If tree-sitter-cuda is not available, returns a skipped result.
    """
    return _analyzer.analyze(repo_root)
