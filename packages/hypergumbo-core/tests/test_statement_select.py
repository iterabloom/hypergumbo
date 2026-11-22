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

from hypergumbo_core import prose_only as po
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
                 records: Optional[dict[str, dict[str, Optional[set[int]]]]] = None,
                 started: Optional[dict[str, list[str]]] = None,
                 no_read_record: Iterable[str] = ()) -> None:
        """``started``: the programs a test is recorded starting. Every test
        gets a read record (one that shows no child process unless
        ``started`` names it), apart from those in ``no_read_record``."""
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
        po.ensure_schema(self.conn)
        for test in sorted(set(records) - set(no_read_record)):
            spawns = ",".join(f"subprocess.Popen:{program}"
                              for program in (started or {}).get(test, ()))
            self.conn.execute(
                "INSERT INTO read_test (test_id, test_file, opaque, observed) "
                "VALUES (?, ?, ?, 1)", (test, test, spawns))
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
    # The tests it could have left out: every test that reached the narrowed
    # function, selected or not (and never an unmeasured one).
    assert chosen.narrowed == {"t_early", "t_full", "t_no_record"}


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
    assert chosen.narrowed == frozenset(), "nothing was narrowed, so nothing was at risk"


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


# ── tests coverage cannot speak for ─────────────────────────────────────────

#: Four tests that reached ``f`` and returned before the line the change
#: edits. By their statements alone the selector leaves all four out.
EARLY: dict[str, dict[str, Optional[set[int]]]] = {
    "t_plain": {"f": {1, 2}},
    "t_gitleaks": {"f": {1, 2}},
    "t_python": {"f": {1, 2}},
    "t_unrecorded": {"f": {1, 2}},
    "t_full": {"f": {1, 3, 4, 5}},
    # Never reached f; starts a child that may have.
    "t_python_elsewhere": {"g": {0}},
}
EDIT = BASE.replace("b = a + 1 ", "b = a + 2 ")


def test_with_clean_read_records_the_early_tests_are_left_out(tmp_path: Path) -> None:
    """The reach check for the three tests below: the same index, every test
    with a record that shows no child process."""
    chosen = Repo(tmp_path, records=EARLY).select(EDIT)
    assert chosen.tests == {"t_full"}
    assert chosen.narrowed == {"t_plain", "t_gitleaks", "t_python", "t_unrecorded", "t_full"}


def test_a_test_that_starts_a_program_able_to_run_python_is_never_narrowed(
        tmp_path: Path) -> None:
    """Coverage did not see what the child ran: "it reached f and ran none
    of the changed statements" is not known of this test."""
    repo = Repo(tmp_path, records=EARLY, started={
        "t_python": ["python"], "t_gitleaks": ["gitleaks"], "t_python_elsewhere": ["bash"]})
    chosen = repo.select(EDIT)
    assert repo.answer(chosen, "f") == ss.BY_STATEMENT
    assert chosen.tests == {"t_full", "t_python"}
    # Not among the tests the selector could have left out: it could not.
    assert chosen.narrowed == {"t_plain", "t_gitleaks", "t_unrecorded", "t_full"}
    assert chosen.by_block == {"t_plain", "t_gitleaks", "t_python", "t_unrecorded", "t_full"}


def test_a_test_that_starts_only_gitleaks_is_narrowed_as_before(tmp_path: Path) -> None:
    repo = Repo(tmp_path, records=EARLY, started={"t_gitleaks": ["gitleaks", "rust-analyzer"]})
    chosen = repo.select(EDIT)
    assert "t_gitleaks" not in chosen.tests
    assert "t_gitleaks" in chosen.narrowed
    assert "t_gitleaks" not in chosen.cannot_speak


def test_a_test_with_no_read_record_is_never_narrowed(tmp_path: Path) -> None:
    """What it started is not known."""
    repo = Repo(tmp_path, records=EARLY, no_read_record=["t_unrecorded"])
    chosen = repo.select(EDIT)
    assert chosen.tests == {"t_full", "t_unrecorded"}
    assert "t_unrecorded" not in chosen.narrowed


def test_the_selection_names_every_test_it_says_nothing_about(tmp_path: Path) -> None:
    """``t_python_elsewhere`` has no row on ``f``. The selector does not
    select it and must not be read as sparing it: its child may run ``f``.
    The unmeasured test is named for the same reason."""
    repo = Repo(tmp_path, records=EARLY, started={
        "t_python": ["python"], "t_python_elsewhere": ["bash"]},
        no_read_record=["t_unrecorded"])
    chosen = repo.select(EDIT)
    assert chosen.cannot_speak == {
        "t_python", "t_python_elsewhere", "t_unrecorded", "t_unmeasured"}
    assert "t_python_elsewhere" not in chosen.tests


def test_an_unmeasured_test_stays_out_of_the_selection_whatever_it_started(
        tmp_path: Path) -> None:
    """Its rows are its fixtures'. The rules for unmeasured tests select it;
    this selector never did, and a recorded child does not change whose it is."""
    records = {**EARLY, "t_unmeasured": {"f": {1, 2}}}
    chosen = Repo(tmp_path, records=records, started={"t_unmeasured": ["python"]}).select(EDIT)
    assert "t_unmeasured" not in chosen.tests | chosen.by_block | chosen.narrowed
    assert "t_unmeasured" in chosen.cannot_speak


def test_an_unmeasured_test_with_a_clean_read_record_is_named_all_the_same(
        tmp_path: Path) -> None:
    """Named for being unmeasured alone: it has a record, and started nothing."""
    records = {**EARLY, "t_unmeasured": {"f": {1, 2}}}
    chosen = Repo(tmp_path, records=records).select(EDIT)
    assert chosen.cannot_speak == {"t_unmeasured"}


def test_every_other_answer_already_selects_such_a_test(tmp_path: Path) -> None:
    """Only the by-statement answer leaves a test that reached the block out,
    so only there is there anything to hold: a signature change selects
    every test that reached ``f``, with or without a child."""
    repo = Repo(tmp_path, records=EARLY, started={"t_python": ["python"]})
    chosen = repo.select(BASE.replace("def f(x):", "def f(x, y=0):"))
    assert chosen.tests == chosen.by_block == {
        "t_plain", "t_gitleaks", "t_python", "t_unrecorded", "t_full"}
    assert chosen.narrowed == frozenset()



# ── tests that read a changed file as data ──────────────────────────────────


def _reads(repo: Repo, test: str, *targets: str) -> None:
    """``test`` is recorded opening each of ``targets`` (repo-relative) as data."""
    repo.conn.execute("INSERT OR IGNORE INTO read_test (test_id, test_file, opaque, observed) "
                      "VALUES (?, ?, '', 1)", (test, test.split("::")[0]))
    for target in targets:
        repo.conn.execute("INSERT INTO read_edge (test_id, kind, target) VALUES (?, ?, ?)",
                          (test, po.READ, target))
    repo.conn.commit()


def test_a_test_that_reads_the_changed_file_as_data_is_selected(tmp_path: Path) -> None:
    """A gate that scans source text (the kept-work guard, campaign 4) ran
    none of ``f``'s statements and depends on every one of them: its record
    is a read of the file, not a coverage row. The read record spells paths
    relative to the repository; the index spells them as given."""
    repo = Repo(tmp_path, records=EARLY)
    _reads(repo, "test_gate.py::t_scans", "m.py")
    _reads(repo, "test_other.py::t_reads_another", "other.py")
    repo.path.write_text(EDIT)
    chosen = ss.select_by_statement(repo.conn, {str(repo.path): BASE}, repo_root=tmp_path)
    assert chosen.readers == {"test_gate.py::t_scans"}
    assert chosen.tests == {"t_full", "test_gate.py::t_scans"}
    assert "test_gate.py::t_scans" not in chosen.narrowed | chosen.by_block


def test_a_reader_is_selected_for_a_change_no_test_runs(tmp_path: Path) -> None:
    """A docstring edit selects no test by coverage, and changes the text a
    reader reads all the same."""
    repo = Repo(tmp_path, records=EARLY)
    _reads(repo, "test_gate.py::t_scans", "m.py")
    repo.path.write_text(BASE.replace('"""Doc."""', '"""Other doc."""'))
    chosen = ss.select_by_statement(repo.conn, {str(repo.path): BASE}, repo_root=tmp_path)
    assert repo.answer(chosen, "f") == ss.PROSE
    assert chosen.tests == {"test_gate.py::t_scans"} == chosen.readers


def test_a_reader_is_named_as_the_index_spells_it(tmp_path: Path) -> None:
    """An index that spells a test id absolutely is matched to the read
    record's repository-relative id, as for the tests coverage cannot speak
    for; an unmeasured reader is selected too: this rule is not coverage's."""
    absolute = f"{tmp_path}/test_gate.py::t_scans"
    records = {**EARLY, absolute: {"g": {0}}}
    repo = Repo(tmp_path, records=records, no_read_record=[absolute])
    _reads(repo, "test_gate.py::t_scans", "m.py")
    repo.conn.execute("INSERT INTO unmeasured_test (test_id) VALUES (?)", (absolute,))
    repo.path.write_text(EDIT)
    chosen = ss.select_by_statement(repo.conn, {str(repo.path): BASE}, repo_root=tmp_path)
    assert chosen.readers == {absolute}
    assert absolute in chosen.tests


def test_without_a_repository_root_paths_are_matched_as_given(repo: Repo) -> None:
    _reads(repo, "test_gate.py::t_scans", str(repo.path))
    chosen = repo.select(EDIT)
    assert chosen.readers == {"test_gate.py::t_scans"}


def test_a_changed_path_outside_the_repository_is_matched_as_given(tmp_path: Path) -> None:
    repo = Repo(tmp_path, records=EARLY)
    _reads(repo, "test_gate.py::t_scans", str(repo.path))
    repo.path.write_text(EDIT)
    elsewhere = tmp_path / "repository"
    chosen = ss.select_by_statement(repo.conn, {str(repo.path): BASE}, repo_root=elsewhere)
    assert chosen.readers == {"test_gate.py::t_scans"}


# ── a new method that overrides an inherited one (INV-bifih's shape) ────────

A_SRC = "class A:\n    def m(self):\n        return 1\n\n    def n(self):\n        return 2\n"
B_SRC = "from .a import A\n\n\nclass B(A):\n    X = 1\n\n\ndef make():\n    return B()\n"
#: ``B`` gains ``m``: a call ``B().m()`` ran ``A.m`` and now runs ``B.m``.
B_OVERRIDES = B_SRC.replace("    X = 1\n", "    X = 1\n\n    def m(self):\n        return 3\n")


class Tree:
    """Source files under ``packages/p/src/p`` and an index in which each test
    ran the named blocks (with a record of every statement)."""

    def __init__(self, tmp_path: Path, files: dict[str, str],
                 records: dict[str, list[tuple[str, str]]]) -> None:
        self.root = tmp_path
        self.pkg = tmp_path / "packages" / "p" / "src" / "p"
        self.pkg.mkdir(parents=True)
        self.files = {"__init__.py": "", **files}
        for rel, text in self.files.items():
            self.at(rel).parent.mkdir(parents=True, exist_ok=True)
            self.at(rel).write_text(text)
        self.conn = si.open_index(tmp_path / "index.sqlite")
        edges = {test: [(str(self.at(rel)), block) for rel, block in ran]
                 for test, ran in records.items()}
        statements = {test: {key: _bitmap({0}) for key in keys} for test, keys in edges.items()}
        digests = {str(self.at(rel)): {b.name: b.digest
                                       for b in blocks_for_source(str(self.at(rel)), text)}
                   for rel, text in self.files.items()}
        si.store_edges(self.conn, edges, digests=digests, statements=statements)
        po.ensure_schema(self.conn)
        for test in records:
            self.conn.execute("INSERT INTO read_test (test_id, test_file, opaque, observed) "
                              "VALUES (?, ?, '', 1)", (test, test))
        self.conn.commit()

    def at(self, rel: str) -> Path:
        return self.pkg / rel

    def select(self, rel: str, new: str) -> ss.Selection:
        self.at(rel).write_text(new)
        return ss.select_by_statement(self.conn, {str(self.at(rel)): self.files[rel]},
                                      repo_root=self.root)

    def answer(self, chosen: ss.Selection, rel: str, block: str) -> str:
        return chosen.answers[(str(self.at(rel)), block)]


#: t_inh ran A.m through a B; t_n ran A.n; t_make ran make.
INHERITED = {"t_inh": [("a.py", "A.m")], "t_n": [("a.py", "A.n")], "t_make": [("b.py", "make")]}


def test_a_new_method_selects_the_tests_that_ran_the_method_it_overrides(
        tmp_path: Path) -> None:
    """t_inh is paired with ``A.m`` in a.py alone: no row on b.py, no pair with
    ``B``. ``B.m`` now runs in place of ``A.m`` for it, and it is selected."""
    tree = Tree(tmp_path, {"a.py": A_SRC, "b.py": B_SRC}, INHERITED)
    chosen = tree.select("b.py", B_OVERRIDES)
    assert "t_inh" in chosen.tests and "t_inh" in chosen.by_block
    assert tree.answer(chosen, "b.py", "B.m") == ss.OVERRIDE
    assert chosen.unknown == frozenset()
    # A.n is not overridden; make is not changed.
    assert chosen.tests == {"t_inh"}


# ── a new function a decorator runs at import ───────────────────────────────

REG_SRC = ("REGISTRY = []\n\n\ndef register(fn):\n    REGISTRY.append(fn)\n    return fn\n\n\n"
           "@register\ndef first():\n    return 1\n")


def test_a_new_decorated_function_is_import_time_code(tmp_path: Path) -> None:
    """``@register`` appends the new function to a registry as the module
    loads. A test that calls every registered function (t_reg, paired with
    ``first`` alone) runs it with no caller changed: the new def is handed
    to the import rule, which refuses narrowing."""
    tree = Tree(tmp_path, {"reg.py": REG_SRC}, {"t_reg": [("reg.py", "first")]})
    chosen = tree.select("reg.py", REG_SRC + "\n\n@register\ndef second():\n    return 2\n")
    assert (str(tree.at("reg.py")), "second") in chosen.import_time
    assert tree.answer(chosen, "reg.py", "second") == ss.NEW_DECORATED


def test_the_override_is_followed_up_the_chain_to_the_nearest_definer(tmp_path: Path) -> None:
    """``C(B)``, ``B(A)``, only ``A`` defines ``m``: a C that called ``m`` ran
    ``A.m``. When ``B`` defines ``m`` too, a C ran ``B.m`` and never ``A.m``
    through it, and the tests of ``A.m`` alone are not the change's."""
    c_src = "from .b import B\n\n\nclass C(B):\n    X = 1\n"
    c_new = c_src + "\n    def m(self):\n        return 4\n"
    records = {"t_a": [("a.py", "A.m")], "t_b": [("b.py", "B.m")]}
    chain = Tree(tmp_path / "chain", {"a.py": A_SRC, "b.py": B_SRC, "c.py": c_src}, records)
    chosen = chain.select("c.py", c_new)
    assert chain.answer(chosen, "c.py", "C.m") == ss.OVERRIDE
    assert chosen.tests == {"t_a"}
    nearer = Tree(tmp_path / "nearer", {"a.py": A_SRC, "b.py": B_OVERRIDES, "c.py": c_src},
                  records)
    assert nearer.select("c.py", c_new).tests == {"t_b"}


def test_every_base_of_a_multiply_inheriting_class_is_followed(tmp_path: Path) -> None:
    """A mixin before the base, both defining ``m``: either may have been the
    one a call reached, and the tests of both are selected."""
    mixin = "class Mixin:\n    def m(self):\n        return 0\n"
    d_src = ("from .a import A\nfrom .mixin import Mixin\n\n\n"
             "class D(Mixin, A):\n    X = 1\n")
    tree = Tree(tmp_path, {"a.py": A_SRC, "mixin.py": mixin, "d.py": d_src},
                {"t_a": [("a.py", "A.m")], "t_mixin": [("mixin.py", "Mixin.m")],
                 "t_n": [("a.py", "A.n")]})
    chosen = tree.select("d.py", d_src + "\n    def m(self):\n        return 5\n")
    assert chosen.tests == {"t_a", "t_mixin"}


def test_a_base_in_the_same_file_and_a_method_that_overrides_nothing(tmp_path: Path) -> None:
    """The same-file base is followed. ``fresh`` is defined by no ancestor:
    the rule for a plain new method, every test that reached the class."""
    src = A_SRC + "\n\nclass B(A):\n    def k(self):\n        return 9\n"
    tree = Tree(tmp_path, {"ab.py": src},
                {"t_inh": [("ab.py", "A.m")], "t_k": [("ab.py", "B.k")]})
    chosen = tree.select("ab.py", src + "\n    def m(self):\n        return 3\n")
    assert tree.answer(chosen, "ab.py", "B.m") == ss.OVERRIDE
    assert chosen.tests == {"t_inh", "t_k"}
    fresh = tree.select("ab.py", src + "\n    def fresh(self):\n        return 3\n")
    assert tree.answer(fresh, "ab.py", "B.fresh") == ss.NEW_METHOD
    assert fresh.tests == {"t_k"}


def test_a_base_that_is_not_source_of_the_repository_makes_the_method_unknown(
        tmp_path: Path) -> None:
    """``Exception.__str__`` ran for a test that printed a ``B`` error, and no
    record of this repository says so. What the new method overrides is not
    known: the block is unknown, so nothing is narrowed. The tests of the
    class itself are still selected."""
    src = "class B(Exception):\n    X = 1\n"
    tree = Tree(tmp_path, {"err.py": src}, {"t_err": [("err.py", "B")]})
    chosen = tree.select("err.py", src + "\n    def __str__(self):\n        return 'b'\n")
    key = (str(tree.at("err.py")), "B.__str__")
    assert chosen.answers[key] == ss.UNRESOLVED_BASE
    assert key in chosen.unknown
    assert chosen.tests == {"t_err"}


def test_an_overridden_method_the_index_has_never_seen_is_unknown(tmp_path: Path) -> None:
    """a.py resolves, and the index holds no block of it: who ran ``A.m`` is
    not known, which is not "nobody"."""
    tree = Tree(tmp_path, {"b.py": B_SRC}, {"t_make": [("b.py", "make")]})
    tree.at("a.py").write_text(A_SRC)
    chosen = tree.select("b.py", B_OVERRIDES)
    assert tree.answer(chosen, "b.py", "B.m") == ss.UNKNOWN
    assert (str(tree.at("b.py")), "B.m") in chosen.unknown
    assert chosen.tests == frozenset(), "make's test never reached class B"


def test_object_as_a_base_is_no_base(tmp_path: Path) -> None:
    src = "class B(object):\n    def k(self):\n        return 1\n"
    tree = Tree(tmp_path, {"o.py": src}, {"t_k": [("o.py", "B.k")]})
    chosen = tree.select("o.py", src + "\n    def m(self):\n        return 2\n")
    assert tree.answer(chosen, "o.py", "B.m") == ss.NEW_METHOD
    assert chosen.tests == {"t_k"} and chosen.unknown == frozenset()


def test_a_new_subclass_is_import_time_code_and_its_methods_keep_the_old_answer(
        tmp_path: Path) -> None:
    """A new class body is import-time code (narrowing refuses on it), and
    no unchanged call reaches a method of a class nobody could construct."""
    tree = Tree(tmp_path, {"a.py": A_SRC, "b.py": B_SRC}, INHERITED)
    chosen = tree.select("b.py", B_SRC + "\n\nclass E(A):\n    def m(self):\n        return 6\n")
    assert tree.answer(chosen, "b.py", "E") == ss.IMPORT_TIME
    assert tree.answer(chosen, "b.py", "E.m") == ss.NEW_METHOD
    assert chosen.tests == frozenset()


def test_a_new_method_with_an_inert_decorator_is_answered_as_a_method(tmp_path: Path) -> None:
    """``property`` and ``staticmethod`` register nothing; a decorator of the
    project's own may."""
    tree = Tree(tmp_path, {"a.py": A_SRC, "b.py": B_SRC}, INHERITED)
    chosen = tree.select("b.py", B_SRC.replace(
        "    X = 1\n", "    X = 1\n\n    @property\n    def m(self):\n        return 3\n\n"
        "    @staticmethod\n    def s():\n        return 0\n"))
    assert tree.answer(chosen, "b.py", "B.m") == ss.OVERRIDE
    assert tree.answer(chosen, "b.py", "B.s") == ss.NEW_METHOD
    assert chosen.import_time == frozenset()
    assert "t_inh" in chosen.tests
    registered = tree.select("b.py", B_SRC.replace(
        "    X = 1\n", "    X = 1\n\n    @hook('m')\n    def m(self):\n        return 3\n"))
    assert tree.answer(registered, "b.py", "B.m") == ss.NEW_DECORATED
    assert (str(tree.at("b.py")), "B.m") in registered.import_time


def test_an_undecorated_new_function_still_has_no_test_of_its_own(tmp_path: Path) -> None:
    tree = Tree(tmp_path, {"reg.py": REG_SRC}, {"t_reg": [("reg.py", "first")]})
    chosen = tree.select("reg.py", REG_SRC + "\n\ndef helper():\n    return 2\n")
    assert tree.answer(chosen, "reg.py", "helper") == ss.NEW
    assert chosen.import_time == frozenset() and chosen.tests == frozenset()


def test_a_second_def_of_a_name_takes_the_tests_of_the_one_it_shadows(tmp_path: Path) -> None:
    """``def first`` again, below the first: every call of ``first`` now runs
    the new body, labelled ``first#2``, and no caller changed. The tests that
    reached ``first`` are selected (block_hash records this as a false
    negative of selecting by block)."""
    tree = Tree(tmp_path, {"reg.py": REG_SRC + "\n\ndef other():\n    return 0\n"},
                {"t_reg": [("reg.py", "first")], "t_other": [("reg.py", "other")]})
    chosen = tree.select("reg.py", tree.files["reg.py"] + "\n\ndef first():\n    return 2\n")
    assert chosen.tests == {"t_reg"}
    assert tree.answer(chosen, "reg.py", "first#2") == ss.SHADOWS
    method = Tree(tmp_path / "m", {"a.py": A_SRC}, {"t_inh": [("a.py", "A.m")]})
    chosen = method.select("a.py", A_SRC + "\n    def m(self):\n        return 7\n")
    assert method.answer(chosen, "a.py", "A.m#2") == ss.SHADOWS
    assert chosen.tests == {"t_inh"}
    unseen = Tree(tmp_path / "u", {"a.py": A_SRC}, {})
    unseen.conn.execute("DELETE FROM block WHERE name = 'A.m'")
    unseen.conn.commit()
    chosen = unseen.select("a.py", A_SRC + "\n    def m(self):\n        return 7\n")
    assert unseen.answer(chosen, "a.py", "A.m#2") == ss.UNKNOWN
