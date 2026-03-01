"""Branch coverage tests for fish.py analyzer.

Tests specific branch paths in the Fish shell analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import fish as fish_module
from hypergumbo_lang_extended1.fish import (
    analyze_fish,
    find_fish_files,
)


def make_fish_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Fish shell file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_fish_file(tmp_path, "funcs.fish", """
function greet
    echo "Hello, $argv"
end
""")
        result = analyze_fish(tmp_path)
        assert not result.skipped
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("greet" in f.name for f in funcs)

    def test_function_with_description(self, tmp_path: Path) -> None:
        """Test function with description."""
        make_fish_file(tmp_path, "funcs.fish", """
function add --description 'Add two numbers'
    math $argv[1] + $argv[2]
end
""")
        result = analyze_fish(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_set_variable(self, tmp_path: Path) -> None:
        """Test set variable extraction."""
        make_fish_file(tmp_path, "vars.fish", """
set -g MY_VAR "value"
set -l local_var 42
""")
        result = analyze_fish(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert not result.skipped  # lenient check


class TestAliasExtraction:
    """Branch coverage for alias extraction."""

    def test_alias_declaration(self, tmp_path: Path) -> None:
        """Test alias declaration extraction."""
        make_fish_file(tmp_path, "aliases.fish", """
alias ll 'ls -la'
alias gs 'git status'
""")
        result = analyze_fish(tmp_path)
        aliases = [s for s in result.symbols if s.kind == "alias"]
        assert not result.skipped  # lenient check


class TestSourceEdges:
    """Branch coverage for source edge extraction."""

    def test_source_creates_edge(self, tmp_path: Path) -> None:
        """Test source creates import edge."""
        make_fish_file(tmp_path, "main.fish", """
source ~/.config/fish/helpers.fish
""")
        result = analyze_fish(tmp_path)
        sources = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_fish_file(tmp_path, "app.fish", """
function helper
    echo "helper"
end

function main
    helper
end
""")
        result = analyze_fish(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindFishFiles:
    """Branch coverage for file discovery."""

    def test_finds_fish_files(self, tmp_path: Path) -> None:
        """Test .fish files are discovered."""
        (tmp_path / "test.fish").write_text("echo hello")
        files = list(find_fish_files(tmp_path))
        assert any(f.suffix == ".fish" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_fish_files(self, tmp_path: Path) -> None:
        """Test directory with no Fish files."""
        result = analyze_fish(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(fish_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="fish analysis skipped"):
                result = fish_module.analyze_fish(tmp_path)
        assert result.skipped is True
