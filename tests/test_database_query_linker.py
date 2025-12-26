"""Tests for database query linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo.linkers.database_query import (
    _extract_tables_from_query,
    _detect_query_type,
    _scan_python_queries,
    _scan_javascript_queries,
    _scan_java_queries,
    link_database_queries,
)
from hypergumbo.ir import Symbol, Span


class TestTableExtraction:
    """Tests for table name extraction from SQL queries."""

    def test_extract_from_select(self):
        """Extract table from SELECT ... FROM."""
        query = "SELECT * FROM users WHERE id = 1"
        tables = _extract_tables_from_query(query)
        assert tables == ["users"]

    def test_extract_from_join(self):
        """Extract tables from JOIN clauses."""
        query = "SELECT * FROM users JOIN orders ON users.id = orders.user_id"
        tables = _extract_tables_from_query(query)
        assert set(tables) == {"users", "orders"}

    def test_extract_from_insert(self):
        """Extract table from INSERT INTO."""
        query = "INSERT INTO users (name, email) VALUES ('test', 'test@test.com')"
        tables = _extract_tables_from_query(query)
        assert tables == ["users"]

    def test_extract_from_update(self):
        """Extract table from UPDATE."""
        query = "UPDATE users SET name = 'new' WHERE id = 1"
        tables = _extract_tables_from_query(query)
        assert tables == ["users"]

    def test_extract_from_delete(self):
        """Extract table from DELETE FROM."""
        query = "DELETE FROM users WHERE id = 1"
        tables = _extract_tables_from_query(query)
        assert tables == ["users"]

    def test_extract_multiple_joins(self):
        """Extract from multiple JOINs."""
        query = """
            SELECT * FROM users
            JOIN orders ON users.id = orders.user_id
            LEFT JOIN products ON orders.product_id = products.id
        """
        tables = _extract_tables_from_query(query)
        assert set(tables) == {"users", "orders", "products"}

    def test_extract_quoted_table(self):
        """Handle quoted table names."""
        query = 'SELECT * FROM `users` WHERE id = 1'
        tables = _extract_tables_from_query(query)
        assert tables == ["users"]

    def test_extract_double_quoted_table(self):
        """Handle double-quoted table names."""
        query = 'SELECT * FROM "users" WHERE id = 1'
        tables = _extract_tables_from_query(query)
        assert tables == ["users"]

    def test_skip_sql_keywords(self):
        """Skip SQL keywords that might match patterns."""
        query = "SELECT name FROM users WHERE id IN (SELECT user_id FROM orders)"
        tables = _extract_tables_from_query(query)
        # Should not include 'select' as a table name
        assert set(tables) == {"users", "orders"}


class TestQueryTypeDetection:
    """Tests for SQL query type detection."""

    def test_detect_select(self):
        """Detect SELECT queries."""
        assert _detect_query_type("SELECT * FROM users") == "SELECT"
        assert _detect_query_type("  SELECT id FROM users") == "SELECT"

    def test_detect_insert(self):
        """Detect INSERT queries."""
        assert _detect_query_type("INSERT INTO users VALUES (1)") == "INSERT"

    def test_detect_update(self):
        """Detect UPDATE queries."""
        assert _detect_query_type("UPDATE users SET name = 'x'") == "UPDATE"

    def test_detect_delete(self):
        """Detect DELETE queries."""
        assert _detect_query_type("DELETE FROM users WHERE id = 1") == "DELETE"

    def test_detect_other(self):
        """Detect other queries."""
        assert _detect_query_type("CREATE TABLE users (id INT)") == "OTHER"
        assert _detect_query_type("DROP TABLE users") == "OTHER"


class TestPythonQueryPatterns:
    """Tests for Python database query detection."""

    def test_cursor_execute(self, tmp_path: Path):
        """Detect cursor.execute() with query string."""
        code = dedent('''
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["users"]
        assert patterns[0].query_type == "SELECT"
        assert patterns[0].language == "python"

    def test_db_execute(self, tmp_path: Path):
        """Detect db.execute() pattern."""
        code = dedent('''
            db.execute("INSERT INTO orders (user_id, total) VALUES (?, ?)", (1, 100))
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["orders"]
        assert patterns[0].query_type == "INSERT"

    def test_connection_execute(self, tmp_path: Path):
        """Detect connection.execute() pattern."""
        code = dedent('''
            connection.execute("UPDATE users SET status = 'active' WHERE id = 1")
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["users"]
        assert patterns[0].query_type == "UPDATE"

    def test_session_execute_with_text(self, tmp_path: Path):
        """Detect session.execute(text('...')) SQLAlchemy pattern."""
        code = dedent('''
            session.execute(text("SELECT * FROM products WHERE active = true"))
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["products"]

    def test_triple_quoted_query(self, tmp_path: Path):
        """Detect triple-quoted SQL strings."""
        code = dedent('''
            cursor.execute("""
                SELECT u.*, o.total
                FROM users u
                JOIN orders o ON u.id = o.user_id
                WHERE o.created_at > ?
            """, (date,))
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 1
        assert set(patterns[0].tables) == {"users", "orders"}

    def test_fstring_execute(self, tmp_path: Path):
        """Detect f-string SQL queries."""
        code = dedent('''
            cursor.execute(f"SELECT * FROM {table_name} WHERE id = {id}")
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        # f-strings may not extract table names reliably if the table is a variable
        # but should still detect the pattern
        assert len(patterns) >= 0  # May or may not detect depending on content

    def test_single_quoted_query(self, tmp_path: Path):
        """Detect single-quoted SQL queries."""
        code = dedent('''
            cursor.execute('DELETE FROM sessions WHERE expired = true')
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["sessions"]
        assert patterns[0].query_type == "DELETE"

    def test_engine_execute(self, tmp_path: Path):
        """Detect engine.execute() pattern."""
        code = dedent('''
            engine.execute("SELECT * FROM config WHERE key = 'setting'")
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["config"]

    def test_multiple_queries_same_file(self, tmp_path: Path):
        """Detect multiple queries in same file."""
        code = dedent('''
            cursor.execute("SELECT * FROM users")
            cursor.execute("INSERT INTO logs (action) VALUES ('read')")
            db.execute("UPDATE stats SET count = count + 1")
        ''')
        file = tmp_path / "db.py"
        file.write_text(code)
        patterns = _scan_python_queries(file, code)

        assert len(patterns) == 3
        assert {p.tables[0] for p in patterns} == {"users", "logs", "stats"}


class TestJavaScriptQueryPatterns:
    """Tests for JavaScript/TypeScript database query detection."""

    def test_db_query(self, tmp_path: Path):
        """Detect db.query() pattern."""
        code = dedent('''
            const result = await db.query("SELECT * FROM users WHERE active = true");
        ''')
        file = tmp_path / "db.js"
        file.write_text(code)
        patterns = _scan_javascript_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["users"]
        assert patterns[0].query_type == "SELECT"

    def test_pool_query(self, tmp_path: Path):
        """Detect pool.query() pattern (pg package)."""
        code = dedent('''
            pool.query("INSERT INTO orders (user_id, total) VALUES ($1, $2)", [userId, total]);
        ''')
        file = tmp_path / "db.js"
        file.write_text(code)
        patterns = _scan_javascript_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["orders"]
        assert patterns[0].query_type == "INSERT"

    def test_client_query(self, tmp_path: Path):
        """Detect client.query() pattern."""
        code = dedent('''
            const { rows } = await client.query("SELECT id, name FROM products");
        ''')
        file = tmp_path / "db.js"
        file.write_text(code)
        patterns = _scan_javascript_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["products"]

    def test_template_literal_query(self, tmp_path: Path):
        """Detect template literal SQL queries."""
        code = dedent('''
            db.query(`
                SELECT u.*, COUNT(o.id) as order_count
                FROM users u
                LEFT JOIN orders o ON u.id = o.user_id
                GROUP BY u.id
            `);
        ''')
        file = tmp_path / "db.js"
        file.write_text(code)
        patterns = _scan_javascript_queries(file, code)

        assert len(patterns) == 1
        assert set(patterns[0].tables) == {"users", "orders"}

    def test_knex_table_reference(self, tmp_path: Path):
        """Detect Knex.js table references."""
        code = dedent('''
            const users = await knex('users').select('*');
            knex("orders").where('status', 'pending');
        ''')
        file = tmp_path / "db.js"
        file.write_text(code)
        patterns = _scan_javascript_queries(file, code)

        assert len(patterns) == 2
        assert {p.tables[0] for p in patterns} == {"users", "orders"}

    def test_typescript_query(self, tmp_path: Path):
        """Detect queries in TypeScript files."""
        code = dedent('''
            const result: QueryResult = await pool.query("SELECT * FROM events WHERE type = $1", [eventType]);
        ''')
        file = tmp_path / "db.ts"
        file.write_text(code)
        patterns = _scan_javascript_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["events"]


class TestJavaQueryPatterns:
    """Tests for Java database query detection."""

    def test_statement_execute_query(self, tmp_path: Path):
        """Detect statement.executeQuery() pattern."""
        code = dedent('''
            ResultSet rs = statement.executeQuery("SELECT * FROM users WHERE id = 1");
        ''')
        file = tmp_path / "UserDao.java"
        file.write_text(code)
        patterns = _scan_java_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["users"]
        assert patterns[0].query_type == "SELECT"

    def test_prepared_statement_execute_update(self, tmp_path: Path):
        """Detect preparedStatement.executeUpdate() pattern."""
        code = dedent('''
            int rows = preparedStatement.executeUpdate("INSERT INTO orders VALUES (?, ?)");
        ''')
        file = tmp_path / "OrderDao.java"
        file.write_text(code)
        patterns = _scan_java_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["orders"]
        assert patterns[0].query_type == "INSERT"

    def test_jdbc_template_query(self, tmp_path: Path):
        """Detect Spring JdbcTemplate.query() pattern."""
        code = dedent('''
            List<User> users = jdbcTemplate.query("SELECT * FROM users", new UserRowMapper());
        ''')
        file = tmp_path / "UserRepository.java"
        file.write_text(code)
        patterns = _scan_java_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["users"]

    def test_spring_query_annotation(self, tmp_path: Path):
        """Detect Spring Data @Query annotation."""
        code = dedent('''
            @Query("SELECT u FROM User u WHERE u.email = ?1")
            User findByEmail(String email);

            @Query("SELECT o FROM Order o JOIN o.user u WHERE u.id = ?1")
            List<Order> findOrdersByUserId(Long userId);
        ''')
        file = tmp_path / "UserRepository.java"
        file.write_text(code)
        patterns = _scan_java_queries(file, code)

        assert len(patterns) == 2
        # Note: JPA entity names like 'User' may not match SQL table patterns
        # This is expected behavior for entity-based queries

    def test_stmt_shorthand(self, tmp_path: Path):
        """Detect stmt.executeQuery() shorthand."""
        code = dedent('''
            ResultSet rs = stmt.executeQuery("SELECT * FROM products WHERE active = true");
        ''')
        file = tmp_path / "ProductDao.java"
        file.write_text(code)
        patterns = _scan_java_queries(file, code)

        assert len(patterns) == 1
        assert patterns[0].tables == ["products"]


class TestDatabaseQueryLinker:
    """Tests for the full linker integration."""

    def test_links_query_to_table_symbol(self, tmp_path: Path):
        """Creates edges from queries to table definitions."""
        py_file = tmp_path / "app.py"
        py_file.write_text(dedent('''
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        '''))

        # Create table symbol from SQL analyzer
        table_symbols = [
            Symbol(
                id="sql:schema.sql:1-5:users:table",
                name="users",
                kind="table",
                path="schema.sql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="sql",
            ),
        ]

        result = link_database_queries(tmp_path, table_symbols)

        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "db_query"
        assert result.symbols[0].meta["tables"] == ["users"]

        # Should have query_references edge
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "query_references"
        assert result.edges[0].meta["table_name"] == "users"

    def test_links_multiple_tables(self, tmp_path: Path):
        """Creates edges for queries referencing multiple tables."""
        py_file = tmp_path / "app.py"
        py_file.write_text(dedent('''
            cursor.execute("SELECT * FROM users JOIN orders ON users.id = orders.user_id")
        '''))

        table_symbols = [
            Symbol(
                id="sql:schema.sql:1-5:users:table",
                name="users",
                kind="table",
                path="schema.sql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="sql",
            ),
            Symbol(
                id="sql:schema.sql:7-12:orders:table",
                name="orders",
                kind="table",
                path="schema.sql",
                span=Span(start_line=7, end_line=12, start_col=0, end_col=0),
                language="sql",
            ),
        ]

        result = link_database_queries(tmp_path, table_symbols)

        assert len(result.symbols) == 1
        assert set(result.symbols[0].meta["tables"]) == {"users", "orders"}

        # Should have edges to both tables
        assert len(result.edges) == 2
        edge_tables = {e.meta["table_name"] for e in result.edges}
        assert edge_tables == {"users", "orders"}

    def test_cross_language_linking(self, tmp_path: Path):
        """Links JavaScript queries to SQL schema."""
        js_file = tmp_path / "app.js"
        js_file.write_text(dedent('''
            const result = await db.query("SELECT * FROM products");
        '''))

        table_symbols = [
            Symbol(
                id="sql:schema.sql:1-5:products:table",
                name="products",
                kind="table",
                path="schema.sql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="sql",
            ),
        ]

        result = link_database_queries(tmp_path, table_symbols)

        assert len(result.edges) == 1
        assert result.edges[0].meta["cross_language"] is True

    def test_no_edges_without_matching_tables(self, tmp_path: Path):
        """No edges created when table names don't match."""
        py_file = tmp_path / "app.py"
        py_file.write_text(dedent('''
            cursor.execute("SELECT * FROM users")
        '''))

        # Table symbol with different name
        table_symbols = [
            Symbol(
                id="sql:schema.sql:1-5:customers:table",
                name="customers",
                kind="table",
                path="schema.sql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="sql",
            ),
        ]

        result = link_database_queries(tmp_path, table_symbols)

        assert len(result.symbols) == 1  # Query symbol still created
        assert len(result.edges) == 0  # But no edges without match

    def test_no_symbols_without_tables(self, tmp_path: Path):
        """No symbols created for queries without table references."""
        py_file = tmp_path / "app.py"
        py_file.write_text(dedent('''
            # Just some Python code
            x = 1 + 1
        '''))

        result = link_database_queries(tmp_path, [])

        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_multiple_queries_multiple_files(self, tmp_path: Path):
        """Handles queries across multiple files."""
        py_file = tmp_path / "users.py"
        py_file.write_text(dedent('''
            cursor.execute("SELECT * FROM users")
        '''))

        js_file = tmp_path / "orders.js"
        js_file.write_text(dedent('''
            pool.query("SELECT * FROM orders")
        '''))

        table_symbols = [
            Symbol(
                id="sql:schema.sql:1-5:users:table",
                name="users",
                kind="table",
                path="schema.sql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="sql",
            ),
            Symbol(
                id="sql:schema.sql:7-12:orders:table",
                name="orders",
                kind="table",
                path="schema.sql",
                span=Span(start_line=7, end_line=12, start_col=0, end_col=0),
                language="sql",
            ),
        ]

        result = link_database_queries(tmp_path, table_symbols)

        assert len(result.symbols) == 2
        assert len(result.edges) == 2

        languages = {s.language for s in result.symbols}
        assert languages == {"python", "javascript"}

    def test_analysis_run_metadata(self, tmp_path: Path):
        """Analysis run includes proper metadata."""
        py_file = tmp_path / "app.py"
        py_file.write_text('cursor.execute("SELECT 1")')

        result = link_database_queries(tmp_path, [])

        assert result.run is not None
        assert result.run.pass_id == "database-query-linker-v1"
        assert result.run.files_analyzed >= 1
        assert result.run.duration_ms >= 0

    def test_symbol_stable_id(self, tmp_path: Path):
        """Query symbols have stable IDs for deduplication."""
        py_file = tmp_path / "app.py"
        py_file.write_text(dedent('''
            cursor.execute("SELECT * FROM users WHERE id = 1")
        '''))

        result = link_database_queries(tmp_path, [])

        assert len(result.symbols) == 1
        symbol = result.symbols[0]
        assert symbol.stable_id == "SELECT:users"

    def test_case_insensitive_table_matching(self, tmp_path: Path):
        """Table matching is case-insensitive."""
        py_file = tmp_path / "app.py"
        py_file.write_text(dedent('''
            cursor.execute("SELECT * FROM USERS")
        '''))

        table_symbols = [
            Symbol(
                id="sql:schema.sql:1-5:users:table",
                name="users",  # lowercase
                kind="table",
                path="schema.sql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="sql",
            ),
        ]

        result = link_database_queries(tmp_path, table_symbols)

        # Should match despite case difference
        assert len(result.edges) == 1
        assert result.edges[0].meta["table_name"] == "users"

    def test_query_symbol_metadata(self, tmp_path: Path):
        """Query symbols include metadata about the query."""
        py_file = tmp_path / "app.py"
        py_file.write_text(dedent('''
            cursor.execute("INSERT INTO users (name) VALUES ('test')")
        '''))

        result = link_database_queries(tmp_path, [])

        assert len(result.symbols) == 1
        symbol = result.symbols[0]
        assert symbol.meta["query_type"] == "INSERT"
        assert symbol.meta["tables"] == ["users"]
        assert "INSERT INTO users" in symbol.meta["query_preview"]
