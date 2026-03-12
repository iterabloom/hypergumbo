# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for matlab.py analyzer.

Tests specific branch paths in the MATLAB analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function definition extraction
- Class definition extraction
- Method extraction within classes
- Function call edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.matlab import (
    analyze_matlab,
    find_matlab_files,
)


def make_matlab_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a MATLAB file with given content."""
    (tmp_path / name).write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function definition extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_matlab_file(tmp_path, "add.m", """
function result = add(a, b)
    result = a + b;
end
""")
        result = analyze_matlab(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1
        assert any(f.name == "add" for f in funcs)

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function definitions."""
        make_matlab_file(tmp_path, "math.m", """
function y = square(x)
    y = x * x;
end

function y = cube(x)
    y = x * x * x;
end
""")
        result = analyze_matlab(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "square" in names
        assert "cube" in names

    def test_function_with_output(self, tmp_path: Path) -> None:
        """Test function with output variable."""
        make_matlab_file(tmp_path, "compute.m", """
function result = compute(x)
    result = x * 2;
end
""")
        result = analyze_matlab(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "compute"]
        assert len(funcs) == 1
        assert funcs[0].signature is not None
        assert "result" in funcs[0].signature


class TestClassExtraction:
    """Branch coverage for class definition extraction."""

    def test_class_definition(self, tmp_path: Path) -> None:
        """Test class definition extraction."""
        make_matlab_file(tmp_path, "Point.m", """
classdef Point
    properties
        x
        y
    end
    methods
        function obj = Point(x, y)
            obj.x = x;
            obj.y = y;
        end
    end
end
""")
        result = analyze_matlab(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) >= 1
        assert any(c.name == "Point" for c in classes)

    def test_class_with_metadata(self, tmp_path: Path) -> None:
        """Test class metadata (property/method counts)."""
        make_matlab_file(tmp_path, "Vector.m", """
classdef Vector
    properties
        x
        y
        z
    end
    methods
        function obj = Vector(x, y, z)
            obj.x = x;
            obj.y = y;
            obj.z = z;
        end
        function m = magnitude(obj)
            m = sqrt(obj.x^2 + obj.y^2 + obj.z^2);
        end
    end
end
""")
        result = analyze_matlab(tmp_path)
        classes = [s for s in result.symbols if s.kind == "class" and s.name == "Vector"]
        assert len(classes) == 1
        assert classes[0].meta is not None
        assert classes[0].meta.get("property_count") == 3
        assert classes[0].meta.get("method_count") == 2


class TestMethodExtraction:
    """Branch coverage for method extraction within classes."""

    def test_method_extraction(self, tmp_path: Path) -> None:
        """Test method extraction from class."""
        make_matlab_file(tmp_path, "Counter.m", """
classdef Counter
    properties
        value
    end
    methods
        function obj = Counter()
            obj.value = 0;
        end
        function increment(obj)
            obj.value = obj.value + 1;
        end
    end
end
""")
        result = analyze_matlab(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        names = [m.name for m in methods]
        assert "Counter" in names
        assert "increment" in names


class TestFunctionCallEdges:
    """Branch coverage for function call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_matlab_file(tmp_path, "caller.m", """
function result = caller()
    result = helper(42);
end

function y = helper(x)
    y = x * 2;
end
""")
        result = analyze_matlab(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_unresolved_call(self, tmp_path: Path) -> None:
        """Test call to unknown function creates unresolved edge."""
        make_matlab_file(tmp_path, "caller.m", """
function result = caller()
    result = unknown_func(42);
end
""")
        result = analyze_matlab(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        unresolved = [e for e in call_edges if "unresolved" in e.dst]
        assert len(unresolved) >= 1


class TestFindMatlabFiles:
    """Branch coverage for file discovery."""

    def test_finds_m_files(self, tmp_path: Path) -> None:
        """Test .m files are discovered."""
        (tmp_path / "test.m").write_text("function f()\nend")

        files = list(find_matlab_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".m"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        src = tmp_path / "src" / "utils"
        src.mkdir(parents=True)
        (src / "helper.m").write_text("function f()\nend")

        files = list(find_matlab_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "helper.m"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_minimal_function(self, tmp_path: Path) -> None:
        """Test minimal MATLAB function."""
        make_matlab_file(tmp_path, "min.m", """
function f()
end
""")
        result = analyze_matlab(tmp_path)
        assert not result.skipped

    def test_no_matlab_files(self, tmp_path: Path) -> None:
        """Test directory with no MATLAB files."""
        result = analyze_matlab(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_matlab_file(tmp_path, "test.m", """
function f()
end
""")
        result = analyze_matlab(tmp_path)
        assert result.run is not None


class TestCrossFileResolution:
    """Branch coverage for cross-file symbol resolution."""

    def test_two_file_resolution(self, tmp_path: Path) -> None:
        """Test call resolution across two files."""
        make_matlab_file(tmp_path, "utils.m", """
function y = helper(x)
    y = x * 2;
end
""")
        make_matlab_file(tmp_path, "main.m", """
function result = main()
    result = helper(21);
end
""")
        result = analyze_matlab(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "helper" in names
        assert "main" in names
