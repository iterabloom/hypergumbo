# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for WI-jozan: top-level-source → matching-test mapper.

smart-test's reverse-slice via ``hypergumbo slice --files`` only reaches
source inside the ``packages/*/src/**`` tree. Any PR that only touches
``.agent/hooks/_shared/*.py`` or ``scripts/*`` produced an empty slice
and the per-PR CI gate was silent. This module maps those top-level
source files to their matching ``tests/test_<basename>.py`` files so
smart-test can run them alongside the slice-found tests.

The predicate is file-existence-based: a match is reported only when
the target test file actually exists on disk. The tests build an
isolated tmp tree so they do not depend on the repo's current test
layout and so new top-level tests do not silently change test
behaviour.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).parent.parent
MODULE_PATH = REPO_ROOT / ".agent" / "hooks" / "_shared" / "top_level_test_map.py"


def _import_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("top_level_test_map", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_tree(tmp_path: Path, test_filenames: list[str]) -> Path:
    """Create a fake repo tree under tmp_path with the given test files."""
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    for name in test_filenames:
        (tmp_path / "tests" / name).write_text("# fixture\n")
    return tmp_path


# --- _candidate_test_basename ---


def test_agent_hooks_shared_py_maps_to_basename() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/_shared/awaits_bakeoff_nudge.py") == "awaits_bakeoff_nudge"


def test_agent_hooks_shared_subdir_is_skipped() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/_shared/subdir/foo.py") is None


def test_scripts_hyphen_collapses_to_underscore() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/agent-supervisor") == "agent_supervisor"
    assert mod._candidate_test_basename("scripts/auto-pr") == "auto_pr"


def test_scripts_strips_py_and_sh_extension() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/foo.py") == "foo"
    assert mod._candidate_test_basename("scripts/foo.sh") == "foo"


def test_scripts_subdir_is_skipped() -> None:
    """scripts/lib/forgejo-api.sh should not auto-map — sub-scripts have
    generic basenames that would over-match unrelated tests."""
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/lib/forgejo-api.sh") is None


def test_scripts_empty_name_is_skipped() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("scripts/") is None


def test_unrelated_path_returns_none() -> None:
    mod = _import_module()
    assert mod._candidate_test_basename("packages/hypergumbo-core/src/foo.py") is None
    assert mod._candidate_test_basename("docs/README.md") is None
    assert mod._candidate_test_basename("") is None


def test_other_agent_dir_is_skipped() -> None:
    """Only .agent/hooks/_shared/ is mapped — .agent/tracker/, .agent/hooks/claude-code/,
    etc. have their own test conventions or no tests at all."""
    mod = _import_module()
    assert mod._candidate_test_basename(".agent/hooks/claude-code/stop.sh") is None
    assert mod._candidate_test_basename(".agent/tracker/config.yaml") is None


# --- map_to_tests ---


def test_map_returns_only_existing_tests(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_awaits_bakeoff_nudge.py"])
    out = mod.map_to_tests(
        [
            ".agent/hooks/_shared/awaits_bakeoff_nudge.py",
            ".agent/hooks/_shared/nonexistent.py",
        ],
        tmp_path,
    )
    assert out == ["tests/test_awaits_bakeoff_nudge.py"]


def test_map_deduplicates(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_watched_process.py"])
    # Same source file appearing twice in the diff (should not happen but
    # guard anyway).
    out = mod.map_to_tests(
        [
            ".agent/hooks/_shared/watched_process.py",
            ".agent/hooks/_shared/watched_process.py",
        ],
        tmp_path,
    )
    assert out == ["tests/test_watched_process.py"]


def test_map_sorts_output(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_a.py", "test_b.py", "test_c.py"])
    out = mod.map_to_tests(
        [
            ".agent/hooks/_shared/c.py",
            ".agent/hooks/_shared/a.py",
            ".agent/hooks/_shared/b.py",
        ],
        tmp_path,
    )
    assert out == ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]


def test_map_skips_blank_lines(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_foo.py"])
    out = mod.map_to_tests(
        [
            "",
            "   ",
            ".agent/hooks/_shared/foo.py",
            "\n",
        ],
        tmp_path,
    )
    assert out == ["tests/test_foo.py"]


def test_map_scripts_hyphen_to_underscore(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, ["test_agent_supervisor.py"])
    out = mod.map_to_tests(["scripts/agent-supervisor"], tmp_path)
    assert out == ["tests/test_agent_supervisor.py"]


def test_map_empty_input(tmp_path: Path) -> None:
    mod = _import_module()
    _make_tree(tmp_path, [])
    assert mod.map_to_tests([], tmp_path) == []


def test_map_skips_unmapped_non_empty_paths(tmp_path: Path) -> None:
    """Non-empty paths whose _candidate_test_basename returns None hit the
    continue branch and contribute nothing to the result — covered here so
    that branch stays exercised."""
    mod = _import_module()
    _make_tree(tmp_path, ["test_foo.py"])
    out = mod.map_to_tests(
        [
            "docs/README.md",
            "packages/hypergumbo-core/src/foo.py",
            ".agent/hooks/_shared/foo.py",  # this one DOES map
        ],
        tmp_path,
    )
    assert out == ["tests/test_foo.py"]


def test_map_no_tests_directory(tmp_path: Path) -> None:
    """When tests/ doesn't exist, nothing matches (no crash)."""
    mod = _import_module()
    out = mod.map_to_tests([".agent/hooks/_shared/foo.py"], tmp_path)
    assert out == []


# --- CLI ---


def test_cli_reads_stdin_and_prints_matches(tmp_path: Path) -> None:
    _make_tree(tmp_path, ["test_watched_process.py", "test_awaits_bakeoff_nudge.py"])
    stdin = (
        ".agent/hooks/_shared/watched_process.py\n"
        ".agent/hooks/_shared/awaits_bakeoff_nudge.py\n"
        "packages/hypergumbo-core/src/foo.py\n"
    )
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(tmp_path)],
        input=stdin,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "tests/test_awaits_bakeoff_nudge.py",
        "tests/test_watched_process.py",
    ]


def test_cli_empty_stdin_exits_zero(tmp_path: Path) -> None:
    _make_tree(tmp_path, [])
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(tmp_path)],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_cli_missing_repo_root_arg_exits_two() -> None:
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr


# --- main() direct invocation for coverage of the return statement ---


def test_main_returns_zero_on_success(tmp_path: Path, monkeypatch) -> None:
    import io
    mod = _import_module()
    _make_tree(tmp_path, ["test_watched_process.py"])
    monkeypatch.setattr("sys.stdin", io.StringIO(".agent/hooks/_shared/watched_process.py\n"))
    rc = mod.main(["top_level_test_map.py", str(tmp_path)])
    assert rc == 0


def test_main_returns_two_on_missing_arg() -> None:
    mod = _import_module()
    rc = mod.main(["top_level_test_map.py"])
    assert rc == 2
