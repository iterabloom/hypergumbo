"""Tests for the SPARQL query analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import sparql as sparql_module
from hypergumbo_lang_extended1.sparql import (
    SPARQLAnalysisResult,
    analyze_sparql,
    find_sparql_files,
    is_sparql_tree_sitter_available,
)


def make_sparql_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a SPARQL file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindSPARQLFiles:
    """Tests for find_sparql_files function."""

    def test_finds_sparql_files(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "query.sparql", "SELECT * WHERE { ?s ?p ?o }")
        make_sparql_file(tmp_path, "queries/other.sparql", "ASK { ?s ?p ?o }")
        files = find_sparql_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"query.sparql", "other.sparql"}

    def test_finds_rq_files(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "example.rq", "SELECT ?x WHERE { ?x a foaf:Person }")
        files = find_sparql_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "example.rq"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_sparql_files(tmp_path)
        assert files == []


class TestIsSPARQLTreeSitterAvailable:
    """Tests for is_sparql_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_sparql_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(sparql_module, "is_sparql_tree_sitter_available", return_value=False):
            assert sparql_module.is_sparql_tree_sitter_available() is False


class TestAnalyzeSPARQL:
    """Tests for analyze_sparql function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", "SELECT * WHERE { ?s ?p ?o }")
        with patch.object(sparql_module, "is_sparql_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="SPARQL analysis skipped"):
                result = sparql_module.analyze_sparql(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_prefix(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>

SELECT ?name
WHERE {
  ?person foaf:name ?name
}
""")
        result = analyze_sparql(tmp_path)
        assert not result.skipped
        prefix = next((s for s in result.symbols if s.kind == "prefix"), None)
        assert prefix is not None
        assert prefix.name == "foaf"
        assert prefix.language == "sparql"
        assert prefix.meta.get("iri") == "http://xmlns.com/foaf/0.1/"
        assert prefix.meta.get("is_standard_vocabulary") is True

    def test_extracts_multiple_prefixes(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX ex: <http://example.org/>

SELECT * WHERE { ?s ?p ?o }
""")
        result = analyze_sparql(tmp_path)
        prefixes = [s for s in result.symbols if s.kind == "prefix"]
        assert len(prefixes) == 3
        names = {p.name for p in prefixes}
        assert names == {"rdf", "foaf", "ex"}

    def test_standard_vocabulary_detection(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX custom: <http://example.org/custom/>

SELECT * WHERE { ?s ?p ?o }
""")
        result = analyze_sparql(tmp_path)
        prefixes = {s.name: s for s in result.symbols if s.kind == "prefix"}
        assert prefixes["rdf"].meta.get("is_standard_vocabulary") is True
        assert prefixes["custom"].meta.get("is_standard_vocabulary") is False

    def test_extracts_select_query(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
SELECT ?name ?email
WHERE {
  ?person a foaf:Person .
  ?person foaf:name ?name
}
""")
        result = analyze_sparql(tmp_path)
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert query.meta.get("query_type") == "SELECT"
        assert "?name" in query.meta.get("variables", [])
        assert "?email" in query.meta.get("variables", [])

    def test_extracts_select_star(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
SELECT *
WHERE {
  ?s ?p ?o
}
""")
        result = analyze_sparql(tmp_path)
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert "*" in query.meta.get("variables", [])

    def test_extracts_construct_query(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
CONSTRUCT {
  ?person foaf:fullName ?name
}
WHERE {
  ?person foaf:name ?name
}
""")
        result = analyze_sparql(tmp_path)
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert query.meta.get("query_type") == "CONSTRUCT"

    def test_extracts_ask_query(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
ASK {
  ?person foaf:name "John Doe"
}
""")
        result = analyze_sparql(tmp_path)
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert query.meta.get("query_type") == "ASK"

    def test_extracts_describe_query(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
DESCRIBE <http://example.org/resource>
""")
        result = analyze_sparql(tmp_path)
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert query.meta.get("query_type") == "DESCRIBE"

    def test_extracts_base_declaration(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
BASE <http://example.org/>

SELECT * WHERE { ?s ?p ?o }
""")
        result = analyze_sparql(tmp_path)
        base = next((s for s in result.symbols if s.kind == "base"), None)
        assert base is not None
        assert base.name == "BASE"
        assert base.meta.get("iri") == "http://example.org/"

    def test_counts_triple_patterns(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
SELECT *
WHERE {
  ?person a foaf:Person .
  ?person foaf:name ?name .
  ?person foaf:mbox ?email
}
""")
        result = analyze_sparql(tmp_path)
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert query.meta.get("pattern_count") == 3

    def test_creates_vocabulary_edges(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT ?name
WHERE {
  ?person rdf:type foaf:Person .
  ?person foaf:name ?name
}
""")
        result = analyze_sparql(tmp_path)
        edges = [e for e in result.edges if e.edge_type == "uses_vocabulary"]
        assert len(edges) >= 2
        dst_prefixes = {e.dst.split(":")[-1] for e in edges}
        assert "foaf" in dst_prefixes
        assert "rdf" in dst_prefixes

    def test_pass_id(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT * WHERE { ?s ?p ?o }
""")
        result = analyze_sparql(tmp_path)
        prefix = next((s for s in result.symbols if s.kind == "prefix"), None)
        assert prefix is not None
        assert prefix.origin == "sparql.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", "SELECT * WHERE { ?s ?p ?o }")
        result = analyze_sparql(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "sparql.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_sparql(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT * WHERE { ?s ?p ?o }
""")
        result = analyze_sparql(tmp_path)
        prefix = next((s for s in result.symbols if s.kind == "prefix"), None)
        assert prefix is not None
        assert prefix.id == prefix.stable_id
        assert "sparql:" in prefix.id
        assert "test.sparql" in prefix.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT * WHERE { ?s ?p ?o }
""")
        result = analyze_sparql(tmp_path)
        prefix = next((s for s in result.symbols if s.kind == "prefix"), None)
        assert prefix is not None
        assert prefix.span is not None
        assert prefix.span.start_line >= 1
        assert prefix.span.end_line >= prefix.span.start_line

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "query-a.sparql", """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT * WHERE { ?s ?p ?o }
""")
        make_sparql_file(tmp_path, "query-b.rq", """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
ASK { ?s rdf:type ?o }
""")
        result = analyze_sparql(tmp_path)
        prefixes = [s for s in result.symbols if s.kind == "prefix"]
        queries = [s for s in result.symbols if s.kind == "query"]
        assert len(prefixes) == 2
        assert len(queries) == 2

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "a.sparql", "SELECT * WHERE { ?s ?p ?o }")
        make_sparql_file(tmp_path, "b.sparql", "ASK { ?s ?p ?o }")
        make_sparql_file(tmp_path, "c.rq", "DESCRIBE ?x")
        result = analyze_sparql(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_long_iri_truncation(self, tmp_path: Path) -> None:
        long_iri = "http://example.org/" + "x" * 100
        make_sparql_file(tmp_path, "test.sparql", f"""
PREFIX ex: <{long_iri}>
SELECT * WHERE {{ ?s ?p ?o }}
""")
        result = analyze_sparql(tmp_path)
        prefix = next((s for s in result.symbols if s.kind == "prefix"), None)
        assert prefix is not None
        assert len(prefix.signature) < len(long_iri)
        assert "..." in prefix.signature

    def test_query_signature_with_many_variables(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "test.sparql", """
SELECT ?a ?b ?c ?d ?e ?f ?g ?h
WHERE { ?s ?p ?o }
""")
        result = analyze_sparql(tmp_path)
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert "(+3 more)" in query.signature

    def test_multiple_queries_in_file(self, tmp_path: Path) -> None:
        # SPARQL files can have multiple queries (separated by comments or in practice)
        # The grammar parses them as separate units
        make_sparql_file(tmp_path, "test.sparql", """
SELECT ?name WHERE { ?person foaf:name ?name }
""")
        make_sparql_file(tmp_path, "test2.sparql", """
ASK { ?person a foaf:Person }
""")
        result = analyze_sparql(tmp_path)
        queries = [s for s in result.symbols if s.kind == "query"]
        assert len(queries) == 2

    def test_complete_sparql_file(self, tmp_path: Path) -> None:
        make_sparql_file(tmp_path, "example.sparql", """
BASE <http://example.org/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX ex: <http://example.org/vocab/>

SELECT ?name ?email ?homepage
WHERE {
  ?person rdf:type foaf:Person .
  ?person foaf:name ?name .
  OPTIONAL { ?person foaf:mbox ?email }
  OPTIONAL { ?person foaf:homepage ?homepage }
}
ORDER BY ?name
LIMIT 100
""")
        result = analyze_sparql(tmp_path)

        # Check base
        base = next((s for s in result.symbols if s.kind == "base"), None)
        assert base is not None

        # Check prefixes
        prefixes = [s for s in result.symbols if s.kind == "prefix"]
        assert len(prefixes) == 4

        # Check query
        query = next((s for s in result.symbols if s.kind == "query"), None)
        assert query is not None
        assert query.meta.get("query_type") == "SELECT"
        assert len(query.meta.get("variables", [])) == 3

        # Check edges
        edges = [e for e in result.edges if e.edge_type == "uses_vocabulary"]
        assert len(edges) >= 2

    def test_wikidata_prefixes(self, tmp_path: Path) -> None:
        """Test that Wikidata prefixes are recognized as standard."""
        make_sparql_file(tmp_path, "test.sparql", """
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>

SELECT ?item ?label
WHERE {
  ?item wdt:P31 wd:Q5 .
  ?item rdfs:label ?label
}
""")
        result = analyze_sparql(tmp_path)
        prefixes = {s.name: s for s in result.symbols if s.kind == "prefix"}
        assert prefixes["wd"].meta.get("is_standard_vocabulary") is True
        assert prefixes["wdt"].meta.get("is_standard_vocabulary") is True
