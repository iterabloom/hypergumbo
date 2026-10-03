# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-kabaf: the GitHub ci.yml manifest banner must count the SELECTION.

The bug this pins. ``.github/workflows/ci.yml`` counted the committed test
manifest (``.ci/affected-tests.txt``) with ``grep -c "^packages/"`` at three
sites: the ``# (manifest lists N test files)`` line and the ``Manifest
validated (N test files)`` banner of the ``pytest`` job, and the same
``manifest lists`` line of the ``pytest-retry`` job. The manifest is
SECTIONED -- ``# === CHANGED_SOURCE_FILES ===`` then ``# === SELECTED_TESTS
===`` -- and a path prefix is not a section reader:

* a selection of root-level ``tests/...`` files (agent-infra / forge tests:
  the shape every scripts-only or hooks-only PR produces) counted **0**, so the
  banner read "Manifest validated (0 test files)" while CI went on to run them;
* the CHANGED_SOURCE_FILES entries also start with ``packages/``, so a
  package-test selection was OVER-counted by the number of changed sources;
* ``grep -c`` prints ``0`` AND exits 1 on no match, so ``|| echo 0`` appended a
  second ``0`` and the banner printed a two-line "0\\n0".

The same mistake ``_manifest_test_count`` (scripts/lib/forgejo-api.sh) was
written to fix in auto-pr's reporting path. The fix here reads the section the
"Run tests with coverage" step reads, so the banner and the run agree.

Blast radius, stated precisely: DIAGNOSTIC ONLY. The values were only echoed;
which tests ran came from the SELECTED_TESTS parse in the run step, and the
SANITY_OK gate keys on ``packages/$mod/tests/``. And the file is DORMANT:
GitHub Actions is disabled under Woodpecker CI (scripts/ci-debug's GitHub arm;
WI-bavak), so the live per-PR gate is ``.woodpecker/woodpecker.yml``, whose
``=== pytest (N selected files`` line already counts the section with
``wc -w`` -- that control is pinned in tests/test_ci_pytest_selection_gate.py.

Why the steps are EXECUTED rather than grepped (the WI-modur rationale): a
text assertion can pass while the behaviour is broken. The two "Validate test
manifest" run blocks are extracted from the YAML, their ``${{ ... }}``
expressions are bound to a pull_request event, and they run under the shell
GitHub uses for a ``run:`` with no ``shell:`` key (``bash -e``) against a
fabricated manifest, with a REPLACED ``$PATH`` (L49) holding a stub ``git``.
"""

# covers: .github/workflows/ci.yml
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The ${{ ... }} expressions the manifest steps read, bound to a PR event.
_EXPRESSIONS = {
    "github.event_name": "pull_request",
    "github.event.pull_request.base.sha": "basesha",
    "github.event.pull_request.head.sha": "headsha",
    "github.head_ref": "feature/x",
}

MANIFEST_ROOT_TESTS_ONLY = (
    "# Mode: targeted\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
    "tests/test_autopr_manifest_union.py\n"
    "tests/test_smart_test.py\n"
)
# 2 changed sources + 3 selected tests: a prefix count says 5.
MANIFEST_MIXED = (
    "# Mode: targeted\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "packages/hypergumbo-core/src/hypergumbo_core/a.py\n"
    "packages/hypergumbo-core/src/hypergumbo_core/b.py\n"
    "# === SELECTED_TESTS ===\n"
    "packages/hypergumbo-core/tests/test_a.py\n"
    "packages/hypergumbo-core/tests/test_b.py\n"
    "tests/test_smart_test.py\n"
)
MANIFEST_NOTHING_SELECTED = (
    "# Mode: targeted\n"
    "# === CHANGED_SOURCE_FILES ===\n"
    "# === SELECTED_TESTS ===\n"
)


def _manifest_step_script(job: str) -> str:
    """The `Validate test manifest` run block of JOB, expressions bound."""
    data = yaml.safe_load(CI_YML.read_text())
    steps = [s for s in data["jobs"][job]["steps"] if s.get("id") == "manifest"]
    assert len(steps) == 1, f"{job}: expected one manifest step, got {len(steps)}"
    script = steps[0]["run"]

    def bind(m: re.Match[str]) -> str:
        expr = m.group(1).strip()
        assert expr in _EXPRESSIONS, f"{job}: unbound expression ${{{{ {expr} }}}}"
        return _EXPRESSIONS[expr]

    return re.sub(r"\$\{\{(.*?)\}\}", bind, script)


def _sandbox(tmp_path: Path, manifest_body: str, changed: list[str]) -> Path:
    repo = tmp_path / "repo"
    (repo / ".ci").mkdir(parents=True)
    (repo / ".ci" / "affected-tests.txt").write_text(manifest_body)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    diff_lines = "\n".join(f"    echo {f!r}" for f in changed) or "    :"
    (bindir / "git").write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        case "$*" in
          *"diff --name-only"*)
        __DIFF__
            ;;
          *) : ;;
        esac
        exit 0
    """).replace("__DIFF__", diff_lines))
    (bindir / "git").chmod(0o755)
    for tool in ("bash", "sed", "grep", "head", "cut", "sort", "tr", "wc", "cat"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    return repo


def _run(job: str, tmp_path: Path, manifest_body: str, changed: list[str],
         *, pipefail: bool = False) -> subprocess.CompletedProcess[str]:
    repo = _sandbox(tmp_path, manifest_body, changed)
    out = tmp_path / "github_output"
    out.touch()
    shell = ["bash", "-eo", "pipefail", "-c"] if pipefail else ["bash", "-e", "-c"]
    return subprocess.run(
        [*shell, _manifest_step_script(job)],
        cwd=repo,
        env={"PATH": str(tmp_path / "bin"), "GITHUB_OUTPUT": str(out),
             "HOME": str(repo)},
        capture_output=True, text=True, timeout=60,
    )


def _lists_line(stdout: str) -> str:
    """Everything from the `manifest lists` banner to the next banner line.

    Taken as a SPAN, not one line, so the old two-line "0\\n0" is visible.
    """
    start = stdout.index("# (manifest lists")
    end = stdout.index("test files)", start) + len("test files)")
    return stdout[start:end]


@pytest.mark.parametrize("job", ["pytest", "pytest-retry"])
def test_extracted_block_is_the_real_thing(job: str) -> None:
    """Non-vacuity floor (L17): the block that prints the banner was found."""
    script = _manifest_step_script(job)
    assert "manifest lists" in script
    assert "use_manifest=true" in script


@pytest.mark.parametrize("job", ["pytest", "pytest-retry"])
def test_root_level_selection_is_counted(job: str, tmp_path: Path) -> None:
    """THE regression: 2 selected root-level tests read as 0 under the prefix."""
    result = _run(job, tmp_path, MANIFEST_ROOT_TESTS_ONLY,
                  ["scripts/auto-pr", "tests/test_smart_test.py"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _lists_line(result.stdout) == "# (manifest lists 2 test files)", (
        f"{job}: the banner does not count the SELECTED_TESTS section.\n"
        f"stdout:\n{result.stdout}"
    )


@pytest.mark.parametrize("job", ["pytest", "pytest-retry"])
def test_changed_sources_are_not_counted_as_tests(job: str, tmp_path: Path) -> None:
    """The prefix also matched CHANGED_SOURCE_FILES: 2 sources + 3 tests != 5."""
    result = _run(job, tmp_path, MANIFEST_MIXED,
                  ["packages/hypergumbo-core/src/hypergumbo_core/a.py",
                   "packages/hypergumbo-core/src/hypergumbo_core/b.py"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _lists_line(result.stdout) == "# (manifest lists 3 test files)", (
        f"{job}: stdout:\n{result.stdout}"
    )


def test_validated_banner_counts_the_selection(tmp_path: Path) -> None:
    """The `Manifest validated (N test files)` banner, pytest job only.

    Reached only when SANITY_OK holds, so the fixture changes a core source
    and selects a core test alongside root-level ones.
    """
    result = _run("pytest", tmp_path, MANIFEST_MIXED,
                  ["packages/hypergumbo-core/src/hypergumbo_core/a.py"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "✓ hypergumbo-core: 2 tests" in result.stdout, result.stdout  # reach
    assert "✅ Manifest validated (3 test files)" in result.stdout, result.stdout


@pytest.mark.parametrize("pipefail", [False, True])
@pytest.mark.parametrize("job", ["pytest", "pytest-retry"])
def test_empty_selection_reads_one_zero(job: str, pipefail: bool,
                                        tmp_path: Path) -> None:
    """`grep -c` prints 0 AND exits 1: `|| echo 0` made that "0\\n0".

    Run under plain `-e` (GitHub's default for a `run:` with no `shell:`) and
    under `-eo pipefail` (the `shell: bash` form): an empty selection is a
    legitimate manifest and must neither kill the step nor double the zero.
    """
    result = _run(job, tmp_path, MANIFEST_NOTHING_SELECTED, ["docs/x.md"],
                  pipefail=pipefail)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _lists_line(result.stdout) == "# (manifest lists 0 test files)", (
        f"{job}: stdout:\n{result.stdout}"
    )


@pytest.mark.parametrize("path", sorted(
    [*(REPO_ROOT / ".github" / "workflows").glob("*.yml"),
     *(REPO_ROOT / ".woodpecker").glob("*.yml")],
    key=str,
), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_workflow_counts_the_manifest_by_path_prefix(path: Path) -> None:
    """Structural companion over BOTH CI trees (live .woodpecker/, dormant .github/).

    Names the regression so a reintroduction reads as intent: a `grep -c` keyed
    on `^packages/` or `^tests/` against a manifest cannot count a sectioned
    selection. The behavioural tests above are the real pin.
    """
    offending = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if not line.lstrip().startswith("#")  # a comment may NAME the old form
        and re.search(r"""grep\s+-c\s+["']\^(packages|tests)/""", line)
    ]
    assert not offending, (
        f"{path.name} counts a manifest by path prefix (WI-kabaf):\n"
        + "\n".join(offending)
    )
