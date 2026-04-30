#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Session-start cadence check for the Fundamental Concept Audit.

Reads ``.agent/.last_concept_audit.json`` to find the SHA + timestamp
of the most recent recorded audit. Counts development commits between
that SHA and HEAD (excluding tracker auto-syncs). If the count exceeds
the threshold (default 72, configurable in ``.agent/tracker/config.yaml``
under ``concept_audit.commit_threshold``), prints a soft reminder that
the session-start hook will inject into the agent's context.

Soft prompt only: the hook reminds the agent to *surface* the audit to
the human, who picks the next suspect domain. The audit playbook flags
"running an audit while you're mid-feature" as an anti-pattern, so when
the working tree has uncommitted changes the message defers to the
next clean-tree session.

Threshold derivation: median ADR-to-ADR cadence is ≈ 4.5 calendar days;
the audit cadence target is ≈ 2× that; empirical commits/calendar-day
≈ 22 → ≈ 66 commits per 3 days, rounded up to 72 for a small margin.

Audits are recorded by ``scripts/concept-audit-record <suspect-domain>``
which updates the state file with the current HEAD SHA, ISO timestamp,
and suspect name.

Failure modes are silent: a missing state file, missing config, GC'd
SHA, or git unavailable all return without output. The cadence hook is
a calibration nudge, not a hard gate, so swallowing errors is the right
default.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Discover repo root: this file lives at .agent/hooks/_shared/.
REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = REPO_ROOT / ".agent" / ".last_concept_audit.json"
TRACKER_CONFIG = REPO_ROOT / ".agent" / "tracker" / "config.yaml"
DEFAULT_THRESHOLD = 72


def load_threshold(config_path: Path = TRACKER_CONFIG) -> int:
    """Read the cadence threshold from ``config_path``.

    Returns ``DEFAULT_THRESHOLD`` if the file is missing, yaml is
    unavailable, or the key is absent — the cadence hook is a soft
    nudge, so missing config means "use the default" rather than
    "fail."
    """
    if not config_path.exists():
        return DEFAULT_THRESHOLD
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — yaml is a tracker dep
        return DEFAULT_THRESHOLD
    try:
        config = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(config, dict):
            return DEFAULT_THRESHOLD
        node = config.get("concept_audit") or {}
        if not isinstance(node, dict):
            return DEFAULT_THRESHOLD
        return int(node.get("commit_threshold", DEFAULT_THRESHOLD))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_THRESHOLD


def load_state(state_path: Path = STATE_FILE) -> dict | None:
    """Load the last-audit state, or ``None`` if absent or corrupt."""
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def commits_since(sha: str, repo_root: Path = REPO_ROOT) -> int:
    """Count commits ``sha..HEAD``, excluding tracker auto-sync paths.

    Returns ``-1`` on git error (sha GC'd, repo not initialised, etc.)
    so the caller can distinguish "no commits since" (return 0) from
    "could not compute" (return -1).
    """
    try:
        result = subprocess.run(
            [
                "git", "rev-list", "--count", f"{sha}..HEAD",
                "--",
                ":(exclude).agent/tracker/.ops",
                ":(exclude).agent/tracker-workspace/.ops",
                ":(top)",
            ],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return -1
    if result.returncode != 0:
        return -1
    try:
        return int(result.stdout.strip())
    except ValueError:
        return -1


def has_dirty_tree(repo_root: Path = REPO_ROOT) -> bool:
    """Return True if the working tree has uncommitted non-auto-sync changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        # status fields are 2 chars + 1 space + path
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path.startswith(".agent/tracker/.ops"):
            continue
        if path.startswith(".agent/tracker-workspace/.ops"):
            continue
        if path:
            return True
    return False


def build_message(
    *, commits: int, threshold: int, suspect: str, last_date: str, dirty: bool,
) -> str:
    """Build the human-readable cadence reminder."""
    suspect_str = suspect or "an unrecorded domain"
    date_str = last_date or "an unknown date"
    header = (
        f"[Fundamental Concept Audit — cadence reminder]\n\n"
        f"It has been {commits} commits since the last conceptual audit "
        f"({suspect_str} on {date_str}). Threshold: {threshold}."
    )
    if dirty:
        body = (
            "\n\nWorking tree has uncommitted changes. The playbook flags "
            "running an audit mid-feature as an anti-pattern; defer this "
            "until the tree is clean and surface the reminder then."
        )
    else:
        body = (
            "\n\nSurface this to the user: ask whether to run a "
            "fundamental concept audit now and which suspect domain to "
            "pick. The playbook lives at "
            ".agent/agent_playbooks_protocols_sops_skills/"
            "what-if-we-dont-know-what-the-fuck-we-are-talking-about-"
            "audit-aka-fundamental-concept-audit.md. Record completion "
            "with: scripts/concept-audit-record <suspect-domain>"
        )
    return header + body


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    _ = argv  # not parsed; kept for parity with other hook scripts
    state = load_state()
    if state is None:
        return 0
    last_sha = state.get("last_audit_sha") or ""
    if not last_sha:
        return 0

    n = commits_since(last_sha)
    if n < 0:
        return 0  # could not compute; stay silent
    threshold = load_threshold()
    if n < threshold:
        return 0

    msg = build_message(
        commits=n,
        threshold=threshold,
        suspect=state.get("suspect_domain", ""),
        last_date=state.get("last_audit_iso_date", ""),
        dirty=has_dirty_tree(),
    )
    print(msg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
