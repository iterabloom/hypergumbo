"""Tests for R language analyzer using tree-sitter.

Tests verify that the analyzer correctly extracts:
- Function definitions (function <- function() {})
- Library/require imports
- source() file references
- Function calls
"""

from hypergumbo.analyze.r_lang import (
    PASS_ID,
    PASS_VERSION,
    RAnalysisResult,
    analyze_r_files,
    find_r_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "r-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_function_definition(tmp_path):
    """Test detection of function definitions."""
    r_file = tmp_path / "script.R"
    r_file.write_text("""
my_function <- function(x, y) {
  return(x + y)
}

another <- function(data) {
  data * 2
}
""")
    result = analyze_r_files(tmp_path)

    assert not result.skipped
    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 2

    my_func = next((f for f in functions if f.name == "my_function"), None)
    assert my_func is not None
    assert my_func.language == "r"

    another_func = next((f for f in functions if f.name == "another"), None)
    assert another_func is not None


def test_analyze_function_with_equals(tmp_path):
    """Test function definition with = assignment."""
    r_file = tmp_path / "script.R"
    r_file.write_text("""
my_func = function(x) {
  x * 2
}
""")
    result = analyze_r_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    assert functions[0].name == "my_func"


def test_analyze_library_imports(tmp_path):
    """Test detection of library() imports."""
    r_file = tmp_path / "script.R"
    r_file.write_text("""
library(ggplot2)
library(dplyr)
require(tidyr)
""")
    result = analyze_r_files(tmp_path)

    imports = [s for s in result.symbols if s.kind == "import"]
    assert len(imports) >= 3

    ggplot_import = next((i for i in imports if i.name == "ggplot2"), None)
    assert ggplot_import is not None

    dplyr_import = next((i for i in imports if i.name == "dplyr"), None)
    assert dplyr_import is not None


def test_analyze_source_imports(tmp_path):
    """Test detection of source() file references."""
    r_file = tmp_path / "script.R"
    r_file.write_text("""
source("utils.R")
source("lib/helpers.R")
""")
    result = analyze_r_files(tmp_path)

    sources = [s for s in result.symbols if s.kind == "source"]
    assert len(sources) >= 2

    utils_src = next((s for s in sources if s.name == "utils.R"), None)
    assert utils_src is not None


def test_analyze_function_calls(tmp_path):
    """Test detection of function calls."""
    r_file = tmp_path / "script.R"
    r_file.write_text("""
helper <- function(x) {
  x * 2
}

main <- function() {
  result <- helper(5)
  print(result)
}
""")
    result = analyze_r_files(tmp_path)

    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert len(calls) >= 1

    # Check for call to helper
    helper_call = next((c for c in calls if "helper" in c.dst), None)
    assert helper_call is not None


def test_find_r_files(tmp_path):
    """Test that R files are discovered correctly."""
    (tmp_path / "script.R").write_text("x <- 1")
    (tmp_path / "analysis.r").write_text("y <- 2")
    (tmp_path / "not_r.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "utils.R").write_text("z <- 3")

    files = list(find_r_files(tmp_path))
    # Should find only .R and .r files
    assert len(files) == 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no R files."""
    result = analyze_r_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    r_file = tmp_path / "script.R"
    r_file.write_text("f <- function() {}")

    result = analyze_r_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_span_information(tmp_path):
    """Test that span information is correct."""
    r_file = tmp_path / "script.R"
    r_file.write_text("""f <- function() {
  1 + 1
}
""")
    result = analyze_r_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    assert functions[0].span is not None
    assert functions[0].span.start_line >= 1


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    r_file = tmp_path / "broken.R"
    r_file.write_text("function( {{{{")

    # Should not raise an exception
    result = analyze_r_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, RAnalysisResult)


def test_pipe_operator_in_function(tmp_path):
    """Test function with pipe operators."""
    r_file = tmp_path / "script.R"
    r_file.write_text("""
library(dplyr)

process_data <- function(data) {
  data %>%
    filter(value > 0) %>%
    mutate(new_col = value * 2)
}
""")
    result = analyze_r_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    assert functions[0].name == "process_data"


class TestRSignatureExtraction:
    """Tests for R function signature extraction."""

    def test_function_with_params(self, tmp_path):
        """Extract signature for function with parameters."""
        r_file = tmp_path / "calc.R"
        r_file.write_text("""
add <- function(x, y) {
  x + y
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x, y)"

    def test_function_no_params(self, tmp_path):
        """Extract signature for function with no parameters."""
        r_file = tmp_path / "constant.R"
        r_file.write_text("""
get_answer <- function() {
  42
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "get_answer"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_function_with_defaults(self, tmp_path):
        """Extract signature for function with default values."""
        r_file = tmp_path / "opts.R"
        r_file.write_text("""
greet <- function(name, greeting = "Hello") {
  paste(greeting, name)
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "greet"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(name, greeting = ...)"

    def test_function_single_param(self, tmp_path):
        """Extract signature for function with single parameter."""
        r_file = tmp_path / "double.R"
        r_file.write_text("""
double_it <- function(x) {
  x * 2
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "double_it"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x)"
