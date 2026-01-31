"""SPARQL query analyzer using tree-sitter.

SPARQL is a query language for RDF (Resource Description Framework) databases,
commonly used in semantic web and knowledge graph applications.

How It Works
------------
1. Uses tree-sitter-sparql grammar from tree-sitter-language-pack to parse files
2. Extracts PREFIX declarations (namespace bindings)
3. Extracts query definitions (SELECT, CONSTRUCT, ASK, DESCRIBE)
4. Tracks referenced predicates and types from known vocabularies

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

import time
import uuid
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from hypergumbo_core.ir import AnalysisRun, Edge, Span, Symbol

if TYPE_CHECKING:
    import tree_sitter


PASS_ID = "sparql.tree_sitter"
PASS_VERSION = "0.1.0"


class SPARQLAnalysisResult:
    """Result of SPARQL analysis."""

    def __init__(
        self,
        symbols: list[Symbol],
        edges: list[Edge],
        run: AnalysisRun | None = None,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> None:
        self.symbols = symbols
        self.edges = edges
        self.run = run
        self.skipped = skipped
        self.skip_reason = skip_reason


def is_sparql_tree_sitter_available() -> bool:
    """Check if tree-sitter-sparql is available."""
    try:
        from tree_sitter_language_pack import get_language

        get_language("sparql")
        return True
    except Exception:  # pragma: no cover
        return False


def find_sparql_files(repo_root: Path) -> list[Path]:
    """Find all SPARQL files in the repository."""
    files: list[Path] = []
    for pattern in ["**/*.sparql", "**/*.rq"]:
        files.extend(repo_root.glob(pattern))
    return sorted(files)


def _get_node_text(node: "tree_sitter.Node") -> str:
    """Get the text content of a node."""
    return node.text.decode("utf-8") if node.text else ""


def _make_symbol_id(path: Path, name: str, kind: str) -> str:
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


class SPARQLAnalyzer:
    """Analyzer for SPARQL files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._symbols: list[Symbol] = []
        self._edges: list[Edge] = []
        self._execution_id = f"uuid:{uuid.uuid4()}"
        self._files_analyzed = 0
        self._current_prefixes: dict[str, str] = {}  # prefix -> IRI
        self._query_counter = 0

    def analyze(self) -> SPARQLAnalysisResult:
        """Run the SPARQL analysis."""
        start_time = time.time()

        files = find_sparql_files(self.repo_root)
        if not files:
            return SPARQLAnalysisResult(
                symbols=[],
                edges=[],
                run=None,
            )

        from tree_sitter_language_pack import get_parser

        parser = get_parser("sparql")

        for path in files:
            try:
                content = path.read_bytes()
                tree = parser.parse(content)
                self._current_prefixes = {}
                self._query_counter = 0
                self._extract_symbols(tree.root_node, path)
                self._files_analyzed += 1
            except Exception:  # pragma: no cover  # noqa: S112  # nosec B112
                continue

        duration_ms = int((time.time() - start_time) * 1000)

        run = AnalysisRun(
            pass_id=PASS_ID,
            execution_id=self._execution_id,
            version=PASS_VERSION,
            toolchain={"name": "sparql", "version": "unknown"},
            duration_ms=duration_ms,
            files_analyzed=self._files_analyzed,
        )

        return SPARQLAnalysisResult(
            symbols=self._symbols,
            edges=self._edges,
            run=run,
        )

    def _extract_symbols(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract symbols from a syntax tree node."""
        if node.type == "prefix_declaration":
            self._extract_prefix(node, path)
        elif node.type == "base_declaration":
            self._extract_base(node, path)
        elif node.type in ("select_query", "construct_query", "ask_query", "describe_query"):
            self._extract_query(node, path)

        for child in node.children:
            self._extract_symbols(child, path)

    def _extract_prefix(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a PREFIX declaration."""
        prefix_name = ""
        iri = ""

        for child in node.children:
            if child.type == "namespace":
                # namespace contains pn_prefix and colon
                for ns_child in child.children:
                    if ns_child.type == "pn_prefix":
                        prefix_name = _get_node_text(ns_child)
                        break
            elif child.type == "iri_reference":
                iri = _get_node_text(child).strip("<>")

        if not prefix_name:
            return  # pragma: no cover

        # Store for later query edge creation
        self._current_prefixes[prefix_name] = iri

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, prefix_name, "prefix")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        # Detect well-known vocabulary
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
        self._symbols.append(symbol)

    def _extract_base(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a BASE declaration."""
        iri = ""

        for child in node.children:
            if child.type == "iri_reference":
                iri = _get_node_text(child).strip("<>")
                break

        if not iri:
            return  # pragma: no cover

        rel_path = path.relative_to(self.repo_root)
        symbol_id = _make_symbol_id(rel_path, "BASE", "base")

        span = Span(
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

        symbol = Symbol(
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
        self._symbols.append(symbol)

    def _extract_query(self, node: "tree_sitter.Node", path: Path) -> None:
        """Extract a query definition."""
        query_type = node.type.replace("_query", "").upper()
        self._query_counter += 1
        query_name = f"query_{self._query_counter}"

        # Extract variables from SELECT clause
        variables: list[str] = []
        if query_type == "SELECT":
            variables = self._extract_select_variables(node)

        rel_path = path.relative_to(self.repo_root)
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
        pattern_count = self._count_triple_patterns(node)

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
        self._symbols.append(symbol)

        # Create edges for vocabulary usage
        used_prefixes = self._find_used_prefixes(node)
        for prefix in used_prefixes:
            if prefix in self._current_prefixes:
                edge = Edge.create(
                    src=symbol_id,
                    dst=_make_symbol_id(rel_path, prefix, "prefix"),
                    edge_type="uses_vocabulary",
                    line=node.start_point[0] + 1,
                    origin=PASS_ID,
                    origin_run_id=self._execution_id,
                    evidence_type="static",
                    confidence=1.0,
                    evidence_lang="sparql",
                )
                self._edges.append(edge)

    def _extract_select_variables(self, node: "tree_sitter.Node") -> list[str]:
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

    def _count_triple_patterns(self, node: "tree_sitter.Node") -> int:
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

    def _find_used_prefixes(self, node: "tree_sitter.Node") -> set[str]:
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


def analyze_sparql(repo_root: Path) -> SPARQLAnalysisResult:
    """Analyze SPARQL files in a repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        SPARQLAnalysisResult containing extracted symbols and edges
    """
    if not is_sparql_tree_sitter_available():
        warnings.warn(
            "SPARQL analysis skipped: tree-sitter-sparql not available",
            UserWarning,
            stacklevel=2,
        )
        return SPARQLAnalysisResult(
            symbols=[],
            edges=[],
            run=AnalysisRun(
                pass_id=PASS_ID,
                execution_id=f"uuid:{uuid.uuid4()}",
                version=PASS_VERSION,
                toolchain={"name": "sparql", "version": "unknown"},
                duration_ms=0,
                files_analyzed=0,
            ),
            skipped=True,
            skip_reason="tree-sitter-sparql not available",
        )

    analyzer = SPARQLAnalyzer(repo_root)
    return analyzer.analyze()
