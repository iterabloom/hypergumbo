# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for cmake.py analyzer.

Tests specific branch paths in the CMake analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Project extraction
- Library target extraction
- Executable target extraction
- Function/macro extraction
- Subdirectory extraction
- Package extraction
- Link edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id
from hypergumbo_lang_mainstream.cmake import _make_edge_id, analyze_cmake_files, find_cmake_files

def make_cmake_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a CMake file with given content."""
    (tmp_path / name).write_text(content)

class TestCMakeHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("cmake", "CMakeLists.txt", 1, 5, "mylib", "library")
        assert symbol_id == "cmake:CMakeLists.txt:1-5:mylib:library"

    def test_make_edge_id_format(self) -> None:
        """Test edge ID is deterministic."""
        edge_id1 = _make_edge_id("src1", "dst1", "links")
        edge_id2 = _make_edge_id("src1", "dst1", "links")
        assert edge_id1 == edge_id2
        assert edge_id1.startswith("edge:sha256:")

class TestProjectExtraction:
    """Branch coverage for project extraction."""

    def test_simple_project(self, tmp_path: Path) -> None:
        """Test simple project extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)
""")
        result = analyze_cmake_files(tmp_path)
        assert not result.skipped

        projects = [s for s in result.symbols if s.kind == "project"]
        assert len(projects) >= 1
        assert any(p.name == "MyProject" for p in projects)

class TestLibraryExtraction:
    """Branch coverage for library target extraction."""

    def test_simple_library(self, tmp_path: Path) -> None:
        """Test simple library extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

add_library(mylib STATIC src/lib.cpp)
""")
        result = analyze_cmake_files(tmp_path)
        libraries = [s for s in result.symbols if s.kind == "library"]
        assert len(libraries) >= 1
        assert any(l.name == "mylib" for l in libraries)

    def test_shared_library(self, tmp_path: Path) -> None:
        """Test shared library extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

add_library(myshared SHARED src/shared.cpp)
""")
        result = analyze_cmake_files(tmp_path)
        libraries = [s for s in result.symbols if s.kind == "library"]
        assert len(libraries) >= 1

class TestExecutableExtraction:
    """Branch coverage for executable target extraction."""

    def test_simple_executable(self, tmp_path: Path) -> None:
        """Test simple executable extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

add_executable(myapp src/main.cpp)
""")
        result = analyze_cmake_files(tmp_path)
        executables = [s for s in result.symbols if s.kind == "executable"]
        assert len(executables) >= 1
        assert any(e.name == "myapp" for e in executables)

class TestFunctionMacroExtraction:
    """Branch coverage for function/macro extraction."""

    def test_function_definition(self, tmp_path: Path) -> None:
        """Test CMake function extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

function(my_helper ARG1 ARG2)
    message("Helper called with ${ARG1} ${ARG2}")
endfunction()
""")
        result = analyze_cmake_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function"]
        assert len(functions) >= 1
        assert any(f.name == "my_helper" for f in functions)

    def test_function_signature(self, tmp_path: Path) -> None:
        """Test function signature extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

function(add_my_library NAME SOURCES)
    add_library(${NAME} ${SOURCES})
endfunction()
""")
        result = analyze_cmake_files(tmp_path)
        functions = [s for s in result.symbols if s.kind == "function" and s.name == "add_my_library"]
        assert len(functions) >= 1
        assert functions[0].signature is not None

    def test_macro_definition(self, tmp_path: Path) -> None:
        """Test CMake macro extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

macro(my_macro ARG)
    message("Macro: ${ARG}")
endmacro()
""")
        result = analyze_cmake_files(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro"]
        assert len(macros) >= 1
        assert any(m.name == "my_macro" for m in macros)

class TestSubdirectoryExtraction:
    """Branch coverage for subdirectory extraction."""

    def test_add_subdirectory(self, tmp_path: Path) -> None:
        """Test add_subdirectory extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

add_subdirectory(src)
add_subdirectory(tests)
""")
        result = analyze_cmake_files(tmp_path)
        subdirs = [s for s in result.symbols if s.kind == "subdirectory"]
        assert len(subdirs) >= 2
        names = [s.name for s in subdirs]
        assert "src" in names
        assert "tests" in names

class TestPackageExtraction:
    """Branch coverage for find_package extraction."""

    def test_find_package(self, tmp_path: Path) -> None:
        """Test find_package extraction."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

find_package(Boost REQUIRED)
find_package(OpenCV)
""")
        result = analyze_cmake_files(tmp_path)
        packages = [s for s in result.symbols if s.kind == "package"]
        assert len(packages) >= 2
        names = [p.name for p in packages]
        assert "Boost" in names
        assert "OpenCV" in names

class TestLinkEdges:
    """Branch coverage for target_link_libraries edge extraction."""

    def test_internal_link(self, tmp_path: Path) -> None:
        """Test link to internal library creates edge."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

add_library(utils STATIC src/utils.cpp)
add_executable(myapp src/main.cpp)
target_link_libraries(myapp PRIVATE utils)
""")
        result = analyze_cmake_files(tmp_path)
        link_edges = [e for e in result.edges if e.edge_type == "links"]
        assert len(link_edges) >= 1

    def test_external_link(self, tmp_path: Path) -> None:
        """Test link to external library."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

add_executable(myapp src/main.cpp)
target_link_libraries(myapp PRIVATE pthread)
""")
        result = analyze_cmake_files(tmp_path)
        link_edges = [e for e in result.edges if e.edge_type == "links"]
        assert len(link_edges) >= 1

    def test_visibility_keywords(self, tmp_path: Path) -> None:
        """Test visibility keywords are skipped."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(MyProject)

add_library(mylib STATIC src/lib.cpp)
add_executable(myapp src/main.cpp)
target_link_libraries(myapp PUBLIC mylib PRIVATE pthread)
""")
        result = analyze_cmake_files(tmp_path)
        link_edges = [e for e in result.edges if e.edge_type == "links"]
        # Should have edges to mylib and pthread, not to PUBLIC/PRIVATE
        assert len(link_edges) >= 2

class TestFindCMakeFiles:
    """Branch coverage for file discovery."""

    def test_finds_cmakelists(self, tmp_path: Path) -> None:
        """Test CMakeLists.txt files are discovered."""
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)")

        files = list(find_cmake_files(tmp_path))
        assert len(files) >= 1
        assert any(f.name == "CMakeLists.txt" for f in files)

    def test_finds_cmake_modules(self, tmp_path: Path) -> None:
        """Test .cmake files are discovered."""
        (tmp_path / "FindMyLib.cmake").write_text("# Find module")

        files = list(find_cmake_files(tmp_path))
        assert len(files) >= 1
        assert any(f.suffix == ".cmake" for f in files)

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "CMakeLists.txt").write_text("add_library(sublib STATIC lib.cpp)")

        files = list(find_cmake_files(tmp_path))
        assert len(files) >= 1

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_cmake_files(self, tmp_path: Path) -> None:
        """Test directory with no CMake files."""
        result = analyze_cmake_files(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_cmake(self, tmp_path: Path) -> None:
        """Test minimal CMake file."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(Minimal)
""")
        result = analyze_cmake_files(tmp_path)
        assert not result.skipped

class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_cmake_file(tmp_path, "CMakeLists.txt", """
cmake_minimum_required(VERSION 3.16)
project(Test)
""")
        result = analyze_cmake_files(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
