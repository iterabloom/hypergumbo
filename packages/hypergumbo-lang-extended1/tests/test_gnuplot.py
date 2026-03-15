# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Gnuplot script analyzer."""
from pathlib import Path

from hypergumbo_lang_extended1.gnuplot import analyze_gnuplot


class TestAnalyzeGnuplot:
    """Tests for Gnuplot script analysis."""

    def test_detects_function(self, tmp_path: Path) -> None:
        (tmp_path / "plot.gp").write_text("f(x) = x**2\n")
        result = analyze_gnuplot(tmp_path)
        names = [s.name for s in result.symbols]
        assert "f" in names
        assert any(s.kind == "function" for s in result.symbols)

    def test_detects_variable(self, tmp_path: Path) -> None:
        (tmp_path / "config.gnuplot").write_text("a = 3.14\nb = 2.71\n")
        result = analyze_gnuplot(tmp_path)
        names = [s.name for s in result.symbols]
        assert "a" in names
        assert "b" in names
        assert all(s.kind == "variable" for s in result.symbols)

    def test_detects_plot_command(self, tmp_path: Path) -> None:
        (tmp_path / "viz.plt").write_text("plot sin(x)\n")
        result = analyze_gnuplot(tmp_path)
        names = [s.name for s in result.symbols]
        assert "plot" in names
        assert any(s.kind == "plot" for s in result.symbols)

    def test_detects_splot(self, tmp_path: Path) -> None:
        (tmp_path / "3d.gp").write_text("splot x*y\n")
        result = analyze_gnuplot(tmp_path)
        assert any(s.name == "splot" and s.kind == "plot" for s in result.symbols)

    def test_skips_comments(self, tmp_path: Path) -> None:
        (tmp_path / "test.gp").write_text("# comment\na = 1\n")
        result = analyze_gnuplot(tmp_path)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "a"

    def test_skips_set_commands(self, tmp_path: Path) -> None:
        (tmp_path / "test.gp").write_text("set = 5\n")
        result = analyze_gnuplot(tmp_path)
        assert len(result.symbols) == 0

    def test_symbols_have_gnuplot_language(self, tmp_path: Path) -> None:
        (tmp_path / "test.gp").write_text("f(x) = x\n")
        result = analyze_gnuplot(tmp_path)
        assert all(s.language == "gnuplot" for s in result.symbols)

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = analyze_gnuplot(tmp_path)
        assert result.symbols == []

    def test_function_and_variable_same_file(self, tmp_path: Path) -> None:
        (tmp_path / "mix.gp").write_text("a = 2\nf(x) = a * x\nplot f(x)\n")
        result = analyze_gnuplot(tmp_path)
        kinds = {s.kind for s in result.symbols}
        assert "function" in kinds
        assert "variable" in kinds
        assert "plot" in kinds
