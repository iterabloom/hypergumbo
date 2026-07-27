# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/coverage_only_deps.py`` (WI-zaziz).

``scripts/smart-test`` selects tests via ``hypergumbo slice --files`` — an
IMPORT-dependency reverse slice. Some tests COVER a source file through the
analysis pipeline without ever IMPORTING it (e.g.
``test_polyglot_call_site_coverage.py`` covers ``java.py``'s static-import
``ExternalRef`` branch by running the analyzer, with no import edge to
``java.py``). The slice misses those, so a PR touching that source file gets a
false ``<100%`` on CI's changed-file coverage gate even though the full suite
is 100%. This helper reads a checked-in declarative map
(``.ci/coverage-only-deps.txt``) of ``source -> covering-test`` pairs and
returns the covering tests for any changed source file, so smart-test always
includes them.
"""
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "scripts" / "coverage_only_deps.py"
MAP_PATH = REPO_ROOT / ".ci" / "coverage-only-deps.txt"


def _load_helper():
    spec = importlib.util.spec_from_file_location("coverage_only_deps", HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _touch(repo: Path, rel: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# placeholder\n")


# --------------------------------------------------------------------------
# parse_map
# --------------------------------------------------------------------------


def test_parse_map_basic() -> None:
    helper = _load_helper()
    dep_map = helper.parse_map("src/a.py tests/test_a.py\n")
    assert dep_map == {"src/a.py": ["tests/test_a.py"]}


def test_parse_map_skips_comments_and_blanks() -> None:
    helper = _load_helper()
    text = "# a comment\n\n   \nsrc/a.py tests/test_a.py\n# trailing\n"
    assert helper.parse_map(text) == {"src/a.py": ["tests/test_a.py"]}


def test_parse_map_multiple_tests_per_source() -> None:
    helper = _load_helper()
    dep_map = helper.parse_map("src/a.py tests/test_a.py tests/test_b.py\n")
    assert dep_map == {"src/a.py": ["tests/test_a.py", "tests/test_b.py"]}


def test_parse_map_accumulates_repeated_source() -> None:
    helper = _load_helper()
    text = "src/a.py tests/test_a.py\nsrc/a.py tests/test_b.py\n"
    assert helper.parse_map(text) == {"src/a.py": ["tests/test_a.py", "tests/test_b.py"]}


def test_parse_map_single_token_line_skipped() -> None:
    helper = _load_helper()
    # A source with no test is malformed / meaningless — dropped, not crashed.
    assert helper.parse_map("src/a.py\nsrc/b.py tests/test_b.py\n") == {
        "src/b.py": ["tests/test_b.py"]
    }


# --------------------------------------------------------------------------
# compute_adds
# --------------------------------------------------------------------------


def test_add_returned_for_changed_mapped_source(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_poly.py")
    helper = _load_helper()
    result = helper.compute_adds(
        repo_root=tmp_path,
        changed_source_files=["packages/x/src/x/java.py"],
        affected_tests=["packages/x/tests/test_java.py"],
        dep_map={"packages/x/src/x/java.py": ["tests/test_poly.py"]},
    )
    assert result == ["tests/test_poly.py"]


def test_no_add_when_source_not_changed(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_poly.py")
    helper = _load_helper()
    result = helper.compute_adds(
        repo_root=tmp_path,
        changed_source_files=["packages/x/src/x/other.py"],
        affected_tests=[],
        dep_map={"packages/x/src/x/java.py": ["tests/test_poly.py"]},
    )
    assert result == []


def test_no_add_when_test_already_selected(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_poly.py")
    helper = _load_helper()
    result = helper.compute_adds(
        repo_root=tmp_path,
        changed_source_files=["packages/x/src/x/java.py"],
        affected_tests=["tests/test_poly.py"],
        dep_map={"packages/x/src/x/java.py": ["tests/test_poly.py"]},
    )
    assert result == []


def test_missing_test_file_defensively_skipped(tmp_path: Path) -> None:
    # A stale map entry pointing at a nonexistent test must not inject a path
    # that would make pytest error out.
    helper = _load_helper()
    result = helper.compute_adds(
        repo_root=tmp_path,
        changed_source_files=["packages/x/src/x/java.py"],
        affected_tests=[],
        dep_map={"packages/x/src/x/java.py": ["tests/does_not_exist.py"]},
    )
    assert result == []


def test_multiple_sources_and_tests_fan_out_sorted(tmp_path: Path) -> None:
    for t in ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py"):
        _touch(tmp_path, t)
    helper = _load_helper()
    result = helper.compute_adds(
        repo_root=tmp_path,
        changed_source_files=["s/one.py", "s/two.py"],
        affected_tests=[],
        dep_map={
            "s/one.py": ["tests/test_c.py", "tests/test_a.py"],
            "s/two.py": ["tests/test_b.py"],
        },
    )
    assert result == ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]


def test_blank_lines_in_changed_and_affected_tolerated(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_poly.py")
    helper = _load_helper()
    result = helper.compute_adds(
        repo_root=tmp_path,
        changed_source_files=["packages/x/src/x/java.py", "", "  "],
        affected_tests=["", "  "],
        dep_map={"packages/x/src/x/java.py": ["tests/test_poly.py"]},
    )
    assert result == ["tests/test_poly.py"]


# --------------------------------------------------------------------------
# The committed map is well-formed and its entries point at real files
# --------------------------------------------------------------------------


def test_committed_map_parses_and_targets_exist() -> None:
    """Every source and test in the checked-in map must exist on disk, so a
    typo can't silently disable the augmentation or inject a phantom test."""
    helper = _load_helper()
    dep_map = helper.parse_map(MAP_PATH.read_text())
    assert dep_map, "the committed map should have at least one entry"
    for source, tests in dep_map.items():
        assert (REPO_ROOT / source).is_file(), f"map source missing: {source}"
        for t in tests:
            assert (REPO_ROOT / t).is_file(), f"map test missing: {t}"


def test_committed_map_covers_the_java_polyglot_case() -> None:
    """The filed WI-zaziz case: java.py -> test_polyglot_call_site_coverage.py."""
    helper = _load_helper()
    dep_map = helper.parse_map(MAP_PATH.read_text())
    java = "packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/java.py"
    assert java in dep_map
    assert (
        "packages/hypergumbo-lang-mainstream/tests/test_polyglot_call_site_coverage.py"
        in dep_map[java]
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_invocation_via_subprocess(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_poly.py")
    map_file = tmp_path / "map.txt"
    map_file.write_text("packages/x/src/x/java.py tests/test_poly.py\n")
    tests_file = tmp_path / "affected.txt"
    tests_file.write_text("packages/x/tests/test_java.py\n")
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER_PATH),
            str(tmp_path),
            "--map",
            str(map_file),
            "--tests-file",
            str(tests_file),
        ],
        input="packages/x/src/x/java.py\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "tests/test_poly.py"


def test_cli_missing_map_is_empty(tmp_path: Path) -> None:
    """A non-existent --map path yields no additions (never crashes)."""
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER_PATH),
            str(tmp_path),
            "--map",
            str(tmp_path / "nope.txt"),
        ],
        input="packages/x/src/x/java.py\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""


def test_cli_no_args_prints_usage() -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "usage" in (result.stdout + result.stderr).lower()


# --------------------------------------------------------------------------
# main() — in-process so the CLI wiring is genuinely covered (subprocess tests
# above are end-to-end smoke tests that do not contribute to coverage)
# --------------------------------------------------------------------------


def test_main_in_process(tmp_path: Path, capsys, monkeypatch) -> None:
    _touch(tmp_path, "tests/test_poly.py")
    map_file = tmp_path / "map.txt"
    map_file.write_text("packages/x/src/x/java.py tests/test_poly.py\n")
    tests_file = tmp_path / "affected.txt"
    tests_file.write_text("packages/x/tests/test_java.py\n")
    helper = _load_helper()
    monkeypatch.setattr(sys, "stdin", io.StringIO("packages/x/src/x/java.py\n"))
    rc = helper.main(
        [str(tmp_path), "--map", str(map_file), "--tests-file", str(tests_file)]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == "tests/test_poly.py"


def test_main_missing_map_in_process(tmp_path: Path, capsys, monkeypatch) -> None:
    helper = _load_helper()
    monkeypatch.setattr(sys, "stdin", io.StringIO("packages/x/src/x/java.py\n"))
    rc = helper.main([str(tmp_path), "--map", str(tmp_path / "nope.txt")])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_main_without_tests_file_in_process(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _touch(tmp_path, "tests/test_poly.py")
    map_file = tmp_path / "map.txt"
    map_file.write_text("packages/x/src/x/java.py tests/test_poly.py\n")
    helper = _load_helper()
    monkeypatch.setattr(sys, "stdin", io.StringIO("packages/x/src/x/java.py\n"))
    rc = helper.main([str(tmp_path), "--map", str(map_file)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "tests/test_poly.py"


def test_read_lines_both_branches(tmp_path: Path) -> None:
    helper = _load_helper()
    existing = tmp_path / "f.txt"
    existing.write_text("one\ntwo\n")
    assert helper._read_lines(existing) == ["one", "two"]
    assert helper._read_lines(tmp_path / "missing.txt") == []
