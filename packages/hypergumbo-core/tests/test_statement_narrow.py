# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a local run leaves out by statement, and when it leaves out nothing.

Each test builds a small index for a toy repository: one package source file,
two test files, which test ran which statements of which function, read
records, and a complete run that measured the source file. It then changes the
source on disk and asks.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Iterable, Optional

import pytest

from hypergumbo_core import prose_only as po
from hypergumbo_core import selection_index as si
from hypergumbo_core import statement_narrow as sn
from hypergumbo_core.block_hash import blocks_for_source

SOURCE_REL = "packages/p/src/p/m.py"
TESTS_A = "packages/p/tests/test_a.py"
TESTS_B = "packages/p/tests/test_b.py"

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
''')

#: test (in test_a.py unless it says test_b) -> {block: statements, None = no record}
RECORDS: dict[str, dict[str, Optional[set[int]]]] = {
    f"{TESTS_A}::t_early": {"f": {1, 2}},
    f"{TESTS_A}::t_early_too": {"f": {1, 2}},
    f"{TESTS_A}::t_full": {"f": {1, 3, 4, 5}},
    f"{TESTS_A}::t_g": {"g": {0}},
    f"{TESTS_A}::t_g_too": {"g": {0}},
    f"{TESTS_B}::t_b_full": {"f": {1, 3, 4, 5}},
}


def _bitmap(ordinals: Iterable[int]) -> bytes:
    return si._int_bitmap(sum(1 << ordinal for ordinal in ordinals))


class Repo:
    def __init__(self, root: Path,
                 records: Optional[dict[str, dict[str, Optional[set[int]]]]] = None,
                 started: Optional[dict[str, str]] = None,
                 complete: bool = True) -> None:
        records = RECORDS if records is None else records
        self.root = root
        self.path = root / SOURCE_REL
        self.path.parent.mkdir(parents=True)
        self.path.write_text(BASE)
        self.conn = si.open_index(root / "index.sqlite")
        key = str(self.path)
        # The index spells tests absolutely, as pytest-cov's contexts do.
        spelled = {test: f"{root}/{test}" for test in records}
        edges = {spelled[t]: [(key, b) for b in blocks] for t, blocks in records.items()}
        statements = {spelled[t]: {(key, b): _bitmap(ran) for b, ran in blocks.items()
                                   if ran is not None} for t, blocks in records.items()}
        digests = {key: {b.name: b.digest for b in blocks_for_source(key, BASE)}}
        si.store_edges(self.conn, edges, digests=digests, statements=statements)
        po.ensure_schema(self.conn)
        for test in sorted(records):
            self.conn.execute(
                "INSERT INTO read_test (test_id, test_file, opaque, observed) "
                "VALUES (?, ?, ?, 1)",
                (test, test.split("::")[0], (started or {}).get(test, "")))
        # One complete run, in this checkout, that measured the source file
        # and left rows for every test.
        cur = self.conn.cursor()
        cur.execute("INSERT INTO run (checkout, complete) VALUES (?, ?)", (str(root), int(complete)))
        run = cur.lastrowid
        cur.execute("INSERT INTO run_file (run, file) SELECT ?, id FROM file WHERE path = ?",
                    (run, key))
        cur.execute("INSERT INTO test_run (test, run, rows) SELECT id, ?, 1 FROM test", (run,))
        self.conn.commit()

    def narrow(self, new: Optional[str] = None, *, changed: Iterable[str] = (SOURCE_REL,),
               run_files: Iterable[str] = (TESTS_A, TESTS_B),
               whole: Iterable[str] = ()) -> sn.StatementNarrowing:
        if new is not None:
            self.path.write_text(new)
        sources = {str(self.root / rel): BASE for rel in changed if sn.is_judged_source(rel)}
        return sn.narrow_by_statement(self.conn, self.root, sources, changed, run_files, whole)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


EDIT_B = BASE.replace("b = a + 1 ", "b = a + 2 ")


def test_tests_that_ran_none_of_the_change_are_left_out_one_coverer_kept(repo: Repo) -> None:
    out = repo.narrow(EDIT_B)
    assert out.refusal is None
    # t_full and t_b_full ran the changed statement. The early returners and
    # g's tests ran none of it; one of each stays so statement 2 of f and g
    # stay covered, and the other is left out.
    assert out.left_out == {f"{TESTS_A}::t_early_too", f"{TESTS_A}::t_g_too"}
    assert out.kept_for_coverage == {f"{TESTS_A}::t_early", f"{TESTS_A}::t_g"}
    assert out.candidates == 6


def test_a_whole_file_and_a_file_outside_the_run_lose_nothing(repo: Repo) -> None:
    out = repo.narrow(EDIT_B, run_files=(TESTS_A,), whole=(TESTS_A,))
    assert out.refusal is None
    assert out.left_out == frozenset()
    assert out.candidates == 0


def test_a_test_starting_python_is_never_left_out(tmp_path: Path) -> None:
    repo = Repo(tmp_path, started={f"{TESTS_A}::t_early_too": "subprocess.Popen:python"})
    out = repo.narrow(EDIT_B)
    assert f"{TESTS_A}::t_early_too" not in out.left_out
    # It covers what t_early ran, so t_early needs no putting back.
    assert out.left_out == {f"{TESTS_A}::t_early", f"{TESTS_A}::t_g_too"}


def test_a_file_whose_last_run_was_not_complete_is_not_narrowed(tmp_path: Path) -> None:
    out = Repo(tmp_path, complete=False).narrow(EDIT_B)
    assert out.refusal is None
    assert out.left_out == frozenset()


def test_a_block_reached_only_by_tests_without_a_record_keeps_one(tmp_path: Path) -> None:
    records = {**RECORDS, f"{TESTS_A}::t_g": {"g": None}, f"{TESTS_A}::t_g_too": {"g": None}}
    out = Repo(tmp_path, records=records).narrow(EDIT_B)
    assert f"{TESTS_A}::t_g" in out.kept_for_coverage
    assert f"{TESTS_A}::t_g_too" in out.left_out


def test_running_tests_already_covering_the_block_need_nothing_back(tmp_path: Path) -> None:
    # t_early ran the changed statement too here, so it is selected and covers
    # what t_early_too ran.
    records = {**RECORDS, f"{TESTS_A}::t_early": {"f": {1, 2, 4}}}
    out = Repo(tmp_path, records=records).narrow(EDIT_B)
    assert f"{TESTS_A}::t_early_too" in out.left_out
    assert f"{TESTS_A}::t_early_too" not in out.kept_for_coverage


@pytest.mark.parametrize("path", [
    "packages/p/tests/conftest.py",
    "packages/p/tests/helpers.py",
    "packages/p/tests/fixtures/test_data.py",
    "packages/p/src/p/catalogue.yaml",
    "packages/p/src/p/README.md",
    "pyproject.toml",
    "scripts/smart-test",
])
def test_a_change_it_has_no_rule_for_refuses(repo: Repo, path: str) -> None:
    out = repo.narrow(EDIT_B, changed=(SOURCE_REL, path))
    assert out.refusal is not None and path in out.refusal
    assert out.left_out == frozenset()


@pytest.mark.parametrize("path", [
    "CHANGELOG.md", "docs/x.md", ".ci/affected-tests.txt",
    ".agent/tracker/.ops/.WI-x.ops", TESTS_B,
])
def test_documents_ci_data_tracker_data_and_test_files_do_not_refuse(
        repo: Repo, path: str) -> None:
    assert repo.narrow(EDIT_B, changed=(SOURCE_REL, path)).refusal is None


def test_no_package_source_changed_refuses(repo: Repo) -> None:
    out = repo.narrow(changed=(TESTS_B,))
    assert out.refusal == "no package source changed"
    assert out.selection is None


def test_import_time_code_refuses(repo: Repo) -> None:
    out = repo.narrow(BASE.replace("LIMIT = 3", "LIMIT = 4"))
    assert out.refusal is not None and "import-time" in out.refusal


def test_a_never_seen_block_is_named_in_the_refusal(repo: Repo) -> None:
    repo.conn.execute("DELETE FROM block WHERE name = 'g'")
    repo.conn.commit()
    out = repo.narrow(EDIT_B.replace("return 1 ", "return 2 "))
    assert out.refusal == "1 changed block(s) the index has never seen"


def test_a_file_that_does_not_parse_refuses(repo: Repo) -> None:
    assert repo.narrow("def f(:\n").refusal == "a changed file does not parse"


def test_the_index_may_spell_tests_relative(tmp_path: Path) -> None:
    repo = Repo(tmp_path)
    repo.conn.execute("UPDATE test SET test_id = substr(test_id, ?)", (len(f"{tmp_path}/") + 1,))
    repo.conn.commit()
    out = repo.narrow(EDIT_B)
    assert out.left_out == {f"{TESTS_A}::t_early_too", f"{TESTS_A}::t_g_too"}


def test_deselected_matches_through_the_xdist_group_suffix() -> None:
    collected = ["a.py::t1", "a.py::t2@grp", "a.py::t3[x]", "a.py::t4"]
    assert sn.deselected(collected, {"a.py::t2", "a.py::t3[x]"}) == [1, 2]
