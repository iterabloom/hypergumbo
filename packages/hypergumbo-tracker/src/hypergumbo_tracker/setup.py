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
1. **Core infrastructure** (checks 1-12): directory structure, git plumbing,
   config validation, textconv driver, data integrity.
2. **Agentic infrastructure** (checks 13-16): wrapper scripts, agent
   instructions, hook integration. Read-only / advisory only.
3. **Summary** (check 17): aggregate counts and exit code.

Entry point: ``run_setup(root, repo_root)`` returns a list of CheckResult.
The CLI handler in cli.py formats and prints them.
"""

from __future__ import annotations

import os
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
# Tracker concept definitions for AGENTS.md scanning (check #14)
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


def _check_ops_writable(root: Path) -> CheckResult:
    """Check #10: Verify .ops/ directories are writable."""
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


def _check_textconv(root: Path, repo_root: Path | None) -> CheckResult:
    """Check #11: Git textconv driver for .ops files."""
    if repo_root is None:
        return CheckResult(
            name="textconv",
            status="ok",
            message="Textconv check skipped (no git repo)",
        )

    fixed: list[str] = []

    # Check git config for textconv driver
    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "config", "diff.tracker-ops.textconv"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        if result.returncode != 0 or not result.stdout.strip():
            # Set the textconv driver
            subprocess.run(  # nosec B603, B607
                [  # noqa: S607
                    "git",
                    "config",
                    "diff.tracker-ops.textconv",
                    "python -m hypergumbo_tracker.cli textconv",
                ],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                check=True,
            )
            fixed.append("textconv driver")
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
    """Check #12: Validate existing .ops files if any exist."""
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
    """Check #13: Check if scripts/tracker wrapper exists and is executable."""
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
    """Check #14: Scan AGENTS.md for key tracker concepts."""
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
    """Check #15: Check if stop hooks reference tracker commands."""
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

    hook_content = None
    for hook_dir in hook_patterns:
        if not hook_dir.is_dir():
            continue
        # Search recursively for stop-related scripts
        for p in hook_dir.rglob("*"):
            if p.is_file() and "stop" in p.name.lower():
                try:
                    hook_content = p.read_text()
                    break
                except OSError:
                    continue
        if hook_content is not None:
            break

    if hook_content is None:
        return CheckResult(
            name="stop_hook",
            status="warn",
            message="No stop hook found",
            details=[
                "Stop hooks prevent premature stopping in autonomous mode.",
                "Check for .agent/hooks/*/stop.sh or .githooks/stop scripts.",
            ],
        )

    # Check for tracker CLI references
    tracker_commands = ["count-todos", "hash-todos", "guidance"]
    found = [cmd for cmd in tracker_commands if cmd in hook_content]

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
    """Check #16: Check if pre-commit hook references tracker validate."""
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
    results.append(_check_ops_writable(root))              # 10
    results.append(_check_textconv(root, repo_root))       # 11
    results.append(_check_existing_data(root))             # 12

    # Part 2: Agentic infrastructure
    results.append(_check_tracker_wrapper(repo_root))      # 13
    results.append(_check_agents_md(repo_root))            # 14
    results.append(_check_stop_hook(repo_root))            # 15
    results.append(_check_precommit_hook(repo_root))       # 16

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
