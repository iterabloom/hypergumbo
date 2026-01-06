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

import importlib.util
import time
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from .base import iter_tree
from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "hlsl-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_hlsl_files(repo_root: Path) -> Iterator[Path]:
    """Find all HLSL files in the repository."""
    yield from find_files(repo_root, ["*.hlsl", "*.hlsli", "*.fx"])


@dataclass
class HLSLAnalysisResult:
    """Result of analyzing HLSL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def is_hlsl_tree_sitter_available() -> bool:
    """Check if tree-sitter-hlsl is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("hlsl")
        return True
    except Exception:  # pragma: no cover - hlsl grammar not available
        return False


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(
    node: "tree_sitter.Node", child_type: str
) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None  # pragma: no cover - defensive


@dataclass
class _FileContext:
    """Context for processing a single file."""

    source: bytes
    rel_path: str
    run_id: str
    symbols: list[Symbol]
    edges: list[Edge]


def _make_symbol(ctx: _FileContext, node: "tree_sitter.Node", name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"hlsl:{ctx.rel_path}:{start_line}-{end_line}:{name}:{kind}"
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
        path=ctx.rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=ctx.run_id,
        stable_id=f"hlsl:{ctx.rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _extract_function_signature(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature from a function_declarator node."""
    param_list = _find_child_by_type(node, "parameter_list")
    if param_list:
        return _node_text(param_list, source)
    return "()"  # pragma: no cover - HLSL functions always have parameter_list


def _process_function(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function definition."""
    # Get function name from function_declarator > identifier
    func_decl = _find_child_by_type(node, "function_declarator")
    if not func_decl:
        return  # pragma: no cover

    name_node = _find_child_by_type(func_decl, "identifier")
    if not name_node:
        return  # pragma: no cover

    func_name = _node_text(name_node, ctx.source)
    signature = _extract_function_signature(func_decl, ctx.source)

    ctx.symbols.append(_make_symbol(ctx, node, func_name, "function", signature=signature))


def _process_struct(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a struct definition."""
    name_node = _find_child_by_type(node, "type_identifier")
    if not name_node:
        return  # pragma: no cover

    struct_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, struct_name, "struct"))


def _process_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a declaration (variable, cbuffer, resource).

    Handles:
    - Regular variables: Texture2D diffuseTexture;
    - cbuffer declarations: cbuffer Name : register(b0) { ... }
    - Sampler declarations: SamplerState linearSampler;

    The tree-sitter-hlsl parser exposes the identifier directly as a child node,
    so we find it with _find_child_by_type(node, "identifier").
    """
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    var_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, var_name, "variable"))


def _process_tree(ctx: _FileContext, tree: "tree_sitter.Tree") -> None:
    """Process all nodes in a tree-sitter tree."""
    for node in iter_tree(tree.root_node):
        if node.type == "function_definition":
            _process_function(ctx, node)
        elif node.type == "struct_specifier":
            _process_struct(ctx, node)
        elif node.type == "declaration":
            _process_declaration(ctx, node)


def analyze_hlsl(repo_root: Path) -> HLSLAnalysisResult:
    """Analyze HLSL files in a repository.

    Returns a HLSLAnalysisResult with symbols for functions, structs, and variables.
    """
    if not is_hlsl_tree_sitter_available():
        warnings.warn("HLSL analysis skipped: tree-sitter-hlsl unavailable")
        return HLSLAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-hlsl unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("hlsl")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = str(uuid.uuid4())
    start_time = time.time()

    for file_path in find_hlsl_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1

        rel_path = str(file_path.relative_to(repo_root))

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            run_id=run_id,
            symbols=symbols,
            edges=edges,
        )

        _process_tree(ctx, tree)

    duration_ms = int((time.time() - start_time) * 1000)
    return HLSLAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=AnalysisRun(
            execution_id=run_id,
            pass_id=PASS_ID,
            version=PASS_VERSION,
            files_analyzed=files_analyzed,
            duration_ms=duration_ms,
        ),
    )
