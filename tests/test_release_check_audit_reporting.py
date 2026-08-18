# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release gate must REPORT a dependency finding, not die on it (WI-zisoj).

``scripts/release-check`` sets ``set -euo pipefail`` at line 18 and then wrote::

    AUDIT_OUTPUT=$(pip-audit ... )
    AUDIT_EXIT=$?
    if [ $AUDIT_EXIT -eq 0 ]; then pass ...; else echo "$AUDIT_OUTPUT"; fail ...; fi

Under ``set -e`` a simple assignment whose command substitution exits non-zero
terminates the script AT THE ASSIGNMENT. So ``AUDIT_EXIT=$?`` never ran and the
entire if/else — including the ``echo "$AUDIT_OUTPUT"`` that prints the
findings — was unreachable whenever pip-audit was non-clean. Every check after
it (secrets scan, coverage, docs sync, tag state) was skipped too.

The script's own comment says the opposite of what it did: "pip-audit's exit
code is the gate: 0 = no vulnerabilities, 1 = found. Anything else (network,
parse, unsupported package) is a real failure we want to surface, not paper
over."

Observed 2026-08-17: the captured report was 31 lines and ended mid-section at
"  Running pip-audit...", exit 1, no findings and no verdict. Standalone,
pip-audit reported 6 vulnerabilities. An operator sees that same truncated
output whether the finding is dev-box noise or a real shipped-dependency CVE,
which is the part that makes this a defect rather than an annoyance.

THESE TESTS EXECUTE THE SHIPPED BLOCK rather than pattern-matching it, because
a guard that cannot be SHOWN to fire is indistinguishable from one matching
nothing. The block is extracted from the real script, run under the real
``set -euo pipefail`` against a stub ``pip-audit``, and the differential test
below reconstructs the OLD form and proves it behaves differently — without
that, "the fixed block reports findings" would be satisfiable by a block that
reports findings for reasons unrelated to the fix.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_CHECK = REPO_ROOT / "scripts" / "release-check"

STUB_FINDING = "Found 6 known vulnerabilities, ignored 7 in 2 packages"
LATER_MARKER = "LATER_CHECK_RAN"


def _extract_audit_block() -> str:
    """Return the shipped audit block: the pip-audit call through its ``fi``.

    Anchored on the assignment and the closing ``fi`` rather than on line
    numbers, so the test follows the script when surrounding sections move.
    """
    text = RELEASE_CHECK.read_text()
    # Anchored on the INITIALIZER, not on the pip-audit call. `AUDIT_EXIT=0` is
    # load-bearing under `set -u`: on a clean audit the `||` branch never runs,
    # so without it `[ $AUDIT_EXIT -eq 0 ]` dies on an unbound variable — and it
    # would die only on the CLEAN path, which is not the case anyone is
    # thinking about when they edit this block. Anchoring here means deleting
    # the initializer raises ValueError from this helper rather than silently
    # narrowing what the tests cover.
    start = text.index("AUDIT_EXIT=0")
    end = text.index("\nfi\n", start) + len("\nfi\n")
    return text[start:end]


def _run_block(block: str, *, audit_exit: int, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Execute *block* under the script's real shell options, with stubs.

    ``pass`` / ``fail`` are stubbed to plain echoes so the harness does not
    depend on the rest of release-check, and a marker line follows the block so
    "did execution continue past the audit" is observable rather than inferred.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub = stub_dir / "pip-audit"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "{STUB_FINDING}"\n'
        f"exit {audit_exit}\n"
    )
    stub.chmod(0o755)

    harness = tmp_path / "harness.sh"
    harness.write_text(
        textwrap.dedent(
            """\
            set -euo pipefail
            FAILED=0
            pass() { echo "PASS: $1"; }
            fail() { echo "FAIL: $1"; FAILED=$((FAILED + 1)); }
            """
        )
        + block
        + f'\necho "{LATER_MARKER}"\n'
    )
    env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )


def test_a_finding_is_reported_and_later_checks_still_run(tmp_path):
    """The defect, stated as the behaviour it broke."""
    result = _run_block(_extract_audit_block(), audit_exit=1, tmp_path=tmp_path)

    assert STUB_FINDING in result.stdout, (
        "the findings never reached the operator — the gate died at the "
        f"assignment. stdout was:\n{result.stdout}"
    )
    assert "FAIL:" in result.stdout, "a non-clean audit must be recorded as a failure"
    assert LATER_MARKER in result.stdout, (
        "execution stopped at the audit, so every later check (secrets scan, "
        "coverage, docs sync, tag state) was skipped"
    )


def test_control_a_clean_audit_still_passes(tmp_path):
    """POSITIVE CONTROL: the fix must not turn every audit into a failure.

    Without this, `AUDIT_EXIT` defaulting to a non-zero value — or the branch
    being inverted — would satisfy the test above while making the gate
    useless in the opposite direction.
    """
    result = _run_block(_extract_audit_block(), audit_exit=0, tmp_path=tmp_path)

    assert "PASS:" in result.stdout, f"a clean audit must pass; got:\n{result.stdout}"
    assert "FAIL:" not in result.stdout
    assert LATER_MARKER in result.stdout


@pytest.mark.parametrize("audit_exit", [1, 2, 127])
def test_any_non_zero_audit_exit_is_surfaced(tmp_path, audit_exit):
    """The script's own comment demands this.

    "Anything else (network, parse, unsupported package) is a real failure we
    want to surface, not paper over." Exit 2 and 127 are not "vulnerabilities
    found" — they are the audit not having run — and reporting them as a clean
    pass would be the worst possible reading.
    """
    result = _run_block(
        _extract_audit_block(), audit_exit=audit_exit, tmp_path=tmp_path
    )

    assert "FAIL:" in result.stdout
    assert str(audit_exit) in result.stdout, (
        "the exit code must appear in the failure message so the operator can "
        "tell 'vulnerabilities found' apart from 'the audit could not run'"
    )
    assert LATER_MARKER in result.stdout


def test_control_the_old_form_really_did_behave_differently(tmp_path):
    """DIFFERENTIAL CONTROL, and the load-bearing test in this file.

    Reconstruct the pre-fix shape and prove the harness separates the two. An
    old-vs-new differential with no input that distinguishes the rules is
    vacuous — if the buggy block also reported findings here, then the tests
    above would be passing for some reason other than the fix, and this file
    would be decoration.
    """
    fixed = _extract_audit_block()
    old = (
        fixed.replace(") || AUDIT_EXIT=$?", ")\nAUDIT_EXIT=$?")
        .replace("AUDIT_EXIT=0\n", "", 1)
    )
    assert old != fixed, (
        "could not reconstruct the pre-fix form — the fix's shape changed and "
        "this control needs updating, not deleting"
    )

    result = _run_block(old, audit_exit=1, tmp_path=tmp_path)

    assert STUB_FINDING not in result.stdout, (
        "the old form printed the findings, so this file proves nothing"
    )
    assert LATER_MARKER not in result.stdout, (
        "the old form continued past the audit, so this file proves nothing"
    )
    assert result.returncode != 0
