"""Tests for the discovery module."""
from pathlib import Path

from hypergumbo.discovery import DEFAULT_EXCLUDES, find_files, is_excluded


def test_is_excluded_with_default_patterns(tmp_path: Path) -> None:
    """Should exclude paths matching default patterns."""
    node_modules = tmp_path / "node_modules" / "package" / "index.py"
    assert is_excluded(node_modules, tmp_path) is True


def test_is_excluded_returns_false_for_normal_paths(tmp_path: Path) -> None:
    """Should not exclude normal source paths."""
    src_file = tmp_path / "src" / "app.py"
    assert is_excluded(src_file, tmp_path) is False


def test_is_excluded_with_custom_patterns(tmp_path: Path) -> None:
    """Should respect custom exclude patterns."""
    third_party = tmp_path / "third_party" / "lib.py"
    # Not excluded by default
    assert is_excluded(third_party, tmp_path) is False
    # Excluded with custom pattern
    assert is_excluded(third_party, tmp_path, excludes=["third_party"]) is True


def test_is_excluded_with_path_outside_repo_root(tmp_path: Path) -> None:
    """Should handle paths that are not relative to repo_root."""
    # Create a path that's not under tmp_path
    outside_path = Path("/some/other/node_modules/file.py")
    # Should still work - checks path components
    assert is_excluded(outside_path, tmp_path) is True


def test_is_excluded_matches_glob_patterns(tmp_path: Path) -> None:
    """Should support glob patterns like *.egg-info."""
    egg_info = tmp_path / "mypackage.egg-info" / "PKG-INFO"
    assert is_excluded(egg_info, tmp_path) is True


def test_find_files_yields_matching_files(tmp_path: Path) -> None:
    """Should yield files matching the patterns."""
    py_file = tmp_path / "app.py"
    py_file.write_text("# python")
    txt_file = tmp_path / "readme.txt"
    txt_file.write_text("text")

    results = list(find_files(tmp_path, ["*.py"]))
    assert len(results) == 1
    assert results[0] == py_file


def test_find_files_excludes_by_default(tmp_path: Path) -> None:
    """Should exclude files in default excluded directories."""
    good_file = tmp_path / "src" / "app.py"
    good_file.parent.mkdir()
    good_file.write_text("# good")

    bad_file = tmp_path / "node_modules" / "pkg" / "index.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("# bad")

    results = list(find_files(tmp_path, ["*.py"]))
    assert len(results) == 1
    assert results[0] == good_file


def test_default_excludes_contains_expected_patterns() -> None:
    """DEFAULT_EXCLUDES should contain all expected patterns."""
    expected = [
        "node_modules",
        "vendor",  # PHP Composer dependencies
        "venv",
        ".venv",
        "dist",
        "build",
        ".git",
        "__pycache__",
        ".hypergumbo",  # Hypergumbo output directory
        "hypergumbo.results.json",  # Hypergumbo behavior map
        # Lock files - generated, inflate LOC counts
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "go.sum",
    ]
    for pattern in expected:
        assert pattern in DEFAULT_EXCLUDES


def test_is_excluded_lock_files(tmp_path: Path) -> None:
    """Lock files should be excluded to prevent inflated LOC counts."""
    lock_files = [
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "go.sum",
        "composer.lock",
        "Gemfile.lock",
    ]
    for lock_file in lock_files:
        lock_path = tmp_path / lock_file
        assert is_excluded(lock_path, tmp_path) is True, f"{lock_file} should be excluded"


def test_is_excluded_hypergumbo_artifacts(tmp_path: Path) -> None:
    """Should exclude hypergumbo output artifacts by default.

    The .hypergumbo directory and hypergumbo.results.json file are generated
    by hypergumbo run and should not pollute sketch or analysis input.
    """
    # .hypergumbo directory
    capsule_file = tmp_path / ".hypergumbo" / "capsule.json"
    assert is_excluded(capsule_file, tmp_path) is True

    # hypergumbo.results.json at repo root
    results_file = tmp_path / "hypergumbo.results.json"
    assert is_excluded(results_file, tmp_path) is True

    # hypergumbo.results.json in subdirectory (less common but should still match)
    nested_results = tmp_path / "subdir" / "hypergumbo.results.json"
    assert is_excluded(nested_results, tmp_path) is True


def test_find_files_respects_max_files(tmp_path: Path) -> None:
    """Should limit the number of files returned when max_files is set."""
    # Create 5 Python files
    for i in range(5):
        (tmp_path / f"file{i}.py").write_text(f"# file {i}")

    # Without limit, should find all 5
    results = list(find_files(tmp_path, ["*.py"]))
    assert len(results) == 5

    # With limit of 3, should only return 3
    results = list(find_files(tmp_path, ["*.py"], max_files=3))
    assert len(results) == 3

    # With limit of 0, should return none
    results = list(find_files(tmp_path, ["*.py"], max_files=0))
    assert len(results) == 0

    # With limit higher than available, should return all
    results = list(find_files(tmp_path, ["*.py"], max_files=100))
    assert len(results) == 5
