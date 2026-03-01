"""Branch coverage tests for subprocess_cli.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.linkers.subprocess_cli import (
    _extract_command_info,
    _scan_python_file,
    _create_call_symbol,
    _has_command_concept,
    SubprocessCall,
    link_subprocess,
)
from hypergumbo_core.ir import Symbol, Span


def make_symbol(
    id_: str,
    name: str,
    kind: str = "function",
    path: str = "cli.py",
    meta: dict | None = None,
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language="python",
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        meta=meta,
    )


class TestExtractCommandInfo:
    """Branch coverage for _extract_command_info function."""

    def test_python_without_m_flag(self) -> None:
        """Test python command without -m flag (branch 103->112)."""
        executable, subcommand, is_python_m = _extract_command_info(
            '["python", "script.py", "arg1"]'
        )
        assert executable == "python"
        assert subcommand == "script.py"
        assert is_python_m is False

    def test_python_m_with_flag_argument(self) -> None:
        """Test python -m where 4th arg starts with dash (branch 107->109)."""
        executable, subcommand, is_python_m = _extract_command_info(
            '["python", "-m", "mypackage", "--verbose"]'
        )
        assert executable == "mypackage"
        assert subcommand is None  # --verbose is a flag, not subcommand
        assert is_python_m is True

    def test_python_m_only_three_args(self) -> None:
        """Test python -m with exactly 3 args (no 4th arg)."""
        executable, subcommand, is_python_m = _extract_command_info(
            '["python", "-m", "mypackage"]'
        )
        assert executable == "mypackage"
        assert subcommand is None
        assert is_python_m is True

    def test_regular_command_all_flags(self) -> None:
        """Test regular command with only flag arguments."""
        executable, subcommand, is_python_m = _extract_command_info(
            '["git", "--version"]'
        )
        assert executable == "git"
        assert subcommand is None  # --version is a flag
        assert is_python_m is False

    def test_empty_args_list(self) -> None:
        """Test with empty list."""
        executable, subcommand, is_python_m = _extract_command_info("[]")
        assert executable is None
        assert subcommand is None
        assert is_python_m is False

    def test_invalid_args_not_list(self) -> None:
        """Test with non-list argument."""
        executable, subcommand, is_python_m = _extract_command_info('"command"')
        assert executable is None
        assert subcommand is None
        assert is_python_m is False


class TestScanPythonFile:
    """Branch coverage for _scan_python_file function."""

    def test_literal_call_no_executable(self) -> None:
        """Test literal subprocess call that yields no executable (branch 232->226)."""
        content = '''
import subprocess
subprocess.run([])  # Empty list, no executable
'''
        calls = _scan_python_file(Path("test.py"), content)
        # Empty list produces no calls (executable is None)
        assert len(calls) == 0

    def test_variable_call_no_executable(self) -> None:
        """Test variable subprocess call where variable has no executable."""
        content = '''
import subprocess
cmd = []  # Empty list
subprocess.run(cmd)
'''
        calls = _scan_python_file(Path("test.py"), content)
        # Variable with empty list found but no executable
        var_calls = [c for c in calls if c.call_type == "variable"]
        # Should still create a call with None executable
        assert len(var_calls) >= 1

    def test_variable_call_not_found(self) -> None:
        """Test variable subprocess call where variable is not defined."""
        content = '''
import subprocess
subprocess.run(undefined_var)
'''
        calls = _scan_python_file(Path("test.py"), content)
        # Variable not found in file, creates call with None executable
        var_calls = [c for c in calls if c.call_type == "variable"]
        assert len(var_calls) == 1
        assert var_calls[0].executable is None


class TestCreateCallSymbol:
    """Branch coverage for _create_call_symbol function."""

    def test_call_with_no_executable(self) -> None:
        """Test symbol creation when executable is None (branch 318->320)."""
        call = SubprocessCall(
            executable=None,
            subcommand=None,
            line=10,
            file_path="test.py",
            call_type="variable",
            is_python_m=False,
            raw_args="unknown_cmd",
        )
        symbol = _create_call_symbol(call, Path("."))
        assert symbol.name == "subprocess"  # Fallback name

    def test_call_with_no_subcommand(self) -> None:
        """Test symbol creation when subcommand is None (branch 320->322)."""
        call = SubprocessCall(
            executable="git",
            subcommand=None,
            line=10,
            file_path="test.py",
            call_type="literal",
            is_python_m=False,
            raw_args='["git", "--version"]',
        )
        symbol = _create_call_symbol(call, Path("."))
        assert symbol.name == "git"  # Just executable, no subcommand


class TestHasCommandConcept:
    """Branch coverage for _has_command_concept function."""

    def test_symbol_without_command_concept(self) -> None:
        """Test symbol that has meta but no command concept (branch 368->367)."""
        sym = make_symbol(
            "id1",
            "my_function",
            meta={"concepts": [{"concept": "handler"}]},
        )
        assert _has_command_concept(sym) is False

    def test_symbol_with_empty_concepts(self) -> None:
        """Test symbol with empty concepts list."""
        sym = make_symbol("id1", "my_function", meta={"concepts": []})
        assert _has_command_concept(sym) is False

    def test_symbol_with_no_meta(self) -> None:
        """Test symbol with no meta at all."""
        sym = make_symbol("id1", "my_function", meta=None)
        assert _has_command_concept(sym) is False


class TestLinkSubprocess:
    """Branch coverage for link_subprocess function."""

    def test_call_with_no_subcommand_match(self, tmp_path: Path) -> None:
        """Test subprocess call with subcommand that doesn't match (branch 392->384)."""
        # Create a file with subprocess call
        test_file = tmp_path / "caller.py"
        test_file.write_text('''
import subprocess
subprocess.run(["myapp", "unknown_cmd"])
''')

        # Create pyproject.toml so myapp is detected as project CLI
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "myapp"
''')

        # CLI symbol with command concept
        cli_sym = make_symbol(
            "id1",
            "known_cmd",
            meta={"concepts": [{"concept": "command"}]},
        )

        result = link_subprocess(tmp_path, [cli_sym])
        # Call was found but subcommand doesn't match - no edges created
        assert result is not None
        assert len(result.edges) == 0  # No match for unknown_cmd

    def test_call_with_no_subcommand(self, tmp_path: Path) -> None:
        """Test subprocess call where subcommand is None."""
        test_file = tmp_path / "caller.py"
        test_file.write_text('''
import subprocess
subprocess.run(["myapp", "--version"])
''')

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "myapp"
''')

        cli_sym = make_symbol(
            "id1",
            "run",
            meta={"concepts": [{"concept": "command"}]},
        )

        result = link_subprocess(tmp_path, [cli_sym])
        # Call found but no subcommand (only flags) - no edge created
        assert result is not None
        assert len(result.edges) == 0

    def test_cli_symbol_without_command_concept(self, tmp_path: Path) -> None:
        """Test with CLI symbol that lacks command concept (branch 368->367)."""
        test_file = tmp_path / "caller.py"
        test_file.write_text('''
import subprocess
subprocess.run(["git", "status"])
''')

        # Symbol without command concept
        sym = make_symbol("id1", "status", meta={"concepts": []})

        result = link_subprocess(tmp_path, [sym])
        # Symbol not added to command_by_name, no matches possible
        assert result is not None
