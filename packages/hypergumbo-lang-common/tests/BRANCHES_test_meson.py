"""Branch coverage tests for meson.py analyzer.

Tests specific branch paths in the Meson analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Project extraction
- Build target extraction (executables, libraries)
- Variable assignments
- Subdir includes
- Dependency edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.meson import (
    _make_stable_id,
    _is_target_command,
    analyze_meson,
    find_meson_files,
)


def make_meson_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Meson build file with given content."""
    (tmp_path / name).write_text(content)


class TestMesonHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_stable_id_format(self, tmp_path: Path) -> None:
        """Test stable ID format."""
        path = tmp_path / "meson.build"
        path.touch()
        stable_id = _make_stable_id(path, tmp_path, "myproject", "project")
        assert stable_id == "meson:meson.build:myproject:project"

    def test_is_target_command_true(self) -> None:
        """Test target command detection."""
        assert _is_target_command("executable") is True
        assert _is_target_command("library") is True
        assert _is_target_command("shared_library") is True
        assert _is_target_command("static_library") is True
        assert _is_target_command("both_libraries") is True
        assert _is_target_command("custom_target") is True
        assert _is_target_command("run_target") is True

    def test_is_target_command_false(self) -> None:
        """Test non-target command detection."""
        assert _is_target_command("project") is False
        assert _is_target_command("subdir") is False
        assert _is_target_command("dependency") is False


class TestProjectExtraction:
    """Branch coverage for project definition extraction."""

    def test_simple_project(self, tmp_path: Path) -> None:
        """Test simple project extraction."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
""")
        result = analyze_meson(tmp_path)
        assert not result.skipped

        projects = [s for s in result.symbols if s.kind == "project"]
        assert len(projects) >= 1
        assert any(p.name == "myapp" for p in projects)

    def test_project_with_languages(self, tmp_path: Path) -> None:
        """Test project with multiple languages."""
        make_meson_file(tmp_path, "meson.build", """
project('mylib', 'c', 'cpp', version: '1.0.0')
""")
        result = analyze_meson(tmp_path)
        projects = [s for s in result.symbols if s.kind == "project"]
        assert len(projects) >= 1
        assert any(p.name == "mylib" for p in projects)


class TestTargetExtraction:
    """Branch coverage for build target extraction."""

    def test_executable_target(self, tmp_path: Path) -> None:
        """Test executable target extraction."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
executable('myapp', 'main.c')
""")
        result = analyze_meson(tmp_path)
        exes = [s for s in result.symbols if s.kind == "executable"]
        assert len(exes) >= 1
        assert any(e.name == "myapp" for e in exes)

    def test_library_target(self, tmp_path: Path) -> None:
        """Test library target extraction."""
        make_meson_file(tmp_path, "meson.build", """
project('mylib', 'c')
library('mylib', 'lib.c')
""")
        result = analyze_meson(tmp_path)
        libs = [s for s in result.symbols if s.kind == "library"]
        assert len(libs) >= 1
        assert any(lib.name == "mylib" for lib in libs)

    def test_shared_library_target(self, tmp_path: Path) -> None:
        """Test shared library target extraction."""
        make_meson_file(tmp_path, "meson.build", """
project('mylib', 'c')
shared_library('myshared', 'shared.c')
""")
        result = analyze_meson(tmp_path)
        libs = [s for s in result.symbols if s.kind == "library"]
        assert len(libs) >= 1
        assert any(lib.name == "myshared" for lib in libs)

    def test_static_library_target(self, tmp_path: Path) -> None:
        """Test static library target extraction."""
        make_meson_file(tmp_path, "meson.build", """
project('mylib', 'c')
static_library('mystatic', 'static.c')
""")
        result = analyze_meson(tmp_path)
        libs = [s for s in result.symbols if s.kind == "library"]
        assert len(libs) >= 1
        assert any(lib.name == "mystatic" for lib in libs)

    def test_custom_target(self, tmp_path: Path) -> None:
        """Test custom target extraction."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
custom_target('generated', output: 'gen.c', command: ['gen.py'])
""")
        result = analyze_meson(tmp_path)
        targets = [s for s in result.symbols if s.kind == "target"]
        assert len(targets) >= 1
        assert any(t.name == "generated" for t in targets)


class TestVariableAssignment:
    """Branch coverage for variable assignment tracking."""

    def test_assigned_target(self, tmp_path: Path) -> None:
        """Test target assigned to variable."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
mylib = library('mylib', 'lib.c')
myexe = executable('myexe', 'main.c', dependencies: [mylib])
""")
        result = analyze_meson(tmp_path)
        libs = [s for s in result.symbols if s.kind == "library"]
        exes = [s for s in result.symbols if s.kind == "executable"]
        assert len(libs) >= 1
        assert len(exes) >= 1


class TestSubdirIncludes:
    """Branch coverage for subdir include edge extraction."""

    def test_subdir_include(self, tmp_path: Path) -> None:
        """Test subdir creates include edge."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
subdir('src')
""")
        # Create subdir
        src = tmp_path / "src"
        src.mkdir()
        make_meson_file(src, "meson.build", """
library('sublib', 'sub.c')
""")
        result = analyze_meson(tmp_path)
        includes = [e for e in result.edges if e.edge_type == "includes"]
        assert len(includes) >= 1


class TestDependencyEdges:
    """Branch coverage for dependency edge extraction."""

    def test_dependency_list(self, tmp_path: Path) -> None:
        """Test dependencies list creates edges."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
utils = library('utils', 'utils.c')
core = library('core', 'core.c')
myexe = executable('myexe', 'main.c', dependencies: [utils, core])
""")
        result = analyze_meson(tmp_path)
        deps = [e for e in result.edges if e.edge_type == "depends_on"]
        assert len(deps) >= 1


class TestFindMesonFiles:
    """Branch coverage for file discovery."""

    def test_finds_meson_build(self, tmp_path: Path) -> None:
        """Test meson.build files are discovered."""
        (tmp_path / "meson.build").write_text("project('test', 'c')")

        files = list(find_meson_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "meson.build" for f in files)

    def test_finds_meson_options(self, tmp_path: Path) -> None:
        """Test meson.options files are discovered."""
        (tmp_path / "meson.options").write_text("option('debug', type: 'boolean')")

        files = list(find_meson_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "meson.options" for f in files)

    def test_finds_meson_options_txt(self, tmp_path: Path) -> None:
        """Test meson_options.txt files are discovered."""
        (tmp_path / "meson_options.txt").write_text("option('debug', type: 'boolean')")

        files = list(find_meson_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "meson_options.txt" for f in files)

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "meson.build").write_text("library('sublib', 'sub.c')")

        files = list(find_meson_files(tmp_path))
        assert len(files) >= 1


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_meson_files(self, tmp_path: Path) -> None:
        """Test directory with no Meson files."""
        result = analyze_meson(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0

    def test_minimal_project(self, tmp_path: Path) -> None:
        """Test minimal Meson project."""
        make_meson_file(tmp_path, "meson.build", """
project('minimal', 'c')
""")
        result = analyze_meson(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
executable('myapp', 'main.c')
""")
        result = analyze_meson(tmp_path)
        assert result.run is not None


class TestTargetMetadata:
    """Branch coverage for target metadata extraction."""

    def test_executable_has_command_meta(self, tmp_path: Path) -> None:
        """Test executable has command metadata."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
executable('myexe', 'main.c')
""")
        result = analyze_meson(tmp_path)
        exes = [s for s in result.symbols if s.kind == "executable"]
        assert len(exes) >= 1
        assert exes[0].meta is not None
        assert exes[0].meta.get("command") == "executable"

    def test_library_has_command_meta(self, tmp_path: Path) -> None:
        """Test library has command metadata."""
        make_meson_file(tmp_path, "meson.build", """
project('myapp', 'c')
shared_library('mylib', 'lib.c')
""")
        result = analyze_meson(tmp_path)
        libs = [s for s in result.symbols if s.kind == "library"]
        assert len(libs) >= 1
        assert libs[0].meta is not None
        assert libs[0].meta.get("command") == "shared_library"
