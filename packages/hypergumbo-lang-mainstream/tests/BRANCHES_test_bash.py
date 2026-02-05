"""Branch coverage tests for bash.py analyzer.

Tests specific branch paths in the Bash analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function extraction
- Export extraction
- Alias extraction
- Source edge extraction
- Call edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_mainstream.bash import (
    _make_symbol_id,
    _make_file_id,
    _is_bash_shebang,
    analyze_bash,
    find_bash_files,
)


def make_bash_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Bash file with given content."""
    (tmp_path / name).write_text(content)


class TestBashHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("scripts/build.sh", 1, 5, "setup", "function")
        assert symbol_id == "bash:scripts/build.sh:1-5:setup:function"

    def test_make_file_id_format(self) -> None:
        """Test file ID format."""
        file_id = _make_file_id("scripts/common.sh")
        assert file_id == "bash:scripts/common.sh:1-1:file:file"

    def test_is_bash_shebang_bash(self) -> None:
        """Test bash shebang detection."""
        assert _is_bash_shebang("#!/bin/bash")
        assert _is_bash_shebang("#!/usr/bin/bash")
        assert _is_bash_shebang("#!/usr/bin/env bash")

    def test_is_bash_shebang_sh(self) -> None:
        """Test sh shebang detection."""
        assert _is_bash_shebang("#!/bin/sh")
        assert _is_bash_shebang("#!/usr/bin/sh")
        assert _is_bash_shebang("#!/usr/bin/env sh")

    def test_is_bash_shebang_false(self) -> None:
        """Test non-bash shebang rejection."""
        assert not _is_bash_shebang("#!/usr/bin/python")
        assert not _is_bash_shebang("#!/usr/bin/env python")
        assert not _is_bash_shebang("# not a shebang")


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
setup() {
    echo "Setting up"
}
""")
        result = analyze_bash(tmp_path)
        assert not result.skipped

        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "setup" for f in functions)

    def test_function_keyword_style(self, tmp_path: Path) -> None:
        """Test function with 'function' keyword."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
function cleanup {
    rm -rf /tmp/test
}
""")
        result = analyze_bash(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "cleanup" for f in functions)

    def test_function_signature(self, tmp_path: Path) -> None:
        """Test function signature is empty (bash uses $1 $2)."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
greet() {
    echo "Hello $1"
}
""")
        result = analyze_bash(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function" and s.name == "greet"]
        assert len(functions) >= 1
        assert functions[0].signature == "()"


class TestExportExtraction:
    """Branch coverage for export extraction."""

    def test_simple_export(self, tmp_path: Path) -> None:
        """Test simple export extraction."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
export PATH="/usr/local/bin:$PATH"
""")
        result = analyze_bash(tmp_path)
        exports = [s for s in result.symbols if s.kind == "export"]
        assert len(exports) >= 1
        assert any(e.name == "PATH" for e in exports)

    def test_multiple_exports(self, tmp_path: Path) -> None:
        """Test multiple exports."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
export HOME="/home/user"
export EDITOR="vim"
""")
        result = analyze_bash(tmp_path)
        exports = [s for s in result.symbols if s.kind == "export"]
        assert len(exports) >= 2


class TestAliasExtraction:
    """Branch coverage for alias extraction."""

    def test_simple_alias(self, tmp_path: Path) -> None:
        """Test simple alias extraction."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
alias ll='ls -la'
""")
        result = analyze_bash(tmp_path)
        aliases = [s for s in result.symbols if s.kind == "alias"]
        assert len(aliases) >= 1
        assert any(a.name == "ll" for a in aliases)

    def test_alias_double_quotes(self, tmp_path: Path) -> None:
        """Test alias with double quotes."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
alias grep="grep --color=auto"
""")
        result = analyze_bash(tmp_path)
        aliases = [s for s in result.symbols if s.kind == "alias"]
        assert len(aliases) >= 1


class TestSourceEdges:
    """Branch coverage for source edge extraction."""

    def test_source_command(self, tmp_path: Path) -> None:
        """Test source command creates edge."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
source ./common.sh

main() {
    echo "main"
}
""")
        result = analyze_bash(tmp_path)
        source_edges = [e for e in result.edges if e.edge_type == "sources"]
        assert len(source_edges) >= 1

    def test_dot_source(self, tmp_path: Path) -> None:
        """Test . (dot) source command."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
. ./utils.sh

main() {
    echo "main"
}
""")
        result = analyze_bash(tmp_path)
        source_edges = [e for e in result.edges if e.edge_type == "sources"]
        assert len(source_edges) >= 1


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call_creates_edge(self, tmp_path: Path) -> None:
        """Test function call creates calls edge."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
helper() {
    echo "helper"
}

main() {
    helper
}
""")
        result = analyze_bash(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1


class TestFindBashFiles:
    """Branch coverage for file discovery."""

    def test_finds_sh_files(self, tmp_path: Path) -> None:
        """Test .sh files are discovered."""
        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hello")

        files = find_bash_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".sh" for f in files)

    def test_finds_bash_files(self, tmp_path: Path) -> None:
        """Test .bash files are discovered."""
        (tmp_path / "script.bash").write_text("#!/bin/bash\necho hello")

        files = find_bash_files(tmp_path)
        assert len(files) >= 1
        assert any(f.suffix == ".bash" for f in files)

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "build.sh").write_text("#!/bin/bash\necho hello")

        files = find_bash_files(tmp_path)
        assert len(files) >= 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_bash_files(self, tmp_path: Path) -> None:
        """Test directory with no Bash files."""
        result = analyze_bash(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_bash(self, tmp_path: Path) -> None:
        """Test minimal Bash file."""
        make_bash_file(tmp_path, "min.sh", """#!/bin/bash
echo "hello"
""")
        result = analyze_bash(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_bash_file(tmp_path, "script.sh", """#!/bin/bash
main() {
    echo "main"
}
""")
        result = analyze_bash(tmp_path)
        assert result.run is not None
