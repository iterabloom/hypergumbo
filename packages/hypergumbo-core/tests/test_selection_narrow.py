# SPDX-License-Identifier: AGPL-3.0-or-later
"""Narrowing the run set: what may be dropped, and what forbids dropping it.

PHASE 3 (WI-bolot) is the first operation in this family that can REMOVE a test,
so it is the first one where being wrong costs something. The three rules below
are the whole safety argument, and each is a separate test because each has a
distinct way of being silently violated.

RULE 1 — DROP ONLY WITH POSITIVE EVIDENCE. A run-set file may be dropped only if
the index can actually speak about it: it has coverage rows, and none of its
tests are recorded as ``unmeasured``. 3,605 node ids in this suite produce no
coverage rows at all (the tracker package is outside COV_PATHS; subprocess and
``scripts/`` tests leave no in-process trace), and any test never yet run locally
is simply absent from the index. Absence of evidence is not evidence of absence,
so everything the index cannot speak about is kept unconditionally. This is the
``unmeasured`` precondition WI-bolot names as the one a careless implementation
breaks.

RULE 2 — KEEP THE WHOLE FILE-TOUCH SET, NOT JUST THE CHANGED BLOCKS. After the
suite, smart-test runs ``coverage report --include=<changed files>
--fail-under=100``, and that gate is WHOLE-FILE. Narrowing to the tests attached
to changed BLOCKS leaves the rest of each changed file uncovered and exits 1 — a
red run on every commit, which is worse than a slow one. Measured on this repo:
at one base the block-selected set was 3 files while the file-touch set was 13,
so the naive narrowing would have dropped 10 files that were covering the
changed files. The keep set is therefore every test observed executing ANY block
of a changed file.

RULE 3 — SOME CHANGES FORBID NARROWING OUTRIGHT, and the distinction here decided
the design. An unknown *coverable source* file — one under ``packages/*/src/``
that the index has never seen — means no test has been observed touching it, so
any droppable test could be its real cover. That forbids dropping. But three
things that LOOK like the same problem are not:

* **new blocks** are not a refusal. The first version of the measuring script
  made them one and reported "narrowing refused" on every commit examined,
  because ordinary development adds blocks. The reason new blocks look dangerous
  is that coverage cannot know which test covers new code — but the test that
  covers a developer's new code is a CHANGED TEST FILE, and changed test files
  enter the run set through a different selector that narrowing never touches.
* **unknown test files** are not a refusal: a new test file is unknown to the
  index by definition, and it is its own cover.
* **unknown files outside the coverage scope** (``scripts/``, docs, config) are
  not a refusal either. Nothing in-process can cover them, so their absence from
  the index is a structural fact rather than a knowledge gap; the tests that
  exercise them do so by subprocess, produce no rows, and are therefore kept by
  Rule 1 anyway.

A missing path — a changed file that no longer exists — always forbids
narrowing: its tests exercised code that is now gone, and those are worth
running.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.selection_index import (
    Selection,
    narrow_run_set,
    narrowing_blockers,
    open_index,
    speakable_test_files,
    touching_test_files,
)

_RUN = [
    "packages/hypergumbo-core/tests/test_alpha.py",
    "packages/hypergumbo-core/tests/test_beta.py",
    "packages/hypergumbo-core/tests/test_gamma.py",
]


def _sel(**kw) -> Selection:
    return Selection(
        tests=frozenset(kw.get("tests", ())),
        changed_blocks=frozenset(kw.get("changed", ())),
        new_blocks=frozenset(kw.get("new", ())),
        unknown_paths=frozenset(kw.get("unknown", ())),
        unmeasured=frozenset(kw.get("unmeasured", ())),
        missing_paths=frozenset(kw.get("missing", ())),
    )


class TestNarrowRunSet:
    def test_a_file_with_positive_evidence_of_irrelevance_is_dropped(self) -> None:
        got = narrow_run_set(
            _RUN,
            keep={_RUN[0]},
            droppable=set(_RUN),
            forbidden=(),
        )
        assert got.refusal is None
        assert got.kept == frozenset({_RUN[0]})
        assert got.dropped == frozenset({_RUN[1], _RUN[2]})

    def test_a_file_the_index_cannot_speak_about_is_never_dropped(self) -> None:
        """RULE 1. test_gamma is not in ``droppable``, so it survives."""
        got = narrow_run_set(
            _RUN,
            keep={_RUN[0]},
            droppable={_RUN[0], _RUN[1]},
            forbidden=(),
        )
        assert got.kept == frozenset({_RUN[0], _RUN[2]})
        assert got.dropped == frozenset({_RUN[1]})

    def test_the_keep_set_survives_even_when_droppable(self) -> None:
        """RULE 2. Being droppable is permission, not instruction."""
        got = narrow_run_set(
            _RUN,
            keep={_RUN[0], _RUN[1]},
            droppable=set(_RUN),
            forbidden=(),
        )
        assert got.kept == frozenset({_RUN[0], _RUN[1]})

    def test_a_forbidden_change_narrows_nothing(self) -> None:
        """RULE 3. The run set comes back untouched and the reason is reported."""
        got = narrow_run_set(
            _RUN,
            keep=set(),
            droppable=set(_RUN),
            forbidden=("unknown coverable source: a/src/x.py",),
        )
        assert got.refusal == "unknown coverable source: a/src/x.py"
        assert got.kept == frozenset(_RUN)
        assert got.dropped == frozenset()

    def test_keep_entries_outside_the_run_set_do_not_join_it(self) -> None:
        """Narrowing may only SHRINK. Adding is Phase 2's job, above."""
        got = narrow_run_set(
            _RUN,
            keep={_RUN[0], "packages/hypergumbo-core/tests/test_delta.py"},
            droppable=set(_RUN),
            forbidden=(),
        )
        assert got.kept == frozenset({_RUN[0]})

    def test_narrowing_to_nothing_is_refused(self) -> None:
        """An empty run set makes pytest exit 5 — green while running nothing.

        This project has been bitten by that exact shape before, so a narrowing
        that would leave no tests at all keeps the original set instead.
        """
        got = narrow_run_set(
            _RUN, keep=set(), droppable=set(_RUN), forbidden=(),
        )
        assert got.refusal is not None and "empty" in got.refusal
        assert got.kept == frozenset(_RUN)


class TestNarrowingBlockers:
    _ROOT = Path("/repo")

    def test_an_unknown_coverable_source_forbids_narrowing(self) -> None:
        got = narrowing_blockers(
            _sel(unknown={"/repo/packages/hypergumbo-core/src/hypergumbo_core/x.py"}),
            self._ROOT,
        )
        assert len(got) == 1 and "unknown coverable source" in got[0]

    def test_a_missing_path_forbids_narrowing(self) -> None:
        got = narrowing_blockers(
            _sel(missing={"/repo/packages/hypergumbo-core/src/hypergumbo_core/x.py"}),
            self._ROOT,
        )
        assert len(got) == 1 and "missing" in got[0]

    def test_new_blocks_do_not_forbid_narrowing(self) -> None:
        """The developer's new test is a CHANGED TEST FILE, selected elsewhere."""
        assert narrowing_blockers(
            _sel(new={("/repo/a/src/x.py", "f")}), self._ROOT,
        ) == ()

    def test_an_unknown_test_file_does_not_forbid_narrowing(self) -> None:
        assert narrowing_blockers(
            _sel(unknown={"/repo/packages/hypergumbo-core/tests/test_new.py"}),
            self._ROOT,
        ) == ()

    def test_an_unknown_file_outside_the_coverage_scope_does_not_forbid(self) -> None:
        """``scripts/`` is outside COV_PATHS; nothing in-process can cover it."""
        assert narrowing_blockers(
            _sel(unknown={"/repo/scripts/measure-thing.py"}), self._ROOT,
        ) == ()

    def test_a_clean_change_forbids_nothing(self) -> None:
        assert narrowing_blockers(_sel(), self._ROOT) == ()

    def test_both_blockers_are_reported(self) -> None:
        got = narrowing_blockers(
            _sel(unknown={"/repo/packages/p/src/p/a.py"},
                 missing={"/repo/packages/p/src/p/b.py"}),
            self._ROOT,
        )
        assert len(got) == 2


# ── The two index queries the rules are computed from ────────────────────────
#
# These were shipped once WITHOUT unit tests, on the reasoning that
# ``coverage-select`` exercises them. It does — by subprocess, which contributes
# no coverage — so the whole-file gate caught them at 95% and was right to.
# Recorded because "a script calls it" is a recurring excuse for an untested
# branch in this repo.


def _index(tmp_path: Path, *, blocks=(), unmeasured=()):
    """A real index with the given ``(test_id, path, name)`` rows."""
    conn = open_index(tmp_path / "index.sqlite")
    conn.executemany("INSERT INTO test_block (test_id, path, name) VALUES (?,?,?)",
                     blocks)
    conn.executemany("INSERT INTO unmeasured_test (test_id) VALUES (?)",
                     [(t,) for t in unmeasured])
    conn.commit()
    return conn


class TestTestsTouching:
    def test_a_test_touching_any_block_of_the_file_is_returned(self, tmp_path) -> None:
        """RULE 2's keep set: ANY block, not only the changed one.

        ``test_alpha`` executes ``other_fn``, which is NOT the block that
        changed — and it must still be kept, because it is contributing coverage
        rows to a file the whole-file gate is about to check.
        """
        src = f"{tmp_path}/pkg/src/mod.py"
        conn = _index(tmp_path, blocks=[
            (f"{tmp_path}/pkg/tests/test_alpha.py::test_one", src, "other_fn"),
            (f"{tmp_path}/pkg/tests/test_beta.py::test_two", src, "changed_fn"),
            (f"{tmp_path}/pkg/tests/test_gamma.py::test_three",
             f"{tmp_path}/pkg/src/elsewhere.py", "f"),
        ])
        got = touching_test_files(conn, [src], tmp_path)
        conn.close()
        assert got == frozenset({
            "pkg/tests/test_alpha.py", "pkg/tests/test_beta.py",
        })

    def test_no_paths_is_no_query(self, tmp_path) -> None:
        """An empty IN () is a SQL syntax error, so the empty case short-circuits."""
        conn = _index(tmp_path)
        assert touching_test_files(conn, [], tmp_path) == frozenset()
        conn.close()

    def test_an_untouched_file_returns_nothing(self, tmp_path) -> None:
        conn = _index(tmp_path, blocks=[
            (f"{tmp_path}/t/test_a.py::t", f"{tmp_path}/s/x.py", "f"),
        ])
        got = touching_test_files(conn, [f"{tmp_path}/s/y.py"], tmp_path)
        conn.close()
        assert got == frozenset()


class TestSpeakableTestFiles:
    def test_a_measured_file_is_speakable(self, tmp_path) -> None:
        conn = _index(tmp_path, blocks=[
            (f"{tmp_path}/t/test_a.py::t", f"{tmp_path}/s/x.py", "f"),
        ])
        got = speakable_test_files(conn, tmp_path)
        conn.close()
        assert got == frozenset({"t/test_a.py"})

    def test_one_unmeasured_test_makes_the_whole_file_unspeakable(
        self, tmp_path,
    ) -> None:
        """The conservative half of RULE 1, and the one worth seeing fail.

        ``test_a`` has 1 unmeasured test beside 1 measured one, and the whole
        file becomes undroppable. On the real index this is what holds 44.7% of
        wall-clock: ``test_profile.py`` is kept by 1 unmeasured test of 283.
        """
        conn = _index(
            tmp_path,
            blocks=[(f"{tmp_path}/t/test_a.py::measured", f"{tmp_path}/s/x.py", "f"),
                    (f"{tmp_path}/t/test_b.py::m", f"{tmp_path}/s/x.py", "f")],
            unmeasured=[f"{tmp_path}/t/test_a.py::subprocess_driven"],
        )
        got = speakable_test_files(conn, tmp_path)
        conn.close()
        assert got == frozenset({"t/test_b.py"})

    def test_a_file_absent_from_the_index_is_not_speakable(self, tmp_path) -> None:
        """Never observed at all — silence is not evidence of irrelevance."""
        conn = _index(tmp_path)
        got = speakable_test_files(conn, tmp_path)
        conn.close()
        assert got == frozenset()
