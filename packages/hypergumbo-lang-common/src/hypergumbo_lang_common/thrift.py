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
Uses TreeSitterAnalyzer base class for grammar checking and parser creation.
1. Parse all .thrift files and extract symbols
2. Detect include statements and create import edges
3. Create contains edges from services to their functions

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
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

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    make_file_id,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("thrift")


def find_thrift_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Thrift files in the repository."""
    yield from find_files(repo_root, ["*.thrift"])


def _make_edge_id() -> str:
    """Generate a unique edge ID."""
    return f"edge:thrift:{uuid.uuid4().hex[:12]}"


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
            return_type = node_text(child, source).strip()
        elif child.type == "parameters":
            params = node_text(child, source).strip()

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
                    part = node_text(subchild, source).strip()
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
        sym_id = make_symbol_id("thrift", file_path, start_line, end_line, name, kind)
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
            service_name_node = find_child_by_type(node, "identifier")
            if service_name_node:
                service_name = node_text(service_name_node, source).strip()
                service_sym = make_symbol(node, service_name, "service")
                symbols.append(service_sym)
                service_by_pos[(node.start_byte, node.end_byte)] = service_sym

        elif node.type == "function_definition":
            # Find containing service by walking up parents
            service_sym = _find_containing_service(node, service_by_pos)
            if service_sym:
                func_name_node = find_child_by_type(node, "identifier")
                if func_name_node:
                    func_name = node_text(func_name_node, source).strip()
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
            struct_name_node = find_child_by_type(node, "identifier")
            if struct_name_node:
                struct_name = node_text(struct_name_node, source).strip()
                symbols.append(make_symbol(node, struct_name, "struct"))

        elif node.type == "enum_definition":
            enum_name_node = find_child_by_type(node, "identifier")
            if enum_name_node:
                enum_name = node_text(enum_name_node, source).strip()
                symbols.append(make_symbol(node, enum_name, "enum"))

        elif node.type == "typedef_definition":
            # typedef: typedef Type typedef_identifier
            typedef_id_node = find_child_by_type(node, "typedef_identifier")
            if typedef_id_node:
                typedef_name = node_text(typedef_id_node, source).strip()
                symbols.append(make_symbol(node, typedef_name, "typedef"))

        elif node.type == "const_definition":
            # const: const Type Name = value
            const_name_node = find_child_by_type(node, "identifier")
            if const_name_node:
                const_name = node_text(const_name_node, source).strip()
                symbols.append(make_symbol(node, const_name, "const"))

        elif node.type == "include_statement":
            # Extract include path
            for subchild in node.children:
                if subchild.type == "string":
                    include_path = node_text(subchild, source).strip().strip('"')
                    edges.append(Edge(
                        id=_make_edge_id(),
                        src=make_file_id("thrift", file_path),
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


class ThriftAnalyzer(TreeSitterAnalyzer):
    """Analyzer for Thrift files using TreeSitterAnalyzer base class."""

    lang = "thrift"
    file_patterns: ClassVar[list[str]] = ["*.thrift"]
    language_pack_name = "thrift"

    def analyze(self, repo_root: Path, max_files: Optional[int] = None) -> AnalysisResult:
        """Override analyze for Thrift's custom extraction logic."""
        import time as _time
        import warnings

        start_time = _time.time()

        if not self._check_grammar_available():
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

        duration_ms = int((_time.time() - start_time) * 1000)

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


_analyzer = ThriftAnalyzer()


def is_thrift_tree_sitter_available() -> bool:
    """Check if tree-sitter with Thrift grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("thrift")
def analyze_thrift(repo_root: Path) -> AnalysisResult:
    """Analyze all Thrift files in the repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        AnalysisResult with symbols and edges found.
    """
    return _analyzer.analyze(repo_root)
