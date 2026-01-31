"""Tests for the Meson build system analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import meson as meson_module
from hypergumbo_lang_common.meson import (
    MesonAnalysisResult,
    analyze_meson,
    find_meson_files,
    is_meson_tree_sitter_available,
)


def make_meson_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Meson file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindMesonFiles:
    """Tests for find_meson_files function."""

    def test_finds_meson_build_files(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", "project('test', 'c')")
        make_meson_file(tmp_path, "src/meson.build", "# subproject")
        files = list(find_meson_files(tmp_path))
        assert len(files) == 2

    def test_finds_meson_options_files(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson_options.txt", "option('debug', type: 'boolean')")
        files = list(find_meson_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "meson_options.txt"

    def test_finds_meson_options_new_format(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.options", "option('debug', type: 'boolean')")
        files = list(find_meson_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "meson.options"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = list(find_meson_files(tmp_path))
        assert files == []


class TestIsMesonTreeSitterAvailable:
    """Tests for is_meson_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_meson_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(meson_module, "is_meson_tree_sitter_available", return_value=False):
            assert meson_module.is_meson_tree_sitter_available() is False


class TestAnalyzeMeson:
    """Tests for analyze_meson function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", "project('test', 'c')")
        with patch.object(meson_module, "is_meson_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Meson analysis skipped"):
                result = meson_module.analyze_meson(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_project(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('myproject', 'c',
  version : '1.0.0')
""")
        result = analyze_meson(tmp_path)
        assert not result.skipped
        proj = next((s for s in result.symbols if s.name == "myproject"), None)
        assert proj is not None
        assert proj.kind == "project"
        assert proj.language == "meson"

    def test_extracts_executables(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
executable('myapp', 'main.c')
""")
        result = analyze_meson(tmp_path)
        exe = next((s for s in result.symbols if s.name == "myapp"), None)
        assert exe is not None
        assert exe.kind == "executable"
        assert exe.meta["command"] == "executable"

    def test_extracts_libraries(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
library('mylib', 'lib.c')
""")
        result = analyze_meson(tmp_path)
        lib = next((s for s in result.symbols if s.name == "mylib"), None)
        assert lib is not None
        assert lib.kind == "library"

    def test_extracts_shared_library(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
shared_library('myshared', 'lib.c')
""")
        result = analyze_meson(tmp_path)
        lib = next((s for s in result.symbols if s.name == "myshared"), None)
        assert lib is not None
        assert lib.kind == "library"
        assert lib.meta["command"] == "shared_library"

    def test_extracts_static_library(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
static_library('mystatic', 'lib.c')
""")
        result = analyze_meson(tmp_path)
        lib = next((s for s in result.symbols if s.name == "mystatic"), None)
        assert lib is not None
        assert lib.kind == "library"
        assert lib.meta["command"] == "static_library"

    def test_extracts_custom_target(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
custom_target('generate_docs',
  output : 'docs',
  command : ['doxygen'])
""")
        result = analyze_meson(tmp_path)
        target = next((s for s in result.symbols if s.name == "generate_docs"), None)
        assert target is not None
        assert target.kind == "target"

    def test_extracts_dependency_edges(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
mylib = library('mylib', 'lib.c')
myapp = executable('myapp', 'main.c', dependencies : [mylib])
""")
        result = analyze_meson(tmp_path)
        # myapp depends on mylib
        edge = next(
            (e for e in result.edges if "myapp" in e.src and "mylib" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "depends_on"
        assert edge.confidence == 1.0

    def test_extracts_subdir_includes(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
subdir('src')
""")
        result = analyze_meson(tmp_path)
        edge = next(
            (e for e in result.edges if "subdir:src" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "includes"

    def test_pass_id(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
executable('foo', 'foo.c')
""")
        result = analyze_meson(tmp_path)
        exe = next((s for s in result.symbols if s.name == "foo"), None)
        assert exe is not None
        assert exe.origin == "meson.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", "project('test', 'c')")
        result = analyze_meson(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "meson.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_meson(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
executable('myapp', 'main.c')
""")
        result = analyze_meson(tmp_path)
        exe = next((s for s in result.symbols if s.name == "myapp"), None)
        assert exe is not None
        assert exe.id == exe.stable_id
        assert "meson:" in exe.id
        assert "meson.build" in exe.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
executable('myapp', 'main.c')
""")
        result = analyze_meson(tmp_path)
        exe = next((s for s in result.symbols if s.name == "myapp"), None)
        assert exe is not None
        assert exe.span is not None
        assert exe.span.start_line >= 1
        assert exe.span.end_line >= exe.span.start_line

    def test_both_libraries(self, tmp_path: Path) -> None:
        make_meson_file(tmp_path, "meson.build", """project('test', 'c')
both_libraries('myboth', 'lib.c')
""")
        result = analyze_meson(tmp_path)
        lib = next((s for s in result.symbols if s.name == "myboth"), None)
        assert lib is not None
        assert lib.kind == "library"
        assert lib.meta["command"] == "both_libraries"
