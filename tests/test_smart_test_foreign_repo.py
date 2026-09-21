# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-fopuh: ``smart-test`` must not write the manifest of a repo you are not in.

THE DEFECT. ``scripts/smart-test`` decides which repo it is talking about from
its OWN location -- ``REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"``, then
``cd "$REPO_ROOT"`` -- and writes to the literal relative path
``.ci/affected-tests.txt``. The caller's working directory is never consulted.
So invoking ``/A/scripts/smart-test`` from inside repo ``/B`` does not produce
``/B``'s manifest and does not fail: it silently rewrites ``/A``'s COMMITTED
``.ci/affected-tests.txt``, which is the artifact CI measures the next PR with.

HOW IT BIT. ``tests/test_autopr_*.py`` drive the real ``scripts/auto-pr`` with
``cwd`` set to a throwaway fixture repo, and ``auto-pr`` regenerates the
manifest before pushing. Ten such test modules do this without
``AUTO_PR_SKIP_MANIFEST=1``, so a local test run rewrote the working tree's
manifest as a side effect. In CI it was worse, because the Woodpecker steps
share one workspace: ``forge-arms`` installs pytest and NOTHING ELSE, so
``smart-test`` took its "no stable hypergumbo found" branch and wrote a
FULL-SUITE manifest over the checked-out one. The ``pytest`` step ran later,
read that file, and announced ``964 selected files`` -- exactly
``find packages/*/tests tests \\( -name 'test_*.py' -o -name
'BRANCHES_test_*.py' \\) | wc -l`` at that commit -- for a PR whose committed
manifest listed 75. The per-PR gate measured the PR against the whole tree,
which is how a five-line change dragged three unrelated latent defects through
five CI rounds, and the only evidence was a count with no traceable source.

WHY THESE TESTS BUILD A DISPOSABLE COPY OF THE SCRIPT. The property under test
is "the manifest of the repo the script lives in is not written", and asserting
that against the REAL repo would either be a no-op (if the guard works) or
destroy the working tree's committed manifest (if it does not) -- a test whose
red state damages the thing it is protecting. So each test stands up a tiny
repo that OWNS a copy of ``smart-test``, and the only variable between the two
load-bearing cases is the caller's cwd.
"""

# covers: scripts/smart-test
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"

#: Written into the tool repo's manifest before each run. Any rewrite -- a
#: targeted slice, a full-suite fallback, even an empty one -- destroys it, so
#: byte equality is a sufficient statement of "untouched".
SENTINEL = (
    "# Test selection manifest\n"
    "# Mode: targeted\n"
    "# Selected tests: 1\n"
    "#\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
    "tests/test_the_one_the_author_meant.py\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True,
        capture_output=True, text=True,
        env={"HOME": str(repo), "PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )


def _repo(path: Path) -> Path:
    """A committed git repo — `git rev-parse --show-toplevel` must resolve."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main", ".")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "root")
    return path


def _tool_repo(tmp_path: Path) -> Path:
    """A repo that owns its own copy of smart-test, with a seeded manifest."""
    tool = _repo(tmp_path / "toolrepo")
    (tool / "scripts").mkdir()
    shutil.copy2(SMART_TEST, tool / "scripts" / "smart-test")
    (tool / ".ci").mkdir()
    (tool / ".ci" / "affected-tests.txt").write_text(SENTINEL, encoding="utf-8")
    # smart-test's full-suite fallback enumerates `packages/*/tests tests` with
    # `find`, and `set -e` turns a missing directory into a non-zero exit. Give
    # the fixture the shape the script assumes, so a failing run means the guard
    # fired and not that the fixture was the wrong shape.
    for d in (tool / "tests", tool / "packages" / "demo" / "tests"):
        d.mkdir(parents=True)
        (d / "test_placeholder.py").write_text("def test_x() -> None:\n    pass\n")
    return tool


def _run(tool: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(tool / "scripts" / "smart-test"), "--manifest"],
        cwd=cwd, capture_output=True, text=True, timeout=180,
        env={"HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


def test_a_foreign_cwd_does_not_rewrite_this_repos_manifest(tmp_path: Path) -> None:
    """THE regression: called from another repo, it must refuse, not rewrite."""
    tool = _tool_repo(tmp_path)
    caller = _repo(tmp_path / "callerrepo")

    proc = _run(tool, cwd=caller)

    manifest = tool / ".ci" / "affected-tests.txt"
    assert manifest.read_text(encoding="utf-8") == SENTINEL, (
        "smart-test rewrote the manifest of a repo the caller was not in.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.returncode != 0, (
        "it must also SAY it did nothing — exiting 0 having written no manifest "
        "is the same silence one level over.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_the_refusal_names_both_repos_and_a_way_out(tmp_path: Path) -> None:
    """A refusal that does not say which two repos it is between is a riddle."""
    tool = _tool_repo(tmp_path)
    caller = _repo(tmp_path / "callerrepo")

    proc = _run(tool, cwd=caller)
    out = proc.stdout + proc.stderr

    assert str(tool) in out and str(caller) in out, (
        f"the message names neither the script's repo nor the caller's:\n{out}"
    )
    assert "cd " in out, f"the message offers no way out:\n{out}"


def test_a_cwd_inside_the_repo_still_writes_the_manifest(tmp_path: Path) -> None:
    """The DIFFERENCE arm (L17/L44).

    Same script, same fixture repo, same command — only the caller's cwd moves
    inside the tool repo. Without this, a smart-test that refused unconditionally
    would pass the two tests above and break every real invocation.
    """
    tool = _tool_repo(tmp_path)

    proc = _run(tool, cwd=tool / "scripts")

    manifest = tool / ".ci" / "affected-tests.txt"
    assert manifest.read_text(encoding="utf-8") != SENTINEL, (
        "a legitimate in-repo run did not regenerate the manifest — the guard "
        "is refusing the case it exists to permit.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def test_no_git_repo_at_the_cwd_is_not_treated_as_foreign(tmp_path: Path) -> None:
    """Scope the refusal to the case that was actually shown to bite (L54).

    A cwd outside any git repo names no competing repo, so there is nothing to
    confuse it with and today's behaviour is preserved. Widening the refusal to
    cover it would break cron-style callers for no evidenced reason.
    """
    tool = _tool_repo(tmp_path)
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()

    proc = _run(tool, cwd=elsewhere)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    manifest = tool / ".ci" / "affected-tests.txt"
    assert manifest.read_text(encoding="utf-8") != SENTINEL
