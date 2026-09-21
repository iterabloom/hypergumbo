# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-furum: a failed manifest regen must say WHY, not guess one reason for all.

THE DEFECT. ``auto-pr`` runs the regen as::

    elif "$SCRIPT_DIR/smart-test" --manifest >/dev/null 2>&1; then

Both streams go to ``/dev/null``, so every non-zero exit -- hypergumbo not
installed, the slice crashing, or (since WI-fopuh) a refusal to write the
manifest of a repo the caller is not in -- arrives as one fixed line::

    ⚠️  Manifest generation skipped (no stable hypergumbo?)

One message standing in for several distinct causes is this project's recurring
absent-versus-empty defect at the diagnostics layer: the reader cannot tell
which of them happened, and the parenthetical actively points at the wrong one.
smart-test's refusal names both repo roots and both ways out, and none of it
survived the redirect.

WHY THESE TESTS COPY THE SCRIPT TREE. The property is "whatever smart-test said
reaches the operator", so the test has to CHOOSE what smart-test says. auto-pr
resolves it as ``$SCRIPT_DIR/smart-test``, i.e. beside itself, so each test
copies ``scripts/`` into a throwaway tree and replaces that one file with a stub
of known exit code and known stderr. A test that asserted on the real
smart-test's current wording would pin the wording, not the relay.

NO NETWORK. ``AUTO_PR_SIMULATE_OUTAGE=1`` makes the push queue a vPR locally,
and the check for it sits *after* the regen block, so the run reaches the code
under test and then stops without touching a remote. That matters: a test that
passes because a push fails fast is a flake with a good day (WI-hajak).
"""

# covers: scripts/auto-pr
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from test_autopr_result_sentinel import _init_fake_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_AUTO_PR = REPO_ROOT / "scripts" / "auto-pr"

_FAIL_STUB = """\
#!/usr/bin/env bash
echo "STUB-REASON: the slice blew up in a way nobody anticipated" >&2
exit 7
"""

_OK_STUB = """\
#!/usr/bin/env bash
mkdir -p .ci
printf '%s\\n' \\
  '# Test selection manifest' \\
  '# Mode: targeted' \\
  '#' \\
  '# === CHANGED_SOURCE_FILES ===' \\
  '# === SELECTED_TESTS ===' \\
  'tests/test_x.py' > .ci/affected-tests.txt
exit 0
"""


def _tool_tree(tmp_path: Path, stub: str) -> Path:
    """A copy of scripts/ whose smart-test is ours to control.

    `.githooks/` comes along because auto-pr resolves brand-scrub.sh relative to
    its own repo root and refuses to publish without it — a tree missing it
    aborts at the title scrub, which is 14 lines ABOVE the code under test and
    looks exactly like the feature not working.
    """
    tool = tmp_path / "tooltree"
    shutil.copytree(REPO_ROOT / "scripts", tool / "scripts")
    shutil.copytree(REPO_ROOT / ".githooks", tool / ".githooks")
    smart = tool / "scripts" / "smart-test"
    smart.write_text(stub, encoding="utf-8")
    smart.chmod(0o755)
    return tool


def _run(auto_pr: Path, fake: Path, **extra_env: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for k in ("FORGEJO_USER", "FORGEJO_TOKEN", "AUTO_PR_SKIP_MANIFEST"):
        env.pop(k, None)
    env.update(
        FORGEJO_USER="u",
        FORGEJO_TOKEN="t",
        AUTO_PR_SIMULATE_OUTAGE="1",
        # Keep the real ledger out of it — these runs must not land rows in
        # .git/AUTOPR_HISTORY.jsonl, which the convergence audit reads.
        AUTOPR_RESULT_FILE=str(fake / ".git" / "AUTOPR_LAST_RESULT.json"),
        AUTOPR_HISTORY_FILE=str(fake / ".git" / "AUTOPR_HISTORY.jsonl"),
    )
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(auto_pr), "--foreground", "--title", "fix: x",
         "--description", "y"],
        cwd=str(fake), env=env, capture_output=True, text=True, timeout=180,
    )


def _skip_line(out: str) -> str:
    for line in out.splitlines():
        if "Manifest generation skipped" in line:
            return line
    return ""


def test_the_reason_smart_test_gave_reaches_the_operator(tmp_path: Path) -> None:
    """THE regression: stderr was discarded, so the cause was unrecoverable."""
    tool = _tool_tree(tmp_path, _FAIL_STUB)
    fake = _init_fake_repo(tmp_path)

    proc = _run(tool / "scripts" / "auto-pr", fake)
    out = proc.stdout + proc.stderr

    assert "STUB-REASON: the slice blew up" in out, (
        "smart-test's own explanation did not survive the call — the operator "
        f"still gets a guess.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_the_skip_names_the_exit_code_it_saw(tmp_path: Path) -> None:
    """A relayed message with no status is still missing half the evidence.

    Pinning the code separately from the text also stops the fix degrading into
    a second hardcoded sentence.
    """
    tool = _tool_tree(tmp_path, _FAIL_STUB)
    fake = _init_fake_repo(tmp_path)

    proc = _run(tool / "scripts" / "auto-pr", fake)
    line = _skip_line(proc.stdout + proc.stderr)

    assert line, f"no skip line at all:\n{proc.stdout}\n{proc.stderr}"
    assert "7" in line, (
        f"the skip does not report the exit status smart-test returned:\n{line}"
    )


def test_it_does_not_claim_a_missing_hypergumbo_it_did_not_check(
    tmp_path: Path,
) -> None:
    """The parenthetical was a guess, and on this path it is the wrong one."""
    tool = _tool_tree(tmp_path, _FAIL_STUB)
    fake = _init_fake_repo(tmp_path)

    proc = _run(tool / "scripts" / "auto-pr", fake)
    line = _skip_line(proc.stdout + proc.stderr)

    assert line, (
        "no skip line at all — without this the assertion below passes on an "
        f"empty string and pins nothing.\n{proc.stdout}\n{proc.stderr}"
    )
    assert "no stable hypergumbo" not in line, (
        "the run asserts a cause it never established — smart-test exited 7 "
        f"for its own stated reason.\n{line}"
    )


def test_a_successful_regen_is_not_reported_as_skipped(tmp_path: Path) -> None:
    """DIFFERENCE arm (L17). A warning on every run is a warning nobody reads."""
    tool = _tool_tree(tmp_path, _OK_STUB)
    fake = _init_fake_repo(tmp_path)

    proc = _run(tool / "scripts" / "auto-pr", fake)
    out = proc.stdout + proc.stderr

    assert "Regenerating test manifest" in out, (
        f"the success path was not taken at all:\n{proc.stdout}\n{proc.stderr}"
    )
    assert not _skip_line(out), f"a successful regen was reported as skipped:\n{out}"


def test_an_explicit_skip_is_still_an_explicit_skip(tmp_path: Path) -> None:
    """Second difference arm: AUTO_PR_SKIP_MANIFEST=1 runs no regen to explain."""
    tool = _tool_tree(tmp_path, _FAIL_STUB)
    fake = _init_fake_repo(tmp_path)

    proc = _run(tool / "scripts" / "auto-pr", fake, AUTO_PR_SKIP_MANIFEST="1")
    out = proc.stdout + proc.stderr

    assert "AUTO_PR_SKIP_MANIFEST=1" in out
    assert "STUB-REASON" not in out, (
        f"the stub ran despite the explicit skip:\n{out}"
    )
    assert not _skip_line(out), f"an explicit skip borrowed the failure line:\n{out}"


def test_end_to_end_a_foreign_cwd_refusal_is_the_reason_reported(
    tmp_path: Path,
) -> None:
    """The two fixes compose, against the REAL smart-test.

    A fixture repo is a foreign cwd, so WI-fopuh's guard fires on the real
    script; this asserts the operator is told THAT, rather than being pointed at
    a hypergumbo install that is fine.
    """
    fake = _init_fake_repo(tmp_path)

    proc = _run(REAL_AUTO_PR, fake)
    out = proc.stdout + proc.stderr

    assert "smart-test belongs to" in out, (
        "the real refusal did not reach the operator — this is the case the "
        f"residual was filed for.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
