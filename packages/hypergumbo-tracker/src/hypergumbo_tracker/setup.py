# SPDX-License-Identifier: MPL-2.0
"""Idempotent setup wizard for the hypergumbo tracker.

Runs a sequence of checks that inspect the tracker's operational state,
auto-fix what can be fixed (directory creation, gitattributes, config copy),
and report advisory diagnostics for things that require human action
(agent instructions, hook integration).

Each check returns a CheckResult with status ok/fixed/warn/error. The wizard
is idempotent: running it twice with no changes between runs produces all
"ok" results.

The check sequence covers three areas:
1. **Core infrastructure** (checks 1-14): directory structure, git plumbing,
   config validation, ownership, group permissions, textconv driver,
   data integrity.
2. **Agentic infrastructure** (checks 15-20): wrapper scripts, agent
   instructions, hook integration, autonomous mode consistency, reflection
   state validity. Read-only / advisory only.
3. **Sync prerequisites** (check 21): remote origin, FORGEJO_TOKEN,
   git identity — advisory check for ``htrac sync`` workflow.

Entry point: ``run_setup(root, repo_root)`` returns a list of CheckResult.
The CLI handler in cli.py formats and prints them.
"""

from __future__ import annotations

import datetime
import grp
import json
import os
import pwd
import re
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hypergumbo_tracker.models import (
    ConfigValidationError,
    _parse_config_dict,
    resolve_actor,
)
from hypergumbo_tracker.store import _find_git_dir
from hypergumbo_tracker.validation import ValidationResult, validate_all


@dataclass
class CheckResult:
    """Result of a single setup check.

    Attributes:
        name: Machine-readable check identifier (e.g. "directory_structure").
        status: One of "ok", "fixed", "warn", "error".
        message: Human-readable one-line summary.
        details: Additional lines (fix instructions, context).
    """

    name: str
    status: str
    message: str
    details: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tracker concept definitions for AGENTS.md scanning (check #16)
# ---------------------------------------------------------------------------

TRACKER_CONCEPTS: dict[str, dict[str, Any]] = {
    "context_protection": {
        "description": "Agents should use CLI (not read .ops files directly)",
        "patterns": [
            r"tracker\s+show",
            r"\.ops\b.*(?:pollute|context|don't read|refuse)",
            r"show\s+<ID>",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  Always use `scripts/tracker show <ID>` to read tracker item state.\n"
            "  Never read .ops files directly — they are internal operation logs\n"
            "  that will pollute your context window."
        ),
    },
    "task_selection": {
        "description": "Agents should use `ready` for task selection",
        "patterns": [
            r"tracker\s+ready",
            r"htrac\s+ready",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  Use `scripts/tracker ready` (not `list`) to pick your next work item.\n"
            "  `ready` filters to actionable items sorted by priority."
        ),
    },
    "commit_convention": {
        "description": "Tracker-only changes use tracker: commit prefix",
        "patterns": [
            r"tracker:\s",
            r"commit.*prefix.*tracker",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  Tracker-only changes use a `tracker:` conventional-commit prefix."
        ),
    },
    "structural_fix_protocol": {
        "description": (
            "Assume a bug is a symptom of a deeper issue affecting multiple "
            "disparate parts of the codebase"
        ),
        "patterns": [
            r"assume\s+structural",
            r"name\s+the\s+invariant",
            r"scope\s+expansion",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  When fixing a bug, assume it is a symptom of a deeper issue\n"
            "  affecting multiple parts of the codebase. Name the violated\n"
            "  invariant, then check for analogous issues via scope expansion."
        ),
    },
    "tdd_protocol": {
        "description": "Agents should follow TDD (Red/Green/Refactor)",
        "patterns": [
            r"red.*green.*refactor",
            r"failing\s+test\s+first",
            r"write.*test.*before",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  Follow TDD: write a failing test first (Red), make it pass\n"
            "  (Green), then refactor. Do not skip the refactor phase."
        ),
    },
    "coverage_requirement": {
        "description": "Test coverage target should be explicit",
        "patterns": [
            r"100%.*coverage",
            r"cov-fail-under",
            r"coverage.*100",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  Maintain 100% test coverage. Verify with:\n"
            "    pytest --cov-fail-under=100"
        ),
    },
    "batching": {
        "description": "Tracker operations should be batched into fewer commits",
        "patterns": [
            r"batch.*tracker",
            r"tracker.*batch",
            r"fewer\s+commits.*tracker",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  Batch tracker operations into fewer commits rather than committing\n"
            "  after every `scripts/tracker update` call."
        ),
    },
    "human_review_status": {
        "description": "Agents should know about needs_human_review for non-blocking items",
        "patterns": [
            r"needs_human_review",
        ],
        "suggestion": (
            "Add to your agent instructions:\n"
            "  Use `needs_human_review` status for governance proposals, architectural\n"
            "  questions, or anything requiring human judgment. This status does NOT\n"
            "  block stopping — the human triages these items in the TUI."
        ),
    },
}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_git_repo(root: Path) -> tuple[CheckResult, Path | None]:
    """Check #1: Verify we're inside a git repository.

    Returns the check result and the repo root path (None if not in a repo).
    """
    git_dir = _find_git_dir(root)
    if git_dir is None:
        return (
            CheckResult(
                name="git_repo",
                status="warn",
                message="Not inside a git repository",
                details=[
                    "Lamport clocks, textconv, and git hooks won't work.",
                    "The tracker can still function without git.",
                ],
            ),
            None,
        )

    # Resolve repo root from .git dir
    if git_dir.is_file():
        # Worktree: .git is a file pointing elsewhere
        repo_root = git_dir.parent
    else:
        repo_root = git_dir.parent

    return (
        CheckResult(
            name="git_repo",
            status="ok",
            message="Git repository detected",
        ),
        repo_root,
    )


def _check_directory_structure(root: Path) -> CheckResult:
    """Check #2: Create required directory structure.

    Directories: .agent/, .agent/tracker/, .agent/tracker/.ops/,
    .agent/tracker-workspace/.ops/, .agent/tracker-workspace/stealth/.
    """
    dirs = [
        root,
        root / "tracker",
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]

    created: list[str] = []
    existed: list[str] = []
    for d in dirs:
        if d.exists():
            existed.append(str(d))
        else:
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))

    if created:
        return CheckResult(
            name="directory_structure",
            status="fixed",
            message=f"Created {len(created)} director{'y' if len(created) == 1 else 'ies'}",
            details=[f"  created: {p}" for p in created],
        )
    return CheckResult(
        name="directory_structure",
        status="ok",
        message="Directory structure",
    )


def _check_gitattributes(root: Path) -> CheckResult:
    """Check #3: Ensure .gitattributes in .ops/ dirs contain merge=union."""
    ops_dirs = [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
    ]
    required_line = "*.ops merge=union"
    fixed: list[str] = []

    for ops_dir in ops_dirs:
        ga_path = ops_dir / ".gitattributes"
        if ga_path.exists():
            content = ga_path.read_text()
            if required_line not in content:
                # Append the missing line
                with open(ga_path, "a") as f:
                    if content and not content.endswith("\n"):
                        f.write("\n")
                    f.write(required_line + "\n")
                fixed.append(str(ops_dir))
        else:
            ga_path.write_text(required_line + "\n")
            fixed.append(str(ops_dir))

    if fixed:
        return CheckResult(
            name="gitattributes",
            status="fixed",
            message=(
                f".gitattributes — added '{required_line}' "
                f"to {len(fixed)} director{'y' if len(fixed) == 1 else 'ies'}"
            ),
        )
    return CheckResult(
        name="gitattributes",
        status="ok",
        message=".gitattributes files",
    )


def _check_gitignore(root: Path) -> CheckResult:
    """Check #4: Ensure .gitignore files have required entries."""
    checks = [
        (root / "tracker" / ".gitignore", "config.yaml"),
        (root / "tracker-workspace" / "stealth" / ".gitignore", "*.ops"),
    ]
    fixed: list[str] = []

    for gi_path, required_line in checks:
        if gi_path.exists():
            content = gi_path.read_text()
            if required_line not in content:
                with open(gi_path, "a") as f:
                    if content and not content.endswith("\n"):
                        f.write("\n")
                    f.write(required_line + "\n")
                fixed.append(str(gi_path))
        else:
            gi_path.write_text(required_line + "\n")
            fixed.append(str(gi_path))

    if fixed:
        return CheckResult(
            name="gitignore",
            status="fixed",
            message=f".gitignore — fixed {len(fixed)} file{'s' if len(fixed) != 1 else ''}",
        )
    return CheckResult(
        name="gitignore",
        status="ok",
        message=".gitignore files",
    )


def _check_config_template(root: Path) -> CheckResult:
    """Check #5: Warn if config.yaml.template is missing."""
    template = root / "tracker" / "config.yaml.template"
    if template.exists():
        return CheckResult(
            name="config_template",
            status="ok",
            message="config.yaml.template found",
        )
    return CheckResult(
        name="config_template",
        status="warn",
        message="config.yaml.template not found",
        details=[
            "This tracked governance file should come from the repo.",
            "Run 'htrac init' in a repo that has it, or create one manually.",
        ],
    )


def _check_config_yaml(root: Path) -> CheckResult:
    """Check #6: Ensure config.yaml exists and is parseable YAML."""
    config_path = root / "tracker" / "config.yaml"
    template_path = root / "tracker" / "config.yaml.template"

    if not config_path.exists():
        if template_path.exists():
            shutil.copy2(template_path, config_path)
            return CheckResult(
                name="config_yaml",
                status="fixed",
                message="config.yaml — copied from template",
            )
        return CheckResult(
            name="config_yaml",
            status="warn",
            message="config.yaml not found (no template to copy from)",
            details=["Using built-in defaults. Create config.yaml for customization."],
        )

    # File exists — check if it parses as YAML
    try:
        with open(config_path) as f:
            parsed = yaml.safe_load(f)
        if parsed is None:
            # Empty file — treat as unparseable
            raise yaml.YAMLError("empty config file")
        if not isinstance(parsed, dict):
            raise yaml.YAMLError("config must be a YAML mapping")
    except yaml.YAMLError:
        # Rename and copy from template
        old_path = config_path.with_suffix(".yaml.old")
        config_path.rename(old_path)
        if template_path.exists():
            shutil.copy2(template_path, config_path)
            return CheckResult(
                name="config_yaml",
                status="fixed",
                message="config.yaml — renamed broken file to .old, copied from template",
                details=[f"Backup: {old_path}"],
            )
        return CheckResult(
            name="config_yaml",
            status="warn",
            message="config.yaml was unparseable — renamed to .old",
            details=[
                f"Backup: {old_path}",
                "No template available to copy from. Using built-in defaults.",
            ],
        )

    return CheckResult(
        name="config_yaml",
        status="ok",
        message="config.yaml",
    )


def _check_config_validation(root: Path) -> CheckResult:
    """Check #7: Validate config.yaml against TrackerConfig schema."""
    config_path = root / "tracker" / "config.yaml"
    if not config_path.exists():
        return CheckResult(
            name="config_validation",
            status="ok",
            message="Config validation skipped (no config.yaml)",
        )

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            return CheckResult(
                name="config_validation",
                status="error",
                message="Config validation failed: not a YAML mapping",
            )
        _parse_config_dict(raw)
    except yaml.YAMLError as e:
        return CheckResult(
            name="config_validation",
            status="error",
            message=f"Config validation failed: YAML parse error: {e}",
        )
    except ConfigValidationError as e:
        return CheckResult(
            name="config_validation",
            status="error",
            message=f"Config validation failed: {e}",
            details=["Fix the errors in .agent/tracker/config.yaml"],
        )

    return CheckResult(
        name="config_validation",
        status="ok",
        message="Config validation passed",
    )


def _check_config_drift(root: Path) -> CheckResult:
    """Check #8: Compare config.yaml and config.yaml.template kind keys."""
    config_path = root / "tracker" / "config.yaml"
    template_path = root / "tracker" / "config.yaml.template"

    if not config_path.exists() or not template_path.exists():
        return CheckResult(
            name="config_drift",
            status="ok",
            message="Config drift check skipped (need both config.yaml and template)",
        )

    try:
        with open(config_path) as f:
            config_raw = yaml.safe_load(f) or {}
        with open(template_path) as f:
            template_raw = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return CheckResult(
            name="config_drift",
            status="ok",
            message="Config drift check skipped (YAML parse error)",
        )

    config_kinds = set((config_raw.get("kinds") or {}).keys())
    template_kinds = set((template_raw.get("kinds") or {}).keys())

    in_config_only = config_kinds - template_kinds
    in_template_only = template_kinds - config_kinds

    if not in_config_only and not in_template_only:
        return CheckResult(
            name="config_drift",
            status="ok",
            message="Config matches template",
        )

    details: list[str] = []
    if in_config_only:
        details.append(f"Kinds in config only: {sorted(in_config_only)}")
    if in_template_only:
        details.append(f"Kinds in template only: {sorted(in_template_only)}")

    return CheckResult(
        name="config_drift",
        status="warn",
        message="Config/template kind drift detected",
        details=details,
    )


def _check_actor_resolution(root: Path) -> CheckResult:
    """Check #9: Check if current user matches agent patterns."""
    config_path = root / "tracker" / "config.yaml"

    # Load agent patterns from config (or defaults)
    agent_patterns = ["*_agent"]
    if config_path.exists():
        try:
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            actor_res = raw.get("actor_resolution", {})
            if isinstance(actor_res, dict):
                patterns = actor_res.get("agent_usernames")
                if isinstance(patterns, list) and patterns:
                    agent_patterns = patterns
        except yaml.YAMLError:
            pass  # Fall through to defaults

    by, username = resolve_actor(agent_patterns)

    if by == "agent":
        return CheckResult(
            name="actor_resolution",
            status="warn",
            message=f"Current user '{username}' matches agent pattern",
            details=[
                "Human-only commands (lock, stealth, discuss --clear) will be blocked.",
                "To fix: edit actor_resolution.agent_usernames in",
                "  .agent/tracker/config.yaml",
            ],
        )

    return CheckResult(
        name="actor_resolution",
        status="ok",
        message=f"Actor resolution: '{username}' is human",
    )


def _check_config_ownership(root: Path) -> CheckResult:
    """Check #10: Verify config.yaml is owned by the human user.

    Config files control governance (agent patterns, stop hook behavior,
    status lifecycle). The human user should own them so the agent can
    read but not modify governance settings.

    If the current user is human, auto-fix ownership. If agent, warn.
    """
    config_paths = [
        root / "tracker" / "config.yaml",
        root / "tracker-workspace" / "config.yaml",
    ]
    existing = [p for p in config_paths if p.exists()]
    if not existing:
        return CheckResult(
            name="config_ownership",
            status="ok",
            message="Config ownership check skipped (no config.yaml yet)",
        )

    # Determine current user role
    agent_patterns = ["*_agent"]
    config_path = root / "tracker" / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            actor_res = raw.get("actor_resolution", {})
            if isinstance(actor_res, dict):
                patterns = actor_res.get("agent_usernames")
                if isinstance(patterns, list) and patterns:
                    agent_patterns = patterns
        except yaml.YAMLError:
            pass

    by, username = resolve_actor(agent_patterns)
    current_uid = os.getuid()

    # Check if any config is NOT owned by the current human user
    if by == "agent":
        wrong_owner = [p for p in existing if p.stat().st_uid == current_uid]
        if wrong_owner:
            return CheckResult(
                name="config_ownership",
                status="warn",
                message="config.yaml is owned by the agent user",
                details=[
                    "Config controls governance settings and should be owned",
                    "by the human user. As the human user, run:",
                    f"  sudo chown $(whoami) {' '.join(str(p) for p in existing)}",
                ],
            )
        return CheckResult(
            name="config_ownership",
            status="ok",
            message="config.yaml ownership looks correct",
        )

    # Current user is human — fix ownership if needed
    fixed: list[str] = []
    for p in existing:
        if p.stat().st_uid != current_uid:
            try:
                os.chown(p, current_uid, -1)
                fixed.append(str(p))
            except OSError:
                return CheckResult(
                    name="config_ownership",
                    status="warn",
                    message="Cannot fix config.yaml ownership (permission denied)",
                    details=[
                        "Run with sudo to fix:",
                        f"  sudo chown {username} {' '.join(str(p) for p in existing)}",
                    ],
                )
    if fixed:
        return CheckResult(
            name="config_ownership",
            status="fixed",
            message=f"config.yaml ownership — fixed {len(fixed)} file{'s' if len(fixed) != 1 else ''}",
        )
    return CheckResult(
        name="config_ownership",
        status="ok",
        message="config.yaml owned by human user",
    )


def _check_group_permissions(
    root: Path, repo_root: Path | None = None
) -> CheckResult:
    """Check #11: Verify two-user group setup on writable directories.

    Checks whether tracker-workspace (which holds tui_preferences.json),
    .ops, stealth, and .git/ directories have a shared group with
    group-write and setgid permissions.  The .git/ directory is included
    because ``htrac sync`` creates branches, commits, and pushes — all
    of which require write access to .git/refs/heads/, .git/objects/,
    and .git/logs/.

    This is read-only — it reports problems but does not fix them, since
    group/permission changes require sudo.

    If directories are owned by the user's primary group (not a shared
    group), the two-user setup is not active and the check passes with
    an informational message.
    """
    import stat

    ops_dirs: list[Path] = [
        root / "tracker" / ".ops",
        root / "tracker-workspace",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]
    # Include .git/ subdirectories that sync needs to write to
    if repo_root is not None:
        git_dir = repo_root / ".git"
        if git_dir.is_dir():
            for sub in ("refs/heads", "refs/tags", "objects", "logs"):
                candidate = git_dir / sub
                if candidate.is_dir():
                    ops_dirs.append(candidate)
    existing = [d for d in ops_dirs if d.exists()]
    if not existing:
        return CheckResult(
            name="group_permissions",
            status="ok",
            message="Group check skipped (no .ops directories yet)",
        )

    current_uid = os.getuid()
    current_user = pwd.getpwuid(current_uid).pw_name
    current_primary_gid = pwd.getpwuid(current_uid).pw_gid

    problems: list[str] = []
    shared_group_detected = False
    detected_group: str | None = None

    for d in existing:
        st = d.stat()
        dir_gid = st.st_gid

        # If the directory group is the user's primary group, two-user
        # setup hasn't been configured — that's fine, not an error.
        if dir_gid == current_primary_gid:
            continue

        # A non-primary group means someone ran chgrp — two-user setup
        # is intended. Verify it's correct.
        shared_group_detected = True
        try:
            group_name = grp.getgrgid(dir_gid).gr_name
            detected_group = group_name
        except KeyError:
            problems.append(f"{d}: owned by unknown gid {dir_gid}")
            continue

        # Check current user is in the group
        try:
            group_members = grp.getgrgid(dir_gid).gr_mem
            # Also check if it's the user's primary group (won't appear in gr_mem)
            user_in_group = (
                current_user in group_members or current_primary_gid == dir_gid
            )
            if not user_in_group:
                problems.append(
                    f"{d}: user '{current_user}' is not in group '{group_name}'"
                )
        except KeyError:
            pass

        # Check group-write permission
        mode = st.st_mode
        if not (mode & stat.S_IWGRP):
            problems.append(f"{d}: missing group-write permission")

        # Check setgid bit (ensures new files inherit the group)
        if not (mode & stat.S_ISGID):
            problems.append(f"{d}: missing setgid bit")

    if not shared_group_detected:
        return CheckResult(
            name="group_permissions",
            status="ok",
            message="Single-user setup (no shared group on .ops directories)",
        )

    if problems:
        grp_label = detected_group or "GROUP"
        fix_lines = [
            *problems,
            "",
            "Fix with (as a user with sudo):",
            f"  sudo chgrp -R {grp_label}"
            " .agent/tracker .agent/tracker-workspace",
            "  sudo chmod -R g+rws .agent/tracker/.ops"
            " .agent/tracker-workspace"
            " .agent/tracker-workspace/.ops"
            " .agent/tracker-workspace/stealth",
        ]
        if repo_root is not None and (repo_root / ".git").is_dir():
            fix_lines.extend([
                f"  sudo chgrp -R {grp_label} .git/",
                "  sudo chmod -R g+w .git/",
                "  sudo find .git/ -type d -exec chmod g+s {} +",
            ])
        return CheckResult(
            name="group_permissions",
            status="error",
            message=f"Two-user group setup has {len(problems)} problem{'s' if len(problems) != 1 else ''}",
            details=fix_lines,
        )

    return CheckResult(
        name="group_permissions",
        status="ok",
        message="Two-user group setup is correct",
    )


def _check_ops_writable(root: Path) -> CheckResult:
    """Check #12: Verify .ops/ directories are writable."""
    ops_dirs = [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]
    not_writable: list[str] = []

    for d in ops_dirs:
        if d.exists() and not os.access(d, os.W_OK):
            not_writable.append(str(d))

    if not_writable:
        return CheckResult(
            name="ops_writable",
            status="error",
            message=f"{len(not_writable)} .ops director{'y' if len(not_writable) == 1 else 'ies'} not writable",
            details=[f"  {p}" for p in not_writable],
        )
    return CheckResult(
        name="ops_writable",
        status="ok",
        message=".ops/ directories are writable",
    )


def _ensure_safe_directory(repo_root: Path) -> bool:
    """Add repo to git's safe.directory if not already trusted.

    In a two-user setup the repo is owned by the agent user but the
    human user needs to run git commands in it. Git blocks this unless
    the repo is listed in safe.directory. Returns True if the config
    was modified.
    """
    try:
        probe = subprocess.run(  # nosec B603, B607
            ["git", "status", "--porcelain"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        if "dubious ownership" not in probe.stderr:
            return False
    except FileNotFoundError:
        return False

    # Add this repo to safe.directory (global config)
    try:
        subprocess.run(  # noqa: S603  # nosec B603, B607
            [  # noqa: S607
                "git",
                "config",
                "--global",
                "--add",
                "safe.directory",
                str(repo_root),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


def _check_textconv(root: Path, repo_root: Path | None) -> CheckResult:
    """Check #13: Git textconv driver for .ops files.

    When running as the human user in a repo owned by the agent user,
    git may block access due to safe.directory restrictions. The wizard
    auto-adds the repo to safe.directory if needed. If the local
    .git/config isn't writable (common in two-user setups), falls back
    to --global config.
    """
    if repo_root is None:
        return CheckResult(
            name="textconv",
            status="ok",
            message="Textconv check skipped (no git repo)",
        )

    fixed: list[str] = []

    # Ensure git trusts this repo (two-user setup: repo may be owned by
    # a different user, triggering safe.directory protection).
    safe_dir_fixed = _ensure_safe_directory(repo_root)
    if safe_dir_fixed:
        fixed.append("safe.directory")

    # Check git config for textconv driver
    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "config", "diff.tracker-ops.textconv"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        if result.returncode != 0 or not result.stdout.strip():
            # Try local config first, fall back to --global if not writable
            textconv_value = "python -m hypergumbo_tracker.cli textconv"
            try:
                subprocess.run(  # noqa: S603  # nosec B603, B607
                    [  # noqa: S607
                        "git",
                        "config",
                        "diff.tracker-ops.textconv",
                        textconv_value,
                    ],
                    capture_output=True,
                    text=True,
                    cwd=str(repo_root),
                    check=True,
                )
                fixed.append("textconv driver")
            except subprocess.CalledProcessError:
                # Local .git/config not writable — use --global
                subprocess.run(  # noqa: S603  # nosec B603, B607
                    [  # noqa: S607
                        "git",
                        "config",
                        "--global",
                        "diff.tracker-ops.textconv",
                        textconv_value,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                fixed.append("textconv driver (global)")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return CheckResult(
            name="textconv",
            status="warn",
            message="Could not configure textconv driver",
            details=["git config command failed"],
        )

    # Check .gitattributes in .ops dirs for diff=tracker-ops
    diff_line = "*.ops diff=tracker-ops"
    ops_dirs = [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
    ]
    for ops_dir in ops_dirs:
        ga_path = ops_dir / ".gitattributes"
        if ga_path.exists():
            content = ga_path.read_text()
            if diff_line not in content:
                with open(ga_path, "a") as f:
                    if content and not content.endswith("\n"):
                        f.write("\n")
                    f.write(diff_line + "\n")
                fixed.append(str(ops_dir))
        else:
            ga_path.write_text(diff_line + "\n")
            fixed.append(str(ops_dir))

    if fixed:
        return CheckResult(
            name="textconv",
            status="fixed",
            message=f"Git textconv driver configured ({len(fixed)} fix{'es' if len(fixed) != 1 else ''})",
        )
    return CheckResult(
        name="textconv",
        status="ok",
        message="Git textconv driver",
    )


def _check_existing_data(root: Path) -> CheckResult:
    """Check #14: Validate existing .ops files if any exist."""
    # Check if any .ops files exist
    ops_dirs = [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]
    has_ops = False
    for d in ops_dirs:
        if d.exists():
            for f in d.iterdir():
                if f.name.startswith(".") and f.name.endswith(".ops"):
                    has_ops = True
                    break
        if has_ops:
            break

    if not has_ops:
        return CheckResult(
            name="existing_data",
            status="ok",
            message="No existing data to validate",
        )

    vr: ValidationResult = validate_all(root)

    details: list[str] = []
    if vr.errors:
        details.append(f"Errors ({len(vr.errors)}):")
        for e in vr.errors[:10]:
            details.append(f"  {e}")
        if len(vr.errors) > 10:
            details.append(f"  ... and {len(vr.errors) - 10} more")
    if vr.warnings:
        details.append(f"Warnings ({len(vr.warnings)}):")
        for w in vr.warnings[:10]:
            details.append(f"  {w}")
        if len(vr.warnings) > 10:
            details.append(f"  ... and {len(vr.warnings) - 10} more")

    if vr.errors:
        return CheckResult(
            name="existing_data",
            status="error",
            message=f"Data validation: {len(vr.errors)} error(s), {len(vr.warnings)} warning(s)",
            details=details,
        )

    if vr.warnings:
        return CheckResult(
            name="existing_data",
            status="warn",
            message=f"Data validation: {len(vr.warnings)} warning(s)",
            details=details,
        )

    return CheckResult(
        name="existing_data",
        status="ok",
        message="Existing data validates cleanly",
    )


# ---------------------------------------------------------------------------
# Part 2: Agentic infrastructure (read-only, advisory)
# ---------------------------------------------------------------------------


def _check_tracker_wrapper(repo_root: Path | None) -> CheckResult:
    """Check #15: Check if scripts/tracker wrapper exists and is executable."""
    if repo_root is None:
        return CheckResult(
            name="tracker_wrapper",
            status="ok",
            message="Tracker wrapper check skipped (no git repo)",
        )

    wrapper = repo_root / "scripts" / "tracker"
    if wrapper.exists():
        if os.access(wrapper, os.X_OK):
            return CheckResult(
                name="tracker_wrapper",
                status="ok",
                message="scripts/tracker wrapper found",
            )
        return CheckResult(
            name="tracker_wrapper",
            status="warn",
            message="scripts/tracker exists but is not executable",
            details=["Run: chmod +x scripts/tracker"],
        )

    return CheckResult(
        name="tracker_wrapper",
        status="warn",
        message="scripts/tracker wrapper not found",
        details=[
            "Create scripts/tracker with content:",
            '  #!/usr/bin/env bash',
            '  exec python -m hypergumbo_tracker.cli "$@"',
            "Then: chmod +x scripts/tracker",
        ],
    )


def _check_agents_md(repo_root: Path | None) -> CheckResult:
    """Check #16: Scan AGENTS.md for key tracker concepts."""
    if repo_root is None:
        return CheckResult(
            name="agents_md",
            status="ok",
            message="AGENTS.md check skipped (no git repo)",
        )

    # Look for AGENTS.md or CLAUDE.md
    agents_content = None
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = repo_root / name
        if path.exists():
            agents_content = path.read_text()
            break

    if agents_content is None:
        return CheckResult(
            name="agents_md",
            status="warn",
            message="No AGENTS.md or CLAUDE.md found",
            details=["Agent instruction files help agents use the tracker correctly."],
        )

    missing: list[str] = []
    present: list[str] = []

    for concept_name, concept in TRACKER_CONCEPTS.items():
        found = False
        for pattern in concept["patterns"]:
            if re.search(pattern, agents_content, re.IGNORECASE):
                found = True
                break
        if found:
            present.append(concept_name)
        else:
            missing.append(concept_name)

    if not missing:
        return CheckResult(
            name="agents_md",
            status="ok",
            message="AGENTS.md covers all tracker concepts",
        )

    details: list[str] = []
    for concept_name in missing:
        concept = TRACKER_CONCEPTS[concept_name]
        details.append(f"Missing concept '{concept_name}': {concept['description']}")
        details.append(concept["suggestion"])

    return CheckResult(
        name="agents_md",
        status="warn",
        message=f"AGENTS.md: {len(missing)} tracker concept(s) missing",
        details=details,
    )


def _check_stop_hook(repo_root: Path | None) -> CheckResult:
    """Check #17: Check if stop hooks reference tracker commands."""
    if repo_root is None:
        return CheckResult(
            name="stop_hook",
            status="ok",
            message="Stop hook check skipped (no git repo)",
        )

    # Look for stop hook scripts
    hook_patterns = [
        repo_root / ".agent" / "hooks",
        repo_root / ".githooks",
    ]

    hook_contents: list[str] = []
    for hook_dir in hook_patterns:
        if not hook_dir.is_dir():
            continue
        # Search recursively for stop-related scripts
        for p in hook_dir.rglob("*"):
            if p.is_file() and "stop" in p.name.lower():
                try:
                    hook_contents.append(p.read_text())
                except OSError:
                    continue

    if not hook_contents:
        return CheckResult(
            name="stop_hook",
            status="warn",
            message="No stop hook found",
            details=[
                "Stop hooks prevent premature stopping in autonomous mode.",
                "Check for .agent/hooks/*/stop.sh or .githooks/stop scripts.",
            ],
        )

    # Check for tracker CLI references across all stop-related files
    combined_content = "\n".join(hook_contents)
    tracker_commands = ["count-todos", "hash-todos", "guidance"]
    found = [cmd for cmd in tracker_commands if cmd in combined_content]

    if found:
        return CheckResult(
            name="stop_hook",
            status="ok",
            message="Stop hook references tracker CLI",
        )

    return CheckResult(
        name="stop_hook",
        status="warn",
        message="Stop hook does not reference tracker commands",
        details=[
            "Consider adding tracker integration to your stop hook:",
            "  htrac count-todos   # Count blocking items",
            "  htrac hash-todos    # Fingerprint blocking items",
            "  htrac guidance      # Generate guidance file",
        ],
    )


def _check_precommit_hook(repo_root: Path | None) -> CheckResult:
    """Check #18: Check if pre-commit hook references tracker validate."""
    if repo_root is None:
        return CheckResult(
            name="precommit_hook",
            status="ok",
            message="Pre-commit hook check skipped (no git repo)",
        )

    # Look for pre-commit hooks
    hook_paths = [
        repo_root / ".githooks" / "pre-commit",
        repo_root / ".git" / "hooks" / "pre-commit",
    ]

    hook_content = None
    for hook_path in hook_paths:
        if hook_path.is_file():
            try:
                hook_content = hook_path.read_text()
                break
            except OSError:
                continue

    if hook_content is None:
        return CheckResult(
            name="precommit_hook",
            status="warn",
            message="No pre-commit hook found",
            details=[
                "A pre-commit hook can validate tracker data on every commit.",
                "Create .githooks/pre-commit or .git/hooks/pre-commit.",
            ],
        )

    if "tracker" in hook_content and "validate" in hook_content:
        return CheckResult(
            name="precommit_hook",
            status="ok",
            message="Pre-commit hook references tracker validate",
        )

    return CheckResult(
        name="precommit_hook",
        status="warn",
        message="Pre-commit hook does not reference tracker validate",
        details=[
            "Add tracker validation to your pre-commit hook:",
            "  htrac validate || exit 1",
        ],
    )


def _check_autonomous_mode(repo_root: Path | None) -> CheckResult:
    """Check #19: Cross-check autonomous mode config and loop sentinel.

    Verifies that AUTONOMOUS_MODE.txt (if present) has a recognized value
    and that the .agent/LOOP sentinel is consistent with the mode setting.
    Active autonomous modes (TRUE, BROAD, DEEP) without a LOOP sentinel
    mean the agent won't actually loop. A LOOP sentinel with OFF mode is
    also inconsistent.
    """
    if repo_root is None:
        return CheckResult(
            name="autonomous_mode",
            status="ok",
            message="Autonomous mode check skipped (no git repo)",
        )

    mode_file = repo_root / "AUTONOMOUS_MODE.txt"
    if not mode_file.exists():
        return CheckResult(
            name="autonomous_mode",
            status="ok",
            message="Autonomous mode not configured (no AUTONOMOUS_MODE.txt)",
        )

    mode = mode_file.read_text().strip().upper()
    known_modes = {"OFF", "TRUE", "BROAD", "DEEP"}
    agent_dir = repo_root / ".agent"
    has_sentinel = (agent_dir / "LOOP").exists()

    if mode not in known_modes:
        return CheckResult(
            name="autonomous_mode",
            status="warn",
            message=f"Unrecognized autonomous mode: '{mode}'",
            details=[f"Expected one of: {', '.join(sorted(known_modes))}"],
        )

    if mode == "OFF":
        if has_sentinel:
            return CheckResult(
                name="autonomous_mode",
                status="warn",
                message="Inconsistent: mode is OFF but .agent/LOOP sentinel exists",
                details=[
                    "The LOOP sentinel will be ignored since mode is OFF.",
                    "Remove .agent/LOOP or change mode to BROAD/DEEP.",
                ],
            )
        return CheckResult(
            name="autonomous_mode",
            status="ok",
            message="Autonomous mode: OFF",
        )

    # Active mode (TRUE, BROAD, DEEP)
    if not has_sentinel:
        return CheckResult(
            name="autonomous_mode",
            status="warn",
            message=f"Mode is {mode} but no .agent/LOOP sentinel found",
            details=[
                "Without the LOOP sentinel, the agent won't actually loop.",
                "Create it with: touch .agent/LOOP",
            ],
        )

    return CheckResult(
        name="autonomous_mode",
        status="ok",
        message=f"Autonomous mode: {mode} (LOOP sentinel present)",
    )


def _check_reflection_state(repo_root: Path | None) -> CheckResult:
    """Check #20: Validate last_stop_check.json schema and freshness.

    Parses the reflection state file and checks that it has the expected
    keys, the timestamp is valid ISO format, and the reflection is not
    unreasonably stale (>7 days). Advisory only.
    """
    if repo_root is None:
        return CheckResult(
            name="reflection_state",
            status="ok",
            message="Reflection state check skipped (no git repo)",
        )

    state_file = repo_root / ".agent" / "last_stop_check.json"
    if not state_file.exists():
        return CheckResult(
            name="reflection_state",
            status="ok",
            message="No reflection state file (last_stop_check.json)",
        )

    try:
        raw = json.loads(state_file.read_text())
    except (json.JSONDecodeError, ValueError):
        return CheckResult(
            name="reflection_state",
            status="warn",
            message="last_stop_check.json: invalid JSON",
            details=["The file exists but cannot be parsed."],
        )

    if not isinstance(raw, dict):
        return CheckResult(
            name="reflection_state",
            status="warn",
            message="last_stop_check.json: not a JSON object",
        )

    required_keys = {"last_completed_utc", "branch"}
    missing = required_keys - set(raw.keys())
    if missing:
        return CheckResult(
            name="reflection_state",
            status="warn",
            message=f"last_stop_check.json: missing key(s): {sorted(missing)}",
        )

    # Validate timestamp
    ts_str = raw["last_completed_utc"]
    try:
        ts = datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return CheckResult(
            name="reflection_state",
            status="warn",
            message="last_stop_check.json: unparseable timestamp",
            details=[f"Value: {ts_str!r}", "Expected ISO format: YYYY-MM-DDTHH:MM:SSZ"],
        )

    # Check freshness
    age = datetime.datetime.now(tz=datetime.timezone.utc) - ts
    if age.days > 7:
        return CheckResult(
            name="reflection_state",
            status="warn",
            message=f"Reflection state is stale ({age.days} days old)",
            details=[
                "The reflection loop may not be running.",
                f"Last reflection: {ts_str}",
            ],
        )

    return CheckResult(
        name="reflection_state",
        status="ok",
        message="Reflection state is valid and recent",
    )


def _check_sync_prerequisites(
    root: Path, repo_root: Path | None
) -> CheckResult:
    """Check #21: Verify prerequisites for ``htrac sync``.

    Advisory check (status is ``ok`` or ``warn``, never ``error``) since
    sync is an optional workflow.  Verifies:
    1. Remote ``origin`` exists.
    2. ``FORGEJO_TOKEN`` present in ``.env`` or environment.
    3. Git identity configured (``user.name``, ``user.email``).
    """
    if repo_root is None:
        return CheckResult(
            name="sync_prerequisites",
            status="ok",
            message="Sync prerequisites check skipped (no git repo)",
        )

    problems: list[str] = []

    # 1. Remote origin
    try:
        remote_result = subprocess.run(  # nosec B603, B607
            ["git", "remote", "get-url", "origin"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            check=False,
        )
        if remote_result.returncode != 0:
            problems.append("No remote 'origin' configured")
    except FileNotFoundError:  # pragma: no cover — git always available in CI
        problems.append("git not found")

    # 2. FORGEJO_TOKEN
    has_token = bool(os.environ.get("FORGEJO_TOKEN"))
    if not has_token:
        # Check .env file
        env_path = repo_root / ".env"
        if env_path.is_file():
            content = env_path.read_text()
            for line in content.splitlines():
                if line.strip().startswith("FORGEJO_TOKEN="):
                    value = line.strip().split("=", 1)[1].strip()
                    if value:
                        has_token = True
                    break
    if not has_token:
        problems.append("FORGEJO_TOKEN not found in .env or environment")

    # 3. Git identity
    if not problems or "git not found" not in problems:
        try:
            name_result = subprocess.run(  # nosec B603, B607
                ["git", "config", "user.name"],  # noqa: S607
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                check=False,
            )
            email_result = subprocess.run(  # nosec B603, B607
                ["git", "config", "user.email"],  # noqa: S607
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                check=False,
            )
            if not name_result.stdout.strip() or not email_result.stdout.strip():
                problems.append("Git identity not configured (user.name / user.email)")
        except FileNotFoundError:  # pragma: no cover — git always available in CI
            pass  # Already caught above

    if problems:
        return CheckResult(
            name="sync_prerequisites",
            status="warn",
            message=f"Sync prerequisites: {problems[0]}",
            details=[
                "htrac sync requires these to push tracker changes via PR.",
                *[f"  - {p}" for p in problems],
            ],
        )

    return CheckResult(
        name="sync_prerequisites",
        status="ok",
        message="Sync prerequisites met",
    )


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------


def run_setup(root: Path, repo_root: Path | None = None) -> list[CheckResult]:
    """Run all setup checks and return results.

    Args:
        root: Path to the .agent/ directory.
        repo_root: Git repo root (for agentic infra checks). If None,
            auto-detected from root via _find_git_dir().

    Returns:
        List of CheckResult objects for all checks.
    """
    results: list[CheckResult] = []

    # Check 1: Git repo
    git_result, detected_repo_root = _check_git_repo(root)
    results.append(git_result)
    if repo_root is None:
        repo_root = detected_repo_root

    # Part 1: Core infrastructure
    results.append(_check_directory_structure(root))       # 2
    results.append(_check_gitattributes(root))             # 3
    results.append(_check_gitignore(root))                 # 4
    results.append(_check_config_template(root))           # 5
    results.append(_check_config_yaml(root))               # 6
    results.append(_check_config_validation(root))         # 7
    results.append(_check_config_drift(root))              # 8
    results.append(_check_actor_resolution(root))          # 9
    results.append(_check_config_ownership(root))          # 10
    results.append(_check_group_permissions(root, repo_root))  # 11
    results.append(_check_ops_writable(root))              # 12
    results.append(_check_textconv(root, repo_root))       # 13
    results.append(_check_existing_data(root))             # 14

    # Part 2: Agentic infrastructure
    results.append(_check_tracker_wrapper(repo_root))      # 15
    results.append(_check_agents_md(repo_root))            # 16
    results.append(_check_stop_hook(repo_root))            # 17
    results.append(_check_precommit_hook(repo_root))       # 18
    results.append(_check_autonomous_mode(repo_root))      # 19
    results.append(_check_reflection_state(repo_root))     # 20
    results.append(_check_sync_prerequisites(root, repo_root))  # 21

    return results


def format_results(results: list[CheckResult]) -> tuple[str, int]:
    """Format check results for terminal output.

    Returns:
        (formatted_text, exit_code) where exit_code is 0 if no errors, 1 otherwise.
    """
    lines: list[str] = []
    separator = "-" * 47

    lines.append(separator)

    for r in results:
        tag = f"[{r.status}]"
        lines.append(f"{tag:<8}{r.message}")
        for detail in r.details:
            lines.append(f"        {detail}")

    lines.append(separator)

    # Summary
    counts: dict[str, int] = {"ok": 0, "fixed": 0, "warn": 0, "error": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    parts: list[str] = []
    if counts["fixed"]:
        parts.append(f"{counts['fixed']} fixed")
    if counts["warn"]:
        parts.append(f"{counts['warn']} warning{'s' if counts['warn'] != 1 else ''}")
    if counts["error"]:
        parts.append(f"{counts['error']} error{'s' if counts['error'] != 1 else ''}")

    if parts:
        lines.append(f"Setup complete. {', '.join(parts)}.")
    else:
        lines.append("Setup complete. All checks passed.")

    exit_code = 1 if counts["error"] else 0
    return "\n".join(lines), exit_code


def results_to_json(results: list[CheckResult]) -> dict[str, Any]:
    """Convert check results to a JSON-serializable dict."""
    counts: dict[str, int] = {"ok": 0, "fixed": 0, "warn": 0, "error": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    return {
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "message": r.message,
                "details": r.details,
            }
            for r in results
        ],
        "summary": counts,
    }


def generate_human_shim(root: Path) -> str:
    """Generate a copy-paste command block for the human user's terminal.

    Detects the repo path, venv location, and shared group (if two-user
    setup is active) to produce a ready-to-run sequence of commands for
    the human user. This is printed when the setup wizard is run as the
    agent user and they decline to continue.

    The shim covers three scenarios:
    1. Running setup as the human user
    2. Fixing group permissions (if two-user setup detected)
    3. Ongoing TUI access from the human terminal
    """
    # Find repo root (git root or parent of .agent/)
    repo_root = root.parent.resolve()
    git_dir = _find_git_dir(repo_root)
    if git_dir is not None:
        repo_root = git_dir.parent

    # Detect the venv
    venv_path: Path | None = None
    for candidate in [repo_root / ".venv", repo_root / "venv"]:
        if (candidate / "bin" / "activate").exists():
            venv_path = candidate
            break
    if venv_path is None:
        # Check VIRTUAL_ENV env var
        env_venv = os.environ.get("VIRTUAL_ENV")
        if env_venv and (Path(env_venv) / "bin" / "activate").exists():
            venv_path = Path(env_venv)

    # Detect shared group from .ops directories
    group_name: str | None = None
    current_primary_gid = pwd.getpwuid(os.getuid()).pw_gid
    for ops_dir in [root / "tracker" / ".ops", root / "tracker-workspace" / ".ops"]:
        if ops_dir.exists():
            dir_gid = ops_dir.stat().st_gid
            if dir_gid != current_primary_gid:
                try:
                    group_name = grp.getgrgid(dir_gid).gr_name
                except KeyError:
                    pass
                break

    # Detect if home dir needs traversal (repo under agent's home)
    agent_home = Path.home()
    needs_traversal = str(repo_root).startswith(str(agent_home))

    lines: list[str] = []
    lines.append("# ── Paste into the human user's terminal ──────────────")

    if needs_traversal:
        lines.append(f"sudo chmod o+rx {agent_home}")

    lines.append(f"cd {repo_root}")

    if venv_path is not None:
        activate = venv_path / "bin" / "activate"
        lines.append(f"source {activate}")

    lines.append("htrac setup")
    lines.append("")

    # If two-user group setup is active or should be set up
    if group_name:
        lines.append("# ── Fix group permissions (after setup) ────────────────")
        lines.append(
            f"sudo chgrp -R {group_name} {repo_root / '.agent' / 'tracker'}"
            f" {repo_root / '.agent' / 'tracker-workspace'}"
        )
        lines.append(
            f"sudo chmod -R g+rws {repo_root / '.agent' / 'tracker' / '.ops'}"
            f" {repo_root / '.agent' / 'tracker-workspace'}"
            f" {repo_root / '.agent' / 'tracker-workspace' / '.ops'}"
            f" {repo_root / '.agent' / 'tracker-workspace' / 'stealth'}"
        )
        # .git/ needs group-write too for htrac sync (branch create, commit)
        git_dir = repo_root / ".git"
        if git_dir.is_dir():
            lines.append(f"sudo chgrp -R {group_name} {git_dir}")
            lines.append(f"sudo chmod -R g+w {git_dir}")
            lines.append(
                f"sudo find {git_dir} -type d -exec chmod g+s {{}} +"
            )
        lines.append(f"newgrp {group_name}")
        lines.append("")

    lines.append("# ── Quick TUI access (anytime) ─────────────────────────")
    tui_parts = []
    if needs_traversal:
        tui_parts.append(f"cd {repo_root}")
    if venv_path is not None:
        tui_parts.append(f"source {venv_path / 'bin' / 'activate'}")
    tui_parts.append("htrac tui")
    lines.append(" && ".join(tui_parts))

    return "\n".join(lines)
