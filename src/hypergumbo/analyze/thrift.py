"""Apache Thrift analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse .thrift files and extract:
- Service definitions (RPC services)
- Function definitions (RPC methods)
- Struct definitions
- Enum definitions
- Typedef definitions
- Const definitions
- Include relationships

If tree-sitter with Thrift support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter with Thrift grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .thrift files and extract symbols
4. Detect include statements and create import edges
5. Create contains edges from services to their functions

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Thrift grammar
- Thrift files define cross-language interfaces (similar to Proto/gRPC)
- Enables full-stack tracing for Thrift-based microservices

Thrift-Specific Considerations
-----------------------------
- Thrift supports multiple namespace declarations (one per target language)
- Services contain function definitions (RPC methods)
- Functions can have throws clauses for exceptions
- Structs are like Proto messages
- Typedefs provide type aliasing
- Constants are compile-time values
"""
from __future__ import annotations

import importlib.util
import time
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .base import iter_tree

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "thrift-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_thrift_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Thrift files in the repository."""
    yield from find_files(repo_root, ["*.thrift"])


def is_thrift_tree_sitter_available() -> bool:
    """Check if tree-sitter with Thrift grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language
        get_language("thrift")
        return True
    except Exception:  # pragma: no cover - thrift grammar not available
        return False


@dataclass
class ThriftAnalysisResult:
    """Result of analyzing Thrift files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"thrift:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Thrift file node (used as import edge source)."""
    return f"thrift:{path}:1-1:file:file"


def _make_edge_id() -> str:
    """Generate a unique edge ID."""
    return f"edge:thrift:{uuid.uuid4().hex[:12]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


def _extract_function_signature(func_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract function signature showing return type and parameters.

    Thrift function syntax:
        ReturnType functionName(1: Type param1, 2: Type param2)

    Returns signature like "(1: string userId) User".
    """
    return_type: Optional[str] = None
    params: Optional[str] = None

    for child in func_node.children:
        if child.type == "type":
            return_type = _node_text(child, source).strip()
        elif child.type == "parameters":
            params = _node_text(child, source).strip()

    sig = params or "()"
    if return_type and return_type != "void":
        sig += f" {return_type}"
    return sig


def _extract_namespace(root: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract first namespace from the thrift file.

    Thrift can have multiple namespace declarations for different languages.
    We use the first one found for canonical naming.
    """
    for node in iter_tree(root):
        if node.type == "namespace_declaration":
            # namespace_declaration structure:
            # "namespace" namespace_scope namespace namespace ...
            # The namespace path parts are in "namespace" type nodes
            namespace_parts = []
            for subchild in node.children:
                if subchild.type == "namespace":
                    part = _node_text(subchild, source).strip()
                    # First "namespace" is the keyword, skip it
                    if part != "namespace":
                        namespace_parts.append(part.lstrip("."))
            if namespace_parts:
                return ".".join(namespace_parts)
    return None


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract all symbols and edges from a parsed Thrift file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    root = tree.root_node
    namespace = _extract_namespace(root, source)

    def make_symbol(
        node: "tree_sitter.Node",
        name: str,
        kind: str,
        prefix: Optional[str] = None,
        signature: Optional[str] = None,
    ) -> Symbol:
        """Create a Symbol from a tree-sitter node."""
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        start_col = node.start_point[1]
        end_col = node.end_point[1]

        # Build canonical name with namespace prefix
        name_parts = []
        if namespace:
            name_parts.append(namespace)
        if prefix:
            name_parts.append(prefix)
        name_parts.append(name)
        canonical_name = ".".join(name_parts)

        span = Span(
            start_line=start_line,
            end_line=end_line,
            start_col=start_col,
            end_col=end_col,
        )
        sym_id = _make_symbol_id(file_path, start_line, end_line, name, kind)
        return Symbol(
            id=sym_id,
            name=name,
            canonical_name=canonical_name,
            kind=kind,
            language="thrift",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )

    # Track service symbols for contains edges - use byte range as key
    # because node.parent returns a new Python object each time
    service_by_pos: dict[tuple[int, int], Symbol] = {}

    # Process nodes using iterative traversal
    for node in iter_tree(root):
        if node.type == "service_definition":
            # Extract service name
            service_name_node = _find_child_by_type(node, "identifier")
            if service_name_node:
                service_name = _node_text(service_name_node, source).strip()
                service_sym = make_symbol(node, service_name, "service")
                symbols.append(service_sym)
                service_by_pos[(node.start_byte, node.end_byte)] = service_sym

        elif node.type == "function_definition":
            # Find containing service by walking up parents
            service_sym = _find_containing_service(node, service_by_pos)
            if service_sym:
                func_name_node = _find_child_by_type(node, "identifier")
                if func_name_node:
                    func_name = _node_text(func_name_node, source).strip()
                    func_sig = _extract_function_signature(node, source)
                    func_sym = make_symbol(
                        node, func_name, "function",
                        prefix=service_sym.name,
                        signature=func_sig
                    )
                    symbols.append(func_sym)

                    # Create contains edge from service to function
                    edges.append(Edge(
                        id=_make_edge_id(),
                        src=service_sym.id,
                        dst=func_sym.id,
                        edge_type="contains",
                        line=func_sym.span.start_line,
                    ))

        elif node.type == "struct_definition":
            struct_name_node = _find_child_by_type(node, "identifier")
            if struct_name_node:
                struct_name = _node_text(struct_name_node, source).strip()
                symbols.append(make_symbol(node, struct_name, "struct"))

        elif node.type == "enum_definition":
            enum_name_node = _find_child_by_type(node, "identifier")
            if enum_name_node:
                enum_name = _node_text(enum_name_node, source).strip()
                symbols.append(make_symbol(node, enum_name, "enum"))

        elif node.type == "typedef_definition":
            # typedef: typedef Type typedef_identifier
            typedef_id_node = _find_child_by_type(node, "typedef_identifier")
            if typedef_id_node:
                typedef_name = _node_text(typedef_id_node, source).strip()
                symbols.append(make_symbol(node, typedef_name, "typedef"))

        elif node.type == "const_definition":
            # const: const Type Name = value
            const_name_node = _find_child_by_type(node, "identifier")
            if const_name_node:
                const_name = _node_text(const_name_node, source).strip()
                symbols.append(make_symbol(node, const_name, "const"))

        elif node.type == "include_statement":
            # Extract include path
            for subchild in node.children:
                if subchild.type == "string":
                    include_path = _node_text(subchild, source).strip().strip('"')
                    edges.append(Edge(
                        id=_make_edge_id(),
                        src=_make_file_id(file_path),
                        dst=f"thrift:{include_path}:1-1:file:file",
                        edge_type="imports",
                        line=node.start_point[0] + 1,
                    ))

    return symbols, edges


def _find_containing_service(
    node: "tree_sitter.Node", service_by_pos: dict[tuple[int, int], Symbol]
) -> Optional[Symbol]:
    """Walk up parents to find the containing service definition."""
    current = node.parent
    while current is not None:
        pos_key = (current.start_byte, current.end_byte)
        if pos_key in service_by_pos:
            return service_by_pos[pos_key]
        current = current.parent  # pragma: no cover - loop continuation
    return None  # pragma: no cover - defensive


def analyze_thrift(repo_root: Path) -> ThriftAnalysisResult:
    """Analyze all Thrift files in the repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        ThriftAnalysisResult with symbols and edges found.
    """
    if not is_thrift_tree_sitter_available():
        warnings.warn("Thrift analysis skipped: tree-sitter-language-pack not available")
        return ThriftAnalysisResult(skipped=True, skip_reason="tree-sitter-language-pack not available")

    from tree_sitter_language_pack import get_parser

    parser = get_parser("thrift")
    run_id = f"uuid:{uuid.uuid4()}"
    start_time = time.time()
    files_analyzed = 0

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for file_path in find_thrift_files(repo_root):
        try:
            source = file_path.read_bytes()
            tree = parser.parse(source)

            rel_path = str(file_path.relative_to(repo_root))
            symbols, edges = _extract_symbols_and_edges(tree, source, rel_path, run_id)

            all_symbols.extend(symbols)
            all_edges.extend(edges)
            files_analyzed += 1

        except (OSError, IOError):  # pragma: no cover - defensive
            continue  # Skip files we can't read

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun(
        execution_id=run_id,
        pass_id=PASS_ID,
        version=PASS_VERSION,
        files_analyzed=files_analyzed,
        duration_ms=duration_ms,
    )

    return ThriftAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
