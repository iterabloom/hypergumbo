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


class TestGnuplotFunctionIdentity:
    """WI-tusad: functions mint the ADR-0035 producer stable_id themselves.

    ``f(x)`` and ``f(x, y)`` in one script used to share the backstop floor
    key and both be left null. Each now hashes its dummy-variable count, so
    the pair is told apart and deleting the first leaves the second's id.
    """

    _TEXT = "f(x) = x\nf(x, y) = x + y\ng() = 1\n"

    @staticmethod
    def _functions(root: Path, text: str) -> dict[int, "object"]:
        root.mkdir(parents=True, exist_ok=True)
        (root / "sub").mkdir(exist_ok=True)
        (root / "sub" / "plot.gp").write_text(text)
        return {
            s.span.start_line: s
            for s in analyze_gnuplot(root).symbols if s.kind == "function"
        }

    def test_id_is_the_producer_formula_over_the_relative_path(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_core.overload_arity import (
            PARAMETERS_KEY, declared_arity, declared_arity_stable_id,
            parameter_entry,
        )
        fns = self._functions(tmp_path, self._TEXT)
        two = [parameter_entry("x"), parameter_entry("y")]
        assert fns[2].meta == {PARAMETERS_KEY: two}
        assert fns[3].meta == {PARAMETERS_KEY: []}
        assert fns[2].stable_id == declared_arity_stable_id(
            kind="function", lang="gnuplot", path="sub/plot.gp", name="f",
            qualified_name="f", arity=declared_arity({PARAMETERS_KEY: two}),
        )
        ids = [fns[line].stable_id for line in (1, 2, 3)]
        assert None not in ids and len(set(ids)) == 3

    def test_deleting_an_earlier_same_name_function_moves_no_id(
        self, tmp_path: Path,
    ) -> None:
        before = self._functions(tmp_path / "r1", self._TEXT)
        after = self._functions(
            tmp_path / "r2", self._TEXT.replace("f(x) = x\n", "\n"),
        )
        assert set(after) == {2, 3}
        for line, sym in after.items():
            assert sym.stable_id == before[line].stable_id is not None
