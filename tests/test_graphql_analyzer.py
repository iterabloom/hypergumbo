"""Tests for GraphQL analyzer using tree-sitter-graphql.

Tests verify that the analyzer correctly extracts:
- Type definitions (object, input, interface, enum, scalar)
- Fragment definitions
- Operation definitions (query, mutation, subscription)
- Directive definitions
"""

from hypergumbo.analyze.graphql import (
    PASS_ID,
    PASS_VERSION,
    GraphQLAnalysisResult,
    analyze_graphql_files,
    find_graphql_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "graphql-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_type(tmp_path):
    """Test detection of object type definition."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""
type User {
    id: ID!
    name: String!
    email: String!
}
""")
    result = analyze_graphql_files(tmp_path)

    assert not result.skipped
    types = [s for s in result.symbols if s.kind == "type"]
    assert len(types) >= 1
    assert types[0].name == "User"
    assert types[0].language == "graphql"


def test_analyze_input_type(tmp_path):
    """Test detection of input type definition."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""
input CreateUserInput {
    name: String!
    email: String!
}
""")
    result = analyze_graphql_files(tmp_path)

    inputs = [s for s in result.symbols if s.kind == "input"]
    assert len(inputs) >= 1
    assert inputs[0].name == "CreateUserInput"


def test_analyze_interface(tmp_path):
    """Test detection of interface type definition."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""
interface Node {
    id: ID!
}
""")
    result = analyze_graphql_files(tmp_path)

    interfaces = [s for s in result.symbols if s.kind == "interface"]
    assert len(interfaces) >= 1
    assert interfaces[0].name == "Node"


def test_analyze_enum(tmp_path):
    """Test detection of enum type definition."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""
enum Status {
    ACTIVE
    INACTIVE
    PENDING
}
""")
    result = analyze_graphql_files(tmp_path)

    enums = [s for s in result.symbols if s.kind == "enum"]
    assert len(enums) >= 1
    assert enums[0].name == "Status"


def test_analyze_scalar(tmp_path):
    """Test detection of scalar type definition."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""
scalar DateTime
scalar JSON
""")
    result = analyze_graphql_files(tmp_path)

    scalars = [s for s in result.symbols if s.kind == "scalar"]
    assert len(scalars) >= 2
    names = [s.name for s in scalars]
    assert "DateTime" in names
    assert "JSON" in names


def test_analyze_directive(tmp_path):
    """Test detection of directive definition."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""
directive @auth(requires: Role!) on FIELD_DEFINITION
directive @deprecated(reason: String) on FIELD_DEFINITION
""")
    result = analyze_graphql_files(tmp_path)

    directives = [s for s in result.symbols if s.kind == "directive"]
    assert len(directives) >= 2
    names = [d.name for d in directives]
    assert "auth" in names
    assert "deprecated" in names
    # Check canonical name has @ prefix
    auth = next(d for d in directives if d.name == "auth")
    assert auth.canonical_name == "@auth"


def test_analyze_fragment(tmp_path):
    """Test detection of fragment definition."""
    graphql_file = tmp_path / "queries.graphql"
    graphql_file.write_text("""
fragment UserFields on User {
    id
    name
    email
}
""")
    result = analyze_graphql_files(tmp_path)

    fragments = [s for s in result.symbols if s.kind == "fragment"]
    assert len(fragments) >= 1
    assert fragments[0].name == "UserFields"


def test_analyze_query_operation(tmp_path):
    """Test detection of query operation definition."""
    graphql_file = tmp_path / "queries.graphql"
    graphql_file.write_text("""
query GetUser($id: ID!) {
    user(id: $id) {
        id
        name
    }
}
""")
    result = analyze_graphql_files(tmp_path)

    queries = [s for s in result.symbols if s.kind == "query"]
    assert len(queries) >= 1
    assert queries[0].name == "GetUser"


def test_analyze_mutation_operation(tmp_path):
    """Test detection of mutation operation definition."""
    graphql_file = tmp_path / "mutations.graphql"
    graphql_file.write_text("""
mutation CreateUser($input: CreateUserInput!) {
    createUser(input: $input) {
        id
        name
    }
}
""")
    result = analyze_graphql_files(tmp_path)

    mutations = [s for s in result.symbols if s.kind == "mutation"]
    assert len(mutations) >= 1
    assert mutations[0].name == "CreateUser"


def test_find_graphql_files(tmp_path):
    """Test that GraphQL files are discovered correctly."""
    (tmp_path / "schema.graphql").write_text("type Query {}")
    (tmp_path / "queries.gql").write_text("query GetUser {}")
    (tmp_path / "not_graphql.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "types.graphql").write_text("type User {}")

    files = list(find_graphql_files(tmp_path))
    # Should find .graphql and .gql files
    assert len(files) >= 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no GraphQL files."""
    result = analyze_graphql_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("type Query {}")

    result = analyze_graphql_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    graphql_file = tmp_path / "broken.graphql"
    graphql_file.write_text("type broken {{{{")

    # Should not raise an exception
    result = analyze_graphql_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, GraphQLAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""type TestType {
    id: ID!
}
""")
    result = analyze_graphql_files(tmp_path)

    types = [s for s in result.symbols if s.kind == "type"]
    assert len(types) >= 1

    # Check span
    assert types[0].span.start_line >= 1
    assert types[0].span.end_line >= types[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.graphql import is_graphql_tree_sitter_available

    # The function should return a boolean
    result = is_graphql_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_graphql_files(tmp_path):
    """Test analysis across multiple GraphQL files."""
    (tmp_path / "schema.graphql").write_text("""
type Query {
    users: [User!]!
}

type User {
    id: ID!
    name: String!
}
""")
    (tmp_path / "mutations.graphql").write_text("""
type Mutation {
    createUser(name: String!): User
}
""")

    result = analyze_graphql_files(tmp_path)

    types = [s for s in result.symbols if s.kind == "type"]
    assert len(types) >= 3  # Query, User, Mutation


def test_complete_graphql_schema(tmp_path):
    """Test a complete GraphQL schema structure."""
    graphql_file = tmp_path / "schema.graphql"
    graphql_file.write_text("""
scalar DateTime

enum UserRole {
    ADMIN
    USER
    GUEST
}

interface Node {
    id: ID!
}

type User implements Node {
    id: ID!
    name: String!
    role: UserRole!
    createdAt: DateTime!
}

input CreateUserInput {
    name: String!
    role: UserRole!
}

type Query {
    user(id: ID!): User
    users: [User!]!
}

type Mutation {
    createUser(input: CreateUserInput!): User
}

fragment UserFields on User {
    id
    name
    role
}
""")
    result = analyze_graphql_files(tmp_path)

    # Check for expected symbol kinds
    kinds = {s.kind for s in result.symbols}
    assert "scalar" in kinds
    assert "enum" in kinds
    assert "interface" in kinds
    assert "type" in kinds
    assert "input" in kinds
    assert "fragment" in kinds


class TestGraphQLSignatureExtraction:
    """Tests for GraphQL operation signature extraction."""

    def test_query_with_variables(self, tmp_path):
        """Extract signature from query with variable definitions."""
        graphql_file = tmp_path / "queries.graphql"
        graphql_file.write_text("""
query GetUser($id: ID!, $includeEmail: Boolean) {
    user(id: $id) {
        name
        email @include(if: $includeEmail)
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query" and s.name == "GetUser"]
        assert len(queries) == 1
        assert queries[0].signature is not None
        assert "$id: ID!" in queries[0].signature
        assert "$includeEmail: Boolean" in queries[0].signature

    def test_mutation_with_variables(self, tmp_path):
        """Extract signature from mutation with variables."""
        graphql_file = tmp_path / "mutations.graphql"
        graphql_file.write_text("""
mutation CreateUser($name: String!, $email: String!) {
    createUser(name: $name, email: $email) {
        id
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        mutations = [s for s in result.symbols if s.kind == "mutation" and s.name == "CreateUser"]
        assert len(mutations) == 1
        assert mutations[0].signature is not None
        assert "$name: String!" in mutations[0].signature

    def test_operation_without_variables(self, tmp_path):
        """Operation without variables has no signature."""
        graphql_file = tmp_path / "queries.graphql"
        graphql_file.write_text("""
query AllUsers {
    users {
        id
        name
    }
}
""")
        result = analyze_graphql_files(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query" and s.name == "AllUsers"]
        assert len(queries) == 1
        # No variables = no signature
        assert queries[0].signature is None
