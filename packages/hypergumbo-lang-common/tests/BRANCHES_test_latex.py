# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for latex.py analyzer.

Tests specific branch paths in the LaTeX analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Section extraction (chapter, section, subsection)
- Label definition extraction
- Custom command definition extraction
- Custom environment definition extraction
- Reference edge extraction
- Citation edge extraction
- Include and package edge extraction
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id
from hypergumbo_lang_common.latex import analyze_latex

def make_latex_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a LaTeX file with given content."""
    (tmp_path / name).write_text(content)

class TestLatexHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("latex", "doc/main.tex", 10, 15, "Introduction", "section")
        assert symbol_id == "latex:doc/main.tex:10-15:Introduction:section"

class TestSectionExtraction:
    """Branch coverage for section extraction."""

    def test_section_extraction(self, tmp_path: Path) -> None:
        """Test section extraction."""
        make_latex_file(tmp_path, "main.tex", r"""
\documentclass{article}
\begin{document}
\section{Introduction}
Hello world.
\end{document}
""")
        result = analyze_latex(tmp_path)
        assert not result.skipped

        sections = [s for s in result.symbols if s.kind == "section"]
        assert len(sections) >= 1
        assert any(s.name == "Introduction" for s in sections)

    def test_multiple_sections(self, tmp_path: Path) -> None:
        """Test multiple section levels."""
        make_latex_file(tmp_path, "doc.tex", r"""
\documentclass{article}
\begin{document}
\section{First}
\subsection{SubFirst}
\subsubsection{SubSubFirst}
\section{Second}
\end{document}
""")
        result = analyze_latex(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        names = [s.name for s in sections]
        assert "First" in names
        assert "SubFirst" in names
        assert "Second" in names

class TestLabelExtraction:
    """Branch coverage for label definition extraction."""

    def test_label_definition(self, tmp_path: Path) -> None:
        """Test label definition extraction."""
        make_latex_file(tmp_path, "labeled.tex", r"""
\documentclass{article}
\begin{document}
\section{Introduction}
\label{sec:intro}
Content here.
\end{document}
""")
        result = analyze_latex(tmp_path)
        labels = [s for s in result.symbols if s.kind == "label"]
        assert len(labels) >= 1
        assert any(l.name == "sec:intro" for l in labels)

    def test_multiple_labels(self, tmp_path: Path) -> None:
        """Test multiple labels."""
        make_latex_file(tmp_path, "labels.tex", r"""
\documentclass{article}
\begin{document}
\section{Intro}\label{sec:intro}
\section{Method}\label{sec:method}
\section{Results}\label{sec:results}
\end{document}
""")
        result = analyze_latex(tmp_path)
        labels = [s for s in result.symbols if s.kind == "label"]
        names = [l.name for l in labels]
        assert "sec:intro" in names
        assert "sec:method" in names
        assert "sec:results" in names

class TestCommandExtraction:
    """Branch coverage for custom command extraction."""

    def test_newcommand_extraction(self, tmp_path: Path) -> None:
        """Test newcommand definition extraction."""
        make_latex_file(tmp_path, "macros.tex", r"""
\newcommand{\mymath}[1]{\ensuremath{#1}}
\newcommand{\todo}[1]{\textbf{TODO: #1}}
""")
        result = analyze_latex(tmp_path)
        commands = [s for s in result.symbols if s.kind == "command"]
        names = [c.name for c in commands]
        # Command names include backslash
        assert any("mymath" in n for n in names)
        assert any("todo" in n for n in names)

class TestEnvironmentExtraction:
    """Branch coverage for custom environment extraction."""

    def test_newenvironment_extraction(self, tmp_path: Path) -> None:
        """Test newenvironment definition extraction."""
        make_latex_file(tmp_path, "envs.tex", r"""
\newenvironment{mybox}{\begin{center}\begin{tabular}{|c|}}{\end{tabular}\end{center}}
""")
        result = analyze_latex(tmp_path)
        envs = [s for s in result.symbols if s.kind == "environment"]
        assert len(envs) >= 1
        assert any(e.name == "mybox" for e in envs)

class TestReferenceEdges:
    """Branch coverage for reference edge extraction."""

    def test_ref_creates_edge(self, tmp_path: Path) -> None:
        """Test \\ref creates reference edge."""
        make_latex_file(tmp_path, "refs.tex", r"""
\documentclass{article}
\begin{document}
\section{Intro}\label{sec:intro}
See Section~\ref{sec:intro}.
\end{document}
""")
        result = analyze_latex(tmp_path)
        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        assert len(ref_edges) >= 1

class TestCitationEdges:
    """Branch coverage for citation edge extraction."""

    def test_cite_creates_edge(self, tmp_path: Path) -> None:
        """Test \\cite creates reference edge."""
        make_latex_file(tmp_path, "cite.tex", r"""
\documentclass{article}
\begin{document}
As shown in \cite{smith2020}.
\end{document}
""")
        result = analyze_latex(tmp_path)
        cite_edges = [e for e in result.edges if e.edge_type == "references" and e.meta and e.meta.get("ref_type") == "citation"]
        assert len(cite_edges) >= 1

class TestIncludeEdges:
    """Branch coverage for include edge extraction."""

    def test_input_creates_edge(self, tmp_path: Path) -> None:
        """Test \\input creates includes edge."""
        make_latex_file(tmp_path, "main.tex", r"""
\documentclass{article}
\begin{document}
\input{chapter1}
\end{document}
""")
        result = analyze_latex(tmp_path)
        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        assert len(include_edges) >= 1

class TestPackageEdges:
    """Branch coverage for package import edge extraction."""

    def test_usepackage_creates_edge(self, tmp_path: Path) -> None:
        """Test \\usepackage creates imports edge."""
        make_latex_file(tmp_path, "main.tex", r"""
\documentclass{article}
\usepackage{amsmath}
\usepackage{graphicx}
\begin{document}
Content.
\end{document}
""")
        result = analyze_latex(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        pkg_names = [e.dst for e in import_edges]
        assert "amsmath" in pkg_names
        assert "graphicx" in pkg_names

class TestFileDiscovery:
    """Branch coverage for file discovery."""

    def test_finds_tex_files(self, tmp_path: Path) -> None:
        """Test .tex files are analyzed."""
        make_latex_file(tmp_path, "doc.tex", r"\documentclass{article}\begin{document}Hi\end{document}")
        result = analyze_latex(tmp_path)
        assert not result.skipped
        assert result.run is not None
        assert result.run.files_analyzed >= 1

    def test_finds_sty_files(self, tmp_path: Path) -> None:
        """Test .sty style files are analyzed."""
        (tmp_path / "mystyle.sty").write_text(r"\ProvidesPackage{mystyle}")
        result = analyze_latex(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        chapters = tmp_path / "chapters"
        chapters.mkdir()
        make_latex_file(chapters, "intro.tex", r"\section{Intro}")
        result = analyze_latex(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1

class TestEdgeCases:
    """Branch coverage for edge cases and malformed input."""

    def test_section_with_empty_braces(self, tmp_path: Path) -> None:
        """Test section with empty braces - exercises 132->129 branch.

        When _get_curly_group_text returns empty string, we skip symbol creation.
        """
        make_latex_file(tmp_path, "empty_section.tex", r"""
\documentclass{article}
\begin{document}
\section{}
Regular section follows:
\section{Real Section}
\end{document}
""")
        result = analyze_latex(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        # Should have "Real Section" but not empty one
        assert any(s.name == "Real Section" for s in sections)

    def test_chapter_various_depths(self, tmp_path: Path) -> None:
        """Test all section depth types."""
        make_latex_file(tmp_path, "depths.tex", r"""
\documentclass{book}
\begin{document}
\chapter{Chapter One}
\section{Section One}
\subsection{Subsection One}
\subsubsection{Subsubsection One}
\paragraph{Paragraph One}
\subparagraph{Subparagraph One}
\end{document}
""")
        result = analyze_latex(tmp_path)
        sections = [s for s in result.symbols if s.kind == "section"]
        names = [s.name for s in sections]
        assert "Chapter One" in names
        assert "Paragraph One" in names
        assert "Subparagraph One" in names

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test empty LaTeX file."""
        make_latex_file(tmp_path, "empty.tex", "")
        result = analyze_latex(tmp_path)
        assert not result.skipped

    def test_minimal_document(self, tmp_path: Path) -> None:
        """Test minimal LaTeX document."""
        make_latex_file(tmp_path, "min.tex", r"""
\documentclass{article}
\begin{document}
\end{document}
""")
        result = analyze_latex(tmp_path)
        assert not result.skipped

    def test_no_latex_files(self, tmp_path: Path) -> None:
        """Test directory with no LaTeX files."""
        result = analyze_latex(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0
