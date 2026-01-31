"""GraphQL resolver linker for detecting resolver implementations.

This linker detects GraphQL resolver functions in JavaScript/Python and links
them to GraphQL schema type definitions.

Detected Patterns
-----------------
JavaScript (Apollo Server, graphql-yoga):
- const resolvers = { Query: { users: (_, args, ctx) => ... } }
- const resolvers = { User: { posts: (parent) => ... } }

Python (Ariadne):
- @query.field("users") def resolve_users(_, info): ...
- @mutation.field("createUser") def resolve_create_user(_, info, input): ...

Python (Strawberry):
- @strawberry.field def users(self) -> List[User]: ...
- @strawberry.mutation def create_user(self, input: UserInput) -> User: ...

How It Works
------------
1. Scan source files for resolver patterns
2. Extract type name and field name from each resolver
3. Match to schema type/field symbols
4. Create resolver_implements edges linking resolvers to schema

Why This Design
---------------
- Resolvers bridge schema to implementation - critical for full-stack understanding
- Regex-based detection covers common patterns without AST parsing
- Separate linker keeps GraphQL concerns modular
- Enables slice traversal from schema to implementation
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

PASS_ID = "graphql-resolver-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class ResolverPattern:
    """Represents a detected resolver implementation."""

    type_name: str  # e.g., 'Query', 'User', 'Mutation'
    field_name: str  # e.g., 'users', 'posts', 'createUser'
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language


@dataclass
class ResolverLinkResult:
    """Result of resolver linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# ============================================================================
# JavaScript resolver patterns (Apollo Server, graphql-yoga, etc.)
# ============================================================================

# Match: Query: { users: ... } or Mutation: { createUser: ... }
# Captures type name and field name from resolver object pattern
JS_RESOLVER_OBJECT_PATTERN = re.compile(
    r"^\s*(Query|Mutation|Subscription|[A-Z][a-zA-Z0-9_]*)\s*:\s*\{",
    re.MULTILINE,
)

# Match field resolvers inside type object: fieldName: (parent, args, ctx) => ...
# or fieldName: async (parent, args, ctx) => ...
# Also handles TypeScript type annotations: fieldName: async (_: any, args: Type): ReturnType => ...
JS_FIELD_RESOLVER_PATTERN = re.compile(
    r"^\s*(\w+)\s*:\s*(?:async\s*)?\([^)]*\)(?:\s*:\s*[^=]+)?\s*=>",
    re.MULTILINE,
)

# Match field resolvers with function keyword: fieldName: function(parent, args)
JS_FIELD_RESOLVER_FUNC_PATTERN = re.compile(
    r"^\s*(\w+)\s*:\s*(?:async\s*)?function\s*\(",
    re.MULTILINE,
)

# Match field resolvers with shorthand: async fieldName(parent, args, ctx)
JS_FIELD_RESOLVER_SHORTHAND_PATTERN = re.compile(
    r"^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)

# ============================================================================
# Python resolver patterns (Ariadne)
# ============================================================================

# Match: @query.field("fieldName") or @mutation.field("fieldName")
ARIADNE_RESOLVER_PATTERN = re.compile(
    r"@(query|mutation|subscription|type)\s*\.\s*field\s*\(\s*['\"](\w+)['\"]",
    re.MULTILINE | re.IGNORECASE,
)

# Match: @user_type.field("fieldName") for custom type resolvers
# Captures the type name from variable like user_type, post_type, etc.
ARIADNE_TYPE_RESOLVER_PATTERN = re.compile(
    r"@(\w+)_type\s*\.\s*field\s*\(\s*['\"](\w+)['\"]",
    re.MULTILINE | re.IGNORECASE,
)

# ============================================================================
# Python resolver patterns (Strawberry)
# ============================================================================

# Match: @strawberry.field or @strawberry.mutation
STRAWBERRY_FIELD_PATTERN = re.compile(
    r"@strawberry\s*\.\s*(field|mutation)\s*(?:\([^)]*\))?\s*\n\s*(?:async\s+)?def\s+(\w+)",
    re.MULTILINE,
)

# Match: class Query: with @strawberry.type
STRAWBERRY_TYPE_CLASS_PATTERN = re.compile(
    r"@strawberry\s*\.\s*type\s*(?:\([^)]*\))?\s*\n\s*class\s+(\w+)",
    re.MULTILINE,
)


def _find_source_files(root: Path) -> Iterator[Path]:
    """Find files that might contain resolver implementations."""
    patterns = ["**/*.py", "**/*.js", "**/*.ts"]
    for path in find_files(root, patterns):
        yield path


def _detect_language(file_path: Path) -> str:
    """Detect language from file extension."""
    ext = file_path.suffix.lower()
    if ext == ".py":
        return "python"
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        return "javascript"
    return "unknown"  # pragma: no cover


def _scan_javascript_resolvers(file_path: Path, content: str) -> list[ResolverPattern]:
    """Scan JavaScript/TypeScript file for resolver patterns."""
    patterns: list[ResolverPattern] = []

    # Find type objects (Query: {, User: {, etc.)
    lines = content.split("\n")
    current_type = None
    brace_depth = 0

    for line_num, line in enumerate(lines, 1):
        # Track brace depth
        brace_depth += line.count("{") - line.count("}")

        # Check for type start: Query: { or User: {
        type_match = JS_RESOLVER_OBJECT_PATTERN.search(line)
        if type_match:
            current_type = type_match.group(1)
            continue

        # Only look for field resolvers if we're inside a type
        if current_type and brace_depth > 0:
            # Check for arrow function resolver
            field_match = JS_FIELD_RESOLVER_PATTERN.search(line)
            if field_match:
                field_name = field_match.group(1)
                # Skip object shorthand that's not a resolver (e.g., __typename)
                if not field_name.startswith("_"):
                    patterns.append(ResolverPattern(
                        type_name=current_type,
                        field_name=field_name,
                        line=line_num,
                        file_path=str(file_path),
                        language="javascript",
                    ))
                continue

            # Check for function keyword resolver
            func_match = JS_FIELD_RESOLVER_FUNC_PATTERN.search(line)
            if func_match:
                field_name = func_match.group(1)
                patterns.append(ResolverPattern(
                    type_name=current_type,
                    field_name=field_name,
                    line=line_num,
                    file_path=str(file_path),
                    language="javascript",
                ))
                continue

            # Check for shorthand method resolver
            shorthand_match = JS_FIELD_RESOLVER_SHORTHAND_PATTERN.search(line)
            if shorthand_match:
                field_name = shorthand_match.group(1)
                # Skip constructor and internal methods
                if not field_name.startswith("_") and field_name not in ("constructor",):
                    patterns.append(ResolverPattern(
                        type_name=current_type,
                        field_name=field_name,
                        line=line_num,
                        file_path=str(file_path),
                        language="javascript",
                    ))

        # Reset type when we exit the object
        if brace_depth == 0:
            current_type = None

    return patterns


def _scan_python_resolvers(file_path: Path, content: str) -> list[ResolverPattern]:
    """Scan Python file for resolver patterns."""
    patterns: list[ResolverPattern] = []

    # Ariadne patterns: @query.field("users"), @mutation.field("createUser")
    for match in ARIADNE_RESOLVER_PATTERN.finditer(content):
        type_name = match.group(1).capitalize()  # query -> Query
        field_name = match.group(2)
        line_num = content[: match.start()].count("\n") + 1

        patterns.append(ResolverPattern(
            type_name=type_name,
            field_name=field_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    # Ariadne custom type patterns: @user_type.field("posts")
    for match in ARIADNE_TYPE_RESOLVER_PATTERN.finditer(content):
        type_name = match.group(1).capitalize()  # user -> User
        field_name = match.group(2)
        line_num = content[: match.start()].count("\n") + 1

        patterns.append(ResolverPattern(
            type_name=type_name,
            field_name=field_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    # Strawberry patterns: @strawberry.field def users(self)
    # First find the containing class if it's a @strawberry.type
    strawberry_classes: dict[int, str] = {}  # line -> class name
    for match in STRAWBERRY_TYPE_CLASS_PATTERN.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        class_name = match.group(1)
        strawberry_classes[line_num] = class_name

    # Then find @strawberry.field decorators and associate with their class
    for match in STRAWBERRY_FIELD_PATTERN.finditer(content):
        decorator_type = match.group(1)  # field or mutation
        method_name = match.group(2)
        line_num = content[: match.start()].count("\n") + 1

        # Determine type name from decorator or enclosing class
        if decorator_type == "mutation":
            type_name = "Mutation"
        else:
            # Find enclosing class by looking for nearest class above this line
            enclosing_class = None
            for class_line, class_name in sorted(strawberry_classes.items(), reverse=True):
                if class_line < line_num:
                    enclosing_class = class_name
                    break
            type_name = enclosing_class or "Query"

        patterns.append(ResolverPattern(
            type_name=type_name,
            field_name=method_name,
            line=line_num,
            file_path=str(file_path),
            language="python",
        ))

    return patterns


def _scan_file(file_path: Path, content: str) -> list[ResolverPattern]:
    """Scan a file for resolver patterns."""
    language = _detect_language(file_path)
    if language == "python":
        return _scan_python_resolvers(file_path, content)
    elif language == "javascript":
        return _scan_javascript_resolvers(file_path, content)
    return []  # pragma: no cover


def _create_resolver_symbol(pattern: ResolverPattern, root: Path) -> Symbol:
    """Create a symbol for a resolver implementation."""
    try:
        rel_path = Path(pattern.file_path).relative_to(root)
    except ValueError:  # pragma: no cover
        rel_path = Path(pattern.file_path)

    return Symbol(
        id=f"{rel_path}::resolver::{pattern.line}",
        name=f"{pattern.type_name}.{pattern.field_name}",
        kind="graphql_resolver",
        path=pattern.file_path,
        span=Span(
            start_line=pattern.line,
            start_col=0,
            end_line=pattern.line,
            end_col=0,
        ),
        language=pattern.language,
        stable_id=f"{pattern.type_name}.{pattern.field_name}",
        meta={
            "type_name": pattern.type_name,
            "field_name": pattern.field_name,
        },
    )


def link_graphql_resolvers(root: Path, schema_symbols: list[Symbol]) -> ResolverLinkResult:
    """Link GraphQL resolver implementations to schema definitions.

    Args:
        root: Repository root path.
        schema_symbols: Symbols from GraphQL analyzer (types, fields).

    Returns:
        ResolverLinkResult with edges linking resolvers to schema.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[ResolverPattern] = []
    files_scanned = 0

    # Collect all resolver patterns
    for file_path in _find_source_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
            patterns = _scan_file(file_path, content)
            all_patterns.extend(patterns)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Build schema lookup: type.field -> symbol
    schema_lookup: dict[str, Symbol] = {}
    type_lookup: dict[str, Symbol] = {}

    for sym in schema_symbols:
        if sym.kind == "type":
            type_lookup[sym.name.lower()] = sym
        # For fields, use parent.field format if available
        if sym.kind == "field" and sym.meta:
            parent = sym.meta.get("parent_type", "")
            if parent:
                key = f"{parent.lower()}.{sym.name.lower()}"
                schema_lookup[key] = sym

    # Create symbols and edges
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for pattern in all_patterns:
        resolver_symbol = _create_resolver_symbol(pattern, root)
        resolver_symbol.origin = PASS_ID
        resolver_symbol.origin_run_id = run.execution_id
        symbols.append(resolver_symbol)

        # Try to link to schema field
        field_key = f"{pattern.type_name.lower()}.{pattern.field_name.lower()}"
        if field_key in schema_lookup:
            schema_sym = schema_lookup[field_key]
            is_cross_language = resolver_symbol.language != schema_sym.language

            edge = Edge.create(
                src=resolver_symbol.id,
                dst=schema_sym.id,
                edge_type="resolver_implements",
                line=pattern.line,
                confidence=0.9,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="resolver_field_match",
            )
            edge.meta = {
                "type_name": pattern.type_name,
                "field_name": pattern.field_name,
                "cross_language": is_cross_language,
            }
            edges.append(edge)

        # Also try to link to the type itself
        type_key = pattern.type_name.lower()
        if type_key in type_lookup:
            type_sym = type_lookup[type_key]
            is_cross_language = resolver_symbol.language != type_sym.language

            edge = Edge.create(
                src=resolver_symbol.id,
                dst=type_sym.id,
                edge_type="resolver_for_type",
                line=pattern.line,
                confidence=0.8,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="resolver_type_match",
            )
            edge.meta = {
                "type_name": pattern.type_name,
                "field_name": pattern.field_name,
                "cross_language": is_cross_language,
            }
            edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return ResolverLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


def _get_graphql_schema_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract GraphQL schema symbols from context.

    Schema symbols are from the GraphQL analyzer with:
    - language="graphql"
    - kind in ("type", "field", "interface")
    """
    return [
        s for s in ctx.symbols
        if s.language == "graphql"
        and s.kind in ("type", "field", "interface")
    ]


def _count_graphql_schema(ctx: LinkerContext) -> int:
    """Count available GraphQL schema symbols for requirement check."""
    return len(_get_graphql_schema_symbols(ctx))


RESOLVER_REQUIREMENTS = [
    LinkerRequirement(
        name="graphql_schema",
        description="GraphQL schema symbols (type/field/interface)",
        check=_count_graphql_schema,
    ),
]


@register_linker(
    "graphql_resolver",
    priority=60,  # Run after analyzers have produced GraphQL symbols
    description="GraphQL resolver linking (resolvers to schema types/fields)",
    requirements=RESOLVER_REQUIREMENTS,
)
def graphql_resolver_linker(ctx: LinkerContext) -> LinkerResult:
    """GraphQL resolver linker for registry-based dispatch.

    This wraps link_graphql_resolvers() to use the LinkerContext/LinkerResult interface.
    Extracts GraphQL schema symbols from ctx and delegates to core linking.
    """
    schema_symbols = _get_graphql_schema_symbols(ctx)
    result = link_graphql_resolvers(ctx.repo_root, schema_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
