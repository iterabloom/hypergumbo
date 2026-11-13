# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read-record union in ``scripts/smart-test``, executed.

WHAT THE STEP IS FOR. A test that checks source files by reading them from disk
(a whole-tree gate: every analyzer's ``meta`` keys, every id construction)
imports none of the files it reads, so the import-graph slice never selects it
for a change to one of them. A PR that changed only ``swift.py`` passed its own
CI and broke ``test_meta_key_coherence.py`` on dev that way. The read record
(``hypergumbo_core.prose_only``) already holds which tests read which files;
this step adds the tests recorded reading a changed file.

WHAT IS PINNED, and what each test would catch:

    it adds a reader the selection lacks      the step does its job at all
    it counts only what it added              the header line reports the step's
                                              own contribution, not the overlap
    it never removes a test                   additive by construction
    a dead or silent helper adds nothing      a broken helper cannot redden or
                                              empty the run
    the off switch works                      --no-read-union, and CI
    it sits ABOVE the manifest seam           the merge-gating CI runs the list;
                                              a local-only step would leave the
                                              reported failure exactly as it was
    it sits AFTER narrowing                   narrowing drops a test with no
                                              coverage row on the changed file,
                                              which is what a reader is

The block is lifted verbatim out of ``scripts/smart-test`` and run against a
stub helper, as in ``test_smart_test_coverage_union.py`` and for its reasons.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SMART_TEST = _REPO / "scripts" / "smart-test"
_TEXT = _SMART_TEST.read_text(encoding="utf-8")

_GUARD = r'^if \[\[ "\$SELECT_READERS" == "true" \]\]'


def _block() -> str:
    start = re.search(_GUARD, _TEXT, re.MULTILINE)
    assert start, "the read-record union is gone from smart-test"
    end = re.search(r"^fi$", _TEXT[start.start():], re.MULTILINE)
    assert end
    return _TEXT[start.start():start.start() + end.end()]


def _run(tmp_path: Path, *, stub: str, affected: str, enabled: str = "true",
         changed: str = "pkg/src/swift.py") -> tuple[set[str], int, str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    helper = scripts / "coverage-select"
    helper.write_text(f"#!/usr/bin/env bash\n{stub}\n")
    helper.chmod(0o755)
    script = "\n".join([
        "set -uo pipefail",
        f'REPO_ROOT="{tmp_path}"',
        f'TOOLS_ROOT="{tmp_path}"',
        f'SELECT_READERS="{enabled}"',
        f'CHANGED_FILES="{changed}"',
        "log() { :; }",
        f"AFFECTED_TESTS='{affected}'",
        "READ_UNION_COUNT=0",
        _block(),
        'echo "COUNT:$READ_UNION_COUNT"',
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


def test_a_recorded_reader_the_selection_lacks_is_added(tmp_path) -> None:
    got, count, said = _run(
        tmp_path, stub='echo "core/tests/test_meta_key_coherence.py"',
        affected="lang/tests/test_swift.py")
    assert got == {"lang/tests/test_swift.py",
                   "core/tests/test_meta_key_coherence.py"}
    assert count == 1
    assert "--no-read-union" in said, "the message must name the way out"


def test_the_helper_is_asked_about_the_changed_python_files(tmp_path) -> None:
    """The stub echoes back what it was handed: the subcommand, and the file
    of changed paths with the non-Python one filtered out."""
    got, _, _ = _run(
        tmp_path,
        stub=('[ "$3" = readers ] || exit 0\n'
              '[ "$4" = --changed-files ] || exit 0\n'
              'sed "s|^|asked/|" "$5"'),
        affected="t/test_a.py",
        changed="pkg/src/swift.py\ndocs/x.md")
    assert got == {"t/test_a.py", "asked/pkg/src/swift.py"}


def test_overlap_is_not_counted(tmp_path) -> None:
    got, count, _ = _run(
        tmp_path, stub='printf "t/test_a.py\\nt/test_gate.py\\n"',
        affected="t/test_a.py")
    assert got == {"t/test_a.py", "t/test_gate.py"}
    assert count == 1


def test_it_never_removes_a_test(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub='echo "t/test_gate.py"',
                         affected="t/test_a.py\nt/test_b.py")
    assert {"t/test_a.py", "t/test_b.py"} <= got
    assert count == 1


def test_a_dead_helper_adds_nothing(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub="echo boom >&2; exit 3",
                         affected="t/test_a.py")
    assert got == {"t/test_a.py"}
    assert count == 0


def test_a_helper_with_nothing_to_say_adds_nothing(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub="exit 0", affected="t/test_a.py")
    assert got == {"t/test_a.py"}
    assert count == 0


def test_the_off_switch_is_honoured(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub='echo "t/test_gate.py"',
                         affected="t/test_a.py", enabled="false")
    assert got == {"t/test_a.py"}
    assert count == 0


def test_no_changed_file_asks_nothing(tmp_path) -> None:
    got, count, _ = _run(tmp_path, stub='echo "t/test_gate.py"',
                         affected="t/test_a.py", changed="")
    assert got == {"t/test_a.py"}
    assert count == 0


def test_it_defaults_on_with_a_flag_to_turn_it_off_and_is_off_in_ci() -> None:
    assert re.search(r"^SELECT_READERS=true$", _TEXT, re.MULTILINE)
    assert re.search(r"--no-read-union\)\s*SELECT_READERS=false", _TEXT)
    parse_end = _TEXT.index("\n    esac\ndone\n")
    guard = re.search(r'if \[\[ -n "\$\{CI:-\}" \]\]; then\n(?:.*\n)*?fi', _TEXT[parse_end:])
    assert guard and "SELECT_READERS=false" in guard.group(0), (
        "CI has no index: the step must be off there, after argument parsing")


def test_it_sits_above_the_manifest_seam_and_after_narrowing() -> None:
    """Above the seam, or CI never runs what it adds. After narrowing, or
    narrowing removes it: a reader has no coverage row on the file it reads."""
    here = re.search(_GUARD, _TEXT, re.MULTILINE)
    assert here
    seam = _TEXT.index('MANIFEST_TESTS="$AFFECTED_TESTS"')
    narrow = re.search(r'^if \[\[ "\$SELECT_NARROW" == "true" \]\]', _TEXT, re.MULTILINE)
    assert narrow
    assert narrow.start() < here.start() < seam


def test_the_manifest_header_reports_the_additions() -> None:
    assert 'echo "# Read-record tests: $READ_UNION_COUNT added' in _TEXT
