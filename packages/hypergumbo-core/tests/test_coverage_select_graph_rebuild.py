# SPDX-License-Identifier: AGPL-3.0-or-later
"""The background rebuild of the baseline commit's graph, and its manners.

smart-test selects from a stored graph by seeding every file that differs
between the graph's tree and the current one. A graph of the branch's BASELINE
commit differs from the branch by the branch's own change only, so it answers
exactly. ``coverage-select graph-rebuild`` makes that graph: a checkout of the
commit of its own, the installed analyzer, the rows stored under the commit.
``scripts/smart-test`` starts it for after a run has ended and stops it when a
run starts. What is pinned:

    it is the COMMIT that is analysed          not the working tree, whose
                                               edits nobody will be on again
    it does nothing when the graph is stored   every run asks for it
    one at a time, never beside a test run,    it takes 4 GB and a core for
    never short of memory, and SIGTERM ends it five minutes
    it leaves nothing behind                   a checkout and a 350 MB survey
                                               per run would fill the disk
    a failed or ambiguous analysis stores      a graph must come from one
    nothing and says why                       identified survey
    smart-test's child does not hold its lock  or the next run is refused for
                                               as long as the analysis takes

The helper is run for real against a small git repository, with a stand-in for
the installed hypergumbo; smart-test's two functions are lifted out of the
script and run as bash.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hypergumbo_core import graph_rows

_REPO = Path(__file__).resolve().parents[3]
_HELPER = _REPO / "scripts" / "coverage-select"
_SMART_TEST = _REPO / "scripts" / "smart-test"
_TEXT = _SMART_TEST.read_text(encoding="utf-8")
_LINES = _TEXT.splitlines()

A = "packages/p/src/p/a.py"
_SURVEY = {
    "nodes": [{"id": "a", "path": A}, {"id": "ta", "path": "packages/p/tests/test_a.py"}],
    "edges": [{"src": "ta", "dst": "a"}],
}

# The installed hypergumbo: records where and how it was run and what a.py
# held there, then leaves {surveys} survey(s) in the cache it was given.
_STUB_HG = """#!/usr/bin/env bash
echo "$2:${{PYTHONPATH-unset}}" >> "{calls}.pythonpath"
if [ "$1" = slice ] && [ "$2" = --help ]; then echo "  --minimal   skip the side outputs"; exit 0; fi
echo "$PWD|$*" >> "{calls}"
cat packages/p/src/p/a.py > "{calls}.saw" 2>/dev/null
{sleep}
for n in $(seq 1 {surveys}); do
    d="$XDG_CACHE_HOME/hypergumbo/fp/results/state$n/buildX"
    mkdir -p "$d" && cp "{survey}" "$d/survey.json"
done
exit {rc}
"""


def _git(root: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",  # noqa: S607
         "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
        cwd=str(root), capture_output=True, text=True, check=True)
    return done.stdout.strip()


class Bench:
    """A repository with one commit, an edit that is NOT committed, and a
    stand-in hypergumbo."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.repo = tmp_path / "repo"
        (self.repo / "packages/p/src/p").mkdir(parents=True)
        (self.repo / A).write_text("A = 'committed'\n")
        # Sorted before every package path: the seed must be a source file,
        # not the first file of the tree.
        (self.repo / "README.md").write_text("read me\n")
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "one")
        self.commit = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / A).write_text("A = 'edited, not committed'\n")
        self.work = tmp_path / "rebuild"
        self.index = tmp_path / "index.sqlite"
        self.calls = tmp_path / "hg.calls"
        self.survey = tmp_path / "survey-template.json"
        self.survey.write_text(json.dumps(_SURVEY))
        self.hg = tmp_path / "hg"
        self.install()

    def install(self, *, rc: int = 0, surveys: int = 1, sleep: str = "") -> None:
        self.hg.write_text(_STUB_HG.format(
            calls=self.calls, survey=self.survey, rc=rc, surveys=surveys, sleep=sleep))
        self.hg.chmod(0o755)

    def argv(self, *extra: str) -> list[str]:
        return [sys.executable, str(_HELPER), "--repo-root", str(self.repo),
                "--index", str(self.index), "graph-rebuild", "--commit", self.commit,
                "--hg", str(self.hg), "--work-dir", str(self.work), "--settle", "0",
                "--min-free-mb", "0", *extra]

    def rebuild(self, *extra: str) -> subprocess.CompletedProcess[str]:
        # A rebuild that waits for something that never comes must fail the
        # test, not hang the suite. PYTHONPATH is set the way a run in a git
        # worktree sets it: the rebuild must not hand it to the analyzer.
        return subprocess.run(self.argv(*extra), capture_output=True, text=True,
                              cwd=str(self.repo), timeout=120,
                              env={**os.environ, "PYTHONPATH": "/a/worktree/packages/x/src"})

    def graphs(self) -> list[graph_rows.StoredGraph]:
        if not self.index.exists():
            return []
        conn = sqlite3.connect(self.index)
        try:
            graph_rows.ensure_schema(conn)
            return graph_rows.stored_graphs(conn)
        finally:
            conn.close()

    def hg_calls(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def left_behind(self) -> list[str]:
        """What a finished rebuild must not leave: its checkout, its cache, its
        pid, a registered worktree."""
        found = [name for name in ("tree", "cache", "rebuild.pid") if (self.work / name).exists()]
        worktrees = _git(self.repo, "worktree", "list").splitlines()
        return found + [f"worktree {line}" for line in worktrees[1:]]

    def log(self) -> str:
        return (self.work / "rebuild.log").read_text()


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


def test_the_commit_is_analysed_in_a_checkout_of_its_own_and_its_graph_stored(bench: Bench) -> None:
    done = bench.rebuild()
    assert done.returncode == 0, done.stderr
    assert f"stored graph 1 of {bench.commit[:9]} (2 nodes, 1 edges, build buildX)" in done.stderr

    (call,) = bench.hg_calls()
    where, command = call.split("|", 1)
    assert where == str(bench.work / "tree"), "run inside the checkout, not the working tree"
    assert command == (f"slice --files {bench.work}/seed.txt --minimal "
                       f"--output {bench.work}/slice.out")
    assert (bench.work / "seed.txt").read_text() == f"{A}\n"
    assert Path(f"{bench.calls}.saw").read_text() == "A = 'committed'\n", (
        "the analysis must see the commit, not the edit in the working tree")

    (graph,) = bench.graphs()
    assert (graph.head, graph.dirty_files, graph.build, graph.tree) == (
        bench.commit, (), "buildX", "state1")
    assert graph.git_tree == _git(bench.repo, "rev-parse", f"{bench.commit}^{{tree}}")
    assert bench.left_behind() == []
    assert (bench.repo / A).read_text() == "A = 'edited, not committed'\n"
    assert f"stored graph 1 of {bench.commit[:9]}" in bench.log()
    # The installed hypergumbo ran its own code both times it was called: the
    # caller's PYTHONPATH (a worktree's sources) did not reach it.
    assert Path(f"{bench.calls}.pythonpath").read_text().splitlines() == [
        "--help:unset", "--files:unset"]


def test_a_graph_that_is_stored_is_not_made_again(bench: Bench) -> None:
    assert bench.rebuild().returncode == 0
    again = bench.rebuild()
    assert again.returncode == 0
    assert f"the graph of {bench.commit[:9]} is stored already (graph 1)" in again.stderr
    assert len(bench.hg_calls()) == 1
    # Asked for the installed build by name: the same build counts, another does not.
    assert bench.rebuild("--build", "buildX").returncode == 0
    assert len(bench.hg_calls()) == 1
    other = bench.rebuild("--build", "another-build")
    assert "stored graph" in other.stderr and len(bench.hg_calls()) == 2


def test_a_graph_of_the_commit_with_uncommitted_files_is_not_the_commits_graph(bench: Bench) -> None:
    """A graph made from the edited working tree has the commit's tree id and
    is not of the commit: the rebuild is still needed."""
    survey = bench.tmp / "cache" / "results" / "dirty-state" / "buildX" / "survey.json"
    survey.parent.mkdir(parents=True)
    survey.write_text(json.dumps(_SURVEY))
    stored = subprocess.run(
        [sys.executable, str(_HELPER), "--repo-root", str(bench.repo), "--index",
         str(bench.index), "graph-store", "--survey", str(survey)],
        capture_output=True, text=True, cwd=str(bench.repo))
    assert "and 1 file(s) not in that commit" in stored.stderr
    done = bench.rebuild()
    assert done.returncode == 0 and len(bench.hg_calls()) == 1
    assert [g.dirty_files for g in bench.graphs()] == [(), (A,)]


def test_only_one_rebuild_runs_at_a_time(bench: Bench) -> None:
    bench.work.mkdir()
    holder = os.open(bench.work / "rebuild.lock", os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        done = bench.rebuild()
    finally:
        os.close(holder)
    assert done.returncode == 2 and "another rebuild is running" in done.stderr
    assert bench.hg_calls() == [] and bench.graphs() == []


def test_it_does_not_start_while_a_test_run_has_the_checkout(bench: Bench) -> None:
    (bench.repo / ".ci").mkdir()
    (bench.repo / ".ci" / ".smart-test.lock.pid").write_text(f"{os.getpid()}\n")
    done = bench.rebuild()
    assert done.returncode == 2
    assert f"not started: a test run (pid {os.getpid()}) has the checkout" in done.stderr
    assert bench.hg_calls() == [] and bench.left_behind() == []

    # A pid file whose process is gone is a run that ended without tidying up.
    gone = subprocess.Popen(["true"])  # noqa: S607
    gone.wait()
    (bench.repo / ".ci" / ".smart-test.lock.pid").write_text(f"{gone.pid}\n")
    assert bench.rebuild().returncode == 0


def test_it_does_not_start_short_of_memory(bench: Bench) -> None:
    done = bench.rebuild("--min-free-mb", str(10**9))
    assert done.returncode == 2
    assert "MB of memory free, 1000000000 needed" in done.stderr
    assert bench.hg_calls() == [] and bench.graphs() == []


@pytest.mark.parametrize(("setup", "said"), [
    ({"rc": 3}, "failed (exit 3)"),
    ({"surveys": 0}, "left 0 surveys; exactly 1 is its graph"),
    ({"surveys": 2}, "left 2 surveys; exactly 1 is its graph"),
])
def test_an_analysis_that_fails_or_leaves_no_single_survey_stores_nothing(
        bench: Bench, setup: dict, said: str) -> None:
    bench.install(**setup)
    done = bench.rebuild()
    assert done.returncode == 1 and said in done.stderr
    assert bench.graphs() == [] and bench.left_behind() == []
    assert said in bench.log()


def test_a_survey_with_no_graph_and_an_unknown_commit_store_nothing(bench: Bench) -> None:
    bench.survey.write_text("{}")
    done = bench.rebuild()
    assert done.returncode == 1 and "holds no node and edge lists" in done.stderr
    assert bench.graphs() == [] and bench.left_behind() == []

    unknown = subprocess.run(
        [*bench.argv()[:7], "--commit", "0" * 40, *bench.argv()[9:]],
        capture_output=True, text=True, cwd=str(bench.repo))
    assert unknown.returncode == 1
    assert f"git does not know the commit {'0' * 40}" in unknown.stderr


def test_a_failure_nobody_foresaw_is_logged_and_is_exit_1(bench: Bench) -> None:
    """The helper's other commands turn any exception into exit 0, because they
    ride on a test run. A rebuild that stored nothing must not look like one
    that did: here the index path is a directory."""
    bench.index.mkdir()
    done = bench.rebuild()
    assert done.returncode == 1, done.stderr
    assert "graph-rebuild: failed: " in done.stderr and "failed: " in bench.log()
    assert bench.left_behind() == []


def test_what_a_dead_rebuild_left_is_cleared_before_the_next(bench: Bench) -> None:
    """A rebuild killed without warning leaves its checkout registered and a
    survey in its cache. The next one must not count that survey as its own."""
    bench.work.mkdir()
    _git(bench.repo, "worktree", "add", "--detach", "--quiet", str(bench.work / "tree"), "HEAD")
    stale = bench.work / "cache" / "hypergumbo" / "fp" / "results" / "old" / "buildX"
    stale.mkdir(parents=True)
    (stale / "survey.json").write_text(json.dumps(_SURVEY))
    done = bench.rebuild()
    assert done.returncode == 0, done.stderr
    assert [g.tree for g in bench.graphs()] == ["state1"]
    assert bench.left_behind() == []


def test_another_worktree_whose_directory_is_missing_keeps_its_registration(bench: Bench) -> None:
    """The repository has worktrees that are not the rebuild's. One whose
    directory is away for the moment (a disk not mounted) must still be
    registered afterwards: the rebuild does not prune. And its own checkout,
    registered by a dead run whose directory is gone, does not stop it."""
    theirs = bench.tmp / "someone-elses-worktree"
    _git(bench.repo, "worktree", "add", "--detach", "--quiet", str(theirs), "HEAD")
    theirs.rename(bench.tmp / "away")
    bench.work.mkdir()
    _git(bench.repo, "worktree", "add", "--detach", "--quiet", str(bench.work / "tree"), "HEAD")
    (bench.work / "tree").rename(bench.tmp / "gone")
    done = bench.rebuild()
    assert done.returncode == 0, done.stderr
    assert str(theirs) in _git(bench.repo, "worktree", "list")
    assert len(bench.graphs()) == 1


def _wait_for(condition, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.05)


def test_sigterm_ends_the_analysis_and_removes_the_checkout(bench: Bench) -> None:
    bench.install(sleep="sleep 60")
    running = subprocess.Popen(bench.argv(), stderr=subprocess.PIPE, text=True,
                               cwd=str(bench.repo), start_new_session=True)
    try:
        _wait_for(lambda: bench.hg_calls() != [])
        assert (bench.work / "rebuild.pid").read_text().strip() == str(running.pid)
        assert (bench.work / "tree").is_dir()
        os.killpg(running.pid, signal.SIGTERM)
        _, stderr = running.communicate(timeout=20)
    finally:
        if running.poll() is None:
            os.killpg(running.pid, signal.SIGKILL)
    assert running.returncode == 2
    assert "stopped after" in stderr and "a test run has the machine" in stderr
    assert bench.graphs() == [] and bench.left_behind() == []


def test_it_waits_for_the_run_that_started_it_to_end(bench: Bench) -> None:
    """The starter here is this test's own child and is not collected until
    the rebuild is done: it ends, then lingers as a zombie. A zombie still
    answers a signal, and must not be waited for."""
    starter = subprocess.Popen(["sleep", "1.5"])  # noqa: S607
    began = time.monotonic()
    done = bench.rebuild("--after-pid", str(starter.pid))
    waited = time.monotonic() - began
    starter.wait()
    assert done.returncode == 0, done.stderr
    assert waited >= 1.4, "the analysis must not start beside its starter"
    assert waited < 15, "a starter that has ended and is not yet collected is over"


# ── smart-test's side ────────────────────────────────────────────────────────

def _function(name: str) -> str:
    start = next(i for i, ln in enumerate(_LINES) if ln == f"{name}() {{")
    end = next(i for i in range(start + 1, len(_LINES)) if _LINES[i] == "}")
    return "\n".join(_LINES[start:end + 1])


def _bash(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,  # noqa: S607
                          cwd=str(cwd))


def _stand_in(name: str) -> subprocess.Popen[bytes]:
    """A process in a group of its own whose command line starts with ``name``."""
    return subprocess.Popen(["bash", "-c", f"exec -a '{name}' sleep 60"],  # noqa: S607
                            start_new_session=True)


def _stop(tmp_path: Path, pid_text: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "rebuild.pid").write_text(pid_text)
    return _bash("\n".join([
        "set -euo pipefail", f'GRAPH_REBUILD_DIR="{tmp_path}"',
        _function("stop_graph_rebuild"), "stop_graph_rebuild", "echo REACHED"]), tmp_path)


def test_a_run_that_starts_stops_the_rebuild_and_only_the_rebuild(tmp_path: Path) -> None:
    rebuild = _stand_in("coverage-select graph-rebuild")
    bystander = _stand_in("something else")
    try:
        done = _stop(tmp_path, f"{rebuild.pid}\n")
        assert "REACHED" in done.stdout
        assert f"stopped a graph rebuild (pid {rebuild.pid})" in done.stderr
        assert rebuild.wait(timeout=10) == -signal.SIGTERM

        # A pid file that names some other process: its number was reused.
        done = _stop(tmp_path, f"{bystander.pid}\n")
        assert "REACHED" in done.stdout and "stopped" not in done.stderr
        time.sleep(0.2)
        assert bystander.poll() is None, "a process that is no rebuild must be left alone"
    finally:
        for proc in (rebuild, bystander):
            if proc.poll() is None:
                proc.kill()
            proc.wait()


@pytest.mark.parametrize("pid_text", ["", "not a number\n", "999999999\n"])
def test_a_pid_file_that_names_no_live_rebuild_stops_nothing(tmp_path: Path, pid_text: str) -> None:
    done = _stop(tmp_path, pid_text)
    assert "REACHED" in done.stdout and done.stderr == ""


# The helper smart-test starts: it says how it was called, whether it leads a
# process group of its own, and whether it inherited the lock's descriptor.
_STUB_HELPER = """#!/usr/bin/env bash
{{
    ( IFS='|'; printf '%s\\n' "$*" )
    [ "$(ps -o pgid= -p $$ | tr -d ' ')" = "$$" ] && echo "leads-its-group" || echo "in-the-callers-group"
    [ -e /proc/$$/fd/9 ] && echo "holds-fd-9" || echo "fd-9-closed"
}} > "{out}"
"""


def _start(tmp_path: Path, *, use: str = "true", build: str = "0123456789abcdef",
           installed: bool = True) -> tuple[list[str] | None, str, str]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "f.txt").write_text("1\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "f.txt").write_text("2\n")
    _git(repo, "commit", "-q", "-am", "mine")
    out = tmp_path / "helper.out"
    (repo / "scripts" / "coverage-select").write_text(_STUB_HELPER.format(out=out))
    (repo / "scripts" / "coverage-select").chmod(0o755)
    interpreter = tmp_path / "python"
    interpreter.write_text(f'#!/usr/bin/env bash\necho "{build}"\n')
    interpreter.chmod(0o755)
    hg = tmp_path / "hg"
    if installed:
        hg.write_text(f"#!{interpreter}\n")
        hg.chmod(0o755)
    done = _bash("\n".join([
        "set -euo pipefail",
        f'REPO_ROOT="{repo}"', f'TOOLS_ROOT="{repo}"', f'USE_STORED_GRAPH={use}',
        f'STABLE_HG="{hg if installed else ""}"', f'HOME="{tmp_path}/no-home"',
        "BASE_BRANCH=main", f'GRAPH_REBUILD_DIR="{tmp_path}/rebuild"',
        f'exec 9>"{tmp_path}/the.lock"',
        _function("stable_analyzer_build"), _function("start_graph_rebuild"),
        "start_graph_rebuild", 'echo "PARENT=$$"',
        # Give the background child time to write, as the real run's tests do.
        "sleep 1",
    ]), repo)
    assert done.returncode == 0, done.stderr
    parent = done.stdout.split("PARENT=")[1].split()[0]
    return (out.read_text().splitlines() if out.exists() else None), base, parent


def test_a_run_starts_a_rebuild_of_its_baseline_that_holds_no_lock(tmp_path: Path) -> None:
    said, base, parent = _start(tmp_path)
    assert said is not None, "the helper was not started"
    call, group, descriptor = said
    assert call == (f"--repo-root|{tmp_path}/repo|graph-rebuild|--commit|{base}|--hg|{tmp_path}/hg"
                    f"|--build|0123456789abcdef|--after-pid|{parent}|--work-dir|{tmp_path}/rebuild"), (
        "the BASELINE (the merge-base with the base branch), not the branch's own commit")
    assert group == "leads-its-group", "smart-test stops it by its process group"
    assert descriptor == "fd-9-closed", (
        "a child holding the lock's descriptor holds the lock for as long as it runs")


@pytest.mark.parametrize("setup", [
    {"use": "false"}, {"installed": False}, {"build": "no build name"}])
def test_no_rebuild_without_stored_graph_selection_an_installed_hypergumbo_or_its_build(
        tmp_path: Path, setup: dict) -> None:
    said, _, _ = _start(tmp_path, **setup)
    assert said is None


def test_where_smart_test_stops_and_starts_the_rebuild() -> None:
    """Stopped as soon as the run holds the lock; started on both paths that
    reach pytest or the manifest, each time before the tests; and every
    function is defined above its first call (bash reads top to bottom)."""
    lock_pid = _TEXT.index('echo $$ > "$LOCKFILE.pid"')
    stop_def = _TEXT.index("stop_graph_rebuild() {")
    stop_call = _TEXT.index("\nstop_graph_rebuild\n")
    start_def = _TEXT.index("start_graph_rebuild() {")
    build_def = _TEXT.index("stable_analyzer_build() {")
    explicit = _TEXT.index('    start_graph_rebuild\n    run_pytest "" "${PYTEST_ARGS[@]}"')
    selecting = _TEXT.index('log "Using stable hypergumbo: $STABLE_HG"\nstart_graph_rebuild\n')
    assert lock_pid < stop_def < stop_call < build_def < start_def < explicit < selecting
    assert _TEXT.count("start_graph_rebuild\n") == 2, "the two calls, and no third"
