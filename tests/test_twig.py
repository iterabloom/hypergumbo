"""Tests for the Twig template analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import twig as twig_module
from hypergumbo_lang_extended1.twig import (
    TwigAnalysisResult,
    analyze_twig,
    find_twig_files,
    is_twig_tree_sitter_available,
)


def make_twig_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Twig file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindTwigFiles:
    """Tests for find_twig_files function."""

    def test_finds_twig_files(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "base.twig", "{% block content %}{% endblock %}")
        make_twig_file(tmp_path, "templates/page.twig", "{% extends 'base.twig' %}")
        files = find_twig_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"base.twig", "page.twig"}

    def test_finds_html_twig_files(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "base.html.twig", "{% block content %}{% endblock %}")
        files = find_twig_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "base.html.twig"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_twig_files(tmp_path)
        assert files == []


class TestIsTwigTreeSitterAvailable:
    """Tests for is_twig_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_twig_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(twig_module, "is_twig_tree_sitter_available", return_value=False):
            assert twig_module.is_twig_tree_sitter_available() is False


class TestAnalyzeTwig:
    """Tests for analyze_twig function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "Hello")
        with patch.object(twig_module, "is_twig_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Twig analysis skipped"):
                result = twig_module.analyze_twig(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_twig(tmp_path)
        assert result.symbols == []
        assert result.run is None

    def test_extracts_extends(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "page.twig", '{% extends "base.html.twig" %}')
        result = analyze_twig(tmp_path)
        assert not result.skipped
        extends = next((s for s in result.symbols if s.kind == "extends"), None)
        assert extends is not None
        assert extends.name == "extends base.html.twig"
        assert extends.meta.get("template") == "base.html.twig"

    def test_extends_creates_edge(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "page.twig", '{% extends "base.html.twig" %}')
        result = analyze_twig(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "extends_template"), None)
        assert edge is not None

    def test_extracts_block(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", """{% block content %}
Hello World
{% endblock %}""")
        result = analyze_twig(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.name == "content"
        assert "{% block content %}" in block.signature

    def test_extracts_multiple_blocks(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", """{% block header %}Header{% endblock %}
{% block content %}Content{% endblock %}
{% block footer %}Footer{% endblock %}""")
        result = analyze_twig(tmp_path)
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert len(blocks) == 3
        names = {b.name for b in blocks}
        assert names == {"header", "content", "footer"}

    def test_extracts_include_directive(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", '{% include "partials/header.twig" %}')
        result = analyze_twig(tmp_path)
        include = next((s for s in result.symbols if s.kind == "include"), None)
        assert include is not None
        assert include.name == "include partials/header.twig"
        assert include.meta.get("template") == "partials/header.twig"

    def test_include_creates_edge(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", '{% include "partials/header.twig" %}')
        result = analyze_twig(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "includes_template"), None)
        assert edge is not None

    def test_extracts_include_function(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{{ include('partials/header.twig') }}")
        result = analyze_twig(tmp_path)
        include = next((s for s in result.symbols if s.kind == "include"), None)
        assert include is not None
        assert "partials/header.twig" in include.name

    def test_extracts_macro(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "macros.twig", """{% macro button(text) %}
<button>{{ text }}</button>
{% endmacro %}""")
        result = analyze_twig(tmp_path)
        macro = next((s for s in result.symbols if s.kind == "macro"), None)
        assert macro is not None
        assert macro.name == "button"
        assert "{% macro button() %}" in macro.signature

    def test_extracts_for_loop(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", """{% for item in items %}
{{ item.name }}
{% endfor %}""")
        result = analyze_twig(tmp_path)
        for_loop = next((s for s in result.symbols if s.kind == "for_loop"), None)
        assert for_loop is not None
        assert "for item in items" in for_loop.name
        assert for_loop.meta.get("loop_variable") == "item"
        assert for_loop.meta.get("iterable") == "items"

    def test_extracts_conditional(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", """{% if user %}
Hello, {{ user.name }}!
{% endif %}""")
        result = analyze_twig(tmp_path)
        conditional = next((s for s in result.symbols if s.kind == "conditional"), None)
        assert conditional is not None
        assert "if user" in conditional.name
        assert conditional.meta.get("condition") == "user"

    def test_extracts_function_call(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{{ date('now') }}")
        result = analyze_twig(tmp_path)
        func = next((s for s in result.symbols if s.kind == "function_call"), None)
        assert func is not None
        assert func.name == "date"
        assert func.meta.get("arg_count") == 1

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{% block content %}{% endblock %}")
        result = analyze_twig(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "twig.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0
        assert result.run.files_analyzed == 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "base.twig", "{% block content %}{% endblock %}")
        make_twig_file(tmp_path, "page.twig", '{% extends "base.twig" %}')
        result = analyze_twig(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 2

    def test_pass_id(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{% block content %}{% endblock %}")
        result = analyze_twig(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.origin == "twig.tree_sitter"

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{% block content %}{% endblock %}")
        result = analyze_twig(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.id == block.stable_id
        assert "twig:" in block.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{% block content %}{% endblock %}")
        result = analyze_twig(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        assert block.span is not None
        assert block.span.start_line >= 1

    def test_complete_template(self, tmp_path: Path) -> None:
        """Test a complete Twig template."""
        make_twig_file(tmp_path, "page.html.twig", """{% extends "base.html.twig" %}

{% block title %}My Page{% endblock %}

{% block content %}
  {% include "partials/header.twig" %}

  {% for item in items %}
    <li>{{ item.name }}</li>
  {% endfor %}

  {% if user %}
    <p>Hello, {{ user.name }}!</p>
  {% endif %}

  {{ include('partials/footer.twig') }}
{% endblock %}""")
        result = analyze_twig(tmp_path)

        # Check extends
        extends = next((s for s in result.symbols if s.kind == "extends"), None)
        assert extends is not None
        assert extends.meta.get("template") == "base.html.twig"

        # Check blocks
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert len(blocks) == 2
        block_names = {b.name for b in blocks}
        assert block_names == {"title", "content"}

        # Check includes
        includes = [s for s in result.symbols if s.kind == "include"]
        assert len(includes) == 2

        # Check for loop
        for_loops = [s for s in result.symbols if s.kind == "for_loop"]
        assert len(for_loops) == 1

        # Check conditional
        conditionals = [s for s in result.symbols if s.kind == "conditional"]
        assert len(conditionals) == 1

        # Check edges
        extends_edges = [e for e in result.edges if e.edge_type == "extends_template"]
        assert len(extends_edges) == 1
        include_edges = [e for e in result.edges if e.edge_type == "includes_template"]
        assert len(include_edges) == 2

    def test_extends_with_single_quotes(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "page.twig", "{% extends 'base.html.twig' %}")
        result = analyze_twig(tmp_path)
        extends = next((s for s in result.symbols if s.kind == "extends"), None)
        assert extends is not None
        assert extends.meta.get("template") == "base.html.twig"
