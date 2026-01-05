"""Tests for PowerShell analysis pass.

Tests verify that the PowerShell analyzer correctly extracts:
- Function definitions
- Advanced functions (CmdletBinding)
- Parameters with types and defaults
- Command/function calls
- Module imports (Import-Module, using)
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import powershell as ps_module
from hypergumbo.analyze.powershell import (
    analyze_powershell,
    find_powershell_files,
    is_powershell_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindPowerShellFiles:
    """Tests for find_powershell_files function."""

    def test_finds_ps1_files(self, temp_repo: Path) -> None:
        """Finds .ps1 files in repo."""
        (temp_repo / "script.ps1").write_text("Write-Host 'Hello'")
        (temp_repo / "module.psm1").write_text("function Get-Data { }")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_powershell_files(temp_repo))
        filenames = {f.name for f in files}

        assert "script.ps1" in filenames
        assert "module.psm1" in filenames
        assert "README.md" not in filenames

    def test_finds_nested_powershell_files(self, temp_repo: Path) -> None:
        """Finds PowerShell files in subdirectories."""
        scripts = temp_repo / "scripts"
        scripts.mkdir()
        (scripts / "deploy.ps1").write_text("Write-Host 'Deploy'")

        files = list(find_powershell_files(temp_repo))

        assert len(files) == 1
        assert files[0].name == "deploy.ps1"


class TestPowerShellTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_powershell_tree_sitter_available()
        assert isinstance(result, bool)


class TestPowerShellAnalysis:
    """Tests for PowerShell analysis with tree-sitter."""

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function declarations."""
        (temp_repo / "script.ps1").write_text('''
function Get-User {
    param($UserId)
    return $UserId
}
''')

        result = analyze_powershell(temp_repo)

        assert not result.skipped
        assert any(s.kind == "function" and s.name == "Get-User" for s in result.symbols)

    def test_analyzes_multiple_functions(self, temp_repo: Path) -> None:
        """Detects multiple function declarations."""
        (temp_repo / "script.ps1").write_text('''
function Get-User { }
function Set-User { }
function Remove-User { }
''')

        result = analyze_powershell(temp_repo)

        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "Get-User" in func_names
        assert "Set-User" in func_names
        assert "Remove-User" in func_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters and types."""
        (temp_repo / "script.ps1").write_text('''
function Get-User {
    param(
        [string]$UserId,
        [int]$Age = 30
    )
}
''')

        result = analyze_powershell(temp_repo)

        func = next(s for s in result.symbols if s.name == "Get-User")
        assert func.signature is not None
        assert "UserId" in func.signature
        assert "string" in func.signature

    def test_analyzes_command_calls(self, temp_repo: Path) -> None:
        """Detects command/function calls and creates edges."""
        (temp_repo / "script.ps1").write_text('''
function Main {
    Get-Process
    Stop-Service -Name "MyService"
}
''')

        result = analyze_powershell(temp_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 2

    def test_analyzes_import_module(self, temp_repo: Path) -> None:
        """Detects Import-Module and creates import edges."""
        (temp_repo / "script.ps1").write_text('''
Import-Module ActiveDirectory
Import-Module -Name AzureAD

function Get-Data { }
''')

        result = analyze_powershell(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 2

    def test_analyzes_using_statement(self, temp_repo: Path) -> None:
        """Detects using statements for module imports."""
        (temp_repo / "script.ps1").write_text('''
using module MyModule
using namespace System.Collections

function Main { }
''')

        result = analyze_powershell(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert any("MyModule" in e.dst for e in import_edges)

    def test_analyzes_filter(self, temp_repo: Path) -> None:
        """Detects filter definitions."""
        (temp_repo / "script.ps1").write_text('''
filter Get-ActiveUsers {
    if ($_.Status -eq 'Active') { $_ }
}
''')

        result = analyze_powershell(temp_repo)

        assert any(s.kind == "filter" and s.name == "Get-ActiveUsers" for s in result.symbols)

    def test_analyzes_workflow(self, temp_repo: Path) -> None:
        """Detects workflow definitions (PowerShell 5.1)."""
        (temp_repo / "script.ps1").write_text('''
workflow Deploy-Application {
    param($Server)
    InlineScript { Write-Host "Deploying" }
}
''')

        result = analyze_powershell(temp_repo)

        # Workflows may be parsed as functions depending on grammar
        symbols = {s.name for s in result.symbols}
        assert "Deploy-Application" in symbols


class TestPowerShellAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "script.ps1").write_text("Write-Host 'Test'")

        with patch.object(ps_module, "is_powershell_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="PowerShell analysis skipped"):
                result = ps_module.analyze_powershell(temp_repo)

        assert result.skipped is True


class TestPowerShellAnalysisRun:
    """Tests for PowerShell analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "script.ps1").write_text('''
function Main { }
''')

        result = analyze_powershell(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "powershell-v1"
        assert result.run.files_analyzed >= 1
