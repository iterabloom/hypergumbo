"""HLSL (DirectX shader) analysis pass using tree-sitter.

Detects:
- Function definitions (vertex, pixel, compute shaders)
- Struct definitions (input/output structures)
- Constant buffer declarations (cbuffer)
- Resource declarations (Texture, Sampler, Buffer)

HLSL is Microsoft's High Level Shading Language for DirectX.
The tree-sitter-hlsl parser handles .hlsl, .hlsli, and .fx files.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration.
The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the HLSL-specific extraction logic.

1. Check if tree-sitter with HLSL grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .hlsl, .hlsli, and .fx files
4. Extract function definitions with signatures
5. Extract struct definitions
6. Track constant buffer and resource declarations

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for HLSL grammar
- HLSL is essential for DirectX game development
- Complements GLSL and WGSL shader analyzers
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = "hlsl-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_hlsl_files(repo_root: Path) -> Iterator[Path]:
    """Find all HLSL files in the repository."""
    yield from find_files(repo_root, ["*.hlsl", "*.hlsli", "*.fx"])


def _find_enclosing_function_hlsl(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing function Symbol by walking up parents."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            func_decl = find_child_by_type(current, "function_declarator")
            if func_decl:
                name_node = find_child_by_type(func_decl, "identifier")
                if name_node:
                    func_name = node_text(name_node, source)
                    sym = local_symbols.get(func_name)
                    if sym:
                        return sym
        current = current.parent
    return None


def _get_call_target_name_hlsl(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract the target name from a call_expression node."""
    # Direct function call: func(args)
    name_node = find_child_by_type(node, "identifier")
    if name_node:
        return node_text(name_node, source)
    # Method call on field: obj.method(args) - get the method name
    # pragma: no cover - tree-sitter-hlsl parses method calls differently
    field_expr = find_child_by_type(node, "field_expression")  # pragma: no cover
    if field_expr:  # pragma: no cover
        for child in field_expr.children:  # pragma: no cover
            if child.type == "field_identifier":  # pragma: no cover
                return node_text(child, source)  # pragma: no cover
    return None  # pragma: no cover - defensive for unknown call patterns


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _make_symbol(rel_path: str, run_id: str, source: bytes, node: "tree_sitter.Node",
                 name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"hlsl:{rel_path}:{start_line}-{end_line}:{name}:{kind}"
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=sym_id,
        name=name,
        canonical_name=name,
        kind=kind,
        language="hlsl",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        stable_id=f"hlsl:{rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _extract_function_signature(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature from a function_declarator node."""
    param_list = find_child_by_type(node, "parameter_list")
    if param_list:
        return node_text(param_list, source)
    return "()"  # pragma: no cover - HLSL functions always have parameter_list


def _extract_hlsl_symbols(
    rel_path: str, run_id: str, source: bytes, tree: "tree_sitter.Tree",
    symbols: list[Symbol], local_symbols: dict[str, Symbol],
) -> None:
    """Extract symbols from a tree-sitter tree (pass 1)."""
    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            func_decl = find_child_by_type(node, "function_declarator")
            if not func_decl:
                continue  # pragma: no cover
            name_node = find_child_by_type(func_decl, "identifier")
            if not name_node:
                continue  # pragma: no cover
            func_name = node_text(name_node, source)
            signature = _extract_function_signature(func_decl, source)
            sym = _make_symbol(rel_path, run_id, source, node, func_name, "function",
                               signature=signature)
            symbols.append(sym)
            local_symbols[func_name] = sym

        elif node.type == "struct_specifier":
            name_node = find_child_by_type(node, "type_identifier")
            if not name_node:
                continue  # pragma: no cover
            struct_name = node_text(name_node, source)
            symbols.append(_make_symbol(rel_path, run_id, source, node, struct_name, "struct"))

        elif node.type == "declaration":
            name_node = find_child_by_type(node, "identifier")
            if not name_node:
                continue  # pragma: no cover - defensive
            var_name = node_text(name_node, source)
            symbols.append(_make_symbol(rel_path, run_id, source, node, var_name, "variable"))


def _extract_hlsl_edges(
    rel_path: str,
    source: bytes,
    tree: "tree_sitter.Tree",
    local_symbols: dict[str, Symbol],
    resolver: "NameResolver",
    run_id: str,
    edges: list[Edge],
) -> None:
    """Extract call edges from a tree-sitter tree (pass 2)."""
    for node in iter_tree(tree.root_node):
        if node.type == "call_expression":
            caller = _find_enclosing_function_hlsl(node, source, local_symbols)
            if not caller:
                continue

            target_name = _get_call_target_name_hlsl(node, source)
            if not target_name:
                continue  # pragma: no cover - defensive for unknown call patterns

            # Try to resolve the callee
            result = resolver.lookup(target_name)
            if result.symbol is not None:
                dst_id = result.symbol.id
                confidence = 0.85 * result.confidence
            else:
                dst_id = f"hlsl:external:{target_name}:function"
                confidence = 0.70

            edge = Edge(
                id=_make_edge_id(caller.id, dst_id, "calls"),
                src=caller.id,
                dst=dst_id,
                edge_type="calls",
                line=node.start_point[0] + 1,
                confidence=confidence,
                origin=PASS_ID,
                evidence_type="static",
            )
            edges.append(edge)


class HlslAnalyzer(TreeSitterAnalyzer):
    """HLSL language analyzer using tree-sitter-language-pack."""

    lang = "hlsl"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = ["*.hlsl", "*.hlsli", "*.fx"]
    language_pack_name = "hlsl"

    def register_symbol(self, symbol: Symbol, global_symbols: dict) -> None:
        """Only register function symbols for cross-file call resolution."""
        if symbol.kind == "function":
            global_symbols[symbol.name] = symbol

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract function, struct, and variable symbols from HLSL."""
        analysis = FileAnalysis()
        local_symbols: dict[str, Symbol] = {}

        _extract_hlsl_symbols(
            rel_path, run.execution_id, source, tree,
            analysis.symbols, local_symbols,
        )

        # Populate symbol_by_name for callable symbols (functions only)
        for sym in analysis.symbols:
            if sym.kind == "function":
                analysis.symbol_by_name[sym.name] = sym

        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from HLSL."""
        edges: list[Edge] = []
        _extract_hlsl_edges(
            rel_path, source, tree, local_symbols,
            resolver, run.execution_id, edges,
        )
        return edges


_analyzer = HlslAnalyzer()


def is_hlsl_tree_sitter_available() -> bool:
    """Check if tree-sitter-hlsl is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("hlsl")
def analyze_hlsl(repo_root: Path) -> AnalysisResult:
    """Analyze HLSL files in a repository.

    Returns a AnalysisResult with symbols for functions, structs, and variables.
    Uses two-pass analysis for cross-file call resolution.
    """
    return _analyzer.analyze(repo_root)
