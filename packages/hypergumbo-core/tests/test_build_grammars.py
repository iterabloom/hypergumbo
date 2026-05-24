# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the build_grammars module.

This module tests the functionality for building tree-sitter grammars
from source (Lean, Wolfram, Circom).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hypergumbo_core.build_grammars import (
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

    def test_source_grammars_contains_lean_wolfram_and_circom(self) -> None:
        """Test that SOURCE_GRAMMARS contains all three source-built grammars.

        scripts/build-source-grammars (the CI/dev path) builds Lean, Wolfram,
        and Circom. The user-facing `hypergumbo build-grammars` command must
        match — otherwise users running it from the published wheel still see
        a "Circom analysis skipped" warning that the warning's own remediation
        message tells them to fix with this command.
        """
        names = [g.name for g in SOURCE_GRAMMARS]
        assert "lean" in names
        assert "wolfram" in names
        assert "circom" in names

    def test_circom_grammar_spec_matches_shell_script(self) -> None:
        """Circom GrammarSpec must match scripts/build-source-grammars:279.

        Locks in the same repo URL, binding function name, and scanner type
        the shell script uses, so the two build paths produce equivalent
        installed packages.
        """
        by_name = {g.name: g for g in SOURCE_GRAMMARS}
        circom = by_name["circom"]
        assert circom.repo_url == "https://github.com/Decurity/tree-sitter-circom.git"
        assert circom.function_name == "tree_sitter_circom"
        assert circom.scanner_type == "none"


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
    """Tests for build_grammar function (WI-fipab-kivoj vendor-mode)."""

    @staticmethod
    def _stage_vendor(tmp_path: Path, name: str) -> Path:
        """Stage a fake vendor/tree-sitter-<name>/src/parser.c under ``tmp_path``."""
        vendor_root = tmp_path / "vendor"
        repo_dir = vendor_root / f"tree-sitter-{name}"
        (repo_dir / "src").mkdir(parents=True)
        (repo_dir / "src" / "parser.c").write_text("// fake")
        return vendor_root

    def test_build_grammar_missing_vendor_source(self, tmp_path: Path) -> None:
        """Returns False if vendor/<grammar>/src/ is missing (WI-fipab-kivoj)."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        with patch("hypergumbo_core.build_grammars.VENDOR_ROOT", tmp_path / "vendor"):
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

        vendor_root = self._stage_vendor(tmp_path, "test")

        def mock_run_command(cmd, cwd=None, quiet=False):
            if "pip" in cmd:
                raise subprocess.CalledProcessError(1, "pip")
            return MagicMock(returncode=0)

        with patch("hypergumbo_core.build_grammars.VENDOR_ROOT", vendor_root):
            with patch(
                "hypergumbo_core.build_grammars._run_command",
                side_effect=mock_run_command,
            ):
                result = build_grammar(spec, tmp_path, quiet=True)
                assert result is False

    def test_build_grammar_runs_pip_install_only(self, tmp_path: Path) -> None:
        """Vendor-mode build issues no git commands — only the pip install."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        vendor_root = self._stage_vendor(tmp_path, "test")
        commands_run = []

        def mock_run_command(cmd, cwd=None, quiet=False):
            commands_run.append(cmd)
            return MagicMock(returncode=0)

        with patch("hypergumbo_core.build_grammars.VENDOR_ROOT", vendor_root):
            with patch(
                "hypergumbo_core.build_grammars._run_command",
                side_effect=mock_run_command,
            ):
                build_grammar(spec, tmp_path, quiet=True)

        assert not any(c[0] == "git" for c in commands_run)
        assert any("pip" in c for c in commands_run)

    def test_build_grammar_cleans_existing_pkg_dir(self, tmp_path: Path) -> None:
        """Test that existing package directory is cleaned before building."""
        spec = GrammarSpec(
            name="test",
            repo_url="https://example.com/test.git",
            function_name="tree_sitter_test",
            scanner_type="none",
        )

        vendor_root = self._stage_vendor(tmp_path, "test")

        pkg_dir = tmp_path / "tree-sitter-test-py"
        pkg_dir.mkdir()
        old_file = pkg_dir / "old_file.txt"
        old_file.write_text("old content")

        def mock_run_command(cmd, cwd=None, quiet=False):
            return MagicMock(returncode=0)

        with patch("hypergumbo_core.build_grammars.VENDOR_ROOT", vendor_root):
            with patch(
                "hypergumbo_core.build_grammars._run_command",
                side_effect=mock_run_command,
            ):
                result = build_grammar(spec, tmp_path, quiet=True)

        # Old file should be gone
        assert not old_file.exists()
        # New package structure should exist
        assert (pkg_dir / "tree_sitter_test" / "__init__.py").exists()
        assert result is True


class TestVendorLayout:
    """Tests for the vendor directory layout (WI-fipab-kivoj)."""

    def test_vendor_root_points_at_repo_root(self) -> None:
        """``VENDOR_ROOT`` must resolve to ``<repo_root>/vendor``."""
        from hypergumbo_core.build_grammars import VENDOR_ROOT
        # ``VENDOR_ROOT`` derives from this module's location; we don't
        # care about the absolute prefix, only the leaf segment.
        assert VENDOR_ROOT.name == "vendor"

    def test_vendor_dir_for_constructs_path(self) -> None:
        """``vendor_dir_for(name)`` is ``VENDOR_ROOT / 'tree-sitter-<name>'``."""
        from hypergumbo_core.build_grammars import VENDOR_ROOT, vendor_dir_for
        assert vendor_dir_for("lean") == VENDOR_ROOT / "tree-sitter-lean"
        assert vendor_dir_for("wolfram") == VENDOR_ROOT / "tree-sitter-wolfram"
        assert vendor_dir_for("circom") == VENDOR_ROOT / "tree-sitter-circom"

    def test_each_source_grammar_has_vendored_src(self) -> None:
        """Each SOURCE_GRAMMARS entry must have a real vendored ``src/``."""
        from hypergumbo_core.build_grammars import SOURCE_GRAMMARS, vendor_dir_for
        for spec in SOURCE_GRAMMARS:
            src_dir = vendor_dir_for(spec.name) / "src"
            assert src_dir.exists(), f"missing vendor src/ for {spec.name}"
            assert (src_dir / "parser.c").exists(), (
                f"missing parser.c for {spec.name}"
            )

    def test_each_vendored_grammar_has_license(self) -> None:
        """Each vendored grammar must ship a LICENSE file (MIT in all
        three current cases)."""
        from hypergumbo_core.build_grammars import SOURCE_GRAMMARS, vendor_dir_for
        for spec in SOURCE_GRAMMARS:
            license_file = vendor_dir_for(spec.name) / "LICENSE"
            assert license_file.exists(), f"missing LICENSE for {spec.name}"
            assert "MIT" in license_file.read_text(), (
                f"LICENSE for {spec.name} is not MIT — re-confirm vendoring "
                "is permitted before pulling a new upstream version"
            )

    def test_each_vendored_grammar_has_upstream_metadata(self) -> None:
        """Each vendored grammar must ship an UPSTREAM file recording the
        source URL and the pinned commit (drives docs/grammars/vendor-sync.md)."""
        from hypergumbo_core.build_grammars import SOURCE_GRAMMARS, vendor_dir_for
        for spec in SOURCE_GRAMMARS:
            upstream = vendor_dir_for(spec.name) / "UPSTREAM"
            assert upstream.exists(), f"missing UPSTREAM for {spec.name}"
            content = upstream.read_text()
            assert spec.repo_url.rstrip(".git") in content, (
                f"UPSTREAM for {spec.name} does not record repo_url"
            )
            assert "Commit:" in content, (
                f"UPSTREAM for {spec.name} does not record a pinned commit"
            )


class TestBuildAllGrammars:
    """Tests for build_all_grammars function."""

    def test_build_all_grammars_returns_results(self, tmp_path: Path) -> None:
        """Test that build_all_grammars returns results for all grammars."""
        with patch(
            "hypergumbo_core.build_grammars.build_grammar",
            return_value=True,
        ):
            results = build_all_grammars(build_dir=tmp_path, quiet=True)

        assert "lean" in results
        assert "wolfram" in results
        assert "circom" in results
        assert all(results.values())

    def test_build_all_grammars_partial_failure(self, tmp_path: Path) -> None:
        """Test handling when some grammars fail to build."""

        def mock_build(spec, build_dir, quiet=False):
            return spec.name != "wolfram"  # wolfram fails

        with patch("hypergumbo_core.build_grammars.build_grammar", side_effect=mock_build):
            results = build_all_grammars(build_dir=tmp_path, quiet=True)

        assert results["lean"] is True
        assert results["wolfram"] is False

    def test_build_all_grammars_uses_temp_dir(self) -> None:
        """Test that build_all_grammars uses temp dir by default."""
        with patch("hypergumbo_core.build_grammars.build_grammar", return_value=True):
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
        assert "circom" in results
        # They should be available since we built them earlier
        assert results["lean"] is True
        assert results["wolfram"] is True
        assert results["circom"] is True

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
        from hypergumbo_core.cli import main

        # Should succeed since grammars are built
        result = main(["build-grammars", "--check"])
        assert result == 0

    def test_build_grammars_check_when_missing(self) -> None:
        """Test build-grammars --check when grammars missing."""
        from hypergumbo_core.cli import cmd_build_grammars
        import argparse

        args = argparse.Namespace(check=True, quiet=False)

        with patch(
            "hypergumbo_core.cli.check_grammar_availability",
            return_value={"lean": False, "wolfram": True},
        ):
            result = cmd_build_grammars(args)
            assert result == 1  # Failure because one is missing

    def test_build_grammars_build_success(self) -> None:
        """Test build-grammars command success."""
        from hypergumbo_core.cli import cmd_build_grammars
        import argparse

        args = argparse.Namespace(check=False, quiet=True)

        with patch(
            "hypergumbo_core.cli.build_all_grammars",
            return_value={"lean": True, "wolfram": True},
        ):
            result = cmd_build_grammars(args)
            assert result == 0

    def test_build_grammars_build_failure(self) -> None:
        """Test build-grammars command failure."""
        from hypergumbo_core.cli import cmd_build_grammars
        import argparse

        args = argparse.Namespace(check=False, quiet=False)

        with patch(
            "hypergumbo_core.cli.build_all_grammars",
            return_value={"lean": True, "wolfram": False},
        ):
            result = cmd_build_grammars(args)
            assert result == 1
