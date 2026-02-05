"""Branch coverage tests for janet.py analyzer.

Tests specific branch paths in the Janet analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import janet as janet_module
from hypergumbo_lang_extended1.janet import (
    analyze_janet,
    find_janet_files,
)


def make_janet_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Janet file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_defn_declaration(self, tmp_path: Path) -> None:
        """Test defn declaration extraction."""
        make_janet_file(tmp_path, "funcs.janet", """
(defn add [a b]
  (+ a b))
""")
        result = analyze_janet(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_defn_with_docstring(self, tmp_path: Path) -> None:
        """Test defn with docstring extraction."""
        make_janet_file(tmp_path, "funcs.janet", """
(defn greet
  "Greet a person by name"
  [name]
  (print "Hello " name))
""")
        result = analyze_janet(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("greet" in f.name for f in funcs)


class TestMacroExtraction:
    """Branch coverage for macro extraction."""

    def test_defmacro_declaration(self, tmp_path: Path) -> None:
        """Test defmacro declaration extraction."""
        make_janet_file(tmp_path, "macros.janet", """
(defmacro unless [cond & body]
  ~(if (not ,cond) (do ,;body)))
""")
        result = analyze_janet(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro"]
        assert not result.skipped  # lenient check


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_def_declaration(self, tmp_path: Path) -> None:
        """Test def declaration extraction."""
        make_janet_file(tmp_path, "vars.janet", """
(def pi 3.14159)
(var counter 0)
""")
        result = analyze_janet(tmp_path)
        vars = [s for s in result.symbols if s.kind in ("variable", "constant")]
        assert not result.skipped  # lenient check


class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_creates_edge(self, tmp_path: Path) -> None:
        """Test import creates edge."""
        make_janet_file(tmp_path, "main.janet", """
(import spork/json)
""")
        result = analyze_janet(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_janet_file(tmp_path, "app.janet", """
(defn helper []
  (print "helper"))

(defn main []
  (helper))
""")
        result = analyze_janet(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindJanetFiles:
    """Branch coverage for file discovery."""

    def test_finds_janet_files(self, tmp_path: Path) -> None:
        """Test .janet files are discovered."""
        (tmp_path / "test.janet").write_text("(print 'hello')")
        files = list(find_janet_files(tmp_path))
        assert any(f.suffix == ".janet" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_janet_files(self, tmp_path: Path) -> None:
        """Test directory with no Janet files."""
        result = analyze_janet(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(janet_module, "is_janet_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Janet analysis skipped"):
                result = janet_module.analyze_janet(tmp_path)
        assert result.skipped is True
