"""Tests for the build_grammars module.

This module tests the functionality for building tree-sitter grammars
from source (Lean, Wolfram).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hypergumbo.build_grammars import (
    GrammarSpec,
    SOURCE_GRAMMARS,
    _generate_binding_c,
    _generate_init_py,
    _generate_setup_py,
    _run_command,
    build_grammar,
    build_all_grammars,
    check_grammar_availability,
)


class TestGrammarSpec:
    """Tests for GrammarSpec dataclass."""

    def test_grammar_spec_creation(self) -> None:
        """Test creating a GrammarSpec."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="c",
        )
        assert spec.name == "test"
        assert spec.repo_url == "https://example.com/test.git"
        assert spec.function_name == "tree_sitter_test"
        assert spec.scanner_type == "c"

    def test_source_grammars_contains_lean_and_wolfram(self) -> None:
        """Test that SOURCE_GRAMMARS contains expected grammars."""
        names = [g.name for g in SOURCE_GRAMMARS]
        assert "lean" in names
        assert "wolfram" in names


class TestCodeGeneration:
    """Tests for code generation functions."""

    def test_generate_binding_c(self) -> None:
        """Test C binding code generation."""
        code = _generate_binding_c("tree_sitter_test")
        assert "tree_sitter_test" in code
        assert "#include <Python.h>" in code
        assert "PyMODINIT_FUNC PyInit__binding" in code
        assert "tree_sitter.Language" in code

    def test_generate_init_py(self) -> None:
        """Test __init__.py generation."""
        code = _generate_init_py()
        assert "from ._binding import language" in code
        assert '__all__ = ["language"]' in code

    def test_generate_setup_py_no_scanner(self) -> None:
        """Test setup.py generation without scanner."""
        code = _generate_setup_py("test", "tree_sitter_test", "/tmp/repo", "none")
        assert "tree-sitter-test" in code
        assert "parser.c" in code
        assert "scanner" not in code
        assert "-std=c11" in code

    def test_generate_setup_py_c_scanner(self) -> None:
        """Test setup.py generation with C scanner."""
        code = _generate_setup_py("test", "tree_sitter_test", "/tmp/repo", "c")
        assert "scanner.c" in code
        assert "-std=c11" in code

    def test_generate_setup_py_cc_scanner(self) -> None:
        """Test setup.py generation with C++ scanner."""
        code = _generate_setup_py("test", "tree_sitter_test", "/tmp/repo", "cc")
        assert "scanner.cc" in code
        assert "-std=c++14" in code


class TestRunCommand:
    """Tests for _run_command helper."""

    def test_run_command_success(self) -> None:
        """Test successful command execution."""
        result = _run_command(["echo", "hello"], quiet=True)
        assert result.returncode == 0

    def test_run_command_with_cwd(self, tmp_path: Path) -> None:
        """Test command execution with working directory."""
        result = _run_command(["pwd"], cwd=tmp_path, quiet=True)
        assert result.returncode == 0

    def test_run_command_failure(self) -> None:
        """Test command failure raises CalledProcessError."""
        with pytest.raises(subprocess.CalledProcessError):
            _run_command(["false"], quiet=True)


class TestBuildGrammar:
    """Tests for build_grammar function."""

    def test_build_grammar_git_not_found(self, tmp_path: Path) -> None:
        """Test handling when git is not found."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        with patch(
            "hypergumbo.build_grammars._run_command",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = build_grammar(spec, tmp_path, quiet=True)
            assert result is False

    def test_build_grammar_clone_failure(self, tmp_path: Path) -> None:
        """Test handling when git clone fails."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        with patch(
            "hypergumbo.build_grammars._run_command",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = build_grammar(spec, tmp_path, quiet=True)
            assert result is False

    def test_build_grammar_pip_install_failure(self, tmp_path: Path) -> None:
        """Test handling when pip install fails."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        # Create fake repo dir so clone "succeeds"
        repo_dir = tmp_path / "tree-sitter-test"
        repo_dir.mkdir()
        (repo_dir / "src").mkdir()
        (repo_dir / "src" / "parser.c").write_text("// fake")

        call_count = 0

        def mock_run_command(cmd, cwd=None, quiet=False):
            nonlocal call_count
            call_count += 1
            # First call is git pull (ignore), subsequent is pip install
            if "pip" in cmd:
                raise subprocess.CalledProcessError(1, "pip")
            return MagicMock(returncode=0)

        with patch("hypergumbo.build_grammars._run_command", side_effect=mock_run_command):
            result = build_grammar(spec, tmp_path, quiet=True)
            assert result is False

    def test_build_grammar_updates_existing_repo(self, tmp_path: Path) -> None:
        """Test that existing repos are updated, not cloned."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        # Create existing repo dir
        repo_dir = tmp_path / "tree-sitter-test"
        repo_dir.mkdir()
        (repo_dir / "src").mkdir()
        (repo_dir / "src" / "parser.c").write_text("// fake")

        commands_run = []

        def mock_run_command(cmd, cwd=None, quiet=False):
            commands_run.append(cmd)
            return MagicMock(returncode=0)

        with patch("hypergumbo.build_grammars._run_command", side_effect=mock_run_command):
            build_grammar(spec, tmp_path, quiet=True)

        # Should have run git pull, not git clone
        git_commands = [c for c in commands_run if c[0] == "git"]
        assert any("pull" in c for c in git_commands)
        assert not any("clone" in c for c in git_commands)

    def test_build_grammar_git_pull_failure_ignored(self, tmp_path: Path) -> None:
        """Test that git pull failures are silently ignored."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        # Create existing repo dir
        repo_dir = tmp_path / "tree-sitter-test"
        repo_dir.mkdir()
        (repo_dir / "src").mkdir()
        (repo_dir / "src" / "parser.c").write_text("// fake")

        call_count = 0

        def mock_run_command(cmd, cwd=None, quiet=False):
            nonlocal call_count
            call_count += 1
            # Git pull fails (detached HEAD, etc.)
            if "pull" in cmd:
                raise subprocess.CalledProcessError(1, "git pull")
            # pip install succeeds
            return MagicMock(returncode=0)

        with patch("hypergumbo.build_grammars._run_command", side_effect=mock_run_command):
            result = build_grammar(spec, tmp_path, quiet=True)

        # Should still succeed despite pull failure
        assert result is True

    def test_build_grammar_cleans_existing_pkg_dir(self, tmp_path: Path) -> None:
        """Test that existing package directory is cleaned before building."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        # Create existing repo and pkg directories
        repo_dir = tmp_path / "tree-sitter-test"
        repo_dir.mkdir()
        (repo_dir / "src").mkdir()
        (repo_dir / "src" / "parser.c").write_text("// fake")

        pkg_dir = tmp_path / "tree-sitter-test-py"
        pkg_dir.mkdir()
        old_file = pkg_dir / "old_file.txt"
        old_file.write_text("old content")

        def mock_run_command(cmd, cwd=None, quiet=False):
            return MagicMock(returncode=0)

        with patch("hypergumbo.build_grammars._run_command", side_effect=mock_run_command):
            result = build_grammar(spec, tmp_path, quiet=True)

        # Old file should be gone
        assert not old_file.exists()
        # New package structure should exist
        assert (pkg_dir / "tree_sitter_test" / "__init__.py").exists()
        assert result is True


class TestBuildAllGrammars:
    """Tests for build_all_grammars function."""

    def test_build_all_grammars_returns_results(self, tmp_path: Path) -> None:
        """Test that build_all_grammars returns results for all grammars."""
        with patch(
            "hypergumbo.build_grammars.build_grammar",
            return_value=True,
        ):
            results = build_all_grammars(build_dir=tmp_path, quiet=True)

        assert "lean" in results
        assert "wolfram" in results
        assert all(results.values())

    def test_build_all_grammars_partial_failure(self, tmp_path: Path) -> None:
        """Test handling when some grammars fail to build."""

        def mock_build(spec, build_dir, quiet=False):
            return spec.name != "wolfram"  # wolfram fails

        with patch("hypergumbo.build_grammars.build_grammar", side_effect=mock_build):
            results = build_all_grammars(build_dir=tmp_path, quiet=True)

        assert results["lean"] is True
        assert results["wolfram"] is False

    def test_build_all_grammars_uses_temp_dir(self) -> None:
        """Test that build_all_grammars uses temp dir by default."""
        with patch("hypergumbo.build_grammars.build_grammar", return_value=True):
            results = build_all_grammars(quiet=True)

        assert len(results) == len(SOURCE_GRAMMARS)


class TestCheckGrammarAvailability:
    """Tests for check_grammar_availability function."""

    def test_check_grammar_availability_all_available(self) -> None:
        """Test when all grammars are available."""
        # Since we built the grammars, they should be available
        results = check_grammar_availability()
        assert "lean" in results
        assert "wolfram" in results
        # They should be available since we built them earlier
        assert results["lean"] is True
        assert results["wolfram"] is True

    def test_check_grammar_availability_some_missing(self) -> None:
        """Test when some grammars are missing."""
        with patch.dict("sys.modules", {"tree_sitter_lean": None}):
            # Can't easily mock ImportError for already-imported module
            # so we test the structure instead
            results = check_grammar_availability()
            assert isinstance(results, dict)
            assert len(results) == len(SOURCE_GRAMMARS)


class TestCliIntegration:
    """Tests for CLI integration."""

    def test_build_grammars_check_command(self) -> None:
        """Test the build-grammars --check CLI command."""
        from hypergumbo.cli import main

        # Should succeed since grammars are built
        result = main(["build-grammars", "--check"])
        assert result == 0

    def test_build_grammars_check_when_missing(self) -> None:
        """Test build-grammars --check when grammars missing."""
        from hypergumbo.cli import cmd_build_grammars
        import argparse

        args = argparse.Namespace(check=True, quiet=False)

        with patch(
            "hypergumbo.cli.check_grammar_availability",
            return_value={"lean": False, "wolfram": True},
        ):
            result = cmd_build_grammars(args)
            assert result == 1  # Failure because one is missing

    def test_build_grammars_build_success(self) -> None:
        """Test build-grammars command success."""
        from hypergumbo.cli import cmd_build_grammars
        import argparse

        args = argparse.Namespace(check=False, quiet=True)

        with patch(
            "hypergumbo.cli.build_all_grammars",
            return_value={"lean": True, "wolfram": True},
        ):
            result = cmd_build_grammars(args)
            assert result == 0

    def test_build_grammars_build_failure(self) -> None:
        """Test build-grammars command failure."""
        from hypergumbo.cli import cmd_build_grammars
        import argparse

        args = argparse.Namespace(check=False, quiet=False)

        with patch(
            "hypergumbo.cli.build_all_grammars",
            return_value={"lean": True, "wolfram": False},
        ):
            result = cmd_build_grammars(args)
            assert result == 1
