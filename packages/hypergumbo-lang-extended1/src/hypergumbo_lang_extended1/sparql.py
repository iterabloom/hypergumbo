# SPDX-License-Identifier: AGPL-3.0-or-later
"""SPARQL query analyzer using tree-sitter.

SPARQL is a query language for RDF (Resource Description Framework) databases,
commonly used in semantic web and knowledge graph applications.

How It Works
------------
Uses TreeSitterAnalyzer base class for two-pass orchestration:
1. Pass 1: Extract PREFIX declarations and query definitions
2. Pass 2: Create vocabulary usage edges linking queries to prefixes

The base class handles grammar checking, parser creation, file discovery,
and result assembly. This module provides only the SPARQL-specific extraction
logic.

Symbols Extracted
-----------------
- **Prefixes**: Namespace prefix declarations (PREFIX foaf: <...>)
- **Queries**: Query definitions with type (SELECT, CONSTRUCT, ASK, DESCRIBE)

Edges Extracted
---------------
- **uses_vocabulary**: Links from queries to prefix declarations (vocabulary usage)

Why This Design
---------------
- SPARQL queries reference RDF vocabularies via prefixes
- Understanding which vocabularies are used helps map data models
- Query types indicate the intent (read, transform, check)
- Prefix IRIs point to external ontologies/schemas
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import Edge, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import (
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    iter_tree,
)
from hypergumbo_core.analyze.registry import register_analyzer

if TYPE_CHECKING:
    import tree_sitter
    from hypergumbo_core.ir import AnalysisRun
    from hypergumbo_core.symbol_resolution import NameResolver


PASS_ID = make_pass_id("sparql")


def find_sparql_files(repo_root: Path) -> list[Path]:
    """Find all SPARQL files in the repository."""
    files: list[Path] = []
    for pattern in ["**/*.sparql", "**/*.rq"]:
        files.extend(find_files(repo_root, [pattern]))
    return sorted(files)


def is_sparql_tree_sitter_available() -> bool:
    """Check if tree-sitter-sparql is available."""
    return _analyzer._check_grammar_available()


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _make_symbol_id(path: str, name: str, kind: str) -> str:
    """Create a stable symbol ID."""
    return f"sparql:{path}:{kind}:{name}"


# Well-known SPARQL vocabulary prefixes
KNOWN_VOCABULARIES = frozenset({
    "rdf", "rdfs", "owl", "xsd", "skos", "dc", "dcterms", "foaf",
    "schema", "void", "dcat", "prov", "geo", "time", "org",
    "sh", "shacl",  # SHACL
    "fhir",  # Healthcare
    "wd", "wdt", "wikibase",  # Wikidata
})


def _extract_prefix(
    node: "tree_sitter.Node", rel_path: str,
) -> tuple[Symbol | None, str, str]:
    """Extract a PREFIX declaration.

    Returns (symbol_or_none, prefix_name, iri).
    """
    prefix_name = ""
    iri = ""

    for child in node.children:
        if child.type == "namespace":
            for ns_child in child.children:
                if ns_child.type == "pn_prefix":
                    prefix_name = _get_node_text(ns_child)
                    break
        elif child.type == "iri_reference":
            iri = _get_node_text(child).strip("<>")

    if not prefix_name:
        return None, "", ""  # pragma: no cover

    symbol_id = _make_symbol_id(rel_path, prefix_name, "prefix")

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    is_standard = prefix_name.lower() in KNOWN_VOCABULARIES

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=prefix_name,
        kind="prefix",
        language="sparql",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"PREFIX {prefix_name}: <{iri[:50]}{'...' if len(iri) > 50 else ''}>",
        meta={"iri": iri, "is_standard_vocabulary": is_standard},
    )
    return symbol, prefix_name, iri


def _extract_base(
    node: "tree_sitter.Node", rel_path: str,
) -> Symbol | None:
    """Extract a BASE declaration."""
    iri = ""

    for child in node.children:
        if child.type == "iri_reference":
            iri = _get_node_text(child).strip("<>")
            break

    if not iri:
        return None  # pragma: no cover

    symbol_id = _make_symbol_id(rel_path, "BASE", "base")

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    return Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name="BASE",
        kind="base",
        language="sparql",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=f"BASE <{iri[:50]}{'...' if len(iri) > 50 else ''}>",
        meta={"iri": iri},
    )


def _extract_select_variables(node: "tree_sitter.Node") -> list[str]:
    """Extract variable names from a SELECT clause."""
    variables: list[str] = []

    def find_vars(n: "tree_sitter.Node") -> None:
        if n.type == "select_clause":
            for child in n.children:
                if child.type == "var":
                    var_text = _get_node_text(child)
                    if var_text.startswith("?") or var_text.startswith("$"):
                        variables.append(var_text)
                elif child.type == "*":
                    variables.append("*")
        else:
            for child in n.children:
                find_vars(child)

    find_vars(node)
    return variables


def _count_triple_patterns(node: "tree_sitter.Node") -> int:
    """Count the number of triple patterns in a query."""
    count = 0

    def count_triples(n: "tree_sitter.Node") -> None:
        nonlocal count
        if n.type == "triples_same_subject":
            count += 1
        for child in n.children:
            count_triples(child)

    count_triples(node)
    return count


def _find_used_prefixes(node: "tree_sitter.Node") -> set[str]:
    """Find all prefixes used in a query."""
    prefixes: set[str] = set()

    def find_prefixes(n: "tree_sitter.Node") -> None:
        if n.type == "prefixed_name":
            for child in n.children:
                if child.type == "namespace":
                    for ns_child in child.children:
                        if ns_child.type == "pn_prefix":
                            prefixes.add(_get_node_text(ns_child))
                            break
        for child in n.children:
            find_prefixes(child)

    find_prefixes(node)
    return prefixes


def _extract_query(
    node: "tree_sitter.Node", rel_path: str,
    query_counter: int,
) -> tuple[Symbol, int]:
    """Extract a query definition. Returns (symbol, updated_counter)."""
    query_type = node.type.replace("_query", "").upper()
    query_counter += 1
    query_name = f"query_{query_counter}"

    # Extract variables from SELECT clause
    variables: list[str] = []
    if query_type == "SELECT":
        variables = _extract_select_variables(node)

    symbol_id = _make_symbol_id(rel_path, query_name, "query")

    span = Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
    )

    # Build signature
    if variables:
        sig = f"{query_type} {', '.join(variables[:5])}"
        if len(variables) > 5:
            sig += f" (+{len(variables) - 5} more)"
    else:
        sig = query_type

    # Count triple patterns
    pattern_count = _count_triple_patterns(node)

    symbol = Symbol(
        id=symbol_id,
        stable_id=symbol_id,
        name=query_name,
        kind="query",
        language="sparql",
        path=str(rel_path),
        span=span,
        origin=PASS_ID,
        signature=sig,
        meta={
            "query_type": query_type,
            "variables": variables,
            "pattern_count": pattern_count,
        },
    )
    return symbol, query_counter


class SPARQLAnalyzer(TreeSitterAnalyzer):
    """Analyzer for SPARQL files using TreeSitterAnalyzer base class."""

    lang = "sparql"
    file_patterns: ClassVar[list[str]] = ["*.sparql", "*.rq"]
    language_pack_name = "sparql"

    def get_import_aliases(
        self, tree: "tree_sitter.Tree", source: bytes,
    ) -> dict[str, str]:
        """Extract PREFIX declarations as import aliases.

        The base class calls this separately from extract_symbols_from_file
        to pass aliases to Pass 2. We extract prefix name -> IRI mappings
        so edge extraction can match used prefixes.
        """
        aliases: dict[str, str] = {}
        for node in iter_tree(tree.root_node):
            if node.type == "prefix_declaration":
                prefix_name = ""
                iri = ""
                for child in node.children:
                    if child.type == "namespace":
                        for ns_child in child.children:
                            if ns_child.type == "pn_prefix":
                                prefix_name = _get_node_text(ns_child)
                                break
                    elif child.type == "iri_reference":
                        iri = _get_node_text(child).strip("<>")
                if prefix_name:
                    aliases[prefix_name] = iri
        return aliases

    def extract_symbols_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str, run: "AnalysisRun",
    ) -> FileAnalysis:
        """Extract prefix and query symbols from a SPARQL file."""
        analysis = FileAnalysis()
        query_counter = 0

        for node in iter_tree(tree.root_node):
            if node.type == "prefix_declaration":
                sym, prefix_name, iri = _extract_prefix(node, rel_path)
                if sym:
                    analysis.symbols.append(sym)
                    # Store prefix info for edge extraction in Pass 2
                    if prefix_name:
                        analysis.import_aliases[prefix_name] = iri
            elif node.type == "base_declaration":
                sym = _extract_base(node, rel_path)
                if sym:
                    analysis.symbols.append(sym)
            elif node.type in ("select_query", "construct_query", "ask_query", "describe_query"):
                sym, query_counter = _extract_query(node, rel_path, query_counter)
                analysis.symbols.append(sym)
                analysis.symbol_by_name[sym.name] = sym

        return analysis

    def extract_edges_from_file(
        self, tree: "tree_sitter.Tree", source: bytes,
        file_path: Path, rel_path: str,
        local_symbols: dict[str, Symbol], global_symbols: dict,
        run: "AnalysisRun", import_aliases: dict[str, str],
        resolver: "NameResolver",
    ) -> list[Edge]:
        """Extract vocabulary usage edges from a SPARQL file."""
        edges: list[Edge] = []

        for node in iter_tree(tree.root_node):
            if node.type in ("select_query", "construct_query", "ask_query", "describe_query"):
                query_type = node.type.replace("_query", "").upper()
                # Find the matching query symbol by scanning local_symbols
                query_sym = None
                for sym in local_symbols.values():
                    if sym.kind == "query" and sym.meta.get("query_type") == query_type:
                        if (sym.span.start_line == node.start_point[0] + 1):
                            query_sym = sym
                            break

                if not query_sym:
                    continue  # pragma: no cover

                # Create edges for vocabulary usage
                used_prefixes = _find_used_prefixes(node)
                for prefix in used_prefixes:
                    if prefix in import_aliases:
                        edge = Edge.create(
                            src=query_sym.id,
                            dst=_make_symbol_id(rel_path, prefix, "prefix"),
                            edge_type="uses_vocabulary",
                            line=node.start_point[0] + 1,
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                            evidence_type="static",
                            confidence=1.0,
                            evidence_lang="sparql",
                        )
                        edges.append(edge)

        return edges


_analyzer = SPARQLAnalyzer()


@register_analyzer("sparql")
def analyze_sparql(repo_root: Path) -> AnalysisResult:
    """Analyze SPARQL files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AnalysisResult containing extracted symbols and edges
    """
    return _analyzer.analyze(repo_root)
