# SPDX-License-Identifier: AGPL-3.0-or-later
"""The by-statement step of ``scripts/smart-test`` (WI-pugol), executed.

WHAT THE STEP DOES. Inside the files a local targeted run will run, it asks
``coverage-select statement-narrow`` which tests the change does not need, and
hands the list to the ``pytest_statement_deselect`` plugin. Which tests, and
when it refuses, is :mod:`hypergumbo_core.statement_narrow` and is tested
there; this file pins the wiring:

    it passes the plugin the helper's list     the step acts at all
    only files the code reached may lose       a file another rule added (a
    tests                                      changed test, a gate's suite)
                                               runs whole
    an empty list or a dead helper             deselects nothing, and a failed
    deselects nothing                          helper cannot redden the run
    the off switch works                       --no-by-statement, CI, a run
                                               started by a test
    it sits BELOW the manifest seam            PR CI still runs whole files
                                               until WI-kunah
    the targeted run receives it               the list reaches pytest

The block is lifted verbatim out of ``scripts/smart-test`` and run against a
stub helper, as in ``test_smart_test_read_union.py`` and for its reasons.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SMART_TEST = _REPO / "scripts" / "smart-test"
_TEXT = _SMART_TEST.read_text(encoding="utf-8")

_START = r"^STATEMENT_ARGS=\(\)$"

#: A stub helper: copies --whole to whole.txt, writes two ids to --out.
_WRITES_TWO = """
while [[ $# -gt 0 ]]; do
  case $1 in
    --whole) cp "$2" whole.txt; shift ;;
    --out) out=$2; shift ;;
  esac
  shift
done
printf 'a/tests/test_x.py::t1\\na/tests/test_x.py::t2\\n' > "$out"
echo "by-statement: left out 2"
"""


def _between(start_pattern: str, end_pattern: str) -> str:
    start = re.search(start_pattern, _TEXT, re.MULTILINE)
    assert start, f"{start_pattern} is gone from smart-test"
    end = re.search(end_pattern, _TEXT[start.start():], re.MULTILINE)
    assert end
    return _TEXT[start.start():start.start() + end.end()]


def _function() -> str:
    return _between(r"^by_statement_left_out\(\) \{$", r"^\}$")


def _block() -> str:
    return _function() + "\n" + _between(_START, r"^fi$")


def _run(tmp_path: Path, *, stub: str, enabled: str = "true",
         extra: str = "") -> tuple[str, str]:
    scripts = tmp_path / "scripts"
    (scripts / "pytest_plugins").mkdir(parents=True, exist_ok=True)
    (scripts / "pytest_plugins" / "pytest_statement_deselect.py").write_text("")
    (tmp_path / ".ci").mkdir(exist_ok=True)
    helper = scripts / "coverage-select"
    helper.write_text(f"#!/usr/bin/env bash\n{stub}\n")
    helper.chmod(0o755)
    script = "\n".join([
        "set -uo pipefail",
        f'REPO_ROOT="{tmp_path}"',
        f'TOOLS_ROOT="{tmp_path}"',
        f'SELECT_BY_STATEMENT="{enabled}"',
        'LOCAL_CHANGED_FILES="a/src/a/m.py"',
        'CHANGED_FILES="a/src/a/m.py"',
        "AFFECTED_TESTS=$'a/tests/test_x.py\\na/tests/test_gate.py\\na/tests/test_changed.py'",
        "SLICE_TESTS=$'a/tests/test_x.py\\na/tests/test_changed.py'",
        'CHANGED_TEST_FILES="a/tests/test_changed.py"',
        extra,
        _block(),
        'echo "ARGS:${STATEMENT_ARGS[*]+${STATEMENT_ARGS[*]}}"',
    ])
    done = subprocess.run(
        ["bash", "-c", script],  # noqa: S607
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    return done.stdout, (tmp_path / "whole.txt").read_text() if (tmp_path / "whole.txt").exists() else ""


def test_the_helpers_list_goes_to_the_plugin(tmp_path: Path) -> None:
    out, _ = _run(tmp_path, stub=_WRITES_TWO)
    assert "by-statement: left out 2 (--no-by-statement runs them)" in out
    left_out = tmp_path / ".ci" / "statement-left-out.txt"
    assert f"ARGS:-p pytest_statement_deselect --statement-deselect={left_out}" in out


def test_only_files_the_code_reached_may_lose_tests(tmp_path: Path) -> None:
    _, whole = _run(tmp_path, stub=_WRITES_TWO)
    # test_gate.py came from another rule; test_changed.py is a changed test
    # the slice also reached. test_x.py is the slice's alone.
    assert whole.split() == ["a/tests/test_changed.py", "a/tests/test_gate.py"]


def test_a_coverage_union_file_may_lose_tests_too(tmp_path: Path) -> None:
    _, whole = _run(tmp_path, stub=_WRITES_TWO, extra='COVERAGE_UNION_NEW="a/tests/test_gate.py"')
    assert whole.split() == ["a/tests/test_changed.py"]


def test_an_empty_list_or_a_dead_helper_deselects_nothing(tmp_path: Path) -> None:
    for stub in ('echo "by-statement: left out nothing (no package source changed)"',
                 "exit 3"):
        out, _ = _run(tmp_path, stub=stub)
        assert out.rstrip().endswith("ARGS:"), out


def test_switched_off_the_helper_is_not_asked(tmp_path: Path) -> None:
    out, whole = _run(tmp_path, stub=_WRITES_TWO, enabled="false")
    assert out.strip() == "ARGS:"
    assert whole == ""


def test_the_off_switches() -> None:
    assert "--no-by-statement) SELECT_BY_STATEMENT=false ;;" in _TEXT
    guard = _TEXT.index('if [[ -n "${CI:-}" ]] || [[ -n "$STARTED_UNDER_SMART_TEST" ]]')
    assert "SELECT_BY_STATEMENT=false" in _TEXT[guard:_TEXT.index("\nfi\n", guard)]


def test_it_sits_below_the_manifest_seam_and_reaches_the_targeted_run() -> None:
    step = re.search(_START, _TEXT, re.MULTILINE)
    assert step
    assert _TEXT.index("\nMANIFEST_TESTS=\"$AFFECTED_TESTS\"") < step.start()
    run = _TEXT.index('run_pytest "$RUN_ORDER" "${FAIL_GRACE_ARGS[@]}" '
                      '${STATEMENT_ARGS[@]+"${STATEMENT_ARGS[@]}"} "${SCOPED_PYTEST_ARGS[@]}"')
    assert run > step.start()


def _manifest(tmp_path: Path, *, stub: str) -> str:
    """The manifest step and the manifest writer's by-statement lines, run."""
    _run(tmp_path, stub=stub)  # lays out the stub helper and plugin
    writer = _between(r'^    if \[\[ -n "\$MANIFEST_LEFT_OUT" \]\]; then$',
                      r'^    echo "# === CHANGED_SOURCE_FILES ==="$')
    script = "\n".join([
        "set -uo pipefail",
        f'REPO_ROOT="{tmp_path}"', f'TOOLS_ROOT="{tmp_path}"',
        'SELECT_BY_STATEMENT="true"', 'CHANGED_FILES="a/src/a/m.py"',
        "MANIFEST_TESTS=$'a/tests/test_x.py'", "SLICE_TESTS=$'a/tests/test_x.py'",
        _function(), _between(r'^MANIFEST_LEFT_OUT=""$', r"^fi$"), "{", writer, "}",
    ])
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,  # noqa: S607
                          cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_manifest_carries_the_ids_as_comment_lines(tmp_path: Path) -> None:
    out = _manifest(tmp_path, stub=_WRITES_TWO)
    assert out.splitlines() == [
        "# By statement: 2 test(s) deselected in 1 file(s) (smart-test --no-by-statement keeps them)",
        "#", "# === DESELECTED_TESTS ===",
        "#- a/tests/test_x.py::t1", "#- a/tests/test_x.py::t2",
        "# === CHANGED_SOURCE_FILES ==="]


def test_a_refusal_is_named_in_the_manifest_header(tmp_path: Path) -> None:
    out = _manifest(tmp_path, stub='echo "by-statement: left out nothing (no package source changed)"')
    assert out.splitlines() == [
        "# By statement: left out nothing (no package source changed)", "#",
        "# === CHANGED_SOURCE_FILES ==="]


def test_the_manifest_list_is_taken_above_the_seam() -> None:
    step = re.search(r'^MANIFEST_LEFT_OUT=""$', _TEXT, re.MULTILINE)
    assert step
    assert _TEXT.index('\nMANIFEST_TESTS="$AFFECTED_TESTS"') < step.start()
    assert step.start() < _TEXT.index('\nLOCAL_CHANGED_FILES="$CHANGED_FILES"')
