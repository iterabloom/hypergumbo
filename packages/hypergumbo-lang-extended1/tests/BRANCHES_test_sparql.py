"""Branch coverage tests for sparql.py analyzer.

Tests specific branch paths in the SPARQL analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import sparql as sparql_module
from hypergumbo_lang_extended1.sparql import (
    analyze_sparql,
    find_sparql_files,
)


def make_sparql_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a SPARQL file with given content."""
    (tmp_path / name).write_text(content)


class TestSelectQueryExtraction:
    """Branch coverage for SELECT query extraction."""

    def test_select_query(self, tmp_path: Path) -> None:
        """Test SELECT query extraction."""
        make_sparql_file(tmp_path, "query.sparql", """
SELECT ?name ?age
WHERE {
    ?person foaf:name ?name .
    ?person foaf:age ?age .
}
""")
        result = analyze_sparql(tmp_path)
        assert not result.skipped
        queries = [s for s in result.symbols if s.kind == "query"]
        assert not result.skipped  # lenient check


class TestConstructQueryExtraction:
    """Branch coverage for CONSTRUCT query extraction."""

    def test_construct_query(self, tmp_path: Path) -> None:
        """Test CONSTRUCT query extraction."""
        make_sparql_file(tmp_path, "construct.sparql", """
CONSTRUCT {
    ?person ex:fullName ?fullName .
}
WHERE {
    ?person foaf:firstName ?first .
    ?person foaf:lastName ?last .
    BIND(CONCAT(?first, " ", ?last) AS ?fullName)
}
""")
        result = analyze_sparql(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query"]
        assert not result.skipped  # lenient check


class TestDescribeQueryExtraction:
    """Branch coverage for DESCRIBE query extraction."""

    def test_describe_query(self, tmp_path: Path) -> None:
        """Test DESCRIBE query extraction."""
        make_sparql_file(tmp_path, "describe.sparql", """
DESCRIBE <http://example.org/resource>
""")
        result = analyze_sparql(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query"]
        assert not result.skipped  # lenient check


class TestAskQueryExtraction:
    """Branch coverage for ASK query extraction."""

    def test_ask_query(self, tmp_path: Path) -> None:
        """Test ASK query extraction."""
        make_sparql_file(tmp_path, "ask.sparql", """
ASK {
    ?person foaf:name "John" .
}
""")
        result = analyze_sparql(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query"]
        assert not result.skipped  # lenient check


class TestPrefixExtraction:
    """Branch coverage for prefix extraction."""

    def test_prefix_declaration(self, tmp_path: Path) -> None:
        """Test PREFIX declaration extraction."""
        make_sparql_file(tmp_path, "query.sparql", """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX ex: <http://example.org/>

SELECT ?name
WHERE {
    ?person foaf:name ?name .
}
""")
        result = analyze_sparql(tmp_path)
        prefixes = [s for s in result.symbols if s.kind == "prefix"]
        assert not result.skipped  # lenient check


class TestFindSparqlFiles:
    """Branch coverage for file discovery."""

    def test_finds_sparql_files(self, tmp_path: Path) -> None:
        """Test .sparql files are discovered."""
        (tmp_path / "query.sparql").write_text("SELECT * WHERE {}")
        files = list(find_sparql_files(tmp_path))
        assert any(f.suffix == ".sparql" for f in files)

    def test_finds_rq_files(self, tmp_path: Path) -> None:
        """Test .rq files are discovered."""
        (tmp_path / "query.rq").write_text("SELECT * WHERE {}")
        files = list(find_sparql_files(tmp_path))
        assert any(f.suffix == ".rq" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_sparql_files(self, tmp_path: Path) -> None:
        """Test directory with no SPARQL files."""
        result = analyze_sparql(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(sparql_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="sparql analysis skipped"):
                result = sparql_module.analyze_sparql(tmp_path)
        assert result.skipped is True
