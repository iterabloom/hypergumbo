# SPDX-License-Identifier: AGPL-3.0-or-later
"""HLSL (DirectX shader) analysis pass using tree-sitter.

Detects:
- Function definitions (vertex, pixel, compute shaders)
- Struct definitions (input/output structures)
- Variable declarations (textures, samplers, buffers and the fields inside
  cbuffer blocks, all kind='variable'; the cbuffer block itself is not a
  distinct symbol)
- Call edges (function-to-function 'calls' edges)

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
6. Extract variable declarations as kind='variable' (constant buffers and
   resource declarations are not distinctly tracked).
7. Extract call edges (pass 2): for each call_expression, resolve the callee
   via the NameResolver and emit a 'calls' edge — to the resolved symbol
   (interpolated in the ast_call_direct band: 0.50 + 0.35 * resolver
   confidence) or to an 'hlsl:external:<name>:function' target at the
   unresolved base, 0.50.

A function records its parameters, types included, in ``meta["parameters"]``
(``c_family_parameters``) and its stable_id hashes them
(``overload_arity.node_declared_arity_stable_id``, WI-tusad): the list sits
under the declarator, where ``compute_stable_id`` never looked, so the
overloads ``f(float)`` / ``f(float2)`` tied on ``param_count 0``. Every symbol
also hashes the namespaces, structs and functions it is declared in
(``c_family_scope_chain(functions=True)`` through
``overload_arity.nested_qualified_name``, empty at top level; INV-punig): ``f``
in ``namespace a`` and in ``namespace b``, or a local ``x`` in two functions,
are two identities.

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
from typing import Any, TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    SymbolsAt,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_symbol_id,
    node_text,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.c_family import c_family_parameters, c_family_scope_chain
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.overload_arity import (
    PARAMETERS_KEY, nested_qualified_name, node_declared_arity_stable_id,
)
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.cyclomatic import compute_cyclomatic_complexity

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("hlsl")


def find_hlsl_files(repo_root: Path) -> Iterator[Path]:
    """Find all HLSL files in the repository.

    The analyzer's own discovery (``_find_source_files``), so the two cannot drift.
    """
    yield from _analyzer._find_source_files(repo_root)


def _find_enclosing_function_hlsl(
    node: "tree_sitter.Node",
    decl_index: SymbolsAt,
) -> Optional[Symbol]:
    """The function whose definition contains ``node``.

    Keyed by the definition's POSITION, not its name (INV-midag, WI-mapor).
    HLSL lets a file overload a function by parameter types, and the name
    lookup credited every overload's calls to the last one.
    """
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            sym = symbol_declared_by(current, decl_index)
            if sym is not None:
                return sym
        current = current.parent
    return None  # pragma: no cover - no enclosing function found


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


def _make_symbol(analyzer: "TreeSitterAnalyzer", rel_path: str, run_id: str,
                 source: bytes, node: "tree_sitter.Node",
                 name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict[str, Any]] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = make_symbol_id("hlsl", rel_path, start_line, end_line, name, kind)
    span = Span(
        start_line=start_line,
        start_col=node.start_point[1],
        end_line=end_line,
        end_col=node.end_point[1],
    )
    return Symbol(
        id=sym_id,
        name=name,
        kind=kind,
        language="hlsl",
        path=rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        # WI-bokab (v7): fold this file's identity into the stable_id so two
        # same-(kind, name) symbols in different files hash distinctly. The
        # tree-sitter producer passes no enclosing-scope containing, so the
        # anchor lands in the containing slot. ``rel_path`` is repo-relative
        # (the base ``analyze()`` loop derives it via relative_to(repo_root)).
        # WI-tusad: the arity comes from meta["parameters"] (the declarator's
        # list, which compute_stable_id never finds), so f(float) / f(float2)
        # stop tying. INV-punig: the namespaces, structs and functions around
        # the declaration (a local variable is emitted too), empty at top
        # level, where the value is unchanged.
        stable_id=node_declared_arity_stable_id(
            analyzer, node, kind=kind, name=name, meta=meta,
            file_stable_id=analyzer._file_anchor(rel_path),
            qualified_name=nested_qualified_name(
                c_family_scope_chain(node, source, functions=True), name,
            ),
        ),
        signature=signature,
        meta=meta,
        # INV-loguk: CC only for callables (kind=="function"); struct/variable
        # share this helper and would otherwise aggregate their subtree.
        cyclomatic_complexity=(
            compute_cyclomatic_complexity(node, "hlsl") if kind == "function" else None
        ),
        line_span=(end_line - start_line + 1) if kind == "function" else None,
    )


def _extract_function_signature(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature from a function_declarator node."""
    param_list = find_child_by_type(node, "parameter_list")
    if param_list:
        return node_text(param_list, source)
    return "()"  # pragma: no cover - HLSL functions always have parameter_list


def _extract_hlsl_symbols(
    analyzer: "TreeSitterAnalyzer",
    rel_path: str, run_id: str, source: bytes, tree: "tree_sitter.Tree",
    symbols: list[Symbol], local_symbols: dict[str, Symbol],
    node_for_symbol: dict[str, "tree_sitter.Node"],
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
            param_list = find_child_by_type(func_decl, "parameter_list")
            meta = (
                {PARAMETERS_KEY: c_family_parameters(param_list, source)}
                if param_list is not None else None
            )
            sym = _make_symbol(analyzer, rel_path, run_id, source, node, func_name, "function",
                               signature=signature, meta=meta)
            symbols.append(sym)
            local_symbols[func_name] = sym
            node_for_symbol[sym.id] = node

        elif node.type == "struct_specifier":
            name_node = find_child_by_type(node, "type_identifier")
            if not name_node:
                continue  # pragma: no cover
            struct_name = node_text(name_node, source)
            sym = _make_symbol(analyzer, rel_path, run_id, source, node, struct_name, "struct")
            symbols.append(sym)
            node_for_symbol[sym.id] = node

        elif node.type == "declaration":
            name_node = find_child_by_type(node, "identifier")
            if not name_node:
                continue  # pragma: no cover - defensive
            var_name = node_text(name_node, source)
            sym = _make_symbol(analyzer, rel_path, run_id, source, node, var_name, "variable")
            symbols.append(sym)
            node_for_symbol[sym.id] = node


def _extract_hlsl_edges(
    rel_path: str,
    source: bytes,
    tree: "tree_sitter.Tree",
    decl_index: SymbolsAt,
    resolver: "NameResolver",
    run_id: str,
    edges: list[Edge],
) -> None:
    """Extract call edges from a tree-sitter tree (pass 2)."""
    for node in iter_tree(tree.root_node):
        if node.type == "call_expression":
            caller = _find_enclosing_function_hlsl(node, decl_index)
            if not caller:
                continue

            target_name = _get_call_target_name_hlsl(node, source)
            if not target_name:
                continue  # pragma: no cover - defensive for unknown call patterns

            # Try to resolve the callee
            result = resolver.lookup(target_name)
            # ADR-0039 ruling 6: resolver match quality interpolates in the
            # ast_call_direct band; an external target derives the
            # unresolved base (ADR-0037).
            resolution_score: float | None = None
            if result.symbol is not None:
                dst_id = result.symbol.id
                resolution_score = result.confidence
            else:
                dst_id = f"hlsl:external:{target_name}:function"

            edge = Edge.create(
                src=caller.id,
                dst=dst_id,
                edge_type="calls",
                line=node.start_point[0] + 1,
                resolution_score=resolution_score,
                is_resolved=resolution_score is not None,
                origin=PASS_ID,
                evidence_type="ast_call_direct",
                origin_run_id=run_id,
            )
            edges.append(edge)


class HlslAnalyzer(TreeSitterAnalyzer):
    """HLSL language analyzer using tree-sitter-language-pack."""

    lang = "hlsl"
    file_patterns: ClassVar[list[str]] = ["*.hlsl", "*.hlsli", "*.fx"]
    language_pack_name = "hlsl"

    def register_symbol(self, symbol: Symbol, global_symbols: dict[str, Symbol]) -> None:
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
            self, rel_path, run.execution_id, source, tree,
            analysis.symbols, local_symbols, analysis.node_for_symbol,
        )

        # Populate symbol_by_name for callable symbols (functions only)
        for sym in analysis.symbols:
            if sym.kind == "function":
                analysis.symbol_by_name[sym.name] = sym

        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict[str, Symbol],
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from HLSL."""
        edges: list[Edge] = []
        _extract_hlsl_edges(
            rel_path, source, tree, symbols_at(self.file_symbols(local_symbols)),
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
