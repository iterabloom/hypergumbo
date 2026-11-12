# SPDX-License-Identifier: AGPL-3.0-or-later
"""smart-test's prose-only rule, end to end on the selection bed.

A changed ``.py`` file whose code is unchanged (comments and docstrings only)
selects the tests recorded seeing its text, not every test that imports it,
and the manifest CI runs carries that short list. The rule leaves tests out,
so it is checked against the bed's oracle from both sides: nothing that must
run is left out, and the tests that only import the file are.

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
    static = _stage(scores, "comment-only", "static")
    manifest = _stage(scores, "comment-only", "manifest")
    # Reach: the import graph selects the importers, so there is something to leave out.
    importers = {f"{CORE}test_chain_a.py", f"{CORE}test_chain_c.py", f"{APP}test_cli.py"}
    assert importers <= static
    assert not importers & manifest
    # The three tests that read chain_c.py as text; two of them the import
    # graph never selected.
    assert manifest == {f"{CORE}test_citations.py"} | TEXT_READERS
    assert not TEXT_READERS & static
    assert manifest == set(scores["comment-only"]["must"])


def test_a_docstring_read_at_runtime_runs_the_tests_that_read_it(scores: dict) -> None:
    """misc.help_text() returns usage.__doc__: no file is read, and the
    recorder names the docstring each test saw."""
    manifest = _stage(scores, "docstring-only", "manifest")
    assert {f"{CORE}test_misc.py", f"{APP}test_cli.py"} <= manifest
    assert set(scores["docstring-only"]["must"]) <= manifest
    assert not manifest & set(scores["docstring-only"]["should_not"])


@pytest.mark.parametrize("edit", ["pragma", "comment-marker", "body-edit-c"])
def test_control_a_code_or_machine_comment_edit_is_left_alone(scores: dict, edit: str) -> None:
    """``# pragma: no cover`` and ``# axis:`` are comments a tool reads; an
    edit to one is not prose, and the rule must not touch the selection."""
    assert _stage(scores, edit, "manifest") == _stage(scores, edit, "static")


def test_a_code_edit_beside_a_comment_edit_keeps_its_whole_selection(scores: dict) -> None:
    mixed = _stage(scores, "comment-with-code", "manifest")
    # tables.py changed for real: its importer stays.
    assert f"{CORE}test_tables.py" in mixed
    # chain_c.py changed in a comment only: its importer goes, its readers stay.
    assert f"{CORE}test_chain_a.py" in _stage(scores, "comment-with-code", "static")
    assert f"{CORE}test_chain_a.py" not in mixed
    assert {f"{CORE}test_citations.py"} | TEXT_READERS <= mixed
    assert set(scores["comment-with-code"]["must"]) <= mixed


def test_the_later_stages_keep_what_the_rule_selected(scores: dict) -> None:
    """The coverage union and the opt-in narrowing judge tests against the
    files whose code changed; the prose-only files are not among those."""
    for edit in ("comment-only", "comment-with-code"):
        for stage in ("union", "narrow"):
            assert _stage(scores, edit, "manifest") <= _stage(scores, edit, stage), (edit, stage)


# ── where the rule sits in the script ────────────────────────────────────────

def _text() -> str:
    return SMART_TEST.read_text(encoding="utf-8")


def test_prose_only_files_never_reach_the_import_graph_slice() -> None:
    """They leave $CHANGED_FILES before select_static runs, and their tests
    join the selection inside it, above the manifest seam."""
    text = _text()
    seam = text.index('MANIFEST_TESTS="$AFFECTED_TESTS"')
    call = text.index("\nselect_static\n")
    removal = text.index(
        'CHANGED_FILES=$(echo "$CHANGED_FILES" | grep -vxFf <(echo "$PROSE_ONLY_FILES")')
    assert text.count(" prose-select ") == 1
    assert text.index(" prose-select ") < removal < call < seam
    union = text.index('"$AFFECTED_TESTS" "$PROSE_TESTS"')
    assert text.index("select_static() {") < union < call


def test_the_manifest_says_when_the_rule_applied() -> None:
    assert 'echo "# Prose-only files: $(echo "$PROSE_ONLY_FILES" | wc -l)' in _text()


def test_the_rule_is_off_in_ci_and_can_be_turned_off() -> None:
    text = _text()
    guard = re.search(r'if \[\[ -n "\$\{CI:-\}" \]\]; then\n    SELECT_NARROW=false\n'
                      r'(?:\s*#.*\n)*\s*SELECT_PROSE=false\n', text)
    assert guard, "SELECT_PROSE must be forced off under CI, after flag parsing"
    assert "--no-prose-rule) SELECT_PROSE=false ;;" in text


def test_the_local_coverage_gate_skips_prose_only_files() -> None:
    """Their importers were not selected, so the gate would report the
    unchanged code as uncovered."""
    text = _text()
    assert 'LOCAL_CHANGED_FILES="$CHANGED_FILES"' in text
    assert 'build_affected_cov_paths "$LOCAL_CHANGED_FILES"' in text
    assert 'COVERABLE_CHANGED_FILES=$(echo "$LOCAL_CHANGED_SOURCE_FILES"' in text


def test_a_failed_helper_leaves_the_selection_to_the_import_graph() -> None:
    """coverage-select exits 0 whatever happens, so an empty answer and a
    dead helper must differ: the rule acts only when both output files exist
    and name at least one prose-only file."""
    text = _text()
    assert 'if [[ -s "$_prose_dir/files" ]] && [[ -e "$_prose_dir/tests" ]]; then' in text


def test_opt_in_narrowing_cannot_remove_what_the_rule_selected() -> None:
    assert '"$NARROWED" "$PROSE_TESTS" | sed' in _text()


def test_the_recorder_is_told_the_pass_version_hash_is_not_a_read() -> None:
    assert '"--source-reads-hash-only=hypergumbo_core.ir:compute_pass_version"' in _text()


# ── a run with failures still leaves its record ─────────────────────────────

def test_a_red_run_stores_the_record_inside_run_pytest() -> None:
    """smart-test exits at run_pytest's non-zero return (set -e), so the
    caller's fold is never reached on a red run."""
    text = _text()
    body = text[text.index("\nrun_pytest() {"):]
    fold = body.index(" fold-reads ")
    assert body.index("if [[ $exit_code -ne 0 ]] && [[ \"${SELECT_SHADOW:-false}\" == \"true\" ]]") < fold
    assert fold < body.index("return $exit_code", fold)


def test_fold_reads_stores_the_passed_tests_and_removes_the_record(tmp_path: Path) -> None:
    """A failed test may have stopped before a read it would have made. The
    record names its failures; a junit report can name more."""
    import sqlite3

    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("def test_ok():\n    pass\n")
    reads = tmp_path / "reads"
    reads.mkdir()
    bucket = {"reads": [], "opaque": [], "docs": [], "lines": [], "fixtures": []}
    ids = [f"tests/test_x.py::{name}" for name in ("test_ok", "test_bad", "test_worse")]
    record = json.dumps({
        "version": 2, "observed": True, "expected": {"tests/test_x.py": ids},
        "failed": [ids[1]], "tests": {test_id: bucket for test_id in ids}})
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_x" name="test_ok"/>'
        '<testcase classname="tests.test_x" name="test_worse"><error/></testcase>'
        '</testsuite></testsuites>')
    select = REPO_ROOT / "scripts" / "coverage-select"

    def stored(index: Path, *junit_args: str) -> list[str]:
        (reads / "main-1.json").write_text(record)
        done = subprocess.run(
            [sys.executable, str(select), "--repo-root", str(root), "--index", str(index),
             *junit_args, "fold-reads", "--source-reads", str(reads)],
            capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        # Stored records are removed: what is left is never-stored by definition.
        assert list(reads.glob("*.json")) == []
        conn = sqlite3.connect(index)
        try:
            return sorted(r[0] for r in conn.execute("SELECT test_id FROM read_test"))
        finally:
            conn.close()

    assert stored(tmp_path / "a.sqlite") == [ids[0], ids[2]]
    assert stored(tmp_path / "b.sqlite", "--junit", str(junit)) == [ids[0]]


def test_a_record_left_by_a_killed_run_is_stored_before_the_next_run_replaces_it() -> None:
    text = _text()
    body = text[text.index("\nrun_pytest() {"):]
    fold = body.index("fold-reads --announce")
    assert fold < body.index('rm -f "$reads_dir"/*.json')
