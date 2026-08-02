# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-modur: the per-PR pytest gate must run on SELECTION, not on changed source.

The bug this pins. The gate used to decide "should pytest run?" from the set of
changed ``packages/*/src/**.py`` files. Anything else -- a test-only fix, a
``scripts/*.py`` change, an ``.agent/hooks/**.py`` change -- produced an empty
source set, so the step printed "No Python source files changed" and exited 0.
The pipeline went GREEN having run nothing. It was caught on the WI-fodad PR,
whose committed manifest carried **0 changed source files and 1 selected test**:
smart-test had correctly selected the changed test, and CI threw that away.

That is the L48 family one level up. The recorded lesson is "a test that cannot
be SELECTED is indistinguishable from a test that passes"; this was an entire
CLASS OF PULL REQUEST for which the suite never ran, reported as a pass.

Why these tests drive the real shell instead of asserting on its text. A
source-text assertion ("the string `SELECTED_TESTS` appears near the skip") is
exactly the kind of test that passed while the behaviour was broken -- the old
condition *also* mentioned the manifest. So the block is extracted from the YAML
and EXECUTED against a fabricated manifest with stub binaries, and the assertion
is on what actually happened: did pytest get invoked?

PATH handling follows L49: the stub directory REPLACES ``$PATH`` rather than
being prepended, so "not stubbed" and "not findable" are the same statement and
a forgotten stub cannot silently fall through to the real binary.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".woodpecker" / "woodpecker.yml"


def _pytest_step_script() -> str:
    """The multi-line shell block of the `pytest` step, verbatim from the YAML."""
    data = yaml.safe_load(WORKFLOW.read_text())
    step = next(s for s in data["steps"] if s["name"] == "pytest")
    blocks = [c for c in step["commands"] if "\n" in c and "MANIFEST=" in c]
    assert len(blocks) == 1, f"expected exactly one manifest block, got {len(blocks)}"
    return blocks[0]


def _sandbox(tmp_path: Path, *, manifest_body: str, changed_files: list[str]) -> tuple[Path, Path]:
    """A fake repo + a REPLACED PATH holding only the stubs we intend (L49)."""
    repo = tmp_path / "repo"
    (repo / ".ci").mkdir(parents=True)
    (repo / ".ci" / "affected-tests.txt").write_text(manifest_body)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "pytest-was-invoked"

    # `git` stub: merge-base resolves, diff --name-only yields the PR's files.
    # NB: emit REAL newlines. A first draft used printf '%s' with a Python repr,
    # which wrote a literal backslash-n, so `grep -E '\.py$'` never matched and
    # every case fell into the earlier "no .py files" skip -- the harness, not
    # the gate, was producing the result.
    diff_lines = "\n".join(f"echo {f!r}" for f in changed_files)
    (bindir / "git").write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        case "$*" in
          *"merge-base"*) echo "basesha" ;;
          *"diff --name-only"*)
        __DIFF__
            ;;
          *) : ;;
        esac
        exit 0
    """).replace("__DIFF__", diff_lines))
    # `pytest` stub: records that it ran, and passes.
    (bindir / "pytest").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" > {str(marker)!r}
        exit 0
    """))
    for tool in ("git", "pytest"):
        (bindir / tool).chmod(0o755)
    # Real tools the block genuinely needs, symlinked in explicitly.
    # The block also runs the WI-fodad telemetry (df/stat/date/tail/...), so the
    # replaced PATH must carry everything it legitimately needs; a missing tool
    # shows up as a confusing empty result rather than a clear failure.
    for tool in ("bash", "sed", "grep", "tr", "wc", "echo", "sort", "cat", "nproc",
                 "coverage", "tail", "head", "date", "df", "stat", "ls", "find",
                 "mktemp", "dirname", "basename", "xargs", "awk", "python3", "env"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    return repo, marker


def _run(script: str, repo: Path, bindir: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": str(bindir),                 # REPLACED, not prepended (L49)
        "CI_PIPELINE_EVENT": "pull_request",
        "CI_COMMIT_TARGET_BRANCH": "dev",
        "CI_COMMIT_SOURCE_BRANCH": "feature/x",
        "CI_COMMIT_BRANCH": "feature/x",
        "CI_COMMIT_SHA": "headsha",
        "CI_PREV_COMMIT_SHA": "prevsha",
        "CI_WORKSPACE": str(repo),
        "HOME": str(repo),
    }
    return subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env,
        capture_output=True, text=True, timeout=120,
    )


MANIFEST_TEST_ONLY = (
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
    "tests/test_something.py\n"
)
MANIFEST_NOTHING_SELECTED = (
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
)


def test_extracted_block_is_the_real_thing() -> None:
    """Non-vacuity floor (L17): prove we extracted the gate, not an empty string."""
    script = _pytest_step_script()
    assert "SELECTED_TESTS" in script
    assert "pytest --rootdir=." in script
    assert len(script.splitlines()) > 30, "extracted block is implausibly short"


def test_test_only_change_still_runs_pytest(tmp_path: Path) -> None:
    """THE regression: 0 changed source files + 1 selected test => pytest RUNS.

    This is the exact manifest shape the WI-fodad PR committed, and the exact
    case the old source-keyed condition skipped.
    """
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_TEST_ONLY,
        changed_files=["tests/test_something.py"],
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")
    assert marker.exists(), (
        "pytest was NOT invoked for a test-only change — the gate is keying on "
        f"changed source again.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "tests/test_something.py" in marker.read_text()


def test_skip_only_when_nothing_is_selected(tmp_path: Path) -> None:
    """The legitimate skip survives: an empty selection runs nothing."""
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_NOTHING_SELECTED,
        changed_files=["scripts/unmapped_helper.py"],
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")
    assert not marker.exists(), "pytest ran despite an empty selection"
    assert "selects no tests" in result.stdout, result.stdout


def test_no_skip_condition_keys_on_changed_source(tmp_path: Path) -> None:
    """Changed-source files may size the COVERAGE gate, never gate execution.

    Kept as a structural companion to the behavioural tests above: it names the
    specific regression (a source-emptiness `exit 0`) so a reintroduction is
    reported as intent rather than as a mysterious skip.
    """
    script = _pytest_step_script()
    lowered = script.lower()
    assert "no python source files changed" not in lowered, (
        "the source-keyed skip is back; it makes test-only, scripts/, and "
        ".agent/hooks/ pull requests run no tests while reporting green"
    )
