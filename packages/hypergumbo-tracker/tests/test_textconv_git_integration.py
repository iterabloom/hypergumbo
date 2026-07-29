# SPDX-License-Identifier: MPL-2.0
"""End-to-end gate: git must actually be able to run the textconv driver.

Why this file exists
--------------------
``textconv`` had thorough tests and was still broken in production for months.
Every existing test called ``textconv_main`` **in-process** (``test_cli.py``) or
**mocked the subprocess** (``test_setup.py``, which asserted the configured
command string equalled ``"python -m hypergumbo_tracker.cli textconv"`` — the
string that does not work). So the suite covered the renderer and the config
plumbing while leaving the only thing that can fail untested: whether *git*,
resolving its own attributes, can invoke the configured command successfully.

The live symptom was that ``git log -p`` exited **128** anywhere it touched a
``.ops`` file — the driver printed argparse's "invalid choice" usage dump to
stderr, and git aborts a diff whose textconv driver writes to stderr.

Two properties matter and neither is checkable in-process:

* the configured command **runs** (exit 0, and critically **empty stderr** —
  that is git's actual requirement, not just a clean exit); and
* git's attribute resolution actually reaches the driver we configured. Nested
  ``.gitattributes`` inside the ``.ops/`` directories override the repo-root
  pattern by path specificity, so a root-level ``diff=`` declaration can be
  live-looking and permanently dead.

The non-vacuity guard is the load-bearing assertion: a driver that silently
emitted nothing would produce a clean ``git log -p`` too. We assert the compiled
item state appears in the patch output, so "green" means the driver ran *and*
rendered.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# A minimal but real op log: one create op, nonce on every line per ADR-0013.
_OPS = """\
- op: create  # a1b2
  at: '2026-01-01T00:00:00Z'  # a1b2
  by: agent  # a1b2
  actor: tester  # a1b2
  clock: 1  # a1b2
  nonce: a1b2  # a1b2
  data:  # a1b2
    kind: work_item  # a1b2
    title: Textconv driver must be invocable by git  # a1b2
    status: todo_soft  # a1b2
    priority: 2  # a1b2
"""

_ITEM_ID = "WI-tctest-aaaaa-bbbbb-ccccc-ddddd-eeeee-fffff-ggggg"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture()
def repo_with_ops(tmp_path: Path) -> Path:
    """A git repo shaped like a real tracker repo, with one committed op file.

    Mirrors production layout deliberately: a root ``.gitattributes`` pattern
    AND a nested one inside ``.ops/``, because the interaction between them is
    the defect this module guards.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / ".gitattributes").write_text(
        ".agent/tracker/.ops/.*.ops  linguist-generated  merge=union  diff=tracker\n"
    )
    ops_dir = repo / ".agent" / "tracker" / ".ops"
    ops_dir.mkdir(parents=True)
    (ops_dir / ".gitattributes").write_text("*.ops merge=union\n*.ops diff=tracker\n")
    (ops_dir / f".{_ITEM_ID}.ops").write_text(_OPS)

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add op file")
    return repo


def _configure_driver(repo: Path, command: str) -> None:
    """Point the resolved diff driver at ``command`` (repo-local config only)."""
    _git(repo, "config", "--local", "diff.tracker.textconv", command)


# The command the tracker configures in real deployments, as argv. Kept as a
# literal list (not a string to split) so the direct-invocation test below runs
# a fixed argv rather than anything derived at call time.
_DRIVER_ARGV = ["hypergumbo-tracker", "textconv"]


def _driver_command() -> str:
    """The configured command, as the single string git config stores."""
    return " ".join(_DRIVER_ARGV)


def test_attribute_resolution_reaches_the_configured_driver(repo_with_ops: Path) -> None:
    """``git check-attr`` must resolve .ops files to the driver we configure.

    Guards the specificity trap: if a nested ``.gitattributes`` names a
    *different* driver than the root, the root's is dead configuration and any
    fix applied there has no effect.
    """
    out = _git(
        repo_with_ops, "check-attr", "diff", "--",
        f".agent/tracker/.ops/.{_ITEM_ID}.ops",
    ).stdout
    assert out.strip().endswith("diff: tracker"), (
        f"attribute resolved to something other than 'tracker': {out!r}"
    )


def test_configured_driver_runs_with_empty_stderr(repo_with_ops: Path) -> None:
    """The configured command must exit 0 AND write nothing to stderr.

    Empty stderr is the real contract: git aborts the diff when a textconv
    driver writes to stderr, even if it exits 0.
    """
    ops = repo_with_ops / ".agent" / "tracker" / ".ops" / f".{_ITEM_ID}.ops"
    proc = subprocess.run(
        [*_DRIVER_ARGV, str(ops)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"driver exited {proc.returncode}: {proc.stderr}"
    assert proc.stderr == "", f"driver wrote to stderr (git will abort): {proc.stderr!r}"
    assert _ITEM_ID in proc.stdout, "driver produced no compiled state"


def test_git_log_patch_succeeds_through_the_driver(repo_with_ops: Path) -> None:
    """``git log -p`` over a .ops file exits 0 and shows compiled state.

    The end-to-end property. The final assertion is a non-vacuity guard: a
    driver emitting nothing would also produce a clean exit, so a green run
    without it would prove only that git did not crash.
    """
    _configure_driver(repo_with_ops, _driver_command())
    proc = _git(repo_with_ops, "log", "-p", "-1", check=False)
    assert proc.returncode == 0, (
        f"git log -p exited {proc.returncode}; stderr:\n{proc.stderr}"
    )
    assert _ITEM_ID in proc.stdout, (
        "textconv output absent from the patch — driver did not render"
    )
    assert "Textconv driver must be invocable by git" in proc.stdout, (
        "compiled title absent — driver ran but rendered nothing useful"
    )


def test_git_diff_and_blame_succeed_through_the_driver(repo_with_ops: Path) -> None:
    """``git diff`` and ``git blame`` also route through textconv and must work."""
    _configure_driver(repo_with_ops, _driver_command())
    ops_rel = f".agent/tracker/.ops/.{_ITEM_ID}.ops"

    (repo_with_ops / ops_rel).write_text(_OPS + _OPS.replace("a1b2", "c3d4"))
    diff = _git(repo_with_ops, "diff", "--", ops_rel, check=False)
    assert diff.returncode == 0, f"git diff exited {diff.returncode}: {diff.stderr}"

    blame = _git(repo_with_ops, "blame", "--", ops_rel, check=False)
    assert blame.returncode == 0, f"git blame exited {blame.returncode}: {blame.stderr}"


def test_a_stderr_writing_driver_is_detected_as_broken(repo_with_ops: Path) -> None:
    """Positive control: the gate must FAIL for a driver that writes to stderr.

    Without this, a gate that never fires looks identical to a gate that passes.
    This is the shape the real defect had — a driver that exits non-zero and
    prints usage to stderr — so we prove the assertions above can catch it.
    """
    _configure_driver(repo_with_ops, "sh -c 'echo boom >&2; exit 1' --")
    proc = _git(repo_with_ops, "log", "-p", "-1", check=False)
    assert proc.returncode != 0, (
        "git log -p succeeded despite a broken driver — the gate cannot detect "
        "the real defect and is therefore vacuous"
    )
