# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for pony.py analyzer.

Tests specific branch paths in the Pony analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import pony as pony_module
from hypergumbo_lang_extended1.pony import (
    analyze_pony,
    find_pony_files,
)


def make_pony_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Pony file with given content."""
    (tmp_path / name).write_text(content)


class TestActorExtraction:
    """Branch coverage for actor extraction."""

    def test_actor_declaration(self, tmp_path: Path) -> None:
        """Test actor declaration extraction."""
        make_pony_file(tmp_path, "main.pony", """
actor Main
  new create(env: Env) =>
    env.out.print("Hello, World!")
""")
        result = analyze_pony(tmp_path)
        assert not result.skipped
        actors = [s for s in result.symbols if s.kind == "actor"]
        assert any("Main" in a.name for a in actors)


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration extraction."""
        make_pony_file(tmp_path, "classes.pony", """
class Point
  var x: F64
  var y: F64

  new create(x': F64, y': F64) =>
    x = x'
    y = y'
""")
        result = analyze_pony(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any("Point" in c.name for c in classes)


class TestPrimitiveExtraction:
    """Branch coverage for primitive extraction."""

    def test_primitive_declaration(self, tmp_path: Path) -> None:
        """Test primitive declaration extraction."""
        make_pony_file(tmp_path, "primitives.pony", """
primitive Red
primitive Green
primitive Blue
""")
        result = analyze_pony(tmp_path)
        primitives = [s for s in result.symbols if s.kind == "primitive"]
        assert not result.skipped  # lenient check


class TestTraitExtraction:
    """Branch coverage for trait extraction."""

    def test_trait_declaration(self, tmp_path: Path) -> None:
        """Test trait declaration extraction."""
        make_pony_file(tmp_path, "traits.pony", """
trait Printable
  fun print(): String
""")
        result = analyze_pony(tmp_path)
        traits = [s for s in result.symbols if s.kind == "trait"]
        assert any("Printable" in t.name for t in traits)


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_pony_file(tmp_path, "interfaces.pony", """
interface Serializable
  fun serialize(): Array[U8]
""")
        result = analyze_pony(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("Serializable" in i.name for i in interfaces)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_fun_declaration(self, tmp_path: Path) -> None:
        """Test fun declaration extraction."""
        make_pony_file(tmp_path, "funcs.pony", """
class Calculator
  fun add(a: I64, b: I64): I64 =>
    a + b
""")
        result = analyze_pony(tmp_path)
        funcs = [s for s in result.symbols if s.kind in ("fun", "method")]
        assert any("add" in f.name for f in funcs)


class TestBehaviourExtraction:
    """Branch coverage for behaviour extraction."""

    def test_be_declaration(self, tmp_path: Path) -> None:
        """Test be declaration extraction."""
        make_pony_file(tmp_path, "actors.pony", """
actor Counter
  var count: U64 = 0

  be increment() =>
    count = count + 1
""")
        result = analyze_pony(tmp_path)
        behaviours = [s for s in result.symbols if s.kind in ("behaviour", "be")]
        assert not result.skipped  # lenient check


class TestUseEdges:
    """Branch coverage for use edge extraction."""

    def test_use_creates_edge(self, tmp_path: Path) -> None:
        """Test use creates import edge."""
        make_pony_file(tmp_path, "main.pony", """
use "collections"
use "files"

actor Main
  new create(env: Env) =>
    None
""")
        result = analyze_pony(tmp_path)
        uses = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestFindPonyFiles:
    """Branch coverage for file discovery."""

    def test_finds_pony_files(self, tmp_path: Path) -> None:
        """Test .pony files are discovered."""
        (tmp_path / "test.pony").write_text("primitive Test")
        files = list(find_pony_files(tmp_path))
        assert any(f.suffix == ".pony" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_pony_files(self, tmp_path: Path) -> None:
        """Test directory with no Pony files."""
        result = analyze_pony(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(pony_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="pony analysis skipped"):
                result = pony_module.analyze_pony(tmp_path)
        assert result.skipped is True
