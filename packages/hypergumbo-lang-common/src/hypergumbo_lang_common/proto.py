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

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
    populate_docstrings_from_tree,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("proto")


def find_proto_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Proto files in the repository."""
    yield from find_files(repo_root, ["*.proto"])


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
            type_text = node_text(child, source).strip()
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
            full_ident = find_child_by_type(child, "full_ident")
            if full_ident:
                return node_text(full_ident, source).strip()
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
    sym_id = make_symbol_id("proto", file_path, start_line, end_line, name, kind)
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
                name_node = find_child_by_type(msg, "message_name")
                if name_node:
                    return node_text(name_node, source).strip()
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
            service_name_node = find_child_by_type(node, "service_name")
            if service_name_node:
                service_name = node_text(service_name_node, source).strip()
                service_sym = _make_proto_symbol(
                    file_path, run_id, package_name, node, source,
                    service_name, "service"
                )
                symbols.append(service_sym)
                service_symbols[service_name] = service_sym

        elif node.type == "rpc":
            # Extract RPC - need to find parent service
            rpc_name_node = find_child_by_type(node, "rpc_name")
            if rpc_name_node:
                rpc_name = node_text(rpc_name_node, source).strip()
                rpc_sig = _extract_rpc_signature(node, source)

                # Find parent service name
                service_name = None
                current = node.parent
                while current:
                    if current.type == "service":
                        svc_name_node = find_child_by_type(current, "service_name")
                        if svc_name_node:
                            service_name = node_text(svc_name_node, source).strip()
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
                    edges.append(Edge.create(
                        src=service_symbols[service_name].id,
                        dst=rpc_sym.id,
                        edge_type="contains",
                        line=rpc_sym.span.start_line,
                    ))

        elif node.type == "message":
            # Extract message name
            message_name_node = find_child_by_type(node, "message_name")
            if message_name_node:
                message_name = node_text(message_name_node, source).strip()
                parent_name = _get_parent_message_name(node, source)
                message_sym = _make_proto_symbol(
                    file_path, run_id, package_name, node, source,
                    message_name, "message", prefix=parent_name
                )
                symbols.append(message_sym)

        elif node.type == "enum":
            # Extract enum name
            enum_name_node = find_child_by_type(node, "enum_name")
            if enum_name_node:
                enum_name = node_text(enum_name_node, source).strip()
                parent_name = _get_parent_message_name(node, source)
                enum_sym = _make_proto_symbol(
                    file_path, run_id, package_name, node, source,
                    enum_name, "enum", prefix=parent_name
                )
                symbols.append(enum_sym)

        elif node.type == "import":
            # Extract import path
            import_string = find_child_by_type(node, "string")
            if import_string:
                import_path = node_text(import_string, source).strip().strip('"')
                # Create import edge from this file to the imported file
                edges.append(Edge.create(
                    src=make_file_id("proto", file_path),
                    dst=f"proto:{import_path}:1-1:file:file",
                    edge_type="imports",
                    line=node.start_point[0] + 1,
                ))

    return symbols, edges


class ProtoAnalyzer(TreeSitterAnalyzer):
    """Tree-sitter-based analyzer for Protocol Buffers files.

    Extracts services, RPC methods, messages, enums, and import edges.
    Uses language_pack_name for the proto grammar.
    """

    lang = "proto"
    file_patterns: ClassVar[list[str]] = ["**/*.proto"]
    language_pack_name = "proto"

    def analyze(self, repo_root: Path, max_files: int | None = None) -> AnalysisResult:
        """Run Proto analysis."""
        if not self._check_grammar_available():
            import warnings
            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            return AnalysisResult(
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
            )

        parser = self._create_parser()
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
                populate_docstrings_from_tree(tree.root_node, source, symbols)
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

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            run=run,
        )


_analyzer = ProtoAnalyzer()


def is_proto_tree_sitter_available() -> bool:
    """Check if tree-sitter with Proto grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("proto")
def analyze_proto(repo_root: Path) -> AnalysisResult:
    """Analyze all Proto files in the repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        AnalysisResult with symbols and edges found.
    """
    return _analyzer.analyze(repo_root)
