"""Tests for centralized path handling utilities."""

import pytest
from pathlib import Path

from hypergumbo_core.paths import (
    normalize_path,
    to_relative_path,
    paths_match,
    path_ends_with,
    get_filename,
    is_under_directory,
    is_test_file,
    is_test_node,
    is_utility_file,
)


class TestNormalizePath:
    """Tests for normalize_path function."""

    def test_forward_slashes_unchanged(self) -> None:
        """Forward slashes remain unchanged."""
        assert normalize_path("src/main.py") == "src/main.py"

    def test_backslashes_converted(self) -> None:
        """Backslashes are converted to forward slashes."""
        assert normalize_path("src\\main.py") == "src/main.py"

    def test_mixed_slashes_normalized(self) -> None:
        """Mixed slashes are all converted to forward."""
        assert normalize_path("src\\sub/main.py") == "src/sub/main.py"

    def test_path_object_converted(self) -> None:
        """Path objects are converted to normalized strings."""
        assert normalize_path(Path("src/main.py")) == "src/main.py"

    def test_absolute_path(self) -> None:
        """Absolute paths are normalized but remain absolute."""
        assert normalize_path("/home/user/repo/main.py") == "/home/user/repo/main.py"

    def test_windows_absolute_path(self) -> None:
        """Windows absolute paths are normalized."""
        assert normalize_path("C:\\Users\\repo\\main.py") == "C:/Users/repo/main.py"


class TestToRelativePath:
    """Tests for to_relative_path function."""

    def test_absolute_to_relative(self, tmp_path: Path) -> None:
        """Absolute path is converted to relative."""
        file_path = tmp_path / "src" / "main.py"
        file_path.parent.mkdir(parents=True)
        file_path.touch()
        result = to_relative_path(file_path, tmp_path)
        assert result == "src/main.py"

    def test_already_relative(self, tmp_path: Path) -> None:
        """Already relative path remains relative."""
        # Create the file so resolve() works
        file_path = tmp_path / "src" / "main.py"
        file_path.parent.mkdir(parents=True)
        file_path.touch()
        # Use string relative path
        result = to_relative_path(str(file_path), tmp_path)
        assert result == "src/main.py"

    def test_path_not_under_root(self, tmp_path: Path) -> None:
        """Path not under repo_root is returned normalized."""
        other_path = "/some/other/path/main.py"
        result = to_relative_path(other_path, tmp_path)
        assert result == "/some/other/path/main.py"

    def test_nested_path(self, tmp_path: Path) -> None:
        """Deeply nested path is properly relativized."""
        file_path = tmp_path / "src" / "pkg" / "sub" / "main.py"
        file_path.parent.mkdir(parents=True)
        file_path.touch()
        result = to_relative_path(file_path, tmp_path)
        assert result == "src/pkg/sub/main.py"


class TestPathsMatch:
    """Tests for paths_match function."""

    def test_exact_match(self) -> None:
        """Identical paths match."""
        assert paths_match("src/main.py", "src/main.py") is True

    def test_slash_normalization(self) -> None:
        """Paths with different slash styles match."""
        assert paths_match("src/main.py", "src\\main.py") is True

    def test_absolute_vs_relative(self) -> None:
        """Absolute path matches relative suffix."""
        assert paths_match("/home/user/repo/src/main.py", "src/main.py") is True

    def test_relative_vs_absolute(self) -> None:
        """Relative path matches absolute suffix."""
        assert paths_match("src/main.py", "/home/user/repo/src/main.py") is True

    def test_no_match(self) -> None:
        """Different paths don't match."""
        assert paths_match("src/main.py", "src/other.py") is False

    def test_partial_filename_no_match(self) -> None:
        """Partial filename should not match."""
        assert paths_match("src/domain.py", "main.py") is False


class TestPathEndsWith:
    """Tests for path_ends_with function."""

    def test_exact_suffix(self) -> None:
        """Exact suffix matches."""
        assert path_ends_with("/home/user/repo/src/main.py", "src/main.py") is True

    def test_filename_only(self) -> None:
        """Filename-only suffix matches."""
        assert path_ends_with("src/main.py", "main.py") is True

    def test_full_path_as_suffix(self) -> None:
        """Full path matches itself as suffix."""
        assert path_ends_with("src/main.py", "src/main.py") is True

    def test_partial_filename_no_match(self) -> None:
        """Partial filename doesn't match (domain.py vs main.py)."""
        assert path_ends_with("src/domain.py", "main.py") is False

    def test_partial_directory_no_match(self) -> None:
        """Partial directory name doesn't match."""
        assert path_ends_with("src/testing/main.py", "sting/main.py") is False

    def test_empty_suffix(self) -> None:
        """Empty suffix returns False."""
        assert path_ends_with("src/main.py", "") is False

    def test_leading_slash_in_suffix(self) -> None:
        """Leading slash in suffix is stripped."""
        assert path_ends_with("/home/repo/src/main.py", "/src/main.py") is True

    def test_backslash_normalization(self) -> None:
        """Backslashes are normalized before comparison."""
        assert path_ends_with("src\\main.py", "main.py") is True


class TestGetFilename:
    """Tests for get_filename function."""

    def test_simple_path(self) -> None:
        """Extract filename from simple path."""
        assert get_filename("src/main.py") == "main.py"

    def test_deep_path(self) -> None:
        """Extract filename from deeply nested path."""
        assert get_filename("a/b/c/d/file.txt") == "file.txt"

    def test_filename_only(self) -> None:
        """Filename without directory returns itself."""
        assert get_filename("main.py") == "main.py"

    def test_backslash_path(self) -> None:
        """Backslashes are normalized before extraction."""
        assert get_filename("src\\main.py") == "main.py"

    def test_absolute_path(self) -> None:
        """Absolute path filename extraction."""
        assert get_filename("/home/user/main.py") == "main.py"


class TestIsUnderDirectory:
    """Tests for is_under_directory function."""

    def test_direct_child(self) -> None:
        """File directly under directory."""
        assert is_under_directory("tests/test_main.py", "tests") is True

    def test_nested_child(self) -> None:
        """File nested under directory."""
        assert is_under_directory("src/tests/unit/test_main.py", "tests") is True

    def test_not_under_directory(self) -> None:
        """File not under directory."""
        assert is_under_directory("src/main.py", "tests") is False

    def test_case_insensitive(self) -> None:
        """Directory matching is case-insensitive."""
        assert is_under_directory("src/Tests/test_main.py", "tests") is True

    def test_filename_not_matched(self) -> None:
        """Filename is not matched as directory."""
        assert is_under_directory("tests.py", "tests") is False

    def test_backslash_path(self) -> None:
        """Backslash paths are normalized."""
        assert is_under_directory("src\\tests\\main.py", "tests") is True


class TestIsTestFile:
    """Tests for is_test_file function."""

    def test_test_prefix(self) -> None:
        """Files starting with test_ are test files."""
        assert is_test_file("test_main.py") is True
        assert is_test_file("src/test_utils.py") is True

    def test_test_suffix(self) -> None:
        """Files with _test suffix are test files."""
        assert is_test_file("main_test.py") is True
        assert is_test_file("utils_test.go") is True

    def test_dot_test_suffix(self) -> None:
        """Files with .test. suffix are test files."""
        assert is_test_file("main.test.py") is True
        assert is_test_file("main.test.js") is True

    def test_spec_patterns(self) -> None:
        """Spec files are test files."""
        assert is_test_file("spec_main.py") is True
        assert is_test_file("main_spec.rb") is True
        assert is_test_file("main.spec.js") is True

    def test_mock_patterns(self) -> None:
        """Mock/fake files are test files."""
        assert is_test_file("main_mock.py") is True
        assert is_test_file("mock_client.py") is True
        assert is_test_file("fake_server.go") is True

    def test_test_directories(self) -> None:
        """Files in test directories are test files."""
        assert is_test_file("tests/main.py") is True
        assert is_test_file("test/helper.py") is True
        assert is_test_file("spec/support.rb") is True
        assert is_test_file("__tests__/main.js") is True

    def test_spec_only_matches_at_root(self) -> None:
        """spec/ only matches as first path component, not nested."""
        # Root-level spec is a test directory (Ruby RSpec convention)
        assert is_test_file("spec/support.rb") is True
        assert is_test_file("spec/factories/user.rb") is True
        # Nested spec is NOT a test directory (e.g., Airflow listeners/spec/)
        assert is_test_file("airflow/listeners/spec/listener.py") is False
        assert is_test_file("src/listeners/spec/base.py") is False

    def test_testing_directory(self) -> None:
        """Files in testing/ directories are test files (Go convention)."""
        assert is_test_file("src/testing/suite.go") is True
        assert is_test_file("pkg/utils/testing/helpers.go") is True

    def test_testutil_directory(self) -> None:
        """Files in testutil/ directories are test files."""
        assert is_test_file("testutil/helpers.go") is True
        assert is_test_file("pkg/testutil/mock.go") is True

    def test_testhelper_directories(self) -> None:
        """Files in testhelper/testhelpers directories are test files."""
        assert is_test_file("testhelper/setup.py") is True
        assert is_test_file("testhelpers/factory.rb") is True

    def test_rust_colocated_test_modules(self) -> None:
        """Rust co-located test modules (tests.rs, testonly.rs) are test files."""
        assert is_test_file("core/lib/dal/src/consensus/tests.rs") is True
        assert is_test_file("core/lib/vm_executor/src/testonly.rs") is True
        assert is_test_file("src/tests.rs") is True
        assert is_test_file("testonly.rs") is True
        # But regular Rust files are not
        assert is_test_file("src/lib.rs") is False
        assert is_test_file("src/main.rs") is False
        assert is_test_file("src/consensus.rs") is False

    def test_fv_and_harness_directories(self) -> None:
        """Files in fv/ and harnesses/ directories are test files."""
        assert is_test_file("fv/harnesses/TokenHarness.sol") is True
        assert is_test_file("fv/specs/Token.spec") is True
        assert is_test_file("harnesses/AccessManagerHarness.sol") is True
        assert is_test_file("contracts/harnesses/mock.sol") is True

    def test_benchmark_directories(self) -> None:
        """Files in bench/benches/benchmark/benchmarks directories are test files.

        Benchmarks are non-production code that should be excluded from
        production slices (--exclude-tests). Same treatment as fv/ and harnesses/.
        """
        assert is_test_file("benches/slow.rs") is True
        assert is_test_file("bench/speed.go") is True
        assert is_test_file("benchmarks/perf.py") is True
        assert is_test_file("benchmark/gcbench.d") is True
        assert is_test_file("crates/core/benches/prover_bench.rs") is True

    def test_not_test_file(self) -> None:
        """Regular files are not test files."""
        assert is_test_file("main.py") is False
        assert is_test_file("src/utils.py") is False
        assert is_test_file("lib/test.py") is False  # test is the filename, not dir


class TestIsUtilityFile:
    """Tests for is_utility_file function."""

    def test_docs_directories(self) -> None:
        """Files in docs directories are utility files."""
        assert is_utility_file("docs/guide.md") is True
        assert is_utility_file("docs_src/tutorial.py") is True
        assert is_utility_file("documentation/api.rst") is True

    def test_examples_directories(self) -> None:
        """Files in examples directories are utility files."""
        assert is_utility_file("examples/basic.py") is True
        assert is_utility_file("example/simple.js") is True
        assert is_utility_file("samples/demo.py") is True

    def test_scripts_directories(self) -> None:
        """Files in scripts directories are utility files."""
        assert is_utility_file("scripts/deploy.sh") is True
        assert is_utility_file("tools/build.py") is True
        assert is_utility_file("bin/run.sh") is True

    def test_benchmarks_directories(self) -> None:
        """Files in benchmark directories are utility files."""
        assert is_utility_file("benchmarks/perf.py") is True
        assert is_utility_file("benchmark/gcbench/testgc3.d") is True
        assert is_utility_file("benches/slow.rs") is True  # Rust convention
        assert is_utility_file("bench/speed.go") is True

    def test_build_system_directories(self) -> None:
        """Files in build system directories are utility files."""
        assert is_utility_file("vcbuild/msvc-lib.d") is True
        assert is_utility_file("cmake/FindFoo.cmake") is True

    def test_dev_directories(self) -> None:
        """Files in dev/ directories are utility files."""
        assert is_utility_file("dev/check_providers.py") is True
        assert is_utility_file("dev/update_versions.py") is True
        assert is_utility_file("dev/breeze/src/tool.py") is True

    def test_contrib_and_hack_directories(self) -> None:
        """Files in contrib/ and hack/ directories are utility files."""
        assert is_utility_file("contrib/nginx.conf") is True
        assert is_utility_file("hack/update-codegen.sh") is True

    def test_devel_prefixed_directories(self) -> None:
        """Directories starting with 'devel' are utility files.

        Airflow uses devel-common/ for CI tooling (get_console, run_command).
        This catches devel/, devel-common/, devel-tools/, etc.
        """
        assert is_utility_file("devel-common/src/tool.py") is True
        assert is_utility_file("devel-common/ci/check_providers.py") is True
        assert is_utility_file("devel/scripts/run.sh") is True
        # Should NOT match production paths that happen to contain "devel"
        assert is_utility_file("src/devel_utils.py") is False

    def test_build_rs_is_utility(self) -> None:
        """Cargo build.rs scripts are utility files.

        build.rs is Cargo's compile-time build script — not user-facing code.
        Its main() should not outrank library exports in entrypoint ranking.
        """
        assert is_utility_file("build.rs") is True
        assert is_utility_file("crate/build.rs") is True
        assert is_utility_file("sub/crate/build.rs") is True
        # Regular Rust files should NOT match
        assert is_utility_file("src/lib.rs") is False
        assert is_utility_file("src/main.rs") is False

    def test_not_utility_file(self) -> None:
        """Regular source files are not utility files."""
        assert is_utility_file("src/main.py") is False
        assert is_utility_file("lib/utils.py") is False
        assert is_utility_file("app/views.py") is False

    def test_case_insensitive(self) -> None:
        """Directory matching is case-insensitive."""
        assert is_utility_file("Examples/demo.py") is True
        assert is_utility_file("DOCS/guide.md") is True


class TestIsTestNode:
    """Tests for is_test_node: checks both file path and annotations."""

    def test_test_file_path_detected(self) -> None:
        """Nodes in test file paths are detected as test nodes."""
        assert is_test_node("tests/test_main.py", None) is True
        assert is_test_node("test_main.py", None) is True

    def test_non_test_path_no_meta(self) -> None:
        """Non-test path with no meta is not a test node."""
        assert is_test_node("src/main.py", None) is False

    def test_non_test_path_empty_meta(self) -> None:
        """Non-test path with empty meta is not a test node."""
        assert is_test_node("src/main.py", {}) is False

    def test_rust_test_attribute(self) -> None:
        """Rust #[test] attribute makes node a test node."""
        meta = {"decorators": [{"name": "test", "args": [], "kwargs": {}}]}
        assert is_test_node("src/lib.rs", meta) is True

    def test_rust_cfg_test_attribute(self) -> None:
        """Rust #[cfg(test)] attribute makes node a test node."""
        meta = {"decorators": [{"name": "cfg", "args": ["test"], "kwargs": {}}]}
        assert is_test_node("src/lib.rs", meta) is True

    def test_rust_tokio_test_attribute(self) -> None:
        """Rust #[tokio::test] attribute makes node a test node."""
        meta = {"decorators": [{"name": "tokio::test", "args": [], "kwargs": {}}]}
        assert is_test_node("src/lib.rs", meta) is True

    def test_python_pytest_mark(self) -> None:
        """Python @pytest.mark.* decorator is not test indicator by itself.

        Python test functions are in test files, not marked by decorator alone.
        The decorator doesn't indicate the node IS a test, just that it has
        pytest configuration. is_test_node checks for pytest.fixture though.
        """
        meta = {"decorators": [{"name": "pytest.fixture", "args": [], "kwargs": {}}]}
        # Fixtures are test infrastructure
        assert is_test_node("src/conftest.py", meta) is False  # conftest is not test_*

    def test_non_test_decorator_ignored(self) -> None:
        """Decorators like #[derive(Debug)] don't make a node a test."""
        meta = {"decorators": [{"name": "derive", "args": ["Debug"], "kwargs": {}}]}
        assert is_test_node("src/lib.rs", meta) is False

    def test_multiple_decorators_one_test(self) -> None:
        """If any decorator is a test annotation, node is test."""
        meta = {"decorators": [
            {"name": "derive", "args": ["Debug"], "kwargs": {}},
            {"name": "test", "args": [], "kwargs": {}},
        ]}
        assert is_test_node("src/lib.rs", meta) is True

    def test_annotations_key_also_checked(self) -> None:
        """The 'annotations' key is also checked (alternative to 'decorators')."""
        meta = {"annotations": [{"name": "test", "args": [], "kwargs": {}}]}
        assert is_test_node("src/lib.rs", meta) is True

    def test_java_test_annotation(self) -> None:
        """Java @Test annotation makes node a test node."""
        meta = {"decorators": [{"name": "Test", "args": [], "kwargs": {}}]}
        assert is_test_node("src/main/java/Foo.java", meta) is True

    def test_junit_annotation(self) -> None:
        """Java @org.junit.Test annotation makes node a test node."""
        meta = {"decorators": [{"name": "org.junit.Test", "args": [], "kwargs": {}}]}
        assert is_test_node("src/main/java/Foo.java", meta) is True
