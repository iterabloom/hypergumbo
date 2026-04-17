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
  This matches the hard/soft TODO convention.
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
    violated_items: list[CompiledItem] = []
    soft_items: list[CompiledItem] = []
    all_items: list[CompiledItem] = []

    for t in tiers_to_check:
        store = ts._tier_stores[t]
        for item in store._compile_all():
            item.tier = t
            all_items.append(item)
            if item.status in hard_statuses:
                hard_items.append(item)
            elif item.status == "violated":
                violated_items.append(item)
            elif item.status in soft_statuses:
                soft_items.append(item)

    # Sort by priority (ascending — P0 first) then ID for stability
    hard_items.sort(key=lambda i: (i.priority, i.id))
    violated_items.sort(key=lambda i: (i.priority, i.id))
    soft_items.sort(key=lambda i: (i.priority, i.id))

    # Scan for unread human messages
    blocking_ids = (
        {i.id for i in hard_items}
        | {i.id for i in violated_items}
        | {i.id for i in soft_items}
    )
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

    # REPLY-FIRST CYCLE branch (WI-ripuz): when any unread human messages
    # exist — blocking or non-blocking — the guidance document changes
    # shape entirely. The TODO list is suppressed to force the agent off
    # the "forward-march vibe" and onto reply debt. See ADR-0008 and
    # WI-ripuz for the rationale.
    unread_total = len(items_with_unread_blocking) + len(unread_non_blocking)
    if unread_total > 0:
        filepath.write_text(
            _build_reply_first_guidance(
                timestamp=timestamp,
                all_items=all_items,
                items_with_unread_blocking=items_with_unread_blocking,
                unread_non_blocking=unread_non_blocking,
            )
        )
        return str(filepath.resolve())

    # Default (no-reply-debt) branch: the REPLY-FIRST branch above handles
    # any scenario where unread_non_blocking or items_with_unread_blocking
    # is non-empty, so here both are empty and we only emit the straight
    # TODO / violated / soft listing.
    lines: list[str] = []
    lines.append(f"# Stop Hook Guidance — {timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("")
    lines.append("## Status")
    lines.append(f"- Hard TODO Items: {len(hard_items)}")
    if violated_items:
        lines.append(f"- Invariant Violations: {len(violated_items)}")
    lines.append(f"- Soft TODO Items: {len(soft_items)}")
    lines.append("")

    if hard_items:
        lines.append("## Hard TODO Items")
        for item in hard_items:
            lines.append(f"- [{item.id}] P{item.priority} {item.title}")
        lines.append("")

    if violated_items:
        lines.append("## Invariant Violations")
        for item in violated_items:
            lines.append(f"- [{item.id}] P{item.priority} {item.title}")
        lines.append("")

    if soft_items:
        lines.append("## Soft TODO Items")
        for item in soft_items:
            lines.append(f"- [{item.id}] P{item.priority} {item.title}")
        lines.append("")

    lines.append("## Guidance")
    lines.append(
        "Hard TODO items: investigate deeply; assume the item is "
        "a symptom of a structural defect."
    )
    if violated_items:
        lines.append(
            "Invariant violations: these are blocking. Investigate the "
            "root cause and fix or escalate."
        )
    lines.append(
        "Soft TODO items: resolve the issue normally. As always, "
        "revise your beliefs when new information comes to light."
    )
    lines.append("")

    filepath.write_text("\n".join(lines))
    return str(filepath.resolve())


def _build_reply_first_guidance(
    *,
    timestamp: datetime.datetime,
    all_items: list[CompiledItem],
    items_with_unread_blocking: set[str],
    unread_non_blocking: list[CompiledItem],
) -> str:
    """Render the REPLY-FIRST CYCLE guidance document (WI-ripuz).

    Called from generate_guidance when any unread human messages exist.
    Deliberately omits the hard/violated/soft TODO listings and the
    normal "Guidance" paragraph — the whole point of this branch is to
    make reply debt the only visible assignment.
    """
    ts = timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
    blocked = [i for i in all_items if i.id in items_with_unread_blocking]
    blocked.sort(key=lambda i: (i.priority, i.id))
    unread_total = len(blocked) + len(unread_non_blocking)

    lines: list[str] = []
    lines.append(f"# REPLY-FIRST CYCLE — {ts}")
    lines.append("")
    lines.append(
        f"**{unread_total} unread human message(s) across "
        f"{unread_total} item(s).**"
    )
    lines.append("")
    lines.append(
        "Take a breather from the \"forward-march vibe\" of autonomous "
        "mode. Do not start new code tasks, do not run bakeoffs, do "
        "not pick from tracker ready. Your only assignment is to clear "
        "reply debt. For each item:"
    )
    lines.append("")
    lines.append(
        "1. `scripts/tracker show <ID>` — load the full thread + description"
    )
    lines.append("2. Classify: approval, directive, or question")
    lines.append(
        "3. Questions: plan the investigation (Read/Grep, Explore subagent, "
        "WebSearch on allowlisted domains) BEFORE drafting"
    )
    lines.append(
        "4. `scripts/tracker discuss <ID>` — post the reply"
    )
    lines.append("")

    if blocked:
        lines.append("## Blocking items with unread messages")
        for item in blocked:
            msgs = unread_human_messages(item)
            lines.append(
                f"- [{item.id}] P{item.priority} {item.title} "
                f"({len(msgs)} unread)"
            )
        lines.append("")

    if unread_non_blocking:
        lines.append("## Non-blocking items with unread messages")
        for item in unread_non_blocking:
            msgs = unread_human_messages(item)
            lines.append(
                f"- [{item.id}] P{item.priority} {item.title} "
                f"({len(msgs)} unread)"
            )
        lines.append("")

    lines.append(
        "Reply to every item above before resuming TODO or bakeoff work. "
        "The TODO listing is intentionally suppressed in this cycle."
    )
    lines.append("")
    return "\n".join(lines)


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
