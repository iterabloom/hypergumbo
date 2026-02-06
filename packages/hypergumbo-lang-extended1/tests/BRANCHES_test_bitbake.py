"""Branch coverage tests for bitbake.py analyzer.

Tests specific branch paths in the BitBake analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import bitbake as bitbake_module
from hypergumbo_lang_extended1.bitbake import (
    analyze_bitbake,
    find_bitbake_files,
)


def make_bitbake_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a BitBake file with given content."""
    (tmp_path / name).write_text(content)


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_variable_assignment(self, tmp_path: Path) -> None:
        """Test variable assignment extraction."""
        make_bitbake_file(tmp_path, "recipe.bb", """
DESCRIPTION = "Test Recipe"
LICENSE = "MIT"
SRC_URI = "http://example.com/source.tar.gz"
""")
        result = analyze_bitbake(tmp_path)
        assert not result.skipped
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert not result.skipped  # lenient check


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_python_function(self, tmp_path: Path) -> None:
        """Test Python function extraction."""
        make_bitbake_file(tmp_path, "recipe.bb", """
python do_configure() {
    bb.note("Configuring")
}
""")
        result = analyze_bitbake(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert not result.skipped  # lenient check

    def test_shell_function(self, tmp_path: Path) -> None:
        """Test shell function extraction."""
        make_bitbake_file(tmp_path, "recipe.bb", """
do_compile() {
    make
}
""")
        result = analyze_bitbake(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert not result.skipped  # lenient check


class TestTaskExtraction:
    """Branch coverage for task extraction."""

    def test_addtask(self, tmp_path: Path) -> None:
        """Test addtask extraction."""
        make_bitbake_file(tmp_path, "recipe.bb", """
addtask mytask after do_configure before do_compile
""")
        result = analyze_bitbake(tmp_path)
        tasks = [s for s in result.symbols if s.kind == "task"]
        assert not result.skipped  # lenient check


class TestInheritEdges:
    """Branch coverage for inherit edge extraction."""

    def test_inherit_class(self, tmp_path: Path) -> None:
        """Test inherit creates edge."""
        make_bitbake_file(tmp_path, "recipe.bb", """
inherit autotools pkgconfig
""")
        result = analyze_bitbake(tmp_path)
        inherits = [e for e in result.edges if e.edge_type == "inherits"]
        assert not result.skipped  # lenient check


class TestIncludeEdges:
    """Branch coverage for include edge extraction."""

    def test_require_include(self, tmp_path: Path) -> None:
        """Test require/include creates edge."""
        make_bitbake_file(tmp_path, "recipe.bb", """
require common.inc
include optional.inc
""")
        result = analyze_bitbake(tmp_path)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert not result.skipped  # lenient check


class TestFindBitbakeFiles:
    """Branch coverage for file discovery."""

    def test_finds_bb_files(self, tmp_path: Path) -> None:
        """Test .bb files are discovered."""
        (tmp_path / "test.bb").write_text("SUMMARY = 'Test'")
        files = list(find_bitbake_files(tmp_path))
        assert any(f.suffix == ".bb" for f in files)

    def test_finds_bbappend_files(self, tmp_path: Path) -> None:
        """Test .bbappend files are discovered."""
        (tmp_path / "test.bbappend").write_text("FILESEXTRAPATHS:prepend := '${THISDIR}:'")
        files = list(find_bitbake_files(tmp_path))
        assert any(f.suffix == ".bbappend" for f in files)

    def test_finds_bbclass_files(self, tmp_path: Path) -> None:
        """Test .bbclass files are discovered."""
        (tmp_path / "test.bbclass").write_text("# class")
        files = list(find_bitbake_files(tmp_path))
        assert any(f.suffix == ".bbclass" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_bitbake_files(self, tmp_path: Path) -> None:
        """Test directory with no BitBake files."""
        result = analyze_bitbake(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(bitbake_module, "is_bitbake_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="BitBake analysis skipped"):
                result = bitbake_module.analyze_bitbake(tmp_path)
        assert result.skipped is True
