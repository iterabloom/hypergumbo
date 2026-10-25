# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-fomid: ``max_files`` bounds every analyzer, enforced where files are found.

The cap was documented as "maximum files per language analyzer" and reached
only the 10 of 119 analyzers registered ``supports_max_files=True``; at a cap
of 1 this repository still analyzed 252 of 1,385 files. 108 of the analyzers
take ``repo_root`` alone, so the cap cannot travel as an argument. It travels
as a per-analyzer budget that ``discovery.find_files`` consults instead.
"""
from __future__ import annotations

import threading
from pathlib import Path

from hypergumbo_core.analyze.all_analyzers import run_all_analyzers
from hypergumbo_core.discovery import (
    FileIndex,
    analyzer_file_budget,
    find_files,
    set_file_index,
)


def _tree(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")


class TestTheBudget:
    def test_no_budget_leaves_discovery_unbounded(self, tmp_path: Path) -> None:
        _tree(tmp_path, ["a.py", "b.py", "c.py"])
        with analyzer_file_budget(None) as budget:
            assert budget is None
            assert len(list(find_files(tmp_path, ["*.py"]))) == 3

    def test_the_budget_admits_at_most_its_limit(self, tmp_path: Path) -> None:
        _tree(tmp_path, ["a.py", "b.py", "c.py"])
        with analyzer_file_budget(2) as budget:
            assert len(list(find_files(tmp_path, ["*.py"]))) == 2
        assert budget is not None and len(budget.refused) == 1

    def test_a_second_pass_sees_the_same_files(self, tmp_path: Path) -> None:
        """A two-pass analyzer (symbols, then edges) calls the finder twice.
        Counting yields would give the second pass nothing; the budget
        re-admits what it admitted."""
        _tree(tmp_path, ["a.py", "b.py", "c.py"])
        with analyzer_file_budget(2):
            first = list(find_files(tmp_path, ["*.py"]))
            second = list(find_files(tmp_path, ["*.py"]))
        assert first == second and len(first) == 2

    def test_the_limit_is_per_analyzer_across_patterns(self, tmp_path: Path) -> None:
        _tree(tmp_path, ["a.py", "b.pyi", "c.toml"])
        with analyzer_file_budget(2):
            found = list(find_files(tmp_path, ["*.py", "*.pyi"]))
            found += list(find_files(tmp_path, ["*.toml"]))
        assert len(found) == 2

    def test_the_index_path_is_bounded_too(self, tmp_path: Path) -> None:
        _tree(tmp_path, ["a.py", "b.py", "c.py"])
        set_file_index(FileIndex.build(tmp_path))
        try:
            with analyzer_file_budget(1):
                assert len(list(find_files(tmp_path, ["*.py"]))) == 1
        finally:
            set_file_index(None)

    def test_the_budget_ends_with_its_block(self, tmp_path: Path) -> None:
        _tree(tmp_path, ["a.py", "b.py"])
        with analyzer_file_budget(1):
            pass
        assert len(list(find_files(tmp_path, ["*.py"]))) == 2

    def test_another_thread_is_not_bounded(self, tmp_path: Path) -> None:
        """Analyzers run in parallel worker threads, each under its own
        budget; a linker or another analyzer's thread must not share it."""
        _tree(tmp_path, ["a.py", "b.py"])
        seen: list[int] = []
        with analyzer_file_budget(1):
            worker = threading.Thread(
                target=lambda: seen.append(len(list(find_files(tmp_path, ["*.py"])))),
            )
            worker.start()
            worker.join()
        assert seen == [2]


_SOURCES = {
    "py": "x = 1\n", "go": "package main\n\nfunc F() {}\n", "md": "# Title\n",
    "sh": "echo hi\n", "rs": "fn f() {}\n",
}


def _analyzed(root: Path, cap: int | None) -> dict[str, int]:
    runs, *_ = run_all_analyzers(root, max_files=cap)
    return {r["pass"]: r["files_analyzed"] for r in runs if r.get("files_analyzed")}


class TestEveryAnalyzerHonoursTheCap:
    def test_a_cap_of_one_bounds_each_language(self, tmp_path: Path) -> None:
        """The audit's repro: three files each of .py/.go/.md/.sh/.rs, and at
        max_files=1 python analyzed 1 while bash, go, markdown and rust
        analyzed 3."""
        for i in range(3):
            for ext, text in _SOURCES.items():
                (tmp_path / f"f{i}.{ext}").write_text(text, encoding="utf-8")
        languages = {"python", "go", "markdown", "bash", "rust"}
        assert self._capped_and_uncapped(tmp_path, languages)

    def test_finders_that_bypass_find_files_are_bounded_too(self, tmp_path: Path) -> None:
        """Measured on this repository at --max-files 1: bash analyzed 33
        (its shebang scan of extensionless files reads the file index
        directly) and gitignore 2 (the root ``.gitignore`` was appended
        unconditionally). The generic yaml finder walks the index too."""
        for i in range(3):
            sub = tmp_path / f"d{i}"
            sub.mkdir()
            (sub / f"tool{i}").write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
            (sub / ".gitignore").write_text("*.o\n", encoding="utf-8")
            (sub / f"conf{i}.yml").write_text("a: 1\n", encoding="utf-8")
        (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
        uncapped = _analyzed(tmp_path, None)
        assert uncapped.get("bash") == 3 and uncapped.get("gitignore") == 4, uncapped
        assert uncapped.get("yaml") == 3, uncapped
        capped = _analyzed(tmp_path, 1)
        assert {p: capped.get(p) for p in ("bash", "gitignore", "yaml")} == {
            "bash": 1, "gitignore": 1, "yaml": 1,
        }, capped

    @staticmethod
    def _capped_and_uncapped(tmp_path: Path, languages: set[str]) -> bool:
        uncapped = _analyzed(tmp_path, None)
        assert {p: uncapped.get(p) for p in languages} == dict.fromkeys(languages, 3)
        capped = _analyzed(tmp_path, 1)
        assert {p: capped.get(p) for p in languages} == dict.fromkeys(languages, 1)
        assert {p: n for p, n in capped.items() if n > 1} == {}
        return True
