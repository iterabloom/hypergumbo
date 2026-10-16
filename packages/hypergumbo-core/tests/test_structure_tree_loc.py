# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sketch's Structure tree carries a LOC column (ADR-0005).

Every line under the root opens with a four-column count: up to three digits
and a unit (`` ``, ``k``, ``M``). A file shows its own non-blank lines, a
directory the sum over the files the tree would count as its items, and an
``[and N other items]`` line the part of its directory the shown lines leave
out. The root line has no count: the Overview already states the project's
LOC (tiers 1-2, ADR-0004 3a), and the tree counts every file on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hypergumbo_core.discovery import FileIndex, set_file_index
from hypergumbo_core.sketch import (
    _format_loc,
    _format_structure_tree,
    _format_structure_tree_fallback,
    _TreeLoc,
)


def _lines(n: int) -> str:
    return "".join(f"x{i}\n" for i in range(n))


def _tree_lines(result: str) -> list[str]:
    body = result.split("```")[1].strip("\n").split("\n")
    return body


def _line_for(result: str, needle: str) -> str:
    return next(line for line in _tree_lines(result) if needle in line)


@pytest.mark.parametrize(
    ("n", "shown"),
    [
        (0, "  0 "),
        (9, "  9 "),
        (999, "999 "),
        (1000, "  1k"),
        (8400, "  8k"),
        (8500, "  9k"),
        (999_499, "999k"),
        (999_500, "  1M"),
        (12_345_678, " 12M"),
        (2_500_000_000, "  3G"),
    ],
)
def test_format_loc_is_four_columns(n: int, shown: str) -> None:
    assert _format_loc(n) == shown


def test_tree_lines_carry_file_dir_and_remainder_loc(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(_lines(5) + "\n\n   \n")  # blank lines not counted
    (src / "util.py").write_text(_lines(1200))
    (src / "deep").mkdir()
    (src / "deep" / "a.py").write_text(_lines(30))
    (tmp_path / "setup.py").write_text(_lines(11))
    (tmp_path / "README.md").write_text(_lines(40))

    result = _format_structure_tree(tmp_path, ["src/main.py", "setup.py"])
    lines = _tree_lines(result)

    assert lines[0] == f"     {tmp_path.name}/"
    assert _line_for(result, "── src") == "  1k ├── src"
    assert _line_for(result, "main.py") == "  5  │   ├── main.py"
    # util.py (1200) + deep/a.py (30) are src's two other items.
    assert _line_for(result, "[and 2 other items]") == "  1k │   └── [and 2 other items]"
    assert _line_for(result, "setup.py") == " 11  ├── setup.py"
    assert _line_for(result, "[and 1 other items]") == " 40  └── [and 1 other items]"


def test_every_line_under_the_root_has_a_four_column_count(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.py").write_text(_lines(3))
    (tmp_path / "a" / "other.py").write_text(_lines(2))

    result = _format_structure_tree(tmp_path, ["a/b/c.py"])

    for line in _tree_lines(result)[1:]:
        count, rest = line[:4], line[4:]
        assert count.strip(), line
        assert rest.startswith(" "), line
        assert rest[1] in "├└│ ", line


def test_binary_symlink_and_excluded_files_count_zero(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(_lines(4))
    (src / "blob.bin").write_bytes(b"\x00\x01\n" * 50)
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "main.cpython-312.pyc").write_text(_lines(500))
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "big.py").write_text(_lines(700))
    os.symlink(tmp_path / "elsewhere", src / "link")

    result = _format_structure_tree(tmp_path, ["src/main.py"])

    assert _line_for(result, "── src") == "  4  ├── src"


def test_tree_loc_memoises_and_reads_each_file_once(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.py").write_text(_lines(7))
    loc = _TreeLoc(tmp_path, [], exclude_tests=False)

    assert loc(tmp_path) == 7
    (tmp_path / "d" / "f.py").write_text(_lines(70))
    assert loc(tmp_path / "d") == 7
    assert loc(tmp_path / "missing.py") == 0


def test_exclude_tests_drops_test_lines(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(_lines(3))
    (tmp_path / "src" / "test_main.py").write_text(_lines(90))

    assert _TreeLoc(tmp_path, [], exclude_tests=True)(tmp_path / "src") == 3
    assert _TreeLoc(tmp_path, [], exclude_tests=False)(tmp_path / "src") == 93


def test_fallback_tree_carries_loc(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text(_lines(2500))
    (tmp_path / "Makefile").write_text(_lines(6))

    result = _format_structure_tree_fallback(tmp_path, [], exclude_tests=False)

    assert _tree_lines(result)[0] == f"     {tmp_path.name}/"
    assert _line_for(result, "docs/") == "  3k ├── docs/ (1 items) — Documentation"
    assert _line_for(result, "Makefile") == "  6  └── Makefile"


def test_fallback_remainder_line_carries_loc(tmp_path: Path) -> None:
    for i in range(12):
        (tmp_path / f"d{i:02}").mkdir()
        (tmp_path / f"d{i:02}" / "f.py").write_text(_lines(i + 1))

    result = _format_structure_tree_fallback(tmp_path, [], exclude_tests=False)

    # d10 (11 lines) and d11 (12 lines) are the two hidden items.
    assert _line_for(result, "[and 2 other items]") == " 23  └── [and 2 other items]"


def test_map_scoped_index_bounds_the_loc(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(_lines(8))
    (tmp_path / "src" / "unmapped.py").write_text(_lines(400))
    set_file_index(FileIndex.from_paths(tmp_path, [tmp_path / "src" / "main.py"]))
    try:
        result = _format_structure_tree(tmp_path, ["src/main.py"])
    finally:
        set_file_index(None)

    assert _line_for(result, "── src") == "  8  └── src"


def test_index_children_map(tmp_path: Path) -> None:
    idx = FileIndex.from_paths(tmp_path, [tmp_path / "a" / "b.py", tmp_path / "c.py"])

    assert idx.children(tmp_path) == {"a": True, "c.py": False}
    assert idx.children(tmp_path / "a") == {"b.py": False}
    assert idx.children(tmp_path / "empty") == {}
    assert idx.children(tmp_path.parent) is None
