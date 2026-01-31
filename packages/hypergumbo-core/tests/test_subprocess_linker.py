"""Tests for the subprocess linker.

The subprocess linker detects subprocess calls (subprocess.run, subprocess.call,
subprocess.Popen) and links them to CLI command entry points in the same repository.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.linkers.subprocess_cli import (
    SubprocessCall,
    link_subprocess,
    _scan_python_file,
    _extract_command_info,
    _detect_project_cli_name,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestScanPythonFile:
    """Tests for detecting subprocess calls in Python files."""

    def test_subprocess_run_literal_list(self, tmp_path: Path) -> None:
        """Detects subprocess.run with literal list argument."""
        code = '''
import subprocess
subprocess.run(["myapp", "serve", "--port", "8080"])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        assert calls[0].executable == "myapp"
        assert calls[0].subcommand == "serve"
        assert calls[0].call_type == "literal"

    def test_subprocess_call_literal_list(self, tmp_path: Path) -> None:
        """Detects subprocess.call with literal list argument."""
        code = '''
import subprocess
subprocess.call(["mycli", "init"])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        assert calls[0].executable == "mycli"
        assert calls[0].subcommand == "init"

    def test_subprocess_popen_literal_list(self, tmp_path: Path) -> None:
        """Detects subprocess.Popen with literal list argument."""
        code = '''
import subprocess
proc = subprocess.Popen(["myapp", "daemon"])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        assert calls[0].executable == "myapp"
        assert calls[0].subcommand == "daemon"

    def test_python_m_module(self, tmp_path: Path) -> None:
        """Detects python -m module invocation."""
        code = '''
import subprocess
subprocess.run(["python", "-m", "mypackage", "run"])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        # For python -m, executable is the module name
        assert calls[0].executable == "mypackage"
        assert calls[0].subcommand == "run"
        assert calls[0].is_python_m is True

    def test_python3_m_module(self, tmp_path: Path) -> None:
        """Detects python3 -m module invocation."""
        code = '''
subprocess.run(["python3", "-m", "hypergumbo", "sketch", "."])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        assert calls[0].executable == "hypergumbo"
        assert calls[0].subcommand == "sketch"
        assert calls[0].is_python_m is True

    def test_no_subcommand(self, tmp_path: Path) -> None:
        """Handles commands without subcommand."""
        code = '''
subprocess.run(["myapp"])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        assert calls[0].executable == "myapp"
        assert calls[0].subcommand is None

    def test_variable_command(self, tmp_path: Path) -> None:
        """Detects subprocess with variable command (lower confidence)."""
        code = '''
cmd = ["myapp", "run"]
subprocess.run(cmd)
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        assert calls[0].call_type == "variable"

    def test_multiple_calls(self, tmp_path: Path) -> None:
        """Detects multiple subprocess calls in one file."""
        code = '''
subprocess.run(["app1", "cmd1"])
subprocess.call(["app2", "cmd2"])
subprocess.Popen(["app3", "cmd3"])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 3

    def test_ignores_non_subprocess(self, tmp_path: Path) -> None:
        """Ignores calls that aren't subprocess functions."""
        code = '''
os.system("echo hello")
result = run_command(["app", "cmd"])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 0

    def test_variable_not_found(self, tmp_path: Path) -> None:
        """Handles variable subprocess call where definition is not in file."""
        code = '''
# cmd_list is imported from elsewhere
subprocess.run(cmd_list)
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        assert len(calls) == 1
        assert calls[0].executable is None
        assert calls[0].call_type == "variable"

    def test_empty_list(self, tmp_path: Path) -> None:
        """Handles empty list argument."""
        code = '''
subprocess.run([])
'''
        calls = _scan_python_file(tmp_path / "test.py", code)
        # Empty list should not produce a valid call
        assert len(calls) == 0

    def test_duplicate_line_skipped(self, tmp_path: Path) -> None:
        """Variable pattern on same line as literal is skipped."""
        # This tests the `continue` branch when line is in literal_lines
        # Two subprocess calls on the same line: literal then variable
        code = 'subprocess.run(["myapp", "cmd"]); subprocess.run(args)'
        calls = _scan_python_file(tmp_path / "test.py", code)
        # The literal call is captured; the variable call on same line is skipped
        # because its line is already in literal_lines
        assert len(calls) == 1
        assert calls[0].executable == "myapp"
        assert calls[0].call_type == "literal"


class TestExtractCommandInfo:
    """Tests for extracting command info from subprocess arguments."""

    def test_simple_list(self) -> None:
        """Extracts from simple list."""
        exe, sub, is_py = _extract_command_info('["myapp", "serve"]')
        assert exe == "myapp"
        assert sub == "serve"
        assert is_py is False

    def test_python_m(self) -> None:
        """Extracts from python -m invocation."""
        exe, sub, is_py = _extract_command_info('["python", "-m", "pkg", "cmd"]')
        assert exe == "pkg"
        assert sub == "cmd"
        assert is_py is True

    def test_single_command(self) -> None:
        """Handles single command without subcommand."""
        exe, sub, is_py = _extract_command_info('["myapp"]')
        assert exe == "myapp"
        assert sub is None
        assert is_py is False

    def test_with_flags(self) -> None:
        """Skips flags to find subcommand."""
        exe, sub, is_py = _extract_command_info('["myapp", "--verbose", "run"]')
        assert exe == "myapp"
        assert sub == "run"

    def test_empty_list(self) -> None:
        """Returns None for empty list."""
        exe, sub, is_py = _extract_command_info('[]')
        assert exe is None
        assert sub is None
        assert is_py is False

    def test_non_list(self) -> None:
        """Returns None for non-list argument."""
        exe, sub, is_py = _extract_command_info('"just a string"')
        assert exe is None
        assert sub is None


class TestDetectProjectCliName:
    """Tests for detecting the project's CLI name."""

    def test_from_pyproject_toml_scripts(self, tmp_path: Path) -> None:
        """Detects CLI name from pyproject.toml [project.scripts]."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "my-package"

[project.scripts]
mycli = "my_package.cli:main"
''')
        names = _detect_project_cli_name(tmp_path)
        assert "mycli" in names
        assert "my-package" in names
        assert "my_package" in names

    def test_from_pyproject_toml_name_only(self, tmp_path: Path) -> None:
        """Falls back to project name if no scripts defined."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('''
[project]
name = "hypergumbo"
''')
        names = _detect_project_cli_name(tmp_path)
        assert "hypergumbo" in names

    def test_no_pyproject(self, tmp_path: Path) -> None:
        """Returns empty set if no pyproject.toml."""
        names = _detect_project_cli_name(tmp_path)
        assert names == set()


class TestLinkSubprocess:
    """Tests for the main linking function."""

    def test_links_subprocess_to_cli_command(self, tmp_path: Path) -> None:
        """Links subprocess call to CLI command symbol."""
        # Create pyproject.toml to identify project CLI
        (tmp_path / "pyproject.toml").write_text('''
[project]
name = "myapp"
[project.scripts]
myapp = "myapp.cli:main"
''')

        # Create test file with subprocess call
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_cli.py").write_text('''
import subprocess
subprocess.run(["myapp", "serve"])
''')

        # Create CLI command symbol (as would be detected by framework patterns)
        cli_symbol = Symbol(
            id="python:src/myapp/cli.py:10-20:serve:function",
            name="serve",
            kind="function",
            language="python",
            path="src/myapp/cli.py",
            span=Span(10, 20, 0, 0),
            meta={"concepts": [{"concept": "command", "framework": "click"}]},
        )

        result = link_subprocess(tmp_path, [cli_symbol])

        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.edge_type == "subprocess_calls"
        assert edge.dst == cli_symbol.id
        assert edge.confidence >= 0.8

    def test_links_python_m_to_cli_command(self, tmp_path: Path) -> None:
        """Links python -m invocation to CLI command."""
        (tmp_path / "pyproject.toml").write_text('''
[project]
name = "hypergumbo"
''')

        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_run.py").write_text('''
subprocess.run(["python", "-m", "hypergumbo", "sketch", "."])
''')

        cli_symbol = Symbol(
            id="python:src/hypergumbo/cli.py:50-100:sketch:function",
            name="sketch",
            kind="function",
            language="python",
            path="src/hypergumbo/cli.py",
            span=Span(50, 100, 0, 0),
            meta={"concepts": [{"concept": "command"}]},
        )

        result = link_subprocess(tmp_path, [cli_symbol])

        assert len(result.edges) == 1
        assert result.edges[0].dst == cli_symbol.id

    def test_no_match_different_project(self, tmp_path: Path) -> None:
        """Does not link subprocess calls to unrelated CLIs."""
        (tmp_path / "pyproject.toml").write_text('''
[project]
name = "myapp"
''')

        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_other.py").write_text('''
subprocess.run(["git", "status"])  # Different CLI, should not match
''')

        cli_symbol = Symbol(
            id="python:src/myapp/cli.py:10-20:serve:function",
            name="serve",
            kind="function",
            language="python",
            path="src/myapp/cli.py",
            span=Span(10, 20, 0, 0),
            meta={"concepts": [{"concept": "command"}]},
        )

        result = link_subprocess(tmp_path, [cli_symbol])

        # No edges because "git" is not this project's CLI
        assert len(result.edges) == 0

    def test_creates_subprocess_call_symbol(self, tmp_path: Path) -> None:
        """Creates a symbol for the subprocess call site."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"')

        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_cli.py").write_text('subprocess.run(["myapp", "run"])')

        cli_symbol = Symbol(
            id="python:src/myapp/cli.py:10-20:run:function",
            name="run",
            kind="function",
            language="python",
            path="src/myapp/cli.py",
            span=Span(10, 20, 0, 0),
            meta={"concepts": [{"concept": "command"}]},
        )

        result = link_subprocess(tmp_path, [cli_symbol])

        # Should create a symbol for the subprocess call
        assert len(result.symbols) >= 1
        call_symbol = result.symbols[0]
        assert call_symbol.kind == "subprocess_call"
        assert "myapp" in call_symbol.name

    def test_variable_command_lower_confidence(self, tmp_path: Path) -> None:
        """Variable commands get lower confidence than literal."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"')

        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        # Using a variable for the command
        (test_dir / "test_cli.py").write_text('''
cmd = ["myapp", "run"]
subprocess.run(cmd)
''')

        cli_symbol = Symbol(
            id="python:src/myapp/cli.py:10-20:run:function",
            name="run",
            kind="function",
            language="python",
            path="src/myapp/cli.py",
            span=Span(10, 20, 0, 0),
            meta={"concepts": [{"concept": "command"}]},
        )

        result = link_subprocess(tmp_path, [cli_symbol])

        # Variable commands should have lower confidence
        if result.edges:
            assert result.edges[0].confidence < 0.8


class TestHasCommandConcept:
    """Tests for _has_command_concept helper."""

    def test_symbol_without_meta(self) -> None:
        """Returns False when symbol has no meta."""
        from hypergumbo_core.linkers.subprocess_cli import _has_command_concept

        symbol = Symbol(
            id="test:1",
            name="func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(1, 5, 0, 0),
            meta=None,  # No meta
        )
        assert _has_command_concept(symbol) is False

    def test_symbol_with_command_concept(self) -> None:
        """Returns True when symbol has command concept."""
        from hypergumbo_core.linkers.subprocess_cli import _has_command_concept

        symbol = Symbol(
            id="test:1",
            name="func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(1, 5, 0, 0),
            meta={"concepts": [{"concept": "command"}]},
        )
        assert _has_command_concept(symbol) is True

    def test_symbol_with_other_concept(self) -> None:
        """Returns False when symbol has non-command concept."""
        from hypergumbo_core.linkers.subprocess_cli import _has_command_concept

        symbol = Symbol(
            id="test:1",
            name="func",
            kind="function",
            language="python",
            path="test.py",
            span=Span(1, 5, 0, 0),
            meta={"concepts": [{"concept": "route"}]},
        )
        assert _has_command_concept(symbol) is False


class TestLinkerRegistration:
    """Tests for linker registry integration."""

    def test_linker_registered(self) -> None:
        """Subprocess linker is registered in the registry."""
        # Reload the module to force re-registration (in case registry was cleared)
        import importlib

        import hypergumbo_core.linkers.subprocess_cli
        importlib.reload(hypergumbo_core.linkers.subprocess_cli)

        from hypergumbo_core.linkers.registry import get_linker

        linker = get_linker("subprocess")
        assert linker is not None
        assert "subprocess" in linker.description.lower() or "cli" in linker.description.lower()

    def test_linker_via_context(self, tmp_path: Path) -> None:
        """Linker works via LinkerContext interface."""
        from hypergumbo_core.linkers.subprocess_cli import subprocess_linker

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"')
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_cli.py").write_text('subprocess.run(["myapp", "cmd"])')

        cli_symbol = Symbol(
            id="python:cli.py:1-5:cmd:function",
            name="cmd",
            kind="function",
            language="python",
            path="cli.py",
            span=Span(1, 5, 0, 0),
            meta={"concepts": [{"concept": "command"}]},
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[cli_symbol])
        result = subprocess_linker(ctx)

        assert result is not None
        assert isinstance(result.edges, list)

    def test_requirement_check_counts_commands(self, tmp_path: Path) -> None:
        """Requirement check correctly counts CLI command symbols."""
        from hypergumbo_core.linkers.subprocess_cli import _count_cli_command_symbols

        cli_symbol = Symbol(
            id="test:1",
            name="cmd",
            kind="function",
            language="python",
            path="cli.py",
            span=Span(1, 5, 0, 0),
            meta={"concepts": [{"concept": "command"}]},
        )
        non_cli_symbol = Symbol(
            id="test:2",
            name="helper",
            kind="function",
            language="python",
            path="helpers.py",
            span=Span(1, 5, 0, 0),
            meta={"concepts": [{"concept": "route"}]},
        )

        ctx = LinkerContext(repo_root=tmp_path, symbols=[cli_symbol, non_cli_symbol])
        count = _count_cli_command_symbols(ctx)
        assert count == 1  # Only the CLI command, not the route
