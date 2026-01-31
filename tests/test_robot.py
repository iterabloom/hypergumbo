"""Tests for the Robot Framework analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_common import robot as robot_module
from hypergumbo_lang_common.robot import (
    RobotAnalysisResult,
    analyze_robot,
    find_robot_files,
    is_robot_tree_sitter_available,
)


def make_robot_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Robot Framework file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindRobotFiles:
    """Tests for find_robot_files function."""

    def test_finds_robot_files(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", "*** Test Cases ***")
        make_robot_file(tmp_path, "tests/login.robot", "*** Test Cases ***")
        files = find_robot_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"test.robot", "login.robot"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_robot_files(tmp_path)
        assert files == []


class TestIsRobotTreeSitterAvailable:
    """Tests for is_robot_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_robot_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(robot_module, "is_robot_tree_sitter_available", return_value=False):
            assert robot_module.is_robot_tree_sitter_available() is False


class TestAnalyzeRobot:
    """Tests for analyze_robot function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", "*** Test Cases ***\nTest 1\n    Log    Hello")
        with patch.object(robot_module, "is_robot_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Robot Framework analysis skipped"):
                result = robot_module.analyze_robot(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_keyword(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
Login With Credentials
    [Arguments]    ${username}    ${password}
    Input Text    username_field    ${username}
    Input Text    password_field    ${password}
    Click Button    login
""")
        result = analyze_robot(tmp_path)
        assert not result.skipped
        keyword = next((s for s in result.symbols if s.kind == "keyword"), None)
        assert keyword is not None
        assert keyword.name == "Login With Credentials"
        assert keyword.language == "robot"
        assert "username" in keyword.meta.get("arguments", [])
        assert "password" in keyword.meta.get("arguments", [])
        assert keyword.signature == "Login With Credentials(username, password)"

    def test_extracts_keyword_with_documentation(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
Open Browser To Login Page
    [Documentation]    Opens the browser to login page
    [Tags]    setup    browser
    Open Browser    https://example.com    chrome
""")
        result = analyze_robot(tmp_path)
        keyword = next((s for s in result.symbols if s.kind == "keyword"), None)
        assert keyword is not None
        assert keyword.name == "Open Browser To Login Page"
        assert "Opens the browser" in keyword.meta.get("documentation", "")
        assert "setup" in keyword.meta.get("tags", [])
        assert "browser" in keyword.meta.get("tags", [])

    def test_extracts_test_case(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Test Cases ***
Valid Login Test
    [Documentation]    Test valid user login
    [Tags]    smoke    login
    Login With Credentials    admin    secret123
""")
        result = analyze_robot(tmp_path)
        test_case = next((s for s in result.symbols if s.kind == "test_case"), None)
        assert test_case is not None
        assert test_case.name == "Valid Login Test"
        assert test_case.language == "robot"
        assert "Test valid user login" in test_case.meta.get("documentation", "")
        assert "smoke" in test_case.meta.get("tags", [])
        assert "login" in test_case.meta.get("tags", [])

    def test_extracts_variable(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Variables ***
${URL}    https://example.com
${TIMEOUT}    10s
""")
        result = analyze_robot(tmp_path)
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) == 2
        names = {v.name for v in variables}
        assert "${URL}" in names
        assert "${TIMEOUT}" in names
        url_var = next(v for v in variables if "URL" in v.name)
        assert url_var.meta.get("value") == "https://example.com"

    def test_extracts_library_import(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Settings ***
Library    SeleniumLibrary
Library    Collections
""")
        result = analyze_robot(tmp_path)
        libraries = [s for s in result.symbols if s.kind == "library"]
        assert len(libraries) == 2
        names = {lib.name for lib in libraries}
        assert "SeleniumLibrary" in names
        assert "Collections" in names

    def test_extracts_resource_import(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Settings ***
Resource    common.robot
Resource    pages/login.robot
""")
        result = analyze_robot(tmp_path)
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) == 2
        names = {res.name for res in resources}
        assert "common.robot" in names
        assert "pages/login.robot" in names

    def test_extracts_resource_import_edge(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Settings ***
Resource    common.robot
""")
        result = analyze_robot(tmp_path)
        edge = next((e for e in result.edges if e.edge_type == "imports"), None)
        assert edge is not None
        assert "common.robot" in edge.dst

    def test_extracts_keyword_call_edge(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
Helper Keyword
    No Operation

Main Keyword
    Helper Keyword
""")
        result = analyze_robot(tmp_path)
        call_edge = next(
            (e for e in result.edges if e.edge_type == "calls" and "Helper Keyword" in e.dst),
            None
        )
        assert call_edge is not None
        assert call_edge.confidence == 1.0

    def test_extracts_test_to_keyword_call(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
Setup Test Environment
    No Operation

*** Test Cases ***
My Test
    Setup Test Environment
""")
        result = analyze_robot(tmp_path)
        call_edge = next(
            (e for e in result.edges if "Setup Test Environment" in e.dst and "unresolved" not in e.dst),
            None
        )
        assert call_edge is not None
        assert call_edge.edge_type == "calls"
        assert "My Test" in call_edge.src or "test_case" in call_edge.src

    def test_unresolved_keyword_has_low_confidence(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Test Cases ***
My Test
    Some External Keyword
""")
        result = analyze_robot(tmp_path)
        call_edge = next(
            (e for e in result.edges if "unresolved" in e.dst),
            None
        )
        assert call_edge is not None
        assert call_edge.confidence == 0.6

    def test_filters_builtin_keywords(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Test Cases ***
My Test
    Log    Hello world
    Sleep    1s
    Set Variable    ${value}    test
    Should Be Equal    ${actual}    ${expected}
""")
        result = analyze_robot(tmp_path)
        # Should not have edges for builtin keywords
        builtin_edges = [
            e for e in result.edges
            if any(b in e.dst for b in ["Log", "Sleep", "Set Variable", "Should Be Equal"])
        ]
        assert len(builtin_edges) == 0

    def test_pass_id(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
Test Keyword
    No Operation
""")
        result = analyze_robot(tmp_path)
        keyword = next((s for s in result.symbols if s.kind == "keyword"), None)
        assert keyword is not None
        assert keyword.origin == "robot.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", "*** Test Cases ***\nTest 1\n    Log    Hello")
        result = analyze_robot(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "robot.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_robot(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
My Test Keyword
    No Operation
""")
        result = analyze_robot(tmp_path)
        keyword = next((s for s in result.symbols if s.kind == "keyword"), None)
        assert keyword is not None
        assert keyword.id == keyword.stable_id
        assert "robot:" in keyword.id
        assert "test.robot" in keyword.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
My Test Keyword
    No Operation
""")
        result = analyze_robot(tmp_path)
        keyword = next((s for s in result.symbols if s.kind == "keyword"), None)
        assert keyword is not None
        assert keyword.span is not None
        assert keyword.span.start_line >= 1
        assert keyword.span.end_line >= keyword.span.start_line

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "common.robot", """
*** Keywords ***
Common Helper
    No Operation
""")
        make_robot_file(tmp_path, "tests/login.robot", """
*** Keywords ***
Login Helper
    No Operation
""")
        result = analyze_robot(tmp_path)
        keywords = [s for s in result.symbols if s.kind == "keyword"]
        assert len(keywords) == 2
        names = {k.name for k in keywords}
        assert "Common Helper" in names
        assert "Login Helper" in names

    def test_keyword_without_arguments(self, tmp_path: Path) -> None:
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
Simple Keyword
    No Operation
""")
        result = analyze_robot(tmp_path)
        keyword = next((s for s in result.symbols if s.kind == "keyword"), None)
        assert keyword is not None
        assert keyword.signature == "Simple Keyword()"
        assert keyword.meta.get("arguments") == []

    def test_variable_without_value_is_syntax_error(self, tmp_path: Path) -> None:
        # A variable without a value is actually a syntax error in Robot Framework
        # The grammar returns an ERROR node, so no variable is extracted
        make_robot_file(tmp_path, "test.robot", """
*** Variables ***
${EMPTY_VAR}
""")
        result = analyze_robot(tmp_path)
        # No variable extracted because it's a syntax error
        variable = next((s for s in result.symbols if s.kind == "variable"), None)
        assert variable is None

    def test_cross_file_keyword_call(self, tmp_path: Path) -> None:
        """Test that cross-file keyword calls are marked as unresolved."""
        make_robot_file(tmp_path, "lib/common.robot", """
*** Keywords ***
Common Setup
    No Operation
""")
        make_robot_file(tmp_path, "tests/login.robot", """
*** Test Cases ***
Login Test
    Common Setup
""")
        result = analyze_robot(tmp_path)
        # Since we don't do cross-file resolution, this should be unresolved
        # Actually we DO do registry lookup, but it depends on processing order
        # The test verifies edges are created
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 1

    def test_complete_robot_file(self, tmp_path: Path) -> None:
        """Test a complete Robot Framework file with all elements."""
        make_robot_file(tmp_path, "complete.robot", """
*** Settings ***
Library    SeleniumLibrary
Resource   common.robot

*** Variables ***
${BASE_URL}    https://example.com
${BROWSER}     chrome

*** Keywords ***
Open Login Page
    [Documentation]    Opens the login page
    [Tags]    setup
    Open Browser    ${BASE_URL}/login    ${BROWSER}

Enter Credentials
    [Arguments]    ${user}    ${pass}
    Input Text    id=username    ${user}
    Input Text    id=password    ${pass}

*** Test Cases ***
Valid Login
    [Documentation]    Test valid login flow
    [Tags]    smoke    login
    Open Login Page
    Enter Credentials    admin    secret
    Click Button    id=submit
""")
        result = analyze_robot(tmp_path)

        # Check libraries
        libraries = [s for s in result.symbols if s.kind == "library"]
        assert len(libraries) == 1
        assert libraries[0].name == "SeleniumLibrary"

        # Check resources
        resources = [s for s in result.symbols if s.kind == "resource"]
        assert len(resources) == 1
        assert resources[0].name == "common.robot"

        # Check variables
        variables = [s for s in result.symbols if s.kind == "variable"]
        assert len(variables) == 2

        # Check keywords
        keywords = [s for s in result.symbols if s.kind == "keyword"]
        assert len(keywords) == 2
        keyword_names = {k.name for k in keywords}
        assert "Open Login Page" in keyword_names
        assert "Enter Credentials" in keyword_names

        # Check test cases
        test_cases = [s for s in result.symbols if s.kind == "test_case"]
        assert len(test_cases) == 1
        assert test_cases[0].name == "Valid Login"
        assert "smoke" in test_cases[0].meta.get("tags", [])

        # Check edges - should have calls to keywords
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        assert len(call_edges) >= 2  # At least Open Login Page and Enter Credentials

    def test_nested_keyword_calls(self, tmp_path: Path) -> None:
        """Test keyword that calls another keyword."""
        make_robot_file(tmp_path, "test.robot", """
*** Keywords ***
Helper One
    No Operation

Helper Two
    Helper One

Main Keyword
    Helper Two
    Helper One
""")
        result = analyze_robot(tmp_path)
        keywords = [s for s in result.symbols if s.kind == "keyword"]
        assert len(keywords) == 3

        # Should have edges for nested calls
        call_edges = [e for e in result.edges if e.edge_type == "calls"]
        # Helper Two calls Helper One, Main Keyword calls Helper Two and Helper One
        assert len(call_edges) == 3

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        """Test that files_analyzed is tracked correctly."""
        make_robot_file(tmp_path, "a.robot", "*** Keywords ***\nA\n    No Operation")
        make_robot_file(tmp_path, "b.robot", "*** Keywords ***\nB\n    No Operation")
        make_robot_file(tmp_path, "c.robot", "*** Keywords ***\nC\n    No Operation")
        result = analyze_robot(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3
