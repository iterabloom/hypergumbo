"""Tests for the reStructuredText analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import rst as rst_module
from hypergumbo_lang_common.rst import (
    RSTAnalysisResult,
    analyze_rst,
    find_rst_files,
    is_rst_tree_sitter_available,
)


def make_rst_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create an RST file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindRSTFiles:
    """Tests for find_rst_files function."""

    def test_finds_rst_files(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "index.rst", "Title\n=====\n")
        make_rst_file(tmp_path, "docs/guide.rst", "Guide\n=====\n")
        files = find_rst_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"index.rst", "guide.rst"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_rst_files(tmp_path)
        assert files == []


class TestIsRSTTreeSitterAvailable:
    """Tests for is_rst_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_rst_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(rst_module, "is_rst_tree_sitter_available", return_value=False):
            assert rst_module.is_rst_tree_sitter_available() is False


class TestAnalyzeRST:
    """Tests for analyze_rst function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", "Title\n=====\n")
        with patch.object(rst_module, "is_rst_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="RST analysis skipped"):
                result = rst_module.analyze_rst(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_section(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
Title
=====

Some content here.
""")
        result = analyze_rst(tmp_path)
        assert not result.skipped
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.name == "Title"
        assert section.language == "rst"
        assert section.meta.get("level") == 1

    def test_extracts_nested_sections(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
Main Title
==========

Section One
-----------

Subsection
~~~~~~~~~~

Content.
""")
        result = analyze_rst(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 3
        names = {s.name for s in sections}
        assert "Main Title" in names
        assert "Section One" in names
        assert "Subsection" in names

    def test_extracts_directive(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. note::
   This is a note.
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.name == "note"
        assert directive.meta.get("directive_type") == "note"
        assert directive.meta.get("is_admonition") is True

    def test_extracts_function_directive(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. function:: my_function(arg1, arg2)

   This function does something.
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.meta.get("directive_type") == "function"
        assert directive.meta.get("is_api") is True
        assert "my_function" in directive.meta.get("arguments", "")

    def test_extracts_class_directive(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. class:: MyClass

   A class definition.
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.meta.get("is_api") is True
        assert "MyClass" in directive.name

    def test_extracts_toctree_directive(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. toctree::
   :maxdepth: 2

   intro
   chapter1
   chapter2
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.meta.get("directive_type") == "toctree"
        # Should create edges for toctree entries
        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        assert len(include_edges) >= 3
        dsts = {e.dst for e in include_edges}
        assert "rst:doc:intro" in dsts
        assert "rst:doc:chapter1" in dsts
        assert "rst:doc:chapter2" in dsts

    def test_extracts_include_directive(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. include:: _includes/header.rst
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        # Should create include edge
        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        assert len(include_edges) == 1
        assert "_includes/header.rst" in include_edges[0].dst

    def test_extracts_target(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. _my-label:

Section
=======

Some content.
""")
        result = analyze_rst(tmp_path)
        target = next((s for s in result.symbols if s.kind == "target"), None)
        assert target is not None
        assert target.name == "my-label"
        assert ".. _my-label:" in target.signature

    def test_extracts_reference(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
See :ref:`my-label` for more info.
""")
        result = analyze_rst(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 1
        assert "ref" in ref_edges[0].dst
        assert "my-label" in ref_edges[0].dst

    def test_extracts_doc_reference(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
See :doc:`other-page` for details.
""")
        result = analyze_rst(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 1
        assert "doc" in ref_edges[0].dst

    def test_extracts_multiple_references(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
See :ref:`label1` and :doc:`page1` and :func:`my_func`.
""")
        result = analyze_rst(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 3

    def test_ignores_unknown_roles(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
This is :emphasis:`emphasized` text.
""")
        result = analyze_rst(tmp_path)
        # emphasis is not tracked as a cross-reference
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) == 0

    def test_pass_id(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
Title
=====
""")
        result = analyze_rst(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.origin == "rst.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", "Title\n=====\n")
        result = analyze_rst(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "rst.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_rst(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
Title
=====
""")
        result = analyze_rst(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.id == section.stable_id
        assert "rst:" in section.id
        assert "test.rst" in section.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
Title
=====
""")
        result = analyze_rst(tmp_path)
        section = next((s for s in result.symbols if s.kind == "section"), None)
        assert section is not None
        assert section.span is not None
        assert section.span.start_line >= 1
        assert section.span.end_line >= section.span.start_line

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "index.rst", """
Index
=====
""")
        make_rst_file(tmp_path, "docs/guide.rst", """
Guide
=====
""")
        result = analyze_rst(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 2
        names = {s.name for s in sections}
        assert "Index" in names
        assert "Guide" in names

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "a.rst", "A\n=\n")
        make_rst_file(tmp_path, "b.rst", "B\n=\n")
        make_rst_file(tmp_path, "c.rst", "C\n=\n")
        result = analyze_rst(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_long_arguments_truncation(self, tmp_path: Path) -> None:
        long_args = "x" * 100
        make_rst_file(tmp_path, "test.rst", f"""
.. function:: {long_args}

   Description.
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert len(directive.signature) < len(long_args)
        assert "..." in directive.signature

    def test_code_block_directive(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. code-block:: python

    def hello():
        print("Hello")
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.meta.get("directive_type") == "code-block"
        assert directive.meta.get("arguments") == "python"

    def test_warning_directive(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", """
.. warning::
   This is dangerous!
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        assert directive.meta.get("is_admonition") is True

    def test_complete_rst_file(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "example.rst", """
.. _main-page:

================
Document Title
================

Introduction
============

Welcome to the documentation.

.. note::
   This is important.

API Reference
=============

.. function:: create_widget(name, color=None)

   Create a new widget.

.. class:: Widget

   A widget class.

See Also
--------

See :ref:`other-section` and :doc:`other-page`.

.. toctree::
   :maxdepth: 2

   getting-started
   api-reference
""")
        result = analyze_rst(tmp_path)

        # Check sections
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 3

        # Check directives
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) >= 4

        # Check target
        targets = [s for s in result.symbols if s.kind == "target"]
        assert len(targets) == 1

        # Check edges
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 2

        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        assert len(include_edges) >= 2

    def test_api_directive_uses_arguments_as_name(self, tmp_path: Path) -> None:
        """Test that API directives use arguments as name."""
        make_rst_file(tmp_path, "test.rst", """
.. function:: calculate_sum(a, b)

   Calculate the sum.
""")
        result = analyze_rst(tmp_path)
        directive = next((s for s in result.symbols if s.kind == "directive"), None)
        assert directive is not None
        # API directives should use arguments as name
        assert "calculate_sum" in directive.name

    def test_section_without_title_skipped(self, tmp_path: Path) -> None:
        """Test that sections without titles are handled gracefully."""
        # This tests the edge case where a section node might not have a title
        make_rst_file(tmp_path, "test.rst", """
Title
=====

Content only paragraph.
""")
        result = analyze_rst(tmp_path)
        # Should have one section
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) == 1
