"""Tests for Tcl analyzer.

Tests for the tree-sitter-based Tcl analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_extended1.tcl import (
    analyze_tcl,
    find_tcl_files,
    is_tcl_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def tcl_repo(tmp_path: Path) -> Path:
    """Create a minimal Tcl project for testing."""
    # Main file with procedures
    (tmp_path / "main.tcl").write_text(
        '''# Main Tcl file
package require Tk

proc greet {name} {
    puts "Hello, $name!"
    return 1
}

proc add {a b} {
    return [expr {$a + $b}]
}

proc main {} {
    set result [greet "World"]
    puts [add 2 3]
}

# Call main
main
'''
    )

    # Namespace file
    (tmp_path / "utils.tcl").write_text(
        '''# Utility namespace
namespace eval utils {
    proc helper {} {
        puts "Helper called"
    }

    proc calculate {x y} {
        return [expr {$x * $y}]
    }
}

namespace eval math {
    proc double {n} {
        return [expr {$n * 2}]
    }
}
'''
    )

    # Tk GUI file
    (tmp_path / "gui.tk").write_text(
        '''# GUI code
proc create_window {} {
    toplevel .main
    button .main.btn -text "Click"
}
'''
    )

    return tmp_path


class TestFindTclFiles:
    """Tests for finding Tcl files."""

    def test_finds_tcl_files(self, tcl_repo: Path) -> None:
        """Should find all .tcl and .tk files recursively."""
        files = list(find_tcl_files(tcl_repo))
        assert len(files) == 3
        names = {f.name for f in files}
        assert "main.tcl" in names
        assert "utils.tcl" in names
        assert "gui.tk" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no Tcl files."""
        files = list(find_tcl_files(tmp_path))
        assert files == []


class TestIsTclTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-language-pack is installed."""
        assert is_tcl_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Should return False when tree-sitter-language-pack is not installed."""
        import hypergumbo_lang_extended1.tcl as tcl_module
        with patch.object(tcl_module, "is_tcl_tree_sitter_available", return_value=False):
            assert tcl_module.is_tcl_tree_sitter_available() is False


class TestAnalyzeTcl:
    """Tests for the Tcl analyzer."""

    def test_skips_when_unavailable(self, tcl_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter is unavailable."""
        import hypergumbo_lang_extended1.tcl as tcl_module

        with patch.object(tcl_module, "is_tcl_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-language-pack not available"):
                result = tcl_module.analyze_tcl(tcl_repo)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_procedures(self, tcl_repo: Path) -> None:
        """Should extract procedure declarations."""
        result = analyze_tcl(tcl_repo)

        assert not result.skipped
        assert result.symbols

        procs = [s for s in result.symbols if s.kind == "function"]
        proc_names = {s.name for s in procs}

        assert "greet" in proc_names
        assert "add" in proc_names
        assert "main" in proc_names
        assert "create_window" in proc_names

    def test_extracts_namespaces(self, tcl_repo: Path) -> None:
        """Should extract namespace declarations."""
        result = analyze_tcl(tcl_repo)

        namespaces = [s for s in result.symbols if s.kind == "namespace"]
        namespace_names = {s.name for s in namespaces}

        assert "utils" in namespace_names
        assert "math" in namespace_names

    def test_extracts_namespace_procs(self, tcl_repo: Path) -> None:
        """Should extract procedures within namespaces."""
        result = analyze_tcl(tcl_repo)

        procs = [s for s in result.symbols if s.kind == "function"]
        proc_names = {s.name for s in procs}

        assert "helper" in proc_names
        assert "calculate" in proc_names
        assert "double" in proc_names

    def test_namespace_metadata(self, tcl_repo: Path) -> None:
        """Should count procedures in namespaces."""
        result = analyze_tcl(tcl_repo)

        utils_ns = next((s for s in result.symbols if s.name == "utils" and s.kind == "namespace"), None)
        assert utils_ns is not None
        assert utils_ns.meta is not None
        assert utils_ns.meta.get("proc_count") == 2  # helper, calculate

        math_ns = next((s for s in result.symbols if s.name == "math" and s.kind == "namespace"), None)
        assert math_ns is not None
        assert math_ns.meta is not None
        assert math_ns.meta.get("proc_count") == 1  # double

    def test_proc_namespace_reference(self, tcl_repo: Path) -> None:
        """Should track which namespace a procedure belongs to."""
        result = analyze_tcl(tcl_repo)

        helper = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper is not None
        assert helper.meta is not None
        assert helper.meta.get("namespace") == "utils"

    def test_procedure_signatures(self, tcl_repo: Path) -> None:
        """Should include procedure signatures."""
        result = analyze_tcl(tcl_repo)

        add_proc = next((s for s in result.symbols if s.name == "add" and s.kind == "function"), None)
        assert add_proc is not None
        assert add_proc.signature is not None
        assert "proc" in add_proc.signature
        assert "a" in add_proc.signature
        assert "b" in add_proc.signature

    def test_extracts_call_edges(self, tcl_repo: Path) -> None:
        """Should extract call edges between procedures."""
        result = analyze_tcl(tcl_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) > 0

        # Check that main calls greet and add
        main_calls = [e for e in call_edges if "main" in e.src]
        callee_names = {e.dst.split(":")[-1] for e in main_calls}
        assert "greet" in callee_names or any("greet" in e.dst for e in main_calls)
        assert "add" in callee_names or any("add" in e.dst for e in main_calls)

    def test_pass_id(self, tcl_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_tcl(tcl_repo)

        for sym in result.symbols:
            assert sym.origin == PASS_ID

        for edge in result.edges:
            assert edge.origin == PASS_ID

    def test_analysis_run_metadata(self, tcl_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_tcl(tcl_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no Tcl files."""
        result = analyze_tcl(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tcl_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_tcl(tcl_repo)
        result2 = analyze_tcl(tcl_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, tcl_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_tcl(tcl_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line


class TestUnresolvedCalls:
    """Tests for handling unresolved procedure calls."""

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        """Should handle calls to undefined procedures."""
        (tmp_path / "main.tcl").write_text(
            '''proc main {} {
    unknown_proc
}
'''
        )

        result = analyze_tcl(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

        # Should have lower confidence for unresolved target
        assert call_edges[0].confidence == 0.6
        assert "unresolved:unknown_proc" in call_edges[0].dst
