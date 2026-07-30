# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Twig template analyzer."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_extended1 import twig as twig_module
from hypergumbo_lang_extended1.twig import (
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
        with patch.object(twig_module._analyzer, "_check_grammar_available", return_value=False):
            assert twig_module.is_twig_tree_sitter_available() is False


class TestAnalyzeTwig:
    """Tests for analyze_twig function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "Hello")
        with patch.object(twig_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="twig analysis skipped"):
                result = twig_module.analyze_twig(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_twig(tmp_path)
        assert result.symbols == []
        assert result.run is not None
        assert result.run.files_analyzed == 0

    def test_extracts_extends(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "page.twig", '{% extends "base.html.twig" %}')
        result = analyze_twig(tmp_path)
        assert not result.skipped
        # Cluster E sub-case (b) per audit-findings 0010: extends Symbol was
        # dropped; the extends_template Edge carries the relationship.
        edge = next((e for e in result.edges if e.edge_type == "extends" and (e.meta or {}).get("ref_construct") == "template"), None)
        assert edge is not None
        assert (edge.meta or {}).get("template") == "base.html.twig"

    def test_extends_creates_edge(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "page.twig", '{% extends "base.html.twig" %}')
        result = analyze_twig(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "extends" and (e.meta or {}).get("ref_construct") == "template"), None)
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
        # Cluster E sub-case (b) per audit-findings 0010: include Symbol was
        # dropped; the includes_template Edge carries the relationship.
        edge = next((e for e in result.edges if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "template"), None)
        assert edge is not None
        assert (edge.meta or {}).get("template") == "partials/header.twig"

    def test_include_creates_edge(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", '{% include "partials/header.twig" %}')
        result = analyze_twig(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "template"), None)
        assert edge is not None

    def test_extracts_include_function(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{{ include('partials/header.twig') }}")
        result = analyze_twig(tmp_path)
        # Cluster E sub-case (b) per audit-findings 0010: include Symbol was
        # dropped; the includes_template Edge (form='function', origin="test", origin_run_id="test") carries it.
        edge = next(
            (e for e in result.edges if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "template"
             and (e.meta or {}).get("form") == "function"),
            None,
        )
        assert edge is not None
        assert "partials/header.twig" in edge.dst

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
        func = next((s for s in result.symbols if s.kind == "call_site"), None)
        assert func is not None
        assert func.name == "date"
        assert func.meta.get("arg_count") == 1
        assert func.meta.get("call_kind") == "function"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{% block content %}{% endblock %}")
        result = analyze_twig(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "twig"
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
        assert block.origin == ["twig"]

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "template.twig", "{% block content %}{% endblock %}")
        result = analyze_twig(tmp_path)
        block = next((s for s in result.symbols if s.kind == "block"), None)
        assert block is not None
        # INV-dulah: node.id and stable_id are minted together by
        # make_doc_symbol_ids; node.id is the canonical ADR-0036
        # "{lang}:{path}:{start}-{end}:{name}:{kind}" (was the doc-family
        # kind-third/name-last order, which put the kind word in the span slot).
        # Parsed RIGHT-anchored, the way the canonical parser does
        # (span, name, kind = parts[-3:]), so a colon in the path cannot shift it.
        _head, _span, _name, _kind = block.id.rsplit(":", 3)
        assert _head.startswith("twig:"), block.id
        assert re.match(r"^\d+-\d+$", _span), block.id
        assert _kind == block.kind, block.id
        assert block.stable_id.startswith("sha256:")

    def test_all_symbols_have_canonical_stable_id(self, tmp_path: Path) -> None:
        """Every emitted symbol's stable_id is the canonical sha256 form.

        WI-rijup: stable_id must be ``sha256:<16hex>`` rather than the raw
        composite ``Symbol.id``. Reuses the complete-template fixture so the
        assertion runs over blocks, for_loops, conditionals, and call_sites.
        """
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

  {{ date('now') }}
{% endblock %}""")
        result = analyze_twig(tmp_path)
        assert len(result.symbols) >= 1
        pattern = re.compile(r"^sha256:[0-9a-f]{16}$")
        for sym in result.symbols:
            assert pattern.match(sym.stable_id), (
                f"non-canonical stable_id: {sym.stable_id!r} (kind={sym.kind})"
            )

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

        # Check extends + includes — Cluster E sub-case (b) per
        # audit-findings 0010: Symbols dropped; Edges carry the relations.
        extends_edge_check = next(
            (e for e in result.edges if e.edge_type == "extends" and (e.meta or {}).get("ref_construct") == "template"
             and (e.meta or {}).get("template") == "base.html.twig"),
            None,
        )
        assert extends_edge_check is not None

        # Check blocks
        blocks = [s for s in result.symbols if s.kind == "block"]
        assert len(blocks) == 2
        block_names = {b.name for b in blocks}
        assert block_names == {"title", "content"}

        # Check for loop
        for_loops = [s for s in result.symbols if s.kind == "for_loop"]
        assert len(for_loops) == 1

        # Check conditional
        conditionals = [s for s in result.symbols if s.kind == "conditional"]
        assert len(conditionals) == 1

        # Check edges
        extends_edges = [e for e in result.edges if e.edge_type == "extends" and (e.meta or {}).get("ref_construct") == "template"]
        assert len(extends_edges) == 1
        include_edges = [e for e in result.edges if e.edge_type == "includes" and (e.meta or {}).get("ref_construct") == "template"]
        assert len(include_edges) == 2

    def test_extends_with_single_quotes(self, tmp_path: Path) -> None:
        make_twig_file(tmp_path, "page.twig", "{% extends 'base.html.twig' %}")
        result = analyze_twig(tmp_path)
        # Cluster E sub-case (b) per audit-findings 0010: extends Symbol was
        # dropped; the extends_template Edge carries the relationship.
        edge = next((e for e in result.edges if e.edge_type == "extends" and (e.meta or {}).get("ref_construct") == "template"), None)
        assert edge is not None
        assert (edge.meta or {}).get("template") == "base.html.twig"
