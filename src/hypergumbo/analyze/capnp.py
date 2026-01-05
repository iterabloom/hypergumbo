"""Cap'n Proto analysis pass using tree-sitter.

This analyzer uses tree-sitter to parse .capnp files and extract:
- Struct definitions
- Interface definitions (RPC services)
- Method definitions (RPC methods)
- Enum definitions
- Const definitions
- Import relationships (using import)

If tree-sitter with Cap'n Proto support is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter with Cap'n Proto grammar is available
2. If not available, return skipped result (not an error)
3. Parse all .capnp files and extract symbols
4. Detect import statements and create import edges
5. Create contains edges from interfaces to their methods

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-language-pack for Cap'n Proto grammar
- Cap'n Proto files define cross-language interfaces (like Proto/Thrift)
- Enables full-stack tracing for Cap'n Proto-based systems

Cap'n Proto-Specific Considerations
----------------------------------
- Cap'n Proto uses unique IDs (@0x...) for schema evolution
- Interfaces are like Proto services (contain RPC methods)
- Methods have parameters and return types
- Structs can be nested
- Uses positional field numbers (@N) like Proto
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

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = "capnp-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_capnp_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Cap'n Proto files in the repository."""
    yield from find_files(repo_root, ["*.capnp"])


def is_capnp_tree_sitter_available() -> bool:
    """Check if tree-sitter with Cap'n Proto grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover - tree-sitter not installed
    if importlib.util.find_spec("tree_sitter_language_pack") is None:
        return False  # pragma: no cover - language pack not installed
    try:
        from tree_sitter_language_pack import get_language
        get_language("capnp")
        return True
    except Exception:  # pragma: no cover - capnp grammar not available
        return False


@dataclass
class CapnpAnalysisResult:
    """Result of analyzing Cap'n Proto files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"capnp:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_file_id(path: str) -> str:
    """Generate ID for a Cap'n Proto file node (used as import edge source)."""
    return f"capnp:{path}:1-1:file:file"


def _make_edge_id() -> str:
    """Generate a unique edge ID."""
    return f"edge:capnp:{uuid.uuid4().hex[:12]}"


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: "tree_sitter.Node", type_name: str) -> Optional["tree_sitter.Node"]:
    """Find first child of given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None  # pragma: no cover - defensive


def _extract_method_signature(method_node: "tree_sitter.Node", source: bytes) -> str:
    """Extract method signature showing parameters and return types.

    Cap'n Proto method syntax:
        methodName @N (param1 :Type1, param2 :Type2) -> (retName :RetType);

    Returns signature like "(userId :Text) -> (user :User)".
    """
    params: Optional[str] = None
    returns: Optional[str] = None

    for child in method_node.children:
        if child.type == "method_parameters":
            params = _node_text(child, source).strip()
        elif child.type == "return_type":
            returns = _node_text(child, source).strip()

    sig = params or "()"
    if returns:
        sig += f" -> {returns}"
    return sig


def _extract_symbols_and_edges(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> tuple[list[Symbol], list[Edge]]:
    """Extract all symbols and edges from a parsed Cap'n Proto file."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    root = tree.root_node

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

        # Build canonical name with prefix
        if prefix:
            canonical_name = f"{prefix}.{name}"
        else:
            canonical_name = name

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
            language="capnp",
            path=file_path,
            span=span,
            origin=PASS_ID,
            origin_run_id=run_id,
            signature=signature,
        )

    def process_struct(node: "tree_sitter.Node", prefix: Optional[str] = None) -> None:
        """Process a struct definition, including nested structs."""
        # Find the type_identifier child for the struct name
        name_node = _find_child_by_type(node, "type_identifier")
        if name_node:
            struct_name = _node_text(name_node, source).strip()
            symbols.append(make_symbol(node, struct_name, "struct", prefix=prefix))

            # Process nested structs inside fields
            # Structure: field > nested_struct > struct
            new_prefix = f"{prefix}.{struct_name}" if prefix else struct_name
            for child in node.children:
                if child.type == "field":
                    for field_child in child.children:
                        if field_child.type == "nested_struct":
                            for nested_child in field_child.children:
                                if nested_child.type == "struct":
                                    process_struct(nested_child, prefix=new_prefix)

    def process_interface(node: "tree_sitter.Node") -> None:
        """Process an interface definition with its methods."""
        # Find the type_identifier child for the interface name
        name_node = _find_child_by_type(node, "type_identifier")
        if name_node:
            interface_name = _node_text(name_node, source).strip()
            interface_sym = make_symbol(node, interface_name, "interface")
            symbols.append(interface_sym)

            # Extract methods
            for child in node.children:
                if child.type == "method":
                    method_name_node = _find_child_by_type(child, "method_identifier")
                    if method_name_node:
                        method_name = _node_text(method_name_node, source).strip()
                        method_sig = _extract_method_signature(child, source)
                        method_sym = make_symbol(
                            child, method_name, "method",
                            prefix=interface_name,
                            signature=method_sig
                        )
                        symbols.append(method_sym)

                        # Create contains edge from interface to method
                        edges.append(Edge(
                            id=_make_edge_id(),
                            src=interface_sym.id,
                            dst=method_sym.id,
                            edge_type="contains",
                            line=method_sym.span.start_line,
                        ))

    def process_enum(node: "tree_sitter.Node") -> None:
        """Process an enum definition."""
        # Enums use enum_identifier, not type_identifier
        name_node = _find_child_by_type(node, "enum_identifier")
        if name_node:
            enum_name = _node_text(name_node, source).strip()
            symbols.append(make_symbol(node, enum_name, "enum"))

    def process_const(node: "tree_sitter.Node") -> None:
        """Process a const definition."""
        # const_identifier is used for constant names
        name_node = _find_child_by_type(node, "const_identifier")
        if name_node:
            const_name = _node_text(name_node, source).strip()
            symbols.append(make_symbol(node, const_name, "const"))

    def process_using_directive(node: "tree_sitter.Node") -> None:
        """Process a using directive that imports another file."""
        # Structure: using_directive > import_using > import_path > string_fragment
        for child in node.children:
            if child.type == "import_using":
                for import_child in child.children:
                    if import_child.type == "import_path":
                        # Find string_fragment inside import_path
                        for path_child in import_child.children:
                            if path_child.type == "string_fragment":
                                import_path = _node_text(path_child, source).strip()
                                edges.append(Edge(
                                    id=_make_edge_id(),
                                    src=_make_file_id(file_path),
                                    dst=f"capnp:{import_path}:1-1:file:file",
                                    edge_type="imports",
                                    line=node.start_point[0] + 1,
                                ))

    # Process children of root (message node)
    # The root_node itself is of type "message"
    for child in root.children:
        if child.type == "struct":
            process_struct(child)
        elif child.type == "interface":
            process_interface(child)
        elif child.type == "enum":
            process_enum(child)
        elif child.type == "const":
            process_const(child)
        elif child.type == "using_directive":
            process_using_directive(child)

    return symbols, edges


def analyze_capnp(repo_root: Path) -> CapnpAnalysisResult:
    """Analyze all Cap'n Proto files in the repository.

    Args:
        repo_root: Path to the repository root.

    Returns:
        CapnpAnalysisResult with symbols and edges found.
    """
    if not is_capnp_tree_sitter_available():
        warnings.warn("Cap'n Proto analysis skipped: tree-sitter-language-pack not available")
        return CapnpAnalysisResult(skipped=True, skip_reason="tree-sitter-language-pack not available")

    from tree_sitter_language_pack import get_parser

    parser = get_parser("capnp")
    run_id = f"uuid:{uuid.uuid4()}"
    start_time = time.time()
    files_analyzed = 0

    all_symbols: list[Symbol] = []
    all_edges: list[Edge] = []

    for file_path in find_capnp_files(repo_root):
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

    return CapnpAnalysisResult(
        symbols=all_symbols,
        edges=all_edges,
        run=run,
    )
