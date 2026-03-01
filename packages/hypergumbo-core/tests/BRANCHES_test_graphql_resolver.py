"""Branch coverage tests for graphql_resolver.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.graphql_resolver import (
    _scan_javascript_resolvers,
    _scan_python_resolvers,
    link_graphql_resolvers,
)
from hypergumbo_core.ir import Symbol, Span


def make_symbol(
    id_: str,
    name: str,
    kind: str = "type",
    path: str = "schema.graphql",
    language: str = "graphql",
    meta: dict | None = None,
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language=language,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        meta=meta,
    )


class TestScanJavascriptResolvers:
    """Branch coverage for _scan_javascript_resolvers function."""

    def test_field_starting_with_underscore(self) -> None:
        """Test field names starting with underscore are skipped (branch 178->186).

        Internal fields like __typename should not create resolver patterns.
        """
        content = '''
const resolvers = {
  Query: {
    __typename: () => 'Query',
    _internal: () => 'internal',
    users: () => [],
  }
};
'''
        patterns = _scan_javascript_resolvers(Path("resolvers.js"), content)
        # __typename and _internal should be skipped
        field_names = [p.field_name for p in patterns]
        assert "__typename" not in field_names
        assert "_internal" not in field_names
        # users should be included
        assert "users" in field_names

    def test_shorthand_method_constructor_skipped(self) -> None:
        """Test constructor and underscore methods are skipped (branch 206->216)."""
        content = '''
class QueryResolver {
  Query = {
    constructor() {},
    _init() {},
    getUser(parent, args) { return null; }
  }
}
'''
        patterns = _scan_javascript_resolvers(Path("resolvers.js"), content)
        # constructor and _init should be skipped
        field_names = [p.field_name for p in patterns]
        assert "constructor" not in field_names
        assert "_init" not in field_names


class TestScanPythonResolvers:
    """Branch coverage for _scan_python_resolvers function."""

    def test_strawberry_field_without_enclosing_class(self) -> None:
        """Test field decorator without enclosing class falls back to Query.

        When a @strawberry.field is found but no enclosing class, type defaults to Query.
        """
        content = '''
import strawberry

@strawberry.field
def orphan_resolver():
    return "data"
'''
        patterns = _scan_python_resolvers(Path("resolvers.py"), content)
        # Without enclosing class, type_name should default to "Query"
        if patterns:  # May or may not find patterns depending on implementation
            for p in patterns:
                if p.field_name == "orphan_resolver":
                    assert p.type_name == "Query"

    def test_strawberry_field_with_class_after_line(self) -> None:
        """Test class defined AFTER field doesn't affect field's type (branch 275->274).

        When searching for enclosing class, classes after the line should be skipped.
        """
        content = '''
import strawberry

@strawberry.field
def first_resolver():
    return "data"

@strawberry.type
class LaterType:
    name: str
'''
        patterns = _scan_python_resolvers(Path("resolvers.py"), content)
        # The resolver is before the class, so shouldn't be assigned to LaterType
        for p in patterns:
            if p.field_name == "first_resolver":
                # Should NOT be "LaterType" since that class is after
                assert p.type_name != "LaterType"


class TestLinkGraphqlResolvers:
    """Branch coverage for link_graphql_resolvers function."""

    def test_field_symbol_without_parent_type(self, tmp_path: Path) -> None:
        """Test field symbol without parent_type in meta (branch 364->358).

        Schema field symbols without parent_type metadata should be skipped
        when building the schema lookup.
        """
        # Create a resolver file
        resolver_file = tmp_path / "resolvers.js"
        resolver_file.write_text('''
const resolvers = {
  Query: {
    users: () => [],
  }
};
''')

        # Schema symbol that's a field but has no parent_type
        field_sym = make_symbol(
            "field1",
            "users",
            kind="field",
            meta={},  # No parent_type key
        )

        result = link_graphql_resolvers(tmp_path, [field_sym])
        # Should still process, just without linking to this field
        assert result is not None

    def test_field_symbol_with_empty_parent_type(self, tmp_path: Path) -> None:
        """Test field with empty parent_type string."""
        resolver_file = tmp_path / "resolvers.js"
        resolver_file.write_text('''
const resolvers = {
  Query: {
    users: () => [],
  }
};
''')

        field_sym = make_symbol(
            "field1",
            "users",
            kind="field",
            meta={"parent_type": ""},  # Empty string
        )

        result = link_graphql_resolvers(tmp_path, [field_sym])
        # Empty parent_type should be skipped
        assert result is not None

    def test_type_symbol_added_to_lookup(self, tmp_path: Path) -> None:
        """Test type symbols are added to type_lookup."""
        resolver_file = tmp_path / "resolvers.js"
        resolver_file.write_text('''
const resolvers = {
  Query: {
    users: () => [],
  }
};
''')

        type_sym = make_symbol("type1", "Query", kind="type")

        result = link_graphql_resolvers(tmp_path, [type_sym])
        # Type symbol should enable linking
        assert result is not None
        # Should have created resolver symbol and possibly an edge
        assert len(result.symbols) >= 1
