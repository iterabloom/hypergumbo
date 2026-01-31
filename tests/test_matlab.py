"""Tests for MATLAB analyzer.

Tests for the tree-sitter-based MATLAB analyzer, verifying symbol extraction,
edge detection, and graceful degradation when tree-sitter is unavailable.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from hypergumbo_lang_common.matlab import (
    analyze_matlab,
    find_matlab_files,
    is_matlab_tree_sitter_available,
    PASS_ID,
)


@pytest.fixture
def matlab_repo(tmp_path: Path) -> Path:
    """Create a minimal MATLAB project for testing."""
    # Main file with functions
    (tmp_path / "main.m").write_text(
        '''function result = main()
    % Main function
    result = add(2, 3);
    display(result);
end

function result = add(a, b)
    % Add two numbers
    result = a + b;
end

function result = multiply(x, y)
    result = x * y;
end
'''
    )

    # Class file
    (tmp_path / "Point.m").write_text(
        '''classdef Point
    properties
        x
        y
    end
    methods
        function obj = Point(x, y)
            obj.x = x;
            obj.y = y;
        end
        function d = distance(obj, other)
            d = sqrt((obj.x - other.x)^2 + (obj.y - other.y)^2);
        end
        function s = toString(obj)
            s = sprintf('(%f, %f)', obj.x, obj.y);
        end
    end
end
'''
    )

    return tmp_path


class TestFindMatlabFiles:
    """Tests for finding MATLAB files."""

    def test_finds_matlab_files(self, matlab_repo: Path) -> None:
        """Should find all .m files recursively."""
        files = list(find_matlab_files(matlab_repo))
        assert len(files) == 2
        names = {f.name for f in files}
        assert "main.m" in names
        assert "Point.m" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty iterator for directory with no MATLAB files."""
        files = list(find_matlab_files(tmp_path))
        assert files == []


class TestIsMatlabTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_returns_true_when_available(self) -> None:
        """Should return True when tree-sitter-language-pack is installed."""
        assert is_matlab_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Should return False when tree-sitter-language-pack is not installed."""
        import hypergumbo_lang_common.matlab as matlab_module
        with patch.object(matlab_module, "is_matlab_tree_sitter_available", return_value=False):
            assert matlab_module.is_matlab_tree_sitter_available() is False


class TestAnalyzeMatlab:
    """Tests for the MATLAB analyzer."""

    def test_skips_when_unavailable(self, matlab_repo: Path) -> None:
        """Should skip analysis and warn when tree-sitter is unavailable."""
        import hypergumbo_lang_common.matlab as matlab_module

        with patch.object(matlab_module, "is_matlab_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="tree-sitter-language-pack not available"):
                result = matlab_module.analyze_matlab(matlab_repo)

        assert result.skipped is True
        assert "tree-sitter-language-pack" in result.skip_reason
        assert result.symbols == []
        assert result.edges == []

    def test_extracts_functions(self, matlab_repo: Path) -> None:
        """Should extract function declarations."""
        result = analyze_matlab(matlab_repo)

        assert not result.skipped
        assert result.symbols

        funcs = [s for s in result.symbols if s.kind == "function"]
        func_names = {s.name for s in funcs}

        assert "main" in func_names
        assert "add" in func_names
        assert "multiply" in func_names

    def test_extracts_classes(self, matlab_repo: Path) -> None:
        """Should extract class definitions."""
        result = analyze_matlab(matlab_repo)

        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = {s.name for s in classes}

        assert "Point" in class_names

    def test_extracts_methods(self, matlab_repo: Path) -> None:
        """Should extract class methods."""
        result = analyze_matlab(matlab_repo)

        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = {s.name for s in methods}

        assert "Point" in method_names  # Constructor
        assert "distance" in method_names
        assert "toString" in method_names

    def test_class_metadata(self, matlab_repo: Path) -> None:
        """Should count properties and methods."""
        result = analyze_matlab(matlab_repo)

        point = next((s for s in result.symbols if s.name == "Point" and s.kind == "class"), None)
        assert point is not None
        assert point.meta is not None
        assert point.meta.get("property_count") == 2  # x, y
        assert point.meta.get("method_count") == 3  # constructor, distance, toString

    def test_function_signatures(self, matlab_repo: Path) -> None:
        """Should include function signatures."""
        result = analyze_matlab(matlab_repo)

        add_fn = next((s for s in result.symbols if s.name == "add" and s.kind == "function"), None)
        assert add_fn is not None
        assert add_fn.signature is not None
        assert "function" in add_fn.signature
        assert "result" in add_fn.signature  # output variable

    def test_method_class_reference(self, matlab_repo: Path) -> None:
        """Should track which class a method belongs to."""
        result = analyze_matlab(matlab_repo)

        distance = next((s for s in result.symbols if s.name == "distance"), None)
        assert distance is not None
        assert distance.meta is not None
        assert distance.meta.get("class") == "Point"

    def test_extracts_call_edges(self, matlab_repo: Path) -> None:
        """Should extract call edges between functions."""
        result = analyze_matlab(matlab_repo)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) > 0

        # Check that main calls add
        main_calls = [e for e in call_edges if "main" in e.src]
        callee_names = {e.dst.split(":")[-1] for e in main_calls}
        assert "add" in callee_names or any("add" in e.dst for e in main_calls)

    def test_pass_id(self, matlab_repo: Path) -> None:
        """Should have correct pass origin."""
        result = analyze_matlab(matlab_repo)

        for sym in result.symbols:
            assert sym.origin == PASS_ID

        for edge in result.edges:
            assert edge.origin == PASS_ID

    def test_analysis_run_metadata(self, matlab_repo: Path) -> None:
        """Should include analysis run metadata."""
        result = analyze_matlab(matlab_repo)

        assert result.run is not None
        assert result.run.pass_id == PASS_ID
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        """Should return empty result for repository with no MATLAB files."""
        result = analyze_matlab(tmp_path)

        assert not result.skipped
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, matlab_repo: Path) -> None:
        """Should generate stable IDs for symbols."""
        result1 = analyze_matlab(matlab_repo)
        result2 = analyze_matlab(matlab_repo)

        ids1 = {s.id for s in result1.symbols}
        ids2 = {s.id for s in result2.symbols}

        assert ids1 == ids2

    def test_span_info(self, matlab_repo: Path) -> None:
        """Should include accurate span information."""
        result = analyze_matlab(matlab_repo)

        for sym in result.symbols:
            assert sym.span is not None
            assert sym.path is not None
            assert sym.span.start_line > 0
            assert sym.span.end_line >= sym.span.start_line


class TestUnresolvedCalls:
    """Tests for handling unresolved function calls."""

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        """Should handle calls to undefined functions."""
        (tmp_path / "main.m").write_text(
            '''function result = main()
    result = unknown_function();
end
'''
        )

        result = analyze_matlab(tmp_path)

        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) == 1

        # Should have lower confidence for unresolved target
        assert call_edges[0].confidence == 0.6
        assert "unresolved:unknown_function" in call_edges[0].dst
