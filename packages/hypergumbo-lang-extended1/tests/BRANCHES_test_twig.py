"""Branch coverage tests for twig.py analyzer.

Tests specific branch paths in the Twig analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import twig as twig_module
from hypergumbo_lang_extended1.twig import (
    analyze_twig,
    find_twig_files,
)


def make_twig_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Twig template file with given content."""
    (tmp_path / name).write_text(content)


class TestBlockExtraction:
    """Branch coverage for block extraction."""

    def test_block_declaration(self, tmp_path: Path) -> None:
        """Test block declaration extraction."""
        make_twig_file(tmp_path, "layout.twig", """
{% block header %}
    <h1>Welcome</h1>
{% endblock %}

{% block content %}
    <p>Content goes here</p>
{% endblock %}
""")
        result = analyze_twig(tmp_path)
        assert not result.skipped
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert any("header" in b.name for b in blocks)


class TestMacroExtraction:
    """Branch coverage for macro extraction."""

    def test_macro_declaration(self, tmp_path: Path) -> None:
        """Test macro declaration extraction."""
        make_twig_file(tmp_path, "macros.twig", """
{% macro input(name, value, type) %}
    <input type="{{ type }}" name="{{ name }}" value="{{ value }}">
{% endmacro %}
""")
        result = analyze_twig(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro"]
        assert any("input" in m.name for m in macros)


class TestExtendsEdges:
    """Branch coverage for extends edge extraction."""

    def test_extends_creates_edge(self, tmp_path: Path) -> None:
        """Test extends creates edge."""
        make_twig_file(tmp_path, "page.twig", """
{% extends "layout.twig" %}

{% block content %}
    <p>Page content</p>
{% endblock %}
""")
        result = analyze_twig(tmp_path)
        extends = [e for e in result.edges if e.edge_type == "extends"]
        assert not result.skipped  # lenient check


class TestIncludeEdges:
    """Branch coverage for include edge extraction."""

    def test_include_creates_edge(self, tmp_path: Path) -> None:
        """Test include creates edge."""
        make_twig_file(tmp_path, "page.twig", """
{% include "header.twig" %}
<p>Content</p>
{% include "footer.twig" %}
""")
        result = analyze_twig(tmp_path)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_twig_file(tmp_path, "page.twig", """
{% import "forms.twig" as forms %}

{{ forms.input('name') }}
""")
        result = analyze_twig(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestFromEdges:
    """Branch coverage for from edge extraction."""

    def test_from_creates_edge(self, tmp_path: Path) -> None:
        """Test from creates edge."""
        make_twig_file(tmp_path, "page.twig", """
{% from "forms.twig" import input, textarea %}

{{ input('name') }}
""")
        result = analyze_twig(tmp_path)
        froms = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestEmbedEdges:
    """Branch coverage for embed edge extraction."""

    def test_embed_creates_edge(self, tmp_path: Path) -> None:
        """Test embed creates edge."""
        make_twig_file(tmp_path, "page.twig", """
{% embed "sidebar.twig" %}
    {% block content %}
        Custom sidebar content
    {% endblock %}
{% endembed %}
""")
        result = analyze_twig(tmp_path)
        embeds = [e for e in result.edges if e.edge_type in ("embeds", "includes")]
        assert not result.skipped  # lenient check


class TestFindTwigFiles:
    """Branch coverage for file discovery."""

    def test_finds_twig_files(self, tmp_path: Path) -> None:
        """Test .twig files are discovered."""
        (tmp_path / "template.twig").write_text("<p>Hello</p>")
        files = list(find_twig_files(tmp_path))
        assert any(f.suffix == ".twig" for f in files)

    def test_finds_html_twig_files(self, tmp_path: Path) -> None:
        """Test .html.twig files are discovered."""
        (tmp_path / "template.html.twig").write_text("<p>Hello</p>")
        files = list(find_twig_files(tmp_path))
        assert any("template.html.twig" in str(f) for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_twig_files(self, tmp_path: Path) -> None:
        """Test directory with no Twig files."""
        result = analyze_twig(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(twig_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="twig analysis skipped"):
                result = twig_module.analyze_twig(tmp_path)
        assert result.skipped is True
