# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two recorders of ``scripts/smart-test``, executed.

WHAT THEY ARE FOR. Neither changes which tests run. Each appends to a log
that a later decision will be judged on, so the failure to guard against is a
log that fills with rows meaning something other than what they say.

THE STALE-GRAPH SHADOW runs after a FRESH slice and stores what the fresh
walk named beside what the graphs stored before it would have selected
(``coverage-select stale-record``, which also keeps the fresh graph as rows).
What is pinned:

    the comparison is the stored graph's own    it is the selection smart-test
    selection, made by the helper               acts on when it does not analyse
    the fresh graph is the ONE new survey       zero (a cache hit) or two (a
                                                second analysis) is no comparison,
                                                and the row says which
    no stored graph, or a refused one, is said  a missing comparison is stored
                                                as missing, never as agreement
    a dead helper changes nothing               it rides on every selection
    it does not run when the stored graph       there is no fresh answer to
    answered                                    compare with

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
exit {rc}
"""


def _survey(cache: Path, state: str) -> Path:
    path = cache / "hypergumbo" / "fp" / "results" / state / "build" / "survey.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    return path


def _shadow(tmp_path: Path, *, new_surveys: int = 1, helper_rc: int = 0) -> tuple[list[str], int]:
    """Run the real ``stale_graph_shadow`` once. Returns (helper calls, exit
    status of the function called the way smart-test calls it)."""
    cache = tmp_path / "cache"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    helper_calls = tmp_path / "helper.calls"
    (scripts / "coverage-select").write_text(_STUB_HELPER.format(calls=helper_calls, rc=helper_rc))
    (scripts / "coverage-select").chmod(0o755)
    stale_before = _survey(cache, "before-the-slice")
    script = "\n".join([
        "set -euo pipefail",
        f'REPO_ROOT="{tmp_path}"', f'TOOLS_ROOT="{tmp_path}"',
        f'export XDG_CACHE_HOME="{cache}"',
        'CHANGED_FILES="packages/p/src/p/a.py\npackages/p/tests/test_new.py"',
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
    calls = helper_calls.read_text().splitlines() if helper_calls.exists() else []
    return calls, done.returncode


def test_the_fresh_answer_and_its_survey_go_to_the_helper_which_compares(tmp_path) -> None:
    helper, _ = _shadow(tmp_path)
    fresh = tmp_path / "cache/hypergumbo/fp/results/new0/build/survey.json"
    assert len(helper) == 1, "one call: the helper makes the stored graph's selection itself"
    record = helper[0]
    assert record.startswith(f"--repo-root|{tmp_path}|stale-record|--changed-files|")
    assert f"|--fresh-out|{tmp_path}/fresh.out|" in record
    assert "|--fresh-seconds|321|" in record
    assert record.endswith(f"|--fresh-survey|{fresh}")
    assert "--why" not in record


def test_a_cache_hit_is_no_comparison_and_says_so(tmp_path) -> None:
    """No survey written since the mark: the slice answered from a cached one,
    and which cached one is not known. The survey that was already in the cache
    must not be taken for the fresh graph."""
    helper, _ = _shadow(tmp_path, new_surveys=0)
    (record,) = helper
    assert record.endswith("|--why|the fresh slice left 0 new surveys in the cache; exactly 1 "
                           "identifies its graph (0: it answered from a cached survey)"), (
        "the reason is one argument, whole")
    assert "--fresh-survey" not in record


def test_two_new_surveys_cannot_be_told_apart(tmp_path) -> None:
    helper, _ = _shadow(tmp_path, new_surveys=2)
    (record,) = helper
    assert "|--why|the fresh slice left 2 new surveys in the cache" in record
    assert "--fresh-survey" not in record


def test_a_dead_helper_does_not_fail_the_selection(tmp_path) -> None:
    _, rc = _shadow(tmp_path, helper_rc=3)
    assert rc == 0


def test_the_shadow_runs_after_a_fresh_slice_only_and_before_the_tests_are_read() -> None:
    """Its place in the selection: only with a seed, only when the slice ran
    (not when the stored graph answered), only after the slice-failure exit,
    and with the mark made before the slice started."""
    mark = _TEXT.index("STALE_MARK=$(mktemp)")
    slice_run = _TEXT.index('$STABLE_HG slice --files "$CHANGED_FILE_LIST" ${SLICE_MINIMAL')
    failure = _TEXT.index('run_full_suite "slice command failed" "$CHANGED_FILES"')
    call = _TEXT.index('if [[ "$STALE_SHADOW" == "true" ]] && [[ "$GRAPH_ANSWERED" != "true" ]] '
                       '&& [[ -s "$CHANGED_FILE_LIST" ]]; then\n    stale_graph_shadow || true')
    read = _TEXT.index("AFFECTED_TESTS=$(grep -E 'packages/.*/tests/")
    assert mark < slice_run < failure < call < read


# ── the stored-graph selection ───────────────────────────────────────────────

# graph-select: logs its arguments and keeps a copy of the change list it was
# handed. Whatever its exit code it writes an answer where --output points and
# prints its line: a helper that got that far and THEN failed is the case the
# exit code exists for.
_STUB_SELECT = """#!/usr/bin/env bash
( IFS='|'; printf '%s\\n' "$*" ) >> "{calls}"
prev=""
for a in "$@"; do
    if [ "$prev" = --output ]; then echo "packages/p/tests/test_stored.py" > "$a"; fi
    if [ "$prev" = --changed-files ]; then cat "$a" > "{calls}.changed"; fi
    prev="$a"
done
[ -n "{line}" ] && echo "{line}"
[ "{rc}" = 0 ] || echo "[coverage-select] graph-select: {reason}" >&2
exit {rc}
"""

# The installed hypergumbo: a script whose first line names its interpreter.
# The interpreter here is a stand-in that prints a build name for any -c.
_STUB_INTERPRETER = """#!/usr/bin/env bash
echo "interpreter:${{PYTHONPATH-unset}}" >> "{seen}"
echo "{build}"
"""

_STUB_SLICE = """#!{interpreter}
"""


def _selection(tmp_path: Path, *, use: str = "true", select_rc: int = 0,
               build: str = "0123456789abcdef", source_changed: bool = True,
               line: str = "stored graph 3 of abc123def, 2 commit(s) old: 1 seed file(s)",
               ) -> dict[str, object]:
    """Run smart-test's own lines from the stored-graph selection to the end of
    the slice, with stand-ins for the helper and the installed hypergumbo."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    calls, slices = tmp_path / "select.calls", tmp_path / "slice.calls"
    (scripts / "coverage-select").write_text(_STUB_SELECT.format(
        calls=calls, rc=select_rc, line=line, reason="an added file needs a fresh analysis"))
    (scripts / "coverage-select").chmod(0o755)
    interpreter = tmp_path / "python"
    seen = tmp_path / "pythonpath.seen"
    interpreter.write_text(_STUB_INTERPRETER.format(build=build, seen=seen))
    interpreter.chmod(0o755)
    # As the command it is "run" by bash below (a function shadows it), so only
    # its first line is read: the interpreter the build name is asked from.
    hg = tmp_path / "hg"
    hg.write_text(_STUB_SLICE.format(interpreter=interpreter))
    hg.chmod(0o755)
    start = next(i for i, ln in enumerate(_LINES) if ln == "# === STORED-GRAPH SELECTION ===")
    end = next(i for i in range(start, len(_LINES))
               if _LINES[i] == "SLICE_DURATION_S=$((SECONDS - SLICE_START_S))")
    script = "\n".join([
        "set -euo pipefail",
        f'REPO_ROOT="{tmp_path}"', f'TOOLS_ROOT="{tmp_path}"',
        f'USE_STORED_GRAPH={use}',
        f'STABLE_HG="{hg}"',
        # The slice itself: recorded, and it writes a fresh answer.
        f'run_slice() {{ echo "$*" >> "{slices}"; echo "slice:${{PYTHONPATH-unset}}" >> "{seen}"; '
        'echo "packages/p/tests/test_fresh.py" > "$SLICE_OUTPUT"; }',
        # As a run in a git worktree has it: pinned to the worktree's sources.
        'export PYTHONPATH="/a/worktree/packages/x/src"',
        'CHANGED_FILES="packages/p/src/p/a.py\ndocs/notes.md"',
        f'CHANGED_FILE_LIST="{tmp_path}/changed.txt"',
        ('echo "packages/p/src/p/a.py" > "$CHANGED_FILE_LIST"' if source_changed
         else ': > "$CHANGED_FILE_LIST"'),
        f'SLICE_OUTPUT="{tmp_path}/slice.out"', f'SLICE_STDERR="{tmp_path}/slice.err"',
        ': > "$SLICE_OUTPUT"', "SLICE_MINIMAL=()", "SLICE_START_S=$SECONDS",
        _function("stable_analyzer_build"),
        *[ln.replace('$STABLE_HG slice --files "$CHANGED_FILE_LIST"',
                     'run_slice --files "$CHANGED_FILE_LIST"') for ln in _LINES[start:end + 1]],
        'echo "ANSWERED=$GRAPH_ANSWERED"', 'echo "LINE=$GRAPH_LINE"',
        'echo "OUTPUT=$(tr "\n" " " < "$SLICE_OUTPUT")"',
    ])
    done = _bash(script, tmp_path)
    assert done.returncode == 0, done.stderr
    said = dict(ln.split("=", 1) for ln in done.stdout.splitlines() if "=" in ln)
    return {
        "answered": said["ANSWERED"], "line": said["LINE"], "output": said["OUTPUT"].split(),
        "select": calls.read_text().splitlines() if calls.exists() else [],
        "seeded": (Path(f"{calls}.changed").read_text().splitlines()
                   if Path(f"{calls}.changed").exists() else None),
        "sliced": slices.exists(), "stderr": done.stderr,
        "pythonpath": seen.read_text().splitlines() if seen.exists() else [],
    }


def test_the_stored_graph_answers_and_the_repository_is_not_analysed(tmp_path) -> None:
    ran = _selection(tmp_path)
    assert ran["answered"] == "true" and not ran["sliced"]
    assert ran["output"] == ["packages/p/tests/test_stored.py"]
    assert ran["line"] == "stored graph 3 of abc123def, 2 commit(s) old: 1 seed file(s)"
    (call,) = ran["select"]
    assert call.startswith(f"--repo-root|{tmp_path}|graph-select|--changed-files|")
    assert call.endswith(f"|--output|{tmp_path}/slice.out|--build|0123456789abcdef"), (
        "only from the installed analyzer's build")
    assert ran["seeded"] == ["packages/p/src/p/a.py", "docs/notes.md"], (
        "every changed file, not the package source the slice is limited to")
    assert "selecting from stored graph 3 of abc123def" in ran["stderr"]


@pytest.mark.parametrize("refusal", [3, 4, 5, 6])
def test_a_refused_stored_graph_is_a_fresh_analysis_and_never_an_empty_selection(
        tmp_path, refusal: int) -> None:
    ran = _selection(tmp_path, select_rc=refusal)
    assert ran["answered"] == "false" and ran["sliced"]
    assert ran["output"] == ["packages/p/tests/test_fresh.py"]
    assert ran["line"] == "", "a manifest must not name a graph that did not answer"
    assert (f"stored graph not used (graph-select exit {refusal}): [coverage-select] "
            "graph-select: an added file needs a fresh analysis; analysing") in ran["stderr"]


def test_the_installed_hypergumbo_never_inherits_the_runs_pythonpath(tmp_path) -> None:
    """A worktree run pins PYTHONPATH to its own sources. Handed on, it makes
    the installed hypergumbo import the code under test: the build name is then
    another build's, no stored graph matches, and the "stable" slice is not."""
    answered = _selection(tmp_path / "answered")
    assert answered["pythonpath"] == ["interpreter:"], "the build name was asked with it set"
    refused = _selection(tmp_path / "refused", select_rc=3)
    assert refused["pythonpath"] == ["interpreter:", "slice:"], "the slice ran with it set"
    # The probe for --minimal is the third call of the installed command.
    assert "if PYTHONPATH= $STABLE_HG slice --help 2>/dev/null | grep -q -- '--minimal'; then" in _TEXT


def test_an_answer_with_no_line_about_its_graph_is_not_taken(tmp_path) -> None:
    """Exit 0 and nothing on stdout is not what graph-select does: a stand-in
    or a broken helper. The slice runs."""
    ran = _selection(tmp_path, line="")
    assert ran["answered"] == "false" and ran["sliced"]
    assert ran["output"] == ["packages/p/tests/test_fresh.py"]


def test_fresh_graph_analyses_and_asks_no_stored_graph(tmp_path) -> None:
    off = _selection(tmp_path, use="false")
    assert off["answered"] == "false" and off["sliced"] and off["select"] == []


def test_a_build_name_that_is_not_one_means_no_stored_graph(tmp_path) -> None:
    ran = _selection(tmp_path, build="Traceback (most recent call last)")
    assert ran["answered"] == "false" and ran["sliced"] and ran["select"] == []
    assert "analyzer build could not be read" in ran["stderr"]


def test_no_changed_source_asks_no_graph_and_runs_no_slice(tmp_path) -> None:
    ran = _selection(tmp_path, source_changed=False)
    assert ran["answered"] == "false" and not ran["sliced"] and ran["select"] == []
    assert ran["output"] == []


def test_the_manifest_header_names_the_graph_that_answered() -> None:
    header = _TEXT.index('echo "# Selected tests: $MANIFEST_TEST_COUNT"')
    graph = _TEXT.index('if [[ -n "${GRAPH_LINE:-}" ]]; then\n        echo "# Graph: $GRAPH_LINE"')
    assert header < graph < _TEXT.index("# === SELECTED_TESTS ===", header)


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
        "STALE_SHADOW=true", "RUN_RECORDS=true", "USE_STORED_GRAPH=true",
        *_LINES[start:end + 1],
        'echo "$STALE_SHADOW $RUN_RECORDS $USE_STORED_GRAPH"',
    ])
    base = {"PATH": "/usr/bin:/bin"}
    done = _bash(script, _REPO, env={**base, **env})
    assert done.returncode == 0, done.stderr
    stale, runs, graph = done.stdout.split()
    return {"stale": stale, "runs": runs, "graph": graph}


def test_a_developers_run_records_and_selects_from_the_stored_graph() -> None:
    assert _switches() == {"stale": "true", "runs": "true", "graph": "true"}


@pytest.mark.parametrize("env", [
    {"CI": "true"}, {"SMART_TEST_ACTIVE": "1"}, {"TARGET_MODE": "true"},
])
def test_ci_a_nested_run_and_target_mode_record_nothing_and_use_no_stored_graph(env) -> None:
    """The stored graphs are one developer's, of this repository: a run started
    by a test, or against another repository, must not select from them."""
    assert _switches(**env) == {"stale": "false", "runs": "false", "graph": "false"}


def test_each_recorder_has_its_own_off_switch() -> None:
    assert re.search(r"^\s*--no-stale-shadow\) STALE_SHADOW=false ;;$", _TEXT, re.MULTILINE)
    assert re.search(r"^\s*--no-run-record\) RUN_RECORDS=false ;;$", _TEXT, re.MULTILINE)
    assert _TEXT.index("STALE_SHADOW=true") < _TEXT.index("--no-stale-shadow) STALE_SHADOW=false")
    assert _TEXT.index("RUN_RECORDS=true") < _TEXT.index("--no-run-record) RUN_RECORDS=false")
    assert re.search(r"^\s*--fresh-graph\) USE_STORED_GRAPH=false ;;$", _TEXT, re.MULTILINE)
    assert _TEXT.index("USE_STORED_GRAPH=true") < _TEXT.index("--fresh-graph) USE_STORED_GRAPH=false")


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
         "--index", str(logs / "index.sqlite"), *argv],
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


def _graph_survey(repo: Path, tree: str, edges: list[tuple[str, str]]) -> Path:
    """A survey where the cache would keep it, holding a small graph: a.py and
    b.py, three tests, and the given (source, target) edges between them."""
    paths = {"a": "packages/p/src/p/a.py", "b": "packages/p/src/p/b.py",
             "ta": "packages/p/tests/test_a.py", "tb": "packages/p/tests/test_b.py",
             "tl": "packages/p/tests/test_lost.py"}
    survey = repo.parent / "cache" / "results" / tree / "build" / "survey.json"
    survey.parent.mkdir(parents=True)
    survey.write_text(json.dumps({
        "nodes": [{"id": node, "path": path} for node, path in paths.items()],
        "edges": [{"src": src, "dst": dst} for src, dst in edges]}))
    return survey


def test_the_stale_graph_commands_keep_compare_and_report(repo: Path) -> None:
    out = repo.parent / "out"
    out.mkdir()
    (out / "changed.txt").write_text("packages/p/src/p/a.py\npackages/p/tests/test_edited.py\n")
    (out / "fresh1.txt").write_text(f"{repo}/packages/p/tests/test_a.py\n")

    # The first fresh slice: no stored graph to compare with, and it is kept.
    first = _graph_survey(repo, "s1", [("ta", "a")])
    done = _helper(repo, "stale-record", "--changed-files", str(out / "changed.txt"),
                   "--fresh-out", str(out / "fresh1.txt"), "--fresh-survey", str(first))
    assert "no comparison (no index, so no stored graph)" in done.stderr
    assert "graph rows: stored graph 1 (5 nodes, 1 edges" in done.stderr
    first_head = _git(repo, "rev-parse", "HEAD")

    # One commit later the fresh walk names a test the stored graph cannot:
    # in the new tree test_lost reaches a.py, an edge the old graph lacks, and
    # nothing that changed since the graph says so.
    (repo / "packages/p/src/p/b.py").write_text("B = 3\n")
    _git(repo, "commit", "-q", "-am", "three")
    second = _graph_survey(repo, "s2", [("ta", "a"), ("tl", "a")])
    (out / "fresh2.txt").write_text(
        f"{repo}/packages/p/tests/test_a.py\n{repo}/packages/p/tests/test_edited.py\n"
        f"{repo}/packages/p/tests/test_lost.py\n{repo}/packages/p/src/p/a.py\n")
    done = _helper(repo, "stale-record", "--changed-files", str(out / "changed.txt"),
                   "--fresh-out", str(out / "fresh2.txt"), "--fresh-survey", str(second),
                   "--fresh-seconds", "300")
    assert "STORED GRAPH LOST 1 TEST FILE(S)" in done.stderr
    assert "packages/p/tests/test_lost.py" in done.stderr
    assert "graph rows: stored graph 2" in done.stderr

    blind, compared = _rows(repo, "stale.jsonl")
    assert blind["stale"] is None and blind["why_no_comparison"] == "no index, so no stored graph"
    assert compared["changed_source"] == ["packages/p/src/p/a.py"]
    assert compared["stale"] == ["packages/p/tests/test_a.py"]
    assert compared["fresh_only"] == ["packages/p/tests/test_edited.py", "packages/p/tests/test_lost.py"]
    assert compared["fresh_only_unedited"] == ["packages/p/tests/test_lost.py"]
    assert compared["fresh_seconds"] == 300 and isinstance(compared["stale_seconds"], float)
    graph = compared["graph"]
    assert (graph["id"], graph["head"], graph["age_commits"], graph["dirty"]) == (
        1, first_head, 1, False)
    # b.py changed since the graph and is not this change's: it is seeded too.
    assert (graph["seeds"], graph["extra_seeds"]) == (3, 1)

    report = _helper(repo, "stale-report").stdout
    assert "compared            : 1" in report and "DISQUALIFIED" in report


def test_an_agreeing_stored_graph_is_reported_with_its_age(repo: Path) -> None:
    answer = repo.parent / "answer.txt"
    answer.write_text(f"{repo}/packages/p/tests/test_a.py\n")
    changed = repo.parent / "changed.txt"
    changed.write_text("packages/p/src/p/a.py\n")
    for tree in ("s1", "s2"):
        done = _helper(repo, "stale-record", "--changed-files", str(changed),
                       "--fresh-out", str(answer),
                       "--fresh-survey", str(_graph_survey(repo, tree, [("ta", "a")])))
    assert ("a stored graph 0 commit(s) old named every unedited test file the fresh walk "
            "named (1 fresh, 1 from the stored graph)") in done.stderr
    assert _rows(repo, "stale.jsonl")[1]["fresh_only_unedited"] == []


def test_the_fresh_graph_is_never_compared_with_itself(repo: Path) -> None:
    """A second fresh slice of the SAME tree and build must not read its own
    stored graph as an older one: that comparison can only agree."""
    answer = repo.parent / "answer.txt"
    answer.write_text(f"{repo}/packages/p/tests/test_a.py\n")
    survey = _graph_survey(repo, "s1", [("ta", "a")])
    for _ in range(2):
        done = _helper(repo, "stale-record", "--fresh-out", str(answer),
                       "--fresh-survey", str(survey))
    assert "no comparison (no usable stored graph: graph 1 is of this very tree)" in done.stderr
    assert [row["stale"] for row in _rows(repo, "stale.jsonl")] == [None, None]


def test_a_comparison_that_fails_is_stored_as_no_comparison_with_the_error(repo: Path) -> None:
    """The row must be written whatever happens to the stored graphs: here the
    index path is a directory, which no index can be opened from."""
    answer = repo.parent / "answer.txt"
    answer.write_text(f"{repo}/packages/p/tests/test_a.py\n")
    (repo.parent / "logs" / "index.sqlite").mkdir(parents=True)
    done = _helper(repo, "stale-record", "--fresh-out", str(answer),
                   "--fresh-survey", str(_graph_survey(repo, "s1", [("ta", "a")])))
    assert done.returncode == 0
    (row,) = _rows(repo, "stale.jsonl")
    assert row["stale"] is None
    assert row["why_no_comparison"].startswith("the selection from the stored graph failed: ")
    assert row["fresh"] == ["packages/p/tests/test_a.py"]


def test_no_identified_fresh_graph_is_no_comparison_and_nothing_kept(repo: Path) -> None:
    answer = repo.parent / "answer.txt"
    answer.write_text("")
    done = _helper(repo, "stale-record", "--fresh-out", str(answer), "--why", "a cache hit")
    assert "no comparison (a cache hit)" in done.stderr and "graph rows" not in done.stderr
    assert not (repo.parent / "logs" / "index.sqlite").exists()
