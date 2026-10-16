# SPDX-License-Identifier: AGPL-3.0-or-later
"""find_files returns the same files with and without a FileIndex (INV-kalaj).

Every ``run_survey`` installs a FileIndex, so an analyzer's patterns are
answered by ``FileIndex.match_pattern`` in production and by ``rglob`` only
in unit tests that never install one. The two used to disagree on any
pattern with a directory segment: the index matched patterns against the
bare file name, which never contains ``/``, so ``requirements/*.txt`` and
Play's ``conf/*.routes`` matched nothing in a survey while matching in
tests. The guard at the bottom collects every literal pattern the source
tree hands to ``find_files`` or declares as ``file_patterns`` and checks
the two paths agree on a tree holding a file for each.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from hypergumbo_core.discovery import FileIndex, find_files, set_file_index

REPO_ROOT = Path(__file__).resolve().parents[3]


def _touch(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n")


def _walk(root: Path, pattern: str) -> set[str]:
    set_file_index(None)
    return {p.relative_to(root).as_posix() for p in find_files(root, [pattern])}


def _indexed(root: Path, pattern: str, index_root: Path | None = None) -> set[str]:
    set_file_index(FileIndex.build(index_root or root))
    try:
        return {p.relative_to(root).as_posix() for p in find_files(root, [pattern])}
    finally:
        set_file_index(None)


_TREE = [
    "requirements.txt",
    "requirements/base.txt",
    "requirements/dev.txt",
    "reqs/test.txt",
    "svc/requirements/prod.txt",
    "conf/routes",
    "conf/admin.routes",
    "api/conf/routes",
    "api/conf/api.routes",
    "src/pkg/mod.py",
    "src/pkg/sub/deep.py",
    "src/top.py",
    "notes.txt",
]


@pytest.mark.parametrize(
    "pattern",
    [
        "requirements/*.txt",
        "reqs/*.txt",
        "conf/*.routes",
        "*/conf/routes",
        "conf/routes",
        "**/conf/routes",
        "pkg/*.py",
        "src/**/*.py",
        "src/**/deep.py",
        "**/requirements/*.txt",
    ],
)
def test_directory_segment_patterns_match_like_rglob(
    tmp_path: Path, pattern: str
) -> None:
    for rel in _TREE:
        _touch(tmp_path, rel)

    walked = _walk(tmp_path, pattern)

    assert walked, f"fixture gives {pattern!r} nothing to match"
    assert _indexed(tmp_path, pattern) == walked


def test_sub_root_patterns_are_relative_to_the_requested_root(
    tmp_path: Path,
) -> None:
    for rel in _TREE:
        _touch(tmp_path, rel)
    sub = tmp_path / "src"

    for pattern in ("src/*.py", "pkg/*.py", "*.py"):
        set_file_index(None)
        walked = {p.relative_to(tmp_path).as_posix() for p in find_files(sub, [pattern])}
        set_file_index(FileIndex.build(tmp_path))
        try:
            indexed = {
                p.relative_to(tmp_path).as_posix() for p in find_files(sub, [pattern])
            }
        finally:
            set_file_index(None)
        assert indexed == walked, pattern


@pytest.mark.parametrize("patterns", [["*.R", "*.r"], ["*.py", "test_*.py"]])
def test_a_file_two_patterns_match_is_yielded_once(
    tmp_path: Path, patterns: list[str]
) -> None:
    for rel in ("a.R", "b.r", "lib/test_c.py", "lib/d.py"):
        _touch(tmp_path, rel)

    set_file_index(None)
    walked = [p.relative_to(tmp_path).as_posix() for p in find_files(tmp_path, patterns)]
    set_file_index(FileIndex.build(tmp_path))
    try:
        indexed = [
            p.relative_to(tmp_path).as_posix() for p in find_files(tmp_path, patterns)
        ]
    finally:
        set_file_index(None)

    assert len(walked) == len(set(walked)) > 0
    assert sorted(indexed) == sorted(walked)


def test_pure_extension_patterns_ignore_case_on_both_paths(tmp_path: Path) -> None:
    for rel in ("Foo.H", "bar.h", "Baz.blade.php"):
        _touch(tmp_path, rel)

    assert _walk(tmp_path, "*.h") == _indexed(tmp_path, "*.h") == {"Foo.H", "bar.h"}
    assert _walk(tmp_path, "**/*.H") == {"Foo.H", "bar.h"}
    # A compound suffix is not a pure extension: plain, case-sensitive glob.
    assert _walk(tmp_path, "*.BLADE.php") == _indexed(tmp_path, "*.BLADE.php") == set()


# --- guard: every literal pattern in the tree --------------------------------

_FIND_CALLS = {"find_files", "find_non_test_files"}


def _string_list(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
    ):
        return [e.value for e in node.elts]  # type: ignore[attr-defined]
    return None


def _patterns_in_source() -> set[str]:
    found: set[str] = set()
    for path in REPO_ROOT.glob("packages/*/src/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                # ``file_patterns`` and any local pattern list a loop feeds to
                # find_files one at a time (requirements.py's ``patterns``).
                if any(
                    isinstance(t, ast.Name) and "pattern" in t.id.lower()
                    for t in targets
                ) and node.value is not None:
                    found.update(_string_list(node.value) or [])
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in _FIND_CALLS and len(node.args) >= 2:
                    found.update(_string_list(node.args[1]) or [])
    return found


_NOT_A_GLOB = re.compile(r"^/|\s|[\\^$()|+{}]")


def _materialize(pattern: str) -> str | None:
    """A relative path the pattern matches, or None for a class we skip.

    Skipped: character classes, and strings a "pattern"-named list holds
    that are not file globs at all (regexes, absolute paths, prose).
    """
    if "[" in pattern or _NOT_A_GLOB.search(pattern):
        return None
    parts = [p for p in pattern.split("/") if p and p != "**"]
    return "/".join(
        re.sub(r"\*+", "x", seg).replace("?", "q") for seg in parts
    ) or None


def test_source_patterns_include_directory_segment_ones() -> None:
    patterns = _patterns_in_source()

    assert "requirements/*.txt" in patterns
    assert "conf/*.routes" in patterns
    assert len(patterns) > 100


def test_every_source_pattern_matches_the_same_files_with_and_without_an_index(
    tmp_path: Path,
) -> None:
    patterns = sorted(p for p in _patterns_in_source() if _materialize(p))
    for pattern in patterns:
        rel = _materialize(pattern)
        assert rel is not None
        _touch(tmp_path, rel)
        _touch(tmp_path, f"deep/er/{rel}")

    set_file_index(FileIndex.build(tmp_path))
    try:
        indexed = {
            p: {f.relative_to(tmp_path).as_posix() for f in find_files(tmp_path, [p])}
            for p in patterns
        }
    finally:
        set_file_index(None)
    walked = {
        p: {f.relative_to(tmp_path).as_posix() for f in find_files(tmp_path, [p])}
        for p in patterns
    }

    mismatched = {p: (indexed[p], walked[p]) for p in patterns if indexed[p] != walked[p]}
    assert not mismatched, mismatched
