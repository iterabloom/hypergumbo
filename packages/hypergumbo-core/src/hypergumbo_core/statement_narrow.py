# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which tests of a local run the by-statement selector leaves out.

WHAT IT IS. smart-test selects test FILES (the import graph, the coverage
narrowing, the read and import-closure unions). The by-statement selector
(:mod:`statement_select`) names TESTS: those that ran a changed statement,
those coverage cannot speak for, and those that read a changed file as data.
This module turns its answer into the node ids a local run deselects, inside
the files the run would run anyway. It never adds a file and never removes
one: a file all of whose tests are left out is still collected.

WHY IT MAY. The owner's bar (fast_smart_test_plan, decision 5 and the
switch-on row): 30 runs that failed a test, none failed outside the
selection. Planted-fault campaign 5 (2026-11-18, notebook
``fast_smart_test_build_11172026/bar/campaign5_summary.txt``): 42 runs failed
a test, 35 of them narrowed, no failed test outside the selection that fails
on its own with the fault. That bounds the per-run miss rate below about 7%
(95%); it does not show it is zero. smart-test asks twice: over the local run
set, and over the manifest's files alone, whose answer the manifest carries
for PR CI (``#- <id>`` lines). The scheduled full run runs everything.

IT REFUSES, AND THE RUN IS NOT NARROWED, WHEN:

* a changed path is one it has no rule for. Judged: package source
  (``packages/*/src/**.py``). Let through: a test module directly in a
  ``tests`` directory (the caller runs it whole), Markdown outside every
  ``src`` and ``tests`` directory, ``.ci/`` and the tracker's data. Anything
  else (a ``conftest.py``, a test helper module, a fixture file,
  ``pyproject.toml``, a script, package data such as a YAML catalogue) can
  change what a test does without changing a statement any test ran;
* no package source changed (there is nothing for statements to say);
* the selector met code it cannot answer by statement: a block the index has
  never seen, import-time code, a file that does not parse.

WHAT IT LEAVES OUT. A test of the run set's files, as the index spells it, that
the selector did not select, coverage can speak for, and whose file is
INFORMED about the change (:func:`selection_index.informed_test_files`: its
latest run here measured every changed source as part of a complete run).
A test the index does not hold (added since its last run) is not named, so it
runs. Files in ``whole`` (changed tests, the prose rule's, the fixture rule's,
the scripts map's) run every test.

THE COVERAGE GATE STAYS MEASURED. smart-test gates the changed files at 100%
over the tests it ran. Tests that ran no changed statement still covered the
rest of each changed file, so leaving them all out would fail the gate on
every narrowed run. For each block of each changed file, left-out tests are
put back, the one covering most of the still uncovered statements first,
until the running tests cover every statement the left-out ones were
recorded running. A block reached only by left-out tests without a record
gets one of them back. Statement numbers are those of the block as the index
stores it; for a changed block that is the version last run, so the cover
is computed on that version and can be short by the statements the change
moved (the gate then says so, and ``--no-by-statement`` runs them all).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Optional

from .selection_index import (
    informed_test_files,
    rebase_to_repo,
    statement_records,
    without_xdist_group,
)
from .statement_select import UNPARSABLE, Selection, select_by_statement

#: Changed paths outside package source that change no test's behaviour the
#: selector could miss: prefixes, and suffixes allowed outside package source.
_INERT_PREFIXES = (".ci/", ".agent/tracker/", ".agent/tracker-workspace/")
_INERT_SUFFIXES = (".md",)


@dataclass(frozen=True)
class StatementNarrowing:
    """What the local run leaves out, and why not, when it leaves out nothing."""

    #: Repo-relative node ids the run deselects.
    left_out: frozenset[str]
    #: Set: nothing is left out, and this says why.
    refusal: Optional[str]
    #: Tests the index holds in the files the selector could narrow.
    candidates: int = 0
    #: Tests put back so that every statement stays covered.
    kept_for_coverage: frozenset[str] = field(default_factory=frozenset)
    selection: Optional[Selection] = None


def is_judged_source(rel: str) -> bool:
    """``packages/<pkg>/src/**.py``: what the selector answers by statement."""
    parts = PurePosixPath(rel).parts
    return (len(parts) > 3 and parts[0] == "packages" and parts[2] == "src"
            and rel.endswith(".py"))


def _is_test_file(rel: str) -> bool:
    """A test module directly in a ``tests`` directory. One deeper (under
    ``tests/fixtures/``) is a test's data, whatever it is called."""
    path = PurePosixPath(rel)
    return (path.parent.name == "tests" and path.suffix == ".py"
            and path.name.startswith(("test_", "BRANCHES_test_")))


def unjudged(rel: str) -> bool:
    """A changed path the by-statement selector has no rule for."""
    if is_judged_source(rel) or _is_test_file(rel) or rel.startswith(_INERT_PREFIXES):
        return False
    # Package data, a conftest, a test helper, a fixture file.
    if {"src", "tests"} & set(PurePosixPath(rel).parts):
        return True
    return not rel.endswith(_INERT_SUFFIXES)


def _refusal(changed: list[str], selection: Optional[Selection]) -> Optional[str]:
    unknown_paths = sorted(rel for rel in changed if unjudged(rel))
    if unknown_paths:
        return f"a change it has no rule for: {', '.join(unknown_paths[:3])}"
    if selection is None:
        return "no package source changed"
    if selection.unknown:
        return f"{len(selection.unknown)} changed block(s) the index has never seen"
    if selection.import_time:
        return f"{len(selection.import_time)} changed block(s) of import-time code"
    if UNPARSABLE in selection.answers.values():
        return "a changed file does not parse"
    return None


def _index_tests_of(conn: sqlite3.Connection, repo_root: Path,
                    files: Iterable[str]) -> dict[str, str]:
    """Repo-relative node id -> the id the index holds, for the tests of
    ``files`` (repo-relative). Range queries on the unique id index; the
    index may spell a test relative or absolute."""
    found: dict[str, str] = {}
    for rel in sorted(set(files)):
        for spelled in (rel, f"{repo_root}/{rel}"):
            low = f"{spelled}::"
            for (test_id,) in conn.execute(
                    "SELECT test_id FROM test WHERE test_id >= ? AND test_id < ?",
                    (low, f"{spelled}:;")):
                found[next(iter(rebase_to_repo([str(test_id)], repo_root)))] = str(test_id)
    return found


def _cover(conn: sqlite3.Connection, repo_root: Path, sources: Iterable[str],
           run_files: frozenset[str], left_out: set[str]) -> set[str]:
    """Left-out tests put back so that, block by block, the running tests
    cover every statement the left-out ones were recorded running."""
    back: set[str] = set()
    for path in sorted(set(sources)):
        blocks = [str(name) for (name,) in conn.execute(
            "SELECT b.name FROM file f JOIN block b ON b.file = f.id WHERE f.path = ?",
            (path,))]
        for block in blocks:
            records = {next(iter(rebase_to_repo([test], repo_root))): ran
                       for test, ran in statement_records(conn, path, block).items()}
            dropped = {t: ran for t, ran in records.items() if t in left_out}
            if not dropped:
                continue
            running = [ran for t, ran in records.items()
                       if t not in left_out and t.split("::")[0] in run_files]
            need = set().union(*(ran for ran in dropped.values() if ran))
            have: set[int] = set()
            for ran in running:
                if not need - have:
                    break
                have |= ran or set()
            missing = need - have
            while missing:
                best = min(dropped, key=lambda t: (-len(missing & (dropped[t] or set())), t))
                back.add(best)
                missing -= dropped.pop(best) or set()
            if not need and not running:
                back.add(min(dropped))
            left_out -= back
    return back


def narrow_by_statement(
    conn: sqlite3.Connection, repo_root: Path,
    base_sources: Mapping[str, Optional[str]], changed: Iterable[str],
    run_files: Iterable[str], whole: Iterable[str] = (),
) -> StatementNarrowing:
    """The tests a local run of ``run_files`` (repo-relative test files) may
    leave out for a change.

    ``changed`` is every changed path, repo-relative; ``base_sources`` maps
    each changed package source file, ABSOLUTE as the index spells files, to
    its text at the base (``None``: the change adds it). Tests of ``whole``
    files are never left out.
    """
    changed = sorted(set(changed))
    selection = (select_by_statement(conn, base_sources, repo_root)
                 if base_sources else None)
    refusal = _refusal(changed, selection)
    if refusal is not None or selection is None:
        return StatementNarrowing(left_out=frozenset(), refusal=refusal, selection=selection)
    files = frozenset(run_files)
    narrowable = (files - frozenset(whole)) & rebase_to_repo(
        informed_test_files(conn, repo_root, base_sources), repo_root)
    held = {without_xdist_group(t) for t in rebase_to_repo(
        selection.tests | selection.cannot_speak, repo_root)}
    candidates = _index_tests_of(conn, repo_root, narrowable)
    left_out = {t for t in candidates if t not in held}
    back = _cover(conn, repo_root, base_sources, files, left_out)
    if left_out and not frozenset(_index_tests_of(conn, repo_root, files)) - left_out:
        # pytest exits 5 when it runs nothing, and CI reads that as a failed
        # selection: leave the files to run whole.
        return StatementNarrowing(left_out=frozenset(), refusal="it would leave no test to run",
                                  candidates=len(candidates), selection=selection)
    return StatementNarrowing(
        left_out=frozenset(left_out), refusal=None, candidates=len(candidates),
        kept_for_coverage=frozenset(back), selection=selection)


def deselected(node_ids: Iterable[str], left_out: Iterable[str]) -> list[int]:
    """Positions in ``node_ids`` (as pytest collected them, perhaps with an
    xdist group suffix) of the tests in ``left_out``."""
    out = frozenset(left_out)
    return [i for i, node in enumerate(node_ids) if without_xdist_group(node) in out]
