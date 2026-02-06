"""Branch coverage tests for database_query.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.database_query import (
    _extract_tables_from_query,
    _scan_javascript_queries,
    _scan_java_queries,
    link_database_queries,
)
from hypergumbo_core.ir import Symbol, Span


def make_symbol(
    id_: str,
    name: str,
    kind: str = "table",
    path: str = "schema.sql",
    language: str = "sql",
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language=language,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
    )


class TestExtractTablesFromQuery:
    """Branch coverage for _extract_tables_from_query function."""

    def test_table_name_is_sql_keyword(self) -> None:
        """Test when extracted table name is a SQL keyword (branch 161->158).

        Some queries might contain constructs that look like table names
        but are actually SQL keywords. These should be skipped.
        """
        # Query where "select" might be matched as a table name
        # FROM select doesn't make sense but tests the filter
        tables = _extract_tables_from_query("SELECT * FROM select")
        # "select" should be filtered out
        assert "select" not in tables

    def test_table_name_is_values_keyword(self) -> None:
        """Test VALUES keyword filtering."""
        # VALUES is a SQL keyword that should be filtered
        tables = _extract_tables_from_query("INSERT INTO values (a, b)")
        assert "values" not in tables

    def test_table_name_is_where_keyword(self) -> None:
        """Test WHERE keyword filtering."""
        tables = _extract_tables_from_query("SELECT * FROM where")
        assert "where" not in tables

    def test_table_name_is_set_keyword(self) -> None:
        """Test SET keyword filtering."""
        tables = _extract_tables_from_query("SELECT * FROM set")
        assert "set" not in tables

    def test_normal_table_extraction(self) -> None:
        """Test that normal table names are extracted."""
        tables = _extract_tables_from_query("SELECT * FROM users JOIN orders ON users.id = orders.user_id")
        assert "users" in tables
        assert "orders" in tables


class TestScanJavascriptQueries:
    """Branch coverage for _scan_javascript_queries function."""

    def test_query_with_no_tables(self) -> None:
        """Test when query extracts no tables (branch 263->255).

        When a JavaScript raw SQL query has no extractable tables,
        no pattern should be created.
        """
        content = '''
db.query("SELECT 1 + 1")
'''
        patterns = _scan_javascript_queries(Path("app.js"), content)
        # Query exists but has no tables - no pattern created
        assert len(patterns) == 0

    def test_query_with_only_keywords(self) -> None:
        """Test query that would extract only SQL keywords."""
        content = '''
pool.query("SELECT * FROM select WHERE x = 1")
'''
        patterns = _scan_javascript_queries(Path("app.js"), content)
        # "select" is filtered as keyword, no tables remain
        assert len(patterns) == 0


class TestScanJavaQueries:
    """Branch coverage for _scan_java_queries function."""

    def test_execute_query_with_no_tables(self) -> None:
        """Test executeQuery with no tables (branch 299->294).

        When a Java executeQuery has no extractable tables,
        no pattern should be created.
        """
        content = '''
statement.executeQuery("SELECT 1 + 1 AS result");
'''
        patterns = _scan_java_queries(Path("App.java"), content)
        # Query has no tables
        assert len(patterns) == 0

    def test_query_annotation_with_no_tables(self) -> None:
        """Test @Query annotation with no tables (branch 315->310).

        When a Spring @Query annotation has no extractable tables,
        no pattern should be created.
        """
        content = '''
@Query("SELECT CURRENT_TIMESTAMP")
public Date getCurrentTime();
'''
        patterns = _scan_java_queries(Path("Repository.java"), content)
        # Query has no tables
        assert len(patterns) == 0

    def test_execute_with_only_keywords(self) -> None:
        """Test execute with query that extracts only SQL keywords."""
        content = '''
statement.executeQuery("SELECT * FROM values");
'''
        patterns = _scan_java_queries(Path("App.java"), content)
        # "values" is filtered as keyword, no tables remain
        assert len(patterns) == 0


class TestLinkDatabaseQueries:
    """Branch coverage for link_database_queries function."""

    def test_table_symbol_with_wrong_kind(self, tmp_path: Path) -> None:
        """Test when table_symbols contains non-table symbols (branch 399->398).

        Symbols with kind != "table" should be skipped when building
        the table lookup.
        """
        # Create a Python file with a query
        py_file = tmp_path / "app.py"
        py_file.write_text('''
cursor.execute("SELECT * FROM users")
''')

        # Pass symbols that aren't tables
        non_table_symbols = [
            make_symbol("id1", "users", kind="column"),  # column, not table
            make_symbol("id2", "orders", kind="index"),  # index, not table
        ]

        result = link_database_queries(tmp_path, non_table_symbols)
        # No edges created because no table symbols to link to
        assert len(result.edges) == 0
        # But query symbols are still created
        assert len(result.symbols) >= 1

    def test_mixed_table_and_non_table_symbols(self, tmp_path: Path) -> None:
        """Test with mix of table and non-table symbols."""
        py_file = tmp_path / "app.py"
        py_file.write_text('''
cursor.execute("SELECT * FROM users")
''')

        symbols = [
            make_symbol("id1", "users", kind="table"),  # table - should match
            make_symbol("id2", "users_idx", kind="index"),  # not table - skipped
        ]

        result = link_database_queries(tmp_path, symbols)
        # Should create edge to the table symbol
        assert len(result.edges) == 1
        # Edge should link to the table (id1)
        assert any(e.dst == "id1" for e in result.edges)

    def test_empty_table_symbols(self, tmp_path: Path) -> None:
        """Test with empty table_symbols list."""
        py_file = tmp_path / "app.py"
        py_file.write_text('''
cursor.execute("SELECT * FROM users")
''')

        result = link_database_queries(tmp_path, [])
        # Query symbol created but no edges
        assert len(result.symbols) >= 1
        assert len(result.edges) == 0
