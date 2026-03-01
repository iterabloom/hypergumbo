"""GLSL shader analysis pass using tree-sitter-glsl.

This analyzer uses tree-sitter to parse OpenGL Shading Language files and extract:
- Shader functions (main and custom functions)
- Struct definitions
- Uniform variable declarations
- Input/output variable declarations (in/out)
- Function calls

If tree-sitter-glsl is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, structs, uniforms, in/out variables
2. Pass 2: Extract call edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the GLSL-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-glsl package for grammar (grammar_module)
- GLSL-specific: shaders, uniforms, in/out are first-class
- Useful for graphics programming analysis
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
    make_symbol_id,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = make_pass_id("glsl")

# GLSL file extensions
GLSL_EXTENSIONS = ["*.vert", "*.frag", "*.glsl", "*.geom", "*.tesc", "*.tese", "*.comp"]


def find_glsl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all GLSL files in the repository."""
    yield from find_files(repo_root, GLSL_EXTENSIONS)


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _get_identifier(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract identifier from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _get_type_identifier(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get type identifier from struct_specifier."""
    for child in node.children:
        if child.type == "type_identifier":
            return node_text(child, source)
    return None  # pragma: no cover


def _extract_glsl_signature(func_def: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a GLSL function_definition node.

    Returns signature in format: (type param1, type param2) return_type
    GLSL is C-like with typed parameters.
    """
    # Find function declarator (contains name and params)
    func_decl = find_child_by_type(func_def, "function_declarator")
    if func_decl is None:  # pragma: no cover - always has declarator
        return None

    # Find parameter list
    params: list[str] = []
    param_list = find_child_by_type(func_decl, "parameter_list")
    if param_list:
        for child in param_list.children:
            if child.type == "parameter_declaration":
                param_text = node_text(child, source).strip()
                params.append(param_text)

    # Get return type (primitive_type or type_identifier before function_declarator)
    return_type: Optional[str] = None
    for child in func_def.children:
        if child.type in ("primitive_type", "type_identifier"):
            return_type = node_text(child, source)
            break

    params_str = ", ".join(params) if params else ""
    signature = f"({params_str})"
    if return_type and return_type != "void":
        signature += f" {return_type}"

    return signature


def _find_enclosing_function_glsl(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing function Symbol by walking up parent nodes."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            for child in current.children:
                if child.type == "function_declarator":
                    func_name = _get_identifier(child, source)
                    if func_name:
                        return local_symbols.get(func_name.lower())
        current = current.parent
    return None  # pragma: no cover - no enclosing function found


def _extract_glsl_symbols(
    tree: "tree_sitter.Tree",
    source: bytes,
    rel_path: str,
    run_id: str,
    symbols: list[Symbol],
    local_symbols: dict[str, Symbol],
) -> None:
    """Extract symbols from GLSL AST tree (pass 1).

    Args:
        tree: Tree-sitter tree to process
        source: Source file bytes
        rel_path: Relative path to file
        run_id: The execution ID for provenance
        symbols: List to append symbols to
        local_symbols: Dict to register local function symbols for caller lookup
    """
    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            # Find function declarator to get the name
            func_name = None
            for child in node.children:
                if child.type == "function_declarator":
                    func_name = _get_identifier(child, source)
                    break

            if func_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("glsl", rel_path, start_line, end_line, func_name, "function")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=func_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="function",
                    name=func_name,
                    path=rel_path,
                    language="glsl",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=_extract_glsl_signature(node, source),
                )
                symbols.append(sym)
                local_symbols[func_name.lower()] = sym

        # Struct definitions
        elif node.type == "struct_specifier":
            struct_name = _get_type_identifier(node, source)
            if struct_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("glsl", rel_path, start_line, end_line, struct_name, "struct")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=struct_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="struct",
                    name=struct_name,
                    path=rel_path,
                    language="glsl",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                )
                symbols.append(sym)

        # Variable declarations (uniform, in, out)
        elif node.type == "declaration":
            text = node_text(node, source).strip()
            var_name = _get_identifier(node, source)

            if var_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Determine kind based on storage qualifier
                kind = "variable"
                if text.startswith("uniform"):
                    kind = "uniform"
                elif text.startswith("in "):
                    kind = "input"
                elif text.startswith("out "):
                    kind = "output"
                elif text.startswith("varying"):  # pragma: no cover - old GLSL syntax
                    kind = "varying"  # pragma: no cover - old GLSL syntax
                elif text.startswith("attribute"):  # pragma: no cover - old GLSL syntax
                    kind = "attribute"  # pragma: no cover - old GLSL syntax

                # Only create symbols for shader-specific declarations
                if kind in ("uniform", "input", "output", "varying", "attribute"):
                    symbol_id = make_symbol_id("glsl", rel_path, start_line, end_line, var_name, kind)

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=var_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind=kind,
                        name=var_name,
                        path=rel_path,
                        language="glsl",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        origin_run_id=run_id,
                    )
                    symbols.append(sym)


def _extract_glsl_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    local_symbols: dict[str, Symbol],
    edges: list[Edge],
    resolver: "NameResolver",
) -> None:
    """Extract call edges from GLSL AST tree (pass 2).

    Args:
        tree: Tree-sitter tree to process
        source: Source file bytes
        local_symbols: Dict of local function symbols for caller lookup
        edges: List to append edges to
        resolver: NameResolver for callee lookup
    """
    for node in iter_tree(tree.root_node):
        if node.type == "call_expression":
            func_name = _get_identifier(node, source)
            caller = _find_enclosing_function_glsl(node, source, local_symbols)
            if func_name and caller:
                start_line = node.start_point[0] + 1

                # Try to resolve the callee
                result = resolver.lookup(func_name.lower())
                if result.symbol is not None:
                    dst_id = result.symbol.id
                    confidence = 0.85 * result.confidence
                else:
                    dst_id = f"glsl:builtin:{func_name}"
                    confidence = 0.70

                edge = Edge.create(
                    src=caller.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    evidence_type="static",
                )
                edges.append(edge)


class GlslAnalyzer(TreeSitterAnalyzer):
    """GLSL language analyzer using tree-sitter-glsl."""

    lang = "glsl"
    file_patterns: ClassVar[list[str]] = GLSL_EXTENSIONS
    grammar_module = "tree_sitter_glsl"
    create_file_symbols = False

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract functions, structs, uniforms, in/out from a GLSL file."""
        analysis = FileAnalysis()

        _extract_glsl_symbols(
            tree, source, rel_path, run.execution_id,
            analysis.symbols, analysis.symbol_by_name,
        )

        return analysis

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol globally; functions indexed by lowercase name."""
        if symbol.kind == "function":
            global_symbols[symbol.name.lower()] = symbol
        else:
            global_symbols[symbol.name] = symbol

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract call edges from a GLSL file."""
        edges: list[Edge] = []
        _extract_glsl_edges(tree, source, local_symbols, edges, resolver)
        return edges


_analyzer = GlslAnalyzer()


def is_glsl_tree_sitter_available() -> bool:
    """Check if tree-sitter with GLSL grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("glsl")
def analyze_glsl_files(repo_root: Path) -> AnalysisResult:
    """Analyze GLSL files in the repository."""
    return _analyzer.analyze(repo_root)
