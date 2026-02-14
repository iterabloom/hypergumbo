# SPDX-License-Identifier: MPL-2.0
"""Stop hook helpers for the hypergumbo tracker.

Provides count_todos and hash_todos functions used by the stop hook
governance system. These functions wrap TrackerSet with fail-closed
semantics: all exceptions are caught and result in exit code 1,
preventing silent governance failures.

Design rationale:
- Fail-closed: if the tracker is broken, the stop hook blocks. This is
  intentional — a broken tracker should not silently allow stopping.
- Hard/soft distinction: --hard returns only statuses containing "hard"
  from the blocking_statuses list. --soft returns the remainder.
  This matches the **TODO!** (hard) vs **TODO** (soft) convention.
- hash_todos provides a fingerprint for circuit-breaker detection:
  if the hash hasn't changed between stop attempts, no progress was made.

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from hypergumbo_tracker.models import TrackerConfig, Tier, load_config
from hypergumbo_tracker.trackerset import TrackerSet


def count_todos(
    tracker_root: Path,
    *,
    hard: bool = False,
    soft: bool = False,
    config: TrackerConfig | None = None,
) -> int:
    """Count blocking items respecting scope and hard/soft filtering.

    Args:
        tracker_root: Path to the .agent/ directory.
        hard: If True, count only hard-blocking statuses (containing "hard").
        soft: If True, count only soft-blocking statuses (remaining blocking).
        config: Optional TrackerConfig. Loaded from tracker_root if None.

    Returns:
        Count of blocking items.
    """
    if config is None:
        config = load_config(tracker_root / "tracker")

    ts = TrackerSet(tracker_root, config=config)
    blocking_statuses = _filter_blocking_statuses(
        config.blocking_statuses, hard=hard, soft=soft
    )

    tiers_to_count: list[Tier]
    if config.scope == "workspace":
        tiers_to_count = [Tier.WORKSPACE, Tier.STEALTH]
    else:
        tiers_to_count = list(Tier)

    count = 0
    for t in tiers_to_count:
        store = ts._tier_stores[t]
        for item in store._compile_all():
            if item.status in blocking_statuses:
                count += 1

    return count


def hash_todos(
    tracker_root: Path,
    *,
    config: TrackerConfig | None = None,
) -> str:
    """Compute SHA256 fingerprint of blocking items for circuit-breaker detection.

    Returns a hex string of SHA256(sorted lines of "id\\tstatus\\ttitle\\n").
    Only includes identity + status fields — ignores discussion, fields, etc.

    Args:
        tracker_root: Path to the .agent/ directory.
        config: Optional TrackerConfig. Loaded from tracker_root if None.

    Returns:
        SHA256 hex string.
    """
    if config is None:
        config = load_config(tracker_root / "tracker")

    ts = TrackerSet(tracker_root, config=config)
    blocking_set = set(config.blocking_statuses)

    tiers_to_count: list[Tier]
    if config.scope == "workspace":
        tiers_to_count = [Tier.WORKSPACE, Tier.STEALTH]
    else:
        tiers_to_count = list(Tier)

    lines: list[str] = []
    for t in tiers_to_count:
        store = ts._tier_stores[t]
        for item in store._compile_all():
            if item.status in blocking_set:
                lines.append(f"{item.id}\t{item.status}\t{item.title}\n")

    lines.sort()
    h = hashlib.sha256("".join(lines).encode())
    return h.hexdigest()


def _filter_blocking_statuses(
    blocking_statuses: list[str],
    *,
    hard: bool = False,
    soft: bool = False,
) -> set[str]:
    """Filter blocking statuses by hard/soft.

    --hard: statuses containing "hard" in the name.
    --soft: remaining blocking statuses (not containing "hard").
    Neither: all blocking statuses.
    """
    if not hard and not soft:
        return set(blocking_statuses)

    hard_set = {s for s in blocking_statuses if "hard" in s}
    soft_set = {s for s in blocking_statuses if "hard" not in s}

    if hard:
        return hard_set
    return soft_set


def count_todos_safe(
    tracker_root: Path,
    *,
    hard: bool = False,
    soft: bool = False,
) -> int:
    """Fail-closed wrapper around count_todos.

    Catches ALL exceptions and returns -1 on failure (caller should
    treat -1 as "blocked" and exit 1).

    Returns:
        Count of blocking items, or -1 on error.
    """
    try:
        return count_todos(tracker_root, hard=hard, soft=soft)
    except Exception:
        print(
            "hypergumbo-tracker: count-todos failed (fail-closed)",
            file=sys.stderr,
        )
        return -1


def hash_todos_safe(tracker_root: Path) -> str | None:
    """Fail-closed wrapper around hash_todos.

    Returns None on error (caller should exit 1).
    """
    try:
        return hash_todos(tracker_root)
    except Exception:
        print(
            "hypergumbo-tracker: hash-todos failed (fail-closed)",
            file=sys.stderr,
        )
        return None
