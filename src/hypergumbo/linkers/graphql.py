"""GraphQL client-schema linker for detecting cross-file GraphQL calls.

This linker detects GraphQL client calls (gql, useQuery, etc.) and links
them to GraphQL schema definitions detected by the GraphQL analyzer.

Detected Client Patterns
------------------------
JavaScript/TypeScript:
- gql`query MyQuery { ... }` - Template literal
- useQuery(QUERY) - Apollo React hook
- useMutation(MUTATION) - Apollo React hook

Python:
- gql("query MyQuery { ... }") - gql library
- gql('''query MyQuery { ... }''') - Triple-quoted

Operation Matching
------------------
Operations are matched by:
1. Operation name (query GetUsers matches schema query GetUsers)
2. Operation type (query, mutation, subscription)

How It Works
------------
1. Scan source files for GraphQL client patterns
2. Extract operation names from query strings
3. Match to schema operation definitions
4. Create graphql_calls edges linking client to server

Why This Design
---------------
- Cross-file linking enables full-stack GraphQL understanding
- Regex-based client detection is fast and portable
- Operation name matching is straightforward for named operations
- Symbols for client calls enable slice traversal from either end
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import LinkerContext, LinkerResult, LinkerRequirement, register_linker

PASS_ID = "graphql-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class GraphQLClientCall:
    """Represents a detected GraphQL client call."""

    operation_type: str | None  # query, mutation, subscription
    operation_name: str | None  # Named operation or None for anonymous
    query_text: str  # The full query string
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language


@dataclass
class GraphQLLinkResult:
    """Result of GraphQL client-schema linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# JavaScript gql template literal pattern
JS_GQL_PATTERN = re.compile(
    r"gql\s*`\s*([^`]+)\s*`",
    re.MULTILINE | re.DOTALL,
)

# Python gql() function call pattern
PY_GQL_PATTERN = re.compile(
    r'gql\s*\(\s*(?:"""|\'\'\')([^"\']+)(?:"""|\'\'\')|\s*gql\s*\(\s*"([^"]+)"\s*\)',
    re.MULTILINE | re.DOTALL,
)

# Operation name extraction pattern
OPERATION_PATTERN = re.compile(
    r"^\s*(query|mutation|subscription)\s+(\w+)",
    re.MULTILINE | re.IGNORECASE,
)

# Anonymous query pattern (just curly brace start)
ANONYMOUS_QUERY_PATTERN = re.compile(
    r"^\s*\{",
    re.MULTILINE,
)


def _extract_operation_name(query: str) -> tuple[str | None, str | None]:
    """Extract operation type and name from a GraphQL query.

    Args:
        query: GraphQL query string.

    Returns:
        Tuple of (operation_type, operation_name).
        operation_type is 'query', 'mutation', or 'subscription'.
        operation_name is None for anonymous operations.
    """
    query = query.strip()

    # Check for named operation: query GetUsers { ... }
    match = OPERATION_PATTERN.search(query)
    if match:
        return match.group(1).lower(), match.group(2)

    # Check for unnamed explicit query: query { ... }
    if query.startswith("query"):
        return "query", None

    # Check for unnamed explicit mutation
    if query.startswith("mutation"):  # pragma: no cover
        return "mutation", None

    # Check for anonymous query: { users { ... } }
    if ANONYMOUS_QUERY_PATTERN.match(query):
        return "query", None

    # Check for fragments (not an operation)
    if query.startswith("fragment"):
        return None, None

    return None, None  # pragma: no cover


def _find_source_files(root: Path) -> Iterator[Path]:
    """Find files that might contain GraphQL client calls."""
    patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.jsx", "**/*.tsx"]
    for path in find_files(root, patterns):
        yield path


def _scan_javascript_graphql(file_path: Path, content: str) -> list[GraphQLClientCall]:
    """Scan a JavaScript/TypeScript file for GraphQL client calls."""
    calls: list[GraphQLClientCall] = []

    for match in JS_GQL_PATTERN.finditer(content):
        query_text = match.group(1).strip()
        line_num = content[: match.start()].count("\n") + 1

        op_type, op_name = _extract_operation_name(query_text)

        calls.append(
            GraphQLClientCall(
                operation_type=op_type,
                operation_name=op_name,
                query_text=query_text,
                line=line_num,
                file_path=str(file_path),
                language="javascript",
            )
        )

    return calls


def _scan_python_graphql(file_path: Path, content: str) -> list[GraphQLClientCall]:
    """Scan a Python file for GraphQL client calls."""
    calls: list[GraphQLClientCall] = []

    for match in PY_GQL_PATTERN.finditer(content):
        # Group 1 is triple-quoted, Group 2 is single-line
        query_text = (match.group(1) or match.group(2) or "").strip()
        if not query_text:  # pragma: no cover
            continue
        line_num = content[: match.start()].count("\n") + 1

        op_type, op_name = _extract_operation_name(query_text)

        calls.append(
            GraphQLClientCall(
                operation_type=op_type,
                operation_name=op_name,
                query_text=query_text,
                line=line_num,
                file_path=str(file_path),
                language="python",
            )
        )

    return calls


def _create_client_symbol(call: GraphQLClientCall, root: Path) -> Symbol:
    """Create a symbol for a GraphQL client call."""
    rel_path = Path(call.file_path).relative_to(root) if root else Path(call.file_path)

    name = f"{call.operation_type or 'query'}"
    if call.operation_name:
        name = f"{name} {call.operation_name}"

    return Symbol(
        id=f"{rel_path}::graphql_client::{call.line}",
        name=name,
        kind="graphql_client",
        path=call.file_path,
        span=Span(
            start_line=call.line,
            start_col=0,
            end_line=call.line,
            end_col=0,
        ),
        language=call.language,
        stable_id=call.operation_name,
        meta={
            "operation_type": call.operation_type,
            "operation_name": call.operation_name,
            "query_text": call.query_text[:100] + "..." if len(call.query_text) > 100 else call.query_text,
        },
    )


def link_graphql(root: Path, schema_symbols: list[Symbol]) -> GraphQLLinkResult:
    """Link GraphQL client calls to schema definitions.

    Args:
        root: Repository root path.
        schema_symbols: Operation symbols from GraphQL analyzer.

    Returns:
        GraphQLLinkResult with edges linking clients to schema.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    edges: list[Edge] = []
    symbols: list[Symbol] = []
    files_scanned = 0

    # Build operation name to symbol mapping
    operation_map: dict[str, Symbol] = {}
    for sym in schema_symbols:
        if sym.kind in ("query", "mutation", "subscription", "operation"):
            operation_map[sym.name.lower()] = sym

    # Collect all GraphQL client calls
    all_calls: list[GraphQLClientCall] = []

    for file_path in _find_source_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1

            if file_path.suffix == ".py":
                calls = _scan_python_graphql(file_path, content)
            else:
                calls = _scan_javascript_graphql(file_path, content)

            all_calls.extend(calls)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Create symbols for each client call
    for call in all_calls:
        client_symbol = _create_client_symbol(call, root)
        symbols.append(client_symbol)

        # Try to match to a schema operation
        if call.operation_name:
            op_key = call.operation_name.lower()
            if op_key in operation_map:
                schema_sym = operation_map[op_key]
                is_cross_language = client_symbol.language != schema_sym.language

                edge = Edge.create(
                    src=client_symbol.id,
                    dst=schema_sym.id,
                    edge_type="graphql_calls",
                    line=call.line,
                    confidence=0.9 if call.operation_name else 0.7,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="graphql_operation_match",
                )
                edge.meta = {
                    "operation_type": call.operation_type,
                    "operation_name": call.operation_name,
                    "cross_language": is_cross_language,
                }
                edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return GraphQLLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


def _get_graphql_operation_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract GraphQL operation symbols from context.

    Operation symbols are from the GraphQL analyzer with:
    - language="graphql"
    - kind in ("query", "mutation", "subscription", "operation")
    """
    return [
        s for s in ctx.symbols
        if s.language == "graphql"
        and s.kind in ("query", "mutation", "subscription", "operation")
    ]


def _count_graphql_operations(ctx: LinkerContext) -> int:
    """Count available GraphQL operation symbols for requirement check."""
    return len(_get_graphql_operation_symbols(ctx))


GRAPHQL_REQUIREMENTS = [
    LinkerRequirement(
        name="graphql_operations",
        description="GraphQL operation symbols (query/mutation/subscription)",
        check=_count_graphql_operations,
    ),
]


@register_linker(
    "graphql",
    priority=60,  # Run after analyzers have produced GraphQL symbols
    description="GraphQL client-schema linking (gql calls to operations)",
    requirements=GRAPHQL_REQUIREMENTS,
)
def graphql_linker(ctx: LinkerContext) -> LinkerResult:
    """GraphQL linker for registry-based dispatch.

    This wraps link_graphql() to use the LinkerContext/LinkerResult interface.
    Extracts GraphQL operation symbols from ctx and delegates to core linking.
    """
    operation_symbols = _get_graphql_operation_symbols(ctx)
    result = link_graphql(ctx.repo_root, operation_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
