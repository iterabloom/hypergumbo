"""Protocol Buffers (Proto) analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse .proto files and extract:
- Service declarations (gRPC services)
- RPC method declarations with request/response types
- Message declarations
- Enum declarations
- Import relationships

If tree-sitter with Proto support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter with Proto grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .proto files and extract symbols
4. Detect import statements and create import edges
5. Create contains edges from services to their RPC methods

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Proto grammar
- Proto files define cross-language interfaces (gRPC)
- Complements the gRPC linker for full stack tracing

Proto-Specific Considerations
-----------------------------
- Proto files define the interface for gRPC services
- Services contain RPC methods with request/response message types
- Messages can be nested
- Imports reference other .proto files (including google/protobuf/)
- Package declarations scope the canonical names
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

PASS_ID = "proto-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_proto_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Proto files in the repository."""
    yield from find_files(repo_root, ["*.proto"])


def is_proto_tree_sitter_available() -> bool:
    """Check if tree-sitter with Proto grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language
        get_language("proto")
        return True
    except Exception:  # pragma: no cover - proto grammar not available
        return False


@dataclass
class ProtoAnalysisResult:
    """Result of analyzing Proto files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"proto:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Proto file node (used as import edge source)."""
    return f"proto:{path}:1-1:file:file"


def _make_edge_id() -> str:
    """Generate a unique edge ID."""
    return f"edge:proto:{uuid.uuid4().hex[:12]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


def _extract_rpc_signature(rpc_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract RPC signature showing request/response types.

    Proto RPC syntax:
        rpc GetUser(GetUserRequest) returns (GetUserResponse);
        rpc ListUsers(ListUsersRequest) returns (stream User);

    Returns signature like "(GetUserRequest) returns (GetUserResponse)"
    or "(ListUsersRequest) returns (stream User)" for streaming.
    """
    request_type: Optional[str] = None
    response_type: Optional[str] = None
    request_stream = False
    response_stream = False

    # Parse the RPC node structure
    in_request = False
    in_response = False

    for child in rpc_node.children:
        if child.type == "(":
            if request_type is None:
                in_request = True
            else:
                in_response = True
        elif child.type == ")":
            in_request = False
            in_response = False
        elif child.type == "stream":
            if in_request:
                request_stream = True
            elif in_response or (request_type is not None and response_type is None):
                response_stream = True
        elif child.type == "message_or_enum_type":
            type_text = _node_text(child, source).strip()
            if in_request:
                request_type = type_text
            elif in_response or (request_type is not None and response_type is None):
                response_type = type_text

    # Build signature
    req = f"stream {request_type}" if request_stream else request_type or ""
    resp = f"stream {response_type}" if response_stream else response_type or ""

    return f"({req}) returns ({resp})"


def _extract_package_name(root: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract package name from the proto file."""
    for child in root.children:
        if child.type == "package":
            full_ident = _find_child_by_type(child, "full_ident")
            if full_ident:
                return _node_text(full_ident, source).strip()
    return None


def _make_proto_symbol(
    file_path: str,
    run_id: str,
    package_name: Optional[str],
    node: "tree_sitter.Node",
    source: bytes,
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

    # Build canonical name with package prefix
    name_parts = []
    if package_name:
        name_parts.append(package_name)
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
        language="proto",
        path=file_path,
        span=span,
        origin=PASS_ID,
        origin_run_id=run_id,
        signature=signature,
    )


def _get_parent_message_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Walk up parents to find enclosing message name for nested messages/enums."""
    current = node.parent
    while current:
        if current.type == "message_body":
            # The message_body's parent should be the message
            msg = current.parent
            if msg and msg.type == "message":
                name_node = _find_child_by_type(msg, "message_name")
                if name_node:
                    return _node_text(name_node, source).strip()
        current = current.parent
    return None  # pragma: no cover - defensive


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract all symbols and edges from a parsed Proto file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    root = tree.root_node
    package_name = _extract_package_name(root, source)

    # Track service symbols for creating contains edges
    service_symbols: dict[str, Symbol] = {}

    for node in iter_tree(root):
        if node.type == "service":
            # Extract service name
            service_name_node = _find_child_by_type(node, "service_name")
            if service_name_node:
                service_name = _node_text(service_name_node, source).strip()
                service_sym = _make_proto_symbol(
                    file_path, run_id, package_name, node, source,
                    service_name, "service"
                )
                symbols.append(service_sym)
                service_symbols[service_name] = service_sym

        elif node.type == "rpc":
            # Extract RPC - need to find parent service
            rpc_name_node = _find_child_by_type(node, "rpc_name")
            if rpc_name_node:
                rpc_name = _node_text(rpc_name_node, source).strip()
                rpc_sig = _extract_rpc_signature(node, source)

                # Find parent service name
                service_name = None
                current = node.parent
                while current:
                    if current.type == "service":
                        svc_name_node = _find_child_by_type(current, "service_name")
                        if svc_name_node:
                            service_name = _node_text(svc_name_node, source).strip()
                        break
                    current = current.parent  # pragma: no cover - defensive

                rpc_sym = _make_proto_symbol(
                    file_path, run_id, package_name, node, source,
                    rpc_name, "rpc",
                    prefix=service_name,
                    signature=rpc_sig
                )
                symbols.append(rpc_sym)

                # Create contains edge from service to rpc
                if service_name and service_name in service_symbols:
                    edges.append(Edge(
                        id=_make_edge_id(),
                        src=service_symbols[service_name].id,
                        dst=rpc_sym.id,
                        edge_type="contains",
                        line=rpc_sym.span.start_line,
                    ))

        elif node.type == "message":
            # Extract message name
            message_name_node = _find_child_by_type(node, "message_name")
            if message_name_node:
                message_name = _node_text(message_name_node, source).strip()
                parent_name = _get_parent_message_name(node, source)
                message_sym = _make_proto_symbol(
                    file_path, run_id, package_name, node, source,
                    message_name, "message", prefix=parent_name
                )
                symbols.append(message_sym)

        elif node.type == "enum":
            # Extract enum name
            enum_name_node = _find_child_by_type(node, "enum_name")
            if enum_name_node:
                enum_name = _node_text(enum_name_node, source).strip()
                parent_name = _get_parent_message_name(node, source)
                enum_sym = _make_proto_symbol(
                    file_path, run_id, package_name, node, source,
                    enum_name, "enum", prefix=parent_name
                )
                symbols.append(enum_sym)

        elif node.type == "import":
            # Extract import path
            import_string = _find_child_by_type(node, "string")
            if import_string:
                import_path = _node_text(import_string, source).strip().strip('"')
                # Create import edge from this file to the imported file
                edges.append(Edge(
                    id=_make_edge_id(),
                    src=_make_file_id(file_path),
                    dst=f"proto:{import_path}:1-1:file:file",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                ))

    return symbols, edges


def analyze_proto(repo_root: Path) -> ProtoAnalysisResult:
    """Analyze all Proto files in the repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        ProtoAnalysisResult with symbols and edges found.
    """
    if not is_proto_tree_sitter_available():
        warnings.warn("Proto analysis skipped: tree-sitter-language-pack not available")
        return ProtoAnalysisResult(skipped=True, skip_reason="tree-sitter-language-pack not available")

    from tree_sitter_language_pack import get_parser

    parser = get_parser("proto")
    run_id = f"uuid:{uuid.uuid4()}"
    start_time = time.time()
    files_analyzed = 0

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for file_path in find_proto_files(repo_root):
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

    return ProtoAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
