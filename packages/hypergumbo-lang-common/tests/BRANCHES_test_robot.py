"""Branch coverage tests for robot.py analyzer.

Tests specific branch paths in the Robot Framework analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Keyword extraction
- Test case extraction
- Variable extraction
- Library/Resource imports
- Keyword call edges
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_lang_common.robot import (
    _make_symbol_id,
    analyze_robot,
    find_robot_files,
)


def make_robot_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Robot Framework file with given content."""
    (tmp_path / name).write_text(content)


class TestRobotHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format."""
        from pathlib import Path
        symbol_id = _make_symbol_id(Path("tests/login.robot"), "Login User", "keyword")
        assert symbol_id == "robot:tests/login.robot:keyword:Login User"


class TestKeywordExtraction:
    """Branch coverage for keyword extraction."""

    def test_simple_keyword(self, tmp_path: Path) -> None:
        """Test simple keyword extraction."""
        make_robot_file(tmp_path, "keywords.robot", """*** Keywords ***
Login User
    Log    Logging in user
""")
        result = analyze_robot(tmp_path)
        assert not result.skipped

        keywords = [s for s in result.symbols if s.kind == "keyword"]
        assert len(keywords) >= 1
        assert any(k.name == "Login User" for k in keywords)

    def test_keyword_with_arguments(self, tmp_path: Path) -> None:
        """Test keyword with arguments."""
        make_robot_file(tmp_path, "keywords.robot", """*** Keywords ***
Login With Credentials
    [Arguments]    ${username}    ${password}
    Input Text    username_field    ${username}
    Input Text    password_field    ${password}
""")
        result = analyze_robot(tmp_path)
        keywords = [s for s in result.symbols if s.kind == "keyword"]
        assert len(keywords) >= 1
        # Check that arguments are captured
        kw = next((k for k in keywords if k.name == "Login With Credentials"), None)
        assert kw is not None
        assert kw.meta is not None

    def test_keyword_with_documentation(self, tmp_path: Path) -> None:
        """Test keyword with documentation."""
        make_robot_file(tmp_path, "keywords.robot", """*** Keywords ***
My Keyword
    [Documentation]    This is my keyword documentation
    Log    Hello
""")
        result = analyze_robot(tmp_path)
        keywords = [s for s in result.symbols if s.kind == "keyword"]
        assert len(keywords) >= 1

    def test_keyword_with_tags(self, tmp_path: Path) -> None:
        """Test keyword with tags."""
        make_robot_file(tmp_path, "keywords.robot", """*** Keywords ***
Tagged Keyword
    [Tags]    smoke    regression
    Log    Tagged
""")
        result = analyze_robot(tmp_path)
        keywords = [s for s in result.symbols if s.kind == "keyword"]
        assert len(keywords) >= 1


class TestTestCaseExtraction:
    """Branch coverage for test case extraction."""

    def test_simple_test_case(self, tmp_path: Path) -> None:
        """Test simple test case extraction."""
        make_robot_file(tmp_path, "tests.robot", """*** Test Cases ***
Valid Login
    Log    Testing valid login
""")
        result = analyze_robot(tmp_path)
        test_cases = [s for s in result.symbols if s.kind == "test_case"]
        assert len(test_cases) >= 1
        assert any(t.name == "Valid Login" for t in test_cases)

    def test_test_case_with_documentation(self, tmp_path: Path) -> None:
        """Test test case with documentation."""
        make_robot_file(tmp_path, "tests.robot", """*** Test Cases ***
My Test
    [Documentation]    Test documentation here
    Log    Testing
""")
        result = analyze_robot(tmp_path)
        test_cases = [s for s in result.symbols if s.kind == "test_case"]
        assert len(test_cases) >= 1

    def test_test_case_with_tags(self, tmp_path: Path) -> None:
        """Test test case with tags."""
        make_robot_file(tmp_path, "tests.robot", """*** Test Cases ***
Tagged Test
    [Tags]    critical    api
    Log    Tagged test
""")
        result = analyze_robot(tmp_path)
        test_cases = [s for s in result.symbols if s.kind == "test_case"]
        assert len(test_cases) >= 1


class TestVariableExtraction:
    """Branch coverage for variable extraction."""

    def test_scalar_variable(self, tmp_path: Path) -> None:
        """Test scalar variable extraction."""
        make_robot_file(tmp_path, "vars.robot", """*** Variables ***
${BASE_URL}    https://example.com
""")
        result = analyze_robot(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) >= 1


class TestLibraryImports:
    """Branch coverage for library imports."""

    def test_library_import(self, tmp_path: Path) -> None:
        """Test library import extraction."""
        make_robot_file(tmp_path, "tests.robot", """*** Settings ***
Library    SeleniumLibrary

*** Test Cases ***
Test
    Log    Hello
""")
        result = analyze_robot(tmp_path)
        libraries = [s for s in result.symbols if s.kind == "library"]
        assert len(libraries) >= 1
        assert any(lib.name == "SeleniumLibrary" for lib in libraries)


class TestResourceImports:
    """Branch coverage for resource imports."""

    def test_resource_import(self, tmp_path: Path) -> None:
        """Test resource import extraction."""
        make_robot_file(tmp_path, "tests.robot", """*** Settings ***
Resource    keywords.robot

*** Test Cases ***
Test
    Log    Hello
""")
        result = analyze_robot(tmp_path)
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) >= 1

    def test_resource_creates_import_edge(self, tmp_path: Path) -> None:
        """Test resource import creates edge."""
        make_robot_file(tmp_path, "tests.robot", """*** Settings ***
Resource    common.robot

*** Test Cases ***
Test
    Log    Hello
""")
        result = analyze_robot(tmp_path)
        import_edges = [e for e in result.edges if e.edge_type == "imports"]
        assert len(import_edges) >= 1


class TestKeywordCallEdges:
    """Branch coverage for keyword call edge extraction."""

    def test_keyword_call_from_test(self, tmp_path: Path) -> None:
        """Test keyword call from test case creates edge."""
        make_robot_file(tmp_path, "tests.robot", """*** Keywords ***
My Custom Keyword
    Log    Custom

*** Test Cases ***
Test Using Keyword
    My Custom Keyword
""")
        result = analyze_robot(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        custom_calls = [e for e in call_edges if "My Custom Keyword" in e.dst]
        assert len(custom_calls) >= 1

    def test_keyword_call_from_keyword(self, tmp_path: Path) -> None:
        """Test keyword call from another keyword creates edge."""
        make_robot_file(tmp_path, "keywords.robot", """*** Keywords ***
Helper Keyword
    Log    Helper

Main Keyword
    Helper Keyword
""")
        result = analyze_robot(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        helper_calls = [e for e in call_edges if "Helper Keyword" in e.dst]
        assert len(helper_calls) >= 1

    def test_builtin_keyword_not_tracked(self, tmp_path: Path) -> None:
        """Test builtin keywords don't create edges."""
        make_robot_file(tmp_path, "tests.robot", """*** Test Cases ***
Test
    Log    Hello
    Sleep    1s
""")
        result = analyze_robot(tmp_path)
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Log and Sleep are builtins, should not create edges
        log_calls = [e for e in call_edges if "Log" in e.dst]
        assert len(log_calls) == 0


class TestFindRobotFiles:
    """Branch coverage for file discovery."""

    def test_finds_robot_files(self, tmp_path: Path) -> None:
        """Test .robot files are discovered."""
        (tmp_path / "tests.robot").write_text("*** Test Cases ***\nTest\n    Log    Hi")

        files = list(find_robot_files(tmp_path))
        assert len(files) == 1
        assert files[0].suffix == ".robot"

    def test_finds_nested_files(self, tmp_path: Path) -> None:
        """Test files in nested directories are found."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "login.robot").write_text("*** Test Cases ***\nTest\n    Log    Hi")

        files = list(find_robot_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "login.robot"


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_robot_files(self, tmp_path: Path) -> None:
        """Test directory with no Robot files."""
        result = analyze_robot(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_test_case(self, tmp_path: Path) -> None:
        """Test minimal Robot test case."""
        make_robot_file(tmp_path, "min.robot", """*** Test Cases ***
Minimal Test
    No Operation
""")
        result = analyze_robot(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_robot_file(tmp_path, "tests.robot", """*** Test Cases ***
Test
    Log    Hello
""")
        result = analyze_robot(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1
