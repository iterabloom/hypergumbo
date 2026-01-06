"""D language analysis pass using tree-sitter.

Detects:
- Module declarations
- Import statements
- Function definitions
- Struct definitions
- Class definitions
- Interface definitions

D is a systems programming language that combines low-level control
with modern features like garbage collection, closures, and ranges.
The tree-sitter-d parser handles .d and .di (interface) files.

How It Works
------------
1. Check if tree-sitter with D grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .d and .di files
4. Extract module declarations
5. Extract function definitions with signatures
6. Extract struct, class, and interface definitions
7. Track import statements as edges

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for D grammar
- D is used for systems programming as a modern C++ alternative
- Supports both source (.d) and interface (.di) files
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

PASS_ID = "d-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_d_files(repo_root: Path) -> Iterator[Path]:
    """Find all D language files in the repository."""
    yield from find_files(repo_root, ["*.d", "*.di"])


@dataclass
class DAnalysisResult:
    """Result of analyzing D files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def is_d_tree_sitter_available() -> bool:
    """Check if tree-sitter-d is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language

        get_language("d")
        return True
    except Exception:  # pragma: no cover - d grammar not available
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
    file_stable_id: str
    run_id: str
    symbols: list[Symbol]
    edges: list[Edge]


def _make_symbol(ctx: _FileContext, node: "tree_sitter.Node", name: str, kind: str,
                 signature: Optional[str] = None, meta: Optional[dict] = None) -> Symbol:
    """Create a Symbol with consistent formatting."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    sym_id = f"d:{ctx.rel_path}:{start_line}-{end_line}:{name}:{kind}"
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
        language="d",
        path=ctx.rel_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=ctx.run_id,
        stable_id=f"d:{ctx.rel_path}:{name}",
        signature=signature,
        meta=meta,
    )


def _process_module_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a module declaration."""
    # module_fqn contains the module name
    fqn = _find_child_by_type(node, "module_fqn")
    if not fqn:
        return  # pragma: no cover - defensive

    mod_name = _node_text(fqn, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, mod_name, "module"))


def _process_import_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process an import declaration."""
    # Find the imported module
    imported = _find_child_by_type(node, "imported")
    if not imported:
        return  # pragma: no cover - defensive

    fqn = _find_child_by_type(imported, "module_fqn")
    if not fqn:
        return  # pragma: no cover - defensive

    import_name = _node_text(fqn, ctx.source)
    ctx.edges.append(
        Edge(
            id=f"edge:d:{uuid.uuid4().hex[:12]}",
            src=ctx.file_stable_id,
            dst=f"d:?:{import_name}:module",
            edge_type="imports",
            line=node.start_point[0] + 1,
            confidence=0.9,
            origin=PASS_ID,
            origin_run_id=ctx.run_id,
        )
    )


def _process_function_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a function declaration."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    func_name = _node_text(name_node, ctx.source)

    # Get parameters for signature
    params = _find_child_by_type(node, "parameters")
    signature = _node_text(params, ctx.source) if params else "()"

    ctx.symbols.append(_make_symbol(ctx, node, func_name, "function", signature=signature))


def _process_struct_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a struct declaration."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    struct_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, struct_name, "struct"))


def _process_class_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process a class declaration."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    class_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, class_name, "class"))


def _process_interface_declaration(ctx: _FileContext, node: "tree_sitter.Node") -> None:
    """Process an interface declaration."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return  # pragma: no cover - defensive

    iface_name = _node_text(name_node, ctx.source)
    ctx.symbols.append(_make_symbol(ctx, node, iface_name, "interface"))


def _process_tree(ctx: _FileContext, tree: "tree_sitter.Tree") -> None:
    """Process a tree-sitter tree iteratively."""
    for node in iter_tree(tree.root_node):
        if node.type == "module_declaration":
            _process_module_declaration(ctx, node)
        elif node.type == "import_declaration":
            _process_import_declaration(ctx, node)
        elif node.type == "function_declaration":
            _process_function_declaration(ctx, node)
        elif node.type == "struct_declaration":
            _process_struct_declaration(ctx, node)
        elif node.type == "class_declaration":
            _process_class_declaration(ctx, node)
        elif node.type == "interface_declaration":
            _process_interface_declaration(ctx, node)


def analyze_d(repo_root: Path) -> DAnalysisResult:
    """Analyze D language files in a repository.

    Returns a DAnalysisResult with symbols for modules, functions, structs,
    classes, and interfaces, plus edges for imports.
    """
    if not is_d_tree_sitter_available():
        warnings.warn("D analysis skipped: tree-sitter-d unavailable")
        return DAnalysisResult(
            skipped=True,
            skip_reason="tree-sitter-d unavailable",
        )

    from tree_sitter_language_pack import get_parser

    parser = get_parser("d")

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    run_id = str(uuid.uuid4())
    start_time = time.time()

    for file_path in find_d_files(repo_root):
        try:
            source = file_path.read_bytes()
        except (OSError, IOError):  # pragma: no cover
            continue

        tree = parser.parse(source)
        files_analyzed += 1

        rel_path = str(file_path.relative_to(repo_root))
        file_stable_id = f"d:{rel_path}:file:"

        ctx = _FileContext(
            source=source,
            rel_path=rel_path,
            file_stable_id=file_stable_id,
            run_id=run_id,
            symbols=symbols,
            edges=edges,
        )

        _process_tree(ctx, tree)

    duration_ms = int((time.time() - start_time) * 1000)
    return DAnalysisResult(
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
