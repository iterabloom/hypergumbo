# SPDX-License-Identifier: AGPL-3.0-or-later
"""``tag-release`` must prove it can push BEFORE it signs a tag.

THE DEFECT THIS PINS. Releasing v8.0.0, the script reached step 5 and printed
``✓ Created signed tag: v8.0.0``, then died at the push::

    remote: Invalid username or token. Password authentication is not
            supported for Git operations.
    fatal: Authentication failed for 'https://github.com/...'

GitHub removed password auth for Git in 2021, so an ``https`` origin with no
credential helper prompts for a password that can never work. The release was
then wedged in a state the script could not recover from on its own: a signed
local tag, nothing on the remote, and a step 3 that offers to *delete and
recreate* the very tag just signed. The fix is ordering — authenticate first,
sign second.

WHY THE TESTS EXECUTE THE FUNCTION rather than grep the source. A guard that
cannot be shown to fire is indistinguishable from one that matches nothing, and
an old-vs-new differential is vacuous if no input separates the two. Each test
below runs the SHIPPED ``verify_push_credentials`` body against stub ``git`` /
``gh`` binaries on PATH and asserts on its exit status, so a refactor that
silently stops probing fails here.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TAG_RELEASE = REPO_ROOT / "scripts" / "tag-release"

# Resolved BEFORE PATH is overridden, because the harness below deliberately
# puts nothing on PATH except its own stubs. Without this the interpreter
# itself becomes unfindable.
BASH = shutil.which("bash") or "/bin/bash"


def _extract_function() -> str:
    """Pull ``verify_push_credentials`` out; sourcing the script would run it."""
    text = TAG_RELEASE.read_text(encoding="utf-8")
    m = re.search(r"^verify_push_credentials\(\) \{\n.*?^\}$", text, re.S | re.M)
    assert m, "verify_push_credentials() not found in scripts/tag-release"
    return m.group(0)


def _stub(path: Path, name: str, body: str) -> None:
    """Write an executable stub named *name* into *path*."""
    # Absolute shebang, not `/usr/bin/env bash`: env resolves `bash` via PATH,
    # and PATH here is the stub dir alone, so the env form cannot start.
    p = path / name
    p.write_text(f"#!{BASH}\n" + body + "\n")
    p.chmod(0o755)


def _run(
    tmp_path: Path,
    *,
    origin_url: str,
    git_body: str,
    gh_body: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the shipped function against stubs, returning the result.

    ``set -euo pipefail`` is applied exactly as the real script has it, so a
    landmine specific to this repo is covered: under ``-e``, a bare
    ``VAR=$(cmd)`` aborts the script at the assignment when cmd exits non-zero,
    which would make the "cannot authenticate" arm unreachable. The function
    uses ``|| probe_exit=$?`` with an initialised variable to avoid that; if
    someone rewrites it into the bare form, these tests stop passing.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _stub(bindir, "git", git_body)
    if gh_body is not None:
        _stub(bindir, "gh", gh_body)

    script = "\n".join([
        "set -euo pipefail",
        "RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''",
        "DRY_RUN=false",
        'run() { if $DRY_RUN; then echo "[dry-run] $*"; else "$@"; fi; }',
        f'ORIGIN_URL="{origin_url}"',
        'ALLOWED_SEATS=("josh-iterabloom" "jgstern-agent")',
        _extract_function(),
        "verify_push_credentials",
    ])
    # PATH is the stub dir and NOTHING else. This has to be hermetic: gh is
    # genuinely installed at /usr/bin/gh on this box, so an earlier version of
    # this harness that appended /usr/bin let the "gh is not installed" case
    # fall through to the REAL gh, which blocked on an interactive
    # `gh auth login` until the timeout killed it. A test that reaches a real
    # credential tool is not testing the branch it claims to test. The function
    # shells out to nothing but git and gh; everything else it uses is a bash
    # builtin, so an empty-but-for-stubs PATH is sufficient.
    run_env = {
        "PATH": str(bindir),
        "HOME": str(tmp_path),
    }
    if env:
        run_env.update(env)
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True, text=True, timeout=30, env=run_env,
    )


# --- the probe succeeds: nothing to do ---


@pytest.mark.parametrize("origin", [
    "git@github.com:iterabloom/hypergumbo.git",
    "https://github.com/iterabloom/hypergumbo.git",
])
def test_probe_success_returns_zero(tmp_path: Path, origin: str) -> None:
    r = _run(tmp_path, origin_url=origin, git_body="exit 0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Authenticated to origin" in r.stdout


# --- the probe fails ---


def test_ssh_origin_failure_aborts_without_touching_gh(tmp_path: Path) -> None:
    """An ssh key problem is not something `gh auth login` can fix.

    The diagnostic names `ssh -T` but must NOT branch on its exit code: GitHub
    exits 1 even on success, so a working key would read as broken.
    """
    r = _run(
        tmp_path,
        origin_url="git@github.com:iterabloom/hypergumbo.git",
        git_body="exit 128",
        gh_body="echo 'gh MUST NOT be called for an ssh origin' >&2; exit 1",
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ssh -T git@github.com" in r.stdout
    assert "MUST NOT be called" not in r.stderr


def test_https_origin_runs_gh_auth_then_succeeds(tmp_path: Path) -> None:
    """The whole point: an unauthenticated https origin self-heals.

    git fails until `gh auth setup-git` has run, exactly as it would in life.
    """
    marker = tmp_path / "setup-git-ran"
    r = _run(
        tmp_path,
        origin_url="https://github.com/iterabloom/hypergumbo.git",
        git_body=f'[[ -f "{marker}" ]] && exit 0 || exit 128',
        gh_body=(
            # `: > file` not `touch`: PATH holds only the stubs, so the stub
            # itself may use nothing but builtins and redirection.
            f'if [[ "$1" == "auth" && "$2" == "setup-git" ]]; then : > "{marker}"; exit 0; fi\n'
            'if [[ "$1" == "auth" && "$2" == "status" ]]; then exit 1; fi\n'
            'if [[ "$1" == "auth" && "$2" == "login" ]]; then exit 0; fi\n'
            'if [[ "$1" == "api" ]]; then echo "josh-iterabloom"; exit 0; fi\n'
            "exit 0"
        ),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert marker.exists(), "gh auth setup-git was never run"
    assert "Authenticated as josh-iterabloom (paid seat)" in r.stdout


def test_https_origin_without_gh_installed_aborts(tmp_path: Path) -> None:
    """No gh and no credential helper — say so and name the ssh escape."""
    r = _run(
        tmp_path,
        origin_url="https://github.com/iterabloom/hypergumbo.git",
        git_body="exit 128",
        gh_body=None,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "git remote set-url origin git@github.com" in r.stdout


def test_gh_auth_that_does_not_fix_it_still_aborts(tmp_path: Path) -> None:
    """gh can exit 0 and the push still be impossible. Re-probe, don't assume.

    Without the second probe this returns success and the caller signs a tag it
    cannot push — the original defect, one layer down.
    """
    r = _run(
        tmp_path,
        origin_url="https://github.com/iterabloom/hypergumbo.git",
        git_body="exit 128",
        gh_body="exit 0",
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Still cannot authenticate" in r.stdout


# --- identity ---


def test_wrong_account_warns_but_does_not_block(tmp_path: Path) -> None:
    """Advisory by design: an ssh push needs no gh login at all, so an
    unrecognised gh identity must not veto an otherwise working credential."""
    r = _run(
        tmp_path,
        origin_url="https://github.com/iterabloom/hypergumbo.git",
        git_body="exit 0",
        gh_body='if [[ "$1" == "api" ]]; then echo "some-other-user"; exit 0; fi\nexit 0',
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "some-other-user" in r.stdout
    assert "paid seats" in r.stdout


@pytest.mark.parametrize("var", ["GH_TOKEN", "GITHUB_TOKEN"])
def test_environment_token_is_flagged(tmp_path: Path, var: str) -> None:
    """A token in the env silently outranks `gh auth login`.

    This is the trap that would let the human log in as themselves and still
    push as the agent seat.
    """
    r = _run(
        tmp_path,
        origin_url="https://github.com/iterabloom/hypergumbo.git",
        git_body="exit 0",
        gh_body='if [[ "$1" == "api" ]]; then echo "jgstern-agent"; exit 0; fi\nexit 0',
        env={var: "xxx"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GH_TOKEN/GITHUB_TOKEN is set" in r.stdout


def test_hg_github_token_is_not_flagged(tmp_path: Path) -> None:
    """Negative control. HG_GITHUB_TOKEN is this repo's own variable and is NOT
    one gh reads, so warning about it would be a false alarm on every run — the
    kind of noise that gets a real warning ignored."""
    r = _run(
        tmp_path,
        origin_url="https://github.com/iterabloom/hypergumbo.git",
        git_body="exit 0",
        gh_body='if [[ "$1" == "api" ]]; then echo "jgstern-agent"; exit 0; fi\nexit 0',
        env={"HG_GITHUB_TOKEN": "xxx"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GH_TOKEN/GITHUB_TOKEN is set" not in r.stdout


# --- ordering: the actual bug ---


def test_credential_check_precedes_tag_creation() -> None:
    """The ordering IS the fix; a correct function called too late is the bug.

    Asserted on the shipped file because ordering is a property of the script,
    not of the function under test above.
    """
    text = TAG_RELEASE.read_text(encoding="utf-8")
    verify_at = text.index('echo "━━━ Step 4: Verify push credentials ━━━"')
    sign_at = text.index('echo "━━━ Step 5: Create signed tag ━━━"')
    push_at = text.index('echo "━━━ Step 6: Push tag ━━━"')
    assert verify_at < sign_at < push_at, (
        "verify_push_credentials must run BEFORE the tag is signed — a tag "
        "that cannot be pushed wedges the release"
    )
    call_at = text.index("if ! verify_push_credentials; then")
    assert call_at < sign_at, "the guard is defined but never called before signing"
