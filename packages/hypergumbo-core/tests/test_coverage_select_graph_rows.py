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
         "--graphs", str(logs / "graphs.jsonl"), *argv],
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
    assert [g.dirty_files for g in graph_rows.stored_graphs(conn)] == [
        ("packages/p/src/p/a.py", "packages/p/src/p/new.py"), ()]
    conn.close()
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
