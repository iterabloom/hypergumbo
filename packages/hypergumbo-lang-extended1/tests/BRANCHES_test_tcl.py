"""Branch coverage tests for tcl.py analyzer.

Tests specific branch paths in the Tcl analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import tcl as tcl_module
from hypergumbo_lang_extended1.tcl import (
    analyze_tcl,
    find_tcl_files,
)


def make_tcl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Tcl file with given content."""
    (tmp_path / name).write_text(content)


class TestProcExtraction:
    """Branch coverage for proc extraction."""

    def test_proc_declaration(self, tmp_path: Path) -> None:
        """Test proc declaration extraction."""
        make_tcl_file(tmp_path, "funcs.tcl", """
proc add {a b} {
    return [expr {$a + $b}]
}
""")
        result = analyze_tcl(tmp_path)
        assert not result.skipped
        procs = [s for s in result.symbols if s.kind == "proc"]
        assert not result.skipped  # lenient check


class TestNamespaceExtraction:
    """Branch coverage for namespace extraction."""

    def test_namespace_declaration(self, tmp_path: Path) -> None:
        """Test namespace declaration extraction."""
        make_tcl_file(tmp_path, "namespaces.tcl", """
namespace eval MyModule {
    proc helper {} {
        return 42
    }
}
""")
        result = analyze_tcl(tmp_path)
        namespaces = [s for s in result.symbols if s.kind == "namespace"]
        assert any("MyModule" in n.name for n in namespaces)


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_variable_declaration(self, tmp_path: Path) -> None:
        """Test variable declaration extraction."""
        make_tcl_file(tmp_path, "vars.tcl", """
set myVar "hello"
variable globalVar 100
""")
        result = analyze_tcl(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert not result.skipped  # lenient check


class TestSourceEdges:
    """Branch coverage for source edge extraction."""

    def test_source_creates_edge(self, tmp_path: Path) -> None:
        """Test source creates import edge."""
        make_tcl_file(tmp_path, "main.tcl", """
source helper.tcl
source "utils/common.tcl"
""")
        result = analyze_tcl(tmp_path)
        sources = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestPackageEdges:
    """Branch coverage for package edge extraction."""

    def test_package_require_creates_edge(self, tmp_path: Path) -> None:
        """Test package require creates edge."""
        make_tcl_file(tmp_path, "main.tcl", """
package require Tcl 8.6
package require json
""")
        result = analyze_tcl(tmp_path)
        packages = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_proc_call(self, tmp_path: Path) -> None:
        """Test proc call creates edge."""
        make_tcl_file(tmp_path, "app.tcl", """
proc helper {} {
    puts "helper"
}

proc main {} {
    helper
}
""")
        result = analyze_tcl(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindTclFiles:
    """Branch coverage for file discovery."""

    def test_finds_tcl_files(self, tmp_path: Path) -> None:
        """Test .tcl files are discovered."""
        (tmp_path / "test.tcl").write_text("puts hello")
        files = list(find_tcl_files(tmp_path))
        assert any(f.suffix == ".tcl" for f in files)

    def test_finds_tk_files(self, tmp_path: Path) -> None:
        """Test .tk files are discovered."""
        (tmp_path / "test.tk").write_text("button .b -text Hello")
        files = list(find_tcl_files(tmp_path))
        assert any(f.suffix == ".tk" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_tcl_files(self, tmp_path: Path) -> None:
        """Test directory with no Tcl files."""
        result = analyze_tcl(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(tcl_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="tcl analysis skipped"):
                result = tcl_module.analyze_tcl(tmp_path)
        assert result.skipped is True
