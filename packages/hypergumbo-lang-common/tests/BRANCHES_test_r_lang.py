"""Branch coverage tests for r_lang.py analyzer.

Tests specific branch paths in the R language analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Function extraction
- Library/require imports
- Source file imports
- Function call edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.r_lang import (
    _make_symbol_id,
    _make_edge_id,
    analyze_r_files,
    find_r_files,
)


def make_r_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an R file with given content."""
    (tmp_path / name).write_text(content)


class TestRHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = _make_symbol_id("utils.R", 1, 10, "myFunc", "function")
        assert symbol_id == "r:utils.R:1-10:myFunc:function"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id_1 = _make_edge_id("src1", "dst1", "calls")
        edge_id_2 = _make_edge_id("src1", "dst1", "calls")
        assert edge_id_1 == edge_id_2
        assert edge_id_1.startswith("edge:sha256:")


class TestFunctionExtraction:
    """Branch coverage for function definition extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_r_file(tmp_path, "utils.R", """
myFunc <- function(x) {
    x * 2
}
""")
        result = analyze_r_files(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1
        assert any(f.name == "myFunc" for f in funcs)

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with multiple parameters."""
        make_r_file(tmp_path, "math.R", """
add <- function(a, b) {
    a + b
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) >= 1
        assert funcs[0].signature is not None

    def test_function_with_default_params(self, tmp_path: Path) -> None:
        """Test function with default parameter values."""
        make_r_file(tmp_path, "utils.R", """
greet <- function(name, greeting = "Hello") {
    paste(greeting, name)
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "greet"]
        assert len(funcs) >= 1

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function extraction."""
        make_r_file(tmp_path, "math.R", """
square <- function(x) { x * x }
cube <- function(x) { x * x * x }
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "square" in names
        assert "cube" in names


class TestLibraryImports:
    """Branch coverage for library/require imports."""

    def test_library_import(self, tmp_path: Path) -> None:
        """Test library import extraction."""
        make_r_file(tmp_path, "analysis.R", """
library(dplyr)
library(ggplot2)
""")
        result = analyze_r_files(tmp_path)
        imports = [s for s in result.symbols if s.kind == "import"]
        names = [i.name for i in imports]
        assert "dplyr" in names
        assert "ggplot2" in names

    def test_require_import(self, tmp_path: Path) -> None:
        """Test require import extraction."""
        make_r_file(tmp_path, "utils.R", """
require(tidyr)
""")
        result = analyze_r_files(tmp_path)
        imports = [s for s in result.symbols if s.kind == "import"]
        assert any(i.name == "tidyr" for i in imports)


class TestSourceImports:
    """Branch coverage for source() imports."""

    def test_source_import(self, tmp_path: Path) -> None:
        """Test source file import extraction."""
        make_r_file(tmp_path, "main.R", """
source("utils.R")
source("helpers.R")
""")
        result = analyze_r_files(tmp_path)
        sources = [s for s in result.symbols if s.kind == "source"]
        names = [s.name for s in sources]
        assert "utils.R" in names
        assert "helpers.R" in names


class TestFunctionCallEdges:
    """Branch coverage for function call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same file."""
        make_r_file(tmp_path, "utils.R", """
helper <- function(x) { x * 2 }

main <- function() {
    helper(21)
}
""")
        result = analyze_r_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

    def test_builtin_call(self, tmp_path: Path) -> None:
        """Test call to builtin function creates edge."""
        make_r_file(tmp_path, "utils.R", """
main <- function(x) {
    sqrt(x)
}
""")
        result = analyze_r_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        builtin_calls = [e for e in call_edges if "builtin" in e.dst]
        assert len(builtin_calls) >= 1


class TestNamespaceQualifiedCalls:
    """Branch coverage for namespace-qualified calls."""

    def test_qualified_call(self, tmp_path: Path) -> None:
        """Test namespace-qualified function call."""
        make_r_file(tmp_path, "main.R", """
process <- function(df) {
    dplyr::filter(df, x > 0)
}
""")
        result = analyze_r_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        qualified_calls = [e for e in call_edges if "dplyr" in e.dst]
        assert len(qualified_calls) >= 1


class TestFindRFiles:
    """Branch coverage for file discovery."""

    def test_finds_R_files(self, tmp_path: Path) -> None:
        """Test .R files are discovered."""
        (tmp_path / "utils.R").write_text("x <- 1")

        files = list(find_r_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".R"

    def test_finds_r_lowercase_files(self, tmp_path: Path) -> None:
        """Test .r files (lowercase) are discovered."""
        (tmp_path / "utils.r").write_text("x <- 1")

        files = list(find_r_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".r"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        src = tmp_path / "R"
        src.mkdir()
        (src / "utils.R").write_text("x <- 1")

        files = list(find_r_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "utils.R"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_r_files(self, tmp_path: Path) -> None:
        """Test directory with no R files."""
        result = analyze_r_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_function(self, tmp_path: Path) -> None:
        """Test minimal R function."""
        make_r_file(tmp_path, "min.R", """
f <- function() {}
""")
        result = analyze_r_files(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_r_file(tmp_path, "utils.R", """
myFunc <- function(x) { x * 2 }
""")
        result = analyze_r_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestCrossFileResolution:
    """Branch coverage for cross-file symbol resolution."""

    def test_two_file_resolution(self, tmp_path: Path) -> None:
        """Test resolution across two files."""
        make_r_file(tmp_path, "utils.R", """
helper <- function(x) { x * 2 }
""")
        make_r_file(tmp_path, "main.R", """
source("utils.R")

main <- function() {
    helper(21)
}
""")
        result = analyze_r_files(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "helper" in names
        assert "main" in names


class TestFunctionSignatures:
    """Branch coverage for function signature extraction."""

    def test_signature_format(self, tmp_path: Path) -> None:
        """Test function signature format."""
        make_r_file(tmp_path, "utils.R", """
process <- function(data, n, verbose) {
    data
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "process"]
        assert len(funcs) >= 1
        assert funcs[0].signature is not None
