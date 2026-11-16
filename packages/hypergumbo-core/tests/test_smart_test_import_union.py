# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import-closure union in ``scripts/smart-test``, executed.

WHAT THE STEP IS FOR. The static selector follows call edges. A test of a
module that only reads a constant of a changed file is reached by no call, so
it was left out of the manifest: one constant changed in ``schema.py`` and the
test that fails was not among the 252 selected. When a source file's
import-time code changes, this step adds every test file whose imports reach
it (``coverage-select import-closure``, :mod:`hypergumbo_core.import_closure`).

WHAT IS PINNED, and what each test would catch:

    it adds an importer the selection lacks   the step does its job at all
    it asks about the changed Python files    against the run's own baseline
    it counts only what it added              the header line reports the step's
                                              own contribution, not the overlap
    it never removes a test                   additive by construction
    a dead or silent helper adds nothing      a broken helper cannot redden or
                                              empty the run
    the off switch works                      --no-import-union
    it sits ABOVE the manifest seam           the merge-gating CI runs the list
    it sits AFTER narrowing                   a later narrowing rule cannot take
                                              these tests back out
    the manifest header says what it added    a reader of the manifest can tell
                                              why a test is there

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

_GUARD = r'^if \[\[ "\$SELECT_IMPORTERS" == "true" \]\]'


def _block() -> str:
    start = re.search(_GUARD, _TEXT, re.MULTILINE)
    assert start, "the import-closure union is gone from smart-test"
    end = re.search(r"^fi$", _TEXT[start.start():], re.MULTILINE)
    assert end
    return _TEXT[start.start():start.start() + end.end()]


def _run(tmp_path: Path, *, stub: str, affected: str, enabled: str = "true",
         changed: str = "pkg/src/schema.py", verbose: str = "false",
         baseline: str = "abc123") -> tuple[set[str], int, str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    helper = scripts / "coverage-select"
    helper.write_text(f"#!/usr/bin/env bash\n{stub}\n")
    helper.chmod(0o755)
    script = "\n".join([
        "set -uo pipefail",
        f'REPO_ROOT="{tmp_path}"',
        f'TOOLS_ROOT="{tmp_path}"',
        f'SELECT_IMPORTERS="{enabled}"',
        f'VERBOSE="{verbose}"',
        f'BASELINE="{baseline}"',
        f'CHANGED_FILES="{changed}"',
        "log() { :; }",
        f"AFFECTED_TESTS='{affected}'",
        "IMPORT_UNION_COUNT=0",
        _block(),
        'echo "COUNT:$IMPORT_UNION_COUNT"',
        'echo "RESULT:"',
        'echo "$AFFECTED_TESTS"',
    ])
    done = subprocess.run(
        ["bash", "-c", script],  # noqa: S607
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    head, result = done.stdout.split("RESULT:\n", 1)
    count = int(head.rsplit("COUNT:", 1)[1].split()[0])
    return set(result.split()), count, head


def test_an_importer_the_selection_lacks_is_added(tmp_path) -> None:
    got, count, said = _run(
        tmp_path, stub='echo "core/tests/test_stable_shape_ids.py"',
        affected="core/tests/test_schema.py")
    assert got == {"core/tests/test_schema.py", "core/tests/test_stable_shape_ids.py"}
    assert count == 1
    assert "--no-import-union" in said, "the message must name the way out"


def test_the_helper_is_asked_about_the_changed_python_files_at_the_baseline(tmp_path) -> None:
    """The stub echoes back what it was handed: the subcommand, the file of
    changed paths with the non-Python one filtered out, and the base ref."""
    got, _, _ = _run(
        tmp_path,
        stub=('[ "$3" = import-closure ] || exit 0\n'
              '[ "$4" = --changed-files ] || exit 0\n'
              '[ "$6" = --base ] || exit 0\n'
              'sed "s|^|asked/$7/|" "$5"'),
        affected="t/test_a.py",
        changed="pkg/src/schema.py\ndocs/x.md")
    assert got == {"t/test_a.py", "asked/abc123/pkg/src/schema.py"}


def test_verbose_reaches_the_helper_only_when_asked(tmp_path) -> None:
    stub = 'for a in "$@"; do [ "$a" = --verbose ] && echo "t/saw_verbose.py"; done; echo "t/ran.py"'
    quiet, _, _ = _run(tmp_path, stub=stub, affected="t/test_a.py")
    loud, _, _ = _run(tmp_path, stub=stub, affected="t/test_a.py", verbose="true")
    assert "t/saw_verbose.py" not in quiet and "t/ran.py" in quiet
    assert "t/saw_verbose.py" in loud


def test_overlap_is_not_counted(tmp_path) -> None:
    got, count, _ = _run(
        tmp_path, stub='printf "t/test_a.py\\nt/test_reader.py\\n"',
        affected="t/test_a.py")
    assert got == {"t/test_a.py", "t/test_reader.py"}
    assert count == 1


def test_it_never_removes_a_test(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub='echo "t/test_reader.py"',
                         affected="t/test_a.py\nt/test_b.py")
    assert {"t/test_a.py", "t/test_b.py"} <= got
    assert count == 1


def test_a_dead_helper_adds_nothing(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub="echo boom >&2; exit 3",
                         affected="t/test_a.py")
    assert got == {"t/test_a.py"}
    assert count == 0


def test_a_helper_with_nothing_to_say_adds_nothing(tmp_path) -> None:
    got, count, said = _run(tmp_path, stub="exit 0", affected="t/test_a.py")
    assert got == {"t/test_a.py"}
    assert count == 0
    assert "Import-closure union" not in said


def test_the_off_switch_is_honoured(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub='echo "t/test_reader.py"',
                         affected="t/test_a.py", enabled="false")
    assert got == {"t/test_a.py"}
    assert count == 0
    assert re.search(r"--no-import-union\) SELECT_IMPORTERS=false", _TEXT)
    assert re.search(r"^SELECT_IMPORTERS=true$", _TEXT, re.MULTILINE)


def test_it_sits_after_narrowing_and_above_the_manifest_seam() -> None:
    narrow = _TEXT.index('if [[ "$SELECT_NARROW" == "true" ]]')
    union = re.search(_GUARD, _TEXT, re.MULTILINE)
    assert union
    seam = _TEXT.index('MANIFEST_TESTS="$AFFECTED_TESTS"')
    assert narrow < union.start() < seam


def test_the_manifest_header_reports_the_step() -> None:
    start = _TEXT.rindex('echo "# Mode: targeted"')
    header = _TEXT[start:_TEXT.index('echo "# === SELECTED_TESTS ==="', start)]
    assert 'if [[ "$IMPORT_UNION_COUNT" -gt 0 ]]' in header
    assert "# Import-closure tests: $IMPORT_UNION_COUNT added" in header
