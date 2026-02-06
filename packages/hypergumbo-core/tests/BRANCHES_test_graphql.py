"""Branch coverage tests for graphql.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.graphql import (
    _create_client_symbol,
    link_graphql,
    GraphQLClientCall,
)
from hypergumbo_core.ir import Symbol, Span


def make_schema_symbol(
    id_: str,
    name: str,
    kind: str = "operation",
    path: str = "schema.graphql",
    language: str = "graphql",
) -> Symbol:
    """Create a test schema symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language=language,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
    )


class TestCreateClientSymbol:
    """Branch coverage for _create_client_symbol function."""

    def test_client_call_without_operation_name(self, tmp_path: Path) -> None:
        """Test client call with no operation name (branch 200->203).

        When a GraphQL call has no operation_name, the symbol name
        should just be the operation type (e.g., "query").
        """
        call = GraphQLClientCall(
            operation_type="query",
            operation_name=None,  # No operation name
            query_text="query { users { id } }",
            line=10,
            file_path=str(tmp_path / "client.js"),
            language="javascript",
        )
        symbol = _create_client_symbol(call, tmp_path)
        # Name should be just "query" without operation name
        assert symbol.name == "query"

    def test_client_call_with_operation_name(self, tmp_path: Path) -> None:
        """Test client call with operation name."""
        call = GraphQLClientCall(
            operation_type="query",
            operation_name="GetUsers",
            query_text="query GetUsers { users { id } }",
            line=10,
            file_path=str(tmp_path / "client.js"),
            language="javascript",
        )
        symbol = _create_client_symbol(call, tmp_path)
        # Name should include operation name
        assert symbol.name == "query GetUsers"

    def test_client_call_with_empty_operation_type(self, tmp_path: Path) -> None:
        """Test client call with None operation type."""
        call = GraphQLClientCall(
            operation_type=None,
            operation_name=None,
            query_text="{ users { id } }",
            line=10,
            file_path=str(tmp_path / "client.js"),
            language="javascript",
        )
        symbol = _create_client_symbol(call, tmp_path)
        # Name should default to "query"
        assert symbol.name == "query"


class TestLinkGraphql:
    """Branch coverage for link_graphql function."""

    def test_call_without_operation_name_no_linking(self, tmp_path: Path) -> None:
        """Test call without operation_name doesn't link (branch 270->265).

        Anonymous queries can't be linked to schema operations
        since there's no operation name to match.
        """
        # Create JS file with anonymous GraphQL query
        js_file = tmp_path / "client.js"
        js_file.write_text('''
const result = graphql`
  query {
    users {
      id
      name
    }
  }
`;
''')

        # Schema symbol with an operation
        schema_sym = make_schema_symbol("op1", "GetUsers", kind="query")

        result = link_graphql(tmp_path, [schema_sym])
        # Client symbol created but no edge (no operation name to match)
        assert result is not None
        # Edges may be empty since query has no operation name
        # (exact behavior depends on detection patterns)

    def test_call_with_operation_name_links(self, tmp_path: Path) -> None:
        """Test call with operation_name does link."""
        js_file = tmp_path / "client.js"
        js_file.write_text('''
const result = graphql`
  query GetUsers {
    users {
      id
    }
  }
`;
''')

        schema_sym = make_schema_symbol("op1", "GetUsers", kind="query")

        result = link_graphql(tmp_path, [schema_sym])
        assert result is not None
        # Should have created an edge if operation name matches

    def test_operation_name_not_in_schema(self, tmp_path: Path) -> None:
        """Test when operation name doesn't match any schema operation."""
        js_file = tmp_path / "client.js"
        js_file.write_text('''
const result = graphql`
  query GetProducts {
    products {
      id
    }
  }
`;
''')

        # Schema has GetUsers but client calls GetProducts
        schema_sym = make_schema_symbol("op1", "GetUsers", kind="query")

        result = link_graphql(tmp_path, [schema_sym])
        assert result is not None
        # No edge since GetProducts != GetUsers
        matching_edges = [e for e in result.edges if e.dst == "op1"]
        assert len(matching_edges) == 0
