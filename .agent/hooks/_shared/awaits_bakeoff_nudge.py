#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compute the ``awaits_bakeoff_validation`` backlog nudge for the stop hook.

When items carrying the ``awaits_bakeoff_validation`` tag accumulate
beyond a threshold AND no DEEP bakeoff cycle has completed within a
configured window, the stop hook appends a short nudge section to its
guidance file encouraging a DEEP cycle to validate the pending claims.

This file is the pure-Python worker: invoked as a subprocess by
``stop_logic.sh``, it prints the nudge markdown (or nothing) to stdout.

The decision logic deliberately lives here rather than in bash because
(a) the YAML config parse, JSON tracker output, and mtime math are all
clumsy in bash, and (b) putting it in Python gives us direct pytest
coverage without a shell harness.

Config (``.agent/tracker/config.yaml`` under ``stop_hook``):

    stop_hook:
      blocking_statuses: [todo_hard, todo_soft, violated, pending_validation, in_progress]
      awaits_bakeoff_validation_nudge:
        threshold: 5
        stale_cycle_hours: 72

Missing keys fall back to the module defaults below. The nudge is
advisory: any unexpected failure (missing config, malformed YAML,
tracker CLI failure, unparseable JSON) is swallowed and no nudge is
printed, so the stop hook is never blocked by this module.

The "stale" check uses strict-greater-than: an age of exactly
``stale_cycle_hours`` is not yet considered stale, matching the common
"72h window" phrasing where hour 73 is when you'd actually nudge.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dep in CI
    yaml = None


DEFAULT_THRESHOLD = 5
DEFAULT_STALE_HOURS = 72
DEFAULT_BLOCKING_STATUSES = (
    "todo_hard",
    "todo_soft",
    "violated",
    "pending_validation",
    "in_progress",
)


def load_nudge_config(config_path: Path) -> dict:
    """Load the nudge config from the tracker config YAML.

    Returns a dict with ``threshold``, ``stale_cycle_hours``, and
    ``blocking_statuses``. Missing keys get module defaults. Missing or
    malformed files also return defaults — the nudge must never fail
    the stop hook.
    """
    defaults = {
        "threshold": DEFAULT_THRESHOLD,
        "stale_cycle_hours": DEFAULT_STALE_HOURS,
        "blocking_statuses": list(DEFAULT_BLOCKING_STATUSES),
    }
    if yaml is None or not config_path.exists():
        return defaults
    try:
        with config_path.open() as f:
            cfg = yaml.safe_load(f)
    except Exception:
        return defaults
    if not isinstance(cfg, dict):
        return defaults
    stop_hook = cfg.get("stop_hook") or {}
    if not isinstance(stop_hook, dict):
        return defaults
    blocking = stop_hook.get("blocking_statuses")
    if isinstance(blocking, list) and blocking:
        defaults["blocking_statuses"] = list(blocking)
    nudge = stop_hook.get("awaits_bakeoff_validation_nudge") or {}
    if isinstance(nudge, dict):
        try:
            defaults["threshold"] = int(nudge.get("threshold", defaults["threshold"]))
        except (TypeError, ValueError):
            pass
        try:
            defaults["stale_cycle_hours"] = int(
                nudge.get("stale_cycle_hours", defaults["stale_cycle_hours"])
            )
        except (TypeError, ValueError):
            pass
    return defaults


def count_tagged_items(tracker_cli: Path, blocking_statuses: list[str]) -> int:
    """Count items with the ``awaits_bakeoff_validation`` tag whose
    status is in ``blocking_statuses``. Returns 0 on any failure."""
    if not tracker_cli.exists():
        return 0
    try:
        result = subprocess.run(
            [
                str(tracker_cli),
                "--json",
                "list",
                "--tag",
                "awaits_bakeoff_validation",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception:
        return 0
    if result.returncode != 0:
        return 0
    try:
        items = json.loads(result.stdout)
    except Exception:
        return 0
    if not isinstance(items, list):
        return 0
    blocking = set(blocking_statuses)
    return sum(1 for it in items if isinstance(it, dict) and it.get("status") in blocking)


def find_latest_deep_cycle_mtime(bakeoff_root: Path) -> float | None:
    """Return the mtime (epoch seconds) of the most recent
    ``deep-*/state.json`` under ``bakeoff_root``, or ``None`` if no such
    session exists. BROAD sessions are intentionally ignored — this
    nudge is specifically about DEEP validation."""
    if not bakeoff_root.exists() or not bakeoff_root.is_dir():
        return None
    latest: float | None = None
    for entry in bakeoff_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("deep-"):
            continue
        state = entry / "state.json"
        if not state.exists():
            continue
        try:
            mtime = state.stat().st_mtime
        except OSError:  # pragma: no cover - defensive: stat after exists() race
            continue
        if latest is None or mtime > latest:
            latest = mtime
    return latest


def compute_nudge(
    *,
    count: int,
    last_deep_mtime: float | None,
    now_epoch: float,
    threshold: int,
    stale_hours: int,
) -> str:
    """Return the nudge markdown block, or ``''`` if conditions aren't met."""
    if count < threshold:
        return ""
    if last_deep_mtime is None:
        age_line = (
            "no DEEP bakeoff cycle has ever completed (never run; "
            f"stale window: {stale_hours}h)"
        )
    else:
        age_hours = (now_epoch - last_deep_mtime) / 3600.0
        if age_hours <= stale_hours:
            return ""
        age_line = (
            f"the most recent DEEP cycle completed {int(age_hours)}h ago "
            f"(stale window: {stale_hours}h)"
        )
    return (
        "\n\n---\n"
        "## AWAITS_BAKEOFF_VALIDATION BACKLOG\n"
        f"{count} item(s) carry the `awaits_bakeoff_validation` tag "
        f"(threshold: {threshold}), and {age_line}.\n"
        "Consider running `./scripts/bakeoff-deep cycle` to validate the "
        "pending claims, or review the list with "
        "`./scripts/tracker list --tag awaits_bakeoff_validation`.\n"
        "See AGENTS.md §Bakeoff Validation Discipline for the tag's "
        "semantics.\n"
    )


def _default_bakeoff_root(repo_root: Path) -> Path:
    repo_name = repo_root.name
    return Path(os.path.expanduser(f"~/{repo_name}_lab_notebook/bakeoff_artifacts"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute awaits_bakeoff_validation nudge for stop hook."
    )
    parser.add_argument("repo_root", help="Absolute path to the repo root")
    parser.add_argument(
        "--bakeoff-root",
        default=None,
        help="Override the bakeoff_artifacts directory (testing).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override 'now' in epoch seconds (testing).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    config = load_nudge_config(repo_root / ".agent" / "tracker" / "config.yaml")
    tracker_cli = repo_root / "scripts" / "tracker"
    count = count_tagged_items(tracker_cli, config["blocking_statuses"])
    bakeoff_root = (
        Path(args.bakeoff_root) if args.bakeoff_root else _default_bakeoff_root(repo_root)
    )
    last_mtime = find_latest_deep_cycle_mtime(bakeoff_root)
    now_epoch = float(args.now) if args.now else time.time()
    nudge = compute_nudge(
        count=count,
        last_deep_mtime=last_mtime,
        now_epoch=now_epoch,
        threshold=config["threshold"],
        stale_hours=config["stale_cycle_hours"],
    )
    sys.stdout.write(nudge)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
