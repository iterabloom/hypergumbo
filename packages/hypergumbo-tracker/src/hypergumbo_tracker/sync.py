# SPDX-License-Identifier: MPL-2.0
"""Streamlined PR workflow for tracker-only changes.

Provides ``htrac sync`` — a purpose-built command that pushes tracker ops
files via a lightweight PR workflow: branch → commit → push → poll CI →
merge → cleanup.  Completes in ~45-60 seconds vs ~3.5 minutes for the
general-purpose ``auto-pr`` script.

Design:
- All git calls go through ``_git()`` for testability (single mock point).
- All Forgejo API calls go through ``_api_call()`` using only stdlib
  ``urllib.request`` (no ``requests`` dependency).
- Gate files (``.git/TRACKER_SYNC_PENDING``) provide mutual exclusion with
  ``auto-pr`` (which uses ``.git/PR_PENDING``).
- Preflight checks fail fast on the first problem (sequential, short-circuit).
- ``do_sync()`` uses try/finally for gate file and branch cleanup.

See the plan document for the full design specification.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 — required for git subprocess calls
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class PreflightResult:
    """Result of pre-sync validation checks.

    Attributes:
        ok: True if all checks passed and sync can proceed.
        error: Human-readable error message if ok is False.
        repo_root: Resolved git repository root.
        git_dir: Path to .git directory.
        original_branch: Branch name to restore after sync.
        changed_files: List of dirty tracker file paths (relative to repo root).
        api_base: Forgejo API base URL for this repo.
        forgejo_user: Forgejo username from environment.
        forgejo_token: Forgejo API token from environment.
    """

    ok: bool
    error: str = ""
    repo_root: Path | None = None
    git_dir: Path | None = None
    original_branch: str = ""
    changed_files: list[str] = field(default_factory=list)
    api_base: str = ""
    forgejo_user: str = ""
    forgejo_token: str = ""


@dataclass
class SyncResult:
    """Result of the full sync workflow.

    Attributes:
        success: True if sync completed and PR was merged.
        pr_number: Forgejo PR number (0 if not created).
        pr_url: Full URL to the merged PR.
        files_synced: Number of tracker files committed.
        error: Human-readable error if success is False.
        exit_code: Process exit code (0=success, 1=user error, 2=timeout).
    """

    success: bool
    pr_number: int = 0
    pr_url: str = ""
    files_synced: int = 0
    error: str = ""
    exit_code: int = 0


# ---------------------------------------------------------------------------
# Pure / simple helpers
# ---------------------------------------------------------------------------


def _load_env(repo_root: Path) -> dict[str, str]:
    """Parse ``.env`` file into a dict.

    Handles ``KEY=VALUE`` lines, strips surrounding quotes from values,
    and ignores comments (lines starting with ``#``) and blank lines.
    Does NOT modify ``os.environ``.
    """
    env_path = repo_root / ".env"
    result: dict[str, str] = {}
    if not env_path.is_file():
        return result

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes (single or double)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value

    return result


def _detect_api_base(repo_root: Path) -> str:
    """Extract Forgejo API base URL from git remote ``origin``.

    Parses the remote URL (HTTPS or SSH) and returns the API endpoint,
    e.g. ``https://codeberg.org/api/v1/repos/iterabloom/hypergumbo``.

    Returns empty string if the remote URL cannot be parsed.
    """
    result = _git(repo_root, "remote", "get-url", "origin", check=False)
    if result.returncode != 0:
        return ""

    url = result.stdout.strip()

    # HTTPS: https://codeberg.org/owner/repo.git
    m = re.match(r"https?://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        host, owner, repo = m.group(1), m.group(2), m.group(3)
        return f"https://{host}/api/v1/repos/{owner}/{repo}"

    # SSH: git@codeberg.org:owner/repo.git
    m = re.match(r"git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        host, owner, repo = m.group(1), m.group(2), m.group(3)
        return f"https://{host}/api/v1/repos/{owner}/{repo}"

    return ""


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result.

    Single mockable entry point for all git subprocess calls in this module.

    Args:
        repo_root: Working directory for the git command.
        *args: Git subcommand and arguments.
        check: If True, raise CalledProcessError on non-zero exit.

    Returns:
        CompletedProcess with stdout/stderr captured as text.
    """
    return subprocess.run(  # noqa: S603  # nosec B603, B607
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        check=check,
    )


def _api_call(
    method: str,
    url: str,
    token: str,
    data: dict[str, Any] | None = None,
    timeout: int = 15,
) -> tuple[int, dict[str, Any] | list[Any] | None]:
    """Make a Forgejo API call via ``urllib.request``.

    Args:
        method: HTTP method (GET, POST, PATCH, etc.).
        url: Full API URL.
        token: Bearer token for authentication.
        data: JSON request body (optional).
        timeout: Request timeout in seconds.

    Returns:
        ``(http_status, parsed_json)`` on success.
        ``(0, None)`` on network/timeout error.
    """
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, method=method)  # noqa: S310  # nosec B310
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            status = resp.status
            resp_body = resp.read().decode()
            parsed = json.loads(resp_body) if resp_body else None
            return (status, parsed)
    except HTTPError as e:
        try:
            err_body = e.read().decode()
            parsed = json.loads(err_body) if err_body else None
        except Exception:
            parsed = None
        return (e.code, parsed)
    except (URLError, TimeoutError, OSError):
        return (0, None)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _find_open_pr(
    api_base: str,
    token: str,
    branch: str,
) -> tuple[int, str] | None:
    """Find an open PR by head branch name.

    Args:
        api_base: Forgejo API base URL for the repo.
        token: API bearer token.
        branch: Head branch name to search for.

    Returns:
        ``(pr_number, head_sha)`` if found, else ``None``.
    """
    status, body = _api_call(
        "GET",
        f"{api_base}/pulls?state=open&limit=50",
        token,
    )
    if status == 0 or body is None or not isinstance(body, list):
        return None

    for pr in body:
        head = pr.get("head", {})
        if head.get("ref") == branch or head.get("label") == branch:
            return (pr["number"], head.get("sha", ""))

    return None


def _poll_ci(
    api_base: str,
    token: str,
    head_sha: str,
    poll_interval: int = 10,
    timeout: int = 300,
) -> str:
    """Poll CI status until terminal state or timeout.

    Watches for the commit status context that signals tracker CI completion.
    Implements sole-holdout bypass: if all other contexts have succeeded and
    only one remains pending for >60s, treat it as success (Scenario A from
    the auto-pr design).

    Args:
        api_base: Forgejo API base URL.
        token: API bearer token.
        head_sha: Commit SHA to poll status for.
        poll_interval: Seconds between polls.
        timeout: Maximum seconds to wait.

    Returns:
        ``"success"``, ``"failure"``, or ``"timeout"``.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        status_code, body = _api_call(
            "GET",
            f"{api_base}/commits/{head_sha}/status",
            token,
        )
        if status_code == 0 or body is None or not isinstance(body, dict):
            time.sleep(poll_interval)
            continue

        state = body.get("state", "pending")
        statuses = body.get("statuses") or []

        if state == "success":
            return "success"
        if state == "failure" or state == "error":
            return "failure"

        # Sole-holdout bypass: if all but one context succeeded and we've
        # been waiting long enough, treat as success
        if len(statuses) > 1:
            non_success = [
                s for s in statuses if s.get("status") != "success"
            ]
            if len(non_success) == 1 and time.monotonic() > deadline - (timeout - 60):
                return "success"

        time.sleep(poll_interval)

    return "timeout"


def _merge_pr(
    api_base: str,
    token: str,
    pr_num: int,
) -> bool:
    """Merge a PR via fast-forward-only merge.

    Handles the idempotent case where the PR is already merged.

    Args:
        api_base: Forgejo API base URL.
        token: API bearer token.
        pr_num: Pull request number.

    Returns:
        True if merge succeeded or PR was already merged.
    """
    status, body = _api_call(
        "POST",
        f"{api_base}/pulls/{pr_num}/merge",
        token,
        data={"Do": "fast-forward-only"},
    )

    if status == 200 or status == 204:
        return True

    # Check if already merged (idempotent)
    if status == 405 or status == 409:
        check_status, check_body = _api_call(
            "GET",
            f"{api_base}/pulls/{pr_num}",
            token,
        )
        if (
            check_status == 200
            and isinstance(check_body, dict)
            and check_body.get("merged")
        ):
            return True

    return False


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


_TRACKER_PATHS = (
    ".agent/tracker/.ops/",
    ".agent/tracker-workspace/.ops/",
    ".agent/tracker-workspace/tui_preferences.json",
)

_TRACKER_PATH_RE = re.compile(
    r"^\.agent/tracker(-workspace)?/("
    r"\.ops/|tui_preferences\.json"
    r")"
)


def preflight_check(repo_root: Path) -> PreflightResult:
    """Run sequential pre-sync checks, fail-fast on first problem.

    Checks (in order):
    1. Git repo: verify .git directory exists.
    2. Gate files: abort if auto-pr or sync is already in flight.
    3. Current branch: record for later restore.
    4. No staged non-tracker files: abort if non-tracker files are staged.
    5. Dirty tracker files: check for changes to commit.
    6. Credentials: FORGEJO_TOKEN from .env or environment.
    7. Git identity: user.name and user.email configured.
    8. Remote exists: origin remote is configured.
    9. API base: parse Forgejo API URL from remote.

    Returns:
        PreflightResult with ok=True if all checks pass.
    """
    # 1. Git repo
    git_dir_result = _git(repo_root, "rev-parse", "--git-dir", check=False)
    if git_dir_result.returncode != 0:
        return PreflightResult(ok=False, error="not a git repository")
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir

    # 2. Gate files
    pr_pending = git_dir / "PR_PENDING"
    sync_pending = git_dir / "TRACKER_SYNC_PENDING"
    if pr_pending.exists():
        return PreflightResult(
            ok=False,
            error="auto-pr in flight (PR_PENDING exists). Wait for it to complete.",
        )
    if sync_pending.exists():
        return PreflightResult(
            ok=False,
            error="tracker sync already in progress (TRACKER_SYNC_PENDING exists).",
        )

    # 3. Current branch
    branch_result = _git(repo_root, "branch", "--show-current", check=False)
    if branch_result.returncode != 0:
        return PreflightResult(
            ok=False,
            error="could not determine current branch (detached HEAD?)",
        )
    original_branch = branch_result.stdout.strip()

    # 4. No staged non-tracker files
    staged_result = _git(
        repo_root, "diff", "--cached", "--name-only", check=False
    )
    if staged_result.returncode == 0 and staged_result.stdout.strip():
        staged_files = staged_result.stdout.strip().splitlines()
        non_tracker = [
            f for f in staged_files if not _TRACKER_PATH_RE.match(f)
        ]
        if non_tracker:
            return PreflightResult(
                ok=False,
                error=(
                    f"staged non-tracker files: {', '.join(non_tracker)}. "
                    "Unstage them or use auto-pr."
                ),
            )

    # 5. Dirty tracker files
    status_args = ["status", "--porcelain", "--"]
    status_args.extend(_TRACKER_PATHS)
    status_result = _git(repo_root, *status_args, check=False)
    changed_files: list[str] = []
    if status_result.returncode == 0 and status_result.stdout.strip():
        for line in status_result.stdout.strip().splitlines():
            # Porcelain format: XY filename
            fname = line[3:].strip()
            if fname:
                changed_files.append(fname)

    if not changed_files:
        return PreflightResult(
            ok=True,
            error="",
            repo_root=repo_root,
            git_dir=git_dir,
            original_branch=original_branch,
            changed_files=[],
        )

    # 6. Credentials
    env_vars = _load_env(repo_root)
    forgejo_token = env_vars.get("FORGEJO_TOKEN") or os.environ.get(
        "FORGEJO_TOKEN", ""
    )
    forgejo_user = env_vars.get("FORGEJO_USER") or os.environ.get(
        "FORGEJO_USER", ""
    )
    if not forgejo_token:
        return PreflightResult(
            ok=False,
            error="FORGEJO_TOKEN not found in .env or environment",
        )

    # 7. Git identity
    name_result = _git(repo_root, "config", "user.name", check=False)
    email_result = _git(repo_root, "config", "user.email", check=False)
    if not name_result.stdout.strip() or not email_result.stdout.strip():
        return PreflightResult(
            ok=False,
            error=(
                "git identity not configured. Run:\n"
                '  git config --global user.name "Your Name"\n'
                '  git config --global user.email "you@example.com"'
            ),
        )

    # 8. Remote exists
    remote_result = _git(repo_root, "remote", "get-url", "origin", check=False)
    if remote_result.returncode != 0:
        return PreflightResult(
            ok=False, error="no remote 'origin' configured"
        )

    # 9. API base
    api_base = _detect_api_base(repo_root)
    if not api_base:
        return PreflightResult(
            ok=False,
            error="could not parse Forgejo API URL from origin remote",
        )

    return PreflightResult(
        ok=True,
        repo_root=repo_root,
        git_dir=git_dir,
        original_branch=original_branch,
        changed_files=changed_files,
        api_base=api_base,
        forgejo_user=forgejo_user,
        forgejo_token=forgejo_token,
    )


def do_sync(
    repo_root: Path,
    preflight: PreflightResult,
    base_branch: str = "dev",
    ci_poll_interval: int = 10,
    ci_timeout: int = 300,
) -> SyncResult:
    """Execute the full sync workflow: branch → commit → push → poll → merge.

    Uses try/finally to ensure gate file cleanup and branch restoration
    regardless of errors or timeouts.

    Args:
        repo_root: Git repository root.
        preflight: Validated preflight result (must have ok=True).
        base_branch: Target branch for the PR (default: dev).
        ci_poll_interval: Seconds between CI status polls.
        ci_timeout: Maximum seconds to wait for CI.

    Returns:
        SyncResult describing the outcome.
    """
    assert preflight.ok, "preflight must pass before calling do_sync"
    assert preflight.git_dir is not None

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    sync_branch = f"tracker-sync/{timestamp}"
    gate_file = preflight.git_dir / "TRACKER_SYNC_PENDING"
    file_count = len(preflight.changed_files)

    try:
        # 1. Create branch
        result = _git(repo_root, "checkout", "-b", sync_branch, check=False)
        if result.returncode != 0:
            return SyncResult(
                success=False,
                error=f"failed to create branch {sync_branch}: {result.stderr.strip()}",
                exit_code=1,
            )

        # 2. Stage tracker files
        stage_args = ["add", "--"]
        stage_args.extend(_TRACKER_PATHS)
        _git(repo_root, *stage_args, check=False)

        # 3. Commit with sign-off
        commit_msg = f"tracker: sync {file_count} file(s)"
        commit_result = _git(
            repo_root, "commit", "-s", "-m", commit_msg, check=False
        )
        if commit_result.returncode != 0:
            return SyncResult(
                success=False,
                error=f"commit failed: {commit_result.stderr.strip()}",
                exit_code=1,
            )

        # 4. Create gate file
        gate_file.write_text("sync\n")

        # 5. Push with retries
        push_ref = f"HEAD:refs/for/{base_branch}/{sync_branch}"
        push_title = f"tracker: sync {file_count} file(s)"
        push_success = False
        cred_helper = (
            f"!f() {{ echo username={preflight.forgejo_user}; "
            f"echo password={preflight.forgejo_token}; }}; f"
        )

        for attempt in range(1, 4):
            push_result = _git(
                repo_root,
                "-c", f"credential.helper={cred_helper}",
                "push", "origin", push_ref,
                "-o", f"title={push_title}",
                "-o", "description=Automated tracker data sync",
                check=False,
            )
            if push_result.returncode == 0:
                push_success = True
                break
            if attempt < 3:
                time.sleep(5)

        if not push_success:
            return SyncResult(
                success=False,
                error="push failed after 3 attempts",
                exit_code=1,
            )

        # 6. Find PR (with brief initial delay)
        time.sleep(2)
        pr_info = _find_open_pr(
            preflight.api_base, preflight.forgejo_token, sync_branch
        )
        if pr_info is None:
            return SyncResult(
                success=False,
                error=f"could not find open PR for branch {sync_branch}",
                exit_code=1,
            )
        pr_num, head_sha = pr_info

        # 7. Update gate file with PR number
        gate_file.write_text(f"{pr_num}\n")

        # 8. Poll CI
        ci_result = _poll_ci(
            preflight.api_base,
            preflight.forgejo_token,
            head_sha,
            poll_interval=ci_poll_interval,
            timeout=ci_timeout,
        )
        if ci_result == "failure":
            return SyncResult(
                success=False,
                pr_number=pr_num,
                error="CI failed",
                exit_code=1,
            )
        if ci_result == "timeout":
            return SyncResult(
                success=False,
                pr_number=pr_num,
                error=f"CI timed out after {ci_timeout}s",
                exit_code=2,
            )

        # 9. Merge PR
        # Extract repo slug from api_base for URL construction
        slug_match = re.search(r"/repos/(.+)$", preflight.api_base)
        repo_slug = slug_match.group(1) if slug_match else ""

        merged = _merge_pr(
            preflight.api_base, preflight.forgejo_token, pr_num
        )
        if not merged:
            return SyncResult(
                success=False,
                pr_number=pr_num,
                error="merge failed (branch may have diverged)",
                exit_code=1,
            )

        # Construct PR URL
        host_match = re.match(r"https?://([^/]+)/", preflight.api_base)
        host = host_match.group(1) if host_match else "codeberg.org"
        pr_url = f"https://{host}/{repo_slug}/pulls/{pr_num}"

        return SyncResult(
            success=True,
            pr_number=pr_num,
            pr_url=pr_url,
            files_synced=file_count,
            exit_code=0,
        )

    finally:
        # 10. Cleanup
        # Remove gate file
        if gate_file.exists():
            gate_file.unlink()

        # Restore original branch
        _git(repo_root, "checkout", preflight.original_branch, check=False)

        # Pull latest (non-fatal)
        _git(repo_root, "pull", "origin", base_branch, check=False)

        # Delete sync branch (non-fatal)
        _git(repo_root, "branch", "-D", sync_branch, check=False)
