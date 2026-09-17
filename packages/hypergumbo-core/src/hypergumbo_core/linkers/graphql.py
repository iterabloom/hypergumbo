# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framework linker: GraphQL client-schema for detecting cross-file GraphQL calls.

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
4. Create canonical 'calls' edges with meta['protocol']='graphql' linking
   client to server (post WI-vumum-juvil; pre-fold name was graphql_calls)

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

from ..analyze.base import make_symbol_id, sanitize_id_name_segment
from ..discovery import find_non_test_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import LinkerActivation, LinkerContext, LinkerResult, LinkerRequirement, register_linker
from ._text_filters import read_masked_source

PASS_ID = make_pass_id("graphql-linker")


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

# The first field selected inside an operation's selection set — the root
# field, which is what the operation actually invokes on the server. The
# selection set is the first BRACE group: variable definitions are
# parenthesised (``query GetUser($id: ID!) { user(...) }``), so the first ``{``
# opens the selection set unless a variable carries an object default.
# Leading ``#`` comment lines inside the set are skipped.
ROOT_FIELD_PATTERN = re.compile(
    r"\{\s*(?:#[^\n]*\n\s*)*([A-Za-z_]\w*)",
)

# The three root types a root field can be declared on.
_ROOT_TYPE_FOR_OPERATION = {
    "query": "query",
    "mutation": "mutation",
    "subscription": "subscription",
}


def _root_field(query: str) -> str | None:
    """The root field an operation selects, or None.

    A fragment definition has a selection set too, but it selects fields on a
    named type rather than invoking a root field, so it is excluded — matching
    :func:`_extract_operation_name`, which already refuses to call a fragment
    an operation.
    """
    if query.lstrip().startswith("fragment"):
        return None
    match = ROOT_FIELD_PATTERN.search(query)
    return match.group(1) if match else None


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
    for path in find_non_test_files(root, patterns):
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

    # ADR-0027 Phase 3 / audit-findings 0013: framework-role leak.
    # Fold to canonical kind="function" + meta["framework_role"].
    return Symbol(
        id=make_symbol_id(call.language, str(rel_path), call.line, call.line, sanitize_id_name_segment(name), "function"),
        name=name,
        kind="function",
        path=call.file_path,
        span=Span(
            start_line=call.line,
            start_col=0,
            end_line=call.line,
            end_col=0,
        ),
        # ADR-0031 Class B: synthetic stand-in for a GraphQL client call.
        language=None,
        discovery_language=call.language,
        protocol_origin="graphql",
        # INV-hunup / ADR-0035 §1: Class-B stand-in. Emit None so the
        # post-linker populate_synthetic_class_b_identity chokepoint stamps a
        # canonical injective stable_id keyed on (protocol_origin, kind, path,
        # name, occurrence). Self-stamping call.operation_name was non-canonical
        # AND collision-prone (operation names repeat across files). The
        # operation name is preserved in meta["operation_name"].
        stable_id=None,
        meta={
            "operation_type": call.operation_type,
            "operation_name": call.operation_name,
            "query_text": call.query_text[:100] + "..." if len(call.query_text) > 100 else call.query_text,
            "framework_role": "graphql_client",
        },
    )


def link_graphql(
    root: Path,
    schema_symbols: list[Symbol],
    schema_fields: list[Symbol] | None = None,
) -> GraphQLLinkResult:
    """Link GraphQL client calls to schema definitions.

    Two joins, and they answer different questions (WI-dinum).

    **Operation name → operation name** is the original, and it is not a
    client-to-server join at all. A client operation NAME is an arbitrary
    client-side label; a schema does not declare one. This match only succeeds
    when some other document in the repository declares an operation of the
    same name — a codegen ``.graphql`` document, typically — so what it
    actually recovers is document-to-document identity. Real, worth keeping,
    but it is why the linker emitted **0 edges in 14 corpus runs**: it can
    never fire on a repository that has no ``.graphql`` documents, which is
    most of them.

    **Root field → schema field** is the client-to-server join.
    ``query GetUsers { users { id } }`` invokes ``Query.users``, and
    ``Query.users`` is a thing a schema really does declare. With
    ``graphql-sdl-linker`` supplying schema fields out of embedded SDL, this
    fires on repositories that keep their schema in a ``gql`` template — 22 of
    apollo-server's 29 client calls resolve this way.

    Args:
        root: Repository root path.
        schema_symbols: Operation symbols (kind query/mutation/subscription/
            operation), from the GraphQL analyzer's parse of a ``.graphql``
            document.
        schema_fields: Schema FIELD symbols carrying ``meta["parent_type"]``,
            from ``graphql-sdl-linker`` or the analyzer. Optional so the
            operation-name join keeps working for a caller that has none.

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

    # Build `<root type>.<field>` to symbol mapping, keyed the same way
    # graphql_resolver.py keys its own schema lookup.
    field_map: dict[str, Symbol] = {}
    for sym in schema_fields or []:
        parent = (sym.meta or {}).get("parent_type", "")
        if parent:
            field_map[f"{parent.lower()}.{sym.name.lower()}"] = sym

    # Collect all GraphQL client calls
    all_calls: list[GraphQLClientCall] = []

    for file_path in _find_source_files(root):
        try:
            content = read_masked_source(file_path, encoding="utf-8", errors="ignore")
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
        client_symbol.origin = [PASS_ID]
        client_symbol.origin_run_id = run.execution_id
        symbols.append(client_symbol)

        # JOIN 1 — same-named operation in another document.
        if call.operation_name:
            op_key = call.operation_name.lower()
            if op_key in operation_map:
                schema_sym = operation_map[op_key]

                # ADR-0023 §6 Phase 3 (WI-vumum-juvil): GraphQL is a
                # wire protocol, not a relationship. The fold target is
                # canonical 'calls' + meta['protocol']='graphql'.
                edge = Edge.create(
                    src=client_symbol.id,
                    dst=schema_sym.id,
                    edge_type="calls",
                    line=call.line,
                    # The former `0.9 if call.operation_name else 0.7` sat
                    # INSIDE `if call.operation_name:`, so the 0.7 arm could
                    # not be reached (WI-dinum). Stated as the constant it
                    # always was rather than left looking like a decision.
                    confidence=0.9,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="ast_call_direct",
                    derived_from=[client_symbol.id, schema_sym.id],
                )
                # ADR-0028 Phase 3 / audit-findings 0014: framework-dispatch
                # leak; meta["framework_dispatch"]="graphql_operation".
                edge.meta = {
                    "protocol": "graphql",
                    "operation_type": call.operation_type,
                    "operation_name": call.operation_name,
                    "framework_dispatch": "graphql_operation",
                }
                edges.append(edge)
                continue

        # JOIN 2 — the root field the operation actually invokes.
        root_field = _root_field(call.query_text)
        root_type = _ROOT_TYPE_FOR_OPERATION.get(call.operation_type or "query")
        if root_field is None or root_type is None:
            continue
        schema_field = field_map.get(f"{root_type}.{root_field.lower()}")
        if schema_field is None:
            continue
        edge = Edge.create(
            src=client_symbol.id,
            dst=schema_field.id,
            edge_type="calls",
            line=call.line,
            # Below the name-identity join: that one matches a declared name
            # exactly, this one reads the first field of a selection set with
            # a regex and could be wrong about a query whose variable default
            # opens a brace before the selection set does.
            confidence=0.8,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="ast_call_direct",
            derived_from=[client_symbol.id, schema_field.id],
        )
        edge.meta = {
            "protocol": "graphql",
            "operation_type": call.operation_type,
            "operation_name": call.operation_name,
            "root_field": root_field,
            "framework_dispatch": "graphql_operation",
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


def _get_graphql_schema_field_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract GraphQL schema FIELD symbols from context.

    The destination of the root-field join. Produced by
    ``graphql-sdl-linker`` from SDL embedded in a ``gql`` template (WI-dinum);
    the GraphQL analyzer has never emitted this kind.
    """
    return [
        s for s in ctx.symbols
        if s.language == "graphql"
        and s.kind == "field"
        and (s.meta or {}).get("parent_type")
    ]


def _count_graphql_operations(ctx: LinkerContext) -> int:
    """Count available GraphQL operation symbols for requirement check."""
    return len(_get_graphql_operation_symbols(ctx))


def _count_graphql_schema_fields(ctx: LinkerContext) -> int:
    """Count available GraphQL schema field symbols for requirement check."""
    return len(_get_graphql_schema_field_symbols(ctx))


# Two requirements, because the linker now has two joins with different
# destinations and reporting only the first would describe a linker that can
# work as one that cannot (these are diagnostics, not a gate).
GRAPHQL_REQUIREMENTS = [
    LinkerRequirement(
        name="graphql_operations",
        description="GraphQL operation symbols (query/mutation/subscription)",
        check=_count_graphql_operations,
    ),
    LinkerRequirement(
        name="graphql_schema_fields",
        description="GraphQL schema field symbols (root-field join targets)",
        check=_count_graphql_schema_fields,
    ),
]


@register_linker(
    "graphql-linker",
    priority=60,  # Run after analyzers have produced GraphQL symbols
    description="GraphQL client-schema linking (gql calls to operations)",
    requirements=GRAPHQL_REQUIREMENTS,
    activation=LinkerActivation(frameworks=["graphql"]),
    # CNF: the passes that can supply the destination of either join —
    # ``graphql`` for a .graphql document's operations, ``graphql-sdl-linker``
    # for schema fields lifted out of an embedded gql template (WI-dinum).
    # The former list named six HOST languages, which is where the CLIENT call
    # is found; this linker reads those files itself and consumes no host
    # analyzer's output, so naming them was the too-wide shape WI-rasal
    # repaired elsewhere.
    depends_on=[["graphql", "graphql-sdl-linker"]],
)
def graphql_linker(ctx: LinkerContext) -> LinkerResult:
    """GraphQL linker for registry-based dispatch.

    This wraps link_graphql() to use the LinkerContext/LinkerResult interface.
    Extracts GraphQL operation symbols from ctx and delegates to core linking.
    """
    operation_symbols = _get_graphql_operation_symbols(ctx)
    field_symbols = _get_graphql_schema_field_symbols(ctx)
    result = link_graphql(ctx.repo_root, operation_symbols, field_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
