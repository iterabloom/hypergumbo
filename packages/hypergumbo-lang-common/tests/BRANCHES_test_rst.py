"""Branch coverage tests for rst.py analyzer.

Tests specific branch paths in the RST analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Section extraction
- Directive extraction
- Target extraction
- Reference extraction
- Toctree edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.rst import (
    _make_symbol_id,
    analyze_rst,
    find_rst_files,
)


def make_rst_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an RST file with given content."""
    (tmp_path / name).write_text(content)


class TestRSTHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        from pathlib import Path
        symbol_id = _make_symbol_id(Path("docs/index.rst"), "Introduction", "section")
        assert symbol_id == "rst:docs/index.rst:section:Introduction"


class TestSectionExtraction:
    """Branch coverage for section extraction."""

    def test_simple_section(self, tmp_path: Path) -> None:
        """Test simple section extraction."""
        make_rst_file(tmp_path, "index.rst", """
Introduction
============

This is the introduction.
""")
        result = analyze_rst(tmp_path)
        assert not result.skipped

        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 1
        assert any(s.name == "Introduction" for s in sections)

    def test_nested_sections(self, tmp_path: Path) -> None:
        """Test nested section extraction."""
        make_rst_file(tmp_path, "index.rst", """
Main Section
============

Content here.

Subsection
----------

More content.
""")
        result = analyze_rst(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 2


class TestDirectiveExtraction:
    """Branch coverage for directive extraction."""

    def test_function_directive(self, tmp_path: Path) -> None:
        """Test function directive extraction."""
        make_rst_file(tmp_path, "api.rst", """
API Reference
=============

.. function:: my_function(arg1, arg2)

   Description of the function.
""")
        result = analyze_rst(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) >= 1

    def test_class_directive(self, tmp_path: Path) -> None:
        """Test class directive extraction."""
        make_rst_file(tmp_path, "api.rst", """
Classes
=======

.. class:: MyClass

   Description of the class.
""")
        result = analyze_rst(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) >= 1

    def test_note_directive(self, tmp_path: Path) -> None:
        """Test note (admonition) directive extraction."""
        make_rst_file(tmp_path, "guide.rst", """
Guide
=====

.. note::

   This is an important note.
""")
        result = analyze_rst(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) >= 1
        note = next((d for d in directives if d.meta.get("directive_type") == "note"), None)
        assert note is not None
        assert note.meta.get("is_admonition") is True

    def test_warning_directive(self, tmp_path: Path) -> None:
        """Test warning directive extraction."""
        make_rst_file(tmp_path, "guide.rst", """
Guide
=====

.. warning::

   Be careful here.
""")
        result = analyze_rst(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        assert len(directives) >= 1


class TestToctreeExtraction:
    """Branch coverage for toctree directive extraction."""

    def test_toctree_creates_edges(self, tmp_path: Path) -> None:
        """Test toctree entries create include edges."""
        make_rst_file(tmp_path, "index.rst", """
Welcome
=======

.. toctree::
   :maxdepth: 2

   intro
   getting-started
   api
""")
        result = analyze_rst(tmp_path)
        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        # Should have edges for intro, getting-started, and api
        assert len(include_edges) >= 3


class TestIncludeDirective:
    """Branch coverage for include directive extraction."""

    def test_include_creates_edge(self, tmp_path: Path) -> None:
        """Test include directive creates edge."""
        make_rst_file(tmp_path, "main.rst", """
Main Document
=============

.. include:: snippets/example.rst
""")
        result = analyze_rst(tmp_path)
        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        assert len(include_edges) >= 1


class TestTargetExtraction:
    """Branch coverage for target extraction."""

    def test_simple_target(self, tmp_path: Path) -> None:
        """Test simple target extraction."""
        make_rst_file(tmp_path, "index.rst", """
.. _my-target:

My Section
==========

Content here.
""")
        result = analyze_rst(tmp_path)
        targets = [s for s in result.symbols if s.kind == "target"]
        assert len(targets) >= 1
        assert any(t.name == "my-target" for t in targets)


class TestReferenceExtraction:
    """Branch coverage for reference extraction."""

    def test_ref_reference(self, tmp_path: Path) -> None:
        """Test :ref: reference creates edge."""
        make_rst_file(tmp_path, "guide.rst", """
Guide
=====

See :ref:`my-target` for more info.
""")
        result = analyze_rst(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 1

    def test_doc_reference(self, tmp_path: Path) -> None:
        """Test :doc: reference creates edge."""
        make_rst_file(tmp_path, "guide.rst", """
Guide
=====

See :doc:`api` for API reference.
""")
        result = analyze_rst(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 1


class TestFindRSTFiles:
    """Branch coverage for file discovery."""

    def test_finds_rst_files(self, tmp_path: Path) -> None:
        """Test .rst files are discovered."""
        (tmp_path / "index.rst").write_text("Title\n=====\n")

        files = list(find_rst_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".rst"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.rst").write_text("Title\n=====\n")

        files = list(find_rst_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "index.rst"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_rst_files(self, tmp_path: Path) -> None:
        """Test directory with no RST files."""
        result = analyze_rst(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_section(self, tmp_path: Path) -> None:
        """Test minimal RST document."""
        make_rst_file(tmp_path, "min.rst", """
Title
=====
""")
        result = analyze_rst(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_rst_file(tmp_path, "index.rst", """
Documentation
=============

Welcome to the docs.
""")
        result = analyze_rst(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestDirectiveMetadata:
    """Branch coverage for directive metadata extraction."""

    def test_api_directive_has_is_api_flag(self, tmp_path: Path) -> None:
        """Test API directive has is_api metadata."""
        make_rst_file(tmp_path, "api.rst", """
API
===

.. function:: example()

   An example function.
""")
        result = analyze_rst(tmp_path)
        directives = [s for s in result.symbols if s.kind == "directive"]
        func_directive = next(
            (d for d in directives if d.meta.get("directive_type") == "function"), None
        )
        assert func_directive is not None
        assert func_directive.meta.get("is_api") is True
