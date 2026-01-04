"""Tests for CMake analyzer using tree-sitter-cmake.

Tests verify that the analyzer correctly extracts:
- Project definitions
- Library targets (add_library)
- Executable targets (add_executable)
- Function definitions
- Macro definitions
- Target link dependencies
- Subdirectory includes
"""

from hypergumbo.analyze.cmake import (
    PASS_ID,
    PASS_VERSION,
    CMakeAnalysisResult,
    analyze_cmake_files,
    find_cmake_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "cmake-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_project(tmp_path):
    """Test detection of project definition."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
cmake_minimum_required(VERSION 3.14)
project(MyProject VERSION 1.0)
""")
    result = analyze_cmake_files(tmp_path)

    assert not result.skipped
    projects = [s for s in result.symbols if s.kind == "project"]
    assert len(projects) >= 1
    assert projects[0].name == "MyProject"
    assert projects[0].language == "cmake"


def test_analyze_library(tmp_path):
    """Test detection of library target."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
add_library(mylib STATIC lib.cpp)
add_library(myshared SHARED shared.cpp)
""")
    result = analyze_cmake_files(tmp_path)

    libraries = [s for s in result.symbols if s.kind == "library"]
    assert len(libraries) >= 2
    names = [l.name for l in libraries]
    assert "mylib" in names
    assert "myshared" in names


def test_analyze_executable(tmp_path):
    """Test detection of executable target."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
add_executable(myapp main.cpp)
add_executable(mytool tool.cpp)
""")
    result = analyze_cmake_files(tmp_path)

    executables = [s for s in result.symbols if s.kind == "executable"]
    assert len(executables) >= 2
    names = [e.name for e in executables]
    assert "myapp" in names
    assert "mytool" in names


def test_analyze_function(tmp_path):
    """Test detection of CMake function definition."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
function(my_helper ARG1 ARG2)
    message(STATUS "${ARG1}")
endfunction()
""")
    result = analyze_cmake_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    assert functions[0].name == "my_helper"


def test_analyze_macro(tmp_path):
    """Test detection of CMake macro definition."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
macro(my_macro ARG)
    set(${ARG} TRUE)
endmacro()
""")
    result = analyze_cmake_files(tmp_path)

    macros = [s for s in result.symbols if s.kind == "macro"]
    assert len(macros) >= 1
    assert macros[0].name == "my_macro"


def test_analyze_target_link_libraries(tmp_path):
    """Test detection of target_link_libraries edges."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
add_library(mylib STATIC lib.cpp)
add_executable(myapp main.cpp)
target_link_libraries(myapp PRIVATE mylib)
""")
    result = analyze_cmake_files(tmp_path)

    # Should have library and executable
    libraries = [s for s in result.symbols if s.kind == "library"]
    executables = [s for s in result.symbols if s.kind == "executable"]
    assert len(libraries) >= 1
    assert len(executables) >= 1

    # Should have links edge
    link_edges = [e for e in result.edges if e.edge_type == "links"]
    assert len(link_edges) >= 1
    # Edge should connect myapp to mylib
    assert link_edges[0].confidence == 0.90


def test_analyze_external_library_link(tmp_path):
    """Test detection of links to external libraries."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
add_executable(myapp main.cpp)
target_link_libraries(myapp PRIVATE OpenSSL::SSL pthread)
""")
    result = analyze_cmake_files(tmp_path)

    # Should have links edges to external libraries
    link_edges = [e for e in result.edges if e.edge_type == "links"]
    assert len(link_edges) >= 2
    # External libraries should have lower confidence
    for edge in link_edges:
        assert edge.confidence == 0.70
        assert "external" in edge.dst


def test_analyze_find_package(tmp_path):
    """Test detection of find_package."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
find_package(OpenSSL REQUIRED)
find_package(Boost COMPONENTS system)
""")
    result = analyze_cmake_files(tmp_path)

    packages = [s for s in result.symbols if s.kind == "package"]
    assert len(packages) >= 2
    names = [p.name for p in packages]
    assert "OpenSSL" in names
    assert "Boost" in names


def test_analyze_add_subdirectory(tmp_path):
    """Test detection of add_subdirectory."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
add_subdirectory(src)
add_subdirectory(tests)
""")
    result = analyze_cmake_files(tmp_path)

    subdirs = [s for s in result.symbols if s.kind == "subdirectory"]
    assert len(subdirs) >= 2
    names = [s.name for s in subdirs]
    assert "src" in names
    assert "tests" in names


def test_find_cmake_files(tmp_path):
    """Test that CMake files are discovered correctly."""
    (tmp_path / "CMakeLists.txt").write_text("project(Test)")
    (tmp_path / "utils.cmake").write_text("# utility functions")
    (tmp_path / "not_cmake.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "CMakeLists.txt").write_text("add_library(sub sub.cpp)")

    files = list(find_cmake_files(tmp_path))
    # Should find CMakeLists.txt and .cmake files
    assert len(files) >= 3


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no CMake files."""
    result = analyze_cmake_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("project(Test)")

    result = analyze_cmake_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("project(broken {{{{")

    # Should not raise an exception
    result = analyze_cmake_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, CMakeAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""project(TestProject)
add_library(mylib lib.cpp)
""")
    result = analyze_cmake_files(tmp_path)

    projects = [s for s in result.symbols if s.kind == "project"]
    assert len(projects) >= 1

    # Check span
    assert projects[0].span.start_line >= 1
    assert projects[0].span.end_line >= projects[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.cmake import is_cmake_tree_sitter_available

    # The function should return a boolean
    result = is_cmake_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_cmake_files(tmp_path):
    """Test analysis across multiple CMake files."""
    (tmp_path / "CMakeLists.txt").write_text("""
project(MainProject)
add_subdirectory(lib)
add_executable(main main.cpp)
""")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "CMakeLists.txt").write_text("""
add_library(mylib lib.cpp)
""")

    result = analyze_cmake_files(tmp_path)

    projects = [s for s in result.symbols if s.kind == "project"]
    libraries = [s for s in result.symbols if s.kind == "library"]
    executables = [s for s in result.symbols if s.kind == "executable"]

    assert len(projects) >= 1
    assert len(libraries) >= 1
    assert len(executables) >= 1


def test_complete_cmake_example(tmp_path):
    """Test a complete CMake project structure."""
    cmake_file = tmp_path / "CMakeLists.txt"
    cmake_file.write_text("""
cmake_minimum_required(VERSION 3.14)
project(CompleteProject VERSION 1.0)

find_package(OpenSSL REQUIRED)

add_library(core STATIC
    src/core.cpp
    src/utils.cpp
)

add_library(network SHARED
    src/network.cpp
)

add_executable(app main.cpp)

target_link_libraries(network PRIVATE core)
target_link_libraries(app PRIVATE network core OpenSSL::SSL)

function(add_test_target NAME)
    add_executable(${NAME} tests/${NAME}.cpp)
    target_link_libraries(${NAME} PRIVATE core)
endfunction()

macro(configure_options)
    option(ENABLE_TESTS "Enable tests" ON)
endmacro()

add_subdirectory(examples)
""")
    result = analyze_cmake_files(tmp_path)

    # Check for expected symbol kinds
    kinds = {s.kind for s in result.symbols}
    assert "project" in kinds
    assert "library" in kinds
    assert "executable" in kinds
    assert "function" in kinds
    assert "macro" in kinds
    assert "package" in kinds
    assert "subdirectory" in kinds

    # Check for link edges
    link_edges = [e for e in result.edges if e.edge_type == "links"]
    assert len(link_edges) >= 3  # network->core, app->network, app->core, plus external


class TestCMakeSignatureExtraction:
    """Tests for CMake function/macro signature extraction."""

    def test_function_with_params(self, tmp_path):
        """Extract signature for function with parameters."""
        cmake_file = tmp_path / "CMakeLists.txt"
        cmake_file.write_text("""
function(my_helper ARG1 ARG2 ARG3)
    message(STATUS "${ARG1}")
endfunction()
""")
        result = analyze_cmake_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "my_helper"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(ARG1, ARG2, ARG3)"

    def test_function_no_params(self, tmp_path):
        """Extract signature for function with no parameters."""
        cmake_file = tmp_path / "CMakeLists.txt"
        cmake_file.write_text("""
function(no_params_func)
    message(STATUS "No params")
endfunction()
""")
        result = analyze_cmake_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "no_params_func"]
        assert len(funcs) == 1
        assert funcs[0].signature == "()"

    def test_macro_with_params(self, tmp_path):
        """Extract signature for macro with parameters."""
        cmake_file = tmp_path / "CMakeLists.txt"
        cmake_file.write_text("""
macro(my_macro X Y)
    set(${X} ${Y})
endmacro()
""")
        result = analyze_cmake_files(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro" and s.name == "my_macro"]
        assert len(macros) == 1
        assert macros[0].signature == "(X, Y)"

    def test_macro_single_param(self, tmp_path):
        """Extract signature for macro with single parameter."""
        cmake_file = tmp_path / "CMakeLists.txt"
        cmake_file.write_text("""
macro(single_arg_macro ARG)
    message("${ARG}")
endmacro()
""")
        result = analyze_cmake_files(tmp_path)
        macros = [s for s in result.symbols if s.kind == "macro" and s.name == "single_arg_macro"]
        assert len(macros) == 1
        assert macros[0].signature == "(ARG)"
