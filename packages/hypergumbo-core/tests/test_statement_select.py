# SPDX-License-Identifier: AGPL-3.0-or-later
"""The by-statement selector: fewer tests than "reached the function", and
never fewer than the records in hand can justify.

Each test builds a small index (which tests ran which statements of which
function, and the digest of each function as it was), changes the file on
disk, and asks. What is pinned is the answer per kind of change AND why: a
block answered with every test because there was no record of it as it was
must say so, and must not read as "by statement".
"""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path
from typing import Iterable, Optional

import pytest

from hypergumbo_core import selection_index as si
from hypergumbo_core import statement_select as ss
from hypergumbo_core.block_hash import blocks_for_source

BASE = textwrap.dedent('''
    LIMIT = 3

    def f(x):
        """Doc."""           # 0
        if x < 0:            # 1
            return -1        # 2
        a = 1                # 3
        b = a + 1            # 4
        return b             # 5

    def g():
        return 1             # 0

    class C:
        def m(self):
            return 1         # 0

    def doomed():
        return 0             # 0
''')

#: test -> {block: the statements it ran, or None for a pair with no record}
RECORDS: dict[str, dict[str, Optional[set[int]]]] = {
    "t_early": {"f": {1, 2}},
    "t_full": {"f": {1, 3, 4, 5}},
    "t_no_record": {"f": None},
    "t_g": {"g": {0}},
    "t_c": {"C.m": {0}},
    "t_doomed": {"doomed": {0}},
    "t_unmeasured": {"f": {1, 3, 4, 5}},
}


def _bitmap(ordinals: Iterable[int]) -> bytes:
    return si._int_bitmap(sum(1 << ordinal for ordinal in ordinals))


class Repo:
    def __init__(self, tmp_path: Path, stored: str = BASE,
                 records: Optional[dict[str, dict[str, Optional[set[int]]]]] = None) -> None:
        records = RECORDS if records is None else records
        self.path = tmp_path / "m.py"
        self.path.write_text(BASE)
        self.conn = si.open_index(tmp_path / "index.sqlite")
        key = str(self.path)
        edges = {test: [(key, block) for block in blocks] for test, blocks in records.items()}
        statements = {test: {(key, block): _bitmap(ran) for block, ran in blocks.items()
                             if ran is not None} for test, blocks in records.items()}
        digests = {key: {b.name: b.digest for b in blocks_for_source(key, stored)}}
        si.store_edges(self.conn, edges, digests=digests, statements=statements)
        self.conn.execute("INSERT INTO unmeasured_test (test_id) VALUES ('t_unmeasured')")
        self.conn.commit()

    def select(self, new: Optional[str], base: Optional[str] = BASE) -> ss.Selection:
        if new is None:
            self.path.unlink()
        else:
            self.path.write_text(new)
        return ss.select_by_statement(self.conn, {str(self.path): base})

    def answer(self, selection: ss.Selection, block: str) -> str:
        return selection.answers[(str(self.path), block)]


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_only_the_tests_that_ran_the_changed_statement_or_have_no_record(repo: Repo) -> None:
    chosen = repo.select(BASE.replace("b = a + 1 ", "b = a + 2 "))
    assert repo.answer(chosen, "f") == ss.BY_STATEMENT
    # t_early returned before the edited line. t_no_record reached f and
    # nothing says what it ran. The unmeasured test is not this selector's.
    assert chosen.tests == {"t_full", "t_no_record"}
    assert chosen.by_block == {"t_early", "t_full", "t_no_record"}
    assert chosen.import_time == frozenset() == chosen.unknown


def test_a_block_the_index_saw_in_another_version_is_answered_with_every_test(
        tmp_path: Path) -> None:
    """The index's records are of ``f`` as some run last saw it, which is not
    ``f`` at the base: they number other statements. Nothing can be narrowed,
    and the answer must say that this is why."""
    seen_by_the_index = BASE.replace("a = 1 ", "a = 100 ")
    repo = Repo(tmp_path, stored=seen_by_the_index)
    chosen = repo.select(BASE.replace("b = a + 1 ", "b = a + 2 "))
    assert repo.answer(chosen, "f") == ss.NO_BASE_RECORD
    assert chosen.tests == {"t_early", "t_full", "t_no_record"} == chosen.by_block


def _fold_a_run(repo: Repo, source: str, ran: dict[str, Optional[set[int]]],
                measured: bool = False) -> None:
    """A later run, folded in: ``f`` is ``source``'s now, and each test in
    ``ran`` ran those statements of it (``None``: it ran and did not reach f)."""
    key = str(repo.path)
    digests = {key: {b.name: b.digest for b in blocks_for_source(key, source)}}
    edges = {test: ([(key, "f")] if ordinals is not None else []) for test, ordinals in ran.items()}
    statements = {test: {(key, "f"): _bitmap(ordinals)} for test, ordinals in ran.items()
                  if ordinals is not None}
    si.store_edges(repo.conn, edges, digests=digests, statements=statements,
                   measured=[key] if measured else None)


def test_a_change_that_has_been_run_is_still_compared_with_its_base(repo: Repo) -> None:
    """The second run of a change. The first stored the edited ``f``'s digest
    and dropped the records of ``f`` as it was; they were kept, and it is
    those the selector reads: before they were, this was answered with every
    test that reached ``f``."""
    first_edit = BASE.replace("b = a + 1 ", "b = a + 2 ")
    _fold_a_run(repo, first_edit, {"t_full": {1, 3, 4, 5}})
    chosen = repo.select(first_edit.replace("b = a + 2 ", "b = a + 3 "))
    assert repo.answer(chosen, "f") == ss.BY_STATEMENT
    assert chosen.tests == {"t_full", "t_no_record"}
    assert chosen.by_block == {"t_early", "t_full", "t_no_record"}


def test_a_test_that_stopped_reaching_the_block_during_the_change_still_counts(
        repo: Repo) -> None:
    """``t_full`` reached ``f`` at the base and, on the edited code, no longer
    does: its pair is gone. Measured from the base it ran the edited
    statement, and it is selected on its kept record."""
    first_edit = BASE.replace("b = a + 1 ", "b = a + 2 ")
    _fold_a_run(repo, first_edit, {"t_full": None}, measured=True)
    assert "t_full" not in si.statement_records(repo.conn, str(repo.path), "f")
    chosen = repo.select(first_edit)
    assert repo.answer(chosen, "f") == ss.BY_STATEMENT
    assert chosen.tests == {"t_full", "t_no_record"}
    assert chosen.by_block == {"t_early", "t_full", "t_no_record"}
    # The same for an answer that does not narrow.
    whole = repo.select(first_edit.replace("def f(x):", "def f(x, y=0):"))
    assert repo.answer(whole, "f").startswith("whole: ")
    assert whole.tests == {"t_early", "t_full", "t_no_record"} == whole.by_block


def test_a_signature_change_is_answered_with_every_test_and_says_why(repo: Repo) -> None:
    chosen = repo.select(BASE.replace("def f(x):", "def f(x, y=0):"))
    assert repo.answer(chosen, "f") == "whole: its signature or a decorator changed"
    assert chosen.tests == {"t_early", "t_full", "t_no_record"}


def test_a_docstring_edit_selects_nothing(repo: Repo) -> None:
    chosen = repo.select(BASE.replace('"""Doc."""', '"""Other words."""'))
    assert repo.answer(chosen, "f") == ss.PROSE
    assert chosen.tests == frozenset() == chosen.by_block


def test_gone_new_and_new_method(repo: Repo) -> None:
    method = "    def m(self):\n        return 1         # 0\n"
    doomed = "def doomed():\n    return 0             # 0\n"
    assert method in BASE and doomed in BASE
    new = BASE.replace(doomed, "").replace(
        method, method + "\n    def fresh(self):\n        return 2\n")
    new += "\ndef brand_new():\n    return g()\n"
    chosen = repo.select(new)
    assert repo.answer(chosen, "doomed") == ss.GONE
    assert repo.answer(chosen, "brand_new") == ss.NEW
    assert repo.answer(chosen, "C.fresh") == ss.NEW_METHOD
    # doomed's test; and the test that reached C, because an unchanged call
    # may now reach C.fresh. Nothing for the new function itself.
    assert chosen.tests == {"t_doomed", "t_c"}
    assert chosen.by_block == {"t_doomed", "t_c"}


def test_a_block_the_index_has_never_seen_is_unknown_not_untested(tmp_path: Path) -> None:
    """The index was built before ``g``, ``C.m`` and ``doomed`` existed: it has
    rows for ``f`` only. "No test is paired with g" is then the index's
    ignorance. Each such block is listed, and no test is claimed for it."""
    only_f = BASE.split("def g():")[0]
    repo = Repo(tmp_path, stored=only_f,
                records={test: blocks for test, blocks in RECORDS.items() if "f" in blocks})
    method = "    def m(self):\n        return 1         # 0\n"
    new = (BASE.replace("return 1             # 0", "return 2             # 0")   # g: edited
           .replace("def doomed():\n    return 0             # 0\n", "")            # doomed: gone
           .replace(method, method + "\n    def fresh(self):\n        return 2\n"))
    chosen = repo.select(new)
    assert {block for _path, block in chosen.unknown} == {"g", "doomed", "C.fresh"}
    assert {repo.answer(chosen, block) for block in ("g", "doomed", "C.fresh")} == {ss.UNKNOWN}
    assert chosen.tests == frozenset(), "nothing is claimed about a block nobody has seen"


def test_a_new_method_of_a_new_class_is_not_unknown(repo: Repo) -> None:
    chosen = repo.select(BASE + "\nclass Fresh:\n    def method(self):\n        return 1\n")
    assert repo.answer(chosen, "Fresh.method") == ss.NEW_METHOD
    assert chosen.unknown == frozenset() and chosen.tests == frozenset()


def test_import_time_code_is_handed_to_the_import_rule(repo: Repo) -> None:
    chosen = repo.select(BASE.replace("LIMIT = 3", "LIMIT = 4"))
    assert repo.answer(chosen, "<module>") == ss.IMPORT_TIME
    assert chosen.import_time == {(str(repo.path), "<module>")}
    assert chosen.tests == frozenset(), "no record says which tests ran a module body"


def test_a_deleted_file_is_every_test_of_every_function_in_it(repo: Repo) -> None:
    chosen = repo.select(None)
    assert {repo.answer(chosen, block) for block in ("f", "g", "C.m", "doomed")} == {ss.GONE}
    assert chosen.tests == {"t_early", "t_full", "t_no_record", "t_g", "t_c", "t_doomed"}
    assert (str(repo.path), "<module>") in chosen.import_time


def test_an_added_file_has_no_test_of_its_own(repo: Repo, tmp_path: Path) -> None:
    added = tmp_path / "added.py"
    added.write_text(BASE)
    chosen = ss.select_by_statement(repo.conn, {str(added): None})
    assert chosen.tests == frozenset() == chosen.by_block
    assert chosen.answers[(str(added), "f")] == ss.NEW
    # A method of a class nobody has reached yet: every test that reached the
    # class is no test.
    assert chosen.answers[(str(added), "C.m")] == ss.NEW_METHOD
    assert chosen.answers[(str(added), "<module>")] == ss.IMPORT_TIME
    assert chosen.unknown == frozenset(), "a file nobody could have run is not a gap in the index"


def test_a_file_that_does_not_parse_is_every_test_that_touched_it(repo: Repo) -> None:
    chosen = repo.select("def f(:\n")
    assert chosen.answers == {(str(repo.path), ""): ss.UNPARSABLE}
    assert chosen.tests == {"t_early", "t_full", "t_no_record", "t_g", "t_c", "t_doomed"}


def test_several_files_and_an_unchanged_one(repo: Repo, tmp_path: Path) -> None:
    other = tmp_path / "other.py"
    other.write_text("def h():\n    return 1\n")
    repo.path.write_text(BASE.replace("return 1             # 0", "return 2             # 0"))
    chosen = ss.select_by_statement(
        repo.conn, {str(repo.path): BASE, str(other): "def h():\n    return 1\n"})
    # g's only statement is where g is entered: every test that reached g.
    assert chosen.answers == {(str(repo.path), "g"): (
        "whole: a statement was added or edited where the block is entered")}
    assert chosen.tests == {"t_g"}


def test_a_closed_index_is_an_error_not_an_empty_selection(repo: Repo) -> None:
    repo.conn.close()
    with pytest.raises(sqlite3.ProgrammingError):
        repo.select(BASE.replace("b = a + 1 ", "b = a + 2 "))
