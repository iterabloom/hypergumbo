"""Tests for the selection.filters module."""

from hypergumbo.selection.filters import (
    is_test_path,
    is_example_path,
    EXCLUDED_KINDS,
    EXAMPLE_PATH_PATTERNS,
)


class TestIsTestPath:
    """Tests for is_test_path function."""

    def test_test_directory(self):
        """Paths in test directories detected."""
        assert is_test_path("tests/test_main.py")
        assert is_test_path("test/test_utils.py")
        assert is_test_path("src/__tests__/Component.test.js")

    def test_test_prefix(self):
        """Files with test_ prefix detected."""
        assert is_test_path("test_main.py")
        assert is_test_path("src/test_utils.py")

    def test_test_suffix(self):
        """Files with test/spec suffix detected."""
        assert is_test_path("main.test.py")
        assert is_test_path("main.spec.js")
        assert is_test_path("Component.test.tsx")
        assert is_test_path("utils_test.py")

    def test_production_files(self):
        """Production files not matched."""
        assert not is_test_path("src/main.py")
        assert not is_test_path("lib/utils.js")
        assert not is_test_path("contest.py")  # contains 'test' but not a test file

    def test_empty_path(self):
        """Empty path returns False."""
        assert not is_test_path("")

    def test_gradle_test_fixtures(self):
        """Gradle test fixtures directory detected."""
        assert is_test_path("src/testFixtures/java/Utils.java")
        assert is_test_path("lib/testfixtures/Helper.kt")

    def test_gradle_integration_tests(self):
        """Gradle integration test directories detected."""
        assert is_test_path("src/intTest/java/IntegrationTest.java")
        assert is_test_path("src/integrationTest/kotlin/ApiTest.kt")

    def test_typescript_type_tests(self):
        """TypeScript type definition test files detected."""
        assert is_test_path("types/index.test-d.ts")
        assert is_test_path("src/types/api.test-d.tsx")

    def test_go_test_files(self):
        """Go test files detected."""
        assert is_test_path("pkg/handler_test.go")
        assert is_test_path("main_test.go")

    def test_rust_test_files(self):
        """Rust test files detected."""
        assert is_test_path("src/lib_test.rs")
        assert is_test_path("tests/integration_test.rs")

    def test_swift_test_files(self):
        """Swift test files detected."""
        assert is_test_path("Tests/RouteTests.swift")
        assert is_test_path("AppTests.swift")
        # Should not match TestHelpers.swift
        assert not is_test_path("src/TestHelpers.swift")

    def test_java_kotlin_test_files(self):
        """Java/Kotlin test files detected."""
        assert is_test_path("src/test/java/AppTest.java")
        assert is_test_path("UserServiceTests.java")
        assert is_test_path("HandlerTest.kt")
        assert is_test_path("RepositoryTests.kt")


class TestIsExamplePath:
    """Tests for is_example_path function."""

    def test_examples_directory(self):
        """examples/ directory detected."""
        assert is_example_path("/project/examples/demo.py")
        assert is_example_path("examples/basic/main.js")

    def test_example_singular(self):
        """example/ (singular) directory detected."""
        assert is_example_path("/project/example/demo.py")
        assert is_example_path("example/main.js")

    def test_demos_directory(self):
        """demos/ directory detected."""
        assert is_example_path("/project/demos/showcase.py")
        assert is_example_path("demos/app.js")

    def test_demo_singular(self):
        """demo/ (singular) directory detected."""
        assert is_example_path("/project/demo/app.py")
        assert is_example_path("demo/main.js")

    def test_samples_directory(self):
        """samples/ directory detected."""
        assert is_example_path("/project/samples/quick.py")
        assert is_example_path("samples/api.js")

    def test_sample_singular(self):
        """sample/ (singular) directory detected."""
        assert is_example_path("/project/sample/app.py")
        assert is_example_path("sample/main.js")

    def test_playground_directory(self):
        """playground/ directory detected."""
        assert is_example_path("/project/playground/test.py")
        assert is_example_path("playground/experiment.js")

    def test_tutorials_directory(self):
        """tutorials/ and tutorial/ directories detected."""
        assert is_example_path("/project/tutorials/lesson1.py")
        assert is_example_path("tutorial/getting-started.md")

    def test_production_files(self):
        """Production files not matched."""
        assert not is_example_path("src/main.py")
        assert not is_example_path("lib/utils.js")
        assert not is_example_path("app/example_helper.py")

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        assert is_example_path("Examples/demo.py")
        assert is_example_path("DEMOS/app.js")


class TestExcludedKinds:
    """Tests for EXCLUDED_KINDS constant."""

    def test_dependency_excluded(self):
        """Dependency kinds are in the set."""
        assert "dependency" in EXCLUDED_KINDS
        assert "devDependency" in EXCLUDED_KINDS

    def test_file_excluded(self):
        """File-level kinds are in the set."""
        assert "file" in EXCLUDED_KINDS
        assert "target" in EXCLUDED_KINDS
        assert "special_target" in EXCLUDED_KINDS

    def test_code_kinds_not_excluded(self):
        """Actual code kinds are NOT in the set."""
        assert "function" not in EXCLUDED_KINDS
        assert "class" not in EXCLUDED_KINDS
        assert "method" not in EXCLUDED_KINDS


class TestExamplePathPatterns:
    """Tests for EXAMPLE_PATH_PATTERNS constant."""

    def test_expected_patterns(self):
        """All expected patterns are present."""
        patterns = EXAMPLE_PATH_PATTERNS
        assert "/examples/" in patterns
        assert "/example/" in patterns
        assert "/demos/" in patterns
        assert "/demo/" in patterns
        assert "/samples/" in patterns
        assert "/sample/" in patterns
        assert "/playground/" in patterns
        assert "/tutorial/" in patterns
        assert "/tutorials/" in patterns
