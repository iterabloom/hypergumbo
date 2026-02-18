# SPDX-License-Identifier: MPL-2.0
"""Stop hook helpers for the hypergumbo tracker.

Provides count_todos, hash_todos, and generate_guidance functions used
by the stop hook governance system. These functions wrap TrackerSet with
fail-closed semantics: all exceptions are caught and result in exit code 1,
preventing silent governance failures.

Design rationale:
- Fail-closed: if the tracker is broken, the stop hook blocks. This is
  intentional — a broken tracker should not silently allow stopping.
- Hard/soft distinction: --hard returns only statuses containing "hard"
  from the blocking_statuses list. --soft returns the remainder.
  This matches the **TODO!** (hard) vs **TODO** (soft) convention.
- hash_todos provides a fingerprint for circuit-breaker detection:
  if the hash hasn't changed between stop attempts, no progress was made.
- generate_guidance writes a markdown file listing blocking items sorted
  by priority. This replaces the grep-based guidance generation in
  stop_logic.sh (Phase 1: dual-mode with grep fallback).

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import sys
from pathlib import Path

from hypergumbo_tracker.models import CompiledItem, TrackerConfig, Tier, load_config
from hypergumbo_tracker.store import has_unread_human_messages, unread_human_messages
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


def generate_guidance(
    tracker_root: Path,
    *,
    guidance_dir: Path | None = None,
    config: TrackerConfig | None = None,
) -> str:
    """Generate guidance markdown listing blocking items.

    Loads the tracker, collects all blocking items respecting scope,
    splits into hard/soft groups, sorts by priority then ID, and
    writes a markdown file to guidance_dir.

    Args:
        tracker_root: Path to the .agent/ directory.
        guidance_dir: Directory to write guidance file. Defaults to
            ~/hypergumbo_lab_notebook/guidance_log/.
        config: Optional TrackerConfig. Loaded from tracker_root if None.

    Returns:
        Absolute path to the generated guidance file.
    """
    if config is None:
        config = load_config(tracker_root / "tracker")

    if guidance_dir is None:
        guidance_dir = (
            Path(os.environ.get("HOME", str(Path.home())))
            / "hypergumbo_lab_notebook"
            / "guidance_log"
        )

    guidance_dir.mkdir(parents=True, exist_ok=True)

    ts = TrackerSet(tracker_root, config=config)
    hard_statuses = _filter_blocking_statuses(
        config.blocking_statuses, hard=True,
    )
    soft_statuses = _filter_blocking_statuses(
        config.blocking_statuses, soft=True,
    )

    tiers_to_check: list[Tier]
    if config.scope == "workspace":
        tiers_to_check = [Tier.WORKSPACE, Tier.STEALTH]
    else:
        tiers_to_check = list(Tier)

    hard_items: list[CompiledItem] = []
    soft_items: list[CompiledItem] = []
    all_items: list[CompiledItem] = []

    for t in tiers_to_check:
        store = ts._tier_stores[t]
        for item in store._compile_all():
            all_items.append(item)
            if item.status in hard_statuses:
                hard_items.append(item)
            elif item.status in soft_statuses:
                soft_items.append(item)

    # Sort by priority (ascending — P0 first) then ID for stability
    hard_items.sort(key=lambda i: (i.priority, i.id))
    soft_items.sort(key=lambda i: (i.priority, i.id))

    # Scan for unread human messages
    blocking_ids = {i.id for i in hard_items} | {i.id for i in soft_items}
    items_with_unread_blocking: set[str] = set()
    unread_non_blocking: list[CompiledItem] = []

    for item in all_items:
        if has_unread_human_messages(item):
            if item.id in blocking_ids:
                items_with_unread_blocking.add(item.id)
            else:
                unread_non_blocking.append(item)

    unread_non_blocking.sort(key=lambda i: (i.priority, i.id))

    # Build guidance markdown
    timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
    filename = f"stop_guidance_{timestamp.strftime('%m%d%Y_%H%M')}.md"
    filepath = guidance_dir / filename

    lines: list[str] = []
    lines.append(f"# Stop Hook Guidance — {timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("")
    lines.append("## Status")
    lines.append(f"- Hard TODO Items: {len(hard_items)}")
    unread_hard_count = sum(1 for i in hard_items if i.id in items_with_unread_blocking)
    if unread_hard_count:
        lines.append(f"  > {unread_hard_count} with unread human message(s)")
    lines.append(f"- Soft TODO Items: {len(soft_items)}")
    unread_soft_count = sum(1 for i in soft_items if i.id in items_with_unread_blocking)
    if unread_soft_count:
        lines.append(f"  > {unread_soft_count} with unread human message(s)")
    if unread_non_blocking:
        lines.append(f"- Non-blocking items with unread messages: {len(unread_non_blocking)}")
    lines.append("")

    if hard_items:
        lines.append("## Hard TODO Items (TODO! — investigate deeply, assume structural)")
        for item in hard_items:
            tier_str = item.tier.value if item.tier else "unknown"
            unread_tag = " **[UNREAD]**" if item.id in items_with_unread_blocking else ""
            lines.append(
                f"- [{item.id}] P{item.priority} {item.title} "
                f"(status: {item.status}, tier: {tier_str}){unread_tag}"
            )
        lines.append("")

    if soft_items:
        lines.append("## Soft TODO Items (TODO — address or defer freely)")
        for item in soft_items:
            tier_str = item.tier.value if item.tier else "unknown"
            unread_tag = " **[UNREAD]**" if item.id in items_with_unread_blocking else ""
            lines.append(
                f"- [{item.id}] P{item.priority} {item.title} "
                f"(status: {item.status}, tier: {tier_str}){unread_tag}"
            )
        lines.append("")

    if unread_non_blocking:
        lines.append("## Unread Human Messages (non-blocking)")
        lines.append("These items have unread human messages but are not in blocking status.")
        for item in unread_non_blocking:
            tier_str = item.tier.value if item.tier else "unknown"
            msgs = unread_human_messages(item)
            lines.append(
                f"- [{item.id}] P{item.priority} {item.title} "
                f"(status: {item.status}, tier: {tier_str}, "
                f"{len(msgs)} unread)"
            )
        lines.append("")

    lines.append("## Guidance")
    lines.append(
        "TODO! items: investigate deeply, assume structural. "
        "Fix or explicitly DEFER with justification."
    )
    lines.append(
        "TODO items: address or defer freely. "
        "OK to defer if higher-priority work exists."
    )
    if items_with_unread_blocking or unread_non_blocking:
        lines.append(
            "Use `scripts/tracker check-messages` to read and reply to "
            "unread human messages."
        )
    lines.append("")

    filepath.write_text("\n".join(lines))
    return str(filepath.resolve())


def generate_guidance_safe(
    tracker_root: Path,
    *,
    guidance_dir: Path | None = None,
) -> str | None:
    """Fail-closed wrapper around generate_guidance.

    Returns None on error (caller should treat as "blocked").
    """
    try:
        return generate_guidance(tracker_root, guidance_dir=guidance_dir)
    except Exception:
        print(
            "hypergumbo-tracker: generate-guidance failed (fail-closed)",
            file=sys.stderr,
        )
        return None
