"""Branch coverage tests for fortran.py analyzer.

Tests specific branch paths in the Fortran analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Module and program extraction
- Subroutine and function definitions
- Derived type definitions
- Use statements with aliases
- Call edge extraction
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id
from hypergumbo_lang_common.fortran import _make_edge_id, analyze_fortran_files, find_fortran_files

def make_fortran_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Fortran file with given content."""
    (tmp_path / name).write_text(content)

class TestFortranHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        symbol_id = make_symbol_id("fortran", "src/math.f90", 5, 20, "calculate", "function")
        assert symbol_id == "fortran:src/math.f90:5-20:calculate:function"

    def test_make_edge_id_deterministic(self) -> None:
        """Test edge ID is deterministic."""
        edge_id_1 = _make_edge_id("src1", "dst1", "calls")
        edge_id_2 = _make_edge_id("src1", "dst1", "calls")
        assert edge_id_1 == edge_id_2
        assert edge_id_1.startswith("edge:sha256:")

class TestModuleExtraction:
    """Branch coverage for module definition extraction."""

    def test_single_module(self, tmp_path: Path) -> None:
        """Test single module definition."""
        make_fortran_file(tmp_path, "math.f90", """
module math_utils
    implicit none
contains
    subroutine add(a, b, c)
        integer, intent(in) :: a, b
        integer, intent(out) :: c
        c = a + b
    end subroutine
end module
""")
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped

        modules = [s for s in result.symbols if s.kind == "module"]
        assert len(modules) == 1
        assert modules[0].name == "math_utils"

    def test_multiple_modules(self, tmp_path: Path) -> None:
        """Test multiple modules in one file."""
        make_fortran_file(tmp_path, "libs.f90", """
module lib_a
    implicit none
end module lib_a

module lib_b
    implicit none
end module lib_b
""")
        result = analyze_fortran_files(tmp_path)
        modules = [s for s in result.symbols if s.kind == "module"]
        names = [m.name for m in modules]
        assert "lib_a" in names
        assert "lib_b" in names

class TestProgramExtraction:
    """Branch coverage for program definition extraction."""

    def test_program_definition(self, tmp_path: Path) -> None:
        """Test program definition extraction."""
        make_fortran_file(tmp_path, "main.f90", """
program hello_world
    implicit none
    print *, "Hello, World!"
end program hello_world
""")
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped

        programs = [s for s in result.symbols if s.kind == "program"]
        assert len(programs) == 1
        assert programs[0].name == "hello_world"

class TestSubroutineExtraction:
    """Branch coverage for subroutine definition extraction."""

    def test_subroutine_with_params(self, tmp_path: Path) -> None:
        """Test subroutine with parameters."""
        make_fortran_file(tmp_path, "utils.f90", """
subroutine swap(a, b)
    integer, intent(inout) :: a, b
    integer :: temp
    temp = a
    a = b
    b = temp
end subroutine swap
""")
        result = analyze_fortran_files(tmp_path)
        subs = [s for s in result.symbols if s.kind == "subroutine"]
        assert len(subs) == 1
        assert subs[0].name == "swap"
        # Check signature is extracted
        assert subs[0].signature is not None
        assert "a" in subs[0].signature
        assert "b" in subs[0].signature

class TestFunctionExtraction:
    """Branch coverage for function definition extraction."""

    def test_function_with_result(self, tmp_path: Path) -> None:
        """Test function with result variable."""
        make_fortran_file(tmp_path, "funcs.f90", """
function add(x, y) result(sum)
    integer, intent(in) :: x, y
    integer :: sum
    sum = x + y
end function add
""")
        result = analyze_fortran_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "add"

    def test_multiple_functions(self, tmp_path: Path) -> None:
        """Test multiple function definitions."""
        make_fortran_file(tmp_path, "math.f90", """
function square(x)
    integer, intent(in) :: x
    integer :: square
    square = x * x
end function

function cube(x)
    integer, intent(in) :: x
    integer :: cube
    cube = x * x * x
end function
""")
        result = analyze_fortran_files(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        names = [f.name for f in funcs]
        assert "square" in names
        assert "cube" in names

class TestDerivedTypeExtraction:
    """Branch coverage for derived type extraction."""

    def test_type_definition(self, tmp_path: Path) -> None:
        """Test derived type definition."""
        make_fortran_file(tmp_path, "types.f90", """
module types
    implicit none
    type :: point
        real :: x, y
    end type point
end module
""")
        result = analyze_fortran_files(tmp_path)
        types = [s for s in result.symbols if s.kind == "type"]
        assert len(types) == 1
        assert types[0].name == "point"

class TestUseStatements:
    """Branch coverage for use statement edge extraction."""

    def test_use_statement_edge(self, tmp_path: Path) -> None:
        """Test use statement creates import edge."""
        make_fortran_file(tmp_path, "math.f90", """
module math_utils
    implicit none
contains
    function add(a, b)
        integer, intent(in) :: a, b
        integer :: add
        add = a + b
    end function
end module
""")
        make_fortran_file(tmp_path, "main.f90", """
program main
    use math_utils
    implicit none
    print *, add(1, 2)
end program
""")
        result = analyze_fortran_files(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1

class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_subroutine_call(self, tmp_path: Path) -> None:
        """Test subroutine call creates edge."""
        make_fortran_file(tmp_path, "calls.f90", """
program main
    implicit none
    call helper()
contains
    subroutine helper()
        print *, "Helper called"
    end subroutine
end program
""")
        result = analyze_fortran_files(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.dst]
        assert len(helper_calls) >= 1

class TestFindFortranFiles:
    """Branch coverage for file discovery."""

    def test_finds_f90_files(self, tmp_path: Path) -> None:
        """Test .f90 files are discovered."""
        (tmp_path / "test.f90").write_text("program test\nend program")

        files = list(find_fortran_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".f90"

    def test_finds_f95_files(self, tmp_path: Path) -> None:
        """Test .f95 files are discovered."""
        (tmp_path / "test.f95").write_text("program test\nend program")

        files = list(find_fortran_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".f95"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        src = tmp_path / "src" / "lib"
        src.mkdir(parents=True)
        (src / "math.f90").write_text("module math\nend module")

        files = list(find_fortran_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "math.f90"

class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_minimal_program(self, tmp_path: Path) -> None:
        """Test minimal Fortran program."""
        make_fortran_file(tmp_path, "min.f90", """
program minimal
end program
""")
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped

    def test_no_fortran_files(self, tmp_path: Path) -> None:
        """Test directory with no Fortran files."""
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped
        assert len(result.symbols) == 0

class TestCrossFileResolution:
    """Branch coverage for cross-file symbol resolution."""

    def test_cross_module_call(self, tmp_path: Path) -> None:
        """Test call resolution across modules."""
        make_fortran_file(tmp_path, "helpers.f90", """
module helpers
    implicit none
contains
    subroutine helper_sub()
        print *, "Helper"
    end subroutine
end module
""")
        make_fortran_file(tmp_path, "main.f90", """
program main
    use helpers
    implicit none
    call helper_sub()
end program
""")
        result = analyze_fortran_files(tmp_path)
        assert not result.skipped

        # Should have both module and program
        modules = [s for s in result.symbols if s.kind == "module"]
        programs = [s for s in result.symbols if s.kind == "program"]
        assert len(modules) >= 1
        assert len(programs) >= 1
