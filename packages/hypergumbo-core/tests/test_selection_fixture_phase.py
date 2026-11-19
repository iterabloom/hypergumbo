# SPDX-License-Identifier: AGPL-3.0-or-later
"""A function a fixture runs in setup or teardown widens to its file's tests.

Campaign 5 of 2026-11-18 (WI-kihah): with the body of
``linkers/registry.py::clear_registry`` dropped, the toy linkers one test file
registers stayed registered for the rest of the worker, and 278 tests of
other files failed. ``clear_registry`` is test-only cleanup, called by that
file's autouse fixture around each test; the 278 never run it, so the
by-statement selector, which selects the tests that ran a changed statement,
left every one of them out. A cleanup fault its own tests do not notice
would leave every failure outside the selection.

Owner ruling of 2026-11-19: a changed block that runs during a test's
TEARDOWN is not narrowed test by test; the selector selects every test the
index records touching its file. Teardown only: setup also runs the analysis
fixtures build (562 blocks of ordinary code on the index of that day). What
these tests pin: the fold marks the blocks it saw run under a test's
``|setup`` or ``|teardown`` context, and which phase; a partial run only adds
marks; a full run that no longer sees one removes it; a file that is gone
takes its marks with it; the selector widens a teardown block's change to the
file's tests and says so; a setup-only block and an unmarked one are still
answered by statement.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hypergumbo_core import selection_index as si
from hypergumbo_core import statement_select as ss

MOD = '''\
_REGISTRY = []


def register(item):
    _REGISTRY.append(item)


def clear():
    held = len(_REGISTRY)
    _REGISTRY.clear()
    return held


def count():
    total = len(_REGISTRY)
    return total
'''

_GENERATE = '''\
import json
import sys
import coverage

db, spec = sys.argv[1], json.loads(sys.argv[2])
sys.path.insert(0, spec["src"])
cov = coverage.Coverage(data_file=db, source=[spec["src"]])
cov.start()
import mod
for context, function, args in spec["calls"]:
    cov.switch_context(context)
    getattr(mod, function)(*args)
cov.stop()
cov.save()
'''

OWN = "tests/test_registry.py::test_registers"
VICTIM = "tests/test_counting.py::test_counts"
WITH_FIXTURE = [(f"{OWN}|setup", "clear", []), (f"{OWN}|run", "register", ["toy"]),
                (f"{OWN}|teardown", "clear", []), (f"{VICTIM}|run", "count", [])]
NO_FIXTURE = [(f"{OWN}|run", "register", ["toy"]), (f"{VICTIM}|run", "count", [])]
SETUP_ONLY = [(f"{OWN}|setup", "clear", []), (f"{VICTIM}|run", "count", [])]
REL = "packages/p/src/p/mod.py"


class Repo:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.source = self.root / REL
        self.source.parent.mkdir(parents=True)
        self.source.write_text(MOD)
        self.path = str(self.source)
        self.conn = si.open_index(tmp_path / "index.sqlite")
        self._runs = 0

    def fold(self, calls: list, *, full: bool = False) -> None:
        self._runs += 1
        db = self.root / f"run-{self._runs}.coverage"
        spec = {"src": str(self.source.parent), "calls": calls}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                       check=True, capture_output=True, text=True)
        si.update_from_run(self.conn, db, ran_tests=[OWN, VICTIM], repo_root=self.root,
                           scope_complete=True, full_run=full)

    def marked(self) -> dict[str, int]:
        return {name: phases for (_path, name), phases
                in si.blocks_run_in_fixtures(self.conn, [self.path]).items()}

    def edit(self, old: str, new: str) -> dict[str, str | None]:
        assert old in MOD
        self.source.write_text(MOD.replace(old, new))
        return {self.path: MOD}


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_the_fold_marks_what_ran_in_setup_or_teardown_and_which(repo: Repo) -> None:
    repo.fold(WITH_FIXTURE)
    assert repo.marked() == {"clear": si.FIXTURE_SETUP | si.FIXTURE_TEARDOWN}
    other = Repo(repo.root.parent / "other")
    other.fold(SETUP_ONLY)
    assert other.marked() == {"clear": si.FIXTURE_SETUP}
    assert si.blocks_run_in_fixtures(repo.conn, []) == {}


def test_a_partial_run_only_adds_and_a_full_run_re_marks(repo: Repo) -> None:
    repo.fold(WITH_FIXTURE)
    repo.fold(NO_FIXTURE)
    assert repo.marked() == {"clear": si.FIXTURE_SETUP | si.FIXTURE_TEARDOWN}
    # A partial run that saw only the setup keeps the teardown mark.
    repo.fold(SETUP_ONLY)
    assert repo.marked() == {"clear": si.FIXTURE_SETUP | si.FIXTURE_TEARDOWN}
    repo.fold(SETUP_ONLY, full=True)
    assert repo.marked() == {"clear": si.FIXTURE_SETUP}
    repo.fold(NO_FIXTURE, full=True)
    assert repo.marked() == {}


def test_a_gone_file_takes_its_marks(repo: Repo) -> None:
    repo.fold(WITH_FIXTURE)
    repo.source.unlink()
    si._forget_dead_checkouts(repo.conn, str(repo.root.parent / "another_checkout"))
    assert repo.marked() == {}
    assert repo.conn.execute("SELECT COUNT(*) FROM fixture_phase_block").fetchone() == (0,)


def test_a_change_to_a_fixture_block_selects_every_test_of_its_file(repo: Repo) -> None:
    repo.fold(WITH_FIXTURE)
    base = repo.edit("    _REGISTRY.clear()\n", "    pass\n")
    selection = ss.select_by_statement(repo.conn, base, repo.root)
    # The victim never ran clear(); it touched the file, so it is selected.
    assert VICTIM in selection.tests and OWN in selection.tests
    assert selection.answers[(repo.path, "clear")] == ss.FIXTURE
    assert VICTIM in selection.by_block
    assert not selection.narrowed


def test_a_block_run_only_in_setup_is_answered_by_statement(repo: Repo) -> None:
    repo.fold(SETUP_ONLY + [(f"{OWN}|run", "clear", [])])
    assert repo.marked() == {"clear": si.FIXTURE_SETUP}
    base = repo.edit("    _REGISTRY.clear()\n", "    pass\n")
    selection = ss.select_by_statement(repo.conn, base, repo.root)
    assert selection.answers[(repo.path, "clear")] == ss.BY_STATEMENT
    assert VICTIM not in selection.tests


def test_an_unmarked_block_is_still_answered_by_statement(repo: Repo) -> None:
    repo.fold(WITH_FIXTURE)
    base = repo.edit("    return total\n", "    return total + 0\n")
    selection = ss.select_by_statement(repo.conn, base, repo.root)
    assert selection.answers[(repo.path, "count")] == ss.BY_STATEMENT
    assert selection.tests == {VICTIM}


def test_without_the_mark_the_victim_is_left_out(repo: Repo) -> None:
    # The control: the same change, folded from a run that never ran clear()
    # in a fixture phase, is narrowed by statement and leaves the victim out.
    repo.fold([(f"{OWN}|run", "clear", []), (f"{VICTIM}|run", "count", [])])
    base = repo.edit("    _REGISTRY.clear()\n", "    pass\n")
    selection = ss.select_by_statement(repo.conn, base, repo.root)
    assert selection.answers[(repo.path, "clear")] == ss.BY_STATEMENT
    assert VICTIM not in selection.tests and OWN in selection.tests
