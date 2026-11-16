# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the reStructuredText analyzer."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_lang_common import rst as rst_module
from hypergumbo_lang_common.rst import analyze_rst, find_rst_files, is_rst_tree_sitter_available

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
        with patch.object(rst_module._analyzer, "_check_grammar_available", return_value=False):
            assert rst_module.is_rst_tree_sitter_available() is False

class TestAnalyzeRST:
    """Tests for analyze_rst function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", "Title\n=====\n")
        with patch.object(rst_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="rst analysis skipped"):
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
        # No such documents here: the stand-in for each name as written.
        assert dsts == {
            "rst:intro:0-0:module:module",
            "rst:chapter1:0-0:module:module",
            "rst:chapter2:0-0:module:module",
        }

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
        # No such document here: the stand-in for the name as written.
        assert ref_edges[0].dst == "rst:other-page:0-0:module:module"

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
        assert section.origin == ["rst"]

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_rst_file(tmp_path, "test.rst", "Title\n=====\n")
        result = analyze_rst(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "rst"
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
        # INV-dulah: node.id and stable_id are minted together by
        # make_doc_symbol_ids. node.id is the canonical ADR-0036
        # "{lang}:{path}:{start}-{end}:{name}:{kind}"; the span slot is what
        # keeps same-name sections from colliding on id (rst was line-less
        # before that segment was added). It was previously kind-third/name-last,
        # which put the kind word where the span belongs.
        assert re.match(r"^rst:test\.rst:\d+-\d+:section_\d+:section$", section.id), section.id
        assert section.stable_id != section.id
        assert re.compile(r"^hgstableid15:[0-9a-f]{16}$").match(section.stable_id)

    def test_all_symbols_have_canonical_stable_id(self, tmp_path: Path) -> None:
        """Every emitted symbol carries a canonical sha256 stable_id."""
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
        assert len(result.symbols) >= 1
        pattern = re.compile(r"^hgstableid15:[0-9a-f]{16}$")
        for symbol in result.symbols:
            assert pattern.match(symbol.stable_id), (
                f"{symbol.kind} {symbol.name!r} has non-canonical "
                f"stable_id {symbol.stable_id!r}"
            )

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


class TestEdgeEndpoints:
    """Every end of an rst edge is a node's id or a stand-in in the id grammar.

    The dsts were ``rst:file:<path>``, ``rst:doc:<entry>`` and
    ``rst:<role>:<target>`` and a reference's src was ``rst:<path>``: no ids,
    so finalize folded every include of a repository into one ``<unknown>``
    node per spelling.
    """

    def _edges(self, repo: Path, edge_type: str) -> list[tuple[str, str, bool]]:
        from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints

        result = analyze_rst(repo)
        assert non_id_edge_endpoints(result.symbols, result.edges) == []
        ids = {s.id: s for s in result.symbols}
        return [
            (ids[e.src].kind if e.src in ids else e.src, e.dst, e.is_resolved)
            for e in result.edges if e.edge_type == edge_type
        ]

    def test_include_of_a_file_of_the_repository_is_its_file_node(self, tmp_path: Path) -> None:
        from hypergumbo_core.analyze.base import make_file_id

        make_rst_file(tmp_path, "docs/guide/index.rst", (
            ".. include:: part.rst\n\n"
            ".. include:: ../../README.md\n\n"
            ".. include:: /shared/note.rst\n\n"
            ".. include:: /nowhere/gone.rst\n\n"
            ".. include:: snippet.inc\n"
        ))
        make_rst_file(tmp_path, "docs/guide/part.rst", "Part\n====\n")
        make_rst_file(tmp_path, "docs/shared/note.rst", "Note\n====\n")
        make_rst_file(tmp_path, "docs/guide/snippet.inc", "text\n")
        (tmp_path / "README.md").write_text("# readme\n")

        assert self._edges(tmp_path, "includes") == [
            ("directive", make_file_id("rst", "docs/guide/part.rst"), True),
            # Another language's file: that analyzer's node, not an rst twin.
            ("directive", make_file_id("markdown", "README.md"), True),
            # ``/x``: under a directory above the file (Sphinx's source dir).
            ("directive", make_file_id("rst", "docs/shared/note.rst"), True),
            ("directive", "rst:/nowhere/gone.rst:0-0:module:module", False),
            # On disk, but no analyzer gives a ``.inc`` file a node.
            ("directive", "rst:snippet.inc:0-0:module:module", False),
        ]

    def test_toctree_entry_is_one_line_and_reaches_its_document(self, tmp_path: Path) -> None:
        from hypergumbo_core.analyze.base import make_file_id

        make_rst_file(tmp_path, "docs/index.rst", (
            ".. toctree::\n"
            "   :maxdepth: 2\n"
            "\n"
            "   intro\n"
            "\n"
            "   api/index\n"
            "   Notes <api/notes>\n"
            "   https://example.com/x\n"
            "   self\n"
            "   missing/page\n"
        ))
        make_rst_file(tmp_path, "docs/intro.rst", "Intro\n=====\n")
        make_rst_file(tmp_path, "docs/api/index.rst", "API\n===\n")
        (tmp_path / "docs" / "api" / "notes.md").write_text("# notes\n")

        assert self._edges(tmp_path, "includes") == [
            ("directive", make_file_id("rst", "docs/intro.rst"), True),
            # ``api/index`` is ONE entry, not ``api``, ``/`` and ``index``.
            ("directive", make_file_id("rst", "docs/api/index.rst"), True),
            ("directive", make_file_id("markdown", "docs/api/notes.md"), True),
            ("directive", "rst:https://example.com/x:0-0:module:module", False),
            ("directive", make_file_id("rst", "docs/index.rst"), True),
            ("directive", "rst:missing/page:0-0:module:module", False),
        ]

    def test_references_start_at_the_file_and_reach_what_they_name(self, tmp_path: Path) -> None:
        from hypergumbo_core.analyze.base import make_file_id

        make_rst_file(tmp_path, "docs/a.rst", (
            "See :ref:`Some-Label`, :ref:`shown <some-label>`, :ref:`gone`,\n"
            ":ref:`twice`, :doc:`b`, :doc:`nowhere`, :func:`~pkg.mod.fn`\n"
            "and :ref:`<>`.\n"
        ))
        make_rst_file(tmp_path, "docs/b.rst", (
            ".. _some-label:\n\nB\n=\n\n.. _twice:\n\nText.\n"
        ))
        make_rst_file(tmp_path, "docs/c.rst", ".. _twice:\n\nC\n=\n")

        result = analyze_rst(tmp_path)
        label = next(s for s in result.symbols if s.name == "some-label")
        here = make_file_id("rst", "docs/a.rst")
        b = make_file_id("rst", "docs/b.rst")
        edges = sorted(self._edges(tmp_path, "references"))
        assert edges == sorted([
            (here, b, True),
            (here, "rst:nowhere:0-0:module:module", False),
            # An object of another domain keeps its role as the namespace.
            (here, "rst:func:0-0:pkg.mod.fn:unresolved", False),
            # A label is global and compared without case; ``Title <label>``.
            (here, label.id, True),
            (here, label.id, True),
            (here, "rst:ref:0-0:gone:unresolved", False),
            # Two targets share the label: not guessed between.
            (here, "rst:ref:0-0:twice:unresolved", False),
        ])
