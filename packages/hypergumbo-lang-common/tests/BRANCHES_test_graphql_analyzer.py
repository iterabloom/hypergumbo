# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for graphql.py analyzer.

Tests specific branch paths in the GraphQL analyzer that may not be covered
by the main test suite. Focuses on:
- Union type definitions
- Subscription operations
- Edge cases in signature extraction
"""
from pathlib import Path

from hypergumbo_lang_common.graphql import analyze_graphql_files


def make_graphql_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a GraphQL file with given content."""
    (tmp_path / name).write_text(content)


class TestGraphQLUnionTypes:
    """Branch coverage for union_type_definition extraction."""

    def test_simple_union(self, tmp_path: Path) -> None:
        """Test simple union type extraction."""
        make_graphql_file(tmp_path, "schema.graphql", """
union SearchResult = User | Post | Comment
""")
        result = analyze_graphql_files(tmp_path)
        unions = [s for s in result.symbols if s.kind == "union"]
        assert len(unions) == 1
        assert unions[0].name == "SearchResult"
        assert unions[0].language == "graphql"

    def test_union_with_description(self, tmp_path: Path) -> None:
        """Test union type with description."""
        make_graphql_file(tmp_path, "schema.graphql", '''
"""A result that can be returned from search."""
union SearchResult = Article | Video | Image
''')
        result = analyze_graphql_files(tmp_path)
        unions = [s for s in result.symbols if s.kind == "union"]
        assert len(unions) == 1
        assert unions[0].name == "SearchResult"

    def test_multiple_unions(self, tmp_path: Path) -> None:
        """Test multiple union type definitions."""
        make_graphql_file(tmp_path, "schema.graphql", """
union MediaType = Image | Video | Audio
union ContentType = Article | Blog | News
""")
        result = analyze_graphql_files(tmp_path)
        unions = [s for s in result.symbols if s.kind == "union"]
        assert len(unions) == 2
        names = [u.name for u in unions]
        assert "MediaType" in names
        assert "ContentType" in names


class TestGraphQLSubscriptions:
    """Branch coverage for subscription operation extraction."""

    def test_simple_subscription(self, tmp_path: Path) -> None:
        """Test subscription operation extraction."""
        make_graphql_file(tmp_path, "subscriptions.graphql", """
subscription OnUserCreated {
    userCreated {
        id
        name
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        subscriptions = [s for s in result.symbols if s.kind == "subscription"]
        assert len(subscriptions) == 1
        assert subscriptions[0].name == "OnUserCreated"

    def test_subscription_with_variables(self, tmp_path: Path) -> None:
        """Test subscription with variable definitions."""
        make_graphql_file(tmp_path, "subscriptions.graphql", """
subscription OnMessageReceived($channelId: ID!) {
    messageReceived(channelId: $channelId) {
        id
        content
        sender {
            name
        }
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        subscriptions = [s for s in result.symbols if s.kind == "subscription"]
        assert len(subscriptions) == 1
        assert subscriptions[0].name == "OnMessageReceived"
        # Should have signature with variable
        assert subscriptions[0].signature is not None
        assert "$channelId: ID!" in subscriptions[0].signature

    def test_multiple_subscriptions(self, tmp_path: Path) -> None:
        """Test multiple subscription definitions."""
        make_graphql_file(tmp_path, "subscriptions.graphql", """
subscription OnUserOnline($userId: ID!) {
    userOnline(userId: $userId) {
        id
        status
    }
}

subscription OnNewNotification {
    notification {
        id
        message
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        subscriptions = [s for s in result.symbols if s.kind == "subscription"]
        assert len(subscriptions) == 2
        names = [s.name for s in subscriptions]
        assert "OnUserOnline" in names
        assert "OnNewNotification" in names


class TestGraphQLComplexSignatures:
    """Branch coverage for complex signature extraction."""

    def test_query_with_multiple_variables(self, tmp_path: Path) -> None:
        """Test query with multiple variable definitions."""
        make_graphql_file(tmp_path, "queries.graphql", """
query SearchUsers($term: String!, $limit: Int, $offset: Int, $sortBy: SortOrder!) {
    searchUsers(term: $term, limit: $limit, offset: $offset, sortBy: $sortBy) {
        id
        name
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query" and s.name == "SearchUsers"]
        assert len(queries) == 1
        sig = queries[0].signature
        assert sig is not None
        assert "$term: String!" in sig
        assert "$limit: Int" in sig
        assert "$offset: Int" in sig
        assert "$sortBy: SortOrder!" in sig

    def test_mutation_with_input_type_variable(self, tmp_path: Path) -> None:
        """Test mutation with input type variable."""
        make_graphql_file(tmp_path, "mutations.graphql", """
mutation UpdateUser($id: ID!, $input: UpdateUserInput!) {
    updateUser(id: $id, input: $input) {
        id
        name
        updatedAt
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        mutations = [s for s in result.symbols if s.kind == "mutation" and s.name == "UpdateUser"]
        assert len(mutations) == 1
        sig = mutations[0].signature
        assert sig is not None
        assert "$id: ID!" in sig
        assert "$input: UpdateUserInput!" in sig

    def test_query_with_list_variable(self, tmp_path: Path) -> None:
        """Test query with list type variable."""
        make_graphql_file(tmp_path, "queries.graphql", """
query GetUsersByIds($ids: [ID!]!) {
    users(ids: $ids) {
        id
        name
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query" and s.name == "GetUsersByIds"]
        assert len(queries) == 1
        sig = queries[0].signature
        assert sig is not None
        assert "$ids: [ID!]!" in sig


class TestGraphQLEdgeCases:
    """Edge case tests for graphql analyzer."""

    def test_gql_extension(self, tmp_path: Path) -> None:
        """Test .gql file extension is recognized."""
        make_graphql_file(tmp_path, "schema.gql", """
type Query {
    hello: String
}
""")
        result = analyze_graphql_files(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 1
        assert types[0].name == "Query"

    def test_mixed_extensions(self, tmp_path: Path) -> None:
        """Test both .graphql and .gql files are processed."""
        make_graphql_file(tmp_path, "schema.graphql", "type Query { hello: String }")
        make_graphql_file(tmp_path, "mutations.gql", "type Mutation { doSomething: Boolean }")
        result = analyze_graphql_files(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 2

    def test_nested_directory(self, tmp_path: Path) -> None:
        """Test GraphQL files in nested directories are found."""
        subdir = tmp_path / "graphql" / "schema"
        subdir.mkdir(parents=True)
        make_graphql_file(subdir, "types.graphql", "type User { id: ID! }")
        result = analyze_graphql_files(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 1
        assert types[0].name == "User"

    def test_empty_type_definition(self, tmp_path: Path) -> None:
        """Test empty type definition (extension stub)."""
        make_graphql_file(tmp_path, "schema.graphql", """
type Query
""")
        result = analyze_graphql_files(tmp_path)
        # Should still extract the type even without fields
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 1
        assert types[0].name == "Query"

    def test_directive_on_type(self, tmp_path: Path) -> None:
        """Test type with directive is still extracted."""
        make_graphql_file(tmp_path, "schema.graphql", """
type User @entity {
    id: ID! @id
    name: String!
}
""")
        result = analyze_graphql_files(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 1
        assert types[0].name == "User"
