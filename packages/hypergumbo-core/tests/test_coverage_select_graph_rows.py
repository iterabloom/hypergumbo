# SPDX-License-Identifier: AGPL-3.0-or-later
"""``coverage-select``'s graph commands, run for real against a small repository.

``graph-store`` keeps a survey's graph as rows, ``stale-record`` does the same
for every fresh analysis smart-test makes, and ``graph-walk`` answers the
reverse file walk from those rows. A caller ACTS on ``graph-walk``'s output
(the files it names decide which tests run), so what is pinned first is that
its three outcomes cannot be mistaken for one another:

    exit 0, output written        a stored graph was walked; an empty file
                                  means no node of it is in the changed files
    exit 3, nothing written       there is no stored graph to walk
    exit 4, nothing written       the walk failed

``coverage-select`` degrades every other command's failure to exit 0, because
those ride on a test run and must not fail it. ``graph-walk`` must not: exit 0
with no output would read as "no test depends on this change".
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from hypergumbo_core import graph_rows

_REPO = Path(__file__).resolve().parents[3]
_HELPER = _REPO / "scripts" / "coverage-select"

_GRAPH = {
    "nodes": [
        {"id": "a", "path": "packages/p/src/p/a.py"},
        {"id": "b", "path": "packages/p/src/p/b.py"},
        {"id": "ta", "path": "packages/p/tests/test_a.py"},
        {"id": "tb", "path": "packages/p/tests/test_b.py"},
    ],
    # test_a reaches a through b; test_b reaches b only.
    "edges": [{"src": "b", "dst": "a"}, {"src": "ta", "dst": "b"}, {"src": "tb", "dst": "b"}],
}


def _git(root: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",  # noqa: S607
         "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
        cwd=str(root), capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _helper(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    logs = repo.parent / "logs"
    return subprocess.run(
        [sys.executable, str(_HELPER), "--repo-root", str(repo),
         "--index", str(logs / "index.sqlite"), "--stale-log", str(logs / "stale.jsonl"),
         *argv],
        capture_output=True, text=True, cwd=str(repo))


def _survey(repo: Path, tree: str, content: object, build: str = "build1") -> Path:
    survey = repo.parent / "cache" / "results" / tree / build / "survey.json"
    survey.parent.mkdir(parents=True)
    survey.write_text(json.dumps(content))
    return survey


def _changed(repo: Path, *names: str) -> Path:
    listing = repo.parent / "changed.txt"
    listing.write_text("".join(f"{name}\n" for name in names))
    return listing


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "packages/p/src/p").mkdir(parents=True)
    (root / "packages/p/src/p/a.py").write_text("A = 1\n")
    (tmp_path / "logs").mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "one")
    return root


def test_a_stored_graph_is_walked_and_named(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    stored = _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree1", _GRAPH)))
    assert stored.returncode == 0, stored.stderr
    assert f"stored graph 1 (4 nodes, 3 edges, head {head[:9]})" in stored.stderr

    out = repo.parent / "walk.txt"
    walked = _helper(repo, "graph-walk", "--changed-files",
                     str(_changed(repo, "packages/p/src/p/a.py")), "--output", str(out))
    assert walked.returncode == 0, walked.stderr
    assert out.read_text().splitlines() == [
        "packages/p/src/p/a.py", "packages/p/src/p/b.py", "packages/p/tests/test_a.py",
        "packages/p/tests/test_b.py"]
    assert f"4 file(s) from graph 1: head {head[:9]}, 0 commit(s) old, build build1" in walked.stderr

    # One hop from a.py is b.py; the tests are two hops away.
    assert _helper(repo, "graph-walk", "--changed-files", str(_changed(repo, "packages/p/src/p/a.py")),
                   "--output", str(out), "--max-hops", "2").returncode == 0
    assert out.read_text().splitlines() == ["packages/p/src/p/a.py", "packages/p/src/p/b.py"]

    listing = _helper(repo, "graph-list").stdout
    assert listing.startswith(f"graph 1: head {head[:9]}, 0 commit(s) old, build build1, 4 nodes")


def test_a_walk_that_names_nothing_still_writes_its_empty_answer(repo: Path) -> None:
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree1", _GRAPH)))
    out = repo.parent / "walk.txt"
    walked = _helper(repo, "graph-walk", "--changed-files", str(_changed(repo, "README.md")),
                     "--output", str(out))
    assert walked.returncode == 0
    assert out.exists() and out.read_text() == ""


def test_no_stored_graph_is_exit_3_and_writes_nothing(repo: Path) -> None:
    out = repo.parent / "walk.txt"
    listing = _changed(repo, "packages/p/src/p/a.py")
    # No index file at all.
    none = _helper(repo, "graph-walk", "--changed-files", str(listing), "--output", str(out))
    assert (none.returncode, out.exists()) == (3, False)
    assert "no stored graph" in none.stderr
    assert not (repo.parent / "logs" / "index.sqlite").exists(), "asking must not create an index"
    assert _helper(repo, "graph-list").stdout.strip() == "no index: no stored graph"

    # An index with a graph, asked for another id or another build.
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree1", _GRAPH)))
    for extra in (["--graph", "7"], ["--build", "another"]):
        missing = _helper(repo, "graph-walk", "--changed-files", str(listing),
                          "--output", str(out), *extra)
        assert (missing.returncode, out.exists()) == (3, False), missing.stderr


def test_a_failed_walk_is_exit_4_not_the_helpers_usual_zero(repo: Path) -> None:
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree1", _GRAPH)))
    out = repo.parent / "walk.txt"
    failed = _helper(repo, "graph-walk", "--changed-files", str(repo.parent / "no-such-list.txt"),
                     "--output", str(out))
    assert (failed.returncode, out.exists()) == (4, False)
    assert "graph-walk FAILED" in failed.stderr


def test_the_newest_graph_is_walked_and_its_age_is_counted_from_its_commit(repo: Path) -> None:
    first_head = _git(repo, "rev-parse", "HEAD")
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree1", _GRAPH)))
    (repo / "packages/p/src/p/a.py").write_text("A = 2\n")
    _git(repo, "commit", "-q", "-am", "two")
    out = repo.parent / "walk.txt"
    listing = _changed(repo, "packages/p/src/p/b.py")
    old = _helper(repo, "graph-walk", "--changed-files", str(listing), "--output", str(out))
    assert f"graph 1: head {first_head[:9]}, 1 commit(s) old" in old.stderr

    # A second graph, of a tree with one edited and one new file, in which
    # test_b no longer reaches b. A pending tracker op is no part of the tree.
    (repo / "packages/p/src/p/a.py").write_text("A = 3\n")
    (repo / "packages/p/src/p/new.py").write_text("N = 1\n")
    (repo / ".agent/tracker/.ops").mkdir(parents=True)
    (repo / ".agent/tracker/.ops/.WI-x.ops").write_text("op\n")
    newer = {"nodes": _GRAPH["nodes"], "edges": [{"src": "ta", "dst": "b"}]}
    stored = _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree2", newer)))
    assert "stored graph 2" in stored.stderr
    assert "and 2 file(s) not in that commit" in stored.stderr
    conn = sqlite3.connect(repo.parent / "logs" / "index.sqlite")
    newest, oldest = graph_rows.stored_graphs(conn)
    conn.close()
    assert [g.dirty_files for g in (newest, oldest)] == [
        ("packages/p/src/p/a.py", "packages/p/src/p/new.py"), ()]
    # Each graph carries its commit's tree id, which outlives a rebased commit.
    assert oldest.git_tree == _git(repo, "rev-parse", f"{first_head}^{{tree}}")
    assert newest.git_tree == _git(repo, "rev-parse", "HEAD^{tree}") != oldest.git_tree
    assert _helper(repo, "graph-walk", "--changed-files", str(listing),
                   "--output", str(out)).returncode == 0
    assert out.read_text().splitlines() == ["packages/p/src/p/b.py", "packages/p/tests/test_a.py"]
    by_id = _helper(repo, "graph-walk", "--changed-files", str(listing), "--output", str(out),
                    "--graph", "1")
    assert by_id.returncode == 0 and "packages/p/tests/test_b.py" in out.read_text()

    # A graph whose commit this repository does not know: its age is not known.
    # With --head the working tree says nothing about the survey's tree; the
    # files that differed are given, or there were none.
    was_dirty = repo.parent / "was_dirty.txt"
    was_dirty.write_text("packages/p/src/p/b.py\n")
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree3", _GRAPH)),
            "--head", "0" * 40, "--dirty-files", str(was_dirty))
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree4", _GRAPH)),
            "--head", first_head)
    listing_out = _helper(repo, "graph-list").stdout.splitlines()
    assert listing_out[0].startswith(f"graph 4: head {first_head[:9]}, 1 commit(s) old")
    assert listing_out[1].startswith(
        "graph 3: head 000000000, and 1 file(s) not in that commit, age not known")
    conn = sqlite3.connect(repo.parent / "logs" / "index.sqlite")
    assert [g.git_tree for g in graph_rows.stored_graphs(conn)][:2] == [
        _git(repo, "rev-parse", f"{first_head}^{{tree}}"), None], "an unknown commit has no tree"
    conn.close()


def test_a_fresh_analysis_recorded_by_the_shadow_leaves_its_rows(repo: Path) -> None:
    """``stale-record`` is what smart-test calls after every fresh slice."""
    fresh_out = repo.parent / "fresh.txt"
    fresh_out.write_text(f"{repo}/packages/p/tests/test_a.py\n")
    done = _helper(repo, "stale-record", "--fresh-out", str(fresh_out),
                   "--fresh-survey", str(_survey(repo, "tree1", _GRAPH)), "--why", "first")
    assert done.returncode == 0 and "stored graph 1 (4 nodes, 3 edges" in done.stderr
    out = repo.parent / "walk.txt"
    assert _helper(repo, "graph-walk", "--changed-files", str(_changed(repo, "packages/p/src/p/b.py")),
                   "--output", str(out)).returncode == 0
    assert "packages/p/tests/test_b.py" in out.read_text()


@pytest.mark.parametrize("content", [{}, {"nodes": []}, {"nodes": {}, "edges": []}, []])
def test_a_file_that_holds_no_graph_stores_nothing_and_opens_no_index(
        repo: Path, content: object) -> None:
    survey = _survey(repo, "tree1", content)
    fresh_out = repo.parent / "fresh.txt"
    fresh_out.write_text("")
    done = _helper(repo, "stale-record", "--fresh-out", str(fresh_out),
                   "--fresh-survey", str(survey), "--why", "first")
    assert done.returncode == 0
    assert "holds no node and edge lists; nothing stored" in done.stderr
    assert not (repo.parent / "logs" / "index.sqlite").exists()
    assert _helper(repo, "graph-store", "--survey", str(survey)).returncode == 1


# ── graph-select: the stored graph's answer for a LATER tree ─────────────────

A, B = "packages/p/src/p/a.py", "packages/p/src/p/b.py"
TEST_A, TEST_B = "packages/p/tests/test_a.py", "packages/p/tests/test_b.py"
#: a.py <- test_a; b.py <- test_b. Nothing joins the two halves.
_TWO_HALVES = {
    "nodes": [{"id": "a", "path": A}, {"id": "b", "path": B},
              {"id": "ta", "path": TEST_A}, {"id": "tb", "path": TEST_B}],
    "edges": [{"src": "ta", "dst": "a"}, {"src": "tb", "dst": "b"}],
}


@pytest.fixture
def later(repo: Path) -> Path:
    """The repository with both source files committed and a graph stored of
    exactly that commit."""
    (repo / B).write_text("B = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two files")
    assert _helper(repo, "graph-store", "--survey",
                   str(_survey(repo, "tree1", _TWO_HALVES))).returncode == 0
    return repo


def _select(repo: Path, *changed: str, extra: tuple[str, ...] = ()) -> tuple[
        subprocess.CompletedProcess[str], list[str] | None, dict]:
    out, report = repo.parent / "select.txt", repo.parent / "report.json"
    for stale in (out, report):
        if stale.exists():
            stale.unlink()
    done = _helper(repo, "graph-select", "--changed-files", str(_changed(repo, *changed)),
                   "--output", str(out), "--report", str(report), *extra)
    return (done, out.read_text().splitlines() if out.exists() else None,
            json.loads(report.read_text()) if report.exists() else {})


def test_a_graph_of_this_very_tree_answers_for_the_change_alone(later: Path) -> None:
    (later / A).write_text("A = 2\n")
    done, files, report = _select(later, A)
    assert done.returncode == 0, done.stderr
    assert files == [A, TEST_A]
    head = _git(later, "rev-parse", "HEAD")
    assert done.stdout.strip() == (
        f"stored graph 1 of {head[:9]}, 0 commit(s) old: 1 seed file(s), 0 of them not this "
        "change's, reaching 0 test file(s) this change does not; 2 file(s) named")
    assert (report["code"], report["age_commits"], report["extra_seeds"]) == (0, 0, 0)


def test_what_changed_since_the_graph_is_seeded_too_and_its_tests_are_counted(later: Path) -> None:
    # Someone else's change to b.py is merged after the graph was made.
    (later / B).write_text("B = 2\n")
    _git(later, "commit", "-q", "-am", "their change")
    (later / A).write_text("A = 2\n")
    done, files, report = _select(later, A)
    assert done.returncode == 0, done.stderr
    assert files == [A, B, TEST_A, TEST_B], "b.py differs from the graph's tree: it is a seed"
    assert "1 commit(s) old: 2 seed file(s), 1 of them not this change's, reaching 1 test" in done.stdout
    assert (report["seeds"], report["extra_seeds"], report["extra_test_files"]) == (2, 1, 1)

    # One surplus test file is over a limit of none: a fresh analysis instead.
    refused, files, report = _select(later, A, extra=("--max-extra-tests", "0"))
    assert (refused.returncode, files) == (6, None)
    assert "they reach 1 test file(s) this change does not (limit 0)" in refused.stderr
    assert report["code"] == 6 and "limit 0" in report["reason"]


def test_a_file_that_was_uncommitted_when_the_graph_was_made_stays_a_seed(repo: Path) -> None:
    """The graph saw b.py as it was edited then. Putting b.py back does not make
    the graph's tree and this one the same."""
    (repo / B).write_text("B = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two files")
    (repo / B).write_text("B = 999\n")
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree1", _TWO_HALVES)))
    _git(repo, "checkout", "--", B)
    (repo / A).write_text("A = 2\n")
    done, files, report = _select(repo, A)
    assert done.returncode == 0, done.stderr
    assert files == [A, B, TEST_A, TEST_B]
    assert (report["dirty"], report["extra_seeds"]) == (True, 1)


def test_an_added_test_runs_itself_and_an_added_file_is_left_to_what_mentions_it(
        later: Path) -> None:
    (later / "packages/p/tests").mkdir(parents=True)
    new_test = "packages/p/tests/test_new.py"
    (later / new_test).write_text("def test_new(): pass\n")
    new_source = "packages/p/src/p/widget_store.py"
    (later / new_source).write_text("STORE = {}\n")
    (later / A).write_text("A = 2\nfrom p.widget_store import STORE\n")
    # The change list names a.py only: the files that are not tracked are found
    # by asking git, not by trusting the caller to have listed them.
    done, files, report = _select(later, A)
    assert done.returncode == 0, done.stderr
    assert files == [A, TEST_A, new_test], (
        "the new test is named though the graph has no node for it; the new "
        "source file has none either and is answered for by a.py, which mentions it")
    assert report["added_tests"] == [new_test]
    assert report["added_mentioned"] == [[new_source, A]]


@pytest.mark.parametrize(("added", "reason"), [
    ("packages/p/tests/fixtures/tree/lonely_fixture.txt", "no other changed file mentions it"),
    ("packages/p/tests/conftest.py", "an added conftest.py applies to every test under"),
])
def test_an_added_file_nothing_accounts_for_is_exit_5(later: Path, added: str, reason: str) -> None:
    (later / added).parent.mkdir(parents=True, exist_ok=True)
    (later / added).write_text("x\n")
    (later / A).write_text("A = 2  # see conftest\n")
    done, files, report = _select(later, A)
    assert (done.returncode, files) == (5, None), done.stderr
    assert f"{added} was added since the stored graph and {reason}" in done.stderr
    assert report["code"] == 5


def test_a_pending_tracker_op_is_no_part_of_either_tree(later: Path) -> None:
    (later / ".agent/tracker/.ops").mkdir(parents=True)
    (later / ".agent/tracker/.ops/.WI-x.ops").write_text("op\n")
    (later / A).write_text("A = 2\n")
    done, files, report = _select(later, A)
    assert done.returncode == 0 and report["seeds"] == 1, done.stderr


def test_no_usable_stored_graph_is_exit_3_with_the_reason_for_each(later: Path) -> None:
    (later / A).write_text("A = 2\n")
    _git(later, "commit", "-q", "-am", "mine")
    too_old, files, report = _select(later, A, extra=("--max-age", "0"))
    assert (too_old.returncode, files) == (3, None)
    assert "no usable stored graph: graph 1 is 1 commits old (limit 0)" in too_old.stderr
    assert report["code"] == 3 and report["id"] is None

    other_build, files, _ = _select(later, A, extra=("--build", "another"))
    assert (other_build.returncode, files) == (3, None)
    assert "graph 1 is from another analyzer build" in other_build.stderr

    # A graph whose commit, and so whose tree, this repository never had.
    _helper(later, "graph-store", "--survey", str(_survey(later, "tree2", _TWO_HALVES)),
            "--head", "0" * 40)
    unknown, files, _ = _select(later, A, extra=("--max-age", "0"))
    assert (unknown.returncode, files) == (3, None)
    assert "graph 2: git does not have its tree; graph 1 is 1 commits old" in unknown.stderr


def test_no_index_and_a_missing_change_list_are_3_and_4(repo: Path) -> None:
    done, files, _ = _select(repo, "packages/p/src/p/a.py")
    assert (done.returncode, files) == (3, None) and "no index" in done.stderr
    out = repo.parent / "select.txt"
    failed = _helper(repo, "graph-select", "--changed-files", str(repo.parent / "absent.txt"),
                     "--output", str(out))
    assert (failed.returncode, out.exists()) == (4, False)
    assert "graph-select FAILED" in failed.stderr


def test_a_graph_of_a_commit_a_rebase_rewrote_is_found_by_its_tree(later: Path) -> None:
    """A pull request merged by rebase gets new commit ids. The graph made on
    the branch names a commit that no longer exists; its tree is the merged
    commit's tree, and that is how the graph is aged and compared."""
    _git(later, "checkout", "-q", "-b", "feature")
    (later / A).write_text("A = 2\n")
    _git(later, "commit", "-q", "-am", "on the branch")
    branch_commit = _git(later, "rev-parse", "HEAD")
    _helper(later, "graph-store", "--survey", str(_survey(later, "tree2", _TWO_HALVES)))
    # The "merge": the same tree under a new commit on main, then one more commit.
    _git(later, "checkout", "-q", "main")
    merged = _git(later, "commit-tree", f"{branch_commit}^{{tree}}", "-p", "main", "-m", "merged")
    _git(later, "reset", "-q", "--hard", merged)
    (later / B).write_text("B = 2\n")
    _git(later, "commit", "-q", "-am", "the next change")
    _git(later, "branch", "-q", "-D", "feature")
    _git(later, "reflog", "expire", "--expire=now", "--all")
    _git(later, "gc", "-q", "--prune=now")
    gone = subprocess.run(["git", "cat-file", "-e", f"{branch_commit}^{{commit}}"],  # noqa: S607
                          cwd=str(later), capture_output=True)
    assert gone.returncode != 0, "the branch commit must really be gone for this test to mean anything"

    assert f"graph 2: head {branch_commit[:9]}, 1 commit(s) old" in _helper(later, "graph-list").stdout
    (later / A).write_text("A = 3\n")
    done, files, report = _select(later, A)
    assert done.returncode == 0, done.stderr
    assert report["id"] == 2 and report["age_commits"] == 1
    assert files == [A, B, TEST_A, TEST_B], "b.py changed after the merged tree: a seed"


def test_a_file_the_graph_already_saw_untracked_is_not_added_since(repo: Path) -> None:
    """A fixture nothing mentions was on disk, not yet tracked, when the graph
    was made: the analysis saw it. It is a seed, not a file added since."""
    (repo / B).write_text("B = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two files")
    lonely = "packages/p/tests/fixtures/tree/lonely_fixture.txt"
    (repo / lonely).parent.mkdir(parents=True)
    (repo / lonely).write_text("x\n")
    _helper(repo, "graph-store", "--survey", str(_survey(repo, "tree1", _TWO_HALVES)))
    (repo / A).write_text("A = 2\n")
    done, files, report = _select(repo, A)
    assert done.returncode == 0, done.stderr
    assert (report["seeds"], report["dirty"]) == (2, True)


def test_a_graph_whose_tree_this_repository_does_not_have_is_passed_over(
        later: Path, tmp_path: Path) -> None:
    """The index is one file for every checkout on the machine. A graph of
    another repository has a tree id git here cannot resolve: it is not usable,
    which is exit 3 and not a failed walk."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "unrelated.py").write_text("U = 1\n")
    _git(elsewhere, "init", "-q", "-b", "main")
    _git(elsewhere, "add", "-A")
    _git(elsewhere, "commit", "-q", "-m", "another repository")
    out = tmp_path / "walk.txt"
    listing = tmp_path / "changed-elsewhere.txt"
    listing.write_text("unrelated.py\n")
    done = subprocess.run(
        [sys.executable, str(_HELPER), "--repo-root", str(elsewhere),
         "--index", str(later.parent / "logs" / "index.sqlite"), "graph-select",
         "--changed-files", str(listing), "--output", str(out)],
        capture_output=True, text=True, cwd=str(elsewhere))
    assert (done.returncode, out.exists()) == (3, False), done.stderr
    assert "graph 1: git does not have its tree" in done.stderr


def test_a_store_waits_for_another_writer_longer_than_sqlite_would(repo: Path) -> None:
    """Another process holds the index's write lock for longer than SQLite's
    own five seconds of patience (a store of a real graph takes about four).
    The store waits and lands; without that it would fail, and this helper
    turns a failure into exit 0."""
    index = repo.parent / "logs" / "index.sqlite"
    writer = sqlite3.connect(index)
    graph_rows.ensure_schema(writer)
    writer.commit()
    writer.execute("BEGIN IMMEDIATE")
    logs = repo.parent / "logs"
    storing = subprocess.Popen(
        [sys.executable, str(_HELPER), "--repo-root", str(repo), "--index", str(index),
         "--stale-log", str(logs / "stale.jsonl"), "graph-store", "--survey",
         str(_survey(repo, "tree1", _GRAPH))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(repo))
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            storing.communicate(timeout=6.5)
        writer.commit()
        _, stderr = storing.communicate(timeout=60)
    finally:
        writer.close()
        if storing.poll() is None:
            storing.kill()
    assert storing.returncode == 0 and "stored graph 1" in stderr, stderr
