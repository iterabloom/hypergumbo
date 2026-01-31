"""Tests for the BitBake analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import bitbake as bitbake_module
from hypergumbo_lang_extended1.bitbake import (
    BitBakeAnalysisResult,
    analyze_bitbake,
    find_bitbake_files,
    is_bitbake_tree_sitter_available,
)


def make_bitbake_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a BitBake file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindBitBakeFiles:
    """Tests for find_bitbake_files function."""

    def test_finds_bb_files(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "example.bb", "SUMMARY = \"Test\"")
        make_bitbake_file(tmp_path, "recipes/other.bb", "SUMMARY = \"Other\"")
        files = find_bitbake_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"example.bb", "other.bb"}

    def test_finds_bbappend_files(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "example.bbappend", "EXTRA_OECONF += \"--flag\"")
        files = find_bitbake_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "example.bbappend"

    def test_finds_bbclass_files(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "classes/cmake.bbclass", "inherit cmake")
        files = find_bitbake_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "cmake.bbclass"

    def test_finds_inc_files(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "common.inc", "DEPENDS = \"base-files\"")
        files = find_bitbake_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "common.inc"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_bitbake_files(tmp_path)
        assert files == []


class TestIsBitBakeTreeSitterAvailable:
    """Tests for is_bitbake_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_bitbake_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(bitbake_module, "is_bitbake_tree_sitter_available", return_value=False):
            assert bitbake_module.is_bitbake_tree_sitter_available() is False


class TestAnalyzeBitBake:
    """Tests for analyze_bitbake function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", "SUMMARY = \"Test\"")
        with patch.object(bitbake_module, "is_bitbake_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="BitBake analysis skipped"):
                result = bitbake_module.analyze_bitbake(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_summary_variable(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
SUMMARY = "Example recipe for testing"
""")
        result = analyze_bitbake(tmp_path)
        assert not result.skipped
        var = next((s for s in result.symbols if s.kind == "variable" and s.name == "SUMMARY"), None)
        assert var is not None
        assert var.language == "bitbake"
        assert "Example recipe" in var.meta.get("value", "")

    def test_extracts_license_variable(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
LICENSE = "MIT"
""")
        result = analyze_bitbake(tmp_path)
        var = next((s for s in result.symbols if s.name == "LICENSE"), None)
        assert var is not None
        assert var.meta.get("value") == "MIT"

    def test_extracts_src_uri_variable(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
SRC_URI = "http://example.com/source.tar.gz"
""")
        result = analyze_bitbake(tmp_path)
        var = next((s for s in result.symbols if s.name == "SRC_URI"), None)
        assert var is not None
        assert "example.com" in var.meta.get("value", "")

    def test_extracts_depends_variable(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
DEPENDS = "zlib openssl"
""")
        result = analyze_bitbake(tmp_path)
        var = next((s for s in result.symbols if s.name == "DEPENDS"), None)
        assert var is not None
        # Should also create dependency edges
        dep_edges = [e for e in result.edges if e.edge_type == "depends"]
        assert len(dep_edges) >= 2
        deps = {e.dst for e in dep_edges}
        assert "bitbake:package:zlib" in deps
        assert "bitbake:package:openssl" in deps

    def test_ignores_unimportant_variables(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
MY_CUSTOM_VAR = "some value"
RANDOM_THING = "other value"
""")
        result = analyze_bitbake(tmp_path)
        # Custom variables should not be extracted
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert len(vars) == 0

    def test_extracts_inherit_directive(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
inherit cmake
""")
        result = analyze_bitbake(tmp_path)
        inherit = next((s for s in result.symbols if s.kind == "inherit"), None)
        assert inherit is not None
        assert inherit.name == "cmake"
        # Should create inherit edge
        inherit_edge = next((e for e in result.edges if e.edge_type == "inherits"), None)
        assert inherit_edge is not None
        assert "cmake" in inherit_edge.dst

    def test_extracts_multiple_inherits(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
inherit cmake pkgconfig
""")
        result = analyze_bitbake(tmp_path)
        inherits = [s for s in result.symbols if s.kind == "inherit"]
        assert len(inherits) == 2
        names = {i.name for i in inherits}
        assert "cmake" in names
        assert "pkgconfig" in names

    def test_extracts_task_function(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
do_configure() {
    cmake ..
}
""")
        result = analyze_bitbake(tmp_path)
        task = next((s for s in result.symbols if s.kind == "task"), None)
        assert task is not None
        assert task.name == "do_configure"
        assert task.meta.get("task_type") == "standard"

    def test_extracts_custom_function(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
my_custom_function() {
    echo "Hello"
}
""")
        result = analyze_bitbake(tmp_path)
        task = next((s for s in result.symbols if s.kind == "task"), None)
        assert task is not None
        assert task.name == "my_custom_function"
        assert task.meta.get("task_type") == "custom"

    def test_extracts_multiple_tasks(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
do_configure() {
    cmake ..
}

do_compile() {
    oe_runmake
}

do_install() {
    install -d ${D}${bindir}
}
""")
        result = analyze_bitbake(tmp_path)
        tasks = [s for s in result.symbols if s.kind == "task"]
        assert len(tasks) == 3
        names = {t.name for t in tasks}
        assert "do_configure" in names
        assert "do_compile" in names
        assert "do_install" in names

    def test_pass_id(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
SUMMARY = "Test"
""")
        result = analyze_bitbake(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.origin == "bitbake.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", "SUMMARY = \"Test\"")
        result = analyze_bitbake(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "bitbake.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_bitbake(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
SUMMARY = "Test"
""")
        result = analyze_bitbake(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.id == var.stable_id
        assert "bitbake:" in var.id
        assert "test.bb" in var.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "test.bb", """
SUMMARY = "Test"
""")
        result = analyze_bitbake(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert var.span is not None
        assert var.span.start_line >= 1
        assert var.span.end_line >= var.span.start_line

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "recipe-a.bb", """
SUMMARY = "Recipe A"
""")
        make_bitbake_file(tmp_path, "recipe-b.bb", """
SUMMARY = "Recipe B"
""")
        result = analyze_bitbake(tmp_path)
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert len(vars) == 2

    def test_complete_recipe(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "example.bb", """
SUMMARY = "Example application"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=abc123"

SRC_URI = "http://example.com/${PN}-${PV}.tar.gz"

DEPENDS = "zlib openssl"

inherit cmake pkgconfig

do_configure() {
    cmake -DENABLE_TESTS=ON ..
}

do_compile() {
    oe_runmake
}

do_install() {
    install -d ${D}${bindir}
    install -m 0755 myapp ${D}${bindir}
}
""")
        result = analyze_bitbake(tmp_path)

        # Check variables
        vars = [s for s in result.symbols if s.kind == "variable"]
        assert len(vars) >= 4  # SUMMARY, LICENSE, SRC_URI, DEPENDS

        # Check inherits
        inherits = [s for s in result.symbols if s.kind == "inherit"]
        assert len(inherits) == 2

        # Check tasks
        tasks = [s for s in result.symbols if s.kind == "task"]
        assert len(tasks) == 3

        # Check edges
        dep_edges = [e for e in result.edges if e.edge_type == "depends"]
        assert len(dep_edges) >= 2

        inherit_edges = [e for e in result.edges if e.edge_type == "inherits"]
        assert len(inherit_edges) == 2

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_bitbake_file(tmp_path, "a.bb", "SUMMARY = \"A\"")
        make_bitbake_file(tmp_path, "b.bb", "SUMMARY = \"B\"")
        make_bitbake_file(tmp_path, "c.bb", "SUMMARY = \"C\"")
        result = analyze_bitbake(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_depends_with_variable_refs(self, tmp_path: Path) -> None:
        """Test that variable references in DEPENDS are handled."""
        make_bitbake_file(tmp_path, "test.bb", """
DEPENDS = "${PN}-native zlib"
""")
        result = analyze_bitbake(tmp_path)
        # Should extract zlib but skip ${PN}-native
        dep_edges = [e for e in result.edges if e.edge_type == "depends"]
        deps = {e.dst for e in dep_edges}
        assert "bitbake:package:zlib" in deps
        # Variable refs should be filtered out
        assert not any("${" in d for d in deps)

    def test_long_value_truncation(self, tmp_path: Path) -> None:
        """Test that long variable values are truncated in signature."""
        make_bitbake_file(tmp_path, "test.bb", f"""
SUMMARY = "{'x' * 100}"
""")
        result = analyze_bitbake(tmp_path)
        var = next((s for s in result.symbols if s.kind == "variable"), None)
        assert var is not None
        assert len(var.signature) < 100
        assert "..." in var.signature

    def test_extracts_python_function(self, tmp_path: Path) -> None:
        """Test that Python functions are extracted."""
        make_bitbake_file(tmp_path, "test.bb", """
python do_custom_task() {
    import os
    bb.plain("Hello")
}
""")
        result = analyze_bitbake(tmp_path)
        task = next((s for s in result.symbols if s.kind == "python_task"), None)
        assert task is not None
        assert task.name == "do_custom_task"
        assert "python" in task.signature
        assert task.meta.get("language") == "python"

    def test_extracts_addtask(self, tmp_path: Path) -> None:
        """Test that addtask directives are extracted."""
        make_bitbake_file(tmp_path, "test.bb", """
do_mytask() {
    echo "hello"
}

addtask do_mytask after do_configure before do_compile
""")
        result = analyze_bitbake(tmp_path)
        addtask = next((s for s in result.symbols if s.kind == "addtask"), None)
        assert addtask is not None
        assert addtask.name == "do_mytask"
        assert "addtask" in addtask.signature
