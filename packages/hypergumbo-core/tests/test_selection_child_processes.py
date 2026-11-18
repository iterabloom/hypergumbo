# SPDX-License-Identifier: AGPL-3.0-or-later
"""A test that starts a child able to run Python is never narrowed.

Coverage does not measure a child process. A test that runs some measured
code itself and also starts ``python`` (or a shell, or ``git``, which runs
hooks) is "measured" in the index, yet has no row for what the child ran. A
change to code only the child reaches then finds no row, and a selector that
reads "no row" as "does not depend on it" drops a test that would fail.

The recorder plugin writes every process a test starts; the fold stores it
beside the coverage rows. These tests run real coverage and the real fold of
a recorder record into one index, and pin who may be dropped afterwards:

* a test recorded starting a program that can run Python: never;
* a test that starts only programs that cannot (``gitleaks``): as before;
* a test with no read record (its worker died, it failed, the recorder was
  off, or the record is of another recorder version): never, because what it
  started is not known;
* an id the index still holds for a test that no longer exists: it holds
  nothing back.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core import prose_only as po
from hypergumbo_core.selection_index import (
    coverage_cannot_speak_for,
    droppable_test_files,
    open_index,
    select_tests,
    speakable_test_files,
    store_edges,
    update_from_run,
)
from hypergumbo_core.started_programs import GIT_ELSEWHERE

_GENERATE = '''\
import importlib
import json
import sys
import coverage

db, spec = sys.argv[1], json.loads(sys.argv[2])
cov = coverage.Coverage(data_file=db, source=spec["measure"])
cov.start()
sys.path[:0] = spec["measure"]
for context, calls in spec["calls"]:
    cov.switch_context(context)
    for call in calls:
        module, function = call.split(".")
        getattr(importlib.import_module(module), function)()
cov.stop()
cov.save()
'''

PLAIN = "tests/test_plain.py::test_plain"
PYTHON = "tests/test_python.py::test_starts_python"
LEAKS = "tests/test_leaks.py::test_starts_gitleaks"
GIT = "tests/test_git.py::test_commits_in_a_scratch_repository"
ALL = (PLAIN, PYTHON, LEAKS, GIT)
STARTED = {PYTHON: ["python"], LEAKS: ["gitleaks", "rust-analyzer"], GIT: [GIT_ELSEWHERE]}
COVERAGE_SELECT = Path(__file__).resolve().parents[3] / "scripts" / "coverage-select"


def _file(test_id: str) -> str:
    return test_id.split("::")[0]


class Checkout:
    """``packages/p/src/p/mod.py`` with two functions. Every test's own call
    runs ``used``; nothing in any test's process runs ``only_a_child_runs``."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "repo"
        self.source = self.root / "packages" / "p" / "src" / "p" / "mod.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(
            "def used():\n    return 1\n\n\ndef only_a_child_runs():\n    return 2\n")
        self.index = tmp_path / "index.sqlite"
        self.conn = open_index(self.index)
        self._folds = 0

    def coverage(self, tests: tuple[str, ...] = ALL) -> Path:
        self._folds += 1
        for test in tests:
            path = self.root / _file(test)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"def {test.split('::')[1]}():\n    pass\n")
        db = self.tmp / f"run-{self._folds}.coverage"
        spec = {"measure": [str(self.source.parent)],
                "calls": [(f"{test}|run", ["mod.used"]) for test in tests]}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db),
                        json.dumps(spec)], check=True, capture_output=True, text=True)
        return db

    def fold_coverage(self, tests: tuple[str, ...] = ALL) -> None:
        update_from_run(self.conn, self.coverage(tests), ran_tests=list(tests),
                        repo_root=self.root, scope_complete=True)

    def reads(self, tests: tuple[str, ...] = ALL, *,
              started: dict[str, list[str]] | None = None,
              expected_too: tuple[str, ...] = (),
              version: int = po.RECORDER_VERSION) -> Path:
        """One process's record, as the plugin writes it."""
        started = STARTED if started is None else started
        out = self.tmp / f"reads-{self._folds}-{len(list(self.tmp.glob('reads-*')))}"
        out.mkdir()
        expected: dict[str, list[str]] = {}
        for test in (*tests, *expected_too):
            expected.setdefault(_file(test), []).append(test)
        bucket = {"reads": [], "docs": [], "lines": [], "fixtures": []}
        (out / "main-1.json").write_text(json.dumps({
            "version": version, "observed": True, "expected": expected, "failed": [],
            "tests": {test: {**bucket, "opaque": [f"subprocess.Popen:{program}"
                                                  for program in started.get(test, ())]}
                      for test in tests},
            "fixtures": {}, "modules": {},
            "unattributed": {**bucket, "opaque": []}}))
        return out

    def fold_reads(self, tests: tuple[str, ...] = ALL, **how: object) -> None:
        po.fold_run(self.conn, self.reads(tests, **how), self.root)  # type: ignore[arg-type]

    def droppable(self) -> frozenset[str]:
        """The files narrowing may drop for a change to the function no
        test's own process runs."""
        return droppable_test_files(self.conn, self.root, [str(self.source)])


@pytest.fixture
def repo(tmp_path: Path) -> Checkout:
    return Checkout(tmp_path)


# ── who coverage cannot speak for ───────────────────────────────────────────


def test_a_test_that_starts_python_is_not_dropped_and_one_that_starts_gitleaks_is(
        repo: Checkout) -> None:
    """The close condition, at the level the live narrowing works on. All four
    tests are measured and none has a row on ``only_a_child_runs``."""
    repo.fold_coverage()
    repo.fold_reads()
    assert {_file(PLAIN), _file(LEAKS)} == repo.droppable()
    assert speakable_test_files(repo.conn, repo.root) == {_file(PLAIN), _file(LEAKS)}


def test_the_reason_names_the_programs(repo: Checkout) -> None:
    repo.fold_coverage()
    repo.fold_reads()
    blind = coverage_cannot_speak_for(repo.conn, repo.root)
    assert blind.started == {PYTHON: ("python",), GIT: (GIT_ELSEWHERE,)}
    assert blind.unmeasured == frozenset() == blind.unrecorded
    assert blind.never_narrowed == {PYTHON, GIT} == blind.tests


def test_with_no_child_process_on_record_every_file_may_be_dropped(repo: Checkout) -> None:
    """The reach check: the same index and the same records, nothing started."""
    repo.fold_coverage()
    repo.fold_reads(started={})
    assert repo.droppable() == {_file(test) for test in ALL}
    assert coverage_cannot_speak_for(repo.conn, repo.root).tests == frozenset()


def test_one_program_that_can_run_python_among_exempt_ones_holds_the_test(
        repo: Checkout) -> None:
    repo.fold_coverage()
    repo.fold_reads(started={LEAKS: ["gitleaks", "bash", "sleep"]})
    assert coverage_cannot_speak_for(repo.conn, repo.root).started == {LEAKS: ("bash",)}
    assert _file(LEAKS) not in repo.droppable()


def test_a_later_run_in_which_the_test_starts_nothing_frees_it(repo: Checkout) -> None:
    """The record is replaced per test, like the coverage rows."""
    repo.fold_coverage()
    repo.fold_reads()
    repo.fold_reads((PYTHON,), started={})
    assert _file(PYTHON) in repo.droppable()
    assert _file(GIT) not in repo.droppable()


def test_a_test_only_the_read_record_knows_is_named_too(repo: Checkout) -> None:
    """It has no coverage row of any kind (the index has never seen it), and
    the record shows it starting python. It is named as the record spells it."""
    unseen = "tests/test_plain.py::test_the_index_has_never_seen"
    repo.fold_coverage()
    repo.fold_reads((*ALL, unseen), started={unseen: ["python"]})
    assert coverage_cannot_speak_for(repo.conn, repo.root).started == {unseen: ("python",)}
    assert _file(PLAIN) not in repo.droppable()


# ── no read record ──────────────────────────────────────────────────────────


def test_an_index_with_no_read_record_at_all_drops_nothing(repo: Checkout) -> None:
    """The recorder never ran against this index. What the tests started is
    not known, and an empty record is not a record of nothing started."""
    repo.fold_coverage()
    assert repo.droppable() == frozenset()
    blind = coverage_cannot_speak_for(repo.conn, repo.root)
    assert blind.unrecorded == set(ALL) and blind.started == {}


def test_empty_read_tables_are_no_record_either(repo: Checkout) -> None:
    repo.fold_coverage()
    po.ensure_schema(repo.conn)
    assert coverage_cannot_speak_for(repo.conn, repo.root).unrecorded == set(ALL)


def test_a_record_of_another_recorder_version_is_no_record(repo: Checkout) -> None:
    """Version 2 left nothing for a ``git`` run in a scratch repository: its
    "started nothing" is not this version's."""
    repo.fold_coverage()
    repo.fold_reads(started={})
    assert repo.droppable() != frozenset(), "reach: droppable under this version"
    repo.conn.execute("UPDATE read_meta SET value = ? WHERE key = 'recorder'",
                      (str(po.RECORDER_VERSION - 1),))
    repo.conn.commit()
    assert repo.droppable() == frozenset()
    assert coverage_cannot_speak_for(repo.conn, repo.root).unrecorded == set(ALL)


def test_a_file_with_no_record_is_held_and_the_others_are_not(repo: Checkout) -> None:
    repo.fold_coverage()
    repo.fold_reads((PLAIN, LEAKS))
    blind = coverage_cannot_speak_for(repo.conn, repo.root)
    assert blind.unrecorded == {PYTHON, GIT}
    assert repo.droppable() == {_file(PLAIN), _file(LEAKS)}


def test_a_test_its_files_record_expected_and_did_not_get_is_held(repo: Checkout) -> None:
    """It failed in the recorded run, or its worker died before writing: the
    fold stores no record for it and the file's list of expected tests still
    names it. It need not be in the index at all."""
    failed = "tests/test_plain.py::test_failed_in_the_recorded_run"
    repo.fold_coverage()
    repo.fold_reads(started={}, expected_too=(failed,))
    blind = coverage_cannot_speak_for(repo.conn, repo.root)
    assert blind.unrecorded == {failed}
    assert _file(PLAIN) not in repo.droppable()
    assert _file(LEAKS) in repo.droppable()


def test_an_id_of_a_test_that_no_longer_exists_holds_nothing_back(repo: Checkout) -> None:
    """Nothing prunes the index: on 2026-11-18 it held 635 ids of renamed and
    removed tests. Such an id has no read record and never will. Its file has
    records, and the file's expected list does not name it."""
    removed = "tests/test_plain.py::test_removed_last_month"
    repo.fold_coverage((*ALL, removed))
    repo.fold_reads(started={})
    assert removed in {row[0] for row in repo.conn.execute("SELECT test_id FROM test")}
    assert coverage_cannot_speak_for(repo.conn, repo.root).tests == frozenset()
    assert _file(PLAIN) in repo.droppable()


# ── unmeasured tests, and spellings ─────────────────────────────────────────


def test_an_unmeasured_test_is_named_as_before_and_for_its_child_too(repo: Checkout) -> None:
    repo.fold_coverage()
    repo.fold_reads()
    repo.conn.execute("INSERT INTO unmeasured_test (test_id) VALUES (?), (?)", (PLAIN, PYTHON))
    blind = coverage_cannot_speak_for(repo.conn, repo.root)
    assert blind.unmeasured == {PLAIN, PYTHON}
    assert blind.tests == {PLAIN, PYTHON, GIT}
    assert repo.droppable() == {_file(LEAKS)}


def test_an_index_id_spelled_absolutely_is_matched_to_its_record(
        tmp_path: Path, record_reads) -> None:
    """The read record is relative to the repository. The answer names the
    test as the index spells it, which is what a caller subtracts."""
    root = tmp_path / "repo"
    absolute = f"{root}/tests/test_a.py::test_one"
    conn = open_index(tmp_path / "index.sqlite")
    store_edges(conn, {absolute: [(f"{root}/src/x.py", "f")]})
    record_reads(conn, ["tests/test_a.py::test_one"],
                 spawns={"tests/test_a.py::test_one": ["python"]})
    assert coverage_cannot_speak_for(conn, root).started == {absolute: ("python",)}
    assert speakable_test_files(conn, root) == frozenset()
    # Without the root the two spellings cannot be joined, and the index's
    # test is then one whose file has no record: held all the same.
    assert absolute in coverage_cannot_speak_for(conn).unrecorded


def test_a_row_is_still_a_row(repo: Checkout) -> None:
    """Selection by function goes on naming a test that starts python when
    the test HAS a row on the changed function: a row is positive evidence,
    whoever the test is. Only its silence is not evidence."""
    repo.fold_coverage()
    repo.fold_reads()
    changed = repo.source.read_text()
    repo.source.write_text(changed.replace("return 1", "return 10"))
    assert select_tests(repo.conn, {str(repo.source): changed}).tests == set(ALL)


# ── the fold says how many ──────────────────────────────────────────────────


def _update(repo: Checkout, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(COVERAGE_SELECT), "--repo-root", str(repo.root),
         "--index", str(repo.index), *flags],
        capture_output=True, text=True, timeout=120)


def test_a_full_fold_says_how_many_tests_are_held_and_for_what(
        repo: Checkout) -> None:
    """``coverage-select update --full`` is what smart-test runs after a full
    run. One more test starts python, so the line shows the order: most
    tests first."""
    second = "tests/test_python.py::test_starts_python_and_bash"
    tests = (*ALL, second)
    db = repo.coverage(tests)
    reads = repo.reads(tests, started={**STARTED, second: ["python", "bash"]},
                       expected_too=("tests/test_plain.py::test_failed",))
    # Unmeasured, and of a file with no read record: held twice over, and
    # counted on the unmeasured line only.
    repo.conn.execute("INSERT INTO unmeasured_test (test_id) VALUES (?)",
                      ("tests/test_never_recorded.py::test_through_a_subprocess",))
    repo.conn.commit()
    repo.conn.close()
    done = _update(repo, "update", "--coverage-db", str(db), "--sha", "0" * 40, "--full",
                   "--source-reads", str(reads))
    assert done.returncode == 0, done.stderr
    assert ("never narrowed, besides the unmeasured: 3 test(s) start a program that can "
            f"run Python (python 2, bash 1, {GIT_ELSEWHERE} 1); "
            "1 test(s) have no read record") in done.stderr


def test_a_fold_that_is_not_full_does_not_print_the_line(repo: Checkout) -> None:
    db = repo.coverage()
    reads = repo.reads()
    repo.conn.close()
    done = _update(repo, "update", "--coverage-db", str(db), "--sha", "0" * 40,
                   "--source-reads", str(reads))
    assert done.returncode == 0, done.stderr
    assert "never narrowed" not in done.stderr
    conn = sqlite3.connect(repo.index)
    assert conn.execute("SELECT COUNT(*) FROM read_test").fetchone()[0] == len(ALL)
    conn.close()


def test_the_line_with_nothing_held_names_no_program(repo: Checkout) -> None:
    db = repo.coverage()
    reads = repo.reads(started={})
    repo.conn.close()
    done = _update(repo, "--verbose", "update", "--coverage-db", str(db), "--sha", "0" * 40,
                   "--source-reads", str(reads))
    assert done.returncode == 0, done.stderr
    assert ("never narrowed, besides the unmeasured: 0 test(s) start a program that can "
            "run Python; 0 test(s) have no read record") in done.stderr
