"""Tests for GDScript (Godot) analysis pass.

Tests verify that the GDScript analyzer correctly extracts:
- Class/script definitions (extends)
- Function definitions
- Variable declarations
- Signal declarations
- Function calls
- Preload/load imports
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import gdscript as gdscript_module
from hypergumbo_lang_extended1.gdscript import (
    analyze_gdscript,
    find_gdscript_files,
    is_gdscript_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindGDScriptFiles:
    """Tests for find_gdscript_files function."""

    def test_finds_gd_files(self, temp_repo: Path) -> None:
        """Finds .gd files in repo."""
        (temp_repo / "player.gd").write_text("extends Node2D")
        (temp_repo / "enemy.gd").write_text("extends CharacterBody2D")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_gdscript_files(temp_repo))
        filenames = {f.name for f in files}

        assert "player.gd" in filenames
        assert "enemy.gd" in filenames
        assert "README.md" not in filenames

    def test_finds_nested_gd_files(self, temp_repo: Path) -> None:
        """Finds GDScript files in subdirectories."""
        scripts = temp_repo / "scripts"
        scripts.mkdir()
        (scripts / "player.gd").write_text("extends Node2D")

        files = list(find_gdscript_files(temp_repo))

        assert len(files) == 1
        assert files[0].name == "player.gd"


class TestGDScriptTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_gdscript_tree_sitter_available()
        assert isinstance(result, bool)


class TestGDScriptAnalysis:
    """Tests for GDScript analysis with tree-sitter."""

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function declarations."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

func _ready():
    pass

func move(direction: Vector2) -> void:
    pass
''')

        result = analyze_gdscript(temp_repo)

        assert not result.skipped
        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "_ready" in func_names
        assert "move" in func_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters and return type."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

func take_damage(amount: int, source: Node) -> int:
    return health
''')

        result = analyze_gdscript(temp_repo)

        func = next(s for s in result.symbols if s.name == "take_damage")
        assert func.signature is not None
        assert "amount" in func.signature
        assert "int" in func.signature

    def test_function_signature_untyped_params(self, temp_repo: Path) -> None:
        """Function signatures work with untyped parameters."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

func process(x, y, z):
    pass
''')

        result = analyze_gdscript(temp_repo)

        func = next(s for s in result.symbols if s.name == "process")
        assert func.signature is not None
        assert "x" in func.signature
        assert "y" in func.signature
        assert "z" in func.signature

    def test_analyzes_variable(self, temp_repo: Path) -> None:
        """Detects variable declarations."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

var speed: float = 100.0
var health: int = 100
var target: Node2D
''')

        result = analyze_gdscript(temp_repo)

        var_names = {s.name for s in result.symbols if s.kind == "variable"}
        assert "speed" in var_names
        assert "health" in var_names
        assert "target" in var_names

    def test_analyzes_signal(self, temp_repo: Path) -> None:
        """Detects signal declarations."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

signal health_changed(new_health)
signal died
''')

        result = analyze_gdscript(temp_repo)

        signal_names = {s.name for s in result.symbols if s.kind == "signal"}
        assert "health_changed" in signal_names
        assert "died" in signal_names

    def test_analyzes_class_name(self, temp_repo: Path) -> None:
        """Detects class_name declaration."""
        (temp_repo / "player.gd").write_text('''
extends Node2D
class_name Player

func _ready():
    pass
''')

        result = analyze_gdscript(temp_repo)

        assert any(s.kind == "class" and s.name == "Player" for s in result.symbols)

    def test_analyzes_function_calls(self, temp_repo: Path) -> None:
        """Detects function calls and creates edges."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

func _ready():
    setup()
    initialize_health()

func setup():
    pass

func initialize_health():
    pass
''')

        result = analyze_gdscript(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 2
        # Src should be the caller function ID, not file ID
        ready_call = next(e for e in call_edges if "setup" in e.dst)
        assert "_ready" in ready_call.src
        # Resolved calls should have higher confidence
        assert ready_call.confidence > 0.70

    def test_cross_file_call_resolution(self, temp_repo: Path) -> None:
        """Resolves calls across files."""
        # Define helper function in one file
        (temp_repo / "utils.gd").write_text('''
extends Node

func helper():
    pass
''')
        # Call it from another file
        (temp_repo / "main.gd").write_text('''
extends Node2D

func _ready():
    helper()
''')

        result = analyze_gdscript(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_call = next(e for e in call_edges if "helper" in e.dst)
        # Should resolve to actual symbol, not external
        assert "external" not in helper_call.dst
        assert helper_call.confidence > 0.70

    def test_external_call_confidence(self, temp_repo: Path) -> None:
        """External calls have lower confidence."""
        (temp_repo / "test.gd").write_text('''
extends Node2D

func _ready():
    unknown_external_function()
''')

        result = analyze_gdscript(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        external_call = next(e for e in call_edges if "external" in e.dst)
        assert external_call.confidence == 0.70

    def test_analyzes_preload(self, temp_repo: Path) -> None:
        """Detects preload() and creates import edges."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

const Enemy = preload("res://scenes/enemy.gd")
var bullet_scene = preload("res://scenes/bullet.tscn")
''')

        result = analyze_gdscript(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

    def test_analyzes_inner_class(self, temp_repo: Path) -> None:
        """Detects inner class definitions."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

class Stats:
    var health: int = 100
    var speed: float = 50.0
''')

        result = analyze_gdscript(temp_repo)

        assert any(s.kind == "class" and s.name == "Stats" for s in result.symbols)


class TestGDScriptAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "player.gd").write_text("extends Node2D")

        with patch.object(gdscript_module, "is_gdscript_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="GDScript analysis skipped"):
                result = gdscript_module.analyze_gdscript(temp_repo)

        assert result.skipped is True


class TestGDScriptAnalysisRun:
    """Tests for GDScript analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "player.gd").write_text('''
extends Node2D

func _ready():
    pass
''')

        result = analyze_gdscript(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "gdscript-v1"
        assert result.run.files_analyzed >= 1
