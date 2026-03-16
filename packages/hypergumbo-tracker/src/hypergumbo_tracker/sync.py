# SPDX-License-Identifier: MPL-2.0
"""Streamlined PR workflow for tracker-only changes.

Provides ``htrac sync`` — a purpose-built command that pushes tracker ops
files via a lightweight PR workflow: plumbing commit → push → poll CI →
merge → cleanup.  Completes in ~45-60 seconds vs ~3.5 minutes for the
general-purpose ``auto-pr`` script.

Also provides ``pending_sync_lines()`` for auto-sync: counts pending
uncommitted ops-file changes so the CLI can trigger sync after a
configurable threshold of accumulated changes.

Design:
- All git calls go through ``_git()`` for testability (single mock point).
  ``_git()`` accepts an optional ``env`` dict for plumbing calls that need
  custom environment variables (e.g. ``GIT_INDEX_FILE``).
- All Forgejo API calls go through ``_api_call()`` using only stdlib
  ``urllib.request`` (no ``requests`` dependency).
- Gate files (``.git/TRACKER_SYNC_PENDING``) provide mutual exclusion with
  ``auto-pr`` (which uses ``.git/PR_PENDING``).
- Preflight checks fail fast on the first problem (sequential, short-circuit).
- ``do_sync()`` uses git plumbing (read-tree/write-tree/commit-tree) with a
  temporary index file to build the sync commit on top of ``origin/dev``
  *without* checking out a branch.  This is critical: the editable install
  means the Python interpreter uses whatever branch is checked out, so
  switching branches during a running bakeoff or test would break things.
  The plumbing approach builds the commit in a separate index, creates a
  branch ref with ``update-ref``, pushes, then cleans up — the working tree
  never changes.  try/finally ensures gate file and temp index cleanup.

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

from hypergumbo_tracker.sync_log import init_sync_log, write_log
from hypergumbo_tracker.validation import validate_all


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
        push_remote: Git remote to push to (``origin`` or ``selfh`` during failover).
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
    push_remote: str = "origin"


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


@dataclass
class _FailoverState:
    """CI failover state parsed from ``.git/CI_FAILOVER_ACTIVE``."""

    active: bool = False
    api_base: str = ""
    push_remote: str = "origin"
    token: str = ""
    user: str = ""


def _detect_failover(git_dir: Path, env_vars: dict[str, str]) -> _FailoverState:
    """Detect active CI failover and return override state.

    Reads the ``.git/CI_FAILOVER_ACTIVE`` JSON flag file written by
    ``ci-failover engage``. When active, overrides API base, credentials,
    and push remote to target the self-hosted Forgejo instance.
    """
    flag_file = git_dir / "CI_FAILOVER_ACTIVE"
    if not flag_file.is_file():
        return _FailoverState()

    try:
        data = json.loads(flag_file.read_text())
    except (json.JSONDecodeError, OSError):
        return _FailoverState()

    local_url = data.get("selfhosted_forgejo_url", "")
    local_repo = data.get("selfhosted_forgejo_repo", "")
    if not local_url or not local_repo:
        return _FailoverState()

    token = (
        env_vars.get("SELFHOSTED_FORGEJO_TOKEN")
        or os.environ.get("SELFHOSTED_FORGEJO_TOKEN", "")
        or env_vars.get("FORGEJO_TOKEN")
        or os.environ.get("FORGEJO_TOKEN", "")
    )
    user = (
        env_vars.get("SELFHOSTED_FORGEJO_USER")
        or os.environ.get("SELFHOSTED_FORGEJO_USER", "")
        or env_vars.get("FORGEJO_USER")
        or os.environ.get("FORGEJO_USER", "")
    )

    return _FailoverState(
        active=True,
        api_base=f"{local_url}/api/v1/repos/{local_repo}",
        push_remote="selfh",
        token=token,
        user=user,
    )


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
    # Also handles embedded credentials: https://user:token@host/...
    m = re.match(
        r"https?://(?:[^@/]+@)?([^/]+)/([^/]+)/([^/]+?)(?:\.git)?$", url
    )
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
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result.

    Single mockable entry point for all git subprocess calls in this module.

    Args:
        repo_root: Working directory for the git command.
        *args: Git subcommand and arguments.
        check: If True, raise CalledProcessError on non-zero exit.
        env: Extra environment variables merged with os.environ.

    Returns:
        CompletedProcess with stdout/stderr captured as text.
    """
    run_env = None
    if env is not None:
        run_env = {**os.environ, **env}
    return subprocess.run(  # noqa: S603  # nosec B603, B607
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        check=check,
        env=run_env,
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
    *,
    title: str = "",
) -> tuple[int, str] | None:
    """Find an open PR by head branch name or title.

    When pushing via Forgejo's AGit flow (``refs/for/``), the PR's
    ``head.ref`` is ``refs/pull/N/head`` rather than the branch name.
    To handle this, we also match by title when provided.

    Args:
        api_base: Forgejo API base URL for the repo.
        token: API bearer token.
        branch: Head branch name to search for.
        title: PR title to match as fallback (for AGit flow PRs).

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

    # Fallback: match by title (AGit flow sets refs/pull/N/head as ref)
    if title:
        for pr in body:
            if pr.get("title") == title:
                head = pr.get("head", {})
                return (pr["number"], head.get("sha", ""))

    return None


def _poll_ci(
    api_base: str,
    token: str,
    head_sha: str,
    poll_interval: int = 10,
    timeout: int = 600,
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

        # Don't trust aggregate state when no statuses exist yet —
        # CI hasn't started.  Wait for at least one status to appear.
        if not statuses:
            time.sleep(poll_interval)
            continue

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


def _log(msg: str) -> None:
    """Write a sync diagnostic message to stderr and the sync log file."""
    write_log(msg)


def _check_pr_merged(
    api_base: str, token: str, pr_num: int
) -> bool:
    """Check whether a PR is merged by fetching its current state."""
    check_status, check_body = _api_call(
        "GET",
        f"{api_base}/pulls/{pr_num}",
        token,
    )
    return (
        check_status == 200
        and isinstance(check_body, dict)
        and bool(check_body.get("merged"))
    )


def _merge_pr(
    api_base: str,
    token: str,
    pr_num: int,
) -> bool:
    """Merge a PR, cascading through merge strategies.

    Tries fast-forward-only first (cleanest history), then rebase
    (preserves commit identity), then merge commit (always works).
    AGit-flow PRs (created via ``refs/for/``) lack a real branch,
    which causes Forgejo's fast-forward merge to silently fail
    (HTTP 200 but ``merged: false``).

    After each attempt, verifies the PR is actually merged before
    declaring success.

    Args:
        api_base: Forgejo API base URL.
        token: API bearer token.
        pr_num: Pull request number.

    Returns:
        True if merge succeeded or PR was already merged.
    """
    merge_url = f"{api_base}/pulls/{pr_num}/merge"

    strategies = [
        ("fast-forward-only", "fast-forward"),
        ("rebase", "rebase"),
        ("merge", "merge commit"),
    ]

    for do_value, label in strategies:
        status, body = _api_call(
            "POST", merge_url, token, data={"Do": do_value},
        )

        if status == 204:
            _log(f"merged via {label}")
            return True

        if status == 200:
            # Forgejo sometimes returns 200 with the PR object
            # without actually merging (especially for AGit-flow PRs
            # with fast-forward-only).  Verify before declaring success.
            if _check_pr_merged(api_base, token, pr_num):
                _log(f"merged via {label}")
                return True
            _log(f"{label} returned 200 but PR not merged, "
                 f"trying next strategy")
            continue

        # 405/409 = merge blocked or conflict
        if status in (405, 409):
            # Could be already merged (idempotent) or genuinely blocked
            if _check_pr_merged(api_base, token, pr_num):
                _log("PR already merged")
                return True
            msg = ""
            if isinstance(body, dict):
                msg = body.get("message", "")
            _log(f"{label} blocked (HTTP {status}): {msg}")
            continue

        _log(f"{label} failed (HTTP {status})")
        continue

    return False


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


_TRACKER_PATHS = (
    ".agent/tracker/.ops/",
    ".agent/tracker-workspace/.ops/",
)

_TRACKER_PATH_RE = re.compile(
    r"^\.agent/tracker(-workspace)?/\.ops/"
)

_OPS_PATHS = (
    ".agent/tracker/.ops/",
    ".agent/tracker-workspace/.ops/",
)

AUTO_SYNC_DEFAULT_THRESHOLD = 40


def _sum_added_lines(numstat_output: str) -> int:
    """Parse ``git diff --numstat`` output and sum the 'added' column.

    Each line has the format ``<added>\\t<deleted>\\t<path>``.
    Binary files show ``-`` instead of numbers; they are skipped.
    Malformed lines are silently ignored.

    Returns:
        Total number of added lines across all entries.
    """
    total = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        added = parts[0]
        if added == "-":
            continue
        try:
            total += int(added)
        except ValueError:
            continue
    return total


def pending_sync_lines(repo_root: Path) -> int:
    """Count pending tracker ops lines (not yet synced to origin/dev).

    Diffs working tree ops against ``origin/dev`` (the sync target) rather
    than ``HEAD``.  This prevents re-syncing ops that were already merged
    to ``origin/dev`` by a previous ``do_sync()`` call — the plumbing
    approach leaves ops dirty relative to the local branch HEAD, which
    caused duplicate sync PRs when the function diffed against HEAD.

    Falls back to ``HEAD`` when ``origin/dev`` doesn't exist (fresh clone,
    disconnected state, or non-standard remote setup).

    Also counts untracked ops files (new files not yet in any commit).

    Returns 0 if git fails or no changes exist.
    """
    total = 0

    # 0. Determine diff base: prefer origin/dev (the sync target),
    #    fall back to HEAD if origin/dev doesn't exist.
    diff_base = "HEAD"
    rev_result = _git(
        repo_root, "rev-parse", "--verify", "origin/dev", check=False,
    )
    if rev_result.returncode == 0 and rev_result.stdout.strip():
        diff_base = "origin/dev"

    # 1. Tracked changes (staged + unstaged) relative to diff base
    numstat_args = ["diff", diff_base, "--numstat", "--"]
    numstat_args.extend(_OPS_PATHS)
    result = _git(repo_root, *numstat_args, check=False)
    if result.returncode == 0 and result.stdout.strip():
        total += _sum_added_lines(result.stdout)

    # 2. Untracked files in ops directories
    untracked_args = ["ls-files", "--others", "--exclude-standard", "--"]
    untracked_args.extend(_OPS_PATHS)
    result = _git(repo_root, *untracked_args, check=False)
    if result.returncode == 0 and result.stdout.strip():
        for fpath in result.stdout.strip().splitlines():
            full = repo_root / fpath
            if full.is_file():
                try:
                    total += len(full.read_text().splitlines())
                except OSError:
                    pass

    return total


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

    # 2b. Write access — sync needs to create branches in .git/refs/heads
    refs_heads = git_dir / "refs" / "heads"
    if refs_heads.is_dir() and not os.access(refs_heads, os.W_OK):
        return PreflightResult(
            ok=False,
            error="no write access to .git/refs/heads/ (wrong user?)",
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

    # 5a. Pre-sync validation — catch dangling refs, cycles, etc.
    #     before data leaves the local machine.
    tracker_root = repo_root / ".agent"
    val_result = validate_all(tracker_root)
    if not val_result.ok:
        summary = "; ".join(val_result.errors[:5])
        if len(val_result.errors) > 5:
            summary += f" (and {len(val_result.errors) - 5} more)"
        return PreflightResult(
            ok=False,
            error=f"tracker validation failed: {summary}",
        )

    # 6. Credentials
    env_vars = _load_env(repo_root)
    forgejo_token = env_vars.get("FORGEJO_TOKEN") or os.environ.get(
        "FORGEJO_TOKEN", ""
    )
    forgejo_user = env_vars.get("FORGEJO_USER") or os.environ.get(
        "FORGEJO_USER", ""
    )

    # 6a. Failover detection — override credentials, API base, push remote
    failover = _detect_failover(git_dir, env_vars)
    push_remote = "origin"
    if failover.active:
        forgejo_token = failover.token
        forgejo_user = failover.user
        push_remote = failover.push_remote
        _log("[SELF-HOSTED] Failover active — targeting self-hosted Forgejo")

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
    target_remote = push_remote
    remote_result = _git(
        repo_root, "remote", "get-url", target_remote, check=False,
    )
    if remote_result.returncode != 0:
        return PreflightResult(
            ok=False, error=f"no remote '{target_remote}' configured",
        )

    # 9. API base — failover overrides origin-based detection
    if failover.active:
        api_base = failover.api_base
    else:
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
        push_remote=push_remote,
    )


def do_sync(
    repo_root: Path,
    preflight: PreflightResult,
    base_branch: str = "dev",
    ci_poll_interval: int = 10,
    ci_timeout: int = 300,
) -> SyncResult:
    """Execute the full sync workflow: commit → push → poll → merge.

    Uses git plumbing (read-tree/write-tree/commit-tree) with a temporary
    index to build the sync commit on top of origin/dev without checking
    out a branch.  This prevents feature branch code from leaking into the
    tracker sync PR.  try/finally ensures gate file and temp index cleanup.

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

    # Initialize file logging (always on, 30-day retention).
    init_sync_log(repo_root)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    sync_branch = f"tracker-sync/{timestamp}"
    gate_file = preflight.git_dir / "TRACKER_SYNC_PENDING"
    file_count = len(preflight.changed_files)

    # Temporary index file for plumbing — avoids checkout, keeps working
    # tree on the feature branch.  Build the commit on top of origin/dev
    # so the PR diff contains *only* tracker ops, not feature branch code.
    tmp_index = str(preflight.git_dir / "tmp-sync-index")
    idx_env = {"GIT_INDEX_FILE": tmp_index}

    try:
        # 0a. Create gate file immediately to prevent concurrent syncs.
        # Previously this was done at step 8 (after commit creation),
        # leaving a window where concurrent _maybe_auto_sync calls could
        # all pass preflight and create duplicate PRs.
        gate_file.write_text("sync\n")

        # 0b. Fetch latest base branch (non-fatal if offline — we'll use
        #     the local ref which may be slightly stale but still correct).
        _git(
            repo_root, "fetch", preflight.push_remote, base_branch,
            check=False,
        )

        # 1. Resolve base ref
        base_ref = f"{preflight.push_remote}/{base_branch}"
        rev_result = _git(
            repo_root, "rev-parse", base_ref, check=False,
        )
        if rev_result.returncode != 0:
            return SyncResult(
                success=False,
                error=f"cannot resolve {base_ref}: {rev_result.stderr.strip()}",
                exit_code=1,
            )
        base_sha = rev_result.stdout.strip()

        # 2. Populate temporary index from base tree
        read_result = _git(
            repo_root, "read-tree", base_sha,
            check=False, env=idx_env,
        )
        if read_result.returncode != 0:
            return SyncResult(
                success=False,
                error=f"read-tree failed: {read_result.stderr.strip()}",
                exit_code=1,
            )

        # 3. Stage tracker ops into the temporary index
        stage_args = ["add", "--"]
        stage_args.extend(_TRACKER_PATHS)
        _git(repo_root, *stage_args, check=False, env=idx_env)

        # 4. Write tree from the temporary index
        write_result = _git(
            repo_root, "write-tree",
            check=False, env=idx_env,
        )
        if write_result.returncode != 0:
            return SyncResult(
                success=False,
                error=f"write-tree failed: {write_result.stderr.strip()}",
                exit_code=1,
            )
        tree_sha = write_result.stdout.strip()

        # 5. Build sign-off trailer (mirrors git commit -s)
        name_r = _git(repo_root, "config", "user.name", check=False)
        email_r = _git(repo_root, "config", "user.email", check=False)
        signoff = ""
        if name_r.returncode == 0 and email_r.returncode == 0:
            signoff = (
                f"\n\nSigned-off-by: "
                f"{name_r.stdout.strip()} <{email_r.stdout.strip()}>"
            )

        # 6. Create commit object (parent = base_sha)
        commit_msg = f"tracker: sync {file_count} file(s){signoff}"
        commit_result = _git(
            repo_root,
            "-c", "commit.gpgSign=false",
            "commit-tree", tree_sha, "-p", base_sha, "-m", commit_msg,
            check=False,
        )
        if commit_result.returncode != 0:
            return SyncResult(
                success=False,
                error=f"commit-tree failed: {commit_result.stderr.strip()}",
                exit_code=1,
            )
        commit_sha = commit_result.stdout.strip()
        # 7. Create branch ref pointing to the new commit
        ref_name = f"refs/heads/{sync_branch}"
        ref_result = _git(
            repo_root, "update-ref", ref_name, commit_sha,
            check=False,
        )
        if ref_result.returncode != 0:
            return SyncResult(
                success=False,
                error=f"update-ref failed: {ref_result.stderr.strip()}",
                exit_code=1,
            )

        # 8. (Gate file already created at step 0a.)

        # 9. Push with retries
        push_ref = f"refs/heads/{sync_branch}:refs/for/{base_branch}/{sync_branch}"
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
                "push", preflight.push_remote, push_ref,
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

        # 10. Find PR (with brief initial delay)
        time.sleep(2)
        pr_info = _find_open_pr(
            preflight.api_base,
            preflight.forgejo_token,
            sync_branch,
            title=push_title,
        )
        if pr_info is None:
            return SyncResult(
                success=False,
                error=f"could not find open PR for branch {sync_branch}",
                exit_code=1,
            )
        pr_num, head_sha = pr_info

        # 11. Update gate file with PR number
        gate_file.write_text(f"{pr_num}\n")

        # 12. Poll CI
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

        # 13. Rebase if dev advanced during CI polling
        # Re-fetch origin/dev; if it moved since step 1, rebuild the
        # sync commit on the new base and force-push so the PR is
        # mergeable.  This prevents "head behind base branch" 405s.
        _git(
            repo_root, "fetch", preflight.push_remote, base_branch,
            check=False,
        )
        new_base_result = _git(
            repo_root, "rev-parse", base_ref, check=False,
        )
        new_base_sha = (
            new_base_result.stdout.strip()
            if new_base_result.returncode == 0
            else base_sha
        )
        if new_base_sha != base_sha:
            _log(
                f"dev advanced during CI "
                f"({base_sha[:8]}→{new_base_sha[:8]}), rebasing"
            )
            # Rebuild: read new base tree → stage ops → write → commit
            rb_read = _git(
                repo_root, "read-tree", new_base_sha,
                check=False, env=idx_env,
            )
            if rb_read.returncode != 0:
                _log(
                    f"read-tree failed during rebase "
                    f"({rb_read.stderr.strip()}), "
                    f"proceeding with original commit"
                )
            if rb_read.returncode == 0:
                _git(
                    repo_root, "add", "--", *_TRACKER_PATHS,
                    check=False, env=idx_env,
                )
                rb_tree = _git(
                    repo_root, "write-tree",
                    check=False, env=idx_env,
                )
                if rb_tree.returncode == 0:
                    rb_commit = _git(
                        repo_root,
                        "-c", "commit.gpgSign=false",
                        "commit-tree", rb_tree.stdout.strip(),
                        "-p", new_base_sha, "-m", commit_msg,
                        check=False,
                    )
                    if rb_commit.returncode == 0:
                        new_sha = rb_commit.stdout.strip()
                        _git(
                            repo_root, "update-ref",
                            ref_name, new_sha,
                            check=False,
                        )
                        # Force-push the rebased branch
                        _git(
                            repo_root,
                            "-c", f"credential.helper={cred_helper}",
                            "push", "--force",
                            preflight.push_remote, push_ref,
                            check=False,
                        )
                        _log("rebased and force-pushed sync branch")
                        # Brief delay for Forgejo to process
                        time.sleep(3)

        # 14. Merge PR (with retries for status check propagation)
        # After CI passes, the required commit status may take a few
        # seconds to propagate.  Retry the merge cascade on 405 responses.
        slug_match = re.search(r"/repos/(.+)$", preflight.api_base)
        repo_slug = slug_match.group(1) if slug_match else ""

        merged = False
        for merge_attempt in range(1, 7):
            merged = _merge_pr(
                preflight.api_base, preflight.forgejo_token, pr_num
            )
            if merged:
                break
            if merge_attempt < 6:
                _log(
                    f"merge attempt {merge_attempt}/6 failed, "
                    f"retrying in 10s..."
                )
                time.sleep(10)

        if not merged:
            return SyncResult(
                success=False,
                pr_number=pr_num,
                error="merge failed after retries (status checks or divergence)",
                exit_code=1,
            )

        # Construct PR URL
        base_url_match = re.match(r"(https?://[^/]+)/", preflight.api_base)
        base_url = base_url_match.group(1) if base_url_match else "https://codeberg.org"
        pr_url = f"{base_url}/{repo_slug}/pulls/{pr_num}"

        return SyncResult(
            success=True,
            pr_number=pr_num,
            pr_url=pr_url,
            files_synced=file_count,
            exit_code=0,
        )

    finally:
        # Cleanup — plumbing approach never leaves the original branch,
        # so ops files remain in the working tree regardless of outcome.
        # Remove gate file
        if gate_file.exists():
            gate_file.unlink()

        # Remove temporary index file
        tmp_index_path = Path(tmp_index)
        if tmp_index_path.exists():
            tmp_index_path.unlink()

        # Fetch latest remote ref (non-fatal).
        _git(
            repo_root, "fetch", preflight.push_remote, base_branch,
            check=False,
        )

        # If we're sitting on the base branch itself, fast-forward it
        # to origin/dev so the synced ops files become tracked.  Without
        # this, the ops files remain "untracked" in the working tree and
        # pending_sync_lines() counts them again on the next call,
        # causing the pending-line count to grow by ~22 per sync cycle.
        # Fast-forward-only is safe: we haven't made local commits on
        # the base branch (the plumbing approach commits to a detached
        # sync branch), so ff-only either succeeds or is a no-op.
        #
        # The synced ops files may be in one of two states:
        # - **Untracked** (new items): must be removed before merge,
        #   otherwise git refuses to overwrite them.
        # - **Modified** (updates to existing items): must be reset to
        #   HEAD content before merge, otherwise git refuses to
        #   overwrite dirty tracked files.
        # Both are safe because the content is identical to what's on
        # origin/dev (we just synced them).  Use ``git checkout HEAD``
        # for tracked files and ``unlink`` for untracked.
        if preflight.original_branch == base_branch:
            # Reset tracked ops files to HEAD (handles modified files).
            # ``git checkout HEAD -- <paths>`` silently ignores
            # untracked files, so this is safe.
            co_result = _git(
                repo_root,
                "checkout", "HEAD", "--",
                *_OPS_PATHS,
                check=False,
            )
            if co_result.returncode != 0:
                _log(
                    f"checkout HEAD failed during cleanup: "
                    f"{co_result.stderr.strip()}"
                )
            # Remove untracked ops files (new items created this
            # session that aren't on the local branch yet).
            untracked = _git(
                repo_root,
                "ls-files", "--others", "--exclude-standard", "--",
                *_OPS_PATHS,
                check=False,
            )
            if untracked.returncode == 0 and untracked.stdout.strip():
                for fpath in untracked.stdout.strip().splitlines():
                    full = repo_root / fpath
                    if full.is_file():
                        try:
                            full.unlink()
                        except OSError as exc:
                            _log(
                                f"failed to remove {fpath}: {exc}"
                            )
            ff_result = _git(
                repo_root,
                "merge", "--ff-only",
                f"{preflight.push_remote}/{base_branch}",
                check=False,
            )
            if ff_result.returncode != 0:
                _log(
                    f"ff-only merge failed during cleanup: "
                    f"{ff_result.stderr.strip()}"
                )

        # Delete sync branch ref (non-fatal)
        _git(repo_root, "branch", "-D", sync_branch, check=False)
