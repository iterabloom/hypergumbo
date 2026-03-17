# SPDX-License-Identifier: MPL-2.0
"""CLI entry points for hypergumbo-tracker.

Provides the full argparse CLI for tracker operations and the git textconv
driver for rendering .ops files as readable text.

Entry points:
- main(): Primary CLI with ~29 subcommands (add, update, list, show, ready,
  log, discuss, deps, lock, unlock, freeze, unfreeze, repair-drift, promote,
  demote, stealth, unstealth, validate, count-todos, hash-todos, guidance,
  check-messages, init, setup, sync, cache-rebuild, reconcile-reset,
  fork-setup, migrate, batch, tui).
- textconv_main(): Git textconv driver that reads an ops file and outputs
  one-line-per-field compiled state.

Design rationale:
- Single TrackerSet instance created at startup, shared across subcommands.
- --json global flag for machine-readable output.
- --tracker-root global option defaults to .agent/ (nearest ancestor).
- Exit codes: 0 = success, 1 = user error, 2 = internal error.
- TUI subcommand launches Textual app (requires textual optional dep).

See ADR-0013 for the full design specification.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 — needed for `screen -Q` query
import sys
import time
from pathlib import Path
from typing import Any

from hypergumbo_tracker.models import (
    CompiledItem,
    DiscussionEntry,
    Tier,
    load_config,
    resolve_actor,
)
from hypergumbo_tracker.store import (
    AmbiguousPrefixError,
    CorruptFileError,
    FrozenItemError,
    HumanAuthorityError,
    ItemExistsError,
    ItemNotFoundError,
    LockedFieldError,
    _parse_ops_file,
    compile_ops,
    has_unread_human_messages,
    unread_human_messages,
)
from hypergumbo_tracker.trackerset import (
    TierMovementError,
    TrackerSet,
)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_USER_ERROR = 1
EXIT_INTERNAL_ERROR = 2


# ---------------------------------------------------------------------------
# Auto-sync constants
# ---------------------------------------------------------------------------

_MUTATION_COMMANDS: frozenset[str] = frozenset({
    "add", "update", "discuss", "lock", "unlock",
    "freeze", "unfreeze", "repair-drift",
    "promote", "demote", "stealth", "unstealth",
    "delete", "reconcile-reset", "fork-setup", "tui",
    "batch",
})


# ---------------------------------------------------------------------------
# Cache directory discovery
# ---------------------------------------------------------------------------


def _get_cache_dir(tracker_root: Path) -> Path | None:
    """Derive the XDG-based cache directory for tracker SQLite caches.

    If ``TRACKER_CACHE_DIR`` is set, returns that path directly — useful when
    the default cache directory is on a network filesystem where SQLite
    locking is unreliable (e.g., NFS).

    Otherwise uses $XDG_CACHE_HOME (default ~/.cache) + repo fingerprint to
    create a unique cache directory per repository. Returns None if the repo
    fingerprint cannot be computed (e.g. not in a git repo).
    """
    # TRACKER_CACHE_DIR overrides everything — use as-is
    override = os.environ.get("TRACKER_CACHE_DIR")
    if override:
        return Path(override)

    from hypergumbo_tracker.cache import Cache
    from hypergumbo_tracker.store import _find_git_dir

    git_dir = _find_git_dir(tracker_root)
    if git_dir is None:
        return None

    fingerprint = Cache.repo_fingerprint(git_dir)
    if fingerprint == "no-git":
        return None

    xdg_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return xdg_cache / "hypergumbo-tracker" / fingerprint


# ---------------------------------------------------------------------------
# Tracker root discovery
# ---------------------------------------------------------------------------


def _find_tracker_root(start: Path | None = None) -> Path:
    """Find the .agent/ directory by walking up from start (or cwd).

    Returns the first .agent/ directory found. Raises SystemExit(1) if
    no tracker root is found.
    """
    current = start or Path.cwd()
    while True:
        candidate = current / ".agent"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent

    print("error: no .agent/ directory found. Run 'init' first.", file=sys.stderr)
    raise SystemExit(EXIT_USER_ERROR)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_item_short(item: CompiledItem, idx: int | None = None) -> str:
    """Format item as a short one-line summary."""
    parts: list[str] = []
    if idx is not None:
        parts.append(f"{idx + 1:>2}")
    tier_label = f"[{item.tier.value}]" if item.tier else ""
    parts.append(f"{item.id}  {item.status:<12} P{item.priority}  {tier_label:<12} {item.title}")
    return "  ".join(parts)


def _format_item_full(item: CompiledItem) -> str:
    """Format item as a detailed multi-line display."""
    lines: list[str] = []
    lines.append(f"{item.id}  {item.title}")
    lines.append(f"  status: {item.status}  priority: P{item.priority}  "
                 f"tags: [{', '.join(item.tags)}]")
    lines.append(f"  parent: {item.parent or 'null'}  "
                 f"before: [{', '.join(item.before)}]  "
                 f"pr_ref: {item.pr_ref or 'null'}")
    if item.fields:
        for k, v in item.fields.items():
            lines.append(f"  fields.{k}: {v}")
    if item.description:
        lines.append(f"  description: {item.description}")
    if item.discussion:
        lines.append(f"  discussion: ({len(item.discussion)} entries)")
        for entry in item.discussion:
            prefix = "[summary] " if entry.is_summary else ""
            lines.append(f"    [{entry.at}] {entry.actor} ({entry.by}): {prefix}{entry.message}")
    else:
        lines.append("  discussion: (none)")
    if item.locked_fields:
        lines.append(f"  locked: [{', '.join(sorted(item.locked_fields))}]")
    if item.duplicate_of:
        lines.append(f"  duplicate_of: [{', '.join(item.duplicate_of)}]")
    if item.not_duplicate_of:
        lines.append(f"  not_duplicate_of: [{', '.join(item.not_duplicate_of)}]")
    tier_str = item.tier.value if item.tier else "unknown"
    lines.append(f"  tier: {tier_str}  created: {item.created_at}  "
                 f"updated: {item.updated_at}")
    if item.frozen:
        lines.append("  *** FROZEN ***")
    if item.cross_tier_conflict:
        lines.append("  *** CROSS-TIER CONFLICT ***")
    return "\n".join(lines)


def _item_to_dict(item: CompiledItem) -> dict[str, Any]:
    """Convert CompiledItem to a JSON-serializable dict."""
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "status": item.status,
        "priority": item.priority,
        "parent": item.parent,
        "tags": item.tags,
        "before": item.before,
        "duplicate_of": item.duplicate_of,
        "not_duplicate_of": item.not_duplicate_of,
        "pr_ref": item.pr_ref,
        "description": item.description,
        "fields": item.fields,
        "locked_fields": sorted(item.locked_fields),
        "discussion": [
            {"by": d.by, "actor": d.actor, "at": d.at,
             "message": d.message, "is_summary": d.is_summary}
            for d in item.discussion
        ],
        "frozen": item.frozen,
        "tier": item.tier.value if item.tier else None,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "cross_tier_conflict": item.cross_tier_conflict,
    }


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_show(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'show' subcommand."""
    item = ts.get(args.item_id)
    if args.json:
        print(json.dumps(_item_to_dict(item), indent=2))
    else:
        print(_format_item_full(item))
    return EXIT_SUCCESS


def _cmd_list(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'list' subcommand."""
    tier = None
    if hasattr(args, "tier") and args.tier:
        tier = Tier(args.tier)
    items = ts.list_items(
        status=getattr(args, "status", None),
        kind=getattr(args, "kind", None),
        tag=getattr(args, "tag", None),
        tier=tier,
    )
    limit = getattr(args, "limit", None)
    if limit:
        items = items[:limit]
    if args.json:
        print(json.dumps([_item_to_dict(i) for i in items], indent=2))
    else:
        if not items:
            print("(no items)")
        else:
            for idx, item in enumerate(items):
                print(_format_item_short(item, idx))
    return EXIT_SUCCESS


def _cmd_ready(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'ready' subcommand."""
    items = ts.ready()
    limit = getattr(args, "limit", None)
    if limit:
        items = items[:limit]

    # Scan all items for unread human messages (regardless of status)
    unread_items = _collect_unread_human_messages(ts)

    if args.json:
        result: dict[str, Any] = {
            "ready": [_item_to_dict(i) for i in items],
        }
        if unread_items:
            result["items_with_unread_messages"] = [
                {"id": item.id, "title": item.title, "status": item.status,
                 "priority": item.priority, "unread_count": count}
                for item, count in unread_items
            ]
        print(json.dumps(result, indent=2))
    else:
        if not items:
            print("(no ready items)")
        else:
            for idx, item in enumerate(items):
                print(_format_item_short(item, idx))
        if unread_items:
            print()
            print(f"--- {len(unread_items)} item(s) with unread human message(s) ---")
            for item, count in unread_items:
                print(f"  {item.id}  [{item.status}]  {item.title}  "
                      f"({count} unread)")
            print()
            print("  View:  scripts/tracker show <ID>")
            print("  Reply: scripts/tracker discuss <ID> \"your reply\"")
    return EXIT_SUCCESS


def _collect_unread_human_messages(
    ts: TrackerSet,
) -> list[tuple[CompiledItem, int]]:
    """Collect items with unread human messages across all tiers.

    Returns a list of (item, unread_count) tuples sorted by priority then ID.
    Scans all tiers based on tracker config scope, checking each item's
    discussion for trailing human-authored messages (indicating the agent
    hasn't replied yet).
    """
    tiers_to_check: list[Tier]
    if ts.config.scope == "workspace":
        tiers_to_check = [Tier.WORKSPACE, Tier.STEALTH]
    else:
        tiers_to_check = list(Tier)

    results: list[tuple[CompiledItem, int]] = []
    for t in tiers_to_check:
        store = ts._tier_stores[t]
        for item in store._compile_all():
            if has_unread_human_messages(item):
                msgs = unread_human_messages(item)
                results.append((item, len(msgs)))

    results.sort(key=lambda r: (r[0].priority, r[0].id))
    return results


def _cmd_log(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'log' subcommand — print raw ops."""
    full_id, store, _ = ts._resolve_id(args.item_id)
    path = store.item_path(full_id)
    if not path.exists():
        print(f"error: item not found: {args.item_id}", file=sys.stderr)
        return EXIT_USER_ERROR
    print(path.read_text(), end="")
    return EXIT_SUCCESS


def _resolve_ref(ts: TrackerSet, raw: str, field_name: str) -> str:
    """Resolve a short ID prefix to its full proquint ID.

    Used for --parent, --before, --add-duplicate-of, etc. to ensure
    that references stored in ops files are always full IDs, not
    short prefixes that would fail cross-file validation.
    """
    try:
        full_id, _store, _tier = ts._resolve_id(raw)
        return full_id
    except (ItemNotFoundError, AmbiguousPrefixError) as exc:
        print(
            f"error: cannot resolve {field_name} '{raw}': {exc}",
            file=sys.stderr,
        )
        raise


def _cmd_add(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'add' subcommand."""
    tier = Tier(args.tier) if args.tier else Tier.WORKSPACE

    kwargs: dict[str, Any] = {}
    if args.status:
        kwargs["status"] = args.status
    if args.priority is not None:
        kwargs["priority"] = args.priority
    if args.parent:
        kwargs["parent"] = _resolve_ref(ts, args.parent, "--parent")
    if args.tag:
        kwargs["tags"] = args.tag
    if args.before:
        kwargs["before"] = [_resolve_ref(ts, b, "--before") for b in args.before]
    if args.description:
        kwargs["description"] = args.description
    if args.pr_ref:
        kwargs["pr_ref"] = args.pr_ref
    # Parse --field key=value pairs
    if args.field:
        fields: dict[str, Any] = {}
        for kv in args.field:
            if "=" not in kv:
                print(f"error: --field must be key=value, got {kv!r}", file=sys.stderr)
                return EXIT_USER_ERROR
            k, v = kv.split("=", 1)
            fields[k] = v
        kwargs["fields"] = fields

    item_id = ts.add(kind=args.kind, title=args.title, tier=tier, **kwargs)
    if args.json:
        print(json.dumps({"id": item_id}))
    else:
        print(item_id)
    return EXIT_SUCCESS


def _short_id(item_id: str) -> str:
    """Return a short display form of a proquint item ID.

    Keeps prefix + first word: 'WI-mamuh-puduz-...' → 'WI-mamuh'.
    """
    parts = item_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return item_id


def _item_label(ts: TrackerSet, item_id: str) -> str:
    """Return 'SHORT_ID (title)' for an item, or just the ID if lookup fails."""
    try:
        item = ts.get(item_id)
        return f"{_short_id(item_id)} ({item.title})"
    except Exception:  # pragma: no cover
        return _short_id(item_id)


def _describe_changes(
    old: "CompiledItem", new: "CompiledItem", ts: TrackerSet,
) -> list[str]:
    """Compare old and new item state, returning human-readable change lines.

    Each line describes one mutation in natural language. For relationship
    changes, includes item titles to make directionality unambiguous.
    """
    changes: list[str] = []

    # Status change
    if old.status != new.status:
        changes.append(f"status: {old.status} → {new.status}")

    # Priority change
    if old.priority != new.priority:
        changes.append(f"priority: P{old.priority} → P{new.priority}")

    # Title change
    if old.title != new.title:
        changes.append(f"title: {old.title!r} → {new.title!r}")

    # Parent change
    if old.parent != new.parent:
        if new.parent:
            changes.append(
                f"parent: now child of {_item_label(ts, new.parent)}"
            )
        else:
            changes.append("parent: removed")

    # PR ref change
    if old.pr_ref != new.pr_ref:
        if new.pr_ref:
            changes.append(f"pr_ref: {new.pr_ref}")
        else:
            changes.append("pr_ref: removed")

    # Tags
    old_tags = set(old.tags)
    new_tags = set(new.tags)
    added_tags = new_tags - old_tags
    removed_tags = old_tags - new_tags
    if added_tags:
        changes.append(f"tags: +{', +'.join(sorted(added_tags))}")
    if removed_tags:
        changes.append(f"tags: -{', -'.join(sorted(removed_tags))}")

    # Before links (blocking relationships)
    old_before = set(old.before)
    new_before = set(new.before)
    for bid in new_before - old_before:
        label = _item_label(ts, bid)
        changes.append(
            f"before: {_short_id(new.id)} now blocks {label}"
        )
    for bid in old_before - new_before:
        label = _item_label(ts, bid)
        changes.append(
            f"before: {_short_id(new.id)} no longer blocks {label}"
        )

    # Duplicate-of links
    old_dup = set(old.duplicate_of)
    new_dup = set(new.duplicate_of)
    for did in new_dup - old_dup:
        changes.append(f"duplicate_of: +{_item_label(ts, did)}")
    for did in old_dup - new_dup:
        changes.append(f"duplicate_of: removed {_item_label(ts, did)}")

    # Not-duplicate-of links
    old_ndup = set(old.not_duplicate_of)
    new_ndup = set(new.not_duplicate_of)
    for nid in new_ndup - old_ndup:
        changes.append(f"not_duplicate_of: +{_item_label(ts, nid)}")
    for nid in old_ndup - new_ndup:
        changes.append(f"not_duplicate_of: removed {_item_label(ts, nid)}")

    # Custom fields
    if old.fields != new.fields:
        for k in set(list(old.fields) + list(new.fields)):
            ov = old.fields.get(k)
            nv = new.fields.get(k)
            if ov != nv:
                if nv is not None:
                    changes.append(f"fields.{k}: {nv}")
                else:
                    changes.append(f"fields.{k}: removed")

    return changes


def _changes_to_dict(changes: list[str]) -> list[dict[str, str]]:
    """Convert change lines to structured dicts for JSON output."""
    result = []
    for line in changes:
        if ": " in line:
            field, detail = line.split(": ", 1)
            result.append({"field": field, "detail": detail})
        else:
            result.append({"field": "unknown", "detail": line})
    return result


def _cmd_update(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'update' subcommand."""
    set_fields: dict[str, Any] = {}
    add_fields: dict[str, list[Any]] = {}
    remove_fields: dict[str, list[Any]] = {}

    if args.status:
        set_fields["status"] = args.status
    if args.priority is not None:
        set_fields["priority"] = args.priority
    if args.title:
        set_fields["title"] = args.title
    if args.parent:
        set_fields["parent"] = _resolve_ref(ts, args.parent, "--parent")
    if args.pr_ref:
        set_fields["pr_ref"] = args.pr_ref
    if args.description:
        set_fields["description"] = args.description

    if args.add_tag:
        add_fields["tags"] = args.add_tag
    if args.remove_tag:
        remove_fields["tags"] = args.remove_tag
    if args.add_before:
        add_fields["before"] = [
            _resolve_ref(ts, b, "--add-before") for b in args.add_before
        ]
    if args.remove_before:
        remove_fields["before"] = [
            _resolve_ref(ts, b, "--remove-before") for b in args.remove_before
        ]
    if args.add_duplicate_of:
        add_fields["duplicate_of"] = [
            _resolve_ref(ts, d, "--add-duplicate-of")
            for d in args.add_duplicate_of
        ]
    if args.remove_duplicate_of:
        remove_fields["duplicate_of"] = [
            _resolve_ref(ts, d, "--remove-duplicate-of")
            for d in args.remove_duplicate_of
        ]
    if args.add_not_duplicate_of:
        add_fields["not_duplicate_of"] = [
            _resolve_ref(ts, d, "--add-not-duplicate-of")
            for d in args.add_not_duplicate_of
        ]
    if args.remove_not_duplicate_of:
        remove_fields["not_duplicate_of"] = [
            _resolve_ref(ts, d, "--remove-not-duplicate-of")
            for d in args.remove_not_duplicate_of
        ]

    if args.field:
        fields_dict: dict[str, Any] = {}
        for kv in args.field:
            if "=" not in kv:
                print(f"error: --field must be key=value, got {kv!r}", file=sys.stderr)
                return EXIT_USER_ERROR
            k, v = kv.split("=", 1)
            fields_dict[k] = v
        set_fields["fields"] = fields_dict

    # Capture old state for verbose confirmation
    has_direct_mutations = bool(set_fields or add_fields or remove_fields)
    old_item = ts.get(args.item_id)

    if has_direct_mutations:
        ts.update(
            args.item_id,
            set_fields=set_fields or None,
            add_fields=add_fields or None,
            remove_fields=remove_fields or None,
        )

    # --note is shorthand for a follow-up discuss call
    if args.note:
        _warn_unread_human_messages(args.item_id, ts)
        ts.discuss(args.item_id, message=args.note)

    # Compute and display changes for the primary item
    new_item = ts.get(args.item_id)
    changes = _describe_changes(old_item, new_item, ts)

    # Handle --add-blocked-by / --remove-blocked-by (inverse before links)
    # These modify OTHER items, not the primary target.
    blocked_by_changes: list[str] = []
    target_full_id, _, _ = ts._resolve_id(args.item_id)

    if getattr(args, "add_blocked_by", None):
        for ref in args.add_blocked_by:
            blocker_id = _resolve_ref(ts, ref, "--add-blocked-by")
            ts.update(
                blocker_id,
                add_fields={"before": [target_full_id]},
            )
            blocked_by_changes.append(
                f"blocked-by: {_item_label(ts, blocker_id)} now blocks "
                f"{_short_id(target_full_id)}"
            )

    if getattr(args, "remove_blocked_by", None):
        for ref in args.remove_blocked_by:
            blocker_id = _resolve_ref(ts, ref, "--remove-blocked-by")
            ts.update(
                blocker_id,
                remove_fields={"before": [target_full_id]},
            )
            blocked_by_changes.append(
                f"blocked-by: {_item_label(ts, blocker_id)} no longer blocks "
                f"{_short_id(target_full_id)}"
            )

    all_changes = changes + blocked_by_changes

    if args.json:
        result: dict[str, Any] = {"ok": True}
        if all_changes:
            result["changes"] = _changes_to_dict(all_changes)
        print(json.dumps(result))
    else:
        if all_changes:
            print(f"updated {_short_id(args.item_id)}: {'; '.join(all_changes)}")
        else:
            print("updated")
    return EXIT_SUCCESS


def _warn_unread_human_messages(item_id: str, ts: TrackerSet) -> None:
    """Print a warning if the item has unread human discussion messages.

    Checks whether the most recent discussion entry is from a human.
    If so, prints the unread message and the full prior transcript to
    stdout so the agent can reply via ``tracker discuss``.

    Uses the single-agent heuristic from AGENTS.md: a trailing human
    message means the agent hasn't replied yet.
    """
    try:
        item = ts.get(item_id)
    except (ItemNotFoundError, AmbiguousPrefixError):
        return  # Item resolution will fail properly in the caller
    if not has_unread_human_messages(item):
        return

    unread = unread_human_messages(item)
    # Build the prior transcript (everything before the unread messages)
    prior_count = len(item.discussion) - len(unread)
    prior = item.discussion[:prior_count]

    print()
    print(
        "Your addition to the Activity/Discussion for this item "
        "comes after an unread message from the human:"
    )
    for entry in unread:
        print(f"  [{entry.at}] {entry.actor}: {entry.message}")
    if prior:
        print()
        print(
            "For context, here is the full Activity/Discussion "
            "transcript preceding that latest unread message:"
        )
        for entry in prior:
            prefix = "(summary) " if entry.is_summary else ""
            print(f"  [{entry.at}] {entry.actor} ({entry.by}): {prefix}{entry.message}")
    print()
    print("Please reply to the human's message using `tracker discuss`.")
    print()


def _cmd_discuss(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'discuss' subcommand."""
    # Warn about unread human messages before adding agent's message
    if not args.clear and args.summarize is None:
        _warn_unread_human_messages(args.item_id, ts)

    if args.clear:
        ts.discuss(args.item_id, message="", clear=True)
    elif args.summarize is not None:
        ts.discuss(args.item_id, message=args.summarize, summarize=True)
    else:
        ts.discuss(args.item_id, message=args.message)

    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("discussed")
    return EXIT_SUCCESS


def _cmd_deps(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'deps' subcommand — show dependency graph for an item.

    Shows: parent, children, items this blocks (before), and items that
    block this (items with this item in their before list).
    """
    item = ts.get(args.item_id)
    full_id = item.id

    # Find children (items whose parent == this item)
    children = ts.children(full_id)

    # Find items this blocks (this item's before list)
    blocks: list[CompiledItem] = []
    for bid in item.before:
        try:
            blocks.append(ts.get(bid))
        except ItemNotFoundError:  # pragma: no cover — dangling reference
            pass

    # Find items that block this (other items with this item in their before)
    blocked_by: list[CompiledItem] = []
    all_items = ts.list_items()
    for other in all_items:
        if full_id in other.before and other.id != full_id:
            blocked_by.append(other)

    if args.json:
        result: dict[str, Any] = {
            "id": full_id,
            "title": item.title,
            "status": item.status,
        }
        if item.parent:
            try:
                parent = ts.get(item.parent)
                result["parent"] = {"id": parent.id, "title": parent.title}
            except ItemNotFoundError:  # pragma: no cover — dangling parent ref
                result["parent"] = {"id": item.parent, "title": "?"}
        if children:
            result["children"] = [
                {"id": c.id, "title": c.title, "status": c.status}
                for c in children
            ]
        if blocks:
            result["blocks"] = [
                {"id": b.id, "title": b.title, "status": b.status}
                for b in blocks
            ]
        if blocked_by:
            result["blocked_by"] = [
                {"id": b.id, "title": b.title, "status": b.status}
                for b in blocked_by
            ]
        print(json.dumps(result))
    else:
        # Text output: tree-like display
        print(f"{_short_id(full_id)}  {item.title}  [{item.status}]")

        if item.parent:
            try:
                parent = ts.get(item.parent)
                print(f"  parent: {_short_id(parent.id)} ({parent.title})")
            except ItemNotFoundError:  # pragma: no cover — dangling parent ref
                print(f"  parent: {_short_id(item.parent)} (?)")

        if children:
            print(f"  children ({len(children)}):")
            for c in children:
                print(f"    {_short_id(c.id)}  {c.title}  [{c.status}]")

        if blocks:
            print(f"  blocks ({len(blocks)}):")
            for b in blocks:
                print(f"    → {_short_id(b.id)}  {b.title}  [{b.status}]")

        if blocked_by:
            print(f"  blocked by ({len(blocked_by)}):")
            for b in blocked_by:
                print(f"    ← {_short_id(b.id)}  {b.title}  [{b.status}]")

    return EXIT_SUCCESS


def _cmd_lock(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'lock' subcommand."""
    ts.lock(args.item_id, args.fields)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("locked")
    return EXIT_SUCCESS


def _cmd_unlock(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'unlock' subcommand."""
    ts.unlock(args.item_id, args.fields)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("unlocked")
    return EXIT_SUCCESS


def _cmd_freeze(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'freeze' subcommand."""
    ts.freeze(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("frozen")
    return EXIT_SUCCESS


def _cmd_unfreeze(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'unfreeze' subcommand."""
    ts.unfreeze(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("unfrozen")
    return EXIT_SUCCESS


def _cmd_repair_drift(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'repair-drift' subcommand."""
    ts.repair_drift(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("drift repaired")
    return EXIT_SUCCESS


def _cmd_promote(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'promote' subcommand."""
    ts.promote(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("promoted")
    return EXIT_SUCCESS


def _cmd_demote(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'demote' subcommand."""
    ts.demote(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("demoted")
    return EXIT_SUCCESS


def _cmd_stealth(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'stealth' subcommand."""
    ts.stealth_item(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("stealthed")
    return EXIT_SUCCESS


def _cmd_unstealth(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'unstealth' subcommand."""
    ts.unstealth_item(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("unstealthed")
    return EXIT_SUCCESS


def _cmd_delete(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'delete' subcommand — set item status to deleted (human only)."""
    ts.update(args.item_id, set_fields={"status": "deleted"})
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("deleted")
    return EXIT_SUCCESS


def _cmd_validate(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'validate' subcommand."""
    from hypergumbo_tracker.validation import validate_all, validate_ops_file

    tracker_root = ts._tracker_root

    if args.files:
        from hypergumbo_tracker.validation import ValidationResult
        result = ValidationResult()
        for f in args.files:
            path = Path(f)
            if not path.exists():
                result.errors.append(f"{f}: file not found")
                continue
            result.merge(validate_ops_file(path, ts.config, check_locks=args.check_locks))
    else:
        result = validate_all(
            tracker_root,
            config=ts.config,
            check_similar=args.similar,
            check_deep_similar=args.deep_similar,
            check_locks=args.check_locks,
            strict=args.strict,
        )

    if args.json:
        print(json.dumps({"errors": result.errors, "warnings": result.warnings}))
    else:
        for e in result.errors:
            print(f"ERROR: {e}")
        for w in result.warnings:
            print(f"WARNING: {w}")
        if result.ok:
            print("validation passed")

    return EXIT_SUCCESS if result.ok else EXIT_USER_ERROR


def _cmd_count_todos(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'count-todos' subcommand."""
    from hypergumbo_tracker.stop_hook import count_todos

    try:
        count = count_todos(
            ts._tracker_root,
            hard=args.hard,
            soft=args.soft,
            config=ts.config,
        )
        if args.json:
            print(json.dumps({"count": count}))
        else:
            print(count)
        return EXIT_SUCCESS
    except Exception as e:
        print(f"error: count-todos failed: {e}", file=sys.stderr)
        return EXIT_USER_ERROR


def _cmd_hash_todos(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'hash-todos' subcommand."""
    from hypergumbo_tracker.stop_hook import hash_todos

    try:
        h = hash_todos(ts._tracker_root, config=ts.config)
        if args.json:
            print(json.dumps({"hash": h}))
        else:
            print(h)
        return EXIT_SUCCESS
    except Exception as e:
        print(f"error: hash-todos failed: {e}", file=sys.stderr)
        return EXIT_USER_ERROR


def _cmd_guidance(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'guidance' subcommand — generate stop hook guidance file."""
    from hypergumbo_tracker.stop_hook import generate_guidance

    guidance_dir = Path(args.guidance_dir) if args.guidance_dir else None

    try:
        path = generate_guidance(
            ts._tracker_root,
            guidance_dir=guidance_dir,
            config=ts.config,
        )
        if args.json:
            print(json.dumps({"path": path}))
        else:
            print(path)
        return EXIT_SUCCESS
    except Exception as e:
        print(f"error: guidance failed: {e}", file=sys.stderr)
        return EXIT_USER_ERROR


def _cmd_check_messages(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'check-messages' subcommand — show items with unread human messages."""
    tiers_to_check: list[Tier]
    if ts.config.scope == "workspace":
        tiers_to_check = [Tier.WORKSPACE, Tier.STEALTH]
    else:
        tiers_to_check = list(Tier)

    results: list[tuple[CompiledItem, list[DiscussionEntry]]] = []
    for t in tiers_to_check:
        store = ts._tier_stores[t]
        for item in store._compile_all():
            if has_unread_human_messages(item):
                msgs = unread_human_messages(item)
                if args.autolimit is not None and args.autolimit > 0:
                    msgs = msgs[-args.autolimit:]
                results.append((item, msgs))

    # Sort by (priority, id)
    results.sort(key=lambda r: (r[0].priority, r[0].id))

    if args.json:
        out = []
        for item, msgs in results:
            out.append({
                "id": item.id,
                "title": item.title,
                "status": item.status,
                "priority": item.priority,
                "tier": item.tier.value if item.tier else None,
                "unread_messages": [
                    {"by": m.by, "actor": m.actor, "at": m.at,
                     "message": m.message, "is_summary": m.is_summary}
                    for m in msgs
                ],
            })
        print(json.dumps(out, indent=2))
    else:
        if not results:
            print("(no unread human messages)")
        else:
            for i, (item, msgs) in enumerate(results):
                if i > 0:
                    print()
                tier_str = item.tier.value if item.tier else "unknown"
                print(f"{item.id}  P{item.priority}  {item.status}  "
                      f"[{tier_str}]  {item.title}")
                for m in msgs:
                    print(f"  [{m.at}] {m.actor} ({m.by}): {m.message}")

    return EXIT_SUCCESS


def _cmd_init(args: argparse.Namespace) -> int:
    """Handle 'init' subcommand — create tracker directory structure."""
    root = Path(args.tracker_root) if args.tracker_root else Path.cwd() / ".agent"

    dirs = [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Create .gitattributes for merge=union
    gitattributes = root / "tracker" / ".ops" / ".gitattributes"
    if not gitattributes.exists():
        gitattributes.write_text("*.ops merge=union\n")

    ws_gitattributes = root / "tracker-workspace" / ".ops" / ".gitattributes"
    if not ws_gitattributes.exists():
        ws_gitattributes.write_text("*.ops merge=union\n")

    # Create .gitignore for stealth tier
    stealth_gitignore = root / "tracker-workspace" / "stealth" / ".gitignore"
    if not stealth_gitignore.exists():
        stealth_gitignore.write_text("*.ops\n")

    # Create .gitignore for tracker dir (config.yaml is human-owned, gitignored)
    tracker_gitignore = root / "tracker" / ".gitignore"
    if not tracker_gitignore.exists():
        tracker_gitignore.write_text("config.yaml\n")

    # Copy config.yaml.template → config.yaml if template exists and config doesn't
    template_path = root / "tracker" / "config.yaml.template"
    config_path = root / "tracker" / "config.yaml"
    if template_path.exists() and not config_path.exists():
        import shutil
        shutil.copy2(template_path, config_path)

    # Enforce read-only on config.yaml (governance boundary)
    from hypergumbo_tracker.setup import config_lock
    config_lock(config_path)

    if args.json:
        print(json.dumps({"root": str(root)}))
    else:
        print(f"initialized tracker at {root}")
    return EXIT_SUCCESS


def _cmd_setup(args: argparse.Namespace) -> int:
    """Handle 'setup' subcommand — idempotent setup wizard."""
    from hypergumbo_tracker.setup import (
        format_results,
        generate_human_shim,
        results_to_json,
        run_setup,
    )

    # Dispatch to configure subcommand
    if getattr(args, "setup_action", None) == "configure":
        from hypergumbo_tracker.configure import run_configure

        if args.setup_root:
            root = Path(args.setup_root)
        else:
            try:
                root = _find_tracker_root()
            except SystemExit:
                root = Path.cwd() / ".agent"

        return run_configure(root)

    if args.setup_root:
        root = Path(args.setup_root)
    else:
        # Try to find existing .agent/ or default to cwd/.agent
        try:
            root = _find_tracker_root()
        except SystemExit:
            root = Path.cwd() / ".agent"

    # Warn if running as agent — config ownership and human-only fixes
    # work best when the human user runs setup.
    by, username = resolve_actor()
    if by == "agent" and sys.stdin.isatty() and not args.json:
        print(
            f"Warning: running as '{username}' (detected as agent).\n"
            "Setup works best when run as the human user, because it can\n"
            "auto-fix config ownership and file permissions.\n"
        )
        try:
            answer = input("Continue as agent? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print(generate_human_shim(root))
            return EXIT_SUCCESS

    results = run_setup(root)

    if args.json:
        print(json.dumps(results_to_json(results), indent=2))
    else:
        text, _ = format_results(results)
        print(text)

    # If there are errors, prompt before continuing (interactive only)
    errors = [r for r in results if r.status == "error"]
    if errors and sys.stdin.isatty() and not args.json:
        print(f"\n{len(errors)} error(s) found. These should be fixed before use.")
        try:
            answer = input("Continue anyway? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            return EXIT_USER_ERROR

    has_errors = any(r.status == "error" for r in results)
    return EXIT_USER_ERROR if has_errors else EXIT_SUCCESS


def _cmd_cache_rebuild(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'cache-rebuild' subcommand."""
    # Cache is optional — rebuild if available
    from hypergumbo_tracker.cache import Cache

    for tier_name, tier_val in [
        ("canonical", Tier.CANONICAL),
        ("workspace", Tier.WORKSPACE),
        ("stealth", Tier.STEALTH),
    ]:
        store = ts._tier_stores[tier_val]
        db_path = ts._tracker_root / f".cache-{tier_name}.db"
        cache = Cache(store, db_path, tier_val)
        cache.rebuild()
        cache.close()

    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("cache rebuilt")
    return EXIT_SUCCESS


def _cmd_reconcile_reset(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'reconcile-reset' subcommand."""
    ts.reconcile_reset(args.item_id)
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("reconciled")
    return EXIT_SUCCESS


def _cmd_fork_setup(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'fork-setup' subcommand — set scope to workspace."""
    by, _ = resolve_actor(ts.config.agent_usernames)
    if by == "agent":
        print("error: fork-setup requires human authority", file=sys.stderr)
        return EXIT_USER_ERROR

    # Write config.yaml with scope: workspace
    config_path = ts._tracker_root / "tracker" / "config.yaml"
    import yaml
    if config_path.exists():
        with open(config_path) as f:
            config_data = yaml.safe_load(f) or {}
    else:
        config_data = {}

    if "stop_hook" not in config_data:
        config_data["stop_hook"] = {}
    config_data["stop_hook"]["scope"] = "workspace"

    from hypergumbo_tracker.setup import config_lock, config_unlock
    config_unlock(config_path)
    with open(config_path, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False)
    config_lock(config_path)

    if args.json:
        print(json.dumps({"ok": True, "scope": "workspace"}))
    else:
        print("scope set to 'workspace'")
    return EXIT_SUCCESS


def _cmd_sync(args: argparse.Namespace) -> int:
    """Handle 'sync' subcommand — push tracker changes via streamlined PR."""
    from hypergumbo_tracker.sync import do_sync, preflight_check

    repo_root = Path(
        subprocess.run(  # nosec B603, B607
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    pre = preflight_check(repo_root)
    if not pre.ok:
        print(f"error: {pre.error}", file=sys.stderr)
        return EXIT_USER_ERROR
    if not pre.changed_files:
        print("nothing to sync")
        return EXIT_SUCCESS
    if args.dry_run:
        print(f"would sync {len(pre.changed_files)} file(s):")
        for f in pre.changed_files:
            print(f"  {f}")
        return EXIT_SUCCESS
    result = do_sync(
        repo_root=repo_root,
        preflight=pre,
        base_branch=args.base_branch,
        ci_timeout=args.timeout,
    )
    if result.success:
        print(f"synced {result.files_synced} file(s) via PR #{result.pr_number}")
    else:
        print(f"error: {result.error}", file=sys.stderr)
    return result.exit_code


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Handle 'migrate' subcommand — convert markdown governance files to YAML ops."""
    from hypergumbo_tracker.migration import migrate

    tracker_root = Path(args.tracker_root) if args.tracker_root else Path.cwd() / ".agent"
    ledger_path = Path(args.ledger) if args.ledger else tracker_root / "invariant-ledger.md"
    work_items_path = Path(args.work_items) if args.work_items else (
        Path.home() / "hypergumbo_lab_notebook" / "guidance_log" / "work_items.md"
    )

    result = migrate(
        ledger_path,
        work_items_path,
        tracker_root,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps({
            "items_created": result.items_created,
            "items_skipped": result.items_skipped,
            "items_by_kind": result.items_by_kind,
            "errors": result.errors,
            "id_map": result.id_map,
            "dry_run": args.dry_run,
        }, indent=2))
    else:
        action = "would create" if args.dry_run else "created"
        print(f"{action} {result.items_created} items, skipped {result.items_skipped}")
        for kind, count in sorted(result.items_by_kind.items()):
            print(f"  {kind}: {count}")
        if result.errors:
            for err in result.errors:
                print(f"  ERROR: {err}", file=sys.stderr)

    return EXIT_SUCCESS if not result.errors else EXIT_USER_ERROR


def _detect_screen_altscreen_off() -> bool:
    """Return True if running inside GNU Screen with altscreen off.

    Detection strategy:
    1. Check STY env var — present only inside GNU Screen.
    2. Query Screen via ``screen -Q altscreen`` to check the setting.
    3. If the query fails (old Screen, no -Q support), assume altscreen is off
       because that is the GNU Screen default.
    """
    if not os.environ.get("STY"):
        return False
    try:
        result = subprocess.run(  # nosec B603, B607
            ["screen", "-Q", "altscreen"],  # noqa: S607
            capture_output=True, text=True, timeout=5,
        )
        # Output varies by version: "altscreen off", "off", etc.
        return "on" not in result.stdout.lower()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Can't query → assume off (the default)
        return True


_SCREEN_HINT = """\
Note: GNU Screen's 'altscreen' is off — TUI apps can't preserve your
scrollback. To fix permanently, add to ~/.screenrc:

    altscreen on

Then restart Screen (or run: screen -X altscreen on)."""


def _print_screen_warning() -> None:
    """Print the altscreen-off hint to stderr."""
    print(_SCREEN_HINT, file=sys.stderr)


def _tui_startup_summary(ts: TrackerSet) -> str:
    """Pre-load diagnostic: count ops files per tier and time the load.

    Returns a compact one-line summary like:
      "298 items (3 tiers, 12 compiled) in 0.43s"
    """
    # Count ops files per tier (cheap — directory listing only).
    file_counts: dict[str, int] = {}
    for t, store in ts._tier_stores.items():
        file_counts[t.value] = len(store._list_item_files())

    tier_str = ", ".join(
        f"{v} {k}" for k, v in file_counts.items() if v > 0
    )

    # Time the actual load.
    t0 = time.monotonic()
    items = ts.list_items()
    elapsed = time.monotonic() - t0

    return (
        f"{len(items)} items ({tier_str}) "
        f"loaded in {elapsed:.2f}s"
    )


def _cmd_tui(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'tui' subcommand — launch Textual TUI."""
    from hypergumbo_tracker.tui import TrackerApp

    altscreen_off = _detect_screen_altscreen_off()
    if altscreen_off:
        _print_screen_warning()
        time.sleep(3)

    # Print startup diagnostics (visible during the pause before TUI).
    startup_msg = _tui_startup_summary(ts)
    sys.stderr.write(f"htrac: {startup_msg}\n")
    sys.stderr.flush()

    app = TrackerApp(tracker_set=ts)
    app.run()

    # Reprint after TUI exits so the user can see it.
    if altscreen_off:
        # Clear the screen to remove TUI remnants, then re-show the hint.
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        _print_screen_warning()
    sys.stderr.write(f"htrac: {startup_msg}\n")
    sys.stderr.flush()

    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Argparse setup
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the full CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="hypergumbo-tracker",
        description="YAML-backed structured tracker for agent governance",
    )
    parser.add_argument(
        "--tracker-root", type=str, default=None,
        help="Path to .agent/ directory (default: auto-discover)",
    )
    parser.add_argument(
        "--json", action="store_true", default=False,
        help="Machine-readable JSON output",
    )
    parser.add_argument(
        "--no-auto-sync", dest="no_auto_sync", action="store_true",
        default=False,
        help="Disable auto-sync check after mutation commands",
    )

    sub = parser.add_subparsers(dest="command")

    # --- show ---
    p_show = sub.add_parser("show", help="Show detailed item info")
    p_show.add_argument("item_id", help="Item ID or prefix")

    # --- list ---
    p_list = sub.add_parser("list", help="List items with filters")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--kind", help="Filter by kind")
    p_list.add_argument("--tag", help="Filter by tag")
    p_list.add_argument("--tier", choices=["canonical", "workspace", "stealth"],
                        help="Filter by tier")
    p_list.add_argument("--limit", type=int, help="Max items to show")

    # --- ready ---
    p_ready = sub.add_parser("ready", help="Show actionable unblocked items")
    p_ready.add_argument("--limit", type=int, help="Max items to show")

    # --- log ---
    p_log = sub.add_parser("log", help="Show raw op log for an item")
    p_log.add_argument("item_id", help="Item ID or prefix")

    # --- add ---
    p_add = sub.add_parser("add", help="Add a new item")
    p_add.add_argument("--kind", required=True, help="Item kind")
    p_add.add_argument("--title", required=True, help="Item title")
    p_add.add_argument("--status", help="Initial status")
    p_add.add_argument("--priority", type=int, help="Priority 0-4")
    p_add.add_argument("--parent", help="Parent item ID")
    p_add.add_argument("--tag", action="append", help="Tag (repeatable)")
    p_add.add_argument("--before", action="append", help="Before ID (repeatable)")
    p_add.add_argument("--description", help="Description text")
    p_add.add_argument("--pr-ref", dest="pr_ref", help="PR reference")
    p_add.add_argument("--field", action="append", help="Field key=value (repeatable)")
    p_add.add_argument("--tier", choices=["canonical", "workspace", "stealth"],
                        default=None, help="Target tier (default: workspace)")

    # --- update ---
    p_update = sub.add_parser("update", help="Update an item")
    p_update.add_argument("item_id", help="Item ID or prefix")
    p_update.add_argument("--status", help="New status")
    p_update.add_argument("--priority", type=int, help="New priority 0-4")
    p_update.add_argument("--title", help="New title")
    p_update.add_argument("--parent", help="New parent")
    p_update.add_argument("--pr-ref", dest="pr_ref", help="New PR reference")
    p_update.add_argument("--description", help="New description")
    p_update.add_argument("--add-tag", action="append", help="Add tag")
    p_update.add_argument("--remove-tag", action="append", help="Remove tag")
    p_update.add_argument("--add-before", action="append", help="Add before link")
    p_update.add_argument("--remove-before", action="append", help="Remove before link")
    p_update.add_argument("--add-duplicate-of", action="append",
                          help="Add duplicate-of link")
    p_update.add_argument("--remove-duplicate-of", action="append",
                          help="Remove duplicate-of link")
    p_update.add_argument("--add-not-duplicate-of", action="append",
                          help="Add not-duplicate-of link")
    p_update.add_argument("--remove-not-duplicate-of", action="append",
                          help="Remove not-duplicate-of link")
    p_update.add_argument("--add-blocked-by", action="append",
                          help="Inverse of --add-before: sets before link on the OTHER item "
                               "pointing to this item (repeatable)")
    p_update.add_argument("--remove-blocked-by", action="append",
                          help="Inverse of --remove-before: removes before link from the "
                               "OTHER item pointing to this item (repeatable)")
    p_update.add_argument("--field", action="append", help="Field key=value (repeatable)")
    p_update.add_argument("--note", help="Add a discussion note (shorthand for discuss)")

    # --- discuss ---
    p_discuss = sub.add_parser("discuss", help="Add discussion to an item")
    p_discuss.add_argument("item_id", help="Item ID or prefix")
    p_discuss.add_argument("message", nargs="?", default="", help="Discussion message")
    p_discuss.add_argument("--clear", action="store_true", help="Clear discussion (human only)")
    p_discuss.add_argument("--summarize", type=str, default=None, metavar="TEXT",
                           help="Replace discussion with summary text")

    # --- lock ---
    p_lock = sub.add_parser("lock", help="Lock fields on an item (human only)")
    p_lock.add_argument("item_id", help="Item ID or prefix")
    p_lock.add_argument("fields", nargs="+", help="Field names to lock")

    # --- unlock ---
    p_unlock = sub.add_parser("unlock", help="Unlock fields (human only)")
    p_unlock.add_argument("item_id", help="Item ID or prefix")
    p_unlock.add_argument("fields", nargs="+", help="Field names to unlock")

    # --- freeze ---
    p_freeze = sub.add_parser("freeze", help="Freeze an item (human only)")
    p_freeze.add_argument("item_id", help="Item ID or prefix")

    # --- unfreeze ---
    p_unfreeze = sub.add_parser("unfreeze", help="Unfreeze an item (human only)")
    p_unfreeze.add_argument("item_id", help="Item ID or prefix")

    # --- repair-drift ---
    p_repair_drift = sub.add_parser(
        "repair-drift", help="Repair drift: restore .ops from .frozen (human only)",
    )
    p_repair_drift.add_argument("item_id", help="Item ID or prefix")

    # --- promote ---
    p_promote = sub.add_parser("promote", help="Promote: workspace → canonical")
    p_promote.add_argument("item_id", help="Item ID or prefix")

    # --- demote ---
    p_demote = sub.add_parser("demote", help="Demote: canonical → workspace")
    p_demote.add_argument("item_id", help="Item ID or prefix")

    # --- stealth ---
    p_stealth = sub.add_parser("stealth", help="Stealth: workspace → stealth (human only)")
    p_stealth.add_argument("item_id", help="Item ID or prefix")

    # --- unstealth ---
    p_unstealth = sub.add_parser("unstealth", help="Unstealth: stealth → workspace (human only)")
    p_unstealth.add_argument("item_id", help="Item ID or prefix")

    # --- delete ---
    p_delete = sub.add_parser("delete", help="Delete an item (human only)")
    p_delete.add_argument("item_id", help="Item ID or prefix")

    # --- validate ---
    p_validate = sub.add_parser("validate", help="Validate tracker data")
    p_validate.add_argument("files", nargs="*", help="Specific files to validate")
    p_validate.add_argument("--similar", action="store_true",
                            help="Check SimHash near-duplicates")
    p_validate.add_argument("--deep-similar", action="store_true",
                            dest="deep_similar",
                            help="Check embedding-based near-duplicates (requires dedup extras)")
    p_validate.add_argument("--strict", action="store_true",
                            help="Treat warnings as errors")
    p_validate.add_argument("--check-locks", action="store_true",
                            help="Check for lock violations")

    # --- count-todos ---
    p_count = sub.add_parser("count-todos", help="Count blocking items")
    p_count.add_argument("--hard", action="store_true", help="Hard-blocking only")
    p_count.add_argument("--soft", action="store_true", help="Soft-blocking only")

    # --- hash-todos ---
    sub.add_parser("hash-todos", help="SHA256 fingerprint of blocking items")

    # --- guidance ---
    p_guidance = sub.add_parser("guidance", help="Generate stop hook guidance file")
    p_guidance.add_argument("--guidance-dir", dest="guidance_dir",
                            help="Directory for guidance output (default: ~/hypergumbo_lab_notebook/guidance_log/)")

    # --- check-messages ---
    p_checkmsg = sub.add_parser("check-messages",
                                help="Show items with unread human discussion messages")
    p_checkmsg.add_argument("--autolimit", type=int, default=None,
                            help="Limit to last N unread messages per item")

    # --- init ---
    sub.add_parser("init", help="Initialize tracker directory structure")

    # --- setup ---
    p_setup = sub.add_parser("setup", help="Idempotent setup wizard (diagnose + fix)")
    p_setup.add_argument("setup_action", nargs="?", default=None,
                         choices=["configure"],
                         help="'configure' for interactive config editor")
    p_setup.add_argument("--root", dest="setup_root",
                         help="Path to .agent/ directory (default: auto-detect or cwd/.agent)")

    # --- cache-rebuild ---
    sub.add_parser("cache-rebuild", help="Rebuild SQLite read cache")

    # --- reconcile-reset ---
    p_recon = sub.add_parser("reconcile-reset",
                             help="Reset cross-tier conflict (human only)")
    p_recon.add_argument("item_id", help="Item ID")

    # --- fork-setup ---
    sub.add_parser("fork-setup", help="Set scope to workspace (human only)")

    # --- migrate ---
    p_migrate = sub.add_parser("migrate", help="Migrate markdown governance files to YAML ops")
    p_migrate.add_argument("--ledger", help="Path to invariant-ledger.md (default: .agent/)")
    p_migrate.add_argument("--work-items", dest="work_items",
                           help="Path to work_items.md (default: ~/hypergumbo_lab_notebook/)")
    p_migrate.add_argument("--dry-run", dest="dry_run", action="store_true",
                           help="Report what would be done without writing files")

    # --- sync ---
    p_sync = sub.add_parser(
        "sync", help="Push tracker changes via streamlined PR workflow"
    )
    p_sync.add_argument(
        "--base-branch", dest="base_branch", default="dev",
        help="Target branch (default: dev)",
    )
    p_sync.add_argument(
        "--timeout", type=int, default=300,
        help="CI timeout in seconds (default: 300)",
    )
    p_sync.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Show what would be synced without pushing",
    )

    # --- tui ---
    sub.add_parser("tui", help="Launch interactive TUI (requires textual)")

    # --- deps ---
    p_deps = sub.add_parser("deps", help="Show dependency graph for an item")
    p_deps.add_argument("item_id", help="Item ID or prefix")

    # --- batch ---
    p_batch = sub.add_parser(
        "batch",
        help="Run multiple operations from a .htrac file with deferred auto-sync",
    )
    p_batch.add_argument(
        "file", help="Path to .htrac batch file, or '-' for stdin",
    )

    return parser


# ---------------------------------------------------------------------------
# Batch command
# ---------------------------------------------------------------------------


# Commands allowed inside a batch file. Read-only commands (show, list, etc.)
# are excluded since they don't produce mutations.
_BATCH_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "add", "update", "discuss", "lock", "unlock",
    "promote", "demote",
})


def _parse_batch_line(line: str) -> list[str] | None:
    """Parse one line from a .htrac batch file into argv tokens.

    Returns None for comments and blank lines. Uses shlex to handle
    quoted arguments (e.g., discuss WI-test "Fixed in PR #100").
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    import shlex

    return shlex.split(stripped)


def _cmd_batch(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'batch' subcommand.

    Reads a .htrac file (or stdin) with one tracker command per line.
    Executes each command sequentially. Auto-sync is NOT triggered between
    operations — the caller (main()) fires it once after batch completes.

    Each operation is individually atomic: if op #3 fails, ops #1 and #2
    still stand. This is NOT transactional — there is no rollback.
    """
    # Read batch input
    if args.file == "-":
        lines = sys.stdin.readlines()
    else:
        batch_path = Path(args.file)
        if not batch_path.exists():
            print(f"error: batch file not found: {args.file}", file=sys.stderr)
            return EXIT_USER_ERROR
        lines = batch_path.read_text().splitlines(keepends=True)

    # Parse into operations
    operations: list[tuple[int, list[str]]] = []
    for line_num, line in enumerate(lines, 1):
        tokens = _parse_batch_line(line)
        if tokens is not None:
            operations.append((line_num, tokens))

    if not operations:
        if args.json:
            print(json.dumps({"ok": True, "results": []}))
        else:
            print("batch: 0 operations (nothing to do)")
        return EXIT_SUCCESS

    # Build a sub-parser for dispatching individual operations
    # We reuse the existing parser's subcommand set
    sub_parser = _build_parser()

    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0

    for line_num, tokens in operations:
        cmd_name = tokens[0] if tokens else "?"
        if cmd_name not in _BATCH_ALLOWED_COMMANDS:
            msg = f"line {line_num}: '{cmd_name}' not allowed in batch"
            print(f"  FAIL {msg}", file=sys.stderr)
            results.append({
                "line": line_num, "command": cmd_name,
                "ok": False, "error": msg,
            })
            failed += 1
            continue

        # Parse the tokens using the full CLI parser, injecting global flags
        full_argv = [
            "--tracker-root", str(args.tracker_root_resolved),
            "--no-auto-sync",
        ]
        if args.json:
            full_argv.append("--json")
        full_argv.extend(tokens)

        try:
            sub_args = sub_parser.parse_args(full_argv)
        except SystemExit:
            msg = f"line {line_num}: invalid arguments for '{cmd_name}'"
            print(f"  FAIL {msg}", file=sys.stderr)
            results.append({
                "line": line_num, "command": cmd_name,
                "ok": False, "error": msg,
            })
            failed += 1
            continue

        # Dispatch — map command to handler
        handler_map: dict[str, Any] = {
            "add": _cmd_add,
            "update": _cmd_update,
            "discuss": _cmd_discuss,
            "lock": _cmd_lock,
            "unlock": _cmd_unlock,
            "promote": _cmd_promote,
            "demote": _cmd_demote,
        }
        handler = handler_map.get(sub_args.command)
        if handler is None:  # pragma: no cover
            msg = f"line {line_num}: no handler for '{cmd_name}'"
            results.append({
                "line": line_num, "command": cmd_name,
                "ok": False, "error": msg,
            })
            failed += 1
            continue

        # Capture stdout for JSON mode (each sub-command prints its own output)
        import io

        old_stdout = sys.stdout
        buf = io.StringIO()
        if args.json:
            sys.stdout = buf

        try:
            exit_code = handler(sub_args, ts)
            if exit_code == EXIT_SUCCESS:
                succeeded += 1
                result_entry: dict[str, Any] = {
                    "line": line_num, "command": cmd_name, "ok": True,
                }
                if args.json:
                    sub_out = buf.getvalue().strip()
                    if sub_out:
                        try:
                            result_entry["output"] = json.loads(sub_out)
                        except json.JSONDecodeError:  # pragma: no cover
                            result_entry["output"] = sub_out
                results.append(result_entry)
            else:
                failed += 1
                results.append({
                    "line": line_num, "command": cmd_name,
                    "ok": False, "error": f"exit code {exit_code}",
                })
        except (
            ItemNotFoundError, AmbiguousPrefixError,
            HumanAuthorityError, LockedFieldError, FrozenItemError,
        ) as e:
            failed += 1
            msg = f"line {line_num}: {e}"
            print(f"  FAIL {msg}", file=sys.stderr)
            results.append({
                "line": line_num, "command": cmd_name,
                "ok": False, "error": str(e),
            })
        except Exception as e:  # pragma: no cover — catch-all for unexpected errors
            failed += 1
            msg = f"line {line_num}: {e}"
            print(f"  FAIL {msg}", file=sys.stderr)
            results.append({
                "line": line_num, "command": cmd_name,
                "ok": False, "error": str(e),
            })
        finally:
            sys.stdout = old_stdout

    total = succeeded + failed
    if args.json:
        print(json.dumps({
            "ok": failed == 0,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }))
    else:
        status = "ok" if failed == 0 else "FAIL"
        print(f"batch: {succeeded}/{total} succeeded ({status})")
        if failed > 0:
            for r in results:
                if not r["ok"]:
                    print(
                        f"  line {r['line']}: {r['command']} — {r.get('error', '?')}",
                        file=sys.stderr,
                    )

    return EXIT_SUCCESS if failed == 0 else EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Auto-sync
# ---------------------------------------------------------------------------


_SYNC_COOLDOWN_SECONDS = 1800  # 30 minutes between retry attempts
_SYNC_MAX_CONSECUTIVE_FAILURES = 3  # disable autonomous mode after this many
_SYNC_PR_WAIT_TIMEOUT = 900  # 15 min max wait for in-flight PR
_SYNC_PR_WAIT_INTERVAL = 10  # seconds between PR_PENDING checks


def _escalate_disable_autonomous(repo_root: Path, fail_count: int) -> None:
    """Disable autonomous mode after too many consecutive sync failures.

    Writes ``OFF`` to ``AUTONOMOUS_MODE.txt`` and prints a prominent
    warning.  This prevents runaway PR creation when the sync mechanism
    is persistently broken.
    """
    mode_file = repo_root / "AUTONOMOUS_MODE.txt"
    try:
        current = mode_file.read_text().strip() if mode_file.exists() else "OFF"
    except OSError:  # pragma: no cover
        current = "UNKNOWN"
    if current == "OFF":
        return
    try:
        mode_file.write_text("OFF\n")
    except OSError:  # pragma: no cover
        print(
            "auto-sync: CRITICAL: could not write AUTONOMOUS_MODE.txt",
            file=sys.stderr,
        )
        return
    print(
        f"auto-sync: ESCALATION — {fail_count} consecutive sync failures. "
        f"Autonomous mode disabled (was {current}).",
        file=sys.stderr,
    )


def _maybe_auto_sync(tracker_root: Path) -> None:
    """Check if pending tracker ops exceed the threshold and trigger sync.

    Reads the threshold from ``TRACKER_AUTO_SYNC_THRESHOLD`` env var
    (default: 50, 0 = disabled).  Runs ``preflight_check()`` and
    ``do_sync()`` if the threshold is exceeded.  All output goes to stderr
    to preserve ``--json`` stdout.  Exceptions are caught and logged — this
    function never raises.

    PR Wait Gate
    ~~~~~~~~~~~~
    Before syncing, checks for ``.git/PR_PENDING`` (created by ``auto-pr``
    while polling CI).  If present, waits up to 15 minutes for the
    in-flight PR to finish.  This prevents sync from advancing ``dev``
    while a feature PR is mid-CI-poll, which would cause the feature PR to
    fail with a 405 "head behind base branch" on merge.

    Circuit Breaker
    ~~~~~~~~~~~~~~~
    When sync fails, a marker file (``.git/TRACKER_SYNC_FAILED``) is
    written with the failure count and timestamp (``count:timestamp``).
    Subsequent calls skip sync for 30 minutes to prevent runaway PR
    creation.  After ``_SYNC_MAX_CONSECUTIVE_FAILURES`` consecutive
    failures, autonomous mode is disabled by writing ``OFF`` to
    ``AUTONOMOUS_MODE.txt``.  On success, the marker is removed.
    """
    from hypergumbo_tracker.sync import (
        AUTO_SYNC_DEFAULT_THRESHOLD,
        do_sync,
        pending_sync_lines,
        preflight_check,
    )

    try:
        threshold_str = os.environ.get(
            "TRACKER_AUTO_SYNC_THRESHOLD",
            str(AUTO_SYNC_DEFAULT_THRESHOLD),
        )
        try:
            threshold = int(threshold_str)
        except ValueError:
            threshold = AUTO_SYNC_DEFAULT_THRESHOLD

        if threshold <= 0:
            return

        # Find repo root
        result = subprocess.run(  # nosec B603, B607
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return
        repo_root = Path(result.stdout.strip())

        lines = pending_sync_lines(repo_root)
        if lines < threshold:
            return

        # Wait for in-flight auto-pr to finish before syncing.
        # auto-pr creates .git/PR_PENDING while polling CI; if we sync
        # and merge while that PR is in flight, dev advances and the
        # feature PR gets a 405 "head behind base branch" on merge.
        git_dir = repo_root / ".git"
        pr_pending = git_dir / "PR_PENDING"
        if pr_pending.exists():
            print(
                "auto-sync: waiting for in-flight PR to finish...",
                file=sys.stderr,
            )
            deadline = time.monotonic() + _SYNC_PR_WAIT_TIMEOUT
            while pr_pending.exists() and time.monotonic() < deadline:
                time.sleep(_SYNC_PR_WAIT_INTERVAL)
            if pr_pending.exists():
                print(
                    "auto-sync: timed out waiting for in-flight PR, "
                    "skipping sync",
                    file=sys.stderr,
                )
                return
            print(
                "auto-sync: in-flight PR finished, proceeding with sync",
                file=sys.stderr,
            )

        # Circuit breaker: skip if recent failure
        fail_marker = git_dir / "TRACKER_SYNC_FAILED"
        fail_count = 0
        if fail_marker.exists():
            try:
                parts = fail_marker.read_text().strip().split(":", 1)
                fail_count = int(parts[0]) if len(parts) == 2 else 0
                fail_ts = float(parts[-1])
                elapsed = time.time() - fail_ts
                if elapsed < _SYNC_COOLDOWN_SECONDS:
                    remaining = int(_SYNC_COOLDOWN_SECONDS - elapsed)
                    print(
                        f"auto-sync: circuit breaker open "
                        f"({remaining}s remaining), skipping",
                        file=sys.stderr,
                    )
                    return
                # Cooldown expired — remove marker and retry
                fail_marker.unlink(missing_ok=True)
            except (ValueError, OSError):
                # Corrupt marker — remove and retry
                fail_marker.unlink(missing_ok=True)

        print(
            f"auto-sync: {lines} lines of tracker changes exceed "
            f"threshold ({threshold}), syncing...",
            file=sys.stderr,
        )

        # Check gate file BEFORE preflight to prevent concurrent auto-sync
        # calls from racing.  Previously the gate was only written in
        # do_sync step 8 (after commit creation), leaving a window where
        # multiple _maybe_auto_sync calls could all pass preflight and
        # push duplicate PRs.  Preflight also checks this gate, but by
        # checking here first we can bail out faster and more reliably.
        sync_gate = git_dir / "TRACKER_SYNC_PENDING"
        if sync_gate.exists():
            print(
                "auto-sync: sync already in progress "
                "(TRACKER_SYNC_PENDING exists), skipping",
                file=sys.stderr,
            )
            return

        pre = preflight_check(repo_root)
        if not pre.ok:
            print(
                f"auto-sync: preflight failed: {pre.error}",
                file=sys.stderr,
            )
            return

        if not pre.changed_files:
            return

        sync_result = do_sync(repo_root=repo_root, preflight=pre)
        if sync_result.success:
            # Clear failure marker on success
            fail_marker.unlink(missing_ok=True)
            print(
                f"auto-sync: synced {sync_result.files_synced} file(s) "
                f"via PR #{sync_result.pr_number}",
                file=sys.stderr,
            )
        else:
            # Set failure marker — circuit breaker opens
            new_count = fail_count + 1
            try:
                fail_marker.write_text(f"{new_count}:{time.time()}")
            except OSError:  # pragma: no cover
                pass
            print(
                f"auto-sync: sync failed ({new_count}/"
                f"{_SYNC_MAX_CONSECUTIVE_FAILURES}): {sync_result.error}",
                file=sys.stderr,
            )
            if new_count >= _SYNC_MAX_CONSECUTIVE_FAILURES:
                _escalate_disable_autonomous(repo_root, new_count)
    except Exception as e:
        print(f"auto-sync: unexpected error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Sync reminder
# ---------------------------------------------------------------------------


def _print_sync_reminder() -> None:
    """Print a short reminder about automatic sync to stderr.

    Shows how many tracker lines are pending and the auto-sync threshold
    so agents remember NOT to push or sync manually — the tracker handles
    that automatically when the threshold is exceeded.

    Never raises; silently no-ops if git or pending_sync_lines fails.
    """
    from hypergumbo_tracker.sync import (
        AUTO_SYNC_DEFAULT_THRESHOLD,
        pending_sync_lines,
    )

    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return
        repo_root = Path(result.stdout.strip())

        threshold_str = os.environ.get(
            "TRACKER_AUTO_SYNC_THRESHOLD",
            str(AUTO_SYNC_DEFAULT_THRESHOLD),
        )
        try:
            threshold = int(threshold_str)
        except ValueError:
            threshold = AUTO_SYNC_DEFAULT_THRESHOLD

        lines = pending_sync_lines(repo_root)
        print(
            f"[tracker] Auto-sync is AUTOMATIC — do NOT push or sync manually."
            f" {lines} pending line(s), threshold={threshold}.",
            file=sys.stderr,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Primary CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        raise SystemExit(EXIT_USER_ERROR)

    # Commands that don't need TrackerSet
    if args.command == "init":
        raise SystemExit(_cmd_init(args))
    if args.command == "setup":
        raise SystemExit(_cmd_setup(args))
    if args.command == "migrate":
        raise SystemExit(_cmd_migrate(args))
    if args.command == "sync":
        raise SystemExit(_cmd_sync(args))

    # Discover tracker root
    try:
        if args.tracker_root:
            tracker_root = Path(args.tracker_root)
            if not tracker_root.is_dir():
                print(f"error: {tracker_root} is not a directory", file=sys.stderr)
                raise SystemExit(EXIT_USER_ERROR)
        else:
            tracker_root = _find_tracker_root()
    except SystemExit:
        raise

    # Store resolved path for batch command's sub-dispatching
    args.tracker_root_resolved = str(tracker_root)

    # Create TrackerSet with optional cache
    try:
        config = load_config(tracker_root / "tracker")
        cache_dir = _get_cache_dir(tracker_root)
        ts = TrackerSet(tracker_root, config=config, cache_dir=cache_dir)

        # Wire Cache instances for each tier
        if cache_dir is not None:
            from hypergumbo_tracker.cache import Cache

            caches = {}
            for tier_val, store in ts._tier_stores.items():
                db_path = cache_dir / f"{tier_val.value}.cache.db"
                db_path.parent.mkdir(parents=True, exist_ok=True)
                caches[tier_val] = Cache(store, db_path, tier_val)
            ts.set_caches(caches)
    except Exception as e:
        print(f"error: failed to initialize tracker: {e}", file=sys.stderr)
        raise SystemExit(EXIT_INTERNAL_ERROR) from e

    # Dispatch
    handler_map: dict[str, Any] = {
        "show": _cmd_show,
        "list": _cmd_list,
        "ready": _cmd_ready,
        "log": _cmd_log,
        "add": _cmd_add,
        "update": _cmd_update,
        "discuss": _cmd_discuss,
        "deps": _cmd_deps,
        "lock": _cmd_lock,
        "unlock": _cmd_unlock,
        "freeze": _cmd_freeze,
        "unfreeze": _cmd_unfreeze,
        "repair-drift": _cmd_repair_drift,
        "promote": _cmd_promote,
        "demote": _cmd_demote,
        "stealth": _cmd_stealth,
        "unstealth": _cmd_unstealth,
        "delete": _cmd_delete,
        "validate": _cmd_validate,
        "count-todos": _cmd_count_todos,
        "hash-todos": _cmd_hash_todos,
        "guidance": _cmd_guidance,
        "check-messages": _cmd_check_messages,
        "cache-rebuild": _cmd_cache_rebuild,
        "reconcile-reset": _cmd_reconcile_reset,
        "fork-setup": _cmd_fork_setup,
        "tui": _cmd_tui,
        "batch": _cmd_batch,
    }

    handler = handler_map.get(args.command)
    if handler is None:  # pragma: no cover — argparse prevents this
        print(f"error: unknown command {args.command!r}", file=sys.stderr)
        raise SystemExit(EXIT_USER_ERROR)

    try:
        exit_code = handler(args, ts) if args.command not in ("init",) else handler(args)
    except (ItemNotFoundError, AmbiguousPrefixError, ItemExistsError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(EXIT_USER_ERROR) from e
    except (
        HumanAuthorityError, LockedFieldError, FrozenItemError, TierMovementError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(EXIT_USER_ERROR) from e
    except CorruptFileError as e:
        print(f"error: corrupt data: {e}", file=sys.stderr)
        raise SystemExit(EXIT_INTERNAL_ERROR) from e
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(EXIT_INTERNAL_ERROR) from e

    if (
        exit_code == EXIT_SUCCESS
        and args.command in _MUTATION_COMMANDS
        and not args.no_auto_sync
    ):
        _maybe_auto_sync(tracker_root)

    if exit_code == EXIT_SUCCESS:
        _print_sync_reminder()

    raise SystemExit(exit_code)


# ---------------------------------------------------------------------------
# Textconv entry point
# ---------------------------------------------------------------------------


def textconv_main(argv: list[str] | None = None) -> None:
    """Git textconv driver: reads an ops file, prints compiled state.

    Output format:
    <ID>  <title>
      status: <status>  priority: P<N>  tags: [tag1, tag2]
      parent: <parent-ID or null>  before: [ID, ...]  pr_ref: <ref or null>
      fields.<key>: <value>
      discussion: <N> entries
      locked: [field1, field2]
      ops: <N>  updated: <timestamp>
    """
    parser = argparse.ArgumentParser(
        prog="hypergumbo-tracker-textconv",
        description="Git textconv driver for .ops files",
    )
    parser.add_argument("file", help="Path to .ops file")
    args = parser.parse_args(argv)

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"error: file not found: {args.file}", file=sys.stderr)
        raise SystemExit(1)

    # Extract ID from filename
    name = filepath.name
    if name.startswith(".") and name.endswith(".ops"):
        item_id = name[1:-4]
    else:
        item_id = name

    try:
        ops = _parse_ops_file(filepath)
        item = compile_ops(ops, item_id)
    except CorruptFileError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    # Output textconv format
    lines: list[str] = []
    lines.append(f"{item.id}  {item.title}")
    lines.append(f"  status: {item.status}  priority: P{item.priority}  "
                 f"tags: [{', '.join(item.tags)}]")
    lines.append(f"  parent: {item.parent or 'null'}  "
                 f"before: [{', '.join(item.before)}]  "
                 f"pr_ref: {item.pr_ref or 'null'}")
    for k, v in item.fields.items():
        lines.append(f"  fields.{k}: {v}")
    lines.append(f"  discussion: {len(item.discussion)} entries")
    if item.locked_fields:
        lines.append(f"  locked: [{', '.join(sorted(item.locked_fields))}]")
    lines.append(f"  ops: {len(ops)}  updated: {item.updated_at}")

    print("\n".join(lines))
    raise SystemExit(0)
