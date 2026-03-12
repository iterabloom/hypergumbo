# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for powershell.py analyzer.

Tests specific branch paths in the PowerShell analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function, filter, and workflow extraction
- Parameter and signature extraction
- Import edges (Import-Module, using)
- Call edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_symbol_id
from hypergumbo_lang_mainstream.powershell import analyze_powershell, find_powershell_files

def make_ps_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a PowerShell file with given content."""
    (tmp_path / name).write_text(content)

class TestPowerShellHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("powershell", "script.ps1", 1, 10, "Get-User", "function")
        assert symbol_id == "powershell:script.ps1:1-10:Get-User:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = make_file_id("powershell", "script.ps1")
        assert file_id == "powershell:script.ps1:1-1:file:file"

class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_ps_file(tmp_path, "script.ps1", """
function Get-Greeting {
    return "Hello, World!"
}
""")
        result = analyze_powershell(tmp_path)
        assert not result.skipped

        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "Get-Greeting" for f in functions)

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with parameters."""
        make_ps_file(tmp_path, "script.ps1", """
function Get-User {
    param(
        [string]$UserId,
        [int]$Age
    )
    return @{UserId = $UserId; Age = $Age}
}
""")
        result = analyze_powershell(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        get_user = next((f for f in functions if f.name == "Get-User"), None)
        assert get_user is not None
        assert get_user.signature is not None
        assert "$UserId" in get_user.signature

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple functions extraction."""
        make_ps_file(tmp_path, "script.ps1", """
function Get-Data {
    return @()
}

function Set-Data {
    param([object]$Data)
    # Set data
}

function Remove-Data {
    # Remove data
}
""")
        result = analyze_powershell(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]

        assert len(functions) >= 3

class TestFilterExtraction:
    """Branch coverage for filter extraction."""

    def test_filter_definition(self, tmp_path: Path) -> None:
        """Test filter definition extraction."""
        make_ps_file(tmp_path, "script.ps1", """
filter ConvertTo-Upper {
    $_.ToUpper()
}
""")
        result = analyze_powershell(tmp_path)
        filters = [s for s in result.symbols if s.kind == "filter"]

        assert len(filters) >= 1
        assert any(f.name == "ConvertTo-Upper" for f in filters)

class TestImportEdges:
    """Branch coverage for import edge extraction."""

    def test_import_module(self, tmp_path: Path) -> None:
        """Test Import-Module creates import edge."""
        make_ps_file(tmp_path, "script.ps1", """
Import-Module Az.Storage

function Get-Blob {
    # Get blob
}
""")
        result = analyze_powershell(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

    def test_using_module(self, tmp_path: Path) -> None:
        """Test using module creates import edge."""
        make_ps_file(tmp_path, "script.ps1", """
using module PSReadLine

function Get-Readline {
    # Get readline
}
""")
        result = analyze_powershell(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]

        assert len(import_edges) >= 1

class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call_creates_edge(self, tmp_path: Path) -> None:
        """Test function call creates call edge."""
        make_ps_file(tmp_path, "script.ps1", """
function Get-Helper {
    return 42
}

function Get-Main {
    $result = Get-Helper
    return $result
}
""")
        result = analyze_powershell(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]

        assert len(call_edges) >= 1

class TestFindPowerShellFiles:
    """Branch coverage for file discovery."""

    def test_finds_ps1_files(self, tmp_path: Path) -> None:
        """Test .ps1 files are discovered."""
        (tmp_path / "script.ps1").write_text("Write-Host 'Hello'")

        files = list(find_powershell_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".ps1" for f in files)

    def test_finds_psm1_files(self, tmp_path: Path) -> None:
        """Test .psm1 module files are discovered."""
        (tmp_path / "Module.psm1").write_text("function Get-Module {}")

        files = list(find_powershell_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".psm1" for f in files)

    def test_finds_psd1_files(self, tmp_path: Path) -> None:
        """Test .psd1 manifest files are discovered."""
        (tmp_path / "Module.psd1").write_text("@{ ModuleVersion = '1.0.0' }")

        files = list(find_powershell_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".psd1" for f in files)

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_powershell_files(self, tmp_path: Path) -> None:
        """Test directory with no PowerShell files."""
        result = analyze_powershell(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_powershell(self, tmp_path: Path) -> None:
        """Test minimal PowerShell file."""
        make_ps_file(tmp_path, "script.ps1", """
Write-Host "Hello"
""")
        result = analyze_powershell(tmp_path)
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_ps_file(tmp_path, "script.ps1", """
function Get-Main {
    Write-Host "Hello"
}
""")
        result = analyze_powershell(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
