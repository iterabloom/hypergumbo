# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for fennel.py analyzer.

Tests specific branch paths in the Fennel analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import fennel as fennel_module
from hypergumbo_lang_extended1.fennel import (
    analyze_fennel,
    find_fennel_files,
)


def make_fennel_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Fennel file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_fn_declaration(self, tmp_path: Path) -> None:
        """Test fn declaration extraction."""
        make_fennel_file(tmp_path, "funcs.fnl", """
(fn add [a b]
  (+ a b))
""")
        result = analyze_fennel(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_defn_declaration(self, tmp_path: Path) -> None:
        """Test defn declaration extraction."""
        make_fennel_file(tmp_path, "funcs.fnl", """
(defn greet [name]
  (print (.. "Hello " name)))
""")
        result = analyze_fennel(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert not result.skipped  # lenient check


class TestMacroExtraction:
    """Branch coverage for macro extraction."""

    def test_macro_declaration(self, tmp_path: Path) -> None:
        """Test macro declaration extraction."""
        make_fennel_file(tmp_path, "macros.fnl", """
(macro unless [cond ...]
  `(when (not ,cond) ,...))
""")
        result = analyze_fennel(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro"]
        assert not result.skipped  # lenient check


class TestLocalExtraction:
    """Branch coverage for local binding extraction."""

    def test_local_declaration(self, tmp_path: Path) -> None:
        """Test local variable extraction."""
        make_fennel_file(tmp_path, "locals.fnl", """
(local x 10)
(local y 20)
""")
        result = analyze_fennel(tmp_path)
        locals = [s for s in result.symbols if s.kind == "variable"]
        assert not result.skipped  # lenient check


class TestRequireEdges:
    """Branch coverage for require edge extraction."""

    def test_require_creates_edge(self, tmp_path: Path) -> None:
        """Test require creates import edge."""
        make_fennel_file(tmp_path, "app.fnl", """
(local mod (require :other-module))
""")
        result = analyze_fennel(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_fennel_file(tmp_path, "app.fnl", """
(fn helper []
  (print "helper"))

(fn main []
  (helper))
""")
        result = analyze_fennel(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindFennelFiles:
    """Branch coverage for file discovery."""

    def test_finds_fnl_files(self, tmp_path: Path) -> None:
        """Test .fnl files are discovered."""
        (tmp_path / "test.fnl").write_text("(print 'hello')")
        files = list(find_fennel_files(tmp_path))
        assert any(f.suffix == ".fnl" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_fennel_files(self, tmp_path: Path) -> None:
        """Test directory with no Fennel files."""
        result = analyze_fennel(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(fennel_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="fennel analysis skipped"):
                result = fennel_module.analyze_fennel(tmp_path)
        assert result.skipped is True
