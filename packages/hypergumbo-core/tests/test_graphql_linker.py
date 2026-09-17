# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for GraphQL client-schema linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.graphql import (
    _count_graphql_operations,
    _count_graphql_schema_fields,
    _extract_operation_name,
    _get_graphql_schema_field_symbols,
    _root_field,
    _scan_javascript_graphql,
    _scan_python_graphql,
    link_graphql,
)
from hypergumbo_core.linkers.registry import LinkerContext


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


class TestRootField:
    """WI-dinum: the client-to-server join the linker never had."""

    def test_named_operation_yields_its_root_selection(self) -> None:
        assert _root_field("query GetUsers { users { id name } }") == "users"

    def test_anonymous_operation_yields_its_root_selection(self) -> None:
        assert _root_field("{ users { id } }") == "users"

    def test_variable_definitions_do_not_hide_the_selection_set(self) -> None:
        """Variables are parenthesised, so the first brace is still the set."""
        assert _root_field("query GetUser($id: ID!) { user(id: $id) { name } }") == "user"

    def test_mutation_yields_its_root_selection(self) -> None:
        assert _root_field("mutation { createUser(name: \"x\") { id } }") == "createUser"

    def test_a_leading_comment_inside_the_set_is_skipped(self) -> None:
        assert _root_field("query Q {\n  # the users\n  users { id }\n}") == "users"

    def test_a_fragment_is_not_an_operation_and_has_no_root_field(self) -> None:
        """A fragment has a selection set but selects on a named type rather
        than invoking a root field — matching ``_extract_operation_name``,
        which already refuses to call a fragment an operation."""
        assert _root_field("fragment UserBits on User { id name }") is None

    def test_text_with_no_selection_set_yields_nothing(self) -> None:
        assert _root_field("scalar DateTime") is None


def _field_symbol(parent: str, name: str) -> Symbol:
    return Symbol(
        id=f"graphql:src/schema.ts:2-2:{parent}.{name}:field",
        name=name,
        kind="field",
        path="src/schema.ts",
        language="graphql",
        span=Span(start_line=2, end_line=2, start_col=0, end_col=0),
        meta={"parent_type": parent},
    )


class TestRootFieldJoin:
    """The join that makes the linker work on a repo with no .graphql files."""

    def _client(self, tmp_path: Path, query: str) -> Path:
        client = tmp_path / "client.js"
        client.write_text("const Q = gql`\n%s\n`;\n" % query, encoding="utf-8")
        return client

    def test_a_client_query_links_to_the_schema_field_it_invokes(
        self, tmp_path: Path,
    ) -> None:
        self._client(tmp_path, "query GetUsers { users { id } }")
        fields = [_field_symbol("Query", "users")]
        result = link_graphql(tmp_path, [], fields)
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.dst == fields[0].id
        assert edge.edge_type == "calls"
        assert (edge.meta or {})["root_field"] == "users"
        assert (edge.meta or {})["protocol"] == "graphql"

    def test_a_mutation_joins_on_the_mutation_root_type(self, tmp_path: Path) -> None:
        self._client(tmp_path, "mutation AddUser { createUser { id } }")
        fields = [_field_symbol("Mutation", "createUser")]
        assert len(link_graphql(tmp_path, [], fields).edges) == 1

    def test_a_query_root_field_does_not_match_a_mutation_field(
        self, tmp_path: Path,
    ) -> None:
        """The root TYPE is part of the key: ``Query.users`` and
        ``Mutation.users`` are different fields."""
        self._client(tmp_path, "query GetUsers { users { id } }")
        assert link_graphql(tmp_path, [], [_field_symbol("Mutation", "users")]).edges == []

    def test_an_unmatched_root_field_emits_nothing(self, tmp_path: Path) -> None:
        self._client(tmp_path, "query GetUsers { users { id } }")
        assert link_graphql(tmp_path, [], [_field_symbol("Query", "books")]).edges == []

    def test_the_client_symbol_is_still_minted_when_nothing_matches(
        self, tmp_path: Path,
    ) -> None:
        """A minted node with no edge is the state this row is about; it is
        still the honest record that a client call was found."""
        self._client(tmp_path, "query GetUsers { users { id } }")
        assert len(link_graphql(tmp_path, [], []).symbols) == 1

    def test_a_fragment_definition_joins_nothing(self, tmp_path: Path) -> None:
        self._client(tmp_path, "fragment UserBits on User { id }")
        assert link_graphql(tmp_path, [], [_field_symbol("User", "id")]).edges == []

    def test_the_operation_name_join_wins_when_both_could_fire(
        self, tmp_path: Path,
    ) -> None:
        """Name identity is the stronger claim, so it short-circuits."""
        self._client(tmp_path, "query GetUsers { users { id } }")
        operation = Symbol(
            id="graphql:ops.graphql:1-3:GetUsers:query",
            name="GetUsers", kind="query", path="ops.graphql", language="graphql",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
        )
        result = link_graphql(tmp_path, [operation], [_field_symbol("Query", "users")])
        assert len(result.edges) == 1
        assert result.edges[0].dst == operation.id
        assert result.edges[0].confidence == 0.9

    def test_a_field_symbol_without_a_parent_type_is_not_a_join_target(
        self, tmp_path: Path,
    ) -> None:
        self._client(tmp_path, "query GetUsers { users { id } }")
        orphan = _field_symbol("Query", "users")
        orphan.meta = {}
        assert link_graphql(tmp_path, [], [orphan]).edges == []


class TestSchemaFieldExtraction:
    """The context filter that feeds the join."""

    def test_only_graphql_fields_with_a_parent_type_are_returned(self) -> None:
        good = _field_symbol("Query", "users")
        no_parent = _field_symbol("Query", "books")
        no_parent.meta = {}
        wrong_language = _field_symbol("Query", "cooks")
        wrong_language.language = "typescript"
        wrong_kind = _field_symbol("Query", "pooks")
        wrong_kind.kind = "type"
        ctx = LinkerContext(
            repo_root=Path("/nonexistent"),
            symbols=[good, no_parent, wrong_language, wrong_kind],
            edges=[],
        )
        assert _get_graphql_schema_field_symbols(ctx) == [good]
        assert _count_graphql_schema_fields(ctx) == 1

    def test_the_operation_requirement_counts_operations_not_fields(self) -> None:
        """Two requirements with two destinations: reporting only the first
        would describe a linker that can work as one that cannot."""
        operation = Symbol(
            id="graphql:ops.graphql:1-3:GetUsers:query",
            name="GetUsers", kind="query", path="ops.graphql", language="graphql",
            span=Span(start_line=1, end_line=3, start_col=0, end_col=0),
        )
        ctx = LinkerContext(
            repo_root=Path("/nonexistent"),
            symbols=[operation, _field_symbol("Query", "users")],
            edges=[],
        )
        assert _count_graphql_operations(ctx) == 1
        assert _count_graphql_schema_fields(ctx) == 1


class TestDeclarationNamesItsDestinationProducers:
    """WI-dinum + WI-rasal: the clause names what supplies the DESTINATION."""

    def test_depends_on_names_the_two_schema_producers(self) -> None:
        import hypergumbo_core.cli  # linkers register by import side-effect
        from hypergumbo_core.catalog import get_default_catalog

        by_id = {p.id: p for p in get_default_catalog().passes}
        assert by_id["graphql-linker"].depends_on == [
            ["graphql", "graphql-sdl-linker"],
        ]

    def test_the_old_clause_named_host_languages_it_never_reads(self) -> None:
        """The linker finds client calls by reading source files itself; it
        consumes no host analyzer's output, so the six host languages the
        clause used to name were the too-wide shape WI-rasal repaired."""
        import hypergumbo_core.cli  # linkers register by import side-effect
        from hypergumbo_core.catalog import get_default_catalog

        by_id = {p.id: p for p in get_default_catalog().passes}
        literals = {lit for clause in by_id["graphql-linker"].depends_on for lit in clause}
        assert not literals & {"javascript", "python", "java", "ruby", "go"}
