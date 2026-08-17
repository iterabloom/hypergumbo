# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a coverage ``Selection`` into test files a run may be widened with.

PHASE 2 of coverage-directed selection unions coverage-selected tests into the
local run set. Shadow mode only ever *reported* its selection, so nothing it
produced had to be runnable; the moment the selection is handed to pytest, two
properties start carrying weight that they did not carry before.

THE STALE-ENTRY PROPERTY, which is the whole reason this is a tested function
rather than a shell one-liner. The selection index is PERSISTENT and lives
out-of-repo. It therefore remembers test files that have since been renamed,
moved or deleted, and it has no way to notice — nothing prunes it on a rename.
Handing pytest a path that no longer exists is a collection ERROR, not a skip:
the run reddens. That would break Phase 2's entire safety story, which is
"strictly safer than today by construction, because it can only ADD tests". An
addition that fails the run is not an addition. So the selection is filtered
against the filesystem before it can widen anything.

THE REBASE PROPERTY. Index paths are absolute; smart-test speaks repo-relative
everywhere — the slice, the manifest, the union sites. Mixing the two produces
a union that looks like it worked and double-counts every file under two names.
``cmd_shadow`` already had to rebase for exactly this reason ("Index paths are
absolute; smart-test speaks repo-relative. Rebase before comparing or every file
differs and the report is pure noise"), so this is the second consumer and the
logic moves into one place.

Node ids reduce to FILES because that is the granularity the run set is in.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.selection_index import (
    rebase_to_repo,
    selectable_test_files,
)


def _tree(tmp_path: Path) -> Path:
    d = tmp_path / "packages" / "pkg" / "tests"
    d.mkdir(parents=True)
    (d / "test_alpha.py").write_text("")
    (d / "test_beta.py").write_text("")
    return tmp_path


class TestRebase:
    def test_an_absolute_id_loses_the_repo_prefix(self, tmp_path) -> None:
        got = rebase_to_repo({f"{tmp_path}/a/test_x.py::test_one"}, tmp_path)
        assert got == frozenset({"a/test_x.py::test_one"})

    def test_a_relative_id_is_left_alone(self, tmp_path) -> None:
        got = rebase_to_repo({"a/test_x.py::test_one"}, tmp_path)
        assert got == frozenset({"a/test_x.py::test_one"})

    def test_only_the_leading_occurrence_is_stripped(self, tmp_path) -> None:
        """A repo path appearing again inside the id is part of the id."""
        nested = f"{tmp_path}/a/test_x.py::test_of_{tmp_path}"
        got = rebase_to_repo({nested}, tmp_path)
        assert got == frozenset({f"a/test_x.py::test_of_{tmp_path}"})

    def test_a_path_that_merely_shares_a_prefix_is_not_stripped(
        self, tmp_path,
    ) -> None:
        """``/repo`` must not swallow ``/repo-backup``."""
        other = f"{tmp_path}-backup/a/test_x.py::test_one"
        assert rebase_to_repo({other}, tmp_path) == frozenset({other})


class TestSelectableTestFiles:
    def test_a_node_id_reduces_to_its_file(self, tmp_path) -> None:
        root = _tree(tmp_path)
        got = selectable_test_files(
            {f"{root}/packages/pkg/tests/test_alpha.py::test_one"}, root)
        assert got == frozenset({"packages/pkg/tests/test_alpha.py"})

    def test_many_node_ids_in_one_file_collapse(self, tmp_path) -> None:
        root = _tree(tmp_path)
        base = f"{root}/packages/pkg/tests/test_alpha.py"
        got = selectable_test_files(
            {f"{base}::test_one", f"{base}::Klass::test_two", base}, root)
        assert got == frozenset({"packages/pkg/tests/test_alpha.py"})

    def test_a_vanished_test_file_is_dropped(self, tmp_path) -> None:
        """THE SAFETY PROPERTY: a stale index entry must not redden the run.

        The index remembers a file that has since been renamed or deleted.
        pytest treats a missing path as a collection ERROR, so an unfiltered
        union would fail runs it was only supposed to widen.
        """
        root = _tree(tmp_path)
        got = selectable_test_files({
            f"{root}/packages/pkg/tests/test_alpha.py::test_one",
            f"{root}/packages/pkg/tests/test_renamed_away.py::test_two",
        }, root)
        assert got == frozenset({"packages/pkg/tests/test_alpha.py"})

    def test_every_surviving_file_is_kept(self, tmp_path) -> None:
        """NEGATIVE CONTROL for the filter: it must not be dropping everything.

        A filter that returned the empty set would pass the test above and
        silently disable Phase 2 — the union would add nothing, forever, and
        look like a selector that simply never fires.
        """
        root = _tree(tmp_path)
        got = selectable_test_files({
            f"{root}/packages/pkg/tests/test_alpha.py::test_one",
            f"{root}/packages/pkg/tests/test_beta.py::test_two",
        }, root)
        assert got == frozenset({
            "packages/pkg/tests/test_alpha.py",
            "packages/pkg/tests/test_beta.py",
        })

    def test_nothing_selected_is_nothing_added(self, tmp_path) -> None:
        assert selectable_test_files(set(), _tree(tmp_path)) == frozenset()

    def test_a_directory_is_not_a_test_file(self, tmp_path) -> None:
        """``Path.exists()`` is true for a directory; pytest needs a file."""
        root = _tree(tmp_path)
        got = selectable_test_files({f"{root}/packages/pkg/tests"}, root)
        assert got == frozenset()
