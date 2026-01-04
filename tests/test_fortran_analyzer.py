"""Tests for Fortran analyzer using tree-sitter-fortran.

Tests verify that the analyzer correctly extracts:
- Module definitions
- Program definitions
- Function definitions
- Subroutine definitions
- Derived type definitions
- Use statements (imports)
"""

from hypergumbo.analyze.fortran import (
    PASS_ID,
    PASS_VERSION,
    FortranAnalysisResult,
    analyze_fortran_files,
    find_fortran_files,
)


def test_pass_metadata():
    """Verify pass ID and version are set correctly."""
    assert PASS_ID == "fortran-v1"
    assert PASS_VERSION == "hypergumbo-0.1.0"


def test_analyze_module(tmp_path):
    """Test detection of module definitions."""
    fortran_file = tmp_path / "mymodule.f90"
    fortran_file.write_text("""
module matrix_ops
    implicit none
contains
    subroutine dummy()
    end subroutine
end module matrix_ops
""")
    result = analyze_fortran_files(tmp_path)

    assert not result.skipped
    modules = [s for s in result.symbols if s.kind == "module"]
    assert len(modules) >= 1
    assert modules[0].name == "matrix_ops"
    assert modules[0].language == "fortran"


def test_analyze_program(tmp_path):
    """Test detection of program definitions."""
    fortran_file = tmp_path / "main.f90"
    fortran_file.write_text("""
program hello_world
    implicit none
    print *, "Hello, World!"
end program hello_world
""")
    result = analyze_fortran_files(tmp_path)

    programs = [s for s in result.symbols if s.kind == "program"]
    assert len(programs) >= 1
    assert programs[0].name == "hello_world"


def test_analyze_function(tmp_path):
    """Test detection of function definitions."""
    fortran_file = tmp_path / "funcs.f90"
    fortran_file.write_text("""
function add_numbers(a, b) result(c)
    real, intent(in) :: a, b
    real :: c
    c = a + b
end function add_numbers
""")
    result = analyze_fortran_files(tmp_path)

    functions = [s for s in result.symbols if s.kind == "function"]
    assert len(functions) >= 1
    assert functions[0].name == "add_numbers"


def test_analyze_subroutine(tmp_path):
    """Test detection of subroutine definitions."""
    fortran_file = tmp_path / "subs.f90"
    fortran_file.write_text("""
subroutine calculate_sum(a, b, result)
    real, intent(in) :: a, b
    real, intent(out) :: result
    result = a + b
end subroutine calculate_sum
""")
    result = analyze_fortran_files(tmp_path)

    subroutines = [s for s in result.symbols if s.kind == "subroutine"]
    assert len(subroutines) >= 1
    assert subroutines[0].name == "calculate_sum"


def test_analyze_type(tmp_path):
    """Test detection of derived type definitions."""
    fortran_file = tmp_path / "types.f90"
    fortran_file.write_text("""
module my_types
    type :: particle
        real :: x, y, z
        real :: mass
    end type particle
end module my_types
""")
    result = analyze_fortran_files(tmp_path)

    types = [s for s in result.symbols if s.kind == "type"]
    assert len(types) >= 1
    assert types[0].name == "particle"


def test_analyze_use_statement(tmp_path):
    """Test detection of use (import) statements."""
    fortran_file = tmp_path / "main.f90"
    fortran_file.write_text("""
program main
    use matrix_ops
    use iso_fortran_env
    implicit none
end program main
""")
    result = analyze_fortran_files(tmp_path)

    imports = [e for e in result.edges if e.edge_type == "imports"]
    assert len(imports) >= 2


def test_analyze_subroutine_call(tmp_path):
    """Test detection of subroutine calls."""
    fortran_file = tmp_path / "caller.f90"
    fortran_file.write_text("""
program main
    implicit none
    real :: a, b, c
    call calculate_sum(a, b, c)
end program main
""")
    result = analyze_fortran_files(tmp_path)

    calls = [e for e in result.edges if e.edge_type == "calls"]
    assert len(calls) >= 1


def test_find_fortran_files(tmp_path):
    """Test that Fortran files are discovered correctly."""
    (tmp_path / "main.f90").write_text("program main\nend program")
    (tmp_path / "module.f").write_text("module test\nend module")
    (tmp_path / "sub.f95").write_text("subroutine test()\nend")
    (tmp_path / "func.f03").write_text("function test()\nend")
    (tmp_path / "not_fortran.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "sub.f90").write_text("program sub\nend")

    files = list(find_fortran_files(tmp_path))
    # Should find .f, .f90, .f95, .f03 files
    assert len(files) >= 5


def test_analyze_empty_directory(tmp_path):
    """Test analysis of directory with no Fortran files."""
    result = analyze_fortran_files(tmp_path)

    assert not result.skipped
    assert len(result.symbols) == 0
    assert len(result.edges) == 0


def test_analysis_run_metadata(tmp_path):
    """Test that AnalysisRun metadata is correctly set."""
    fortran_file = tmp_path / "test.f90"
    fortran_file.write_text("program test\nend program")

    result = analyze_fortran_files(tmp_path)

    assert result.run is not None
    assert result.run.pass_id == PASS_ID
    assert result.run.version == PASS_VERSION
    assert result.run.files_analyzed >= 1
    assert result.run.duration_ms >= 0


def test_syntax_error_handling(tmp_path):
    """Test that syntax errors don't crash the analyzer."""
    fortran_file = tmp_path / "broken.f90"
    fortran_file.write_text("program broken {{{{")

    # Should not raise an exception
    result = analyze_fortran_files(tmp_path)

    # Result should still be valid
    assert isinstance(result, FortranAnalysisResult)


def test_span_information(tmp_path):
    """Test that span information is correct."""
    fortran_file = tmp_path / "span.f90"
    fortran_file.write_text("""program test
    implicit none
end program test
""")
    result = analyze_fortran_files(tmp_path)

    programs = [s for s in result.symbols if s.kind == "program"]
    assert len(programs) >= 1

    # Check span
    assert programs[0].span.start_line >= 1
    assert programs[0].span.end_line >= programs[0].span.start_line


def test_tree_sitter_not_available():
    """Test graceful degradation when tree-sitter is not available."""
    from hypergumbo.analyze.fortran import is_fortran_tree_sitter_available

    # The function should return a boolean
    result = is_fortran_tree_sitter_available()
    assert isinstance(result, bool)


def test_multiple_fortran_files(tmp_path):
    """Test analysis across multiple Fortran files."""
    (tmp_path / "module.f90").write_text("""
module mymod
    implicit none
end module mymod
""")
    (tmp_path / "main.f90").write_text("""
program main
    use mymod
    implicit none
end program main
""")

    result = analyze_fortran_files(tmp_path)

    modules = [s for s in result.symbols if s.kind == "module"]
    programs = [s for s in result.symbols if s.kind == "program"]
    assert len(modules) >= 1
    assert len(programs) >= 1


def test_complete_fortran_structure(tmp_path):
    """Test a complete Fortran code structure."""
    fortran_file = tmp_path / "complete.f90"
    fortran_file.write_text("""
module physics
    implicit none

    type :: vector3
        real :: x, y, z
    end type vector3

contains

    function cross_product(a, b) result(c)
        type(vector3), intent(in) :: a, b
        type(vector3) :: c
        c%x = a%y * b%z - a%z * b%y
        c%y = a%z * b%x - a%x * b%z
        c%z = a%x * b%y - a%y * b%x
    end function cross_product

    subroutine normalize(v)
        type(vector3), intent(inout) :: v
        real :: mag
        mag = sqrt(v%x**2 + v%y**2 + v%z**2)
        v%x = v%x / mag
        v%y = v%y / mag
        v%z = v%z / mag
    end subroutine normalize

end module physics

program main
    use physics
    implicit none
    type(vector3) :: a, b, c
    c = cross_product(a, b)
    call normalize(c)
end program main
""")
    result = analyze_fortran_files(tmp_path)

    # Check for expected symbol kinds
    kinds = {s.kind for s in result.symbols}
    assert "module" in kinds
    assert "type" in kinds
    assert "function" in kinds
    assert "subroutine" in kinds
    assert "program" in kinds


class TestFortranSignatureExtraction:
    """Tests for Fortran function/subroutine signature extraction."""

    def test_function_with_result_signature(self, tmp_path):
        """Extracts signature from a function with result variable."""
        from hypergumbo.analyze.fortran import analyze_fortran_files

        (tmp_path / "calculator.f90").write_text("""
function add(x, y) result(z)
    integer, intent(in) :: x, y
    integer :: z
    z = x + y
end function add
""")
        result = analyze_fortran_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "add"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(x, y): integer"

    def test_subroutine_signature(self, tmp_path):
        """Extracts signature from subroutine (no return type)."""
        from hypergumbo.analyze.fortran import analyze_fortran_files

        (tmp_path / "logger.f90").write_text("""
subroutine log_message(message)
    character(len=*), intent(in) :: message
    print *, message
end subroutine log_message
""")
        result = analyze_fortran_files(tmp_path)
        subs = [s for s in result.symbols if s.kind == "subroutine" and s.name == "log_message"]
        assert len(subs) == 1
        assert subs[0].signature == "(message)"

    def test_function_no_params_signature(self, tmp_path):
        """Extracts signature from a function with no parameters."""
        from hypergumbo.analyze.fortran import analyze_fortran_files

        (tmp_path / "getter.f90").write_text("""
function get_zero() result(z)
    integer :: z
    z = 0
end function get_zero
""")
        result = analyze_fortran_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function" and s.name == "get_zero"]
        assert len(funcs) == 1
        assert funcs[0].signature == "(): integer"
