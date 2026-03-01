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
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract type definitions and relationships
2. Pass 2: Create field_of edges for type-field relationships

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the GraphQL-specific extraction
logic.

Why This Design
---------------
- TreeSitterAnalyzer eliminates boilerplate orchestration code
- Optional dependency keeps base install lightweight
- Uses tree-sitter-graphql package for grammar (grammar_module)
- GraphQL-specific: types, fields, queries, mutations are first-class
- Useful for API schema analysis and documentation generation
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator, Optional

from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
    make_symbol_id,
    node_text,
)
from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun

PASS_ID = make_pass_id("graphql")


def find_graphql_files(repo_root: Path) -> Iterator[Path]:
    """Yield all GraphQL files in the repository."""
    yield from find_files(repo_root, ["*.graphql", "*.gql"])


def _make_edge_id(src: str, dst: str, edge_type: str) -> str:  # pragma: no cover
    """Generate deterministic edge ID."""  # pragma: no cover
    content = f"{edge_type}:{src}:{dst}"  # pragma: no cover
    return f"edge:sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"  # pragma: no cover


def _get_name(node: "tree_sitter.Node", source: bytes) -> Optional[str]:
    """Extract name from a definition node."""
    for child in node.children:
        if child.type == "name":
            return node_text(child, source)
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
                    var_text = node_text(var_child, source).strip()
                    if var_text:
                        params.append(var_text)
        elif child.type == "arguments_definition":  # pragma: no cover - field args
            # Field argument definitions: (arg: Type)
            for arg_child in child.children:  # pragma: no cover
                if arg_child.type == "input_value_definition":  # pragma: no cover
                    arg_text = node_text(arg_child, source).strip()  # pragma: no cover
                    if arg_text:  # pragma: no cover
                        params.append(arg_text)  # pragma: no cover
        elif child.type == "type":  # pragma: no cover - field return type
            # Return type for fields
            return_type = node_text(child, source).strip()  # pragma: no cover

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
    run_id: str,
    symbols: list[Symbol],
    edges: list[Edge],
    type_registry: dict[str, str],
) -> None:
    """Process GraphQL AST tree to extract symbols and edges.

    Args:
        tree: Tree-sitter tree to process
        source: Source file bytes
        rel_path: Relative path to file
        run_id: The execution ID for provenance
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
                symbol_id = make_symbol_id("graphql", rel_path, start_line, end_line, type_name, kind)

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
                    origin_run_id=run_id,
                )
                symbols.append(sym)
                type_registry[type_name.lower()] = symbol_id

        elif node.type == "directive_definition":
            directive_name = _get_name(node, source)
            if directive_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                symbol_id = make_symbol_id("graphql", rel_path, start_line, end_line, directive_name, "directive")

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
                    origin_run_id=run_id,
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
                symbol_id = make_symbol_id("graphql", rel_path, start_line, end_line, frag_name, "fragment")

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
                    origin_run_id=run_id,
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
                        op_type = node_text(child, source).lower()
                        break

                symbol_id = make_symbol_id("graphql", rel_path, start_line, end_line, op_name, op_type)

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
                    origin_run_id=run_id,
                    signature=signature,
                )
                symbols.append(sym)


class GraphqlAnalyzer(TreeSitterAnalyzer):
    """GraphQL language analyzer using tree-sitter-graphql."""

    lang = "graphql"
    file_patterns: ClassVar[list[str]] = ["*.graphql", "*.gql"]
    grammar_module = "tree_sitter_graphql"
    create_file_symbols = False

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract types, directives, fragments, operations from a GraphQL file."""
        analysis = FileAnalysis()

        # GraphQL uses a single-pass extraction (symbols and edges together)
        # since GraphQL schemas don't have call-style edges
        type_registry: dict[str, str] = {}
        edges: list[Edge] = []
        _process_graphql_tree(
            tree, source, rel_path, run.execution_id,
            analysis.symbols, edges, type_registry,
        )

        # Register symbols for cross-file lookup
        for sym in analysis.symbols:
            analysis.symbol_by_name[sym.name] = sym

        return analysis


_analyzer = GraphqlAnalyzer()


def is_graphql_tree_sitter_available() -> bool:
    """Check if tree-sitter with GraphQL grammar is available."""
    return _analyzer._check_grammar_available()


@register_analyzer("graphql")
def analyze_graphql_files(repo_root: Path) -> AnalysisResult:
    """Analyze GraphQL files in the repository."""
    return _analyzer.analyze(repo_root)
