"""Tests for Starlark (Bazel/Buck) analysis pass.

Tests verify that the Starlark analyzer correctly extracts:
- Function definitions (def)
- Rule/target invocations (py_binary, cc_library, etc.)
- Load statements
- Variable assignments
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo.analyze import starlark as starlark_module
from hypergumbo.analyze.starlark import (
    analyze_starlark,
    find_starlark_files,
    is_starlark_tree_sitter_available,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository for testing."""
    return tmp_path


class TestFindStarlarkFiles:
    """Tests for find_starlark_files function."""

    def test_finds_build_files(self, temp_repo: Path) -> None:
        """Finds BUILD and BUILD.bazel files."""
        (temp_repo / "BUILD").write_text("# build file")
        (temp_repo / "subdir").mkdir()
        (temp_repo / "subdir" / "BUILD.bazel").write_text("# build file")
        (temp_repo / "README.md").write_text("# Docs")

        files = list(find_starlark_files(temp_repo))
        filenames = {f.name for f in files}

        assert "BUILD" in filenames
        assert "BUILD.bazel" in filenames
        assert "README.md" not in filenames

    def test_finds_bzl_files(self, temp_repo: Path) -> None:
        """Finds .bzl extension files."""
        (temp_repo / "rules.bzl").write_text("def my_rule(): pass")
        (temp_repo / "macros.bzl").write_text("def macro(): pass")

        files = list(find_starlark_files(temp_repo))
        filenames = {f.name for f in files}

        assert "rules.bzl" in filenames
        assert "macros.bzl" in filenames

    def test_finds_buck_files(self, temp_repo: Path) -> None:
        """Finds BUCK files."""
        (temp_repo / "BUCK").write_text("# buck build file")

        files = list(find_starlark_files(temp_repo))
        filenames = {f.name for f in files}

        assert "BUCK" in filenames


class TestStarlarkTreeSitterAvailable:
    """Tests for tree-sitter availability check."""

    def test_availability_check_runs(self) -> None:
        """Availability check returns a boolean."""
        result = is_starlark_tree_sitter_available()
        assert isinstance(result, bool)


class TestStarlarkAnalysis:
    """Tests for Starlark analysis with tree-sitter."""

    def test_analyzes_function(self, temp_repo: Path) -> None:
        """Detects function definitions."""
        (temp_repo / "rules.bzl").write_text('''
def my_rule(name, srcs):
    """A custom rule."""
    native.genrule(name = name, srcs = srcs)

def _private_helper():
    pass
''')

        result = analyze_starlark(temp_repo)

        assert not result.skipped
        func_names = {s.name for s in result.symbols if s.kind == "function"}
        assert "my_rule" in func_names
        assert "_private_helper" in func_names

    def test_function_signature(self, temp_repo: Path) -> None:
        """Function signatures include parameters."""
        (temp_repo / "rules.bzl").write_text('''
def compile_proto(name, srcs, deps = [], visibility = None):
    pass
''')

        result = analyze_starlark(temp_repo)

        func = next(s for s in result.symbols if s.name == "compile_proto")
        assert func.signature is not None
        assert "name" in func.signature
        assert "srcs" in func.signature
        assert "deps" in func.signature

    def test_analyzes_targets(self, temp_repo: Path) -> None:
        """Detects rule invocations as targets."""
        (temp_repo / "BUILD").write_text('''
py_binary(
    name = "main",
    srcs = ["main.py"],
    deps = [":lib"],
)

py_library(
    name = "lib",
    srcs = ["lib.py"],
)
''')

        result = analyze_starlark(temp_repo)

        target_names = {s.name for s in result.symbols if s.kind == "target"}
        assert "main" in target_names
        assert "lib" in target_names

    def test_target_rule_type_in_meta(self, temp_repo: Path) -> None:
        """Target symbols include rule type in meta."""
        (temp_repo / "BUILD").write_text('''
cc_library(
    name = "mylib",
    srcs = ["mylib.cc"],
)
''')

        result = analyze_starlark(temp_repo)

        target = next(s for s in result.symbols if s.name == "mylib")
        assert target.meta is not None
        assert target.meta.get("rule_type") == "cc_library"

    def test_analyzes_load_statements(self, temp_repo: Path) -> None:
        """Detects load statements as import edges."""
        (temp_repo / "BUILD").write_text('''
load("@rules_python//python:defs.bzl", "py_binary", "py_library")
load(":my_rules.bzl", "custom_rule")

py_binary(name = "app", srcs = ["app.py"])
''')

        result = analyze_starlark(temp_repo)

        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        # Should have edges for each loaded symbol
        assert len(import_edges) >= 2

    def test_analyzes_variable_assignment(self, temp_repo: Path) -> None:
        """Detects top-level variable assignments."""
        (temp_repo / "defs.bzl").write_text('''
COMMON_COPTS = ["-Wall", "-Werror"]
DEFAULT_VISIBILITY = ["//visibility:public"]
''')

        result = analyze_starlark(temp_repo)

        var_names = {s.name for s in result.symbols if s.kind == "variable"}
        assert "COMMON_COPTS" in var_names
        assert "DEFAULT_VISIBILITY" in var_names

    def test_analyzes_target_deps_edges(self, temp_repo: Path) -> None:
        """Creates dependency edges between targets."""
        (temp_repo / "BUILD").write_text('''
py_library(
    name = "utils",
    srcs = ["utils.py"],
)

py_binary(
    name = "main",
    srcs = ["main.py"],
    deps = [":utils"],
)
''')

        result = analyze_starlark(temp_repo)

        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        # main depends on utils
        assert any(
            ":main" in e.src and ":utils" in e.dst
            for e in dep_edges
        )


class TestStarlarkAnalysisUnavailable:
    """Tests for handling unavailable tree-sitter."""

    def test_skipped_when_unavailable(self, temp_repo: Path) -> None:
        """Returns skipped result when tree-sitter unavailable."""
        (temp_repo / "BUILD").write_text("py_binary(name = 'main')")

        with patch.object(starlark_module, "is_starlark_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Starlark analysis skipped"):
                result = starlark_module.analyze_starlark(temp_repo)

        assert result.skipped is True


class TestStarlarkAnalysisRun:
    """Tests for Starlark analysis run metadata."""

    def test_analysis_run_created(self, temp_repo: Path) -> None:
        """Analysis run is created with correct metadata."""
        (temp_repo / "BUILD").write_text('''
py_binary(name = "app", srcs = ["app.py"])
''')

        result = analyze_starlark(temp_repo)

        assert result.run is not None
        assert result.run.pass_id == "starlark-v1"
        assert result.run.files_analyzed >= 1
