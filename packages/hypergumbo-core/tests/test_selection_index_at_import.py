# SPDX-License-Identifier: AGPL-3.0-or-later
"""A function that runs at import is import-time code, whatever kind of block.

Narrowing refuses when a module or class body changed: such code runs when
the file is imported, under no test's context, so a test that only loads the
file has no coverage row for it and a missing row proves nothing.

A function called from a module body is in the same position. ``TABLE =
build()`` runs ``build`` at import; a test that reads ``TABLE`` never runs
``build`` and has no row for it. Narrowing judged "import-time" by the kind
of block alone, so a change to ``build`` was narrowed like any function
change: asked about one statement added to ``io_boundary.load_catalog``
(which two analyzer modules call while they load), it dropped those
analyzers' test files. 113 of this repository's 6,515 functions have a
statement that runs during some module's import.

What these tests pin: the fold marks the function blocks it saw run in
coverage's empty context; a change to a marked function is "import-time code
changed" for narrowing, for the import-closure rule and for the by-statement
selector; a partial run only adds marks (it loaded fewer modules); a full
run that no longer sees one removes it; a file that is gone takes its marks
with it.
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
from hypergumbo_core.selection_index import (
    blocks_run_at_import,
    import_time_changed_sources,
    narrowing_blockers,
    select_tests,
    update_from_run,
)

MOD = '''\
def build():
    table = {"a": 1}
    return table


TABLE = build()


def use():
    return TABLE["a"]


def plain():
    return 2
'''

_GENERATE = '''\
import json
import sys
import coverage

db, spec = sys.argv[1], json.loads(sys.argv[2])
sys.path.insert(0, spec["src"])
if not spec["sees_the_import"]:
    import mod
cov = coverage.Coverage(data_file=db, source=[spec["src"]])
cov.start()
import mod
for context, function in spec["calls"]:
    cov.switch_context(context)
    getattr(mod, function)()
cov.stop()
cov.save()
'''

USES = "tests/test_uses.py::test_reads_the_table"
PLAIN = "tests/test_plain.py::test_plain"
CALLS = [(f"{USES}|run", "use"), (f"{PLAIN}|run", "plain")]
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

    def fold(self, *, sees_the_import: bool = True, full: bool = False,
             calls: list[tuple[str, str]] = CALLS) -> None:
        self._runs += 1
        db = self.root / f"run-{self._runs}.coverage"
        spec = {"src": str(self.source.parent), "sees_the_import": sees_the_import,
                "calls": calls}
        subprocess.run([sys.executable, "-c", textwrap.dedent(_GENERATE), str(db), json.dumps(spec)],
                       check=True, capture_output=True, text=True)
        update_from_run(self.conn, db, ran_tests=[USES, PLAIN], repo_root=self.root,
                        scope_complete=True, full_run=full)

    def marked(self) -> set[str]:
        return {name for _path, name in blocks_run_at_import(self.conn, [self.path])}

    def edit(self, old: str, new: str) -> dict[str, str | None]:
        assert old in MOD
        self.source.write_text(MOD.replace(old, new))
        return {self.path: MOD}


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_the_fold_marks_the_function_that_ran_at_import_and_no_other(repo: Repo) -> None:
    repo.fold()
    # build ran under no test's context. use and plain ran in a test's. The
    # module body is import-time by its kind and needs no mark.
    assert repo.marked() == {"build"}
    assert blocks_run_at_import(repo.conn, ["/no/such/file.py"]) == frozenset()


def test_a_change_to_it_is_import_time_code_changed_and_narrowing_refuses(repo: Repo) -> None:
    repo.fold()
    selection = select_tests(repo.conn, repo.edit('table = {"a": 1}', 'table = {"a": 2}'))
    assert selection.import_time_changed == {(repo.path, "build")}
    assert narrowing_blockers(selection, repo.root) == (
        f"import-time code changed: {REL} (build)",)
    # A function that runs only inside tests is narrowed as before.
    selection = select_tests(repo.conn, repo.edit("return 2", "return 3"))
    assert selection.import_time_changed == frozenset()
    assert narrowing_blockers(selection, repo.root) == ()


def test_the_import_closure_rule_takes_the_file_only_with_the_index(repo: Repo) -> None:
    """The modules that call the function while they load import its file, so
    the file's importers cover them. Without an index the kinds of block
    decide alone, as before."""
    repo.fold()
    base = repo.edit('table = {"a": 1}', 'table = {"a": 2}')
    assert import_time_changed_sources(base, repo.root, repo.conn) == (REL,)
    assert import_time_changed_sources(base, repo.root) == ()
    base = repo.edit("return 2", "return 3")
    assert import_time_changed_sources(base, repo.root, repo.conn) == ()


def test_a_marked_function_that_is_deleted_counts_too(repo: Repo) -> None:
    repo.fold()
    repo.source.write_text(MOD.replace(
        'def build():\n    table = {"a": 1}\n    return table\n\n\nTABLE = build()',
        'TABLE = {"a": 1}'))
    selection = select_tests(repo.conn, {repo.path: MOD})
    assert (repo.path, "build") in selection.import_time_changed


def test_the_by_statement_selector_hands_it_to_the_import_rule(repo: Repo) -> None:
    """``test_reads_the_table`` never ran ``build``: by its record it would be
    left out, and it is the test the change can break."""
    repo.fold()
    chosen = ss.select_by_statement(repo.conn, repo.edit('table = {"a": 1}', 'table = {"a": 2}'))
    assert chosen.answers[(repo.path, "build")] == ss.IMPORT_TIME
    assert (repo.path, "build") in chosen.import_time and chosen.narrowed == frozenset()
    # A function that runs only inside tests stays this selector's own.
    chosen = ss.select_by_statement(repo.conn, repo.edit("return 2", "return 3"))
    assert chosen.answers[(repo.path, "plain")] != ss.IMPORT_TIME
    assert chosen.import_time == frozenset() and PLAIN in chosen.tests


def test_a_partial_run_that_does_not_see_the_import_keeps_the_mark(repo: Repo) -> None:
    repo.fold()
    # The module was loaded before this run measured anything: it shows no
    # statement of build, and that says nothing about whether build runs at
    # import.
    repo.fold(sees_the_import=False)
    assert repo.marked() == {"build"}


def test_a_full_run_marks_afresh(repo: Repo) -> None:
    repo.fold()
    repo.fold(sees_the_import=False, full=True)
    assert repo.marked() == set(), "a full run loaded every module and did not see it"
    repo.fold(full=True)
    assert repo.marked() == {"build"}


def test_a_file_that_is_gone_takes_its_marks_with_it(repo: Repo, tmp_path: Path) -> None:
    repo.fold()
    assert repo.conn.execute("SELECT COUNT(*) FROM at_import_block").fetchone()[0] == 1
    # Not on disk, and no source file of the checkout that is asking.
    repo.source.unlink()
    si._forget_dead_checkouts(repo.conn, str(tmp_path / "another_checkout"))
    assert repo.conn.execute("SELECT COUNT(*) FROM at_import_block").fetchone()[0] == 0


def test_an_index_from_before_the_marks_opens_and_marks_nothing(repo: Repo, tmp_path: Path) -> None:
    repo.fold()
    repo.conn.execute("DROP TABLE at_import_block")
    repo.conn.commit()
    repo.conn.close()
    again = si.open_index(tmp_path / "index.sqlite")
    assert again.execute("PRAGMA user_version").fetchone()[0] == si.FORMAT
    assert blocks_run_at_import(again, [repo.path]) == frozenset()
