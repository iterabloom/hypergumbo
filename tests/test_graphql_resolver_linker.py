"""Tests for GraphQL resolver linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo.linkers.graphql_resolver import (
    _scan_javascript_resolvers,
    _scan_python_resolvers,
    link_graphql_resolvers,
)
from hypergumbo.ir import Symbol, Span


class TestJavaScriptResolverPatterns:
    """Tests for JavaScript resolver detection."""

    def test_apollo_query_resolver(self, tmp_path: Path):
        """Detect Apollo Server Query resolvers."""
        code = dedent('''
            const resolvers = {
                Query: {
                    users: (_, args, context) => {
                        return db.users.findAll();
                    },
                    user: (_, { id }) => {
                        return db.users.findById(id);
                    },
                },
            };
        ''')
        file = tmp_path / "resolvers.js"
        file.write_text(code)
        patterns = _scan_javascript_resolvers(file, code)

        assert len(patterns) == 2
        assert patterns[0].type_name == "Query"
        assert patterns[0].field_name == "users"
        assert patterns[1].type_name == "Query"
        assert patterns[1].field_name == "user"

    def test_apollo_mutation_resolver(self, tmp_path: Path):
        """Detect Apollo Server Mutation resolvers."""
        code = dedent('''
            const resolvers = {
                Mutation: {
                    createUser: async (_, { input }, context) => {
                        return db.users.create(input);
                    },
                    deleteUser: (_, { id }) => {
                        return db.users.delete(id);
                    },
                },
            };
        ''')
        file = tmp_path / "resolvers.js"
        file.write_text(code)
        patterns = _scan_javascript_resolvers(file, code)

        assert len(patterns) == 2
        assert patterns[0].type_name == "Mutation"
        assert patterns[0].field_name == "createUser"
        assert patterns[1].type_name == "Mutation"
        assert patterns[1].field_name == "deleteUser"

    def test_type_field_resolver(self, tmp_path: Path):
        """Detect field resolvers for custom types."""
        code = dedent('''
            const resolvers = {
                User: {
                    posts: (parent, args, context) => {
                        return db.posts.findByUserId(parent.id);
                    },
                    email: (parent) => {
                        return parent.email.toLowerCase();
                    },
                },
            };
        ''')
        file = tmp_path / "resolvers.js"
        file.write_text(code)
        patterns = _scan_javascript_resolvers(file, code)

        assert len(patterns) == 2
        assert patterns[0].type_name == "User"
        assert patterns[0].field_name == "posts"
        assert patterns[1].type_name == "User"
        assert patterns[1].field_name == "email"

    def test_function_keyword_resolver(self, tmp_path: Path):
        """Detect resolvers using function keyword."""
        code = dedent('''
            const resolvers = {
                Query: {
                    users: function(parent, args, context) {
                        return [];
                    },
                    items: async function(parent, args) {
                        return [];
                    },
                },
            };
        ''')
        file = tmp_path / "resolvers.js"
        file.write_text(code)
        patterns = _scan_javascript_resolvers(file, code)

        assert len(patterns) == 2
        assert patterns[0].field_name == "users"
        assert patterns[1].field_name == "items"

    def test_shorthand_method_resolver(self, tmp_path: Path):
        """Detect shorthand method syntax resolvers."""
        code = dedent('''
            const resolvers = {
                Query: {
                    users(parent, args, context) {
                        return [];
                    },
                    async items(parent, args) {
                        return [];
                    },
                },
            };
        ''')
        file = tmp_path / "resolvers.js"
        file.write_text(code)
        patterns = _scan_javascript_resolvers(file, code)

        assert len(patterns) == 2
        assert patterns[0].field_name == "users"
        assert patterns[1].field_name == "items"

    def test_subscription_resolver(self, tmp_path: Path):
        """Detect Subscription resolvers."""
        code = dedent('''
            const resolvers = {
                Subscription: {
                    messageAdded: {
                        subscribe: () => pubsub.asyncIterator(['MESSAGE_ADDED']),
                    },
                },
            };
        ''')
        file = tmp_path / "resolvers.js"
        file.write_text(code)
        patterns = _scan_javascript_resolvers(file, code)

        # Should detect subscribe as a resolver
        assert len(patterns) >= 1

    def test_typescript_resolver(self, tmp_path: Path):
        """Detect TypeScript resolvers with type annotations."""
        code = dedent('''
            const resolvers: Resolvers = {
                Query: {
                    users: async (_: any, args: QueryUsersArgs, context: Context): Promise<User[]> => {
                        return context.db.users.findAll();
                    },
                },
            };
        ''')
        file = tmp_path / "resolvers.ts"
        file.write_text(code)
        patterns = _scan_javascript_resolvers(file, code)

        assert len(patterns) == 1
        assert patterns[0].type_name == "Query"
        assert patterns[0].field_name == "users"


class TestPythonResolverPatterns:
    """Tests for Python resolver detection."""

    def test_ariadne_query_resolver(self, tmp_path: Path):
        """Detect Ariadne @query.field decorators."""
        code = dedent('''
            from ariadne import QueryType

            query = QueryType()

            @query.field("users")
            def resolve_users(_, info):
                return get_users()

            @query.field("user")
            async def resolve_user(_, info, id: str):
                return get_user(id)
        ''')
        file = tmp_path / "resolvers.py"
        file.write_text(code)
        patterns = _scan_python_resolvers(file, code)

        assert len(patterns) == 2
        assert patterns[0].type_name == "Query"
        assert patterns[0].field_name == "users"
        assert patterns[1].type_name == "Query"
        assert patterns[1].field_name == "user"

    def test_ariadne_mutation_resolver(self, tmp_path: Path):
        """Detect Ariadne @mutation.field decorators."""
        code = dedent('''
            from ariadne import MutationType

            mutation = MutationType()

            @mutation.field("createUser")
            def resolve_create_user(_, info, input):
                return create_user(input)
        ''')
        file = tmp_path / "resolvers.py"
        file.write_text(code)
        patterns = _scan_python_resolvers(file, code)

        assert len(patterns) == 1
        assert patterns[0].type_name == "Mutation"
        assert patterns[0].field_name == "createUser"

    def test_ariadne_type_resolver(self, tmp_path: Path):
        """Detect Ariadne custom type field resolvers."""
        code = dedent('''
            from ariadne import ObjectType

            user_type = ObjectType("User")

            @user_type.field("posts")
            def resolve_user_posts(user, info):
                return get_posts_for_user(user.id)
        ''')
        file = tmp_path / "resolvers.py"
        file.write_text(code)
        patterns = _scan_python_resolvers(file, code)

        assert len(patterns) == 1
        assert patterns[0].type_name == "User"
        assert patterns[0].field_name == "posts"

    def test_strawberry_field_resolver(self, tmp_path: Path):
        """Detect Strawberry @strawberry.field decorators."""
        code = dedent('''
            import strawberry
            from typing import List

            @strawberry.type
            class Query:
                @strawberry.field
                def users(self) -> List[User]:
                    return get_users()

                @strawberry.field
                async def user(self, id: str) -> User:
                    return get_user(id)
        ''')
        file = tmp_path / "schema.py"
        file.write_text(code)
        patterns = _scan_python_resolvers(file, code)

        assert len(patterns) == 2
        assert patterns[0].type_name == "Query"
        assert patterns[0].field_name == "users"
        assert patterns[1].type_name == "Query"
        assert patterns[1].field_name == "user"

    def test_strawberry_mutation_resolver(self, tmp_path: Path):
        """Detect Strawberry @strawberry.mutation decorators."""
        code = dedent('''
            import strawberry

            @strawberry.type
            class Mutation:
                @strawberry.mutation
                def create_user(self, input: UserInput) -> User:
                    return create_user(input)
        ''')
        file = tmp_path / "schema.py"
        file.write_text(code)
        patterns = _scan_python_resolvers(file, code)

        assert len(patterns) == 1
        assert patterns[0].type_name == "Mutation"
        assert patterns[0].field_name == "create_user"


class TestGraphQLResolverLinker:
    """Tests for the full linker integration."""

    def test_links_resolver_to_schema_type(self, tmp_path: Path):
        """Creates edges from resolvers to schema types."""
        resolver = tmp_path / "resolvers.js"
        resolver.write_text(dedent('''
            const resolvers = {
                Query: {
                    users: (_, args, context) => {
                        return [];
                    },
                },
            };
        '''))

        # Create schema symbols for linking
        schema_symbols = [
            Symbol(
                id="graphql:schema.graphql:1-5:Query:type",
                name="Query",
                kind="type",
                path="schema.graphql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="graphql",
            ),
        ]

        result = link_graphql_resolvers(tmp_path, schema_symbols)

        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "graphql_resolver"
        assert result.symbols[0].name == "Query.users"

        # Should have resolver_for_type edge
        type_edges = [e for e in result.edges if e.edge_type == "resolver_for_type"]
        assert len(type_edges) == 1
        assert type_edges[0].meta["type_name"] == "Query"

    def test_links_resolver_to_schema_field(self, tmp_path: Path):
        """Creates edges from resolvers to schema fields."""
        resolver = tmp_path / "resolvers.py"
        resolver.write_text(dedent('''
            from ariadne import QueryType
            query = QueryType()

            @query.field("users")
            def resolve_users(_, info):
                return []
        '''))

        # Create schema symbols including field
        schema_symbols = [
            Symbol(
                id="graphql:schema.graphql:1-5:Query:type",
                name="Query",
                kind="type",
                path="schema.graphql",
                span=Span(start_line=1, end_line=5, start_col=0, end_col=0),
                language="graphql",
            ),
            Symbol(
                id="graphql:schema.graphql:2-2:users:field",
                name="users",
                kind="field",
                path="schema.graphql",
                span=Span(start_line=2, end_line=2, start_col=0, end_col=0),
                language="graphql",
                meta={"parent_type": "Query"},
            ),
        ]

        result = link_graphql_resolvers(tmp_path, schema_symbols)

        # Should have resolver_implements edge to field
        field_edges = [e for e in result.edges if e.edge_type == "resolver_implements"]
        assert len(field_edges) == 1
        assert field_edges[0].meta["field_name"] == "users"

    def test_cross_language_linking(self, tmp_path: Path):
        """Links JavaScript resolvers to GraphQL schema."""
        resolver = tmp_path / "resolvers.js"
        resolver.write_text(dedent('''
            const resolvers = {
                User: {
                    posts: (parent) => {
                        return parent.posts;
                    },
                },
            };
        '''))

        schema_symbols = [
            Symbol(
                id="graphql:schema.graphql:10-15:User:type",
                name="User",
                kind="type",
                path="schema.graphql",
                span=Span(start_line=10, end_line=15, start_col=0, end_col=0),
                language="graphql",
            ),
        ]

        result = link_graphql_resolvers(tmp_path, schema_symbols)

        assert len(result.edges) >= 1
        assert result.edges[0].meta["cross_language"] is True

    def test_no_edges_without_schema(self, tmp_path: Path):
        """No schema edges created without matching schema symbols."""
        resolver = tmp_path / "resolvers.js"
        resolver.write_text(dedent('''
            const resolvers = {
                Query: {
                    users: () => [],
                },
            };
        '''))

        result = link_graphql_resolvers(tmp_path, [])  # No schema symbols

        assert len(result.symbols) == 1  # Still creates resolver symbol
        assert len(result.edges) == 0  # But no edges without schema

    def test_multiple_resolvers_same_type(self, tmp_path: Path):
        """Multiple resolvers for same type create multiple symbols."""
        resolver = tmp_path / "resolvers.js"
        resolver.write_text(dedent('''
            const resolvers = {
                Query: {
                    users: () => [],
                    posts: () => [],
                    comments: () => [],
                },
            };
        '''))

        result = link_graphql_resolvers(tmp_path, [])

        assert len(result.symbols) == 3
        field_names = {s.meta["field_name"] for s in result.symbols}
        assert field_names == {"users", "posts", "comments"}

    def test_analysis_run_metadata(self, tmp_path: Path):
        """Analysis run includes proper metadata."""
        resolver = tmp_path / "resolvers.js"
        resolver.write_text("const resolvers = { Query: { test: () => {} } };")

        result = link_graphql_resolvers(tmp_path, [])

        assert result.run is not None
        assert result.run.pass_id == "graphql-resolver-linker-v1"
        assert result.run.files_analyzed >= 1
        assert result.run.duration_ms >= 0

    def test_resolver_symbol_metadata(self, tmp_path: Path):
        """Resolver symbols have proper metadata."""
        resolver = tmp_path / "resolvers.py"
        resolver.write_text(dedent('''
            @query.field("users")
            def resolve_users(_, info):
                return []
        '''))

        result = link_graphql_resolvers(tmp_path, [])

        assert len(result.symbols) == 1
        symbol = result.symbols[0]
        assert symbol.kind == "graphql_resolver"
        assert symbol.meta["type_name"] == "Query"
        assert symbol.meta["field_name"] == "users"
        assert symbol.stable_id == "Query.users"
