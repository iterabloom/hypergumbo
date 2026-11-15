# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two recorders of ``scripts/smart-test``, executed.

WHAT THEY ARE FOR. Neither changes which tests run. Each appends to a log
that a later decision will be judged on, so the failure to guard against is a
log that fills with rows meaning something other than what they say.

THE STALE-GRAPH SHADOW runs the reverse slice a second time on an older graph
and stores both answers (``coverage-select stale-record``). What is pinned:

    the older graph gets the same seed          else the two answers differ for
                                                a reason that is not the graph
    the fresh graph is the ONE new survey       zero (a cache hit) or two (a
                                                second analysis) is no comparison,
                                                and the row says which
    no older graph, or a failed walk, is said   a missing comparison is stored
                                                as missing, never as agreement
    a dead helper changes nothing               it rides on every selection

THE RUN RECORD stores, for every run that reaches pytest, the commit, the
change, the test files that ran and the tests that failed
(``coverage-select run-record``). What is pinned:

    explicit-path runs are recorded             they are most runs while a change
                                                is being written
    the kind says how the tests were chosen     a replay must not read a hand-picked
                                                run as a selection
    the uncommitted change is stored            a tree that was never committed
                                                cannot be read back later
    tracker ops and stray files are left out    they are no part of the change

BOTH ARE OFF in CI, in a smart-test started by a test, and in target mode, and
each has its own switch.

The bash blocks are lifted verbatim out of ``scripts/smart-test`` and run
against stub helpers, as in ``test_smart_test_import_union.py``; the helper's
own subcommands are run for real against a small git repository.
"""
from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SMART_TEST = _REPO / "scripts" / "smart-test"
_HELPER = _REPO / "scripts" / "coverage-select"
_TEXT = _SMART_TEST.read_text(encoding="utf-8")
_LINES = _TEXT.splitlines()


def _function(name: str) -> str:
    start = next(i for i, ln in enumerate(_LINES) if ln == f"{name}() {{")
    end = next(i for i in range(start + 1, len(_LINES)) if _LINES[i] == "}")
    return "\n".join(_LINES[start:end + 1])


def _bash(script: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script], capture_output=True,  # noqa: S607
                          text=True, cwd=str(cwd), env=env)


# ── the stale-graph shadow ───────────────────────────────────────────────────

# The stubs log their arguments joined by "|", so a test can see where one
# argument ends: a sentence passed as one argument stays one field.
_STUB_HELPER = """#!/usr/bin/env bash
( IFS='|'; printf '%s\\n' "$*" ) >> "{calls}"
for a in "$@"; do
    if [ "$a" = stale-pick ] && [ -f "{root}/older.txt" ]; then cat "{root}/older.txt"; fi
done
exit {rc}
"""

_STUB_HG = """#!/usr/bin/env bash
( IFS='|'; printf '%s\\n' "$*" ) >> "{calls}"
prev=""
for a in "$@"; do
    [ "$prev" = --output ] && echo "packages/p/tests/test_stale.py" > "$a"
    prev="$a"
done
exit {rc}
"""


def _survey(cache: Path, state: str) -> Path:
    path = cache / "hypergumbo" / "fp" / "results" / state / "build" / "survey.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    return path


def _shadow(tmp_path: Path, *, new_surveys: int = 1, older: bool = True,
            hg_rc: int = 0, helper_rc: int = 0) -> tuple[list[str], list[str], int]:
    """Run the real ``stale_graph_shadow`` once. Returns (helper calls, slice
    calls, exit status of the function called the way smart-test calls it)."""
    cache = tmp_path / "cache"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    helper_calls, hg_calls = tmp_path / "helper.calls", tmp_path / "hg.calls"
    (scripts / "coverage-select").write_text(
        _STUB_HELPER.format(calls=helper_calls, root=tmp_path, rc=helper_rc))
    (scripts / "coverage-select").chmod(0o755)
    (tmp_path / "hg").write_text(_STUB_HG.format(calls=hg_calls, rc=hg_rc))
    (tmp_path / "hg").chmod(0o755)
    stale_before = _survey(cache, "before-the-slice")
    if older:
        (tmp_path / "older.txt").write_text(str(stale_before) + "\n")
    script = "\n".join([
        "set -euo pipefail",
        f'REPO_ROOT="{tmp_path}"', f'TOOLS_ROOT="{tmp_path}"',
        f'export XDG_CACHE_HOME="{cache}"',
        f'STABLE_HG="{tmp_path}/hg"',
        'CHANGED_FILES="packages/p/src/p/a.py\npackages/p/tests/test_new.py"',
        f'CHANGED_FILE_LIST="{tmp_path}/changed.txt"',
        'echo "packages/p/src/p/a.py" > "$CHANGED_FILE_LIST"',
        f'SLICE_OUTPUT="{tmp_path}/fresh.out"',
        'echo "packages/p/tests/test_fresh.py" > "$SLICE_OUTPUT"',
        "SLICE_DURATION_S=321",
        f'STALE_MARK="{tmp_path}/mark"',
        # Everything already in the cache is older than the mark.
        f'touch -d "1 hour ago" "{stale_before}"',
        'touch -d "30 minutes ago" "$STALE_MARK"',
        *[f'mkdir -p "{cache}/hypergumbo/fp/results/new{n}/build" && '
          f'echo "{{}}" > "{cache}/hypergumbo/fp/results/new{n}/build/survey.json"'
          for n in range(new_surveys)],
        _function("stale_graph_shadow"),
        "stale_graph_shadow || true",
        'echo "REACHED_THE_END"',
    ])
    done = _bash(script, tmp_path)
    assert "REACHED_THE_END" in done.stdout, done.stderr

    def read(path: Path) -> list[str]:
        return path.read_text().splitlines() if path.exists() else []
    return read(helper_calls), read(hg_calls), done.returncode


def _record_call(calls: list[str]) -> str:
    (record,) = [c for c in calls if "|stale-record|" in c]
    return record


def test_the_older_graph_is_walked_from_the_same_seed_and_both_answers_stored(tmp_path) -> None:
    helper, hg, _ = _shadow(tmp_path)
    older = tmp_path / "cache/hypergumbo/fp/results/before-the-slice/build/survey.json"
    fresh = tmp_path / "cache/hypergumbo/fp/results/new0/build/survey.json"
    (walk,) = hg
    assert walk.startswith(f"slice|--files|{tmp_path}/changed.txt|--input|{older}|--output|")
    assert any(c.endswith(f"stale-pick|--fresh-survey|{fresh}") for c in helper)
    record = _record_call(helper)
    assert record.startswith(f"--repo-root|{tmp_path}|stale-record|--changed-files|")
    assert f"|--fresh-out|{tmp_path}/fresh.out|" in record
    assert "|--fresh-seconds|321|" in record
    assert f"|--fresh-survey|{fresh}|" in record
    assert f"|--older-survey|{older}|--stale-out|" in record
    assert re.search(r"\|--stale-seconds\|\d+$", record)
    assert "--why" not in record


def test_a_cache_hit_is_no_comparison_and_says_so(tmp_path) -> None:
    """No survey written since the mark: the slice answered from a cached one,
    and which cached one is not known. The survey that was already in the cache
    must not be taken for the fresh graph."""
    helper, hg, _ = _shadow(tmp_path, new_surveys=0)
    assert hg == [], "nothing may be walked when the fresh graph is not identified"
    assert [c for c in helper if "stale-pick" in c] == []
    record = _record_call(helper)
    assert record.endswith("|--why|the fresh slice left 0 new surveys in the cache; exactly 1 "
                           "identifies its graph (0: it answered from a cached survey)"), (
        "the reason is one argument, whole")
    assert "--fresh-survey" not in record and "--older-survey" not in record


def test_two_new_surveys_cannot_be_told_apart(tmp_path) -> None:
    helper, hg, _ = _shadow(tmp_path, new_surveys=2)
    assert hg == []
    record = _record_call(helper)
    assert "|--why|the fresh slice left 2 new surveys in the cache" in record
    assert "--fresh-survey" not in record


def test_no_older_graph_registers_the_fresh_one_and_says_so(tmp_path) -> None:
    helper, hg, _ = _shadow(tmp_path, older=False)
    assert hg == []
    record = _record_call(helper)
    assert "|--fresh-survey|" in record, "the fresh graph must still be registered"
    assert record.endswith("|--why|no older graph from this analyzer build is registered "
                           "and still on disk")
    assert "--older-survey" not in record and "--stale-out" not in record


def test_a_failed_walk_on_the_older_graph_is_not_an_empty_answer(tmp_path) -> None:
    helper, hg, _ = _shadow(tmp_path, hg_rc=1)
    assert len(hg) == 1
    record = _record_call(helper)
    assert record.endswith("|--why|the slice on the older graph failed")
    assert "--older-survey" not in record and "--stale-out" not in record
    assert "|--fresh-survey|" in record


def test_a_dead_helper_does_not_fail_the_selection(tmp_path) -> None:
    _, _, rc = _shadow(tmp_path, helper_rc=3)
    assert rc == 0


def test_the_shadow_runs_after_a_successful_slice_and_before_the_tests_are_read() -> None:
    """Its place in the selection: only with a seed, only after the slice-failure
    exit, and with the mark made before the slice started."""
    mark = _TEXT.index("STALE_MARK=$(mktemp)")
    slice_run = _TEXT.index('$STABLE_HG slice --files "$CHANGED_FILE_LIST" ${SLICE_MINIMAL')
    failure = _TEXT.index('run_full_suite "slice command failed" "$CHANGED_FILES"')
    call = _TEXT.index('if [[ "$STALE_SHADOW" == "true" ]] && [[ -s "$CHANGED_FILE_LIST" ]]; then\n'
                       '    stale_graph_shadow || true')
    read = _TEXT.index("AFFECTED_TESTS=$(grep -E 'packages/.*/tests/")
    assert mark < slice_run < failure < call < read


# ── the switches ─────────────────────────────────────────────────────────────

def _switches(**env: str) -> dict[str, str]:
    remember = next(ln for ln in _LINES if ln.startswith("STARTED_UNDER_SMART_TEST="))
    export_at = _LINES.index("export SMART_TEST_ACTIVE=1")
    assert _LINES.index(remember) < export_at, (
        "the inherited value must be read before smart-test sets its own, or "
        "every run looks like one started by a test and nothing is ever recorded")
    start = next(i for i, ln in enumerate(_LINES)
                 if ln.startswith('if [[ -n "${CI:-}" ]] || [[ -n "$STARTED_UNDER_SMART_TEST" ]]'))
    end = next(i for i in range(start, len(_LINES)) if _LINES[i] == "fi")
    script = "\n".join([
        "set -euo pipefail", remember, "export SMART_TEST_ACTIVE=1",
        'TARGET_MODE="${TARGET_MODE:-false}"',
        "STALE_SHADOW=true", "RUN_RECORDS=true",
        *_LINES[start:end + 1],
        'echo "$STALE_SHADOW $RUN_RECORDS"',
    ])
    base = {"PATH": "/usr/bin:/bin"}
    done = _bash(script, _REPO, env={**base, **env})
    assert done.returncode == 0, done.stderr
    stale, runs = done.stdout.split()
    return {"stale": stale, "runs": runs}


def test_a_developers_run_records() -> None:
    assert _switches() == {"stale": "true", "runs": "true"}


@pytest.mark.parametrize("env", [
    {"CI": "true"}, {"SMART_TEST_ACTIVE": "1"}, {"TARGET_MODE": "true"},
])
def test_ci_a_nested_run_and_target_mode_record_nothing(env) -> None:
    assert _switches(**env) == {"stale": "false", "runs": "false"}


def test_each_recorder_has_its_own_off_switch() -> None:
    assert re.search(r"^\s*--no-stale-shadow\) STALE_SHADOW=false ;;$", _TEXT, re.MULTILINE)
    assert re.search(r"^\s*--no-run-record\) RUN_RECORDS=false ;;$", _TEXT, re.MULTILINE)
    assert _TEXT.index("STALE_SHADOW=true") < _TEXT.index("--no-stale-shadow) STALE_SHADOW=false")
    assert _TEXT.index("RUN_RECORDS=true") < _TEXT.index("--no-run-record) RUN_RECORDS=false")


# ── the run record, as run_pytest makes it ───────────────────────────────────

def _run_record_block() -> str:
    start = next(i for i, ln in enumerate(_LINES)
                 if ln.strip() == 'if [[ "${RUN_RECORDS:-false}" == "true" ]]; then')
    end = next(i for i in range(start + 1, len(_LINES)) if _LINES[i] == "        fi")
    return "\n".join(_LINES[start:end + 1])


def _record_run(tmp_path: Path, *, setup: list[str], targets: str = "t/test_a.py",
                helper_rc: int = 0) -> list[str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    calls = tmp_path / "helper.calls"
    (scripts / "coverage-select").write_text(
        _STUB_HELPER.format(calls=calls, root=tmp_path, rc=helper_rc))
    (scripts / "coverage-select").chmod(0o755)
    script = "\n".join([
        "set -euo pipefail",
        f'REPO_ROOT="{tmp_path}"', f'TOOLS_ROOT="{tmp_path}"',
        'JUNIT_XML=".ci/pytest-results.xml"',
        *setup,
        "record() {", f'local test_targets="{targets}" exit_code=1', _run_record_block(), "}",
        "record",
        'echo "REACHED_THE_END"',
    ])
    done = _bash(script, tmp_path)
    assert "REACHED_THE_END" in done.stdout, done.stderr
    return calls.read_text().splitlines() if calls.exists() else []


def test_an_explicit_run_is_recorded_as_one(tmp_path) -> None:
    (call,) = _record_run(tmp_path, setup=["RUN_RECORDS=true", "HAS_EXPLICIT_TESTS=true"])
    assert call == (f"--repo-root|{tmp_path}|--junit|.ci/pytest-results.xml|run-record|"
                    "--kind|explicit|--exit|1|--base-branch|origin/dev")


def test_a_selected_run_and_a_full_run_are_told_apart(tmp_path) -> None:
    (selected,) = _record_run(tmp_path, setup=[
        "RUN_RECORDS=true", "HAS_EXPLICIT_TESTS=false", 'FULL_SUITE_PYTEST_TARGETS="packages tests"'])
    assert "|--kind|selected|" in selected
    (tmp_path / "helper.calls").unlink()
    (tmp_path / "scripts" / "coverage-select").unlink()
    (tmp_path / "scripts").rmdir()
    (full,) = _record_run(tmp_path, targets="packages tests", setup=[
        "RUN_RECORDS=true", "HAS_EXPLICIT_TESTS=false", 'FULL_SUITE_PYTEST_TARGETS="packages tests"'])
    assert "|--kind|full|" in full


def test_the_branch_the_baseline_is_taken_from_is_passed_on(tmp_path) -> None:
    (call,) = _record_run(tmp_path, setup=["RUN_RECORDS=true", "BASE_BRANCH=origin/main"])
    assert call.endswith("|--base-branch|origin/main")


def test_nothing_is_recorded_when_the_switch_is_off_or_absent(tmp_path) -> None:
    """Absent is the state of every test that drives ``run_pytest`` alone."""
    assert _record_run(tmp_path, setup=[]) == []
    (tmp_path / "scripts" / "coverage-select").unlink()
    (tmp_path / "scripts").rmdir()
    assert _record_run(tmp_path, setup=["RUN_RECORDS=false"]) == []


def test_a_dead_helper_does_not_change_the_runs_outcome(tmp_path) -> None:
    assert len(_record_run(tmp_path, setup=["RUN_RECORDS=true"], helper_rc=3)) == 1


def test_the_record_is_made_inside_run_pytest_after_the_tests_ran() -> None:
    body = _function("run_pytest")
    ran = body.index('summarize_pytest_output "$PYTEST_LOG" "$exit_code" "$zero_run"')
    record = body.index('if [[ "${RUN_RECORDS:-false}" == "true" ]]; then')
    assert ran < record < body.rindex("return $exit_code")


# ── coverage-select's own subcommands, against a real repository ─────────────

def _git(repo: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",  # noqa: S607
         "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *argv],
        capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _helper(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    logs = repo.parent / "logs"
    return subprocess.run(
        [sys.executable, str(_HELPER), "--repo-root", str(repo),
         "--runs", str(logs / "runs.jsonl"), "--stale-log", str(logs / "stale.jsonl"),
         "--graphs", str(logs / "graphs.jsonl"), *argv],
        capture_output=True, text=True, cwd=str(repo))


def _rows(repo: Path, name: str) -> list[dict]:
    path = repo.parent / "logs" / name
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Two commits: ``base`` marks the first, the second changes ``a.py``."""
    root = tmp_path / "repo"
    (root / "packages/p/src/p").mkdir(parents=True)
    (root / "packages/p/tests").mkdir(parents=True)
    (root / ".agent/tracker/.ops").mkdir(parents=True)
    (root / "packages/p/src/p/a.py").write_text("A = 1\n")
    (root / "packages/p/src/p/b.py").write_text("B = 1\n")
    (root / "packages/p/tests/test_a.py").write_text("def test_a():\n    pass\n")
    (root / ".agent/tracker/.ops/x.ops").write_text("op 1\n")
    _git(root, "init", "-q", "-b", "work")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "one")
    _git(root, "branch", "base")
    (root / "packages/p/src/p/a.py").write_text("A = 2\n")
    _git(root, "commit", "-q", "-am", "two")
    return root


_JUNIT = """<testsuites><testsuite>
<testcase classname="packages.p.tests.test_a" name="test_a"/>
<testcase classname="packages.p.tests.test_new" name="test_new"><failure>boom</failure></testcase>
</testsuite></testsuites>
"""


def test_a_run_record_holds_the_commit_the_change_and_the_failures(repo: Path) -> None:
    (repo / "packages/p/src/p/b.py").write_text("B = 2\n")
    (repo / "packages/p/tests/test_new.py").write_text("def test_new():\n    assert 0\n")
    (repo / "junk.txt").write_text("not part of any change\n")
    (repo / ".agent/tracker/.ops/x.ops").write_text("op 1\nop 2\n")
    (repo / "junit.xml").write_text(_JUNIT)
    done = _helper(repo, "--junit", str(repo / "junit.xml"), "run-record",
                   "--kind", "explicit", "--exit", "1", "--base-branch", "base")
    assert done.returncode == 0, done.stderr
    (row,) = _rows(repo, "runs.jsonl")
    assert row["head"] == _git(repo, "rev-parse", "HEAD")
    assert row["base"] == _git(repo, "rev-parse", "base")
    assert row["kind"] == "explicit" and row["exit"] == 1
    assert row["changed_files"] == [
        "packages/p/src/p/a.py", "packages/p/src/p/b.py", "packages/p/tests/test_new.py"]
    assert row["ran_files"] == ["packages/p/tests/test_a.py", "packages/p/tests/test_new.py"]
    assert row["tests_ran"] == 2
    assert row["failed"] == ["packages/p/tests/test_new.py::test_new"]
    with gzip.open(repo.parent / "logs" / "runs.d" / f"{row['snapshot']}.patch.gz", "rt") as fh:
        patch = fh.read()
    assert "+B = 2" in patch, "the uncommitted edit"
    assert "+    assert 0" in patch, "the untracked test file"
    assert "A = 2" not in patch, "the committed change is in the commit, not the snapshot"
    assert "junk" not in patch and "op 2" not in patch


def test_a_run_on_a_committed_tree_needs_no_snapshot(repo: Path) -> None:
    done = _helper(repo, "run-record", "--kind", "selected", "--exit", "0",
                   "--base-branch", "base")
    assert done.returncode == 0, done.stderr
    (row,) = _rows(repo, "runs.jsonl")
    assert row["snapshot"] is None
    assert row["changed_files"] == ["packages/p/src/p/a.py"]
    assert row["failed"] is None and row["ran_files"] is None and row["tests_ran"] is None, (
        "no junit report was given: that is not the same as nothing failed")
    assert not (repo.parent / "logs" / "runs.d").exists()


def test_the_stale_graph_commands_register_compare_and_report(repo: Path) -> None:
    cache = repo.parent / "cache"
    first = cache / "results" / "s1" / "build" / "survey.json"
    second = cache / "results" / "s2" / "build" / "survey.json"
    for survey in (first, second):
        survey.parent.mkdir(parents=True)
        survey.write_text("{}")
    out = repo.parent / "out"
    out.mkdir()
    (out / "changed.txt").write_text("packages/p/src/p/a.py\npackages/p/tests/test_edited.py\n")
    (out / "fresh1.txt").write_text(f"{repo}/packages/p/tests/test_a.py\n")

    # The first fresh slice: nothing to compare with, and it becomes a graph.
    assert _helper(repo, "stale-pick", "--fresh-survey", str(first)).stdout == ""
    done = _helper(repo, "stale-record", "--changed-files", str(out / "changed.txt"),
                   "--fresh-out", str(out / "fresh1.txt"), "--fresh-survey", str(first),
                   "--why", "no older graph")
    assert "no comparison (no older graph)" in done.stderr
    first_head = _git(repo, "rev-parse", "HEAD")

    # One commit later the first graph is the older one.
    (repo / "packages/p/src/p/b.py").write_text("B = 3\n")
    _git(repo, "commit", "-q", "-am", "three")
    assert _helper(repo, "stale-pick", "--fresh-survey", str(second)).stdout.strip() == str(first)
    (out / "fresh2.txt").write_text(
        f"{repo}/packages/p/tests/test_a.py\n{repo}/packages/p/tests/test_edited.py\n"
        f"{repo}/packages/p/tests/test_lost.py\n{repo}/packages/p/src/p/a.py\n")
    (out / "stale2.txt").write_text(f"{repo}/packages/p/tests/test_a.py\n")
    done = _helper(repo, "stale-record", "--changed-files", str(out / "changed.txt"),
                   "--fresh-out", str(out / "fresh2.txt"), "--fresh-survey", str(second),
                   "--older-survey", str(first), "--stale-out", str(out / "stale2.txt"),
                   "--fresh-seconds", "300", "--stale-seconds", "5")
    assert "STALE GRAPH LOST 1 TEST FILE(S)" in done.stderr
    assert "packages/p/tests/test_lost.py" in done.stderr

    blind, compared = _rows(repo, "stale.jsonl")
    assert blind["stale"] is None and blind["why_no_comparison"] == "no older graph"
    assert compared["changed_source"] == ["packages/p/src/p/a.py"]
    assert compared["fresh_only"] == ["packages/p/tests/test_edited.py", "packages/p/tests/test_lost.py"]
    assert compared["fresh_only_unedited"] == ["packages/p/tests/test_lost.py"]
    assert compared["graph"]["survey"] == str(first)
    assert compared["graph"]["head"] == first_head
    assert compared["graph"]["age_commits"] == 1
    assert compared["graph"]["dirty"] is False
    assert [g["survey"] for g in _rows(repo, "graphs.jsonl")] == [str(first), str(second)]

    report = _helper(repo, "stale-report").stdout
    assert "compared            : 1" in report and "DISQUALIFIED" in report


def test_an_agreeing_older_graph_is_reported_with_its_age(repo: Path) -> None:
    cache = repo.parent / "cache"
    surveys = [cache / "results" / s / "build" / "survey.json" for s in ("s1", "s2")]
    for survey in surveys:
        survey.parent.mkdir(parents=True)
        survey.write_text("{}")
    answer = repo.parent / "answer.txt"
    answer.write_text(f"{repo}/packages/p/tests/test_a.py\n")
    _helper(repo, "stale-record", "--fresh-out", str(answer),
            "--fresh-survey", str(surveys[0]), "--why", "no older graph")
    done = _helper(repo, "stale-record", "--fresh-out", str(answer),
                   "--fresh-survey", str(surveys[1]), "--older-survey", str(surveys[0]),
                   "--stale-out", str(answer))
    assert "a graph 0 commit(s) old named every unedited test file" in done.stderr
    assert _rows(repo, "stale.jsonl")[1]["fresh_only_unedited"] == []
