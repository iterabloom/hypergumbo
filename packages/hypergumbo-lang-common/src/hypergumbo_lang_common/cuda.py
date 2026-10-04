# SPDX-License-Identifier: AGPL-3.0-or-later
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

A definition's name is read at any declarator depth by the C-family walk
shared with c.py / cpp.py (``hypergumbo_core.analyze.c_family``): a function
returning a pointer (``float **f()``), a member, qualified (``A::run``),
operator or destructor leaf, and an explicit specialisation (named by its
template) each get a symbol. Before WI-fohuh only a ``function_declarator``
directly under the definition was read, so all of them got none and every
call in them was dropped. A call's caller is the enclosing definition found
by POSITION (INV-midag), not by name. A call inside a definition with no
honest name (``PFX(f)(args)``, a macro tree-sitter cannot expand) is drawn
from the nearest enclosing definition that has a symbol, else the file
anchor, and carries ``meta["src_stands_in_for"] = "unnamed_definition"``
(WI-tikop). A call in no definition at all is not emitted.

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

Symbol Kind Fold (WI-vibaz / ADR-0027)
---------------------------------------
Per the canonical fold, every CUDA function is emitted as
``Symbol(kind="function")`` regardless of its CUDA attributes. The
GPU/CPU execution space is carried instead on
``meta["cuda_execution_space"]`` (one of ``global`` / ``device`` /
``host_device`` / ``host``, or absent for a plain host function).
``__global__`` kernels additionally carry ``meta["is_kernel"] = True``,
a legacy flag kept for external consumers; nothing under packages/*/src
reads it. Kernel launches are ``calls`` edges marked with
``meta["mechanism"] = "kernel_launch"``, decided from the ``<<<...>>>``
launch syntax at the call site rather than from this flag.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    file_anchor_symbol,
    find_child_by_type,
    iter_tree,
    make_symbol_id,
    node_text,
    symbols_at,
)
from hypergumbo_core.analyze.c_family import c_family_declarator
from hypergumbo_core.analyze.edge_source import anchor_in_definitions, mark_stand_in
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

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


#: The name leaves a CUDA (C++) definition's declarator chain can end in, other
#: than ``template_function`` (read up to its ``<``).
_NAME_LEAVES = frozenset({
    "identifier", "field_identifier", "qualified_identifier",
    "operator_name", "destructor_name",
})


def _get_function_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """The name a ``function_definition`` defines, at any declarator depth.

    The shared C-family walk (``c_family_declarator``, WI-fohuh): ``float *f()``
    and ``float **f()`` put the ``function_declarator`` under one or two
    ``pointer_declarator`` s, and the reader that looked for it as a DIRECT child
    gave every pointer-returning function no symbol. The leaf decides the name:
    a plain, member (``field_identifier``) or qualified (``A::run``) identifier,
    an operator or destructor as written, and an explicit specialisation by its
    template's name. A macro-call-shaped declarator (``PFX(x)(args)``) and a
    definition with no function declarator name nothing.
    """
    shape = c_family_declarator(node)
    leaf = shape.name
    if shape.function_declarator is None or leaf is None:
        return None
    if leaf.type in _NAME_LEAVES:
        return node_text(leaf, source)
    if leaf.type == "template_function":
        return node_text(leaf, source).split("<", 1)[0].strip()
    return None  # pragma: no cover - no other name leaf in tree-sitter-cuda


def _extract_cuda_signature(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a CUDA function definition.

    CUDA uses C/C++ syntax: return_type function_name(type1 param1, type2 param2)
    Returns signature like "(int x, float* data) int"; a pointer return carries
    one ``*`` per level (``float **f()`` -> ``float**``), and a function returning
    a function pointer renders no return type rather than a false one.
    """
    params: list[str] = []
    return_type: Optional[str] = None
    shape = c_family_declarator(node)

    # The function's own declarator, at any depth, holds its parameters.
    declarator = shape.function_declarator
    if declarator:
        for child in declarator.children:
            if child.type == "parameter_list":
                for param_child in child.children:
                    if param_child.type == "parameter_declaration":
                        param_text = node_text(param_child, source).strip()
                        if param_text:
                            params.append(param_text)

    sig = "(" + ", ".join(params) + ")"
    if shape.returns_function:
        return sig

    # Find return type (primitive_type, type_identifier, etc.)
    for child in node.children:
        if child.type in ("primitive_type", "type_identifier", "sized_type_specifier"):
            return_type = node_text(child, source).strip() + "*" * shape.pointer_depth
            break

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


def _determine_function_kind(
    is_global: bool, is_device: bool, is_host: bool,
) -> tuple[str, str | None]:
    """Determine the function kind based on CUDA attributes.

    Returns a (canonical_kind, cuda_execution_space) pair per WI-vibaz's
    ADR-0027 fold: every CUDA function is canonically a Python-style
    ``function`` symbol; the GPU/CPU execution space lives on
    ``Symbol.meta["cuda_execution_space"]``. The legacy bare kinds
    ``kernel`` / ``device_function`` / ``host_device_function`` were
    never in the canonical ``SYMBOL_KINDS`` registry, so the
    pre-WI-vibaz YAML rules in ``language-conventions.yaml`` (keyed on
    ``^global$`` / ``^device$`` / ``^host$``) silently no-op'd against
    the producer output. After the fold, those rules switch to
    ``^function$`` + ``meta_match: {cuda_execution_space: ...}`` and
    actually match.
    """
    if is_global:
        return "function", "global"
    elif is_device and is_host:
        return "function", "host_device"
    elif is_device:
        return "function", "device"
    elif is_host:
        return "function", "host"  # pragma: no cover - __host__ alone is rare
    else:
        return "function", None  # No CUDA attributes = regular function


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
                # WI-vibaz: canonical fold — every CUDA function is
                # kind="function"; GPU/CPU execution space lives on
                # meta["cuda_execution_space"]. The symbol_id discriminator
                # is the same string as the meta value when present, or
                # the literal "function" when no __global__/__device__/
                # __host__ attribute applies — preserving id uniqueness
                # without re-introducing registry-absent kind names.
                kind, exec_space = _determine_function_kind(is_global, is_device, is_host)
                discriminator = exec_space if exec_space else kind
                symbol_id = make_symbol_id(
                    "cuda",
                    rel_path,
                    node.start_point[0] + 1,
                    node.end_point[0] + 1,
                    func_name,
                    discriminator,
                )

                # Extract signature
                signature = _extract_cuda_signature(node, source)

                meta: dict[str, Any] | None = None
                if exec_space is not None:
                    meta = {"cuda_execution_space": exec_space}
                    if is_global:
                        # Preserve the legacy `is_kernel` flag for external
                        # consumers; no in-tree code reads it (launch edges
                        # are detected from the call-site syntax instead).
                        meta["is_kernel"] = True

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    fingerprint=None,  # WI-vudul: central stamp_symbol_fingerprints owns this (was dead bare-hex)
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
                    meta=meta,
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "cuda"),
                    line_span=end_line - start_line + 1,
                )
                symbols.append(sym)
                symbol_registry[func_name.lower()] = sym


#: The declaration a CUDA call is drawn from.
_DEFINITIONS = frozenset({"function_definition"})


def _extract_cuda_edges(
    root_node: "tree_sitter.Node",
    source: bytes,
    edges: list[Edge],
    file_symbols: list[Symbol],
    resolver: NameResolver,
    *,
    run_id: str,
    file_anchor: Symbol,
) -> None:
    """Extract edges from CUDA AST tree (pass 2).

    Uses NameResolver for callee resolution to enable cross-file symbol lookup.
    The caller is the enclosing ``function_definition``'s symbol, found by its
    POSITION in ``file_symbols`` (INV-midag): the name-keyed lookup this replaced
    drew every call in one of two same-named definitions (an ``#ifdef`` pair, a
    method name in two structs) from the other. A call inside a definition with
    no symbol -- one named by a macro tree-sitter cannot expand -- is drawn from
    the nearest enclosing definition that has one, else the file anchor, and
    says so (``anchor_in_definitions`` / ``mark_stand_in``, WI-tikop). A call in
    no definition at all is not emitted.

    Args:
        root_node: Root tree-sitter node to process
        source: Source file bytes
        edges: List to append edges to
        file_symbols: Every symbol of this file (``file_symbols()``)
        resolver: NameResolver for callee resolution
        run_id: The run's execution id
        file_anchor: The file's anchor symbol (``file_anchor_symbol``)
    """
    index = symbols_at(file_symbols)
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

            caller, stands_in = anchor_in_definitions(
                node, index, _DEFINITIONS, top_level=None, fallback=file_anchor,
            )
            edges_before = len(edges)
            if func_node and caller:
                called_name = node_text(func_node, source)
                # ADR-0023 fold: a CUDA kernel launch IS a call; the launch
                # mechanism moves to meta['mechanism']='kernel_launch' (set on
                # the is_kernel_launch branch below) rather than the edge_type.
                # Spelled as a literal so the call-anchor gate enumerates this
                # module (test_call_anchor_gate_common::_CALLS).
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

                if is_kernel_launch:
                    edge = Edge.create(
                        src=caller.id,
                        dst=dst_id,
                        edge_type="calls",
                        line=start_line,
                        confidence=confidence,
                        origin=PASS_ID,
                        evidence_type="ast_call_direct",
                        meta={
                            "framework_dispatch": "cuda_kernel_launch",
                            "mechanism": "kernel_launch",
                        },
                        origin_run_id=run_id,
                    )
                else:
                    edge = Edge.create(
                        src=caller.id,
                        dst=dst_id,
                        edge_type="calls",
                        line=start_line,
                        confidence=confidence,
                        origin=PASS_ID,
                        evidence_type="ast_call_direct",
                        origin_run_id=run_id,
                    )
                edges.append(edge)
                if stands_in:
                    mark_stand_in(edges, edges_before, caller.id)


class CudaAnalyzer(TreeSitterAnalyzer):
    """CUDA language analyzer using tree-sitter-cuda."""

    lang = "cuda"
    file_patterns: ClassVar[list[str]] = ["*.cu", "*.cuh"]
    grammar_module = "tree_sitter_cuda"
    create_file_symbols = False

    def register_symbol(
        self,
        symbol: Symbol,
        global_symbols: dict[str, Symbol],
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
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call and kernel launch edges from a CUDA file."""
        edges: list[Edge] = []
        _extract_cuda_edges(
            tree.root_node, source, edges, self.file_symbols(local_symbols),
            resolver, run_id=run.execution_id,
            file_anchor=file_anchor_symbol("cuda", rel_path, PASS_ID, run.execution_id),
        )
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
