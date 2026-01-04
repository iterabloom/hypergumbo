"""Tests for SQL analyzer using tree-sitter-sql.

Tests verify that the analyzer correctly extracts:
- Table definitions
- View definitions
- Function/procedure definitions
- Trigger definitions
- Index definitions
- Foreign key references (as edges)
"""

from hypergumbo.analyze.sql import (
    PASS_ID,
    PASS_VERSION,
    SQLAnalysisResult,
    analyze_sql_files,
    find_sql_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "sql-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_simple_table(tmp_path):
    """Test detection of simple table definition."""
    sql_file = tmp_path / "schema.sql"
    sql_file.write_text("""
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE
);
""")
    result = analyze_sql_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) >= 1

    # Find the table symbol
    tables = [s for s in result.symbols if s.kind == "table"]
    assert len(tables) == 1
    assert tables[0].name == "users"
    assert tables[0].language == "sql"


def test_analyze_view(tmp_path):
    """Test detection of view definition."""
    sql_file = tmp_path / "views.sql"
    sql_file.write_text("""
CREATE VIEW active_users AS
SELECT * FROM users WHERE status = 'active';
""")
    result = analyze_sql_files(tmp_path)

    views = [s for s in result.symbols if s.kind == "view"]
    assert len(views) == 1
    assert views[0].name == "active_users"


def test_analyze_function(tmp_path):
    """Test detection of function/procedure definition."""
    sql_file = tmp_path / "functions.sql"
    sql_file.write_text("""
CREATE FUNCTION get_user_name(user_id INTEGER)
RETURNS VARCHAR(100)
AS $$
    SELECT name FROM users WHERE id = user_id;
$$ LANGUAGE SQL;
""")
    result = analyze_sql_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) == 1
    assert functions[0].name == "get_user_name"


def test_analyze_trigger(tmp_path):
    """Test detection of trigger definition."""
    sql_file = tmp_path / "triggers.sql"
    sql_file.write_text("""
CREATE TRIGGER update_timestamp
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION update_modified_time();
""")
    result = analyze_sql_files(tmp_path)

    triggers = [s for s in result.symbols if s.kind == "trigger"]
    assert len(triggers) == 1
    assert triggers[0].name == "update_timestamp"


def test_analyze_index(tmp_path):
    """Test detection of index definition."""
    sql_file = tmp_path / "indexes.sql"
    sql_file.write_text("""
CREATE INDEX idx_users_email ON users(email);
CREATE UNIQUE INDEX idx_users_unique_email ON users(email);
""")
    result = analyze_sql_files(tmp_path)

    indexes = [s for s in result.symbols if s.kind == "index"]
    assert len(indexes) == 2
    names = {idx.name for idx in indexes}
    assert "idx_users_email" in names
    assert "idx_users_unique_email" in names


def test_analyze_foreign_key_reference(tmp_path):
    """Test detection of foreign key relationships as edges."""
    sql_file = tmp_path / "schema.sql"
    sql_file.write_text("""
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100)
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    total DECIMAL(10, 2)
);
""")
    result = analyze_sql_files(tmp_path)

    tables = [s for s in result.symbols if s.kind == "table"]
    assert len(tables) == 2

    # Check for references edge
    ref_edges = [e for e in result.edges if e.edge_type == "references"]
    assert len(ref_edges) >= 1
    # The orders table should reference users


def test_analyze_multiple_files(tmp_path):
    """Test analysis across multiple SQL files."""
    schema = tmp_path / "schema.sql"
    schema.write_text("CREATE TABLE users (id INTEGER PRIMARY KEY);")

    views = tmp_path / "views.sql"
    views.write_text("CREATE VIEW all_users AS SELECT * FROM users;")

    result = analyze_sql_files(tmp_path)

    assert len(result.symbols) >= 2
    kinds = {s.kind for s in result.symbols}
    assert "table" in kinds
    assert "view" in kinds


def test_analyze_stored_procedure(tmp_path):
    """Test detection of stored procedure definition.

    Note: tree-sitter-sql uses a generic SQL grammar that may not support
    all dialect-specific syntax like MySQL's CREATE PROCEDURE with BEGIN/END.
    PostgreSQL-style CREATE FUNCTION is used for better compatibility.
    """
    sql_file = tmp_path / "procedures.sql"
    # Use PostgreSQL-style function which is well-supported
    sql_file.write_text("""
CREATE FUNCTION update_user_status(user_id INT, new_status VARCHAR(20))
RETURNS VOID
AS $$
    UPDATE users SET status = new_status WHERE id = user_id;
$$ LANGUAGE SQL;
""")
    result = analyze_sql_files(tmp_path)

    # Functions detected
    funcs = [s for s in result.symbols if s.kind == "function"]
    assert len(funcs) >= 1


def test_find_sql_files(tmp_path):
    """Test that SQL files are discovered correctly."""
    (tmp_path / "schema.sql").write_text("SELECT 1;")
    (tmp_path / "data.SQL").write_text("SELECT 2;")
    (tmp_path / "not_sql.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "more.sql").write_text("SELECT 3;")

    files = list(find_sql_files(tmp_path))
    assert len(files) >= 2  # schema.sql and subdir/more.sql at minimum


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no SQL files."""
    result = analyze_sql_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    sql_file = tmp_path / "test.sql"
    sql_file.write_text("CREATE TABLE test (id INT);")

    result = analyze_sql_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    sql_file = tmp_path / "broken.sql"
    sql_file.write_text("CREATE BROKEN SYNTAX;")

    # Should not raise an exception
    result = analyze_sql_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, SQLAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    sql_file = tmp_path / "schema.sql"
    sql_file.write_text("""CREATE TABLE users (
    id INTEGER PRIMARY KEY
);
""")
    result = analyze_sql_files(tmp_path)

    tables = [s for s in result.symbols if s.kind == "table"]
    assert len(tables) == 1

    # Check span
    assert tables[0].span.start_line >= 1
    assert tables[0].span.end_line >= tables[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    # This test verifies the skip logic exists
    from hypergumbo.analyze.sql import is_sql_tree_sitter_available

    # The function should return a boolean
    result = is_sql_tree_sitter_available()
    assert isinstance(result, bool)


class TestSQLSignatureExtraction:
    """Tests for SQL function signature extraction."""

    def test_function_with_params(self, tmp_path):
        """Extract signature for function with parameters."""
        sql_file = tmp_path / "funcs.sql"
        sql_file.write_text("""
CREATE FUNCTION calculate_total(price DECIMAL, qty INT) RETURNS DECIMAL
AS $$
BEGIN
    RETURN price * qty;
END;
$$ LANGUAGE plpgsql;
""")
        result = analyze_sql_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "calculate_total"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(price DECIMAL, qty INT) RETURNS DECIMAL"

    def test_function_no_params(self, tmp_path):
        """Extract signature for function with no parameters."""
        sql_file = tmp_path / "funcs.sql"
        sql_file.write_text("""
CREATE FUNCTION get_current_timestamp() RETURNS timestamp
AS $$
BEGIN
    RETURN now();
END;
$$ LANGUAGE plpgsql;
""")
        result = analyze_sql_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "get_current_timestamp"]
        assert len(funcs) == 1
        assert "RETURNS" in funcs[0].signature
        assert "()" in funcs[0].signature

    def test_function_single_param(self, tmp_path):
        """Extract signature for function with single parameter."""
        sql_file = tmp_path / "funcs.sql"
        sql_file.write_text("""
CREATE FUNCTION double_it(x INT) RETURNS INT
AS $$
BEGIN
    RETURN x * 2;
END;
$$ LANGUAGE plpgsql;
""")
        result = analyze_sql_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "double_it"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x INT) RETURNS INT"
