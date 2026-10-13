# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-tazur: a targeted smart-test run never silently collects zero tests.

THE BUG. A commit that ``git rm``'d a test file left that path in smart-test's
selection, because ``CHANGED_TEST_FILES`` was computed from the git diff, and a
diff names deleted paths exactly as it names edited ones. The run handed the
dead path to ``pytest -n``. Under xdist a missing path is not a usage error:
the controller reports ``6 workers [0 items]``, ``no tests ran``, and exit 5,
and the "file or directory not found" message never reaches the log. The
summary then printed ``COVERAGE: (all files at 100%)``, because "no line has a
gap" is true of a run that measured nothing. A 824-file selection was skipped
and the output read green.

TWO LAYERS, both pinned here, because each one fixes a different question:

(a) THE SELECTION. A deleted test file is a change, not a test to run. The
    selection is computed from the paths that still exist, the deleted ones are
    named in the output, and a backstop refuses to publish (or run) a selection
    that names any path missing from the working tree, so a future selector
    that forgets the distinction fails loudly rather than reaching pytest.

(b) THE VERDICT. Whatever the cause, a run that executes zero tests is not a
    pass. pytest's exit 5, its exit 4, everything deselected, everything
    skipped: each exits non-zero with a message that says so, and the summary
    does not claim coverage. "Nothing was affected, so nothing ran" stays a
    success, because it never reaches pytest, and says so in its own words.

WHY THESE DRIVE THE REAL SCRIPT. Every assertion below runs the shipped
``scripts/smart-test`` end to end in a throwaway git repo, with real pytest, a
stub "stable hypergumbo" and a stub mypy ratchet. A text assertion ("the word
``present`` appears near ``CHANGED_TEST_FILES``") is the kind of test that
passes while the behaviour is broken. Run time is about two seconds a case.
"""

# covers: scripts/smart-test
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SMART_TEST = REPO_ROOT / "scripts" / "smart-test"

PASSING_TEST = "def test_ok():\n    assert True\n"
SKIPPED_TEST = (
    "import pytest\n\n"
    "@pytest.mark.skip(reason='fixture')\n"
    "def test_skipped():\n    assert True\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    )


def _fixture_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A git repo holding a copy of smart-test and ``files``, committed once.

    The copy matters: smart-test refuses to run from a cwd inside a different
    repo (WI-fopuh), so it has to live in the repo it measures.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SMART_TEST, repo / "scripts" / "smart-test")
    shutil.copy2(REPO_ROOT / ".coveragerc.no-embeddings", repo)
    ratchet = repo / "scripts" / "check-mypy-ratchet"
    ratchet.write_text("#!/usr/bin/env bash\nexit 0\n")
    ratchet.chmod(0o755)
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _commit(repo: Path, message: str = "change") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _smart_test(repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the fixture's smart-test with a stub stable hypergumbo on HOME.

    The baseline falls back to ``HEAD~1`` (no ``origin/dev`` in the fixture),
    so the diff under test is exactly the last commit.
    """
    home = tmp_path / "home"
    hg = home / ".local" / "bin" / "hypergumbo"
    hg.parent.mkdir(parents=True, exist_ok=True)
    hg.write_text("#!/usr/bin/env bash\nexit 0\n")
    hg.chmod(0o755)
    env = {
        k: v for k, v in os.environ.items()
        if k not in {"SMART_TEST_ACTIVE", "CI", "HG_SELECT_SHADOW",
                     "COVERAGE_PROCESS_START", "PYTEST_ADDOPTS",
                     "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT"}
    }
    env["HOME"] = str(home)
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", str(repo / "scripts" / "smart-test"),
         "--no-select-shadow", "--no-select-union",
         "-n0", "-p", "no:cacheprovider", *args],
        cwd=repo, env=env, capture_output=True, text=True, timeout=300,
    )


def _manifest_selection(repo: Path) -> list[str]:
    text = (repo / ".ci" / "affected-tests.txt").read_text()
    section = text.split("# === SELECTED_TESTS ===", 1)[1]
    return [ln for ln in section.splitlines() if ln and not ln.startswith("#")]


# ── (a) THE SELECTION ──────────────────────────────────────────────────────


def test_a_deleted_test_file_is_not_selected(tmp_path: Path) -> None:
    """THE filed repro: ``git rm`` a test, edit another, run smart-test.

    The deleted path must reach neither the manifest (which CI runs) nor
    pytest, and the edited test must still run and pass.
    """
    repo = _fixture_repo(tmp_path, {
        "tests/test_kept.py": PASSING_TEST,
        "tests/test_gone.py": PASSING_TEST,
    })
    _git(repo, "rm", "-q", "tests/test_gone.py")
    (repo / "tests" / "test_kept.py").write_text(PASSING_TEST + "\n# edited\n")
    _commit(repo)

    result = _smart_test(repo, tmp_path)
    out = result.stdout + result.stderr

    assert result.returncode == 0, out
    assert _manifest_selection(repo) == ["tests/test_kept.py"], out
    assert "1 passed" in out, (
        "the surviving edited test did not run: the deleted path took the "
        f"whole selection down with it\n{out}"
    )
    assert "tests/test_gone.py" in out and "deleted" in out.lower(), (
        "a deleted test file must be NAMED in the output, not silently "
        f"dropped: the reader has to be able to see what was not run\n{out}"
    )


def test_a_deletion_only_change_is_a_disclosed_success(tmp_path: Path) -> None:
    """Deleting a test and nothing else: there is nothing to run, legitimately.

    That is a success, and it must SAY it is the nothing-to-run success, in
    words distinct from a run that collected zero tests, and distinct from the
    docs/config-only skip (which would be a false description of this diff).
    """
    repo = _fixture_repo(tmp_path, {
        "tests/test_kept.py": PASSING_TEST,
        "tests/test_gone.py": PASSING_TEST,
    })
    _git(repo, "rm", "-q", "tests/test_gone.py")
    _commit(repo)

    result = _smart_test(repo, tmp_path)
    out = result.stdout + result.stderr

    assert result.returncode == 0, out
    assert _manifest_selection(repo) == [], out
    assert "0 tests ran" in out, out
    assert "tests/test_gone.py" in out, out
    assert "docs/config only" not in out, (
        f"a deletion-only change was described as a docs/config change\n{out}"
    )
    assert "ZERO TESTS EXECUTED" not in out, out


def test_the_backstop_refuses_a_selection_naming_a_missing_path() -> None:
    """Defense in depth for selectors not written yet.

    The shipped guard is extracted and fed a selection that names a missing
    path; it must fail and name the path. Without this, the next selector
    that forgets deleted paths reaches pytest again.
    """
    text = SMART_TEST.read_text()
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("require_selection_on_disk() {"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    fn = "\n".join(lines[start:end + 1])
    script = textwrap.dedent("""\
        set -euo pipefail
        cd "$1"
        {fn}
        require_selection_on_disk "the manifest" "$(printf 'present.py\\nmissing.py\\n')"
        echo REACHED
    """).replace("{fn}", fn)
    tmp = Path(subprocess.run(["mktemp", "-d"], capture_output=True,
                              text=True, check=True).stdout.strip())
    try:
        (tmp / "present.py").write_text("")
        proc = subprocess.run(["bash", "-c", script, "bash", str(tmp)],
                              capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "REACHED" not in out, out
        assert "missing.py" in out, out
        assert "present.py" not in out.replace("missing.py", ""), (
            f"the guard named a path that exists\n{out}"
        )

        ok = script.replace("missing.py\\n", "")
        proc = subprocess.run(["bash", "-c", ok, "bash", str(tmp)],
                              capture_output=True, text=True)
        assert proc.returncode == 0 and "REACHED" in proc.stdout, (
            proc.stdout + proc.stderr
        )
    finally:
        shutil.rmtree(tmp)


# ── (b) THE VERDICT ────────────────────────────────────────────────────────


def _assert_loud_zero_run(result: subprocess.CompletedProcess) -> None:
    out = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"a run that executed zero tests exited 0\n{out}"
    )
    assert "ZERO TESTS EXECUTED" in out, out
    assert "(all files at 100%)" not in out, (
        "the summary claimed coverage for a run that measured nothing — the "
        f"exact line INV-tazur reported\n{out}"
    )


def test_everything_deselected_fails_loudly(tmp_path: Path) -> None:
    """pytest exits 5 when ``-k`` matches nothing. That is not a pass."""
    repo = _fixture_repo(tmp_path, {"tests/test_one.py": PASSING_TEST})
    (repo / "tests" / "test_one.py").write_text(PASSING_TEST + "\n# edited\n")
    _commit(repo)
    _assert_loud_zero_run(_smart_test(repo, tmp_path, "-k", "no_such_test"))


def test_everything_skipped_fails_loudly(tmp_path: Path) -> None:
    """pytest exits 0 when every collected test skips. Zero ran: not a pass."""
    repo = _fixture_repo(tmp_path, {"tests/test_one.py": SKIPPED_TEST})
    (repo / "tests" / "test_one.py").write_text(SKIPPED_TEST + "\n# edited\n")
    _commit(repo)
    _assert_loud_zero_run(_smart_test(repo, tmp_path))


def test_an_explicit_target_that_collects_nothing_fails_loudly(
    tmp_path: Path,
) -> None:
    """The explicit-path mode shares run_pytest, so it shares the verdict."""
    repo = _fixture_repo(tmp_path, {"tests/test_one.py": PASSING_TEST})
    _assert_loud_zero_run(
        _smart_test(repo, tmp_path, "tests/test_one.py", "-k", "no_such_test")
    )


def test_a_normal_targeted_run_is_still_green(tmp_path: Path) -> None:
    """Positive control: without it, a harness that always failed would make
    every assertion above pass while proving nothing."""
    repo = _fixture_repo(tmp_path, {"tests/test_one.py": PASSING_TEST})
    (repo / "tests" / "test_one.py").write_text(PASSING_TEST + "\n# edited\n")
    _commit(repo)
    result = _smart_test(repo, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "1 passed" in out, out
    assert "ZERO TESTS EXECUTED" not in out, out
