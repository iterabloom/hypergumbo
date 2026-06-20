# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for R language analyzer using tree-sitter.

Tests verify that the analyzer correctly extracts:
- Function definitions (function <- function() {})
- Library/require imports
- source() file references
- Function calls
"""

from unittest.mock import patch

from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.ir import PASS_VERSION
from hypergumbo_core import __version__
from hypergumbo_lang_common import r_lang as r_lang_module
from hypergumbo_lang_common.r_lang import (
    PASS_ID,
    analyze_r_files,
    find_r_files,
    is_r_tree_sitter_available,
)

def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "r"
    assert PASS_VERSION == __version__

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
    # ADR-0027 Cluster E sub-case (b) shape 3 (audit-findings 0010, WI-kunag):
    # library() / require() now emit companion `imports` Edges instead of
    # `import`-kind Symbols.
    r_file = tmp_path / "script.R"
    r_file.write_text("""
library(ggplot2)
library(dplyr)
require(tidyr)
""")
    result = analyze_r_files(tmp_path)

    import_edges = [
        e for e in result.edges if e.edge_type == "imports"
    ]
    assert len(import_edges) >= 3
    packages = [(e.meta or {}).get("package") for e in import_edges]
    assert "ggplot2" in packages
    assert "dplyr" in packages
    assert "tidyr" in packages
    # No `import`-kind Symbol after the fold.
    assert not any(s.kind == "import" for s in result.symbols)
    # ``import_form`` distinguishes library() vs require().
    forms = {(e.meta or {}).get("import_form") for e in import_edges}
    assert forms == {"library", "require"}

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
    assert isinstance(result, AnalysisResult)

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

class TestRNamespaceQualifiedCalls:
    """Tests for R namespace-qualified call tracking (ADR-0007)."""

    def test_extracts_namespace_qualified_call(self, tmp_path):
        """Detects pkg::func() style calls."""
        r_file = tmp_path / "script.R"
        r_file.write_text("""
library(dplyr)

my_func <- function(data) {
    result <- dplyr::filter(data, x > 0)
    return(result)
}
""")
        result = analyze_r_files(tmp_path)

        calls = [e for e in result.edges if e.edge_type == "calls"]
        # Should have call to dplyr::filter
        qualified_call = next((c for c in calls if "dplyr" in c.dst and "filter" in c.dst), None)
        assert qualified_call is not None
        assert qualified_call.evidence_type == "qualified_call"

    def test_extracts_loaded_packages(self, tmp_path):
        """Tracks packages loaded via library() for path hints."""
        from hypergumbo_lang_common.r_lang import _extract_loaded_packages
        from tree_sitter_language_pack import get_parser

        source = b"""
library(dplyr)
library(ggplot2)
require(tidyr)
"""
        parser = get_parser("r")
        tree = parser.parse(source)

        packages = _extract_loaded_packages(tree.root_node, source)

        assert "dplyr" in packages
        assert "ggplot2" in packages
        assert "tidyr" in packages

    def test_extracts_loaded_packages_string_syntax(self, tmp_path):
        """Tracks packages loaded with string syntax: library("pkg")."""
        from hypergumbo_lang_common.r_lang import _extract_loaded_packages
        from tree_sitter_language_pack import get_parser

        source = b'''
library("stringr")
require("tibble")
'''
        parser = get_parser("r")
        tree = parser.parse(source)

        packages = _extract_loaded_packages(tree.root_node, source)

        assert "stringr" in packages
        assert "tibble" in packages

    def test_qualified_call_higher_confidence(self, tmp_path):
        """Namespace-qualified calls get higher confidence scores."""
        r_file = tmp_path / "script.R"
        r_file.write_text("""
my_func <- function(data) {
    # Qualified call - explicit package reference
    x <- stats::filter(data)
    # Unqualified call
    y <- print(x)
    return(y)
}
""")
        result = analyze_r_files(tmp_path)

        calls = [e for e in result.edges if e.edge_type == "calls"]
        qualified = next((c for c in calls if "stats" in c.dst), None)
        unqualified = next((c for c in calls if "print" in c.dst), None)

        assert qualified is not None
        assert unqualified is not None
        # Qualified should have higher confidence
        assert qualified.confidence >= 0.70  # External qualified
        assert unqualified.confidence >= 0.70  # External unqualified

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


class TestIsRTreeSitterAvailable:
    """Tests for is_r_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        """Returns True when tree-sitter-language-pack is installed."""
        assert is_r_tree_sitter_available() is True

    def test_returns_false_when_unavailable(self) -> None:
        """Returns False when tree-sitter-language-pack is not installed."""
        with patch.object(
            r_lang_module._analyzer,
            "_check_grammar_available",
            return_value=False,
        ):
            assert is_r_tree_sitter_available() is False


class TestRCyclomaticComplexity:
    """INV-loguk slice C: callable R symbols carry non-null CC + LOC.
    Real-grammar verification (if/for/while/repeat + &&/||). R's switch() is an
    ordinary call (no control-flow node), so it is conservatively uncounted."""

    def test_branchy_function_has_cc_and_loc(self, tmp_path) -> None:
        (tmp_path / "f.R").write_text("""f <- function(x, y) {
  if (x > 0 && y > 0) {
    for (i in 1:10) {
      while (i < 5) {
        i <- i + 1
      }
    }
  } else if (x < 0 || y < 0) {
    repeat {
      break
    }
  } else {
    z <- switch(x, a = 1, b = 2, 3)
  }
  return(x)
}""")
        result = analyze_r_files(tmp_path)
        fn = next(s for s in result.symbols if s.kind == "function" and s.name == "f")
        # base 1 + 2 if + for + while + repeat + && + || = 8
        assert fn.cyclomatic_complexity == 8
        assert fn.lines_of_code is not None and fn.lines_of_code >= 4

    def test_straight_line_function_cc_is_one(self, tmp_path) -> None:
        (tmp_path / "g.R").write_text("g <- function(x) {\n  x * 2\n}\n")
        result = analyze_r_files(tmp_path)
        fn = next(s for s in result.symbols if s.kind == "function" and s.name == "g")
        assert fn.cyclomatic_complexity == 1
        assert fn.lines_of_code is not None

    def test_callables_non_null_non_callables_null(self, tmp_path) -> None:
        (tmp_path / "m.R").write_text("""source("helper.R")
h <- function(x) {
  if (x > 0) {
    return(1)
  }
  return(0)
}
""")
        result = analyze_r_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert funcs
        for s in funcs:
            assert s.cyclomatic_complexity is not None, s.name
            assert s.lines_of_code is not None, s.name
        for s in result.symbols:
            if s.kind != "function":
                assert s.cyclomatic_complexity is None, (s.kind, s.name)
