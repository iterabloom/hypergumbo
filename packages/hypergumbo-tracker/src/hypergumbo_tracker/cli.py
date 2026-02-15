# SPDX-License-Identifier: MPL-2.0
"""CLI entry points for hypergumbo-tracker.

Provides the full argparse CLI for tracker operations and the git textconv
driver for rendering .ops files as readable text.

Entry points:
- main(): Primary CLI with ~24 subcommands (add, update, list, show, ready,
  log, discuss, lock, unlock, promote, demote, stealth, unstealth, validate,
  count-todos, hash-todos, guidance, init, cache-rebuild, reconcile-reset,
  fork-setup, migrate, tui).
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
import sys
from pathlib import Path
from typing import Any

from hypergumbo_tracker.models import (
    CompiledItem,
    Tier,
    load_config,
    resolve_actor,
)
from hypergumbo_tracker.store import (
    AmbiguousPrefixError,
    CorruptFileError,
    HumanAuthorityError,
    ItemExistsError,
    ItemNotFoundError,
    LockedFieldError,
    _parse_ops_file,
    compile_ops,
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
    if item.justification:
        lines.append(f"  justification: {item.justification}")
    lines.append(f"  discussion: {len(item.discussion)} entries")
    if item.locked_fields:
        lines.append(f"  locked: [{', '.join(sorted(item.locked_fields))}]")
    if item.duplicate_of:
        lines.append(f"  duplicate_of: [{', '.join(item.duplicate_of)}]")
    if item.not_duplicate_of:
        lines.append(f"  not_duplicate_of: [{', '.join(item.not_duplicate_of)}]")
    tier_str = item.tier.value if item.tier else "unknown"
    lines.append(f"  tier: {tier_str}  created: {item.created_at}  "
                 f"updated: {item.updated_at}")
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
        "justification": item.justification,
        "description": item.description,
        "fields": item.fields,
        "locked_fields": sorted(item.locked_fields),
        "discussion": [
            {"by": d.by, "actor": d.actor, "at": d.at,
             "message": d.message, "is_summary": d.is_summary}
            for d in item.discussion
        ],
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
    if args.json:
        print(json.dumps([_item_to_dict(i) for i in items], indent=2))
    else:
        if not items:
            print("(no ready items)")
        else:
            for idx, item in enumerate(items):
                print(_format_item_short(item, idx))
    return EXIT_SUCCESS


def _cmd_log(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'log' subcommand — print raw ops."""
    full_id, store, _ = ts._resolve_id(args.item_id)
    path = store.item_path(full_id)
    if not path.exists():
        print(f"error: item not found: {args.item_id}", file=sys.stderr)
        return EXIT_USER_ERROR
    print(path.read_text(), end="")
    return EXIT_SUCCESS


def _cmd_add(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'add' subcommand."""
    tier = Tier(args.tier) if args.tier else Tier.WORKSPACE

    kwargs: dict[str, Any] = {}
    if args.status:
        kwargs["status"] = args.status
    if args.priority is not None:
        kwargs["priority"] = args.priority
    if args.parent:
        kwargs["parent"] = args.parent
    if args.tag:
        kwargs["tags"] = args.tag
    if args.before:
        kwargs["before"] = args.before
    if args.description:
        kwargs["description"] = args.description
    if args.pr_ref:
        kwargs["pr_ref"] = args.pr_ref
    if args.justification:
        kwargs["justification"] = args.justification

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
        set_fields["parent"] = args.parent
    if args.pr_ref:
        set_fields["pr_ref"] = args.pr_ref
    if args.justification:
        set_fields["justification"] = args.justification
    if args.description:
        set_fields["description"] = args.description

    if args.add_tag:
        add_fields["tags"] = args.add_tag
    if args.remove_tag:
        remove_fields["tags"] = args.remove_tag
    if args.add_before:
        add_fields["before"] = args.add_before
    if args.remove_before:
        remove_fields["before"] = args.remove_before
    if args.duplicate_of:
        add_fields["duplicate_of"] = args.duplicate_of
    if args.not_duplicate_of:
        add_fields["not_duplicate_of"] = args.not_duplicate_of

    if args.field:
        fields_dict: dict[str, Any] = {}
        for kv in args.field:
            if "=" not in kv:
                print(f"error: --field must be key=value, got {kv!r}", file=sys.stderr)
                return EXIT_USER_ERROR
            k, v = kv.split("=", 1)
            fields_dict[k] = v
        set_fields["fields"] = fields_dict

    ts.update(
        args.item_id,
        set_fields=set_fields or None,
        add_fields=add_fields or None,
        remove_fields=remove_fields or None,
    )

    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("updated")
    return EXIT_SUCCESS


def _cmd_discuss(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'discuss' subcommand."""
    if args.clear:
        ts.discuss(args.item_id, message="", clear=True)
    elif args.summarize:
        ts.discuss(args.item_id, message=args.message, summarize=True)
    else:
        ts.discuss(args.item_id, message=args.message)

    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print("discussed")
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

    # Create .gitignore for stealth
    gitignore = root / "tracker-workspace" / "stealth" / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*.ops\n")

    if args.json:
        print(json.dumps({"root": str(root)}))
    else:
        print(f"initialized tracker at {root}")
    return EXIT_SUCCESS


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

    with open(config_path, "w") as f:
        yaml.dump(config_data, f, default_flow_style=False)

    if args.json:
        print(json.dumps({"ok": True, "scope": "workspace"}))
    else:
        print("scope set to 'workspace'")
    return EXIT_SUCCESS


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


def _cmd_tui(args: argparse.Namespace, ts: TrackerSet) -> int:
    """Handle 'tui' subcommand — launch Textual TUI."""
    try:
        from hypergumbo_tracker.tui import TrackerApp
    except ImportError:
        print("TUI requires textual: pip install hypergumbo-tracker[tui]", file=sys.stderr)
        return EXIT_USER_ERROR
    app = TrackerApp(tracker_set=ts)
    app.run()
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
    p_add.add_argument("--justification", help="Justification text")
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
    p_update.add_argument("--justification", help="New justification")
    p_update.add_argument("--description", help="New description")
    p_update.add_argument("--add-tag", action="append", help="Add tag")
    p_update.add_argument("--remove-tag", action="append", help="Remove tag")
    p_update.add_argument("--add-before", action="append", help="Add before link")
    p_update.add_argument("--remove-before", action="append", help="Remove before link")
    p_update.add_argument("--duplicate-of", dest="duplicate_of", action="append",
                          help="Mark as duplicate of")
    p_update.add_argument("--not-duplicate-of", dest="not_duplicate_of", action="append",
                          help="Mark as not duplicate of")
    p_update.add_argument("--field", action="append", help="Field key=value (repeatable)")

    # --- discuss ---
    p_discuss = sub.add_parser("discuss", help="Add discussion to an item")
    p_discuss.add_argument("item_id", help="Item ID or prefix")
    p_discuss.add_argument("message", nargs="?", default="", help="Discussion message")
    p_discuss.add_argument("--clear", action="store_true", help="Clear discussion (human only)")
    p_discuss.add_argument("--summarize", action="store_true",
                           help="Replace discussion with summary")

    # --- lock ---
    p_lock = sub.add_parser("lock", help="Lock fields on an item (human only)")
    p_lock.add_argument("item_id", help="Item ID or prefix")
    p_lock.add_argument("fields", nargs="+", help="Field names to lock")

    # --- unlock ---
    p_unlock = sub.add_parser("unlock", help="Unlock fields (human only)")
    p_unlock.add_argument("item_id", help="Item ID or prefix")
    p_unlock.add_argument("fields", nargs="+", help="Field names to unlock")

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

    # --- validate ---
    p_validate = sub.add_parser("validate", help="Validate tracker data")
    p_validate.add_argument("files", nargs="*", help="Specific files to validate")
    p_validate.add_argument("--similar", action="store_true",
                            help="Check SimHash near-duplicates")
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

    # --- init ---
    sub.add_parser("init", help="Initialize tracker directory structure")

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

    # --- tui ---
    sub.add_parser("tui", help="Launch interactive TUI (requires textual)")

    return parser


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
    if args.command == "migrate":
        raise SystemExit(_cmd_migrate(args))

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

    # Create TrackerSet
    try:
        config = load_config(tracker_root / "tracker")
        ts = TrackerSet(tracker_root, config=config)
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
        "lock": _cmd_lock,
        "unlock": _cmd_unlock,
        "promote": _cmd_promote,
        "demote": _cmd_demote,
        "stealth": _cmd_stealth,
        "unstealth": _cmd_unstealth,
        "validate": _cmd_validate,
        "count-todos": _cmd_count_todos,
        "hash-todos": _cmd_hash_todos,
        "guidance": _cmd_guidance,
        "cache-rebuild": _cmd_cache_rebuild,
        "reconcile-reset": _cmd_reconcile_reset,
        "fork-setup": _cmd_fork_setup,
        "tui": _cmd_tui,
    }

    handler = handler_map.get(args.command)
    if handler is None:  # pragma: no cover — argparse prevents this
        print(f"error: unknown command {args.command!r}", file=sys.stderr)
        raise SystemExit(EXIT_USER_ERROR)

    try:
        exit_code = handler(args, ts) if args.command not in ("init",) else handler(args)
    except (ItemNotFoundError, AmbiguousPrefixError, ItemExistsError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(EXIT_USER_ERROR) from e
    except (HumanAuthorityError, LockedFieldError, TierMovementError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(EXIT_USER_ERROR) from e
    except CorruptFileError as e:
        print(f"error: corrupt data: {e}", file=sys.stderr)
        raise SystemExit(EXIT_INTERNAL_ERROR) from e
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(EXIT_INTERNAL_ERROR) from e

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
