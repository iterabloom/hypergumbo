# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Jupyter notebook (.ipynb) analyzer.

Verifies that hypergumbo can parse Jupyter notebooks by extracting code cells,
stripping magics, and analyzing them with the Python AST analyzer.
"""
import json
from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.jupyter import (
    analyze_jupyter,
    extract_code_cells,
    preprocess_notebook_source,
)


def _make_notebook(tmp_path: Path, name: str, cells: list[dict], **kwargs) -> Path:
    """Create a minimal .ipynb file with given cells."""
    kernel_lang = kwargs.get("kernel_language", "python")
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": kernel_lang,
                "name": "python3",
            },
            "language_info": {
                "name": kernel_lang,
            },
        },
        "cells": cells,
    }
    nb_path = tmp_path / name
    nb_path.write_text(json.dumps(notebook))
    return nb_path


def _code_cell(source: str | list[str]) -> dict:
    """Create a code cell dict."""
    if isinstance(source, str):
        source = source.splitlines(keepends=True)
    return {
        "cell_type": "code",
        "source": source,
        "metadata": {},
        "outputs": [],
        "execution_count": None,
    }


def _markdown_cell(source: str) -> dict:
    """Create a markdown cell dict."""
    return {
        "cell_type": "markdown",
        "source": source.splitlines(keepends=True),
        "metadata": {},
    }


class TestExtractCodeCells:
    """Tests for extracting code cells from notebook JSON."""

    def test_extracts_code_cells_only(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "test.ipynb", [
            _markdown_cell("# Title"),
            _code_cell("x = 1"),
            _markdown_cell("Some text"),
            _code_cell("y = 2"),
        ])
        cells = extract_code_cells(nb)
        assert len(cells) == 2
        assert cells[0] == "x = 1"
        assert cells[1] == "y = 2"

    def test_handles_multiline_source_list(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "test.ipynb", [
            _code_cell(["def foo():\n", "    return 42\n"]),
        ])
        cells = extract_code_cells(nb)
        assert len(cells) == 1
        assert "def foo():" in cells[0]
        assert "return 42" in cells[0]

    def test_empty_notebook(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "test.ipynb", [])
        cells = extract_code_cells(nb)
        assert cells == []

    def test_only_markdown_cells(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "test.ipynb", [
            _markdown_cell("# Just markdown"),
        ])
        cells = extract_code_cells(nb)
        assert cells == []

    def test_skips_non_python_kernel(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "r_notebook.ipynb", [
            _code_cell("x <- 1"),
        ], kernel_language="R")
        cells = extract_code_cells(nb)
        assert cells == []

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        nb_path = tmp_path / "bad.ipynb"
        nb_path.write_text("not json {{{")
        cells = extract_code_cells(nb_path)
        assert cells == []

    def test_missing_cells_key_returns_empty(self, tmp_path: Path) -> None:
        nb_path = tmp_path / "nocells.ipynb"
        nb_path.write_text(json.dumps({"nbformat": 4, "metadata": {}}))
        cells = extract_code_cells(nb_path)
        assert cells == []

    def test_nbformat_3_skipped(self, tmp_path: Path) -> None:
        nb_path = tmp_path / "old.ipynb"
        nb_path.write_text(json.dumps({
            "nbformat": 3,
            "metadata": {},
            "worksheets": [{"cells": []}],
        }))
        cells = extract_code_cells(nb_path)
        assert cells == []


class TestPreprocessNotebookSource:
    """Tests for stripping magics and shell commands."""

    def test_strips_line_magics(self) -> None:
        source = "%matplotlib inline\nx = 1\n%timeit x + 1"
        result = preprocess_notebook_source(source)
        assert "%matplotlib" not in result
        assert "%timeit" not in result
        assert "x = 1" in result

    def test_strips_cell_magics(self) -> None:
        source = "%%sql\nSELECT * FROM users"
        result = preprocess_notebook_source(source)
        # Entire cell magic block replaced with blank lines
        lines = result.strip().splitlines()
        assert all(line.strip() == "" for line in lines)

    def test_strips_shell_commands(self) -> None:
        source = "!pip install pandas\nx = 1"
        result = preprocess_notebook_source(source)
        assert "!pip" not in result
        assert "x = 1" in result

    def test_preserves_line_count(self) -> None:
        source = "%magic\nx = 1\n!shell\ny = 2"
        result = preprocess_notebook_source(source)
        assert result.count("\n") == source.count("\n")

    def test_preserves_regular_code(self) -> None:
        source = "def foo(x):\n    return x * 2\n\nresult = foo(21)"
        result = preprocess_notebook_source(source)
        assert result == source

    def test_cell_magic_replaces_entire_body(self) -> None:
        source = "%%bash\necho hello\necho world"
        result = preprocess_notebook_source(source)
        lines = result.splitlines()
        assert len(lines) == 3
        assert all(line.strip() == "" for line in lines)

    def test_percent_in_string_not_stripped(self) -> None:
        source = 'fmt = "%d items"'
        result = preprocess_notebook_source(source)
        assert result == source

    def test_question_mark_help_stripped(self) -> None:
        source = "?print\nx = 1"
        result = preprocess_notebook_source(source)
        assert "?print" not in result
        assert "x = 1" in result

    def test_empty_string_passthrough(self) -> None:
        result = preprocess_notebook_source("")
        assert result == ""


class TestAnalyzeJupyter:
    """Tests for the full notebook analyzer."""

    def test_detects_functions(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "analysis.ipynb", [
            _code_cell("def greet(name):\n    return f'Hello {name}'"),
        ])
        result = analyze_jupyter(tmp_path)
        func_names = [s.name for s in result.symbols]
        assert "greet" in func_names

    def test_detects_classes(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "models.ipynb", [
            _code_cell("class Dog:\n    def bark(self):\n        return 'woof'"),
        ])
        result = analyze_jupyter(tmp_path)
        names = [s.name for s in result.symbols]
        assert "Dog" in names
        assert "Dog.bark" in names

    def test_symbols_have_jupyter_language(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "test.ipynb", [
            _code_cell("def hello(): pass"),
        ])
        result = analyze_jupyter(tmp_path)
        for s in result.symbols:
            assert s.language == "jupyter"

    def test_symbols_reference_ipynb_path(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "demo.ipynb", [
            _code_cell("def foo(): pass"),
        ])
        result = analyze_jupyter(tmp_path)
        assert any(s.path.endswith(".ipynb") for s in result.symbols)

    def test_cross_cell_functions(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "multi.ipynb", [
            _code_cell("def add(a, b):\n    return a + b"),
            _code_cell("def multiply(a, b):\n    return a * b"),
        ])
        result = analyze_jupyter(tmp_path)
        names = [s.name for s in result.symbols]
        assert "add" in names
        assert "multiply" in names

    def test_magics_dont_break_parsing(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "magic.ipynb", [
            _code_cell("%matplotlib inline"),
            _code_cell("def plot_data(x):\n    pass"),
        ])
        result = analyze_jupyter(tmp_path)
        names = [s.name for s in result.symbols]
        assert "plot_data" in names

    def test_empty_notebook_returns_empty_result(self, tmp_path: Path) -> None:
        _make_notebook(tmp_path, "empty.ipynb", [])
        result = analyze_jupyter(tmp_path)
        assert result.symbols == []
        assert result.edges == []

    def test_non_python_notebook_skipped(self, tmp_path: Path) -> None:
        _make_notebook(tmp_path, "r.ipynb", [
            _code_cell("x <- 1"),
        ], kernel_language="R")
        result = analyze_jupyter(tmp_path)
        assert result.symbols == []

    def test_call_edges_within_notebook(self, tmp_path: Path) -> None:
        nb = _make_notebook(tmp_path, "calls.ipynb", [
            _code_cell("def helper():\n    return 42"),
            _code_cell("def main():\n    return helper()"),
        ])
        result = analyze_jupyter(tmp_path)
        edge_pairs = [(e.src, e.dst) for e in result.edges]
        # Should detect that main calls helper
        assert any("main" in src and "helper" in dst for src, dst in edge_pairs)

    def test_no_ipynb_files_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "regular.py").write_text("x = 1")
        result = analyze_jupyter(tmp_path)
        assert result.symbols == []

    def test_ipynb_checkpoint_excluded(self, tmp_path: Path) -> None:
        checkpoint_dir = tmp_path / ".ipynb_checkpoints"
        checkpoint_dir.mkdir()
        _make_notebook(checkpoint_dir, "test-checkpoint.ipynb", [
            _code_cell("def should_not_appear(): pass"),
        ])
        _make_notebook(tmp_path, "real.ipynb", [
            _code_cell("def should_appear(): pass"),
        ])
        result = analyze_jupyter(tmp_path)
        names = [s.name for s in result.symbols]
        assert "should_appear" in names
        assert "should_not_appear" not in names

    def test_result_not_skipped(self, tmp_path: Path) -> None:
        _make_notebook(tmp_path, "test.ipynb", [
            _code_cell("x = 1"),
        ])
        result = analyze_jupyter(tmp_path)
        assert not result.skipped

    def test_syntax_error_in_cells_returns_empty(self, tmp_path: Path) -> None:
        _make_notebook(tmp_path, "broken.ipynb", [
            _code_cell("def broken(:\n    pass"),
        ])
        result = analyze_jupyter(tmp_path)
        assert result.symbols == []
        assert result.edges == []

    def test_attribute_call_edge(self, tmp_path: Path) -> None:
        """Calls like obj.method() should resolve attribute name."""
        _make_notebook(tmp_path, "attr.ipynb", [
            _code_cell("class Foo:\n    def bar(self):\n        pass"),
            _code_cell("def main():\n    f = Foo()\n    f.bar()"),
        ])
        result = analyze_jupyter(tmp_path)
        # bar is both a method name and an attribute call target
        edge_pairs = [(e.src, e.dst) for e in result.edges]
        assert any("main" in src and "bar" in dst for src, dst in edge_pairs)

    def test_call_to_unknown_function_no_edge(self, tmp_path: Path) -> None:
        """Calls to functions not defined in the notebook produce no edges."""
        _make_notebook(tmp_path, "extern.ipynb", [
            _code_cell("def main():\n    import os\n    os.listdir('.')"),
        ])
        result = analyze_jupyter(tmp_path)
        # No edges since os.listdir is not defined in the notebook
        assert result.edges == []

    def test_complex_call_no_edge(self, tmp_path: Path) -> None:
        """Complex call expressions (subscript, etc.) don't crash."""
        _make_notebook(tmp_path, "complex.ipynb", [
            _code_cell("def main():\n    funcs = [print]\n    funcs[0]('hi')"),
        ])
        result = analyze_jupyter(tmp_path)
        # funcs[0]() is a Subscript call — should not produce an edge
        assert result.edges == []

    def test_nested_function_detected(self, tmp_path: Path) -> None:
        """Nested functions are detected as symbols."""
        _make_notebook(tmp_path, "nested.ipynb", [
            _code_cell(
                "def outer():\n"
                "    def inner():\n"
                "        return 1\n"
                "    return inner()"
            ),
        ])
        result = analyze_jupyter(tmp_path)
        names = [s.name for s in result.symbols]
        assert "outer" in names
        assert "inner" in names
        # Should detect the call from outer to inner
        assert any("outer" in e.src and "inner" in e.dst for e in result.edges)
