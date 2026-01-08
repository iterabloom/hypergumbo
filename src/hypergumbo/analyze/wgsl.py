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
1. Check if tree-sitter-wgsl is available
2. If not available, return skipped result (not an error)
3. Parse all .wgsl files
4. Extract functions, structs, bindings, global variables
5. Create calls edges for function invocations
6. Mark entry points (vertex, fragment, compute) in meta

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-wgsl package for grammar
- WGSL-specific: shader entry points, bindings are first-class
- Useful for WebGPU graphics and compute analysis
- Complements GLSL analyzer for shader coverage
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

PASS_ID = "wgsl-v1"
PASS_VERSION = "hypergumbo-0.1.0"

# WGSL file extensions
WGSL_EXTENSIONS = ["*.wgsl"]


def find_wgsl_files(repo_root: Path) -> Iterator[Path]:
    """Yield all WGSL files in the repository."""
    yield from find_files(repo_root, WGSL_EXTENSIONS)


def is_wgsl_tree_sitter_available() -> bool:
    """Check if tree-sitter with WGSL grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    # Try tree_sitter_language_pack first (bundled languages)
    if importlib.util.find_spec("tree_sitter_language_pack") is not None:
        try:
            from tree_sitter_language_pack import get_language
            get_language("wgsl")
            return True
        except Exception:  # pragma: no cover
            pass  # pragma: no cover
    # Fall back to standalone tree_sitter_wgsl
    if importlib.util.find_spec("tree_sitter_wgsl") is not None:  # pragma: no cover
        return True  # pragma: no cover
    return False  # pragma: no cover


@dataclass
class WGSLAnalysisResult:
    """Result of analyzing WGSL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"wgsl:{path}:{start_line}-{end_line}:{name}:{kind}"


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
        if child.type == "identifier" or child.type == "ident":
            return _node_text(child, source)
    return None  # pragma: no cover


def _detect_entry_point(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Detect WGSL entry point attributes (@vertex, @fragment, @compute).

    Returns the entry point type (vertex, fragment, compute) or None if not an entry point.

    In the WGSL tree-sitter grammar, attributes are children of the function_declaration.
    """
    # Look for attribute children of the function
    for child in node.children:
        if child.type == "attribute":
            attr_text = _node_text(child, source).strip()
            if "@vertex" in attr_text:
                return "vertex"
            if "@fragment" in attr_text:
                return "fragment"
            if "@compute" in attr_text:
                return "compute"
    return None


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


def _extract_wgsl_signature(func_decl: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract function signature from a WGSL function_declaration node.

    Returns signature in format: (x: type, y: type) -> ReturnType
    WGSL is Rust-like with typed parameters.
    """
    # Find parameter list
    params: list[str] = []
    param_list = _find_child_by_type(func_decl, "parameter_list")

    if param_list:
        for child in param_list.children:
            if child.type == "parameter":
                # Extract param text (name: type)
                param_text = _node_text(child, source).strip()
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
    return_decl = _find_child_by_type(func_decl, "function_return_type_declaration")
    if return_decl:
        type_decl = _find_child_by_type(return_decl, "type_declaration")
        if type_decl:
            return_type = _node_text(type_decl, source)

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
            attr_text = _node_text(child, source).strip()
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


def _find_containing_function(
    node: "tree_sitter.Node", function_by_pos: dict[tuple[int, int], str]
) -> Optional[str]:
    """Walk up parents to find the containing function's symbol ID."""
    current = node.parent
    while current is not None:
        pos_key = (current.start_byte, current.end_byte)
        if pos_key in function_by_pos:
            return function_by_pos[pos_key]
        current = current.parent
    return None  # pragma: no cover - defensive


def _process_wgsl_tree(
    root: "tree_sitter.Node",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    function_registry: dict[str, str],
) -> None:
    """Process WGSL AST tree to extract symbols and edges.

    Args:
        root: Root tree-sitter node to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        function_registry: Registry mapping function names to symbol IDs
    """
    # Track function nodes by byte position for parent walking
    # (node.parent returns new Python object, so id() doesn't work)
    function_by_pos: dict[tuple[int, int], str] = {}

    for node in iter_tree(root):
        # Function definitions (fn name(...) { ... })
        if node.type == "function_declaration":
            func_name = None
            # WGSL function structure: fn identifier ...
            for child in node.children:
                if child.type in ("identifier", "ident"):
                    func_name = _node_text(child, source)
                    break

            if func_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, func_name, "function")

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
                function_registry[func_name.lower()] = symbol_id
                function_by_pos[(node.start_byte, node.end_byte)] = symbol_id

        # Struct definitions (struct Name { ... })
        elif node.type == "struct_declaration":
            struct_name = _get_identifier(node, source)
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
                text = _node_text(node, source).strip()
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
                    symbol_id = _make_symbol_id(rel_path, start_line, end_line, var_name, kind)

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

        # Function calls (WGSL uses type_constructor_or_function_call_expression)
        elif node.type == "type_constructor_or_function_call_expression":
            # Find containing function by walking up parents
            current_function = _find_containing_function(node, function_by_pos)
            # Extract function name from type_declaration child
            func_name = None
            for child in node.children:
                if child.type == "type_declaration":
                    func_name = _get_identifier(child, source)
                    break
            if func_name and current_function:
                start_line = node.start_point[0] + 1
                dst_id = function_registry.get(func_name.lower(), f"wgsl:builtin:{func_name}")

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


def analyze_wgsl_files(repo_root: Path) -> WGSLAnalysisResult:
    """Analyze WGSL files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        WGSLAnalysisResult with symbols and edges
    """
    if not is_wgsl_tree_sitter_available():  # pragma: no cover
        return WGSLAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-wgsl not installed (pip install tree-sitter-wgsl or tree-sitter-language-pack)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Function registry for cross-file resolution: name -> symbol_id
    function_registry: dict[str, str] = {}

    # Create parser - try language pack first, then standalone
    try:
        try:
            from tree_sitter_language_pack import get_language
            wgsl_lang = get_language("wgsl")
            parser = tree_sitter.Parser(wgsl_lang)
        except Exception:  # pragma: no cover - language pack available
            import tree_sitter_wgsl  # pragma: no cover
            parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_wgsl.language()))  # pragma: no cover
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize WGSL parser: {e}")
        return WGSLAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    wgsl_files = list(find_wgsl_files(repo_root))

    for wgsl_path in wgsl_files:
        try:
            rel_path = str(wgsl_path.relative_to(repo_root))
            source = wgsl_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file using iterative traversal
            _process_wgsl_tree(
                tree.root_node,
                source,
                rel_path,
                symbols,
                edges,
                function_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {wgsl_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return WGSLAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )


# Convenience alias
analyze_wgsl = analyze_wgsl_files
