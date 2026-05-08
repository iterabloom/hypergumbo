# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/lib/per_package_fallback.py``.

The helper guarantees that every ``packages/<P>/src/`` package whose source
files appear in the changed set is represented by at least one test path
under ``packages/<P>/tests/`` in the final test selection. CI's per-package
manifest sanity check requires this; the reverse-slice cannot supply it on
its own when (e.g.) only ``__init__.py`` changed and no test imports
``__init__`` directly — the case that blocked release v4.1.0.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "scripts" / "per_package_fallback.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("per_package_fallback", HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_pkg(repo: Path, pkg: str, *, tests: list[str], src_files: list[str] | None = None) -> None:
    src_dir = repo / "packages" / pkg / "src" / pkg
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("__version__ = '0.0.0'\n")
    for src in src_files or []:
        (src_dir / src).write_text("# placeholder\n")
    if tests:
        tests_dir = repo / "packages" / pkg / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        for t in tests:
            (tests_dir / t).write_text("def test_x(): pass\n")


def test_no_fallback_when_all_packages_represented(tmp_path: Path) -> None:
    _make_pkg(tmp_path, "alpha", tests=["test_alpha.py"])
    _make_pkg(tmp_path, "beta", tests=["test_beta.py"])
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=[
            "packages/alpha/src/alpha/foo.py",
            "packages/beta/src/beta/bar.py",
        ],
        affected_tests=[
            "packages/alpha/tests/test_alpha.py",
            "packages/beta/tests/test_beta.py",
        ],
    )
    assert result == []


def test_fallback_added_for_unrepresented_package(tmp_path: Path) -> None:
    _make_pkg(tmp_path, "alpha", tests=["test_alpha.py"])
    _make_pkg(tmp_path, "beta", tests=["test_beta.py"])
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=[
            "packages/alpha/src/alpha/foo.py",
            "packages/beta/src/beta/__init__.py",
        ],
        affected_tests=["packages/alpha/tests/test_alpha.py"],
    )
    assert result == ["packages/beta/tests/test_beta.py"]


def test_alphabetical_first_test_chosen(tmp_path: Path) -> None:
    _make_pkg(tmp_path, "alpha", tests=["test_zebra.py", "test_alpha.py", "test_beta.py"])
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=["packages/alpha/src/alpha/__init__.py"],
        affected_tests=[],
    )
    assert result == ["packages/alpha/tests/test_alpha.py"]


def test_branches_test_files_eligible(tmp_path: Path) -> None:
    """``BRANCHES_test_*.py`` files match the same regex as ``test_*.py`` in CI."""
    _make_pkg(tmp_path, "alpha", tests=["BRANCHES_test_alpha.py", "test_zebra.py"])
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=["packages/alpha/src/alpha/__init__.py"],
        affected_tests=[],
    )
    assert result == ["packages/alpha/tests/BRANCHES_test_alpha.py"]


def test_no_tests_dir_silently_skipped(tmp_path: Path) -> None:
    _make_pkg(tmp_path, "alpha", tests=[])  # no tests/
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=["packages/alpha/src/alpha/__init__.py"],
        affected_tests=[],
    )
    assert result == []


def test_empty_tests_dir_silently_skipped(tmp_path: Path) -> None:
    _make_pkg(tmp_path, "alpha", tests=[])
    (tmp_path / "packages" / "alpha" / "tests").mkdir(parents=True, exist_ok=True)
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=["packages/alpha/src/alpha/__init__.py"],
        affected_tests=[],
    )
    assert result == []


def test_non_package_paths_ignored(tmp_path: Path) -> None:
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=[
            "scripts/foo.py",
            ".agent/hooks/_shared/bar.py",
            "docs/baz.md",
        ],
        affected_tests=[],
    )
    assert result == []


def test_fallback_not_duplicated_against_affected(tmp_path: Path) -> None:
    """If the alphabetical-first test happens to already be in affected_tests, skip it."""
    _make_pkg(tmp_path, "alpha", tests=["test_alpha.py", "test_beta.py"])
    helper = _load_helper()
    # The package isn't covered by alpha/tests/test_other yet smart-test selected
    # an unrelated test in another package — the alphabetical-first rule would
    # pick test_alpha.py, but if test_alpha.py is somehow already present we
    # should not duplicate.
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=["packages/alpha/src/alpha/__init__.py"],
        affected_tests=["packages/alpha/tests/test_alpha.py"],
    )
    assert result == []


def test_multiple_packages_fan_out(tmp_path: Path) -> None:
    _make_pkg(tmp_path, "alpha", tests=["test_alpha.py"])
    _make_pkg(tmp_path, "beta", tests=["test_beta.py"])
    _make_pkg(tmp_path, "gamma", tests=["test_gamma.py"])
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=[
            "packages/alpha/src/alpha/__init__.py",
            "packages/beta/src/beta/__init__.py",
            "packages/gamma/src/gamma/__init__.py",
        ],
        affected_tests=[],
    )
    assert sorted(result) == [
        "packages/alpha/tests/test_alpha.py",
        "packages/beta/tests/test_beta.py",
        "packages/gamma/tests/test_gamma.py",
    ]


def test_blank_lines_in_input_tolerated(tmp_path: Path) -> None:
    _make_pkg(tmp_path, "alpha", tests=["test_alpha.py"])
    helper = _load_helper()
    result = helper.compute_fallbacks(
        repo_root=tmp_path,
        changed_source_files=["packages/alpha/src/alpha/__init__.py", "", " "],
        affected_tests=["", "  "],
    )
    assert result == ["packages/alpha/tests/test_alpha.py"]


def test_cli_invocation_via_subprocess(tmp_path: Path) -> None:
    """End-to-end smoke test: CLI reads stdin + --tests-file, prints to stdout."""
    _make_pkg(tmp_path, "alpha", tests=["test_alpha.py"])
    tests_file = tmp_path / "affected.txt"
    tests_file.write_text("")  # empty: no tests selected yet
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER_PATH),
            str(tmp_path),
            "--tests-file",
            str(tests_file),
        ],
        input="packages/alpha/src/alpha/__init__.py\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "packages/alpha/tests/test_alpha.py"


def test_cli_handles_missing_tests_file_as_empty(tmp_path: Path) -> None:
    """A non-existent --tests-file path means 'no affected tests yet'."""
    _make_pkg(tmp_path, "alpha", tests=["test_alpha.py"])
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER_PATH),
            str(tmp_path),
            "--tests-file",
            str(tmp_path / "does-not-exist.txt"),
        ],
        input="packages/alpha/src/alpha/__init__.py\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "packages/alpha/tests/test_alpha.py"


def test_cli_no_args_prints_usage(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "usage" in (result.stdout + result.stderr).lower()
