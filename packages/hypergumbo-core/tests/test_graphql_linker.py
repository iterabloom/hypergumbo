# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for GraphQL client-schema linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.graphql import (
    _extract_operation_name,
    _scan_javascript_graphql,
    _scan_python_graphql,
    link_graphql,
)


class TestExtractOperationName:
    """Tests for extracting operation names from GraphQL queries."""

    def test_named_query(self):
        query = "query GetUsers { users { id name } }"
        assert _extract_operation_name(query) == ("query", "GetUsers")

    def test_named_mutation(self):
        query = "mutation CreateUser($name: String!) { createUser(name: $name) { id } }"
        assert _extract_operation_name(query) == ("mutation", "CreateUser")

    def test_named_subscription(self):
        query = "subscription OnUserCreated { userCreated { id } }"
        assert _extract_operation_name(query) == ("subscription", "OnUserCreated")

    def test_unnamed_query(self):
        query = "{ users { id name } }"
        assert _extract_operation_name(query) == ("query", None)

    def test_unnamed_explicit_query(self):
        query = "query { users { id } }"
        assert _extract_operation_name(query) == ("query", None)

    def test_fragment_not_operation(self):
        query = "fragment UserFields on User { id name }"
        assert _extract_operation_name(query) == (None, None)


class TestScanJavaScriptGraphQL:
    """Tests for JavaScript GraphQL client call detection."""

    def test_gql_template_literal(self):
        code = dedent('''
            import { gql } from '@apollo/client';

            const GET_USERS = gql`
                query GetUsers {
                    users { id name }
                }
            `;
        ''')
        calls = _scan_javascript_graphql(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].operation_type == "query"
        assert calls[0].operation_name == "GetUsers"

    def test_usequery_hook(self):
        code = dedent('''
            import { useQuery, gql } from '@apollo/client';

            const GET_POSTS = gql`
                query GetPosts {
                    posts { id title }
                }
            `;

            function Posts() {
                const { data } = useQuery(GET_POSTS);
            }
        ''')
        calls = _scan_javascript_graphql(Path("test.tsx"), code)
        assert len(calls) == 1
        assert calls[0].operation_name == "GetPosts"

    def test_mutation(self):
        code = dedent('''
            const CREATE_USER = gql`
                mutation CreateUser($name: String!) {
                    createUser(name: $name) {
                        id
                    }
                }
            `;
        ''')
        calls = _scan_javascript_graphql(Path("test.js"), code)
        assert len(calls) == 1
        assert calls[0].operation_type == "mutation"
        assert calls[0].operation_name == "CreateUser"

    def test_multiple_operations(self):
        code = dedent('''
            const QUERY1 = gql`query First { a }`;
            const QUERY2 = gql`query Second { b }`;
        ''')
        calls = _scan_javascript_graphql(Path("test.js"), code)
        assert len(calls) == 2
        names = {c.operation_name for c in calls}
        assert names == {"First", "Second"}


class TestScanPythonGraphQL:
    """Tests for Python GraphQL client call detection."""

    def test_gql_function(self):
        code = dedent('''
            from gql import gql

            GET_USERS = gql("""
                query GetUsers {
                    users { id name }
                }
            """)
        ''')
        calls = _scan_python_graphql(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].operation_type == "query"
        assert calls[0].operation_name == "GetUsers"

    def test_mutation(self):
        code = dedent('''
            CREATE_USER = gql("""
                mutation CreateUser($name: String!) {
                    createUser(name: $name) { id }
                }
            """)
        ''')
        calls = _scan_python_graphql(Path("test.py"), code)
        assert len(calls) == 1
        assert calls[0].operation_type == "mutation"
        assert calls[0].operation_name == "CreateUser"


class TestLinkGraphQL:
    """Integration tests for GraphQL linking."""

    def test_links_client_to_schema(self, tmp_path: Path):
        """Links JavaScript client query to GraphQL schema operation."""
        # Create schema file
        schema_file = tmp_path / "schema.graphql"
        schema_file.write_text("""
type Query {
    users: [User]
}

type User {
    id: ID!
    name: String!
}
""")

        # Create client file
        client_file = tmp_path / "client.js"
        client_file.write_text('''
import { gql, useQuery } from '@apollo/client';

const GET_USERS = gql`
    query GetUsers {
        users { id name }
    }
`;

export function UserList() {
    const { data } = useQuery(GET_USERS);
    return data?.users;
}
''')

        # Create query operation symbols (simulating GraphQL analyzer output)
        query_symbol = Symbol(
            id="graphql:schema.graphql:1-3:Query:type",
            name="Query",
            kind="type",
            language="graphql",
            path="schema.graphql",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
        )

        result = link_graphql(tmp_path, [query_symbol])

        assert result.run is not None
        # Should have symbols for detected client calls
        assert len(result.symbols) >= 1
        # Client calls should be GraphQL operations
        client_ops = [s for s in result.symbols if (s.meta or {}).get("framework_role") == "graphql_client"]
        assert len(client_ops) == 1
        assert client_ops[0].meta.get("operation_name") == "GetUsers"

    def test_no_graphql_files(self, tmp_path: Path):
        """Returns empty result when no GraphQL files."""
        py_file = tmp_path / "app.py"
        py_file.write_text("print('hello')")

        result = link_graphql(tmp_path, [])

        assert result.run is not None
        assert len(result.symbols) == 0
        assert len(result.edges) == 0

    def test_creates_edge_to_matching_operation(self, tmp_path: Path):
        """Creates edge from client call to matching schema operation."""
        # Create client file with named query
        client_file = tmp_path / "client.js"
        client_file.write_text('''
const GET_USERS = gql`query GetUsers { users { id } }`;
''')

        # Create operation symbol
        operation_symbol = Symbol(
            id="graphql:schema.graphql:1-3:GetUsers:query",
            name="GetUsers",
            kind="query",
            language="graphql",
            path="schema.graphql",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
        )

        result = link_graphql(tmp_path, [operation_symbol])

        # Should have canonical 'calls' edge with meta['protocol']='graphql'
        # linking client to schema (post WI-vumum-juvil; pre-fold edge_type
        # was 'graphql_calls').
        graphql_edges = [
            e for e in result.edges
            if e.edge_type == "calls" and e.meta.get("protocol") == "graphql"
        ]
        assert len(graphql_edges) == 1
        assert operation_symbol.id in graphql_edges[0].dst


class TestGraphQLLinkerRegistered:
    """Tests for the registered graphql_linker function."""

    def test_graphql_linker_returns_result(self, tmp_path: Path) -> None:
        """graphql_linker function returns LinkerResult."""
        from hypergumbo_core.linkers.graphql import graphql_linker
        from hypergumbo_core.linkers.registry import LinkerContext

        ctx = LinkerContext(repo_root=tmp_path)
        result = graphql_linker(ctx)

        assert result is not None
        assert hasattr(result, "symbols")
        assert hasattr(result, "edges")

    def test_graphql_linker_extracts_operations(self, tmp_path: Path) -> None:
        """graphql_linker extracts GraphQL operation symbols from context."""
        from hypergumbo_core.linkers.graphql import graphql_linker
        from hypergumbo_core.linkers.registry import LinkerContext

        # Create a GraphQL operation symbol in the context
        operation_sym = Symbol(
            id="graphql:schema.graphql:1-3:GetUsers:query",
            name="GetUsers",
            kind="query",
            language="graphql",
            path="schema.graphql",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[operation_sym])
        result = graphql_linker(ctx)

        assert result is not None
        # The linker should still work even if no client calls are found
        assert isinstance(result.symbols, list)
        assert isinstance(result.edges, list)


class TestClientSymbolCanonicalStableId:
    """INV-hunup: graphql_client stand-ins emit stable_id=None and the
    post-linker chokepoint stamps a canonical injective id (was a
    collision-prone bare operation_name)."""

    def _call(self, tmp_path: Path, fname: str, op_name):
        from hypergumbo_core.linkers.graphql import GraphQLClientCall
        return GraphQLClientCall(
            operation_type="query",
            operation_name=op_name,
            query_text="query %s { x }" % (op_name or ""),
            line=10,
            file_path=str(tmp_path / fname),
            language="javascript",
        )

    def test_client_symbol_canonical_via_chokepoint(self, tmp_path: Path) -> None:
        from hypergumbo_core.linkers.graphql import _create_client_symbol
        from hypergumbo_core.analyze.base import populate_synthetic_class_b_identity
        from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN

        sym = _create_client_symbol(self._call(tmp_path, "app.js", "GetUsers"), tmp_path)
        # Class-B stand-in: defers stable_id to the chokepoint (not bare op name).
        assert sym.stable_id is None
        assert sym.language is None and sym.protocol_origin == "graphql"
        assert sym.meta["operation_name"] == "GetUsers"  # preserved in meta
        populate_synthetic_class_b_identity([sym])
        assert _CANONICAL_STABLE_ID_PATTERN.match(sym.stable_id)

    def test_repeated_operation_name_no_collision(self, tmp_path: Path) -> None:
        # The old bare-operation_name stable_id collided when an op name repeated
        # across files; the chokepoint's (protocol_origin, kind, path, name,
        # occurrence) key makes them injective.
        from hypergumbo_core.linkers.graphql import _create_client_symbol
        from hypergumbo_core.analyze.base import populate_synthetic_class_b_identity

        a = _create_client_symbol(self._call(tmp_path, "a.js", "GetUsers"), tmp_path)
        b = _create_client_symbol(self._call(tmp_path, "b.js", "GetUsers"), tmp_path)
        populate_synthetic_class_b_identity([a, b])
        assert a.stable_id != b.stable_id
