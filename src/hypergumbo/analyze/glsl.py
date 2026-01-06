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
1. Check if tree-sitter-glsl is available
2. If not available, return skipped result (not an error)
3. Parse all .vert, .frag, .glsl, .geom, .tesc, .tese, .comp files
4. Extract functions, structs, uniforms, in/out variables
5. Create calls edges for function invocations

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-glsl package for grammar
- GLSL-specific: shaders, uniforms, in/out are first-class
- Useful for graphics programming analysis
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from .base import iter_tree
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "glsl-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# GLSL file extensions
GLSL_EXTENSIONS = ["*.vert", "*.frag", "*.glsl", "*.geom", "*.tesc", "*.tese", "*.comp"]


def find_glsl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all GLSL files in the repository."""
    yield from find_files(repo_root, GLSL_EXTENSIONS)


def is_glsl_tree_sitter_available() -> bool:
    """Check if tree-sitter with GLSL grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_glsl") is None:
        return False  # pragma: no cover
    return True


@dataclass
class GLSLAnalysisResult:
    """Result of analyzing GLSL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"glsl:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:
    """Generate deterministic edge ID."""
    content = f"{edge_type}:{src}:{dst}"
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_identifier(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract identifier from a node's children."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None  # pragma: no cover


def _get_type_identifier(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Get type identifier from struct_specifier."""
    for child in node.children:
        if child.type == "type_identifier":
            return _node_text(child, source)
    return None  # pragma: no cover


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


def _extract_glsl_signature(func_def: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a GLSL function_definition node.

    Returns signature in format: (type param1, type param2) return_type
    GLSL is C-like with typed parameters.
    """
    # Find function declarator (contains name and params)
    func_decl = _find_child_by_type(func_def, "function_declarator")
    if func_decl is None:  # pragma: no cover - always has declarator
        return None

    # Find parameter list
    params: list[str] = []
    param_list = _find_child_by_type(func_decl, "parameter_list")
    if param_list:
        for child in param_list.children:
            if child.type == "parameter_declaration":
                param_text = _node_text(child, source).strip()
                params.append(param_text)

    # Get return type (primitive_type or type_identifier before function_declarator)
    return_type: Optional[str] = None
    for child in func_def.children:
        if child.type in ("primitive_type", "type_identifier"):
            return_type = _node_text(child, source)
            break

    params_str = ", ".join(params) if params else ""
    signature = f"({params_str})"
    if return_type and return_type != "void":
        signature += f" {return_type}"

    return signature


def _find_enclosing_function(
    node: "tree_sitter.Node",
    source: bytes,
    function_registry: dict[str, str],
) -> Optional[str]:
    """Find the enclosing function's symbol ID by walking up parent nodes."""
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            for child in current.children:
                if child.type == "function_declarator":
                    func_name = _get_identifier(child, source)
                    if func_name:
                        return function_registry.get(func_name.lower())
        current = current.parent
    return None  # pragma: no cover - no enclosing function found


def _process_glsl_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    function_registry: dict[str, str],
) -> None:
    """Process GLSL AST tree to extract symbols and edges.

    Args:
        tree: Tree-sitter tree to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        function_registry: Registry mapping function names to symbol IDs
    """
    # First pass: collect all function definitions to build registry
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
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, func_name, "function")

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
                    signature=_extract_glsl_signature(node, source),
                )
                symbols.append(sym)
                function_registry[func_name.lower()] = symbol_id

        # Struct definitions
        elif node.type == "struct_specifier":
            struct_name = _get_type_identifier(node, source)
            if struct_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, struct_name, "struct")

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
                )
                symbols.append(sym)

        # Variable declarations (uniform, in, out)
        elif node.type == "declaration":
            text = _node_text(node, source).strip()
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
                    symbol_id = _make_symbol_id(rel_path, start_line, end_line, var_name, kind)

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
                    )
                    symbols.append(sym)

        # Function calls
        elif node.type == "call_expression":
            func_name = _get_identifier(node, source)
            current_function = _find_enclosing_function(node, source, function_registry)
            if func_name and current_function:
                start_line = node.start_point[0] + 1
                dst_id = function_registry.get(func_name.lower(), f"glsl:builtin:{func_name}")

                edge = Edge(
                    id=_make_edge_id(current_function, dst_id, "calls"),
                    src=current_function,
                    dst=dst_id,
                    edge_type="calls",
                    line=start_line,
                    confidence=0.90 if func_name.lower() in function_registry else 0.70,
                    origin=PASS_ID,
                    evidence_type="static",
                )
                edges.append(edge)


def analyze_glsl_files(repo_root: Path) -> GLSLAnalysisResult:
    """Analyze GLSL files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        GLSLAnalysisResult with symbols and edges
    """
    if not is_glsl_tree_sitter_available():  # pragma: no cover
        return GLSLAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-glsl not installed (pip install tree-sitter-glsl)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_glsl

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Function registry for cross-file resolution: name -> symbol_id
    function_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_glsl.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize GLSL parser: {e}")
        return GLSLAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    glsl_files = list(find_glsl_files(repo_root))

    for glsl_path in glsl_files:
        try:
            rel_path = str(glsl_path.relative_to(repo_root))
            source = glsl_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_glsl_tree(
                tree,
                source,
                rel_path,
                symbols,
                edges,
                function_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {glsl_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return GLSLAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
