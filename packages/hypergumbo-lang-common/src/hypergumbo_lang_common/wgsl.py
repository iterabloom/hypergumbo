"""WGSL (WebGPU Shading Language) analysis pass using tree-sitter-wgsl.

This analyzer uses tree-sitter to parse WebGPU Shading Language files and extract:
- Shader functions (entry points marked with @vertex, @fragment, @compute)
- Struct definitions
- Uniform/storage buffer bindings (@group/@binding)
- Global variable declarations
- Function calls

If tree-sitter-wgsl is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract functions, structs, uniforms, storage bindings
2. Pass 2: Extract call edges using NameResolver

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the WGSL-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-wgsl package for grammar (via language_pack_name)
- WGSL-specific: shader entry points, bindings are first-class
- Useful for WebGPU graphics and compute analysis
- Complements GLSL analyzer for shader coverage
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
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver

PASS_ID = "wgsl-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# WGSL file extensions
WGSL_EXTENSIONS = ["*.wgsl"]


def find_wgsl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all WGSL files in the repository."""
    yield from find_files(repo_root, WGSL_EXTENSIONS)


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _get_identifier(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract identifier from a node's children."""
    for child in node.children:
        if child.type == "identifier" or child.type == "ident":
            return node_text(child, source)
    return None  # pragma: no cover


def _detect_entry_point(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Detect WGSL entry point attributes (@vertex, @fragment, @compute).

    Returns the entry point type (vertex, fragment, compute) or None if not an entry point.

    In the WGSL tree-sitter grammar, attributes are children of the function_declaration.
    """
    # Look for attribute children of the function
    for child in node.children:
        if child.type == "attribute":
            attr_text = node_text(child, source).strip()
            if "@vertex" in attr_text:
                return "vertex"
            if "@fragment" in attr_text:
                return "fragment"
            if "@compute" in attr_text:
                return "compute"
    return None


def _extract_wgsl_signature(func_decl: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a WGSL function_declaration node.

    Returns signature in format: (x: type, y: type) -> ReturnType
    WGSL is Rust-like with typed parameters.
    """
    # Find parameter list
    params: list[str] = []
    param_list = find_child_by_type(func_decl, "parameter_list")

    if param_list:
        for child in param_list.children:
            if child.type == "parameter":
                # Extract param text (name: type)
                param_text = node_text(child, source).strip()
                # Remove attributes like @builtin(vertex_index)
                if "@" not in param_text:
                    params.append(param_text)
                else:  # pragma: no cover - attribute parameters
                    # Extract just name: type part after attributes
                    parts = param_text.split(")")
                    if len(parts) > 1:
                        params.append(parts[-1].strip())

    # Find return type from function_return_type_declaration
    return_type: Optional[str] = None
    return_decl = find_child_by_type(func_decl, "function_return_type_declaration")
    if return_decl:
        type_decl = find_child_by_type(return_decl, "type_declaration")
        if type_decl:
            return_type = node_text(type_decl, source)

    params_str = ", ".join(params) if params else ""
    signature = f"({params_str})"
    if return_type:
        signature += f" -> {return_type}"

    return signature


def _detect_binding(node: "tree_sitter.Node", source: bytes) -> Optional[dict]:
    """Detect WGSL binding attributes (@group/@binding).

    Returns a dict with group and binding numbers, or None if not a binding.

    In the WGSL tree-sitter grammar, attributes are children of the variable declaration.
    """
    import re

    binding_info: dict = {}

    # Check children for @group and @binding attributes
    for child in node.children:
        if child.type == "attribute":
            attr_text = node_text(child, source).strip()
            if "@group" in attr_text:
                # Extract number from @group(N)
                match = re.search(r"@group\s*\(\s*(\d+)\s*\)", attr_text)
                if match:
                    binding_info["group"] = int(match.group(1))
            if "@binding" in attr_text:
                match = re.search(r"@binding\s*\(\s*(\d+)\s*\)", attr_text)
                if match:
                    binding_info["binding"] = int(match.group(1))

    if binding_info:
        return binding_info
    return None  # pragma: no cover - no bindings found


def _find_enclosing_function_wgsl(
    node: "tree_sitter.Node",
    source: bytes,
    local_symbols: dict[str, Symbol],
) -> Optional[Symbol]:
    """Find the enclosing function Symbol by walking up parent nodes.

    Walks up the tree to find a function_declaration, extracts its name,
    and looks it up in the local_symbols dict (keyed by lowercase name).
    """
    current = node.parent
    while current is not None:
        if current.type == "function_declaration":
            for child in current.children:
                if child.type in ("identifier", "ident"):
                    func_name = node_text(child, source)
                    if func_name:
                        return local_symbols.get(func_name.lower())
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_wgsl_symbols(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    symbol_by_name: dict[str, Symbol],
) -> None:
    """Extract symbols from WGSL AST tree (pass 1).

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        symbol_by_name: Dict to track symbols by lowercase name for caller lookup
    """
    for node in iter_tree(root):
        # Function definitions (fn name(...) { ... })
        if node.type == "function_declaration":
            func_name = None
            # WGSL function structure: fn identifier ...
            for child in node.children:
                if child.type in ("identifier", "ident"):
                    func_name = node_text(child, source)
                    break

            if func_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("wgsl", rel_path, start_line, end_line, func_name, "function")

                # Check for entry point attributes
                entry_type = _detect_entry_point(node, source)
                meta: Optional[dict] = None
                if entry_type:
                    meta = {"entry_point": entry_type}

                sym = Symbol(
                    id=symbol_id,
                    stable_id=entry_type,  # Set stable_id to entry point type
                    shape_id=None,
                    canonical_name=func_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="function",
                    name=func_name,
                    path=rel_path,
                    language="wgsl",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    meta=meta,
                    signature=_extract_wgsl_signature(node, source),
                )
                symbols.append(sym)
                symbol_by_name[func_name.lower()] = sym

        # Struct definitions (struct Name { ... })
        elif node.type == "struct_declaration":
            struct_name = _get_identifier(node, source)
            if struct_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("wgsl", rel_path, start_line, end_line, struct_name, "struct")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=struct_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="struct",
                    name=struct_name,
                    path=rel_path,
                    language="wgsl",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

        # Global variable declarations (var<...> name: Type)
        elif node.type == "global_variable_declaration":
            # Variable name is nested: global_variable_declaration > variable_declaration
            #   > variable_identifier_declaration > identifier
            var_name = None
            for child in node.children:
                if child.type == "variable_declaration":
                    for grandchild in child.children:
                        if grandchild.type == "variable_identifier_declaration":
                            var_name = _get_identifier(grandchild, source)
                            break
            if var_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Check for binding attributes
                binding_info = _detect_binding(node, source)

                # Determine kind based on variable storage
                # Note: storage buffers may have access mode like var<storage, read_write>
                # so we check for patterns without the closing >
                text = node_text(node, source).strip()
                kind = "variable"
                if "var<uniform" in text:
                    kind = "uniform"
                elif "var<storage" in text:
                    kind = "storage"
                elif "var<private" in text:  # pragma: no cover - not extracted
                    kind = "private"  # pragma: no cover - not extracted
                elif "var<workgroup" in text:  # pragma: no cover - not extracted
                    kind = "workgroup"  # pragma: no cover - not extracted

                # Only create symbols for shader-specific declarations
                if kind in ("uniform", "storage") or binding_info:
                    symbol_id = make_symbol_id("wgsl", rel_path, start_line, end_line, var_name, kind)

                    meta_dict: Optional[dict] = None
                    if binding_info:
                        meta_dict = binding_info

                    sym = Symbol(
                        id=symbol_id,
                        stable_id=None,
                        shape_id=None,
                        canonical_name=var_name,
                        fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                        kind=kind,
                        name=var_name,
                        path=rel_path,
                        language="wgsl",
                        span=Span(
                            start_line=start_line,
                            end_line=end_line,
                            start_col=node.start_point[1],
                            end_col=node.end_point[1],
                        ),
                        origin=PASS_ID,
                        meta=meta_dict,
                    )
                    symbols.append(sym)


def _extract_wgsl_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    local_symbols: dict[str, Symbol],
    edges: list[Edge],
    resolver: "NameResolver",
) -> None:
    """Extract call edges from WGSL AST tree (pass 2).

    Args:
        tree: Tree-sitter tree to process
        source: Source file bytes
        local_symbols: Dict of local function symbols for caller lookup (keyed by lowercase name)
        edges: List to append edges to
        resolver: NameResolver for callee lookup
    """
    for node in iter_tree(tree.root_node):
        # Function calls (WGSL uses type_constructor_or_function_call_expression)
        if node.type == "type_constructor_or_function_call_expression":
            # Find containing function by walking up parents
            caller = _find_enclosing_function_wgsl(node, source, local_symbols)
            # Extract function name from type_declaration child
            func_name = None
            for child in node.children:
                if child.type == "type_declaration":
                    func_name = _get_identifier(child, source)
                    break
            if func_name and caller:
                start_line = node.start_point[0] + 1

                # Try to resolve the callee
                result = resolver.lookup(func_name.lower())
                if result.symbol is not None:
                    dst_id = result.symbol.id
                    confidence = 0.85 * result.confidence
                else:
                    dst_id = f"wgsl:builtin:{func_name}"
                    confidence = 0.70

                edge = Edge(
                    id=_make_edge_id(caller.id, dst_id, "calls"),
                    src=caller.id,
                    dst=dst_id,
                    edge_type="calls",
                    line=start_line,
                    confidence=confidence,
                    origin=PASS_ID,
                    evidence_type="static",
                )
                edges.append(edge)


class WgslAnalyzer(TreeSitterAnalyzer):
    """WGSL language analyzer using tree-sitter-wgsl via language pack."""

    lang = "wgsl"
    pass_id = PASS_ID
    pass_version = PASS_VERSION
    file_patterns: ClassVar[list[str]] = WGSL_EXTENSIONS
    language_pack_name = "wgsl"
    create_file_symbols = False

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract functions, structs, uniforms, bindings from a WGSL file."""
        analysis = FileAnalysis()

        _extract_wgsl_symbols(
            tree.root_node, source, rel_path,
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
        """Extract call edges from a WGSL file."""
        edges: list[Edge] = []
        _extract_wgsl_edges(tree, source, local_symbols, edges, resolver)
        return edges


_analyzer = WgslAnalyzer()


def is_wgsl_tree_sitter_available() -> bool:
    """Check if tree-sitter with WGSL grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("wgsl")
def analyze_wgsl_files(repo_root: Path) -> AnalysisResult:
    """Analyze WGSL files in the repository.

    Uses two-pass analysis for cross-file call resolution.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult with symbols and edges
    """
    return _analyzer.analyze(repo_root)


# Convenience alias
analyze_wgsl = analyze_wgsl_files
