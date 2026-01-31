"""Database query linker for detecting SQL queries in application code.

This linker detects SQL queries embedded in Python, JavaScript, and Java code
and links them to table definitions in SQL schema files.

Detected Patterns
-----------------
Python:
- cursor.execute("SELECT * FROM users WHERE ...")
- db.execute("INSERT INTO users ...")
- connection.execute("UPDATE users SET ...")
- session.execute(text("SELECT * FROM users"))

JavaScript/TypeScript:
- db.query("SELECT * FROM users")
- pool.query("INSERT INTO users ...")
- client.query("SELECT * FROM orders")
- knex('users').select() - Knex.js table reference

Java:
- statement.executeQuery("SELECT * FROM users")
- preparedStatement.executeUpdate("INSERT INTO users ...")
- jdbcTemplate.query("SELECT * FROM users", ...)
- @Query("SELECT * FROM users") - Spring Data annotation

Table Extraction
----------------
Tables are extracted from SQL queries using regex patterns:
- FROM table_name
- JOIN table_name
- INTO table_name
- UPDATE table_name
- DELETE FROM table_name

How It Works
------------
1. Scan source files for SQL query patterns
2. Extract table names from each query
3. Match to table symbols from SQL analyzer
4. Create query_references edges linking code to schema

Why This Design
---------------
- Cross-language linking enables full-stack database understanding
- Regex-based detection covers common patterns without SQL parsing
- Table name extraction is fast and handles most SQL dialects
- Symbols for queries enable slice traversal from code to schema
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

PASS_ID = "database-query-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class DatabaseQueryPattern:
    """Represents a detected database query."""

    query_text: str  # The SQL query string
    tables: list[str]  # Tables referenced in the query
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    query_type: str  # SELECT, INSERT, UPDATE, DELETE


@dataclass
class DatabaseQueryLinkResult:
    """Result of database query linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# ============================================================================
# SQL query detection patterns
# ============================================================================

# Python cursor.execute(), db.execute(), connection.execute()
PYTHON_EXECUTE_PATTERN = re.compile(
    r"(?:cursor|db|connection|conn|session|engine)\s*\.\s*execute\s*\(\s*"
    r"(?:text\s*\()?\s*"
    r"(?:\"\"\"([^\"]+)\"\"\"|'''([^']+)'''|\"([^\"]+)\"|'([^']+)')",
    re.MULTILINE | re.IGNORECASE | re.DOTALL,
)

# Python f-string or format string execute
PYTHON_EXECUTE_FSTRING_PATTERN = re.compile(
    r"(?:cursor|db|connection|conn|session)\s*\.\s*execute\s*\(\s*f[\"']([^\"']+)[\"']",
    re.MULTILINE | re.IGNORECASE,
)

# JavaScript/TypeScript db.query(), pool.query(), client.query()
JS_QUERY_PATTERN = re.compile(
    r"(?:db|pool|client|connection|conn)\s*\.\s*query\s*\(\s*"
    r"(?:`([^`]+)`|\"([^\"]+)\"|'([^']+)')",
    re.MULTILINE | re.IGNORECASE | re.DOTALL,
)

# JavaScript Knex.js pattern: knex('table_name')
JS_KNEX_PATTERN = re.compile(
    r"knex\s*\(\s*['\"](\w+)['\"]",
    re.MULTILINE | re.IGNORECASE,
)

# Java executeQuery(), executeUpdate()
JAVA_EXECUTE_PATTERN = re.compile(
    r"(?:statement|preparedStatement|stmt|ps|jdbcTemplate)\s*\.\s*"
    r"(?:executeQuery|executeUpdate|query|update)\s*\(\s*"
    r"\"([^\"]+)\"",
    re.MULTILINE | re.IGNORECASE,
)

# Java Spring @Query annotation
JAVA_QUERY_ANNOTATION_PATTERN = re.compile(
    r"@Query\s*\(\s*(?:value\s*=\s*)?\"([^\"]+)\"",
    re.MULTILINE | re.IGNORECASE,
)

# ============================================================================
# Table extraction patterns
# ============================================================================

# Extract table names from SQL queries
# Handles: FROM table, JOIN table, INTO table, UPDATE table, DELETE FROM table
TABLE_EXTRACTION_PATTERNS = [
    re.compile(r"\bFROM\s+[`\"]?(\w+)[`\"]?", re.IGNORECASE),
    re.compile(r"\bJOIN\s+[`\"]?(\w+)[`\"]?", re.IGNORECASE),
    re.compile(r"\bINTO\s+[`\"]?(\w+)[`\"]?", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+[`\"]?(\w+)[`\"]?", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\s+[`\"]?(\w+)[`\"]?", re.IGNORECASE),
]


def _extract_tables_from_query(query: str) -> list[str]:
    """Extract table names from a SQL query string.

    Args:
        query: SQL query string.

    Returns:
        List of unique table names found in the query.
    """
    tables: set[str] = set()

    for pattern in TABLE_EXTRACTION_PATTERNS:
        for match in pattern.finditer(query):
            table_name = match.group(1).lower()
            # Skip common SQL keywords that might be captured
            if table_name not in ("select", "set", "values", "where", "and", "or"):
                tables.add(table_name)

    return list(tables)


def _detect_query_type(query: str) -> str:
    """Detect the type of SQL query.

    Args:
        query: SQL query string.

    Returns:
        Query type: SELECT, INSERT, UPDATE, DELETE, or OTHER.
    """
    query_upper = query.strip().upper()

    if query_upper.startswith("SELECT"):
        return "SELECT"
    elif query_upper.startswith("INSERT"):
        return "INSERT"
    elif query_upper.startswith("UPDATE"):
        return "UPDATE"
    elif query_upper.startswith("DELETE"):
        return "DELETE"
    else:
        return "OTHER"


def _find_source_files(root: Path) -> Iterator[Path]:
    """Find files that might contain database queries."""
    patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.java"]
    for path in find_files(root, patterns):
        yield path


def _detect_language(file_path: Path) -> str:
    """Detect language from file extension."""
    ext = file_path.suffix.lower()
    if ext == ".py":
        return "python"
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        return "javascript"
    elif ext == ".java":
        return "java"
    return "unknown"  # pragma: no cover


def _scan_python_queries(file_path: Path, content: str) -> list[DatabaseQueryPattern]:
    """Scan Python file for database query patterns."""
    patterns: list[DatabaseQueryPattern] = []

    for match in PYTHON_EXECUTE_PATTERN.finditer(content):
        # Get query from whichever group matched
        query = match.group(1) or match.group(2) or match.group(3) or match.group(4) or ""
        if not query.strip():  # pragma: no cover
            continue

        line = content[: match.start()].count("\n") + 1
        tables = _extract_tables_from_query(query)

        if tables:  # Only create pattern if we found tables
            patterns.append(DatabaseQueryPattern(
                query_text=query.strip()[:200],
                tables=tables,
                line=line,
                file_path=str(file_path),
                language="python",
                query_type=_detect_query_type(query),
            ))

    # Also check for f-string patterns
    for match in PYTHON_EXECUTE_FSTRING_PATTERN.finditer(content):
        query = match.group(1)
        line = content[: match.start()].count("\n") + 1
        tables = _extract_tables_from_query(query)

        if tables:  # pragma: no cover - f-string table extraction is unreliable
            patterns.append(DatabaseQueryPattern(
                query_text=query.strip()[:200],
                tables=tables,
                line=line,
                file_path=str(file_path),
                language="python",
                query_type=_detect_query_type(query),
            ))

    return patterns


def _scan_javascript_queries(file_path: Path, content: str) -> list[DatabaseQueryPattern]:
    """Scan JavaScript/TypeScript file for database query patterns."""
    patterns: list[DatabaseQueryPattern] = []

    for match in JS_QUERY_PATTERN.finditer(content):
        query = match.group(1) or match.group(2) or match.group(3) or ""
        if not query.strip():  # pragma: no cover
            continue

        line = content[: match.start()].count("\n") + 1
        tables = _extract_tables_from_query(query)

        if tables:
            patterns.append(DatabaseQueryPattern(
                query_text=query.strip()[:200],
                tables=tables,
                line=line,
                file_path=str(file_path),
                language="javascript",
                query_type=_detect_query_type(query),
            ))

    # Knex.js table references
    for match in JS_KNEX_PATTERN.finditer(content):
        table_name = match.group(1)
        line = content[: match.start()].count("\n") + 1

        patterns.append(DatabaseQueryPattern(
            query_text=f"knex('{table_name}')",
            tables=[table_name.lower()],
            line=line,
            file_path=str(file_path),
            language="javascript",
            query_type="SELECT",  # Knex defaults to select
        ))

    return patterns


def _scan_java_queries(file_path: Path, content: str) -> list[DatabaseQueryPattern]:
    """Scan Java file for database query patterns."""
    patterns: list[DatabaseQueryPattern] = []

    for match in JAVA_EXECUTE_PATTERN.finditer(content):
        query = match.group(1)
        line = content[: match.start()].count("\n") + 1
        tables = _extract_tables_from_query(query)

        if tables:
            patterns.append(DatabaseQueryPattern(
                query_text=query.strip()[:200],
                tables=tables,
                line=line,
                file_path=str(file_path),
                language="java",
                query_type=_detect_query_type(query),
            ))

    # Spring @Query annotations
    for match in JAVA_QUERY_ANNOTATION_PATTERN.finditer(content):
        query = match.group(1)
        line = content[: match.start()].count("\n") + 1
        tables = _extract_tables_from_query(query)

        if tables:
            patterns.append(DatabaseQueryPattern(
                query_text=query.strip()[:200],
                tables=tables,
                line=line,
                file_path=str(file_path),
                language="java",
                query_type=_detect_query_type(query),
            ))

    return patterns


def _scan_file(file_path: Path, content: str) -> list[DatabaseQueryPattern]:
    """Scan a file for database query patterns."""
    language = _detect_language(file_path)
    if language == "python":
        return _scan_python_queries(file_path, content)
    elif language == "javascript":
        return _scan_javascript_queries(file_path, content)
    elif language == "java":
        return _scan_java_queries(file_path, content)
    return []  # pragma: no cover


def _create_query_symbol(pattern: DatabaseQueryPattern, root: Path) -> Symbol:
    """Create a symbol for a database query."""
    try:
        rel_path = Path(pattern.file_path).relative_to(root)
    except ValueError:  # pragma: no cover
        rel_path = Path(pattern.file_path)

    tables_str = ", ".join(pattern.tables)

    return Symbol(
        id=f"{rel_path}::db_query::{pattern.line}",
        name=f"{pattern.query_type} {tables_str}",
        kind="db_query",
        path=pattern.file_path,
        span=Span(
            start_line=pattern.line,
            start_col=0,
            end_line=pattern.line,
            end_col=0,
        ),
        language=pattern.language,
        stable_id=f"{pattern.query_type}:{','.join(sorted(pattern.tables))}",
        meta={
            "query_type": pattern.query_type,
            "tables": pattern.tables,
            "query_preview": pattern.query_text[:100],
        },
    )


def link_database_queries(root: Path, table_symbols: list[Symbol]) -> DatabaseQueryLinkResult:
    """Link database queries to table definitions.

    Args:
        root: Repository root path.
        table_symbols: Table symbols from SQL analyzer.

    Returns:
        DatabaseQueryLinkResult with edges linking queries to tables.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[DatabaseQueryPattern] = []
    files_scanned = 0

    # Collect all query patterns
    for file_path in _find_source_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
            patterns = _scan_file(file_path, content)
            all_patterns.extend(patterns)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Build table lookup: table_name -> symbol
    table_lookup: dict[str, Symbol] = {}
    for sym in table_symbols:
        if sym.kind == "table":
            table_lookup[sym.name.lower()] = sym

    # Create symbols and edges
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for pattern in all_patterns:
        query_symbol = _create_query_symbol(pattern, root)
        query_symbol.origin = PASS_ID
        query_symbol.origin_run_id = run.execution_id
        symbols.append(query_symbol)

        # Link to each referenced table
        for table_name in pattern.tables:
            if table_name in table_lookup:
                table_sym = table_lookup[table_name]
                is_cross_language = query_symbol.language != table_sym.language

                edge = Edge.create(
                    src=query_symbol.id,
                    dst=table_sym.id,
                    edge_type="query_references",
                    line=pattern.line,
                    confidence=0.85,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="table_name_match",
                )
                edge.meta = {
                    "table_name": table_name,
                    "query_type": pattern.query_type,
                    "cross_language": is_cross_language,
                }
                edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return DatabaseQueryLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


def _get_table_symbols(ctx: LinkerContext) -> list[Symbol]:
    """Extract table symbols from context for linking."""
    return [s for s in ctx.symbols if s.kind == "table"]


def _count_table_symbols(ctx: LinkerContext) -> int:
    """Count available table symbols for requirement check."""
    return sum(1 for s in ctx.symbols if s.kind == "table")


DATABASE_QUERY_REQUIREMENTS = [
    LinkerRequirement(
        name="table_symbols",
        description="SQL table symbols from schema files",
        check=_count_table_symbols,
    ),
]


@register_linker(
    "database_query",
    priority=70,  # Run after SQL analyzer has produced table symbols
    description="Database query linking (SQL queries in code to schema tables)",
    requirements=DATABASE_QUERY_REQUIREMENTS,
)
def database_query_linker(ctx: LinkerContext) -> LinkerResult:
    """Database query linker for registry-based dispatch.

    This wraps link_database_queries() to use the LinkerContext/LinkerResult interface.
    Extracts table symbols from ctx and delegates to core linking.
    """
    table_symbols = _get_table_symbols(ctx)
    result = link_database_queries(ctx.repo_root, table_symbols)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
