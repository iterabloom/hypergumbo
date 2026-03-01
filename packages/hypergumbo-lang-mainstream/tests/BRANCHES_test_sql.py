"""Branch coverage tests for sql.py analyzer.

Tests specific branch paths in the SQL analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Table and view extraction
- Function and trigger extraction
- Index extraction
- Foreign key reference edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_mainstream.sql import _make_edge_id, analyze_sql_files, find_sql_files

def make_sql_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a SQL file with given content."""
    (tmp_path / name).write_text(content)

class TestSQLHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("sql", "schema.sql", 1, 10, "users", "table")
        assert symbol_id == "sql:schema.sql:1-10:users:table"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("sql", "schema.sql")
        assert file_id == "sql:schema.sql:1-1:file:file"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "references")
        edge_id2 = _make_edge_id("src1", "dst1", "references")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")

class TestTableExtraction:
    """Branch coverage for table extraction."""

    def test_simple_table(self, tmp_path: Path) -> None:
        """Test simple CREATE TABLE extraction."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name VARCHAR(100)
);
""")
        result = analyze_sql_files(tmp_path)
        assert not result.skipped

        tables = [s for s in result.symbols if s.kind == "table"]
        assert len(tables) >= 1
        assert any(t.name == "users" for t in tables)

    def test_multiple_tables(self, tmp_path: Path) -> None:
        """Test multiple table extraction."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE users (id INT PRIMARY KEY);
CREATE TABLE posts (id INT PRIMARY KEY);
CREATE TABLE comments (id INT PRIMARY KEY);
""")
        result = analyze_sql_files(tmp_path)
        tables = [s for s in result.symbols if s.kind == "table"]

        assert len(tables) >= 3

class TestViewExtraction:
    """Branch coverage for view extraction."""

    def test_view_definition(self, tmp_path: Path) -> None:
        """Test CREATE VIEW extraction."""
        make_sql_file(tmp_path, "views.sql", """
CREATE VIEW active_users AS
SELECT * FROM users WHERE active = 1;
""")
        result = analyze_sql_files(tmp_path)
        views = [s for s in result.symbols if s.kind == "view"]

        assert len(views) >= 1
        assert any(v.name == "active_users" for v in views)

class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_definition(self, tmp_path: Path) -> None:
        """Test CREATE FUNCTION extraction."""
        # Use PostgreSQL-style syntax which tree-sitter-sql supports
        make_sql_file(tmp_path, "functions.sql", """
CREATE FUNCTION get_user_count()
RETURNS INT
AS $$
    SELECT COUNT(*) FROM users;
$$ LANGUAGE SQL;
""")
        result = analyze_sql_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 1
        assert any(f.name == "get_user_count" for f in functions)

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with parameters has signature."""
        # Use PostgreSQL-style syntax
        make_sql_file(tmp_path, "functions.sql", """
CREATE FUNCTION add_numbers(a INT, b INT)
RETURNS INT
AS $$
BEGIN
    RETURN a + b;
END;
$$ LANGUAGE plpgsql;
""")
        result = analyze_sql_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        add_func = next((f for f in functions if f.name == "add_numbers"), None)
        assert add_func is not None
        assert add_func.signature is not None

class TestTriggerExtraction:
    """Branch coverage for trigger extraction."""

    def test_trigger_definition(self, tmp_path: Path) -> None:
        """Test CREATE TRIGGER extraction."""
        # Use PostgreSQL-style syntax
        make_sql_file(tmp_path, "triggers.sql", """
CREATE TRIGGER update_timestamp
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION update_modified_time();
""")
        result = analyze_sql_files(tmp_path)
        triggers = [s for s in result.symbols if s.kind == "trigger"]

        assert len(triggers) >= 1
        assert any(t.name == "update_timestamp" for t in triggers)

class TestIndexExtraction:
    """Branch coverage for index extraction."""

    def test_index_definition(self, tmp_path: Path) -> None:
        """Test CREATE INDEX extraction."""
        make_sql_file(tmp_path, "indexes.sql", """
CREATE INDEX idx_user_email ON users(email);
""")
        result = analyze_sql_files(tmp_path)
        indexes = [s for s in result.symbols if s.kind == "index"]

        assert len(indexes) >= 1
        assert any(i.name == "idx_user_email" for i in indexes)

class TestForeignKeyEdges:
    """Branch coverage for foreign key reference edges."""

    def test_foreign_key_creates_edge(self, tmp_path: Path) -> None:
        """Test foreign key creates reference edge."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE users (
    id INT PRIMARY KEY
);

CREATE TABLE posts (
    id INT PRIMARY KEY,
    user_id INT REFERENCES users(id)
);
""")
        result = analyze_sql_files(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]

        assert len(ref_edges) >= 1

class TestFindSQLFiles:
    """Branch coverage for file discovery."""

    def test_finds_sql_files(self, tmp_path: Path) -> None:
        """Test .sql files are discovered."""
        (tmp_path / "schema.sql").write_text("CREATE TABLE t (id INT);")

        files = list(find_sql_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".sql" for f in files)

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_sql_files(self, tmp_path: Path) -> None:
        """Test directory with no SQL files."""
        result = analyze_sql_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_sql(self, tmp_path: Path) -> None:
        """Test minimal SQL file."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE t (id INT);
""")
        result = analyze_sql_files(tmp_path)
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE users (id INT PRIMARY KEY);
""")
        result = analyze_sql_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1

class TestFunctionSignatureBranches:
    """Branch coverage for function signature extraction."""

    def test_function_without_params(self, tmp_path: Path) -> None:
        """Test function with no parameters (empty func_args branch)."""
        make_sql_file(tmp_path, "functions.sql", """
CREATE FUNCTION get_current_time()
RETURNS TIMESTAMP
AS $$
    SELECT NOW();
$$ LANGUAGE SQL;
""")
        result = analyze_sql_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 1
        func = next((f for f in functions if f.name == "get_current_time"), None)
        assert func is not None
        # Should have empty params in signature
        assert "()" in func.signature

    def test_function_without_return_type(self, tmp_path: Path) -> None:
        """Test function without explicit RETURNS clause."""
        make_sql_file(tmp_path, "functions.sql", """
CREATE FUNCTION do_nothing()
AS $$
    -- Does nothing
$$ LANGUAGE SQL;
""")
        result = analyze_sql_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        # May or may not extract depending on tree-sitter parsing
        # The important thing is no crash
        assert not result.skipped

class TestDuplicateForeignKeyRefs:
    """Branch coverage for duplicate foreign key handling."""

    def test_duplicate_fk_references(self, tmp_path: Path) -> None:
        """Test table with duplicate foreign key references to same table."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE users (
    id INT PRIMARY KEY
);

CREATE TABLE orders (
    id INT PRIMARY KEY,
    user_id INT REFERENCES users(id),
    approver_id INT REFERENCES users(id)
);
""")
        result = analyze_sql_files(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]

        # Should have references edges (may dedupe to 1 or have 2)
        assert len(ref_edges) >= 1

class TestEdgeExtractionBranches:
    """Branch coverage for edge extraction code paths."""

    def test_table_without_column_definitions(self, tmp_path: Path) -> None:
        """Test CREATE TABLE without column definitions."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE empty_table;
""")
        result = analyze_sql_files(tmp_path)
        # Should handle gracefully - may or may not extract
        assert not result.skipped

    def test_fk_reference_to_nonexistent_table(self, tmp_path: Path) -> None:
        """Test foreign key to table not in analyzed files."""
        make_sql_file(tmp_path, "schema.sql", """
CREATE TABLE orders (
    id INT PRIMARY KEY,
    customer_id INT REFERENCES customers(id)
);
""")
        result = analyze_sql_files(tmp_path)
        # customers table doesn't exist, so no edge should be created
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        # Should have 0 edges since target doesn't exist
        assert len(ref_edges) == 0

class TestComprehensiveSchema:
    """Branch coverage for comprehensive SQL schema."""

    def test_comprehensive_schema(self, tmp_path: Path) -> None:
        """Test comprehensive SQL schema with multiple construct types."""
        # Use PostgreSQL-style syntax for functions and triggers
        make_sql_file(tmp_path, "schema.sql", """
-- Tables
CREATE TABLE users (
    id INT PRIMARY KEY,
    email VARCHAR(255) UNIQUE
);

CREATE TABLE posts (
    id INT PRIMARY KEY,
    user_id INT REFERENCES users(id),
    title VARCHAR(200)
);

-- View
CREATE VIEW user_posts AS
SELECT u.email, p.title
FROM users u JOIN posts p ON u.id = p.user_id;

-- Function (PostgreSQL style)
CREATE FUNCTION post_count()
RETURNS INT
AS $$
    SELECT COUNT(*) FROM posts;
$$ LANGUAGE SQL;

-- Trigger (PostgreSQL style)
CREATE TRIGGER posts_audit
AFTER INSERT ON posts
FOR EACH ROW
EXECUTE FUNCTION log_post_insert();

-- Index
CREATE INDEX idx_posts_user ON posts(user_id);
""")
        result = analyze_sql_files(tmp_path)

        # Should have multiple symbol types
        tables = [s for s in result.symbols if s.kind == "table"]
        views = [s for s in result.symbols if s.kind == "view"]
        functions = [s for s in result.symbols if s.kind == "function"]
        triggers = [s for s in result.symbols if s.kind == "trigger"]
        indexes = [s for s in result.symbols if s.kind == "index"]

        assert len(tables) >= 2
        assert len(views) >= 1
        assert len(functions) >= 1
        assert len(triggers) >= 1
        assert len(indexes) >= 1
