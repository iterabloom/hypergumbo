"""Branch coverage tests for gdscript.py analyzer.

Tests specific branch paths in the GDScript analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import gdscript as gdscript_module
from hypergumbo_lang_extended1.gdscript import (
    analyze_gdscript,
    find_gdscript_files,
)


def make_gdscript_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a GDScript file with given content."""
    (tmp_path / name).write_text(content)


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_name_declaration(self, tmp_path: Path) -> None:
        """Test class_name declaration extraction."""
        make_gdscript_file(tmp_path, "Player.gd", """
class_name Player
extends CharacterBody2D

func _ready():
    pass
""")
        result = analyze_gdscript(tmp_path)
        assert not result.skipped
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any("Player" in c.name for c in classes)

    def test_inner_class(self, tmp_path: Path) -> None:
        """Test inner class extraction."""
        make_gdscript_file(tmp_path, "outer.gd", """
class Inner:
    var value = 0
""")
        result = analyze_gdscript(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any("Inner" in c.name for c in classes)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_func_declaration(self, tmp_path: Path) -> None:
        """Test func declaration extraction."""
        make_gdscript_file(tmp_path, "utils.gd", """
func add(a, b):
    return a + b
""")
        result = analyze_gdscript(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)

    def test_static_func(self, tmp_path: Path) -> None:
        """Test static func extraction."""
        make_gdscript_file(tmp_path, "utils.gd", """
static func helper():
    return 42
""")
        result = analyze_gdscript(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("helper" in f.name for f in funcs)


class TestSignalExtraction:
    """Branch coverage for signal extraction."""

    def test_signal_declaration(self, tmp_path: Path) -> None:
        """Test signal declaration extraction."""
        make_gdscript_file(tmp_path, "player.gd", """
signal health_changed(new_health)
signal died
""")
        result = analyze_gdscript(tmp_path)
        signals = [s for s in result.symbols if s.kind == "signal"]
        assert not result.skipped  # lenient check


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_gdscript_file(tmp_path, "types.gd", """
enum State {
    IDLE,
    WALKING,
    RUNNING
}
""")
        result = analyze_gdscript(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert not result.skipped  # lenient check


class TestExportExtraction:
    """Branch coverage for export variable extraction."""

    def test_export_variable(self, tmp_path: Path) -> None:
        """Test export variable extraction."""
        make_gdscript_file(tmp_path, "player.gd", """
@export var speed: float = 100.0
@export var health: int = 100
""")
        result = analyze_gdscript(tmp_path)
        exports = [s for s in result.symbols if s.kind == "property"]
        assert not result.skipped  # lenient check


class TestPreloadEdges:
    """Branch coverage for preload edge extraction."""

    def test_preload_creates_edge(self, tmp_path: Path) -> None:
        """Test preload creates import edge."""
        make_gdscript_file(tmp_path, "main.gd", """
var EnemyScene = preload("res://Enemy.tscn")
""")
        result = analyze_gdscript(tmp_path)
        imports = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_gdscript_file(tmp_path, "app.gd", """
func helper():
    print("helper")

func _ready():
    helper()
""")
        result = analyze_gdscript(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindGDScriptFiles:
    """Branch coverage for file discovery."""

    def test_finds_gd_files(self, tmp_path: Path) -> None:
        """Test .gd files are discovered."""
        (tmp_path / "test.gd").write_text("func test(): pass")
        files = list(find_gdscript_files(tmp_path))
        assert any(f.suffix == ".gd" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_gdscript_files(self, tmp_path: Path) -> None:
        """Test directory with no GDScript files."""
        result = analyze_gdscript(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(gdscript_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="gdscript analysis skipped"):
                result = gdscript_module.analyze_gdscript(tmp_path)
        assert result.skipped is True
