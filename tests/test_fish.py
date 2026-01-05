"""Tests for Fish shell analysis pass.

Tests verify that the Fish analyzer correctly extracts:
- Function definitions
- Alias declarations
- Global variable assignments
- Source/import statements
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import fish as fish_module
from hypergumbo.analyze.fish import (
    analyze_fish,
    find_fish_files,
    is_fish_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindFishFiles:
    """Tests for find_fish_files function."""

    def test_finds_fish_files(self, temp_repo: Path) -> None:
        """Finds .fish extension files."""
        (temp_repo / "config.fish").write_text("set -g PATH $PATH")
        (temp_repo / "functions").mkdir()
        (temp_repo / "functions" / "greet.fish").write_text("function greet\nend")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_fish_files(temp_repo))
        filenames = {f.name for f in files}

        assert "config.fish" in filenames
        assert "greet.fish" in filenames
        assert "README.md" not in filenames


class TestFishTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_fish_tree_sitter_available()
        assert isinstance(result, bool)


class TestFishAnalysis:
    """Tests for Fish shell analysis with tree-sitter."""

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function definitions."""
        (temp_repo / "funcs.fish").write_text('''
function greet
    echo "Hello!"
end

function farewell --description "Say goodbye"
    echo "Bye!"
end
''')

        result = analyze_fish(temp_repo)

        assert not result.skipped
        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "greet" in func_names
        assert "farewell" in func_names

    def test_function_with_arguments(self, temp_repo: Path) -> None:
        """Detects function argument options."""
        (temp_repo / "math.fish").write_text('''
function add --argument a b
    math "$a + $b"
end
''')

        result = analyze_fish(temp_repo)

        func = next(s for s in result.symbols if s.name == "add")
        assert func.signature is not None
        assert "a" in func.signature
        assert "b" in func.signature

    def test_analyzes_alias(self, temp_repo: Path) -> None:
        """Detects alias declarations."""
        (temp_repo / "aliases.fish").write_text('''
alias ll "ls -la"
alias g "git"
alias vim "nvim"
''')

        result = analyze_fish(temp_repo)

        alias_names = {s.name for s in result.symbols if s.kind == "alias"}
        assert "ll" in alias_names
        assert "g" in alias_names
        assert "vim" in alias_names

    def test_analyzes_global_variable(self, temp_repo: Path) -> None:
        """Detects global variable assignments."""
        (temp_repo / "config.fish").write_text('''
set -g EDITOR nvim
set -gx PATH $HOME/bin $PATH
set -U fish_greeting ""
''')

        result = analyze_fish(temp_repo)

        var_names = {s.name for s in result.symbols if s.kind == "variable"}
        assert "EDITOR" in var_names
        assert "PATH" in var_names
        assert "fish_greeting" in var_names

    def test_analyzes_source_statements(self, temp_repo: Path) -> None:
        """Detects source statements as import edges."""
        (temp_repo / "config.fish").write_text('''
source ~/.config/fish/aliases.fish
source /usr/share/fish/config.fish
''')

        result = analyze_fish(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "sources"]
        assert len(import_edges) >= 2

    def test_analyzes_function_calls(self, temp_repo: Path) -> None:
        """Detects function/command calls."""
        (temp_repo / "init.fish").write_text('''
function setup
    greet
    configure_path
end

function greet
    echo "Hello"
end

function configure_path
    set -g PATH /usr/bin
end
''')

        result = analyze_fish(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 2


class TestFishAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "config.fish").write_text("set -g PATH $PATH")

        with patch.object(fish_module, "is_fish_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Fish analysis skipped"):
                result = fish_module.analyze_fish(temp_repo)

        assert result.skipped is True


class TestFishAnalysisRun:
    """Tests for Fish analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "config.fish").write_text('''
function greet
    echo "Hello"
end
''')

        result = analyze_fish(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "fish-v1"
        assert result.run.files_analyzed >= 1
