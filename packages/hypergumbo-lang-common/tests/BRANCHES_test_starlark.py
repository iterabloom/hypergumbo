# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for starlark.py analyzer.

Tests specific branch paths in the Starlark analyzer that may not be covered
by the main test suite. Focuses on:
- Function definition extraction
- Build target extraction
- Load statement extraction
- Variable assignment extraction
- Dependency edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.starlark import (
    analyze_starlark,
    find_starlark_files,
)


def make_bzl_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Starlark .bzl file with given content."""
    (tmp_path / name).write_text(content)


def make_build_file(tmp_path: Path, content: str) -> None:
    """Create a BUILD file with given content."""
    (tmp_path / "BUILD").write_text(content)


class TestFunctionExtraction:
    """Branch coverage for function definition extraction."""

    def test_simple_function(self, tmp_path: Path) -> None:
        """Test simple function extraction."""
        make_bzl_file(tmp_path, "rules.bzl", """
def my_rule():
    pass
""")
        result = analyze_starlark(tmp_path)
        assert not result.skipped

        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) >= 1
        assert any(f.name == "my_rule" for f in funcs)

    def test_function_with_params(self, tmp_path: Path) -> None:
        """Test function with parameters."""
        make_bzl_file(tmp_path, "rules.bzl", """
def build_target(name, srcs, deps = []):
    pass
""")
        result = analyze_starlark(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "build_target"]
        assert len(funcs) >= 1
        assert funcs[0].signature is not None


class TestTargetExtraction:
    """Branch coverage for build target extraction."""

    def test_py_binary_target(self, tmp_path: Path) -> None:
        """Test py_binary target extraction."""
        make_build_file(tmp_path, """
py_binary(
    name = "main",
    srcs = ["main.py"],
)
""")
        result = analyze_starlark(tmp_path)
        targets = [s for s in result.symbols if s.kind == "target"]
        assert len(targets) >= 1
        assert any(t.name == "main" for t in targets)

    def test_py_library_target(self, tmp_path: Path) -> None:
        """Test py_library target extraction."""
        make_build_file(tmp_path, """
py_library(
    name = "utils",
    srcs = ["utils.py"],
)
""")
        result = analyze_starlark(tmp_path)
        targets = [s for s in result.symbols if s.kind == "target"]
        assert len(targets) >= 1
        assert any(t.name == "utils" for t in targets)

    def test_target_with_rule_type_meta(self, tmp_path: Path) -> None:
        """Test target has rule_type metadata."""
        make_build_file(tmp_path, """
cc_library(
    name = "mylib",
    srcs = ["lib.cc"],
)
""")
        result = analyze_starlark(tmp_path)
        targets = [s for s in result.symbols if s.kind == "target"]
        assert len(targets) >= 1
        assert targets[0].meta is not None
        assert targets[0].meta.get("rule_type") == "cc_library"


class TestLoadStatements:
    """Branch coverage for load statement extraction."""

    def test_simple_load(self, tmp_path: Path) -> None:
        """Test simple load statement creates import edge."""
        make_build_file(tmp_path, """
load("@rules_python//python:defs.bzl", "py_binary")

py_binary(
    name = "main",
    srcs = ["main.py"],
)
""")
        result = analyze_starlark(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

    def test_aliased_load(self, tmp_path: Path) -> None:
        """Test aliased load statement."""
        make_build_file(tmp_path, """
load(":rules.bzl", custom = "custom_rule")

custom(
    name = "my_target",
)
""")
        result = analyze_starlark(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestDependencyEdges:
    """Branch coverage for dependency edge extraction."""

    def test_deps_list_creates_edges(self, tmp_path: Path) -> None:
        """Test deps list creates dependency edges."""
        make_build_file(tmp_path, """
py_library(
    name = "utils",
    srcs = ["utils.py"],
)

py_binary(
    name = "main",
    srcs = ["main.py"],
    deps = [":utils"],
)
""")
        result = analyze_starlark(tmp_path)
        dep_edges = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(dep_edges) >= 1


class TestVariableExtraction:
    """Branch coverage for variable assignment extraction."""

    def test_constant_variable(self, tmp_path: Path) -> None:
        """Test uppercase constant extraction."""
        make_bzl_file(tmp_path, "defs.bzl", """
DEFAULT_VISIBILITY = ["//visibility:public"]
""")
        result = analyze_starlark(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 1
        assert any(v.name == "DEFAULT_VISIBILITY" for v in variables)


class TestFindStarlarkFiles:
    """Branch coverage for file discovery."""

    def test_finds_build_files(self, tmp_path: Path) -> None:
        """Test BUILD files are discovered."""
        (tmp_path / "BUILD").write_text("py_binary(name = 'main', srcs = ['main.py'])")

        files = list(find_starlark_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "BUILD" for f in files)

    def test_finds_build_bazel_files(self, tmp_path: Path) -> None:
        """Test BUILD.bazel files are discovered."""
        (tmp_path / "BUILD.bazel").write_text("py_binary(name = 'main', srcs = ['main.py'])")

        files = list(find_starlark_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "BUILD.bazel" for f in files)

    def test_finds_bzl_files(self, tmp_path: Path) -> None:
        """Test .bzl files are discovered."""
        (tmp_path / "rules.bzl").write_text("def my_rule(): pass")

        files = list(find_starlark_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".bzl" for f in files)

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "BUILD").write_text("py_library(name = 'lib', srcs = [])")

        files = list(find_starlark_files(tmp_path))
        assert len(files) >= 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_starlark_files(self, tmp_path: Path) -> None:
        """Test directory with no Starlark files."""
        result = analyze_starlark(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_build(self, tmp_path: Path) -> None:
        """Test minimal BUILD file."""
        make_build_file(tmp_path, """
py_binary(
    name = "main",
    srcs = ["main.py"],
)
""")
        result = analyze_starlark(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_build_file(tmp_path, """
py_binary(name = "main", srcs = ["main.py"])
""")
        result = analyze_starlark(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestCallEdges:
    """Branch coverage for function call edge extraction."""

    def test_internal_call(self, tmp_path: Path) -> None:
        """Test call to function in same file creates edge."""
        make_bzl_file(tmp_path, "rules.bzl", """
def helper():
    pass

def main():
    helper()
""")
        result = analyze_starlark(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1
