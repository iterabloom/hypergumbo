"""Tests for centralized path handling utilities."""

import pytest
from pathlib import Path

from hypergumbo.paths import (
    normalize_path,
    to_relative_path,
    paths_match,
    path_ends_with,
    get_filename,
    is_under_directory,
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
