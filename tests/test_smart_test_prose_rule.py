# SPDX-License-Identifier: AGPL-3.0-or-later
"""smart-test's prose-only rule, end to end on the selection bed.

A changed ``.py`` file whose code is unchanged (comments and docstrings only)
selects the tests with a reason to read its text, not every test that imports
it. The rule REMOVES tests from the local run, so it is checked against the
bed's oracle from both sides: nothing that must run is dropped, and the tests
that only import the file are.

The decision logic is unit-tested in ``test_prose_only.py`` (core) and the
recorder in ``test_pytest_source_reads.py``. This file pins the wiring: the
real ``scripts/smart-test`` on the bed, and where in the script the rule sits.
"""

# covers: scripts/smart-test, scripts/coverage-select, scripts/smart-test-bed
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"
HARNESS = REPO_ROOT / "scripts" / "smart-test-bed"

CORE = "packages/bed-core/tests/"
APP = "packages/bed-app/tests/"
TEXT_READERS = {f"{CORE}test_docstrings.py", "tests/test_tree_gate.py"}


@pytest.fixture(scope="module")
def scores(tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = tmp_path_factory.mktemp("bed") / "scores.json"
    done = subprocess.run(
        [sys.executable, str(HARNESS), "run", "--json", str(out),
         "--edit", "comment-only", "--edit", "docstring-only",
         "--edit", "comment-marker", "--edit", "pragma",
         "--edit", "comment-with-code", "--edit", "body-edit-c"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert done.returncode == 0, done.stdout + done.stderr
    return {s["edit"]: s for s in json.loads(out.read_text())}


def _stage(scores: dict, edit: str, stage: str) -> set[str]:
    return set(scores[edit]["selected"][stage])


def test_a_comment_edit_runs_its_text_readers_and_not_its_importers(scores: dict) -> None:
    manifest = _stage(scores, "comment-only", "manifest")
    prose = _stage(scores, "comment-only", "prose")
    # Reach: the import graph selects the importers, so there is something to drop.
    importers = {f"{CORE}test_chain_a.py", f"{CORE}test_chain_c.py", f"{APP}test_cli.py"}
    assert importers <= manifest
    assert not importers & prose
    # The three tests that read chain_c.py as text; two of them the import
    # graph never selected.
    assert prose == {f"{CORE}test_citations.py"} | TEXT_READERS
    assert not TEXT_READERS & manifest
    assert set(scores["comment-only"]["must"]) <= prose


def test_a_docstring_read_at_runtime_keeps_the_tests_that_execute_the_reader(scores: dict) -> None:
    """misc.help_text() returns usage.__doc__: no file is read, so only the
    coverage index knows which tests can see the new docstring."""
    prose = _stage(scores, "docstring-only", "prose")
    assert {f"{CORE}test_misc.py", f"{APP}test_cli.py"} <= prose
    assert set(scores["docstring-only"]["must"]) <= prose


@pytest.mark.parametrize("edit", ["pragma", "comment-marker", "body-edit-c"])
def test_control_a_code_or_machine_comment_edit_is_left_alone(scores: dict, edit: str) -> None:
    """``# pragma: no cover`` and ``# axis:`` are comments a tool reads; an
    edit to one is not prose, and the rule must not touch the selection."""
    assert _stage(scores, edit, "prose") == _stage(scores, edit, "manifest")


def test_a_code_edit_beside_a_comment_edit_keeps_its_whole_selection(scores: dict) -> None:
    mixed = _stage(scores, "comment-with-code", "prose")
    # tables.py changed for real: its importer stays.
    assert f"{CORE}test_tables.py" in mixed
    # chain_c.py changed in a comment only: its importer goes, its readers stay.
    assert f"{CORE}test_chain_a.py" in _stage(scores, "comment-with-code", "manifest")
    assert f"{CORE}test_chain_a.py" not in mixed
    assert {f"{CORE}test_citations.py"} | TEXT_READERS <= mixed
    assert set(scores["comment-with-code"]["must"]) <= mixed


def test_the_manifest_is_never_narrowed(scores: dict) -> None:
    """CI runs the manifest. It still holds the import-graph selection."""
    assert f"{CORE}test_chain_a.py" in _stage(scores, "comment-only", "manifest")


# ── where the rule sits in the script ────────────────────────────────────────

def _text() -> str:
    return SMART_TEST.read_text(encoding="utf-8")


def test_the_rule_acts_below_the_manifest_seam() -> None:
    text = _text()
    seam = text.index('MANIFEST_TESTS="$AFFECTED_TESTS"')
    assert text.count("prose-narrow") == 1
    assert text.index("prose-narrow") > seam
    assert text.index('select_static\n') < seam


def test_the_rule_is_off_in_ci_and_can_be_turned_off() -> None:
    text = _text()
    guard = re.search(r'if \[\[ -n "\$\{CI:-\}" \]\]; then\n    SELECT_NARROW=false\n'
                      r'(?:\s*#.*\n)*\s*SELECT_PROSE=false\n', text)
    assert guard, "SELECT_PROSE must be forced off under CI, after flag parsing"
    assert "--no-prose-rule) SELECT_PROSE=false ;;" in text


def test_the_local_coverage_gate_skips_prose_only_files() -> None:
    """Their importers were just dropped, so the gate would report the
    unchanged code as uncovered."""
    text = _text()
    assert 'build_affected_cov_paths "$LOCAL_CHANGED_FILES"' in text
    assert 'COVERABLE_CHANGED_FILES=$(echo "$LOCAL_CHANGED_SOURCE_FILES"' in text


def test_a_failed_helper_leaves_the_run_set_alone() -> None:
    """coverage-select exits 0 whatever happens, so an empty answer and a
    dead helper must differ: the rule acts only when the output file exists."""
    text = _text()
    assert 'if [[ -e "$_prose_dir/kept" ]]; then' in text
