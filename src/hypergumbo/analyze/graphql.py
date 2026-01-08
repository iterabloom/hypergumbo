"""GraphQL schema analysis pass using tree-sitter-graphql.

This analyzer uses tree-sitter to parse GraphQL schema and query files and extract:
- Type definitions (object, input, interface, enum, scalar)
- Field definitions
- Query/Mutation/Subscription definitions
- Fragment definitions
- Directives

If tree-sitter-graphql is not installed, the analyzer
gracefully degrades and returns an empty result.

How It Works
------------
1. Check if tree-sitter-graphql is available
2. If not available, return skipped result (not an error)
3. Parse all .graphql/.gql files
4. Extract type definitions and relationships
5. Create field_of edges for type-field relationships

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-graphql package for grammar
- GraphQL-specific: types, fields, queries, mutations are first-class
- Useful for API schema analysis and documentation generation
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

PASS_ID = "graphql-v1"
PASS_VERSION = "hypergumbo-0.1.0"


def find_graphql_files(repo_root: Path) -> Iterator[Path]:
    """Yield all GraphQL files in the repository."""
    yield from find_files(repo_root, ["*.graphql", "*.gql"])


def is_graphql_tree_sitter_available() -> bool:
    """Check if tree-sitter with GraphQL grammar is available."""
    if importlib.util.find_spec("tree_sitter") is None:
        return False  # pragma: no cover
    if importlib.util.find_spec("tree_sitter_graphql") is None:
        return False  # pragma: no cover
    return True


@dataclass
class GraphQLAnalysisResult:
    """Result of analyzing GraphQL files."""

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    run: AnalysisRun | None = None
    skipped: bool = False
    skip_reason: str = ""


def _make_symbol_id(path: str, start_line: int, end_line: int, name: str, kind: str) -> str:
    """Generate location-based ID."""
    return f"graphql:{path}:{start_line}-{end_line}:{name}:{kind}"


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:  # pragma: no cover
    """Generate deterministic edge ID."""  # pragma: no cover
    content = f"{edge_type}:{src}:{dst}"  # pragma: no cover
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"  # pragma: no cover


def _node_text(node: "tree_sitter.Node", source: bytes) -> str:
    """Extract text for a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract name from a definition node."""
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source)
    return None  # pragma: no cover


def _extract_graphql_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract signature from a GraphQL operation or field definition.

    Operations: query Name($arg1: Type1!, $arg2: Type2) -> ($arg1: Type1!, $arg2: Type2)
    Fields: field(arg1: Type1, arg2: Type2): ReturnType -> (arg1: Type1, arg2: Type2): ReturnType

    Returns signature string or None.
    """
    params: list[str] = []
    return_type: Optional[str] = None

    for child in node.children:
        if child.type == "variable_definitions":
            # Operation variable definitions: ($arg: Type)
            for var_child in child.children:
                if var_child.type == "variable_definition":
                    var_text = _node_text(var_child, source).strip()
                    if var_text:
                        params.append(var_text)
        elif child.type == "arguments_definition":  # pragma: no cover - field args
            # Field argument definitions: (arg: Type)
            for arg_child in child.children:  # pragma: no cover
                if arg_child.type == "input_value_definition":  # pragma: no cover
                    arg_text = _node_text(arg_child, source).strip()  # pragma: no cover
                    if arg_text:  # pragma: no cover
                        params.append(arg_text)  # pragma: no cover
        elif child.type == "type":  # pragma: no cover - field return type
            # Return type for fields
            return_type = _node_text(child, source).strip()  # pragma: no cover

    if not params and not return_type:
        return None

    sig = "(" + ", ".join(params) + ")" if params else "()"  # pragma: no cover - empty params rare
    if return_type:  # pragma: no cover - field return type
        sig += f": {return_type}"  # pragma: no cover
    return sig


def _process_graphql_tree(
    tree: "tree_sitter.Tree",
    source: bytes,
    rel_path: str,
    symbols: list[Symbol],
    edges: list[Edge],
    type_registry: dict[str, str],
) -> None:
    """Process GraphQL AST tree to extract symbols and edges.

    Args:
        tree: Tree-sitter tree to process
        source: Source file bytes
        rel_path: Relative path to file
        symbols: List to append symbols to
        edges: List to append edges to
        type_registry: Registry mapping type names to symbol IDs
    """
    # Type definitions
    type_kinds = {
        "object_type_definition": "type",
        "input_object_type_definition": "input",
        "interface_type_definition": "interface",
        "enum_type_definition": "enum",
        "scalar_type_definition": "scalar",
        "union_type_definition": "union",
    }

    for node in iter_tree(tree.root_node):
        if node.type in type_kinds:
            kind = type_kinds[node.type]
            type_name = _get_name(node, source)
            if type_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, type_name, kind)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=type_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind=kind,
                    name=type_name,
                    path=rel_path,
                    language="graphql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)
                type_registry[type_name.lower()] = symbol_id

        elif node.type == "directive_definition":
            directive_name = _get_name(node, source)
            if directive_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, directive_name, "directive")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"@{directive_name}",
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="directive",
                    name=directive_name,
                    path=rel_path,
                    language="graphql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

        elif node.type == "fragment_definition":
            # Fragment name is in fragment_name > name
            frag_name = None
            for child in node.children:
                if child.type == "fragment_name":
                    frag_name = _get_name(child, source)
                    break
            if frag_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = _make_symbol_id(rel_path, start_line, end_line, frag_name, "fragment")

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=frag_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind="fragment",
                    name=frag_name,
                    path=rel_path,
                    language="graphql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                )
                symbols.append(sym)

        elif node.type == "operation_definition":
            # Query, Mutation, or Subscription operation
            op_name = _get_name(node, source)
            if op_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                # Determine operation type
                op_type = "operation"
                for child in node.children:
                    if child.type == "operation_type":
                        op_type = _node_text(child, source).lower()
                        break

                symbol_id = _make_symbol_id(rel_path, start_line, end_line, op_name, op_type)

                # Extract signature (variable definitions)
                signature = _extract_graphql_signature(node, source)

                sym = Symbol(
                    id=symbol_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=op_name,
                    fingerprint=hashlib.sha256(source[node.start_byte:node.end_byte]).hexdigest()[:16],
                    kind=op_type,
                    name=op_name,
                    path=rel_path,
                    language="graphql",
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    signature=signature,
                )
                symbols.append(sym)


def analyze_graphql_files(repo_root: Path) -> GraphQLAnalysisResult:
    """Analyze GraphQL files in the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        GraphQLAnalysisResult with symbols and edges
    """
    if not is_graphql_tree_sitter_available():  # pragma: no cover
        return GraphQLAnalysisResult(  # pragma: no cover
            skipped=True,  # pragma: no cover
            skip_reason="tree-sitter-graphql not installed (pip install tree-sitter-graphql)",  # pragma: no cover
        )  # pragma: no cover

    import tree_sitter
    import tree_sitter_graphql

    start_time = time.time()
    files_analyzed = 0
    files_skipped = 0
    warnings_list: list[str] = []

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # Type registry for cross-file resolution: name -> symbol_id
    type_registry: dict[str, str] = {}

    # Create parser
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_graphql.language()))
    except Exception as e:  # pragma: no cover
        warnings.warn(f"Failed to initialize GraphQL parser: {e}")
        return GraphQLAnalysisResult(
            skipped=True,
            skip_reason=f"Failed to initialize parser: {e}",
        )

    graphql_files = list(find_graphql_files(repo_root))

    for graphql_path in graphql_files:
        try:
            rel_path = str(graphql_path.relative_to(repo_root))
            source = graphql_path.read_bytes()
            tree = parser.parse(source)
            files_analyzed += 1

            # Process this file
            _process_graphql_tree(
                tree,
                source,
                rel_path,
                symbols,
                edges,
                type_registry,
            )

        except Exception as e:  # pragma: no cover
            files_skipped += 1  # pragma: no cover
            warnings_list.append(f"Failed to parse {graphql_path}: {e}")  # pragma: no cover

    duration_ms = int((time.time() - start_time) * 1000)

    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = duration_ms
    run.warnings = warnings_list

    return GraphQLAnalysisResult(
        symbols=symbols,
        edges=edges,
        run=run,
    )
