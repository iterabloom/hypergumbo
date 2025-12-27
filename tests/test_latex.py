"""Tests for LaTeX analyzer."""
from pathlib import Path

import pytest

from hypergumbo.analyze.latex import analyze_latex


class TestAnalyzeLaTeX:
    """Tests for analyze_latex function."""

    def test_detects_sections(self, tmp_path: Path) -> None:
        """Should detect LaTeX sections."""
        (tmp_path / "paper.tex").write_text(r"""
\documentclass{article}
\begin{document}

\section{Introduction}
Hello world.

\subsection{Background}
More text.

\section{Methods}
Methodology.

\end{document}
""")

        result = analyze_latex(tmp_path)

        assert not result.skipped
        sections = [s for s in result.symbols if s.kind == "section"]
        names = [s.name for s in sections]
        assert "Introduction" in names
        assert "Background" in names
        assert "Methods" in names

    def test_detects_chapters(self, tmp_path: Path) -> None:
        """Should detect LaTeX chapters in books."""
        (tmp_path / "book.tex").write_text(r"""
\documentclass{book}
\begin{document}

\chapter{First Chapter}
Content here.

\chapter{Second Chapter}
More content.

\end{document}
""")

        result = analyze_latex(tmp_path)

        sections = [s for s in result.symbols if s.kind == "section"]
        names = [s.name for s in sections]
        assert "First Chapter" in names
        assert "Second Chapter" in names

    def test_detects_labels(self, tmp_path: Path) -> None:
        """Should detect LaTeX labels."""
        (tmp_path / "doc.tex").write_text(r"""
\documentclass{article}
\begin{document}

\section{Introduction}
\label{sec:intro}

See Section~\ref{sec:intro}.

\begin{equation}
\label{eq:main}
E = mc^2
\end{equation}

\end{document}
""")

        result = analyze_latex(tmp_path)

        labels = [s for s in result.symbols if s.kind == "label"]
        names = [l.name for l in labels]
        assert "sec:intro" in names
        assert "eq:main" in names

    def test_detects_custom_commands(self, tmp_path: Path) -> None:
        """Should detect custom command definitions."""
        (tmp_path / "macros.tex").write_text(r"""
\documentclass{article}

\newcommand{\myname}{Alice}
\newcommand{\foo}[1]{#1}

\begin{document}
Hello, \myname!
\end{document}
""")

        result = analyze_latex(tmp_path)

        commands = [s for s in result.symbols if s.kind == "command"]
        names = [c.name for c in commands]
        assert r"\myname" in names
        assert r"\foo" in names

    def test_detects_custom_environments(self, tmp_path: Path) -> None:
        """Should detect custom environment definitions."""
        (tmp_path / "envs.tex").write_text(r"""
\documentclass{article}

\newenvironment{myblock}{\begin{center}}{\end{center}}
\newenvironment{myquote}{\begin{quotation}}{\end{quotation}}

\begin{document}
\begin{myblock}
Content
\end{myblock}
\end{document}
""")

        result = analyze_latex(tmp_path)

        environments = [s for s in result.symbols if s.kind == "environment"]
        names = [e.name for e in environments]
        assert "myblock" in names
        assert "myquote" in names

    def test_detects_ref_edges(self, tmp_path: Path) -> None:
        """Should detect reference edges."""
        (tmp_path / "refs.tex").write_text(r"""
\documentclass{article}
\begin{document}

\section{Introduction}
\label{sec:intro}

See Section~\ref{sec:intro}.
Also see Equation~\eqref{eq:main}.

\end{document}
""")

        result = analyze_latex(tmp_path)

        ref_edges = [e for e in result.edges if e.edge_type == "references"]
        targets = [e.dst for e in ref_edges]
        assert "sec:intro" in targets

    def test_detects_citation_edges(self, tmp_path: Path) -> None:
        """Should detect citation edges."""
        (tmp_path / "citations.tex").write_text(r"""
\documentclass{article}
\begin{document}

This is shown by Smith~\cite{smith2020}.
Multiple citations~\cite{jones2019,brown2021}.

\end{document}
""")

        result = analyze_latex(tmp_path)

        cite_edges = [e for e in result.edges if e.meta and e.meta.get("ref_type") == "citation"]
        targets = [e.dst for e in cite_edges]
        assert "smith2020" in targets

    def test_detects_include_edges(self, tmp_path: Path) -> None:
        """Should detect include edges."""
        (tmp_path / "main.tex").write_text(r"""
\documentclass{article}
\begin{document}

\input{chapter1}
\include{chapter2}

\end{document}
""")

        result = analyze_latex(tmp_path)

        include_edges = [e for e in result.edges if e.edge_type == "includes"]
        targets = [e.dst for e in include_edges]
        assert "chapter1" in targets or "chapter2" in targets

    def test_detects_package_imports(self, tmp_path: Path) -> None:
        """Should detect package imports."""
        (tmp_path / "imports.tex").write_text(r"""
\documentclass{article}
\usepackage{amsmath}
\usepackage{graphicx}
\usepackage{hyperref}

\begin{document}
Content
\end{document}
""")

        result = analyze_latex(tmp_path)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        packages = [e.dst for e in import_edges]
        assert "amsmath" in packages
        assert "graphicx" in packages
        assert "hyperref" in packages

    def test_handles_sty_files(self, tmp_path: Path) -> None:
        """Should analyze .sty package files."""
        (tmp_path / "mypackage.sty").write_text(r"""
\ProvidesPackage{mypackage}

\newcommand{\mymacro}{content}
\newenvironment{myenv}{}{}
""")

        result = analyze_latex(tmp_path)

        # Should find symbols in .sty file
        symbols = result.symbols
        names = [s.name for s in symbols]
        assert r"\mymacro" in names
        assert "myenv" in names

    def test_handles_cls_files(self, tmp_path: Path) -> None:
        """Should analyze .cls class files."""
        (tmp_path / "myclass.cls").write_text(r"""
\ProvidesClass{myclass}
\LoadClass{article}

\newcommand{\classmacro}{stuff}
""")

        result = analyze_latex(tmp_path)

        # Should find symbols in .cls file
        commands = [s for s in result.symbols if s.kind == "command"]
        names = [c.name for c in commands]
        assert r"\classmacro" in names

    def test_handles_multiple_files(self, tmp_path: Path) -> None:
        """Should analyze multiple LaTeX files."""
        (tmp_path / "main.tex").write_text(r"""
\documentclass{article}
\begin{document}
\section{Main}
\end{document}
""")
        (tmp_path / "appendix.tex").write_text(r"""
\section{Appendix A}
Content
""")

        result = analyze_latex(tmp_path)

        sections = [s for s in result.symbols if s.kind == "section"]
        names = [s.name for s in sections]
        assert "Main" in names
        assert "Appendix A" in names

    def test_empty_repo_returns_empty_result(self, tmp_path: Path) -> None:
        """Should return empty result for repo with no LaTeX files."""
        (tmp_path / "readme.txt").write_text("Not LaTeX")

        result = analyze_latex(tmp_path)

        assert result.symbols == []
        assert result.edges == []


class TestAnalyzeLaTeXFallback:
    """Tests for fallback when LaTeX tree-sitter is not available."""

    def test_returns_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Should return skipped result when tree-sitter not available."""
        (tmp_path / "test.tex").write_text(r"\documentclass{article}")

        # Temporarily make it unavailable by mocking
        import hypergumbo.analyze.latex as latex_mod

        original_func = latex_mod.is_latex_tree_sitter_available

        def mock_unavailable():
            return False

        latex_mod.is_latex_tree_sitter_available = mock_unavailable

        try:
            with pytest.warns(UserWarning, match="tree-sitter-language-pack"):
                result = analyze_latex(tmp_path)
            assert result.skipped
            assert "not available" in result.skipped_reason
        finally:
            latex_mod.is_latex_tree_sitter_available = original_func
