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

WI-vilor: a SECOND fail-open hole in the same block. WI-modur fixed which
*question* the gate asks; this covers what it does when it cannot get an answer.
``git diff`` against a base that does not exist writes nothing to stdout, which
is byte-identical to a successful diff finding no Python files -- so the gate
skipped the suite and reported green. It was reached systematically, not by bad
luck: ``CI_PREV_COMMIT_SHA`` is the previous *pipeline's* commit, and auto-pr
merges by rebase, so on every post-merge push that SHA names a rewritten object
absent from the clone. Both halves (fail-closed diff, validated base) are
mutation-tested here.
"""

# covers: .woodpecker/woodpecker.yml
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


def _sandbox(
    tmp_path: Path,
    *,
    manifest_body: str,
    changed_files: list[str],
    diff_fails: bool = False,
    prev_sha_resolvable: bool = True,
    committed_manifest: str | None = None,
    committed_readable: bool = True,
) -> tuple[Path, Path]:
    """A fake repo + a REPLACED PATH holding only the stubs we intend (L49).

    `diff_fails` makes `git diff` behave the way it does against a rewritten
    base: a `fatal:` on stderr, nothing on stdout, non-zero exit.
    `prev_sha_resolvable` controls whether `git cat-file -e <sha>^{commit}`
    succeeds, which is how the block decides whether to trust
    CI_PREV_COMMIT_SHA.
    `committed_manifest` is what `git show HEAD:.ci/affected-tests.txt` returns
    -- the blob the PR actually committed, which WI-fopuh showed is not always
    the file sitting on disk. It defaults to the on-disk body (the ordinary
    case, where nothing rewrote it). `committed_readable=False` makes that read
    FAIL instead, the way it would for a manifest untracked at HEAD.
    """
    repo = tmp_path / "repo"
    (repo / ".ci").mkdir(parents=True)
    (repo / ".ci" / "affected-tests.txt").write_text(manifest_body)
    committed_blob = tmp_path / "committed-manifest.txt"
    committed_blob.write_text(
        manifest_body if committed_manifest is None else committed_manifest
    )

    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "pytest-was-invoked"

    # `git` stub: merge-base resolves, diff --name-only yields the PR's files.
    # NB: emit REAL newlines. A first draft used printf '%s' with a Python repr,
    # which wrote a literal backslash-n, so `grep -E '\.py$'` never matched and
    # every case fell into the earlier "no .py files" skip -- the harness, not
    # the gate, was producing the result.
    if diff_fails:
        diff_lines = (
            '    echo "fatal: bad object deadbeefdeadbeefdeadbeefdeadbeef" >&2\n'
            "    exit 128"
        )
    else:
        diff_lines = "\n".join(f"echo {f!r}" for f in changed_files)
    catfile_rc = "0" if prev_sha_resolvable else "1"
    # `cat-file` must be matched BEFORE the generic arms; the block calls it as
    # `git cat-file -e <sha>^{commit}`.
    # `show HEAD:<path>` must be matched before the generic arms too; it is how
    # the block reaches the blob the PR committed rather than the working file.
    show_arm = (
        f"    cat {str(committed_blob)!r}" if committed_readable
        else "    exit 128"
    )
    (bindir / "git").write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        case "$*" in
          *"cat-file -e"*) exit __CATFILE_RC__ ;;
          *"show HEAD:"*)
        __SHOW__
            ;;
          *"merge-base"*) echo "basesha" ;;
          *"diff --name-only"*)
        __DIFF__
            ;;
          *) : ;;
        esac
        exit 0
    """).replace("__DIFF__", diff_lines).replace("__CATFILE_RC__", catfile_rc)
       .replace("__SHOW__", show_arm))
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


def _run(
    script: str,
    repo: Path,
    bindir: Path,
    *,
    event: str = "pull_request",
) -> subprocess.CompletedProcess:
    env = {
        "PATH": str(bindir),                 # REPLACED, not prepended (L49)
        "CI_PIPELINE_EVENT": event,
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


def test_failed_diff_does_not_skip_the_suite(tmp_path: Path) -> None:
    """WI-vilor: a git error must never read as "nothing changed".

    Observed live on the dev push pipeline for 6e92792a34, which printed
    `fatal: bad object 66737979de...` and then "PR changes no .py files —
    skipping pytest", reporting SUCCESS having run nothing on a commit that
    changed four Python files. A failed `git diff` writes nothing to stdout,
    which is byte-identical to a successful diff that found no Python files.
    """
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_TEST_ONLY,
        changed_files=["tests/test_something.py"],
        diff_fails=True,
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")
    assert marker.exists(), (
        "pytest was NOT invoked after `git diff` failed — the gate is treating "
        "an ERROR as an empty changed-file set and skipping the suite.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "cannot diff" in result.stdout, (
        "the diff failure was swallowed silently; it must be reported so a "
        f"green pipeline is not mistaken for a tested one.\n{result.stdout}"
    )


def test_push_event_falls_back_when_prev_sha_was_rewritten(tmp_path: Path) -> None:
    """WI-vilor: CI_PREV_COMMIT_SHA is the previous PIPELINE's commit.

    auto-pr merges by rebase, which rewrites the commit, so on every post-merge
    push that SHA names an object absent from the clone. Trusting it produced a
    guaranteed-failing diff on every rebase-merged push -- not intermittently.
    """
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_TEST_ONLY,
        changed_files=["packages/hypergumbo-core/src/hypergumbo_core/x.py"],
        prev_sha_resolvable=False,
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin", event="push")
    assert "using HEAD^" in result.stdout, (
        "an unresolvable CI_PREV_COMMIT_SHA was used as the diff base instead "
        f"of falling back to HEAD^.\nstdout:\n{result.stdout}"
    )
    assert marker.exists(), (
        f"pytest did not run on the fallback range.\nstdout:\n{result.stdout}"
    )


def test_push_event_keeps_a_resolvable_prev_sha(tmp_path: Path) -> None:
    """The fallback is scoped: a valid previous commit is still used.

    Without this the fix would be indistinguishable from "always diff HEAD^",
    which silently undercounts a push that lands more than one commit.
    """
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_TEST_ONLY,
        changed_files=["packages/hypergumbo-core/src/hypergumbo_core/x.py"],
        prev_sha_resolvable=True,
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin", event="push")
    assert "using HEAD^" not in result.stdout, (
        "fell back to HEAD^ despite CI_PREV_COMMIT_SHA resolving cleanly.\n"
        f"stdout:\n{result.stdout}"
    )
    assert marker.exists(), result.stdout


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


# ── WI-fopuh: the file on disk is not necessarily the file the PR committed ──
#
# The Woodpecker steps share ONE workspace. `smart-test` resolves its manifest
# path from its OWN location, never the caller's cwd, so any step that reaches
# it rewrites the checked-out `.ci/affected-tests.txt`: `forge-arms` runs
# `tests/test_autopr_*.py`, those shell out to the real `scripts/auto-pr`, and
# auto-pr regenerates the manifest before pushing. That container installs
# pytest and nothing else, so the regen fell back to a FULL-SUITE manifest.
# PR #1107's pytest step then announced `964 selected files` -- the full-suite
# `find` count -- for a PR whose committed manifest listed 75, and ran the whole
# tree. The root cause is fixed at the writer (scripts/smart-test now refuses a
# foreign cwd); these tests pin the reader, so a second writer cannot make the
# gate silently measure the wrong set again.
#
# The gate DISCLOSES rather than fails. Measuring the committed artifact is the
# safety property; turning an unexplained rewrite into a red PR is the
# recall-loss direction, and the disclosure is what makes the next one
# diagnosable in one look instead of five CI rounds.

MANIFEST_CLOBBERED = (
    "# Mode: full-suite\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
    "tests/test_everything_else.py\n"
)


def test_the_gate_reads_the_committed_manifest_not_the_workspace_copy(
    tmp_path: Path,
) -> None:
    """THE regression: a rewritten workspace manifest must not steer the gate."""
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_CLOBBERED,
        committed_manifest=MANIFEST_TEST_ONLY,
        changed_files=["tests/test_something.py"],
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")

    assert marker.exists(), (
        f"pytest never ran.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    invoked = marker.read_text()
    assert "tests/test_something.py" in invoked, (
        "the gate ran the WORKSPACE manifest's selection, not the one the PR "
        f"committed.\npytest args: {invoked}\nstdout:\n{result.stdout}"
    )
    assert "tests/test_everything_else.py" not in invoked, (
        f"the clobbered selection reached pytest anyway.\npytest args: {invoked}"
    )


def test_a_rewritten_manifest_is_disclosed(tmp_path: Path) -> None:
    """Reading the right file silently would leave the rewrite undiagnosable."""
    repo, _ = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_CLOBBERED,
        committed_manifest=MANIFEST_TEST_ONLY,
        changed_files=["tests/test_something.py"],
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")

    assert "WI-fopuh" in result.stdout, (
        f"the divergence is not attributed to anything:\n{result.stdout}"
    )
    assert "Mode: full-suite" in result.stdout, (
        "the disclosure must show the header of the file that was on disk — "
        "without it the reader still cannot tell WHAT overwrote it.\n"
        f"{result.stdout}"
    )


def test_an_undisturbed_manifest_is_not_flagged(tmp_path: Path) -> None:
    """The DIFFERENCE arm (L17). A warning printed on every run is not read."""
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_TEST_ONLY,
        changed_files=["tests/test_something.py"],
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")

    assert marker.exists(), f"pytest never ran:\n{result.stdout}\n{result.stderr}"
    assert "WI-fopuh" not in result.stdout, (
        f"an untouched manifest was reported as rewritten:\n{result.stdout}"
    )


def test_the_step_prints_the_header_of_the_manifest_it_read(tmp_path: Path) -> None:
    """The filed ask: make the file observable, not just its cardinality.

    The whole investigation cost what it did because the step's only output
    about selection was a COUNT, which cannot be traced back to a file.
    """
    repo, _ = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_TEST_ONLY,
        changed_files=["tests/test_something.py"],
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")

    assert "=== CHANGED_SOURCE_FILES ===" in result.stdout, (
        f"the step does not show the manifest it read:\n{result.stdout}"
    )


def test_an_unreadable_committed_blob_falls_back_and_says_so(tmp_path: Path) -> None:
    """Absent is not empty: a failed `git show` is not evidence of agreement.

    The working-tree copy is still the best available answer, so the step uses
    it — but it must not present that as having read the committed artifact.
    """
    repo, marker = _sandbox(
        tmp_path,
        manifest_body=MANIFEST_TEST_ONLY,
        committed_readable=False,
        changed_files=["tests/test_something.py"],
    )
    result = _run(_pytest_step_script(), repo, tmp_path / "bin")

    assert marker.exists(), (
        "an unreadable committed blob must not stop the suite — that is the "
        f"fail-open direction WI-vilor already paid for.\n{result.stdout}"
    )
    assert "tests/test_something.py" in marker.read_text()
    assert "working-tree" in result.stdout, (
        f"the fallback is not declared:\n{result.stdout}"
    )
