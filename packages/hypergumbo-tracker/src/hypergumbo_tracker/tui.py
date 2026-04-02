# SPDX-License-Identifier: MPL-2.0
"""Textual TUI for the hypergumbo tracker.

Provides a responsive terminal interface for browsing and managing tracker
items. The layout adapts to terminal size using a tier system:

- too-small (< 40x16): Shows only a "terminal too small" message
- compact (40x16 - 59x19): Full-width DataTable list, stacked detail view
  via Enter/Esc toggle
- standard (60x20 - 120x38): Two-pane layout with left list/tree panel
  and right detail panel. Cursor movement auto-updates the detail view.
  Tree toggle (t) switches between DataTable and Tree. Filter (f) narrows
  items by title, status, tags, or kind.
- wide (> 120x38): Extra DataTable columns (created, updated, conflict),
  longer proquint IDs, split right panel with activity log below detail,
  filter status indicator. Dynamic resize transitions between standard↔wide.

Two separate DataTable instances exist for compact (#item-table) and standard
(#std-table) to avoid reparenting complexity. Both use _populate_table() for
shared population logic. _format_detail_lines() is shared between compact
stacked detail and standard right-panel detail.

The tier computation, ID truncation, and shortest-unique-prefix computation
are pure functions for easy unit testing. IDs are auto-shortened to the
minimum distinguishing prefix (snapped to proquint syllable boundaries);
the ``i`` key toggles full ID display.

``Ctrl+C`` copies text to the system clipboard via the OSC 52 terminal
escape sequence (Textual's ``copy_to_clipboard``).  When the inline chat
TextArea (wide mode) is focused with a selection, it yanks the selection;
otherwise it yanks the full detail text for the highlighted item.  This
bypasses the mouse-capture issue where Textual intercepts click-and-drag,
preventing terminal-native text selection in clients like Royal TSX.

Write keybindings (d, D, m, n, e, p, b, l) push ModalScreen subclasses that
gather input, then call TrackerSet write methods on dismiss. Errors are shown
via ``self.notify(str(e), severity="error")``. After each write, _load_items()
refreshes the tables and _restore_selection() keeps the cursor stable.

Status visibility toggles (``c``/``w``) hide or show items with ``done``
and ``wont_do`` statuses respectively.  A status filter bar below the table
shows the current show/hide state for each resolved status.

Manual display reordering (``<``/``>``) lets users visually group related
items without changing their priority.  The reorder is persisted to a
``tui_preferences.json`` file alongside toggle state.

See ADR-0013 §TUI for the responsive design specification.
"""

from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Click, MouseDown, MouseMove, MouseUp, Resize
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    OptionList,
    Rule,
    Select,
    Static,
    TextArea,
    Tree,
)
from textual.widgets.option_list import Option

from rich.text import Text as RichText

from hypergumbo_tracker.models import CompiledItem, FieldSchema, Tier
from hypergumbo_tracker.store import (
    AmbiguousPrefixError,
    DiscussionRateLimitError,
    FrozenItemError,
    HumanAuthorityError,
    ItemNotFoundError,
    LockedFieldError,
)
from hypergumbo_tracker.trackerset import TierMovementError, TrackerSet


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def _compute_tier(w: int, h: int) -> str:
    """Return layout tier based on terminal dimensions.

    Thresholds from ADR-0013 responsive design matrix:
    - too-small: w < 40 or h < 16
    - compact: w < 60 or h < 20
    - wide: w > 120 and h > 38
    - standard: everything else
    """
    if w < 40 or h < 16:
        return "too-small"
    if w < 60 or h < 20:
        return "compact"
    if w > 120 and h > 38:
        return "wide"
    return "standard"


def _shortest_unique_prefix_len(ids: list[str]) -> int:
    """Return the minimum character count so every ID's prefix is unique.

    The algorithm extends from the kind prefix (e.g., "INV-", "WI-")
    incrementally until all prefixes are distinct, then snaps up to the
    next proquint syllable boundary (prefix + N complete ``-xxxxx`` pairs).
    Floor is prefix + 1 pair (e.g., ``INV-bolil`` = 9 chars) — never
    shows just the kind prefix alone.

    Returns 0 for an empty list.
    """
    if not ids:
        return 0

    # Find minimum prefix + 1 pair length for each ID
    # Proquint IDs: PREFIX-xxxxx-xxxxx-... where each pair is 6 chars (-xxxxx)
    # We need to find the character length where all prefixes are unique

    # Step 1: Find the minimum distinguishing raw char count
    max_len = max(len(id_) for id_ in ids)
    min_unique_len = 1
    for length in range(1, max_len + 1):
        prefixes = [id_[:length] for id_ in ids]
        if len(prefixes) == len(set(prefixes)):
            min_unique_len = length
            break
    else:
        # All IDs are identical up to the longest — use full length
        min_unique_len = max_len

    # Step 2: Snap up to the next proquint syllable boundary
    # Parse the first ID to understand the structure (all share the same
    # prefix structure within a kind, and we want the global max)
    result = 0
    for id_ in ids:
        parts = id_.split("-")
        if len(parts) <= 1:
            # Not a proquint — use raw length
            result = max(result, min_unique_len)
            continue

        prefix = parts[0]
        # prefix_len includes the trailing dash: "INV-" = 4
        prefix_len = len(prefix) + 1

        # Floor: prefix + 1 pair
        floor_len = prefix_len + len(parts[1]) if len(parts) > 1 else prefix_len

        # Snap min_unique_len to the next syllable boundary for this ID
        snapped = floor_len  # at least 1 pair
        cumulative = prefix_len
        for part in parts[1:]:
            cumulative += len(part)
            if cumulative >= min_unique_len:
                snapped = cumulative
                break
            cumulative += 1  # for the dash separator
            if cumulative >= min_unique_len:
                snapped = cumulative
                break
        else:
            # min_unique_len exceeds all pairs — use full ID
            snapped = len(id_)

        snapped = max(snapped, floor_len)
        result = max(result, snapped)

    return result


def _truncate_id(full_id: str, max_width: int) -> str:
    """Truncate proquint ID to fit column width.

    Proquint IDs follow the pattern PREFIX-xxxxx-xxxxx-xxxxx-xxxxx where each
    xxxxx is a 5-char syllable pair. Truncation preserves the prefix and as
    many syllable pairs as fit:

    - ≤10: prefix + 1 pair (with ellipsis)
    - 11-20: prefix + 2 pairs (with ellipsis if needed)
    - 21-32: prefix + 3-4 pairs (with ellipsis if needed)
    - >32: full ID
    """
    if len(full_id) <= max_width:
        return full_id

    # Split into prefix and syllable pairs
    parts = full_id.split("-")
    if len(parts) <= 1:
        # Not a proquint ID, just hard-truncate
        return full_id[:max_width - 1] + "…"

    prefix = parts[0]

    if max_width <= 10:
        # prefix + dash + 1 pair + ellipsis (len(parts) > 1 guaranteed by line 74)
        candidate = f"{prefix}-{parts[1]}…"
        if len(candidate) <= max_width:
            return candidate
        return full_id[:max_width - 1] + "…"

    # Try progressively more pairs
    for n_pairs in range(len(parts) - 1, 0, -1):
        candidate = "-".join(parts[: n_pairs + 1])
        if n_pairs < len(parts) - 1:
            candidate += "…"
        if len(candidate) <= max_width:
            return candidate

    return full_id[:max_width - 1] + "…"


def _format_timestamp(iso_ts: str) -> str:
    """Convert ISO 8601 timestamp to compact display format.

    Converts ``"2026-02-15T10:30:45Z"`` → ``"2026-02-15 10:30"``.
    Returns ``""`` for empty input.  Short/malformed strings are returned
    as-is (graceful truncation).
    """
    if not iso_ts:
        return ""
    # Replace 'T' separator and truncate to minute precision
    readable = iso_ts.replace("T", " ")
    # "2026-02-15 10:30:45Z" → take first 16 chars "2026-02-15 10:30"
    if len(readable) >= 16:
        return readable[:16]
    return readable.rstrip("Z")


def _is_human_unread(
    item: CompiledItem,
    human_read_state: dict[str, dict[str, object]],
) -> bool:
    """Determine if an item has unread discussion entries from the human's perspective.

    Auto-toggle logic: last discussion entry by agent → unread, by human → read.
    Empty discussion → read.

    Manual overrides stored in *human_read_state* take precedence, but become
    stale when the discussion length changes (new entries arrived since the
    override was set), at which point auto-toggle fires again.

    State dict format per item: ``{"read": bool, "discussion_len": int}``.
    """
    # Check manual override first (takes precedence over auto-detect)
    override = human_read_state.get(item.id)
    if override is not None:
        stored_len = override.get("discussion_len", 0)
        if stored_len == len(item.discussion):
            # Override is current — use it
            return not override.get("read", True)
        # Override is stale — fall through to auto-detect

    if not item.discussion:
        return False

    # Auto-detect: last entry by agent → unread, by human → read
    return item.discussion[-1].by != "human"


def _item_title_text(
    item: CompiledItem,
    human_read_state: dict[str, dict[str, object]],
) -> str:
    """Build the display title for an item with frozen/unread indicators.

    Prepends 🔵 for human-unread items and 🧊 for frozen items.
    When both apply, the blue dot comes first: ``🔵 🧊 Title``.
    """
    prefix = ""
    if item.frozen:
        prefix = "\U0001f9ca "
    if _is_human_unread(item, human_read_state):
        prefix = "\U0001f535 " + prefix
    return prefix + item.title


def _format_activity_lines(item: CompiledItem, limit: int = 10) -> list[str]:
    """Format discussion entries as compact activity log lines.

    Returns a list of lines like ``"2026-02-15 10:30 [agent]: message"``.
    When discussion has more than *limit* entries, shows only the last
    *limit* with a ``"showing last N of M"`` header.  Returns
    ``["No recent activity"]`` when discussion is empty.

    Prepends a ``[20+ msgs]`` badge when entry count >= 20 (D9).
    """
    if not item.discussion:
        return ["No recent activity"]

    entries = item.discussion
    lines: list[str] = []

    if len(entries) >= 20:
        lines.append("\\[20+ msgs]")

    if len(entries) > limit:
        lines.append(f"(showing last {limit} of {len(entries)} entries)")
        entries = entries[-limit:]

    from hypergumbo_tracker.preview import (
        extract_svg_paths,
        format_preview_placeholder,
    )

    for entry in entries:
        ts = _format_timestamp(entry.at)
        lines.append(f"{ts} \\[{entry.by}]: {entry.message}")

        # Detect SVG paths and add inline preview placeholders (ADR-0020)
        svg_paths = extract_svg_paths(entry.message)
        for svg_path_str in svg_paths:
            svg_path = Path(svg_path_str)
            if svg_path.is_file():
                lines.append(f"  {format_preview_placeholder(svg_path_str)}")
            elif not svg_path.exists():
                lines.append(f"  \\[screenshot: {svg_path.name} — file not found]")

    return lines


_TIER_INDICATOR = {
    Tier.CANONICAL: "C",
    Tier.WORKSPACE: "W",
    Tier.STEALTH: "S",
}


def _label(name: str) -> str:
    """Wrap a structural field label in Rich bold-reverse markup.

    Escapes any literal ``[`` characters (e.g. ``[locked]``, ``[20+ msgs]``)
    so Rich doesn't swallow them as style tags.  The ``Static`` widget
    already renders Rich text, so the markup is applied automatically.
    """
    escaped = name.replace("[", "\\[")
    return f"[bold reverse]{escaped}[/]"


def _collapse_double_spacing(text: str) -> str:
    """Remove artificial double-spacing from multi-line text.

    Descriptions stored via YAML block scalars often end up with ``\\n\\n``
    between every line (a storage artifact). When the text has *no* single
    ``\\n`` between content lines — i.e. every line break is ``\\n\\n`` — we
    collapse to single-spaced.  If the text contains a mix of ``\\n`` and
    ``\\n\\n``, the ``\\n\\n`` are intentional paragraph breaks and are kept.
    """
    stripped = text.strip()
    if not stripped:
        return stripped
    # Split on \n\n (blank-line boundaries) — if there are NO single-\n
    # joins between content lines, every break is double-spaced.
    paragraphs = re.split(r"\n{2,}", stripped)
    has_single_newline = any("\n" in p for p in paragraphs)
    if has_single_newline:
        # Mixed: preserve paragraph breaks (join with blank line)
        return "\n\n".join(paragraphs)
    # All-double-spaced: collapse to single-spaced
    return "\n".join(paragraphs)


def _collect_all_tags(
    items: list[CompiledItem], well_known_tags: list[str],
) -> list[str]:
    """Return deduplicated, sorted list of all tags across items and config.

    Merges tags found on items with *well_known_tags* so pre-configured
    tags always appear in the filter panel even when no item carries them.
    """
    tags: set[str] = set()
    for item in items:
        tags.update(item.tags)
    tags.update(well_known_tags)
    return sorted(tags)


def _compute_fav_5(
    toggle_sessions: list[dict[str, int]],
    max_sessions: int = 9,
) -> list[str]:
    """Compute the "Fav 5" filter entries from recent toggle history.

    Sums toggle counts across the most recent *max_sessions* sessions.
    Returns the top 5 keys by summed count, ties broken alphabetically.
    Keys with zero total count are excluded.
    """
    recent = toggle_sessions[-max_sessions:] if toggle_sessions else []
    totals: dict[str, int] = {}
    for session in recent:
        for key, count in session.items():
            totals[key] = totals.get(key, 0) + count
    # Exclude zeros
    totals = {k: v for k, v in totals.items() if v > 0}
    # Sort by count desc, then alphabetically
    ranked = sorted(totals.keys(), key=lambda k: (-totals[k], k))
    return sorted(ranked[:5])


def _format_filter_entry(
    idx: int, label: str, is_hidden: bool, count: int,
    is_fav: bool = False,
) -> str:
    """Format a single filter panel entry with Rich markup.

    Args:
        idx: 1-based shortcut number. 0 means no shortcut (entries beyond 9).
        label: Status or tag name.
        is_hidden: Whether items matching this filter are currently hidden.
        count: Number of items matching this label.
        is_fav: Whether this entry is in the Fav 5.

    Returns Rich markup like ``[1] [X] ★ done (12)`` with dim styling
    when hidden.
    """
    num = f"\\[{idx}]" if idx > 0 else "   "
    check = "\\[X]" if is_hidden else "\\[ ]"
    star = " ★" if is_fav else ""
    text = f"{num} {check}{star} {label} ({count})"
    if is_hidden:
        return f"[dim]{text}[/dim]"
    return text


_PREFS_DEFAULTS: dict = {
    "hidden_statuses": [],
    "display_order": [],
    "hidden_tags": [],
    "toggle_sessions": [],
    "last_filters": {"hidden_statuses": [], "hidden_tags": []},
    "hide_tagged": False,
    "human_read_state": {},
}

_MAX_TOGGLE_SESSIONS = 9


def _load_tui_preferences(path: Path) -> dict:
    """Load TUI preferences from disk. Returns defaults on missing/corrupt file.

    Supports version 1 (hidden_statuses, display_order only) and version 2
    (adds hidden_tags, toggle_sessions, last_filters).  Missing v2 keys in a
    v1 file are filled with defaults.  A legacy ``toggle_counts`` flat dict
    is migrated to a single-element ``toggle_sessions`` list.

    Returns a dict with all v2 keys populated.
    """
    defaults = dict(_PREFS_DEFAULTS)
    defaults["last_filters"] = dict(_PREFS_DEFAULTS["last_filters"])
    if not path.is_file():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return defaults
        hs = data.get("hidden_statuses", [])
        do = data.get("display_order", [])
        if not isinstance(hs, list) or not isinstance(do, list):
            return defaults
        hrs_raw = data.get("human_read_state", {})
        hrs = hrs_raw if isinstance(hrs_raw, dict) else {}
        result: dict = {
            "hidden_statuses": hs,
            "display_order": do,
            "hidden_tags": data.get("hidden_tags", []),
            "toggle_sessions": data.get("toggle_sessions", []),
            "last_filters": data.get(
                "last_filters",
                {"hidden_statuses": [], "hidden_tags": []},
            ),
            "hide_tagged": data.get("hide_tagged", False),
            "human_read_state": hrs,
        }
        # Migrate legacy flat toggle_counts → single-element sessions list
        if not result["toggle_sessions"] and "toggle_counts" in data:
            tc = data["toggle_counts"]
            if isinstance(tc, dict) and tc:
                result["toggle_sessions"] = [tc]
        return result
    except (json.JSONDecodeError, OSError):
        return defaults


def _save_tui_preferences(
    path: Path,
    hidden_statuses: set[str],
    display_order: list[str],
    *,
    hidden_tags: set[str] | None = None,
    toggle_sessions: list[dict[str, int]] | None = None,
    last_filters: dict[str, list[str]] | None = None,
    hide_tagged: bool = False,
    human_read_state: dict[str, dict[str, object]] | None = None,
) -> bool:
    """Persist TUI preferences to disk (v2 schema).

    Writes a JSON file with ``version``, ``hidden_statuses``,
    ``display_order``, ``hidden_tags``, ``toggle_sessions``,
    ``last_filters``, and ``human_read_state`` keys.  Creates parent
    directories as needed.

    ``toggle_sessions`` is capped to the most recent 9 entries.

    Returns ``True`` on success, ``False`` if the write failed (e.g.
    permission denied).  Callers should tolerate failure gracefully —
    preferences are best-effort and must never crash the TUI.
    """
    sessions = list(toggle_sessions) if toggle_sessions else []
    if len(sessions) > _MAX_TOGGLE_SESSIONS:
        sessions = sessions[-_MAX_TOGGLE_SESSIONS:]
    has_v2 = hidden_tags is not None or toggle_sessions is not None or last_filters is not None or hide_tagged or human_read_state is not None
    data: dict[str, Any] = {
        "version": 2 if has_v2 else 1,
        "hidden_statuses": sorted(hidden_statuses),
        "display_order": list(display_order),
    }
    if has_v2:
        data["hidden_tags"] = sorted(hidden_tags) if hidden_tags else []
        data["toggle_sessions"] = sessions
        data["last_filters"] = last_filters or {
            "hidden_statuses": [], "hidden_tags": [],
        }
        data["hide_tagged"] = hide_tagged
        data["human_read_state"] = human_read_state or {}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _apply_custom_order(
    items: list[CompiledItem],
    custom_order: list[str],
) -> list[CompiledItem]:
    """Reorder items per custom display order.

    Items in *custom_order* appear first, in that order.
    Remaining items appear after, in their original (default) sort order.
    Stale IDs (not matching any item) are silently skipped.
    """
    id_to_item = {item.id: item for item in items}
    ordered: list[CompiledItem] = []
    seen: set[str] = set()
    for item_id in custom_order:
        if item_id in id_to_item and item_id not in seen:
            ordered.append(id_to_item[item_id])
            seen.add(item_id)
    for item in items:
        if item.id not in seen:
            ordered.append(item)
    return ordered


def _build_dep_index(
    items: list[CompiledItem],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build blockers/dependents maps from item before-links.

    Returns ``(blockers_of, dependents_of)`` where:
    - ``blockers_of[id]`` = list of IDs that *block* this item
      (i.e. the item's ``before`` field, filtered to valid IDs)
    - ``dependents_of[id]`` = list of IDs that this item *blocks*
      (i.e. items that list this ID in their ``before``)

    Dangling references (IDs not present in *items*) are silently dropped.
    """
    valid_ids = {item.id for item in items}
    blockers_of: dict[str, list[str]] = {}
    dependents_of: dict[str, list[str]] = {}
    for item in items:
        valid_before = [b for b in item.before if b in valid_ids]
        if valid_before:
            blockers_of[item.id] = valid_before
            for b in valid_before:
                dependents_of.setdefault(b, []).append(item.id)
    return blockers_of, dependents_of


def _compute_chain(
    item_id: str,
    blockers_of: dict[str, list[str]],
    dependents_of: dict[str, list[str]],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Compute the dependency chain around *item_id* via BFS.

    Returns ``(direct_up, trans_up, direct_down, trans_down)`` where:
    - ``direct_up``: items that directly block *item_id* (depth 1 upward)
    - ``trans_up``: items that transitively block *item_id* (depth > 1)
    - ``direct_down``: items directly blocked by *item_id* (depth 1 downward)
    - ``trans_down``: items transitively blocked by *item_id* (depth > 1)

    Cycle-safe via visited sets.
    """
    def _bfs(start: str, graph: dict[str, list[str]]) -> tuple[list[str], list[str]]:
        direct: list[str] = []
        transitive: list[str] = []
        visited: set[str] = {start}
        queue: list[tuple[str, int]] = [(start, 0)]
        while queue:
            current, depth = queue.pop(0)
            for neighbor in graph.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    if depth == 0:
                        direct.append(neighbor)
                    else:
                        transitive.append(neighbor)
                    queue.append((neighbor, depth + 1))
        return direct, transitive

    direct_up, trans_up = _bfs(item_id, blockers_of)
    direct_down, trans_down = _bfs(item_id, dependents_of)
    return direct_up, trans_up, direct_down, trans_down


def _dep_pill_id(full_id: str) -> str:
    """Truncate an ID for dep pill display: prefix + first syllable pair.

    ``"INV-babab-dabab-fabab-habab"`` → ``"INV-babab"``
    ``"WI-dabab"`` → ``"WI-dabab"`` (already short)
    """
    parts = full_id.split("-")
    if len(parts) <= 2:
        return full_id
    return f"{parts[0]}-{parts[1]}"


def _format_dep_pills(
    item_id: str,
    blockers_of: dict[str, list[str]],
    dependents_of: dict[str, list[str]],
    max_pills: int = 2,
    hidden_ids: set[str] | None = None,
) -> str:
    """Format dependency pills as Rich markup for DataTable display.

    Returns a string like ``← INV-babab  → WI-dabab`` with Rich color markup.
    Blockers use dark_orange (←), dependents use green (→).
    If a dep ID is in *hidden_ids*, italic weight is used instead of bold
    to signal that the item is currently filtered out of the table.
    If total deps exceed *max_pills*, shows first few + ``+N``.
    Returns empty string when no deps exist.
    """
    blockers = blockers_of.get(item_id, [])
    deps = dependents_of.get(item_id, [])
    if not blockers and not deps:
        return ""

    pills: list[str] = []
    remaining = max_pills

    for b in blockers:
        if remaining <= 0:
            break
        weight = "italic" if hidden_ids and b in hidden_ids else "bold"
        pills.append(f"[{weight} dark_orange]← {_dep_pill_id(b)}[/]")
        remaining -= 1

    for d in deps:
        if remaining <= 0:
            break
        weight = "italic" if hidden_ids and d in hidden_ids else "bold"
        pills.append(f"[{weight} green]→ {_dep_pill_id(d)}[/]")
        remaining -= 1

    overflow = len(blockers) + len(deps) - max_pills
    if overflow > 0:
        pills.append(f"[dim]+{overflow}[/]")

    return " ".join(pills)


def _format_detail_lines(
    item: CompiledItem,
    tier: str = "standard",
    fields_schema: dict[str, FieldSchema] | None = None,
    dependents_of: dict[str, list[str]] | None = None,
) -> list[str]:
    """Format a CompiledItem into lines for detail display.

    Used by both compact (stacked) and standard/wide (side-panel) detail views.
    Returns a list of lines suitable for joining with newlines.

    When *tier* is ``"wide"``, shows timestamps and conflict status in the
    detail panel, but suppresses the inline discussion section (the activity
    panel handles it instead).

    Per-field ``[locked]`` indicators replace the old summary line (D8).
    When *fields_schema* is provided, known fields render in schema
    declaration order with descriptions; unknown fields go under ``Other`` (D7).
    Discussion badge ``[20+ msgs]`` appears when entry count >= 20 (D9).
    """
    lines: list[str] = []
    if item.frozen:
        lines.append("[bold cyan]*** FROZEN ***[/bold cyan]")
    lines.append(f"{_label('Title:')} {item.title}")
    lines.append(f"{_label('ID:')} {item.id}")

    lock_s = " [locked]" if "status" in item.locked_fields else ""
    lines.append(f"{_label(f'Status{lock_s}:')} {item.status}")

    lock_p = " [locked]" if "priority" in item.locked_fields else ""
    lines.append(f"{_label(f'Priority{lock_p}:')} P{item.priority}")

    tier_str = item.tier.value if item.tier else "unknown"
    lines.append(f"{_label('Tier:')} {tier_str}")

    if tier == "wide":
        if item.created_at:
            lines.append(f"{_label('Created:')} {_format_timestamp(item.created_at)}")
        if item.updated_at:
            lines.append(f"{_label('Updated:')} {_format_timestamp(item.updated_at)}")
        if item.cross_tier_conflict:
            lines.append(f"{_label('Cross-tier conflict:')} YES")

    if item.tags:
        lines.append(f"{_label('Tags:')} {', '.join(item.tags)}")
    if item.parent:
        lines.append(f"{_label('Parent:')} {item.parent}")

    if item.before:
        lock_b = " [locked]" if "before" in item.locked_fields else ""
        lines.append(f"{_label(f'Blocked by{lock_b}:')} {', '.join(item.before)}")
    if dependents_of and item.id in dependents_of:
        lines.append(f"{_label('Blocks:')} {', '.join(dependents_of[item.id])}")

    lock_desc = " [locked]" if "description" in item.locked_fields else ""
    if item.description:
        desc = _collapse_double_spacing(item.description)
        lines.append(f"\n{_label(f'Description{lock_desc}:')}\n{desc}")

    if item.fields:
        if fields_schema:
            lines.append(f"\n{_label('Fields:')}")
            # Known fields in schema declaration order
            for fname, fschema in fields_schema.items():
                if fname in item.fields:
                    flabel = f" ({fschema.description})" if fschema.description else ""
                    lock = " [locked]" if fname in item.locked_fields else ""
                    lines.append(f"  {_label(f'{fname}{flabel}{lock}:')} {item.fields[fname]}")
            # Unknown fields (not in schema)
            unknown = {k: v for k, v in item.fields.items() if k not in fields_schema}
            if unknown:
                lines.append(f"\n  {_label('Other:')}")
                for k, v in unknown.items():
                    lock = " [locked]" if k in item.locked_fields else ""
                    lines.append(f"    {_label(f'{k}{lock}:')} {v}")
        else:
            lines.append(f"\n{_label('Fields:')}")
            for k, v in item.fields.items():
                lock = " [locked]" if k in item.locked_fields else ""
                lines.append(f"  {_label(f'{k}{lock}:')} {v}")

    # In wide mode, discussion is shown in the activity panel
    if tier != "wide" and item.discussion:
        count = len(item.discussion)
        badge = " [20+ msgs]" if count >= 20 else ""
        lock_d = " [locked]" if "discussion" in item.locked_fields else ""
        lines.append(f"\n{_label(f'Discussion{lock_d} ({count} entries){badge}:')}")
        for entry in item.discussion[-5:]:
            lines.append(f"  \\[{entry.at}] {entry.by}: {entry.message}")

    return lines


# ---------------------------------------------------------------------------
# Shared modal CSS factory
# ---------------------------------------------------------------------------


def _modal_css(cls_name: str) -> str:
    """Generate Textual CSS for a ModalScreen subclass.

    Wraps ``align: center middle`` in a selector matching *cls_name* so the
    stylesheet parser doesn't reject bare properties.
    """
    return f"""
    {cls_name} {{
        align: center middle;
    }}

    #modal-dialog {{
        width: 60;
        height: auto;
        max-height: 80%;
        overflow-y: auto;
        border: thick $accent;
        padding: 1 2;
        background: $surface;
    }}

    #modal-title {{
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }}

    .modal-buttons {{
        height: 3;
        align: center middle;
        margin-top: 1;
    }}

    .modal-buttons Button {{
        margin: 0 1;
    }}
"""

# ---------------------------------------------------------------------------
# Modal screens
# ---------------------------------------------------------------------------


class DiscussScreen(ModalScreen[str | None]):
    """Modal for adding a discussion entry to a tracker item.

    Presents a single-line Input for the message. Submit returns the message
    string; Cancel or Escape returns None.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("DiscussScreen")

    def __init__(self, item_id: str, item_title: str) -> None:
        super().__init__()
        self._item_id = item_id
        self._item_title = item_title

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(f"Discuss: {self._item_title}", id="modal-title")
            yield Input(placeholder="Enter message...", id="discuss-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Submit", variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            value = self.query_one("#discuss-input", Input).value.strip()
            self.dismiss(value if value else None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Reusable confirmation dialog with Yes/No buttons.

    Returns True if confirmed, False if cancelled. Used by ``D``
    (clear discussion) and potentially other destructive actions.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("ConfirmScreen")

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._message, id="modal-title")
            with Horizontal(classes="modal-buttons"):
                yield Button("Yes", variant="warning", id="yes")
                yield Button("No", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class TierMoveScreen(ModalScreen[str | None]):
    """Modal for tier movement operations.

    Shows available moves based on the item's current tier:
    - canonical → demote to workspace
    - workspace → promote to canonical, or stealth
    - stealth → unstealth to workspace

    Returns the chosen move string or None if cancelled.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("TierMoveScreen")

    def __init__(self, item_id: str, current_tier: Tier | None) -> None:
        super().__init__()
        self._item_id = item_id
        self._current_tier = current_tier

    def compose(self) -> ComposeResult:
        options = self._available_moves()
        tier_str = self._current_tier.value if self._current_tier else "unknown"
        with Vertical(id="modal-dialog"):
            yield Static(f"Move from: {tier_str}", id="modal-title")
            if options:
                yield Select[str](
                    options, id="move-select", allow_blank=False,
                )
            else:
                yield Static("No moves available for this tier")
            with Horizontal(classes="modal-buttons"):
                yield Button("Move", variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def _available_moves(self) -> list[tuple[str, str]]:
        if self._current_tier == Tier.CANONICAL:
            return [("Demote to workspace", "demote")]
        if self._current_tier == Tier.WORKSPACE:
            return [
                ("Promote to canonical", "promote"),
                ("Stealth", "stealth"),
            ]
        if self._current_tier == Tier.STEALTH:
            return [("Unstealth to workspace", "unstealth")]
        return []

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            try:
                select = self.query_one("#move-select", Select)
            except Exception:
                self.dismiss(None)
                return
            if select.value is not Select.BLANK:
                self.dismiss(str(select.value))
            else:  # pragma: no cover
                self.dismiss(None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NewItemScreen(ModalScreen[dict[str, Any] | None]):
    """Modal for creating a new tracker item.

    Presents fields for kind, title, status, priority, tier, and description.
    When a kind with ``allowed_statuses`` is selected, the status dropdown
    is filtered to only show those statuses.
    Returns a dict suitable for ``TrackerSet.add(**result)`` or None if
    cancelled.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("NewItemScreen")

    def __init__(
        self,
        kinds: list[str],
        statuses: list[str],
        kinds_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._kinds = kinds
        self._statuses = statuses
        self._kinds_config = kinds_config or {}

    def _statuses_for_kind(self, kind: str) -> list[str]:
        """Return statuses allowed for the given kind, or all statuses."""
        kc = self._kinds_config.get(kind)
        if kc is not None and kc.allowed_statuses is not None:
            return kc.allowed_statuses
        return self._statuses

    def compose(self) -> ComposeResult:
        kind_options: list[tuple[str, str]] = [(k, k) for k in self._kinds]
        # Initial status options based on first kind
        initial_kind = self._kinds[0] if self._kinds else ""
        initial_statuses = self._statuses_for_kind(initial_kind)
        status_options: list[tuple[str, str]] = [(s, s) for s in initial_statuses]
        tier_options: list[tuple[str, str]] = [
            ("workspace", "workspace"),
            ("canonical", "canonical"),
            ("stealth", "stealth"),
        ]
        with Vertical(id="modal-dialog"):
            yield Static("New Item", id="modal-title")
            yield Static("Kind:")
            yield Select[str](kind_options, id="kind-select", allow_blank=False)
            yield Static("Title:")
            yield Input(placeholder="Title", id="title-input")
            yield Static("Status:")
            yield Select[str](
                status_options, id="status-select", allow_blank=False,
            )
            yield Static("Priority:")
            yield Input(
                placeholder="Priority (0-9)", id="priority-input", value="2",
            )
            yield Static("Tier:")
            yield Select[str](
                tier_options, id="tier-select", allow_blank=False,
            )
            yield Static("Description:")
            yield Input(placeholder="Description (optional)", id="desc-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Create", variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_select_changed(self, event: Select.Changed) -> None:
        """When kind changes, update status dropdown to allowed statuses."""
        if event.select.id != "kind-select":
            return
        kind = str(event.value)
        new_statuses = self._statuses_for_kind(kind)
        status_select = self.query_one("#status-select", Select)
        new_options = [(s, s) for s in new_statuses]
        status_select.set_options(new_options)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            title = self.query_one("#title-input", Input).value.strip()
            if not title:
                self.dismiss(None)
                return
            kind = str(self.query_one("#kind-select", Select).value)
            status = str(self.query_one("#status-select", Select).value)
            tier_str = str(self.query_one("#tier-select", Select).value)
            tier_map = {
                "workspace": Tier.WORKSPACE,
                "canonical": Tier.CANONICAL,
                "stealth": Tier.STEALTH,
            }
            tier = tier_map.get(tier_str, Tier.WORKSPACE)
            priority_str = self.query_one("#priority-input", Input).value.strip()
            try:
                priority = int(priority_str)
            except ValueError:
                priority = 2
            desc = self.query_one("#desc-input", Input).value.strip()
            result: dict[str, Any] = {
                "kind": kind,
                "title": title,
                "status": status,
                "priority": priority,
                "tier": tier,
            }
            if desc:
                result["description"] = desc
            self.dismiss(result)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditItemScreen(ModalScreen[dict[str, Any] | None]):
    """Modal for editing an existing tracker item.

    Pre-populated with the item's current values. When the item's kind has
    ``allowed_statuses``, the status dropdown is filtered to only those.
    Returns a dict with ``set_fields``, ``add_fields``, and ``remove_fields``
    keys suitable for ``TrackerSet.update()``, or None if cancelled or
    nothing changed.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("EditItemScreen")

    def __init__(
        self,
        item: CompiledItem,
        statuses: list[str],
        kinds_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._item = item
        # Filter statuses by kind's allowed_statuses if applicable
        kc = (kinds_config or {}).get(item.kind)
        if kc is not None and kc.allowed_statuses is not None:
            self._statuses = list(kc.allowed_statuses)
        else:
            self._statuses = list(statuses)
        # Ensure the item's current status is always an option, even if
        # it was removed from allowed_statuses — otherwise the Select
        # widget raises InvalidSelectValueError.  Track it as deprecated
        # so compose() can render it dimmed.
        self._deprecated_status: str | None = None
        if item.status not in self._statuses:
            self._statuses.insert(0, item.status)
            self._deprecated_status = item.status

    def compose(self) -> ComposeResult:
        status_options: list[tuple[RichText | str, str]] = []
        for s in self._statuses:
            if s == self._deprecated_status:
                label = RichText.from_markup(f"[dim]{s} (deprecated)[/dim]")
                status_options.append((label, s))
            else:
                status_options.append((s, s))

        with Vertical(id="modal-dialog"):
            yield Static(f"Edit: {self._item.title}", id="modal-title")
            yield Static("Status:")
            yield Select[str](
                status_options, id="status-select",
                value=self._item.status, allow_blank=False,
            )
            yield Static("Priority:")
            yield Input(
                id="priority-input", value=str(self._item.priority),
            )
            yield Static("Title:")
            yield Input(id="title-input", value=self._item.title)
            yield Static("Tags (comma-separated):")
            yield Input(
                id="tags-input", value=", ".join(self._item.tags),
            )
            yield Static("Description:")
            yield Input(
                id="desc-input", value=self._item.description or "",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Save", variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            result: dict[str, Any] = {
                "set_fields": {},
                "add_fields": {},
                "remove_fields": {},
            }
            new_status = str(self.query_one("#status-select", Select).value)
            if new_status != self._item.status:
                result["set_fields"]["status"] = new_status

            priority_str = self.query_one(
                "#priority-input", Input,
            ).value.strip()
            try:
                new_priority = int(priority_str)
                if new_priority != self._item.priority:
                    result["set_fields"]["priority"] = new_priority
            except ValueError:
                pass

            new_title = self.query_one("#title-input", Input).value.strip()
            if new_title and new_title != self._item.title:
                result["set_fields"]["title"] = new_title

            new_desc = self.query_one("#desc-input", Input).value.strip()
            if new_desc != (self._item.description or ""):
                result["set_fields"]["description"] = new_desc

            new_tags_str = self.query_one("#tags-input", Input).value.strip()
            new_tags = (
                [t.strip() for t in new_tags_str.split(",") if t.strip()]
                if new_tags_str
                else []
            )
            old_tags = list(self._item.tags)
            tags_to_add = [t for t in new_tags if t not in old_tags]
            tags_to_remove = [t for t in old_tags if t not in new_tags]
            if tags_to_add:
                result["add_fields"]["tags"] = tags_to_add
            if tags_to_remove:
                result["remove_fields"]["tags"] = tags_to_remove

            if (
                result["set_fields"]
                or result["add_fields"]
                or result["remove_fields"]
            ):
                self.dismiss(result)
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ParentScreen(ModalScreen[str | None]):
    """Modal for setting or clearing an item's parent.

    Shows the current parent and provides an Input for the new parent ID.
    Submitting with an empty string clears the parent. Cancel returns None
    (distinct from empty-string submission).
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("ParentScreen")

    def __init__(self, item_id: str, current_parent: str | None) -> None:
        super().__init__()
        self._item_id = item_id
        self._current_parent = current_parent

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Set Parent", id="modal-title")
            yield Static(
                f"Current: {self._current_parent or '(none)'}",
            )
            yield Input(
                placeholder="Parent ID (empty to clear)",
                id="parent-input",
                value=self._current_parent or "",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Set", variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            value = self.query_one("#parent-input", Input).value.strip()
            # Return the value (empty string means clear parent)
            self.dismiss(value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class BeforeScreen(ModalScreen[dict[str, list[str]] | None]):
    """Modal for editing before (dependency) links.

    Shows current before links and provides inputs for IDs to add and
    IDs to remove. Returns ``{"add": [...], "remove": [...]}`` or None.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("BeforeScreen")

    def __init__(self, item_id: str, current_before: list[str]) -> None:
        super().__init__()
        self._item_id = item_id
        self._current_before = current_before

    def compose(self) -> ComposeResult:
        before_str = (
            ", ".join(self._current_before)
            if self._current_before
            else "(none)"
        )
        with Vertical(id="modal-dialog"):
            yield Static("Edit Before Links", id="modal-title")
            yield Static(f"Current: {before_str}")
            yield Static("Add IDs (comma-separated):")
            yield Input(placeholder="IDs to add", id="add-input")
            yield Static("Remove IDs (comma-separated):")
            yield Input(placeholder="IDs to remove", id="remove-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Apply", variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            add_str = self.query_one("#add-input", Input).value.strip()
            remove_str = self.query_one("#remove-input", Input).value.strip()
            add_ids = (
                [x.strip() for x in add_str.split(",") if x.strip()]
                if add_str
                else []
            )
            remove_ids = (
                [x.strip() for x in remove_str.split(",") if x.strip()]
                if remove_str
                else []
            )
            if add_ids or remove_ids:
                self.dismiss({"add": add_ids, "remove": remove_ids})
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DisambiguateScreen(ModalScreen[str | None]):
    """Modal for disambiguating a prefix that matches multiple items.

    Displays an ``OptionList`` of candidate items (full ID + title).
    Selecting an item dismisses with its full ID; Cancel/Escape dismisses
    with ``None``.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("DisambiguateScreen")

    def __init__(
        self, prefix: str, candidates: list[tuple[str, str]],
    ) -> None:
        super().__init__()
        self._prefix = prefix
        self._candidates = candidates

    def compose(self) -> ComposeResult:
        options = [
            Option(f"{full_id}  {title}", id=full_id)
            for full_id, title in self._candidates
        ]
        with Vertical(id="modal-dialog"):
            yield Static(
                f'Ambiguous prefix: "{self._prefix}"', id="modal-title",
            )
            yield Static("Select the intended item:")
            yield OptionList(*options, id="disambig-list")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        self.dismiss(event.option_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LockScreen(ModalScreen[dict[str, list[str]] | None]):
    """Modal for locking and unlocking item fields.

    Shows currently locked fields and provides inputs for fields to lock
    and fields to unlock. Returns ``{"lock": [...], "unlock": [...]}``
    or None.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("LockScreen")

    def __init__(self, item_id: str, locked_fields: set[str]) -> None:
        super().__init__()
        self._item_id = item_id
        self._locked_fields = locked_fields

    def compose(self) -> ComposeResult:
        locked_str = (
            ", ".join(sorted(self._locked_fields))
            if self._locked_fields
            else "(none)"
        )
        with Vertical(id="modal-dialog"):
            yield Static("Lock/Unlock Fields", id="modal-title")
            yield Static(f"Currently locked: {locked_str}")
            yield Static("Lock fields (comma-separated):")
            yield Input(placeholder="Fields to lock", id="lock-input")
            yield Static("Unlock fields (comma-separated):")
            yield Input(placeholder="Fields to unlock", id="unlock-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Apply", variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            lock_str = self.query_one("#lock-input", Input).value.strip()
            unlock_str = self.query_one("#unlock-input", Input).value.strip()
            to_lock = (
                [x.strip() for x in lock_str.split(",") if x.strip()]
                if lock_str
                else []
            )
            to_unlock = (
                [x.strip() for x in unlock_str.split(",") if x.strip()]
                if unlock_str
                else []
            )
            if to_lock or to_unlock:
                self.dismiss({"lock": to_lock, "unlock": to_unlock})
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Annotation overlay (ADR-0020 Part 1)
# ---------------------------------------------------------------------------


class _AnnotationCanvas(Widget):
    """Full-screen overlay widget for drawing annotations.

    Uses ``render_line()`` to produce per-line Strips where annotation
    cells have colored characters and non-annotation cells are truly
    transparent (Rich Segments with ``bgcolor=None``).  This lets the
    frozen TUI screen show through the overlay.
    """

    DEFAULT_CSS = """
    _AnnotationCanvas {
        width: 100%;
        height: 100%;
    }
    """

    can_focus = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rects: list[tuple[int, int, int, int, str]] = []
        self._labels: list[tuple[int, int, str, str]] = []
        self._arrows: list[tuple[int, int, int, int, str]] = []
        self._draft_rect: tuple[int, int, int, int] | None = None

    def set_draft_rect(
        self, x1: int, y1: int, x2: int, y2: int,
    ) -> None:
        """Update the in-progress rectangle (shown while dragging)."""
        self._draft_rect = (
            min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2),
        )
        self._render_canvas()

    def commit_rect(self, color: str = "#ff3333") -> None:
        """Promote the draft rectangle to a committed annotation."""
        if self._draft_rect is not None:
            self._rects.append((*self._draft_rect, color))
            self._draft_rect = None
            self._render_canvas()

    def discard_draft(self) -> None:
        """Discard the in-progress rectangle."""
        self._draft_rect = None
        self._render_canvas()

    def add_arrow(
        self, from_x: int, from_y: int, to_x: int, to_y: int,
        color: str = "#ff3333",
    ) -> None:
        """Add an arrow annotation."""
        self._arrows.append((from_x, from_y, to_x, to_y, color))
        self._render_canvas()

    def add_label(
        self, x: int, y: int, text: str, color: str = "#ff3333",
    ) -> None:
        """Add a text label annotation."""
        self._labels.append((x, y, text, color))
        self._render_canvas()

    def get_annotations(self) -> list[object]:
        """Return all committed annotations as dataclass instances."""
        from hypergumbo_tracker.annotations import (
            ArrowAnnotation,
            LabelAnnotation,
            RectAnnotation,
        )

        result: list[object] = []
        for x1, y1, x2, y2, color in self._rects:
            result.append(RectAnnotation(
                cell_x1=x1, cell_y1=y1, cell_x2=x2, cell_y2=y2, color=color,
            ))
        for fx, fy, tx, ty, color in self._arrows:
            result.append(ArrowAnnotation(
                from_x=fx, from_y=fy, to_x=tx, to_y=ty, color=color,
            ))
        for x, y, text, color in self._labels:
            result.append(LabelAnnotation(cell_x=x, cell_y=y, text=text, color=color))
        return result

    def _build_grid(self) -> list[list[str | None]]:
        """Build a sparse annotation grid (None = transparent cell)."""
        width = self.size.width or 80
        height = self.size.height or 24
        grid: list[list[str | None]] = [[None] * width for _ in range(height)]

        def _draw_rect(
            x1: int, y1: int, x2: int, y2: int, ch: str = "█",
        ) -> None:
            for x in range(max(0, x1), min(width, x2 + 1)):
                if 0 <= y1 < height:
                    grid[y1][x] = ch
                if 0 <= y2 < height:
                    grid[y2][x] = ch
            for y in range(max(0, y1), min(height, y2 + 1)):
                if 0 <= x1 < width:
                    grid[y][x1] = ch
                if 0 <= x2 < width:
                    grid[y][x2] = ch

        for x1, y1, x2, y2, _color in self._rects:
            _draw_rect(x1, y1, x2, y2, "█")

        if self._draft_rect is not None:
            x1, y1, x2, y2 = self._draft_rect
            _draw_rect(x1, y1, x2, y2, "░")

        for fx, fy, tx, ty, _color in self._arrows:
            dx = tx - fx
            dy = ty - fy
            steps = max(abs(dx), abs(dy), 1)
            for i in range(steps + 1):
                cx = fx + round(dx * i / steps)
                cy = fy + round(dy * i / steps)
                if 0 <= cy < height and 0 <= cx < width:
                    if i == steps:
                        grid[cy][cx] = "▶" if abs(dx) >= abs(dy) and dx >= 0 else \
                                       "◀" if abs(dx) >= abs(dy) else \
                                       "▼" if dy >= 0 else "▲"
                    else:
                        grid[cy][cx] = "─" if abs(dx) >= abs(dy) else "│"

        for lx, ly, text, _color in self._labels:
            if 0 <= ly < height:
                for i, ch in enumerate(text):
                    cx = lx + i
                    if 0 <= cx < width:
                        grid[ly][cx] = ch

        return grid

    def _render_canvas(self) -> None:
        """Trigger a re-render by refreshing the widget."""
        self.refresh()

    def render_line(self, y: int) -> Strip:
        """Render a single line as a Strip with transparent non-annotation cells.

        Annotation characters are bright red; non-annotation cells use
        Segment(" ") with no background, which Textual composites
        transparently over the underlying screen content.
        """
        from rich.segment import Segment
        from rich.style import Style

        width = self.size.width or 80
        height = self.size.height or 24
        if y < 0 or y >= height:
            return Strip([Segment(" " * width)])

        grid = self._build_grid()
        if y >= len(grid):
            return Strip([Segment(" " * width)])

        row = grid[y]
        ann_style = Style(color="red", bold=True)
        segments: list[Segment] = []
        for ch in row:
            if ch is not None:
                segments.append(Segment(ch, ann_style))
            else:
                segments.append(Segment(" "))

        return Strip(segments)


class AnnotationScreen(ModalScreen[list[object] | None]):
    """Full-screen annotation overlay for screenshot markup (ADR-0020).

    Enters annotation mode: the screen content is frozen, a transparent
    overlay appears on top. The user draws rectangles via mouse drag,
    places labels via ``L`` key + click, and confirms with Enter or
    discards with Escape.

    Returns a list of Annotation dataclass instances on confirm, or
    None on discard.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "discard", "Discard"),
        ("enter", "confirm", "Confirm"),
        ("r", "rect_mode", "Rect"),
        ("a", "arrow_mode", "Arrow"),
        ("l", "label_mode", "Label"),
        ("u", "undo", "Undo"),
        ("up", "nudge_up", "Nudge up"),
        ("down", "nudge_down", "Nudge down"),
        ("left", "nudge_left", "Nudge left"),
        ("right", "nudge_right", "Nudge right"),
    ]

    DEFAULT_CSS = """
    AnnotationScreen {
        background: transparent;
    }

    #annotation-canvas {
        width: 100%;
        height: 1fr;
        background: transparent;
    }

    #annotation-status {
        height: 1;
        dock: bottom;
        background: $accent;
        color: $text;
        text-align: center;
    }
    """

    def __init__(self, svg_content: str) -> None:
        super().__init__()
        self._svg_content = svg_content
        self._mode: str = "rect"  # "rect" or "label"
        self._dragging = False
        self._drag_start: tuple[int, int] | None = None
        self._label_pending = False

    _STATUS_TEXT = "  [R]ect  |  [A]rrow  |  [L]abel  |  [U]ndo  |  ←↑↓→=Nudge  |  Enter=Confirm  |  Esc=Discard  "

    def compose(self) -> ComposeResult:
        yield _AnnotationCanvas(id="annotation-canvas")
        yield Static(self._STATUS_TEXT, id="annotation-status")

    @property
    def canvas(self) -> _AnnotationCanvas:
        """The annotation canvas widget."""
        return self.query_one("#annotation-canvas", _AnnotationCanvas)

    def on_mouse_down(self, event: MouseDown) -> None:
        """Start a rect/arrow drag or place a label."""
        if self._mode == "label":
            self._label_pending = True
            self._label_pos = (event.x, event.y)
            self.query_one("#annotation-status", Static).update(
                "  Type label text, then press Enter  "
            )
        else:
            self._dragging = True
            self._drag_start = (event.x, event.y)

    def on_mouse_move(self, event: MouseMove) -> None:
        """Update draft rect while dragging."""
        if self._dragging and self._drag_start is not None and self._mode == "rect":
            sx, sy = self._drag_start
            self.canvas.set_draft_rect(sx, sy, event.x, event.y)

    def on_mouse_up(self, event: MouseUp) -> None:
        """Commit the rect or arrow on mouse release."""
        if self._dragging and self._drag_start is not None:
            sx, sy = self._drag_start
            has_area = abs(event.x - sx) > 1 or abs(event.y - sy) > 1
            if self._mode == "arrow":
                if has_area:
                    self.canvas.add_arrow(sx, sy, event.x, event.y)
                # No draft to discard for arrows
            else:
                if has_area:
                    self.canvas.set_draft_rect(sx, sy, event.x, event.y)
                    self.canvas.commit_rect()
                else:
                    self.canvas.discard_draft()
            self._dragging = False
            self._drag_start = None

    def action_discard(self) -> None:
        """Discard all annotations and exit."""
        self.dismiss(None)

    def action_confirm(self) -> None:
        """Confirm annotations and return them."""
        if self._label_pending and hasattr(self, "_label_text_buf"):
            # Commit pending label first
            x, y = self._label_pos
            self.canvas.add_label(x, y, self._label_text_buf)
            self._label_pending = False
            del self._label_text_buf
            self._mode = "rect"
            self.query_one("#annotation-status", Static).update(self._STATUS_TEXT)
            return
        annotations = self.canvas.get_annotations()
        self.dismiss(annotations if annotations else None)

    def action_rect_mode(self) -> None:
        """Switch to rectangle drawing mode."""
        self._mode = "rect"
        self.query_one("#annotation-status", Static).update(self._STATUS_TEXT)

    def action_arrow_mode(self) -> None:
        """Switch to arrow drawing mode."""
        self._mode = "arrow"
        self.query_one("#annotation-status", Static).update(
            "  ARROW MODE: Drag from start to end  |  Esc=Discard  "
        )

    def action_label_mode(self) -> None:
        """Switch to label placement mode."""
        self._mode = "label"
        self._label_text_buf = ""
        self.query_one("#annotation-status", Static).update(
            "  LABEL MODE: Click to place, type text, Enter to confirm  "
        )

    def _nudge(self, dx: int, dy: int) -> None:
        """Nudge the last annotation by (dx, dy) cells.

        Arrow key adjustment mitigates SSH mouse coordinate drift (ADR-0020).
        Applies to the most recently added annotation (rect, arrow, or label).
        """
        canvas = self.canvas
        if canvas._labels:
            x, y, text, color = canvas._labels[-1]
            canvas._labels[-1] = (x + dx, y + dy, text, color)
            canvas._render_canvas()
        elif canvas._arrows:
            fx, fy, tx, ty, color = canvas._arrows[-1]
            canvas._arrows[-1] = (fx + dx, fy + dy, tx + dx, ty + dy, color)
            canvas._render_canvas()
        elif canvas._rects:
            x1, y1, x2, y2, color = canvas._rects[-1]
            canvas._rects[-1] = (x1 + dx, y1 + dy, x2 + dx, y2 + dy, color)
            canvas._render_canvas()

    def action_nudge_up(self) -> None:
        """Move last annotation up by 1 cell."""
        self._nudge(0, -1)

    def action_nudge_down(self) -> None:
        """Move last annotation down by 1 cell."""
        self._nudge(0, 1)

    def action_nudge_left(self) -> None:
        """Move last annotation left by 1 cell."""
        self._nudge(-1, 0)

    def action_nudge_right(self) -> None:
        """Move last annotation right by 1 cell."""
        self._nudge(1, 0)

    def action_undo(self) -> None:
        """Remove the last annotation."""
        canvas = self.canvas
        if canvas._labels:
            canvas._labels.pop()
            canvas._render_canvas()
        elif canvas._arrows:
            canvas._arrows.pop()
            canvas._render_canvas()
        elif canvas._rects:
            canvas._rects.pop()
            canvas._render_canvas()

    def on_key(self, event: object) -> None:
        """Capture keystrokes for label text input."""
        if not self._label_pending:
            return
        if not hasattr(event, "character"):
            return  # pragma: no cover
        char = event.character  # type: ignore[attr-defined]
        if char and len(char) == 1 and char.isprintable():
            if not hasattr(self, "_label_text_buf"):
                self._label_text_buf = ""
            self._label_text_buf += char


# ---------------------------------------------------------------------------
# TrackerApp
# ---------------------------------------------------------------------------


class TrackerApp(App):
    """Textual TUI for the hypergumbo tracker.

    Three layout tiers are fully implemented:

    - **compact** (40x16 - 59x19): Single DataTable (#item-table) with
      stacked detail view (Enter/Esc).
    - **standard** (60x20 - 120x38): Two-pane layout -- left panel holds
      a DataTable (#std-table) or Tree (#item-tree), right panel shows
      detail for the highlighted item. Filter input (f) narrows items.
    - **wide** (>120x38): Standard layout enhanced with extra DataTable
      columns (conflict, created, updated), longer proquint IDs, split
      right panel with activity log below detail, and filter status
      indicator.
    """

    DEFAULT_CSS = """
    #too-small-msg {
        display: none;
        content-align: center middle;
        width: 100%;
        height: 100%;
        text-align: center;
    }

    #detail-view {
        display: none;
        height: 100%;
        overflow-y: auto;
    }

    #item-table {
        height: 1fr;
    }

    #two-pane {
        display: none;
        height: 1fr;
    }

    #left-panel {
        width: 55%;
        min-width: 30;
    }

    #std-table {
        height: 1fr;
    }

    #item-tree {
        display: none;
        height: 1fr;
    }

    #filter-input {
        display: none;
        dock: top;
        height: 1;
    }

    #filter-status {
        display: none;
        dock: top;
        height: 1;
    }

    #right-panel {
        width: 1fr;
    }

    #std-detail-view {
        overflow-y: auto;
    }

    #activity-divider {
        display: none;
    }

    #activity-column {
        height: 1fr;
    }

    #activity-view {
        display: none;
        height: 1fr;
        overflow-y: auto;
    }

    #chat-hint {
        display: none;
        height: 1;
    }

    #chat-input {
        display: none;
        height: 6;
        min-height: 4;
    }

    #chat-buttons {
        display: none;
        height: auto;
    }

    #chat-send {
        width: auto;
        min-width: 8;
    }

    #mark-read {
        width: auto;
        min-width: 12;
    }

    #mark-unread {
        width: auto;
        min-width: 14;
    }

    #status-filter-bar {
        display: none;
        height: 1;
        dock: bottom;
    }

    #chain-summary-bar {
        display: none;
        height: auto;
        dock: bottom;
        background: $surface;
    }

    #activity-filter-row {
        display: none;
        height: 55%;
        layout: horizontal;
    }

    #filter-panel-view {
        display: none;
        width: 1fr;
        min-width: 20;
        overflow-y: auto;
    }

    #filter-divider {
        display: none;
    }

    #std-filter-panel-view {
        display: none;
        overflow-y: auto;
        height: auto;
        max-height: 50%;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("escape", "back", "Back"),
        ("t", "toggle_tree", "Tree"),
        ("f", "toggle_filter_panel", "Filters"),
        ("less_than_sign", "move_up", "Move Up"),
        ("greater_than_sign", "move_down", "Move Down"),
        ("d", "discuss", "Discuss"),
        ("D", "discuss_clear", "Clear Disc."),
        ("m", "tier_move", "Move Tier"),
        ("n", "new_item", "New"),
        ("e", "edit_item", "Edit"),
        ("p", "set_parent", "Parent"),
        ("b", "edit_before", "Before"),
        ("l", "toggle_lock", "Lock"),
        ("z", "toggle_freeze", "Freeze"),
        ("R", "repair_drift", "Repair Drift"),
        ("i", "toggle_full_ids", "Full IDs"),
        ("ctrl+c", "yank", "Copy"),
        ("S", "capture_screenshot", "Screenshot"),
    ]

    def deliver_screenshot(
        self,
        filename: str | None = None,
        path: str | None = None,
        time_format: str | None = None,
    ) -> str | None:
        """Save SVG screenshot, creating the target directory if needed.

        The default save directory is the user's Downloads folder
        (``platformdirs.user_downloads_path``).  When the TUI is run from
        a different user account than the one whose home directory hosts
        the tracker, that directory may not exist, causing a silent
        ``FileNotFoundError`` and a "Failed to take screenshot" toast.
        We fix this by ensuring the directory exists before delegating.
        """
        if path is None:
            from platformdirs import user_downloads_path

            path = str(user_downloads_path())
        Path(path).mkdir(parents=True, exist_ok=True)
        return super().deliver_screenshot(filename, path, time_format)

    def __init__(self, tracker_set: TrackerSet, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._tracker_set = tracker_set
        self._layout_tier = "compact"
        self._layout_width = 0
        self._items: list[CompiledItem] = []
        self._in_detail = False
        self._selected_item_id: str | None = None
        self._tree_mode: bool = False
        self._filter_active: bool = False
        self._filter_text: str = ""
        self._show_full_ids: bool = False
        self._hidden_statuses: set[str] = set()
        self._hidden_tags: set[str] = set()
        self._hide_tagged: bool = False
        self._custom_order: list[str] = []
        self._blockers_of: dict[str, list[str]] = {}
        self._dependents_of: dict[str, list[str]] = {}
        self._ghost_row_ids: set[str] = set()
        self._filter_panel_visible: bool = False
        self._current_session_counts: dict[str, int] = {}
        self._toggle_sessions: list[dict[str, int]] = []
        self._last_filters: dict[str, list[str]] = {
            "hidden_statuses": [], "hidden_tags": [],
        }
        self._filter_entries: list[tuple[str, str]] = []  # (type:key, label) ordered
        self._filter_line_to_entry: dict[int, int] = {}  # line_num → 1-based entry index
        self._human_read_state: dict[str, dict[str, object]] = {}
        self._unread_override_ids: set[str] = set()
        self._prefs_path = (
            tracker_set._tracker_root / "tracker-workspace" / "tui_preferences.json"
        )

    def compose(self) -> ComposeResult:
        """Build the widget tree.

        Yields both compact and standard widgets. Visibility is controlled
        by _apply_layout() based on the current tier.
        """
        yield Header()
        yield Static("Terminal too small", id="too-small-msg")
        yield Input(placeholder="Filter...", id="filter-input")
        yield Static("", id="filter-status")
        # Compact-only widgets
        yield DataTable(id="item-table", cursor_type="row")
        yield VerticalScroll(Static("", id="detail-content"), id="detail-view")
        # Standard/wide two-pane widgets
        with Horizontal(id="two-pane"):
            with Vertical(id="left-panel"):
                yield DataTable(id="std-table", cursor_type="row")
                yield Tree("Items", id="item-tree")
            yield Rule(orientation="vertical", id="divider")
            with Vertical(id="right-panel"):
                yield VerticalScroll(
                    Static("", id="std-detail-content"), id="std-detail-view"
                )
                yield Rule(id="activity-divider")
                # Wide mode: activity + filter side by side
                with Horizontal(id="activity-filter-row"):
                    with Vertical(id="activity-column"):
                        yield VerticalScroll(
                            Static("", id="activity-content"),
                            id="activity-view",
                        )
                        yield Static(
                            "[dim]Ctrl+C copy \u00b7 Shift+Ins paste[/dim]",
                            id="chat-hint",
                        )
                        yield TextArea("", id="chat-input")
                        with Horizontal(id="chat-buttons"):
                            yield Button("Send", id="chat-send")
                            yield Button("Mark Read", id="mark-read")
                            yield Button("Mark Unread", id="mark-unread")
                    yield Rule(
                        orientation="vertical", id="filter-divider",
                    )
                    yield VerticalScroll(
                        Static("", id="filter-panel-content"),
                        id="filter-panel-view",
                    )
                # Standard mode: filter panel below detail
                yield VerticalScroll(
                    Static("", id="std-filter-content"),
                    id="std-filter-panel-view",
                )
        yield Static("", id="chain-summary-bar")
        yield Static("", id="status-filter-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize layout on mount, loading persisted preferences."""
        prefs = _load_tui_preferences(self._prefs_path)
        self._hidden_statuses = set(prefs["hidden_statuses"])
        self._hidden_tags = set(prefs.get("hidden_tags", []))
        self._hide_tagged = bool(prefs.get("hide_tagged", False))
        self._custom_order = list(prefs["display_order"])
        self._toggle_sessions = list(prefs.get("toggle_sessions", []))
        self._human_read_state = dict(prefs.get("human_read_state", {}))
        self._last_filters = prefs.get(
            "last_filters", {"hidden_statuses": [], "hidden_tags": []},
        )
        self._refresh_tier()
        self._load_items()
        self._update_status_filter_bar()

    def on_resize(self, event: Resize) -> None:
        """Re-evaluate layout tier when terminal is resized.

        In Textual 7.x, self.size is not yet updated when on_resize fires;
        the new dimensions are only available via event.size.
        """
        old_tier = self._layout_tier
        self._refresh_tier(event.size)
        if not self._items:
            self._load_items()
        elif old_tier != self._layout_tier:
            self._reload_active_table()
            self._restore_selection()

    # ------------------------------------------------------------------
    # Tier / layout management
    # ------------------------------------------------------------------

    def _refresh_tier(self, size: tuple[int, int] | None = None) -> None:
        """Compute and apply the layout tier based on terminal size.

        Args:
            size: Explicit (w, h) to use. When called from on_resize, pass
                  event.size because self.size is stale in Textual 7.x.
                  Defaults to self.size (used by on_mount).
        """
        w, h = size if size is not None else self.size
        self._layout_width = w
        new_tier = _compute_tier(w, h)
        if new_tier != self._layout_tier:
            # Leaving compact detail mode when switching to standard
            if self._in_detail and new_tier in ("standard", "wide"):
                self._in_detail = False
            self._layout_tier = new_tier
        self._apply_layout()

    def _apply_layout(self) -> None:
        """Show/hide widgets based on the current layout tier.

        Also manages focus — Textual's Input widget can steal focus even
        when hidden, so we explicitly focus the active interactive widget
        after toggling visibility.
        """
        table = self.query_one("#item-table", DataTable)
        detail = self.query_one("#detail-view")
        msg = self.query_one("#too-small-msg", Static)
        two_pane = self.query_one("#two-pane")
        filter_input = self.query_one("#filter-input", Input)
        # Filter panel widgets
        activity_filter_row = self.query_one("#activity-filter-row")
        filter_panel_view = self.query_one("#filter-panel-view")
        filter_divider = self.query_one("#filter-divider")
        std_filter_panel = self.query_one("#std-filter-panel-view")

        if self._layout_tier == "too-small":
            w, h = self.size
            msg.update(f"Terminal too small (need 40x16, got {w}x{h})")
            msg.display = True
            table.display = False
            detail.display = False
            two_pane.display = False
            filter_input.display = False
            activity_filter_row.display = False
            std_filter_panel.display = False
            return

        msg.display = False

        if self._layout_tier in ("standard", "wide"):
            # Standard/wide two-pane layout
            table.display = False
            detail.display = False
            two_pane.display = True
            filter_input.display = self._filter_active and self._layout_width >= 80

            # Activity panel and filter panel in wide mode
            activity_view = self.query_one("#activity-view")
            activity_divider = self.query_one("#activity-divider")
            # Chat widgets (wide-only inline discuss)
            chat_input = self.query_one("#chat-input", TextArea)
            chat_buttons = self.query_one("#chat-buttons", Horizontal)
            chat_hint = self.query_one("#chat-hint", Static)
            if self._layout_tier == "wide":
                activity_filter_row.display = True
                activity_view.display = True
                activity_divider.display = True
                chat_input.display = True
                chat_buttons.display = True
                chat_hint.display = True
                # Wide filter panel: inside the activity-filter-row
                filter_panel_view.display = self._filter_panel_visible
                filter_divider.display = self._filter_panel_visible
                # Standard filter panel hidden in wide mode
                std_filter_panel.display = False
            else:
                activity_filter_row.display = False
                activity_view.display = False
                activity_divider.display = False
                chat_input.display = False
                chat_buttons.display = False
                chat_hint.display = False
                filter_panel_view.display = False
                filter_divider.display = False
                # Standard mode: filter panel below detail
                std_filter_panel.display = self._filter_panel_visible

            # Tree/table toggle within left panel
            std_table = self.query_one("#std-table", DataTable)
            tree = self.query_one("#item-tree", Tree)
            if self._tree_mode:
                std_table.display = False
                tree.display = True
                if not self._filter_active:
                    tree.focus()
            else:
                std_table.display = True
                tree.display = False
                if not self._filter_active:
                    std_table.focus()
        else:
            # Compact layout
            two_pane.display = False
            filter_input.display = self._filter_active and self._layout_width >= 80
            activity_filter_row.display = False
            std_filter_panel.display = False
            filter_panel_view.display = False
            filter_divider.display = False
            if self._in_detail:
                table.display = False
                detail.display = True
            else:
                table.display = True
                detail.display = False
                if not self._filter_active:
                    table.focus()

    # ------------------------------------------------------------------
    # Item loading and table population
    # ------------------------------------------------------------------

    def _load_items(self) -> None:
        """Load items from TrackerSet and populate the active table."""
        self._items = self._tracker_set.list_items()
        self._blockers_of, self._dependents_of = _build_dep_index(self._items)
        if self._layout_tier in ("standard", "wide"):
            self._load_std_table()
            self._load_tree()
        else:
            self._populate_compact_table()

    def _filtered_items(self) -> list[CompiledItem]:
        """Return items matching the current filter text and status toggles.

        Pipeline:
        1. Exclude items whose status is in ``_hidden_statuses``
        2. Apply text-based filter (title, status, tags, kind)
        3. Apply custom display order (if any)
        4. Append human-unread items that were filtered out (override)

        Matches against title, status, tags, and kind (case-insensitive).
        Empty filter returns all (non-hidden) items.

        Items that are hidden by filters but have human-unread status are
        appended at the end.  Their IDs are tracked in ``_unread_override_ids``
        so the rendering code can style them differently (italic + blue dot).
        """
        # 1. Exclude hidden statuses
        base = [i for i in self._items if i.status not in self._hidden_statuses]
        # 1b. Exclude hidden tags (items with no tags are never hidden)
        if self._hidden_tags:
            base = [
                i for i in base
                if not any(t in self._hidden_tags for t in i.tags)
            ]
        # 1c. Exclude all tagged items if meta:tagged is active
        if self._hide_tagged:
            base = [i for i in base if not i.tags]
        # 2. Apply text filter
        if self._filter_text:
            needle = self._filter_text.lower()
            result: list[CompiledItem] = []
            for item in base:
                if needle in item.title.lower():
                    result.append(item)
                elif needle in item.status.lower():
                    result.append(item)
                elif needle in item.kind.lower():
                    result.append(item)
                elif any(needle in tag.lower() for tag in item.tags):
                    result.append(item)
            base = result
        # 3. Apply custom display order
        if self._custom_order:
            base = _apply_custom_order(base, self._custom_order)
        # 4. Append human-unread items hidden by filters (override)
        visible_ids = {i.id for i in base}
        self._unread_override_ids = set()
        for item in self._items:
            if item.id not in visible_ids and _is_human_unread(
                item, self._human_read_state,
            ):
                base.append(item)
                self._unread_override_ids.add(item.id)
        return base

    def _hidden_ids(self) -> set[str]:
        """Return IDs present in _items but excluded by current filters.

        These are items that exist in the tracker but are hidden by status
        or tag filters.  Used to render italic dep pills and ghost rows.
        """
        visible = {i.id for i in self._filtered_items()}
        return {i.id for i in self._items} - visible

    def _populate_table(self, table: DataTable, width: int, tier: str) -> None:
        """Populate a DataTable with items, adapting columns to width and tier.

        Shared by both compact (#item-table) and standard/wide (#std-table).
        Standard always shows the status column; compact only at width >= 55.
        Wide mode adds conflict, created, and updated columns after title,
        and widens the ID column.
        """
        items = self._filtered_items()
        table.clear(columns=True)
        hidden = self._hidden_ids()

        is_standard = table.id == "std-table"
        is_wide = tier == "wide"
        show_status = is_standard or width >= 55

        table.add_column("#", key="row_num", width=4)
        table.add_column("T", key="tier")
        table.add_column("P", key="priority")

        id_width = min(max(10, width // 4), 35)
        if is_standard:
            id_cap = 45 if is_wide else 35
            id_width = min(max(15 if not is_wide else 20, width // 3), id_cap)
        table.add_column("ID", key="id")

        if show_status:
            table.add_column("Status", key="status")

        show_deps = is_standard
        if show_deps:
            table.add_column("Deps", key="deps")

        table.add_column("Title", key="title")

        if is_standard:
            table.add_column("Tags", key="tags")

        if is_wide:
            table.add_column("Conflict", key="conflict")
            table.add_column("Created", key="created")
            table.add_column("Updated", key="updated")

        # Content-driven ID width: use shortest unique prefix unless
        # full IDs are toggled on
        all_ids = [item.id for item in items]
        if self._show_full_ids:
            id_display_len = max((len(id_) for id_ in all_ids), default=id_width)
        else:
            id_display_len = _shortest_unique_prefix_len(all_ids)
            # Still cap at column width as upper bound
            if id_display_len > 0:
                id_display_len = min(id_display_len, id_width)
            else:
                id_display_len = id_width

        for idx, item in enumerate(items):
            tier_char = _TIER_INDICATOR.get(item.tier, "?") if item.tier else "?"
            truncated_id = (
                item.id if self._show_full_ids
                else _truncate_id(item.id, id_display_len)
            )

            row: list[str | RichText] = [
                str(idx + 1),
                tier_char,
                str(item.priority),
                truncated_id,
            ]
            if show_status:
                row.append(item.status)
            if show_deps:
                pills = _format_dep_pills(
                    item.id, self._blockers_of, self._dependents_of,
                    hidden_ids=hidden,
                )
                row.append(RichText.from_markup(pills) if pills else "")
            row.append(_item_title_text(item, self._human_read_state))

            if is_standard:
                row.append(", ".join(item.tags) if item.tags else "")

            if is_wide:
                row.append("\u26a0" if item.cross_tier_conflict else "")
                row.append(_format_timestamp(item.created_at))
                row.append(_format_timestamp(item.updated_at))

            table.add_row(*row, key=item.id)

        if items:
            table.move_cursor(row=0)

    def _populate_compact_table(self) -> None:
        """Populate the compact DataTable."""
        table = self.query_one("#item-table", DataTable)
        w, _ = self.size
        self._populate_table(table, w, self._layout_tier)

    def _load_std_table(self) -> None:
        """Populate the standard/wide DataTable."""
        self._ghost_row_ids = set()
        table = self.query_one("#std-table", DataTable)
        w, _ = self.size
        self._populate_table(table, w, self._layout_tier)

    def _load_tree(self) -> None:
        """Build tree from parent-child hierarchy using self._items."""
        tree = self.query_one("#item-tree", Tree)
        tree.clear()
        items = self._filtered_items()

        # Build parent→children map
        children_map: dict[str | None, list[CompiledItem]] = {}
        item_ids = {item.id for item in items}
        for item in items:
            parent = item.parent if item.parent in item_ids else None
            children_map.setdefault(parent, []).append(item)

        def _add_children(parent_node: object, parent_id: str | None) -> None:
            for child in children_map.get(parent_id, []):
                tier_char = (
                    _TIER_INDICATOR.get(child.tier, "?") if child.tier else "?"
                )
                label = f"[{tier_char}] {_item_title_text(child, self._human_read_state)}"
                node = parent_node.add(label, data=child.id)  # type: ignore[union-attr]
                _add_children(node, child.id)

        _add_children(tree.root, None)
        tree.root.expand_all()

    def _reload_active_table(self) -> None:
        """Repopulate whichever view is active, using _filtered_items()."""
        if self._layout_tier in ("standard", "wide"):
            self._load_std_table()
            self._load_tree()
        else:
            self._populate_compact_table()

    def _restore_selection(self) -> None:
        """After tier change or reload, move cursor to _selected_item_id."""
        if not self._selected_item_id:
            return

        if self._layout_tier in ("standard", "wide"):
            table = self.query_one("#std-table", DataTable)
        else:
            table = self.query_one("#item-table", DataTable)

        for idx, row_key in enumerate(table.rows):
            if str(row_key.value) == self._selected_item_id:
                table.move_cursor(row=idx)
                return

    # ------------------------------------------------------------------
    # Detail display
    # ------------------------------------------------------------------

    def _show_detail(self, item: CompiledItem) -> None:
        """Populate the compact detail view with item information."""
        fields_schema = self._get_fields_schema(item)
        lines = _format_detail_lines(
            item, fields_schema=fields_schema,
            dependents_of=self._dependents_of,
        )
        if item.frozen:
            drift = self._tracker_set.drift_check(item.id)
            if drift:
                lines[0] = "[bold yellow]*** FROZEN (DRIFTED) ***[/bold yellow]"
        content = self.query_one("#detail-content", Static)
        content.update("\n".join(lines))

        self._in_detail = True
        self._apply_layout()

    def _show_std_detail(self, item_id: str) -> None:
        """Update the standard/wide right-panel detail for the given item ID.

        In wide mode, passes the tier to _format_detail_lines (to suppress
        inline discussion) and updates the activity panel.
        """
        item = next((i for i in self._items if i.id == item_id), None)
        if not item:
            return
        self._selected_item_id = item_id
        fields_schema = self._get_fields_schema(item)
        lines = _format_detail_lines(
            item, tier=self._layout_tier, fields_schema=fields_schema,
            dependents_of=self._dependents_of,
        )
        if item.frozen:
            drift = self._tracker_set.drift_check(item.id)
            if drift:
                lines[0] = "[bold yellow]*** FROZEN (DRIFTED) ***[/bold yellow]"
        try:
            content = self.query_one("#std-detail-content", Static)
        except NoMatches:  # pragma: no cover — race: compose not finished
            return
        content.update("\n".join(lines))
        self._update_chain_summary(item_id)
        if self._layout_tier == "wide":
            self._show_activity(item)

    def _get_fields_schema(
        self, item: CompiledItem,
    ) -> dict[str, FieldSchema] | None:
        """Look up the fields_schema for an item's kind from config."""
        kind_config = self._tracker_set.config.kinds.get(item.kind)
        if kind_config:
            return kind_config.fields_schema
        return None

    def _show_activity(self, item: CompiledItem) -> None:
        """Populate the activity panel with discussion entries.

        Only effective in wide mode; in other tiers this is a no-op.
        """
        if self._layout_tier != "wide":
            return
        lines = _format_activity_lines(item)
        content = self.query_one("#activity-content", Static)
        content.update("\n".join(lines))

    def _remove_ghost_rows(self) -> None:
        """Remove italic ghost rows from previous chain selection."""
        if not self._ghost_row_ids:
            return
        try:
            table = self.query_one("#std-table", DataTable)
        except NoMatches:  # pragma: no cover — race: compose not finished
            return
        for rid in list(self._ghost_row_ids):
            try:
                table.remove_row(rid)
            except Exception:  # pragma: no cover
                pass  # Row may already be gone
        self._ghost_row_ids = set()

    def _append_hidden_chain_rows(self, selected_id: str) -> None:
        """Append hidden dependency chain members as italic rows at table bottom.

        When status/tag filters hide items that are part of the selected
        item's dependency chain, those items are surfaced as "ghost" rows
        at the bottom of the table.  Ghost rows are tracked in
        ``_ghost_row_ids`` so they can be removed when the cursor moves.
        """
        try:
            table = self.query_one("#std-table", DataTable)
        except NoMatches:  # pragma: no cover — race: compose not finished
            return
        hidden = self._hidden_ids()
        if not hidden:
            return

        direct_up, trans_up, direct_down, trans_down = _compute_chain(
            selected_id, self._blockers_of, self._dependents_of,
        )
        chain_ids = set(direct_up + trans_up + direct_down + trans_down)
        hidden_chain = chain_ids & hidden

        if not hidden_chain:
            return

        self._ghost_row_ids = hidden_chain

        # Determine column layout to match existing rows
        col_keys = [c.key.value for c in table.columns.values()]
        is_wide = "conflict" in col_keys
        show_status = "status" in col_keys
        show_deps = "deps" in col_keys

        # Compute ID display length to match existing rows
        visible_items = self._filtered_items()
        all_ids = [i.id for i in visible_items]
        if self._show_full_ids:
            id_display_len = max((len(id_) for id_ in all_ids), default=20)
        else:
            id_display_len = _shortest_unique_prefix_len(all_ids)
            if id_display_len <= 0:  # pragma: no cover
                id_display_len = 20

        ghost_num = table.row_count + 1
        for item_id in sorted(hidden_chain):
            item = next((i for i in self._items if i.id == item_id), None)
            if not item:  # pragma: no cover
                continue
            tier_char = (
                _TIER_INDICATOR.get(item.tier, "?") if item.tier else "?"
            )
            truncated_id = (
                item.id if self._show_full_ids
                else _truncate_id(item.id, id_display_len)
            )
            row: list[str | RichText] = [
                str(ghost_num),
                tier_char,
                str(item.priority),
                truncated_id,
            ]
            if show_status:
                row.append(item.status)
            if show_deps:
                pills = _format_dep_pills(
                    item.id, self._blockers_of, self._dependents_of,
                    hidden_ids=hidden,
                )
                row.append(RichText.from_markup(pills) if pills else "")
            row.append(_item_title_text(item, self._human_read_state))
            if is_wide:
                row.append("\u26a0" if item.cross_tier_conflict else "")
                row.append(_format_timestamp(item.created_at))
                row.append(_format_timestamp(item.updated_at))
            table.add_row(*row, key=item.id)
            ghost_num += 1

    def _update_chain_highlight(self, selected_id: str) -> None:
        """Highlight chain members in the DataTable with colored rows.

        For each row in the standard table, applies color styling to ALL
        columns based on chain membership:
        - ``▶`` / blue: selected item
        - ``↑`` / dark_orange: direct blocker
        - ``↓`` / green: direct dependent
        - ``·`` / dim: transitive chain member

        Hidden chain members (filtered out by status/tag toggles) are
        appended as italic "ghost" rows at the bottom of the table.
        Ghost rows use italic weight instead of bold for their markers.

        Rows outside the chain are restored to plain text.
        The ``#`` column gets a directional marker prefix; other columns
        get the chain color applied to their text content.
        """
        if self._layout_tier not in ("standard", "wide"):
            return
        # Manage ghost rows: remove old ones, append new ones
        self._remove_ghost_rows()
        try:
            table = self.query_one("#std-table", DataTable)
        except NoMatches:  # pragma: no cover — race: compose not finished
            return
        if table.row_count == 0:
            return

        self._append_hidden_chain_rows(selected_id)

        direct_up, trans_up, direct_down, trans_down = _compute_chain(
            selected_id, self._blockers_of, self._dependents_of,
        )
        chain_direct_up = set(direct_up)
        chain_trans_up = set(trans_up)
        chain_direct_down = set(direct_down)
        chain_trans_down = set(trans_down)
        has_chain = bool(chain_direct_up or chain_direct_down)

        # Determine which columns to colorize (all except deps which has
        # its own styling)
        col_keys = [c.key.value for c in table.columns.values()]
        text_cols = [k for k in col_keys if k not in ("row_num", "deps")]

        for idx, row_key in enumerate(table.rows):
            rid = str(row_key.value)
            plain_num = str(idx + 1)
            is_ghost = rid in self._ghost_row_ids

            # Determine style and marker for this row
            if rid == selected_id and has_chain:
                style = "bold blue"
                marker_char = "▶"
            elif rid in chain_direct_up:
                style = "italic dark_orange" if is_ghost else "bold dark_orange"
                marker_char = "↑"
            elif rid in chain_direct_down:
                style = "italic green" if is_ghost else "bold green"
                marker_char = "↓"
            elif rid in chain_trans_up:
                style = "italic dim dark_orange" if is_ghost else "dim dark_orange"
                marker_char = "·"
            elif rid in chain_trans_down:
                style = "italic dim green" if is_ghost else "dim green"
                marker_char = "·"
            else:
                style = ""
                marker_char = ""

            # Update row_num column with marker
            if style:
                table.update_cell(
                    row_key, "row_num",
                    RichText.from_markup(f"[{style}]{marker_char}{plain_num}[/]"),
                )
            else:
                table.update_cell(row_key, "row_num", plain_num)

            # Update all other text columns with the chain color
            for col_key in text_cols:
                cell = table.get_cell(row_key, col_key)
                plain = cell.plain if hasattr(cell, "plain") else str(cell)
                if style:
                    table.update_cell(
                        row_key, col_key,
                        RichText.from_markup(f"[{style}]{plain}[/]"),
                    )
                else:
                    table.update_cell(row_key, col_key, plain)

    def _update_chain_summary(self, item_id: str) -> None:
        """Update the chain summary bar for the given item.

        Shows direct and transitive blockers/dependents with counts.
        Hides the bar when the item has no dependency relationships.
        """
        bar = self.query_one("#chain-summary-bar", Static)
        if not self._blockers_of and not self._dependents_of:
            bar.display = False
            return
        direct_up, trans_up, direct_down, trans_down = _compute_chain(
            item_id, self._blockers_of, self._dependents_of,
        )
        if not direct_up and not direct_down:
            bar.display = False
            return
        parts: list[str] = [f"[bold blue]▶ {_dep_pill_id(item_id)}[/]"]
        if direct_up:
            labels = [f"[bold dark_orange]{_dep_pill_id(x)}[/]" for x in direct_up]
            parts.append("blocked by " + ", ".join(labels))
        if trans_up:
            labels = [f"[dim dark_orange]{_dep_pill_id(x)}[/]" for x in trans_up]
            parts.append("(via " + ", ".join(labels) + ")")
        if direct_down:
            labels = [f"[bold green]{_dep_pill_id(x)}[/]" for x in direct_down]
            parts.append("blocks " + ", ".join(labels))
        if trans_down:
            labels = [f"[dim green]{_dep_pill_id(x)}[/]" for x in trans_down]
            parts.append("(then " + ", ".join(labels) + ")")
        total_up = len(direct_up) + len(trans_up)
        total_down = len(direct_down) + len(trans_down)
        parts.append(f"({total_up}↑ {total_down}↓)")
        bar.update("  ".join(parts))
        bar.display = True

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open compact detail view on Enter (compact only)."""
        if event.data_table.id == "std-table":
            # In standard mode, Enter is a no-op (detail already visible)
            return
        if self._in_detail or self._layout_tier == "too-small":
            return

        item_id = str(event.row_key.value)
        item = next((i for i in self._items if i.id == item_id), None)
        if item:
            self._show_detail(item)

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        """Auto-update right panel on cursor move (standard only)."""
        if event.data_table.id != "std-table":
            return
        if self._layout_tier not in ("standard", "wide"):
            return
        if event.row_key is None:
            return
        item_id = str(event.row_key.value)
        self._show_std_detail(item_id)
        self._update_chain_highlight(item_id)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Auto-update right panel when tree cursor moves."""
        if self._layout_tier not in ("standard", "wide"):
            return
        if event.node.data is None:
            return
        self._show_std_detail(str(event.node.data))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Update filter and reload table when filter input changes.

        Also shows/hides the filter status indicator based on whether
        there is active filter text.
        """
        if event.input.id != "filter-input":
            return
        self._filter_text = event.value
        self._reload_active_table()
        filter_status = self.query_one("#filter-status", Static)
        if event.value:
            filter_status.update(f"Filtering: {event.value}")
            filter_status.display = True
        else:
            filter_status.display = False

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_back(self) -> None:
        """Return from detail view to list view (compact), or dismiss filter."""
        if self._filter_active:
            self._dismiss_filter()
            return
        if not self._in_detail:
            return
        self._in_detail = False
        self._apply_layout()

    def action_toggle_tree(self) -> None:
        """Toggle between DataTable and Tree view (standard only)."""
        if self._layout_tier not in ("standard", "wide"):
            return
        self._tree_mode = not self._tree_mode
        self._apply_layout()

        # If switching to tree and we have a selection, the tree
        # NodeHighlighted will fire and update the detail panel
        if not self._tree_mode and self._selected_item_id:
            # Switching back to table — restore selection
            self._restore_selection()

    def action_toggle_filter_panel(self) -> None:
        """Toggle filter panel visibility.

        In compact mode, shows a notification (filter panel not available).
        In standard/wide mode, toggles the filter panel and refreshes content.
        """
        if self._layout_tier == "compact":
            self.notify(
                "Filter panel not available in compact mode",
                severity="warning",
            )
            return
        if self._layout_tier == "too-small":
            return
        self._filter_panel_visible = not self._filter_panel_visible
        self._apply_layout()
        if self._filter_panel_visible:
            self._refresh_filter_panel()

    def _dismiss_filter(self) -> None:
        """Hide filter input, clear filter text, hide status, and reload."""
        self._filter_active = False
        self._filter_text = ""
        filter_input = self.query_one("#filter-input", Input)
        filter_input.value = ""
        self.query_one("#filter-status", Static).display = False
        self._apply_layout()
        self._reload_active_table()

    def action_toggle_full_ids(self) -> None:
        """Toggle between shortest-unique-prefix and full ID display."""
        self._show_full_ids = not self._show_full_ids
        self._reload_active_table()
        self._restore_selection()

    def _toggle_status(self, status: str) -> None:
        """Show or hide items with the given *status*.

        Toggles the status in ``_hidden_statuses``, updates the status
        filter bar, reloads the active table, and tries to restore the
        cursor to the previously selected item.
        """
        if status in self._hidden_statuses:
            self._hidden_statuses.discard(status)
        else:
            self._hidden_statuses.add(status)
        self._update_status_filter_bar()
        self._reload_active_table()
        self._restore_selection()

    def _update_status_filter_bar(self) -> None:
        """Refresh the status filter bar text.

        Shows one ``[status: shown/hidden]`` badge per resolved status
        defined in the tracker config, plus a tag hidden count when any
        tags are hidden.  The bar is visible when at least one resolved
        status exists in the config or tags are hidden.
        """
        bar = self.query_one("#status-filter-bar", Static)
        resolved = self._tracker_set.config.resolved_statuses
        has_hidden_tags = bool(self._hidden_tags)
        if not resolved and not has_hidden_tags and not self._hide_tagged:
            bar.display = False
            return
        parts: list[str] = []
        for s in resolved:
            state = "hidden" if s in self._hidden_statuses else "shown"
            parts.append(f"[{s}: {state}]")
        if has_hidden_tags:
            parts.append(f"[tags hidden: {len(self._hidden_tags)}]")
        if self._hide_tagged:
            parts.append("[tagged: hidden]")
        bar.update("  ".join(parts))
        bar.display = True

    def action_move_up(self) -> None:
        """Move the selected item up one row in the display order."""
        self._move_selected(-1)

    def action_move_down(self) -> None:
        """Move the selected item down one row in the display order."""
        self._move_selected(1)

    def _move_selected(self, direction: int) -> None:
        """Move selected item up (-1) or down (+1) in custom display order.

        Operates on the flat table view only (not tree view).  At list
        boundaries the move is a no-op.  After moving, the custom order
        is persisted immediately and the table is refreshed.
        """
        item = self._get_selected_item()
        if not item:
            return
        displayed = self._filtered_items()
        idx = next((i for i, it in enumerate(displayed) if it.id == item.id), None)
        if idx is None:
            return  # pragma: no cover
        target = idx + direction
        if target < 0 or target >= len(displayed):
            return  # boundary — no-op
        # Ensure custom_order is populated from current display
        self._ensure_custom_order(displayed)
        # Swap in custom_order
        a_id, b_id = item.id, displayed[target].id
        a_idx = self._custom_order.index(a_id)
        b_idx = self._custom_order.index(b_id)
        self._custom_order[a_idx], self._custom_order[b_idx] = (
            self._custom_order[b_idx], self._custom_order[a_idx]
        )
        if not _save_tui_preferences(
            self._prefs_path, self._hidden_statuses, self._custom_order,
        ):
            self.notify("Could not save display order (permission denied)",
                        severity="warning")
        self._selected_item_id = item.id
        self._reload_active_table()
        self._restore_selection()

    def _ensure_custom_order(self, displayed: list[CompiledItem]) -> None:
        """Populate ``_custom_order`` from *displayed* items if incomplete.

        If any displayed item is missing from the current order list, the
        order is rebuilt from the currently displayed items, preserving
        existing positions for items already present.
        """
        displayed_ids = {it.id for it in displayed}
        if displayed_ids <= set(self._custom_order):
            return  # all displayed items are already in the order
        # Rebuild: keep existing order for known items, append new ones
        existing = [oid for oid in self._custom_order if oid in displayed_ids]
        existing_set = set(existing)
        for it in displayed:
            if it.id not in existing_set:
                existing.append(it.id)
        self._custom_order = existing

    def _toggle_tag(self, tag: str) -> None:
        """Show or hide items with the given *tag*.

        Toggles the tag in ``_hidden_tags``, updates the status filter bar,
        reloads the active table, and tries to restore the cursor.
        """
        if tag in self._hidden_tags:
            self._hidden_tags.discard(tag)
        else:
            self._hidden_tags.add(tag)
        self._update_status_filter_bar()
        self._reload_active_table()
        self._restore_selection()

    def _increment_toggle_count(self, key: str) -> None:
        """Bump toggle count for *key* in the current session."""
        self._current_session_counts[key] = (
            self._current_session_counts.get(key, 0) + 1
        )

    def _clear_all_filters(self) -> None:
        """Save current filters to _last_filters and clear all."""
        self._last_filters = {
            "hidden_statuses": sorted(self._hidden_statuses),
            "hidden_tags": sorted(self._hidden_tags),
            "hide_tagged": self._hide_tagged,
        }
        self._hidden_statuses.clear()
        self._hidden_tags.clear()
        self._hide_tagged = False
        self._update_status_filter_bar()
        self._reload_active_table()
        self._restore_selection()
        if self._filter_panel_visible:
            self._refresh_filter_panel()

    def _restore_last_filters(self) -> None:
        """Restore filters from _last_filters."""
        self._hidden_statuses = set(
            self._last_filters.get("hidden_statuses", []),
        )
        self._hidden_tags = set(
            self._last_filters.get("hidden_tags", []),
        )
        self._hide_tagged = bool(self._last_filters.get("hide_tagged", False))
        self._update_status_filter_bar()
        self._reload_active_table()
        self._restore_selection()
        if self._filter_panel_visible:
            self._refresh_filter_panel()

    def _refresh_filter_panel(self) -> None:
        """Rebuild filter panel content with statuses, tags, counts, and fav 5.

        Populates both the wide-mode (#filter-panel-content) and standard-mode
        (#std-filter-content) Static widgets with the same content.
        """
        items = self._items
        statuses = list(self._tracker_set.config.statuses)
        well_known = self._tracker_set.config.well_known_tags
        all_tags = _collect_all_tags(items, well_known)
        fav_5 = _compute_fav_5(
            self._toggle_sessions + (
                [self._current_session_counts]
                if self._current_session_counts else []
            ),
        )
        # Count items per status and tag
        status_counts: dict[str, int] = {}
        for item in items:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1
        tag_counts: dict[str, int] = {}
        for item in items:
            for t in item.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        # Build ordered entry list: fav 5 first, then statuses, then tags
        self._filter_entries = []
        self._filter_line_to_entry = {}
        lines: list[str] = []
        active_count = (
            len(self._hidden_statuses) + len(self._hidden_tags)
            + (1 if self._hide_tagged else 0)
        )
        lines.append(f"Filters Active: {active_count}")
        lines.append("")

        idx = 1

        # Favorites section
        fav_entries: list[tuple[str, str, bool, int]] = []
        for key in fav_5:
            if key.startswith("status:"):
                name = key[7:]
                is_hidden = name in self._hidden_statuses
                count = status_counts.get(name, 0)
            elif key.startswith("tag:"):
                name = key[4:]
                is_hidden = name in self._hidden_tags
                count = tag_counts.get(name, 0)
            else:
                continue
            fav_entries.append((key, name, is_hidden, count))

        if fav_entries:
            lines.append("── Favorites ──")
            for key, name, is_hidden, count in fav_entries:
                entry_idx = idx if idx <= 9 else 0
                self._filter_line_to_entry[len(lines)] = idx
                lines.append(
                    _format_filter_entry(entry_idx, name, is_hidden, count, is_fav=True),
                )
                self._filter_entries.append((key, name))
                idx += 1

        # Statuses section
        lines.append("── Statuses ──")
        for s in statuses:
            key = f"status:{s}"
            is_hidden = s in self._hidden_statuses
            count = status_counts.get(s, 0)
            entry_idx = idx if idx <= 9 else 0
            self._filter_line_to_entry[len(lines)] = idx
            lines.append(
                _format_filter_entry(entry_idx, s, is_hidden, count),
            )
            self._filter_entries.append((key, s))
            idx += 1

        # Tags section — always show "Tagged" meta-entry first
        tagged_count = sum(1 for item in items if item.tags)
        lines.append("── Tags ──")
        entry_idx_display = idx if idx <= 9 else 0
        self._filter_line_to_entry[len(lines)] = idx
        lines.append(
            _format_filter_entry(
                entry_idx_display, "Tagged", self._hide_tagged, tagged_count,
            ),
        )
        self._filter_entries.append(("meta:tagged", "Tagged"))
        idx += 1

        # Then individual tag entries
        if all_tags:
            for t in all_tags:
                key = f"tag:{t}"
                is_hidden = t in self._hidden_tags
                count = tag_counts.get(t, 0)
                entry_idx = idx if idx <= 9 else 0
                self._filter_line_to_entry[len(lines)] = idx
                lines.append(
                    _format_filter_entry(entry_idx, t, is_hidden, count),
                )
                self._filter_entries.append((key, t))
                idx += 1

        content = "\n".join(lines)
        # Update both panel locations
        try:
            self.query_one("#filter-panel-content", Static).update(content)
        except Exception:  # pragma: no cover
            pass
        try:
            self.query_one("#std-filter-content", Static).update(content)
        except Exception:  # pragma: no cover
            pass

    def _quick_toggle(self, idx: int) -> None:
        """Toggle filter entry at 1-based index *idx*.

        Maps the number to the corresponding entry from ``_filter_entries``
        (fav 5 first, then statuses, then tags) and toggles visibility.
        """
        if idx < 1 or idx > len(self._filter_entries):
            return
        key, _label_name = self._filter_entries[idx - 1]
        if key.startswith("status:"):
            status = key[7:]
            self._toggle_status(status)
            self._increment_toggle_count(key)
        elif key.startswith("tag:"):
            tag = key[4:]
            self._toggle_tag(tag)
            self._increment_toggle_count(key)
        elif key.startswith("meta:"):
            if key == "meta:tagged":
                self._hide_tagged = not self._hide_tagged
                self._update_status_filter_bar()
                self._reload_active_table()
                self._restore_selection()
            self._increment_toggle_count(key)
        if self._filter_panel_visible:
            self._refresh_filter_panel()

    def on_key(self, event: Any) -> None:
        """Handle digit key quick-toggles when filter panel is visible."""
        if not self._filter_panel_visible:
            return
        key = event.key
        if key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            event.prevent_default()
            self._quick_toggle(int(key))

    def on_click(self, event: Click) -> None:
        """Toggle filter entry when clicking on a filter panel line."""
        if not self._filter_panel_visible:
            return
        widget = event.widget
        if not isinstance(widget, Static):
            return
        if widget.id not in ("filter-panel-content", "std-filter-content"):
            return
        entry_idx = self._filter_line_to_entry.get(event.y)
        if entry_idx is not None:
            self._quick_toggle(entry_idx)

    async def action_quit(self) -> None:
        """Save TUI preferences (v2) and exit."""
        # Append current session counts to toggle_sessions
        sessions = list(self._toggle_sessions)
        if self._current_session_counts:
            sessions.append(self._current_session_counts)
        if not _save_tui_preferences(
            self._prefs_path, self._hidden_statuses, self._custom_order,
            hidden_tags=self._hidden_tags,
            toggle_sessions=sessions,
            last_filters=self._last_filters,
            hide_tagged=self._hide_tagged,
            human_read_state=self._human_read_state,
        ):
            self.notify("Could not save preferences (permission denied)",
                        severity="warning")
        await super().action_quit()

    def action_yank(self) -> None:
        """Copy text to the system clipboard via OSC 52.

        When the ``#chat-input`` TextArea is focused and has a selection,
        yanks only the selected text.  Otherwise falls back to yanking the
        full detail text for the currently selected tracker item.

        Rich markup is stripped so the clipboard receives plain text
        (e.g. ``Title:`` not ``[bold reverse]Title:[/]``).
        """
        from rich.text import Text

        # If the chat TextArea is focused with a selection, yank that
        try:
            text_area = self.query_one("#chat-input", TextArea)
            if text_area.has_focus and text_area.selected_text:
                self.copy_to_clipboard(text_area.selected_text)
                self.notify("Copied selection to clipboard")
                return
        except Exception:  # pragma: no cover
            pass

        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        fields_schema = self._get_fields_schema(item)
        lines = _format_detail_lines(
            item, tier=self._layout_tier, fields_schema=fields_schema,
            dependents_of=self._dependents_of,
        )
        plain_text = Text.from_markup("\n".join(lines)).plain
        self.copy_to_clipboard(plain_text)
        self.notify(f"Copied {item.id} to clipboard")

    def action_capture_screenshot(self) -> None:
        """Save SVG screenshot and open annotation mode (ADR-0020).

        Captures the current TUI screen as SVG, saves it, then opens
        the annotation overlay. If the user adds annotations, the SVG
        is re-saved with annotation elements injected.
        """
        from datetime import datetime

        from hypergumbo_tracker.annotations import screenshot_path as _screenshot_path

        item = self._get_selected_item()
        # Use short proquint prefix for filename readability
        if item:
            parts = item.id.split("-")
            item_id = "-".join(parts[:3]) if len(parts) >= 3 else item.id
        else:
            item_id = "screen"

        ts = datetime.now()
        rel_path = _screenshot_path(item_id, ts)
        svg = self.export_screenshot()

        # Try .agent/screenshots/ first, fall back to a user-owned temp dir
        # if not writable (e.g., different user than .agent/ owner).
        import os
        import tempfile

        path = self._tracker_set._tracker_root / "screenshots" / rel_path.name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg)
        except PermissionError:
            # Use $TMPDIR/<uid>-htrac-screenshots/ so each user gets their
            # own writable directory (avoids cross-user permission issues).
            fallback = Path(tempfile.gettempdir()) / f"{os.getuid()}-htrac-screenshots"
            fallback.mkdir(parents=True, exist_ok=True)
            path = fallback / rel_path.name
            path.write_text(svg)
        self.notify(f"Screenshot saved: {path}")

        # Open annotation mode
        self._pending_screenshot_path = path
        self._pending_svg = svg
        self.push_screen(
            AnnotationScreen(svg),
            callback=self._on_annotation_result,
        )

    def _on_annotation_result(
        self, annotations: list[object] | None,
    ) -> None:
        """Handle annotation screen result — inject annotations into SVG."""
        if annotations is None or not annotations:
            return

        from hypergumbo_tracker.annotations import (
            ArrowAnnotation,
            LabelAnnotation,
            RectAnnotation,
            sanitize_label_text,
        )

        path = self._pending_screenshot_path
        svg = self._pending_svg

        # Build SVG annotation group
        # Textual SVGs use 8.65px cell width and 18px cell height
        cell_w, cell_h = 8.65, 18.0
        elements: list[str] = []
        for ann in annotations:
            if isinstance(ann, RectAnnotation):
                x = ann.cell_x1 * cell_w
                y = ann.cell_y1 * cell_h
                w = (ann.cell_x2 - ann.cell_x1) * cell_w
                h = (ann.cell_y2 - ann.cell_y1) * cell_h
                elements.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" '
                    f'width="{w:.1f}" height="{h:.1f}" '
                    f'fill="none" stroke="{ann.color}" '
                    f'stroke-width="2" opacity="0.8"/>'
                )
            elif isinstance(ann, ArrowAnnotation):
                x1 = ann.from_x * cell_w + cell_w / 2
                y1 = ann.from_y * cell_h + cell_h / 2
                x2 = ann.to_x * cell_w + cell_w / 2
                y2 = ann.to_y * cell_h + cell_h / 2
                elements.append(
                    f'<line x1="{x1:.1f}" y1="{y1:.1f}" '
                    f'x2="{x2:.1f}" y2="{y2:.1f}" '
                    f'stroke="{ann.color}" stroke-width="2" '
                    f'marker-end="url(#arrowhead)" opacity="0.8"/>'
                )
            elif isinstance(ann, LabelAnnotation):
                x = ann.cell_x * cell_w
                y = ann.cell_y * cell_h + cell_h * 0.75  # baseline offset
                text = sanitize_label_text(ann.text)
                elements.append(
                    f'<text x="{x:.1f}" y="{y:.1f}" '
                    f'fill="{ann.color}" font-size="14" '
                    f'font-family="monospace">{text}</text>'
                )

        if elements:
            # Add arrowhead marker if any arrows present
            has_arrows = any(
                isinstance(a, ArrowAnnotation) for a in annotations
            )
            defs = ""
            if has_arrows:
                defs = (
                    '<defs><marker id="arrowhead" markerWidth="10" '
                    'markerHeight="7" refX="10" refY="3.5" orient="auto">'
                    '<polygon points="0 0, 10 3.5, 0 7" fill="#ff3333"/>'
                    '</marker></defs>\n'
                )
            group = (
                defs
                + '<g class="annotations">\n'
                + "\n".join(f"  {e}" for e in elements)
                + "\n</g>"
            )
            # Inject before closing </svg>
            svg_annotated = svg.replace("</svg>", f"{group}\n</svg>")
            path.write_text(svg_annotated)
            self.notify(
                f"Annotated screenshot saved: {path}",
                severity="information",
            )

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _get_selected_item(self) -> CompiledItem | None:
        """Return the CompiledItem for the currently highlighted row.

        Works in all layout tiers: compact table, standard/wide table,
        and tree mode. Returns None when no item is selected or the
        tier is too-small.
        """
        if self._layout_tier == "too-small":
            return None

        if self._layout_tier in ("standard", "wide"):
            if self._tree_mode:
                if self._selected_item_id:
                    return next(
                        (i for i in self._items
                         if i.id == self._selected_item_id),
                        None,
                    )
                return None
            table = self.query_one("#std-table", DataTable)
        else:
            table = self.query_one("#item-table", DataTable)

        if table.row_count == 0:
            return None

        row_keys = list(table.rows.keys())
        cursor_row = table.cursor_coordinate.row
        if cursor_row >= len(row_keys):
            return None  # pragma: no cover

        item_id = str(row_keys[cursor_row].value)
        return next(
            (i for i in self._items if i.id == item_id), None,
        )

    def _reload_after_write(self, select_id: str | None = None) -> None:
        """Reload items from TrackerSet and refresh the active table.

        Optionally restores cursor to *select_id* after reload.
        """
        self._load_items()
        if select_id:
            self._selected_item_id = select_id
        self._restore_selection()

    # ------------------------------------------------------------------
    # Human read/unread state management
    # ------------------------------------------------------------------

    def _mark_human_read(self, item_id: str) -> None:
        """Mark an item as read by the human.

        Stores the override in ``_human_read_state`` keyed by discussion
        length so auto-toggle can invalidate it when new entries arrive.
        """
        item = next((i for i in self._items if i.id == item_id), None)
        if not item:
            return  # pragma: no cover
        self._human_read_state[item_id] = {
            "read": True,
            "discussion_len": len(item.discussion),
        }
        self._save_human_read_state()
        self._reload_active_table()
        self._restore_selection()

    def _mark_human_unread(self, item_id: str) -> None:
        """Mark an item as unread by the human.

        Stores the override in ``_human_read_state`` keyed by discussion
        length so auto-toggle can invalidate it when new entries arrive.
        """
        item = next((i for i in self._items if i.id == item_id), None)
        if not item:
            return  # pragma: no cover
        self._human_read_state[item_id] = {
            "read": False,
            "discussion_len": len(item.discussion),
        }
        self._save_human_read_state()
        self._reload_active_table()
        self._restore_selection()

    def _save_human_read_state(self) -> None:
        """Persist the human read state to the preferences file."""
        _save_tui_preferences(
            self._prefs_path, self._hidden_statuses, self._custom_order,
            hidden_tags=self._hidden_tags,
            toggle_sessions=self._toggle_sessions,
            last_filters=self._last_filters,
            hide_tagged=self._hide_tagged,
            human_read_state=self._human_read_state,
        )

    # ------------------------------------------------------------------
    # Write actions
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses: chat Send, mark-read, mark-unread."""
        if event.button.id == "mark-read":
            item = self._get_selected_item()
            if item:
                self._mark_human_read(item.id)
            return
        if event.button.id == "mark-unread":
            item = self._get_selected_item()
            if item:
                self._mark_human_unread(item.id)
            return
        if event.button.id != "chat-send":
            return
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        text_area = self.query_one("#chat-input", TextArea)
        message = text_area.text.strip()
        if not message:
            return
        try:
            self._tracker_set.discuss(item.id, message)
            text_area.clear()
            self.notify(f"Discussion added to {item.id}")
            self._reload_after_write(item.id)
        except (
            ItemNotFoundError,
            LockedFieldError,
            DiscussionRateLimitError,
            PermissionError,
        ) as e:
            self.notify(str(e), severity="error")

    def action_discuss(self) -> None:
        """Open the discuss modal for the selected item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        self.push_screen(
            DiscussScreen(item.id, item.title),
            callback=partial(self._on_discuss, item.id),
        )

    def _on_discuss(self, item_id: str, message: str | None) -> None:
        """Handle discuss modal result."""
        if message is None:
            return
        try:
            self._tracker_set.discuss(item_id, message)
            self.notify(f"Discussion added to {item_id}")
            self._reload_after_write(item_id)
        except (
            ItemNotFoundError,
            LockedFieldError,
            DiscussionRateLimitError,
            PermissionError,
        ) as e:
            self.notify(str(e), severity="error")

    def action_discuss_clear(self) -> None:
        """Open confirmation dialog to clear discussion."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        self.push_screen(
            ConfirmScreen(f"Clear discussion for '{item.title}'?"),
            callback=partial(self._on_discuss_clear, item.id),
        )

    def _on_discuss_clear(self, item_id: str, confirmed: bool) -> None:
        """Handle discuss-clear confirmation result."""
        if not confirmed:
            return
        try:
            self._tracker_set.discuss(item_id, "", clear=True)
            self.notify(f"Discussion cleared for {item_id}")
            self._reload_after_write(item_id)
        except (HumanAuthorityError, ItemNotFoundError, PermissionError) as e:
            self.notify(str(e), severity="error")

    def action_tier_move(self) -> None:
        """Open the tier-move modal for the selected item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        self.push_screen(
            TierMoveScreen(item.id, item.tier),
            callback=partial(self._on_tier_move, item.id),
        )

    def _on_tier_move(self, item_id: str, move: str | None) -> None:
        """Handle tier-move modal result."""
        if move is None:
            return
        try:
            if move == "promote":
                self._tracker_set.promote(item_id)
            elif move == "demote":
                self._tracker_set.demote(item_id)
            elif move == "stealth":
                self._tracker_set.stealth_item(item_id)
            elif move == "unstealth":
                self._tracker_set.unstealth_item(item_id)
            self.notify(f"Tier moved: {move} for {item_id}")
            self._reload_after_write(item_id)
        except (
            TierMovementError, HumanAuthorityError, ItemNotFoundError,
            PermissionError,
        ) as e:
            self.notify(str(e), severity="error")

    def action_new_item(self) -> None:
        """Open the new-item modal."""
        config = self._tracker_set.config
        kinds = list(config.kinds.keys())
        statuses = list(config.statuses)
        self.push_screen(
            NewItemScreen(kinds, statuses, kinds_config=config.kinds),
            callback=self._on_new_item,
        )

    def _on_new_item(self, result: dict[str, Any] | None) -> None:
        """Handle new-item modal result."""
        if result is None:
            return
        try:
            item_id = self._tracker_set.add(**result)
            self.notify(f"Created: {item_id}")
            self._reload_after_write(item_id)
        except Exception as e:
            self.notify(str(e), severity="error")

    def action_edit_item(self) -> None:
        """Open the edit modal for the selected item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        config = self._tracker_set.config
        statuses = list(config.statuses)
        self.push_screen(
            EditItemScreen(item, statuses, kinds_config=config.kinds),
            callback=partial(self._on_edit_item, item.id),
        )

    def _on_edit_item(
        self, item_id: str, result: dict[str, Any] | None,
    ) -> None:
        """Handle edit-item modal result."""
        if result is None:
            return
        try:
            self._tracker_set.update(
                item_id,
                set_fields=result.get("set_fields") or None,
                add_fields=result.get("add_fields") or None,
                remove_fields=result.get("remove_fields") or None,
            )
            self.notify(f"Updated: {item_id}")
            self._reload_after_write(item_id)
        except (ItemNotFoundError, LockedFieldError, PermissionError) as e:
            self.notify(str(e), severity="error")

    def action_set_parent(self) -> None:
        """Open the parent modal for the selected item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        self.push_screen(
            ParentScreen(item.id, item.parent),
            callback=partial(self._on_set_parent, item.id),
        )

    def _resolve_ids(
        self,
        raw_ids: list[str],
        on_resolved: Any,
        resolved_so_far: list[str] | None = None,
        remaining: list[str] | None = None,
    ) -> None:
        """Resolve raw ID strings (possibly prefixes) to full IDs.

        Unique prefixes auto-resolve via ``TrackerSet._resolve_id``.
        Ambiguous prefixes push a ``DisambiguateScreen`` for interactive
        selection.  Calls ``on_resolved(full_ids)`` on success, or
        ``on_resolved(None)`` on cancel/error.

        Uses a callback chain for disambiguation (no worker required).
        """
        if resolved_so_far is None:
            resolved_so_far = []
        if remaining is None:
            remaining = list(raw_ids)

        while remaining:
            raw = remaining.pop(0)
            try:
                full_id, _store, _tier = self._tracker_set._resolve_id(raw)
                resolved_so_far.append(full_id)
            except AmbiguousPrefixError as exc:
                # Push disambiguation screen; continue chain in callback
                def _on_disambiguate(
                    chosen: str | None,
                    _resolved: list[str] = resolved_so_far,
                    _remaining: list[str] = remaining,
                    _callback: Any = on_resolved,
                ) -> None:
                    if chosen is None:
                        _callback(None)
                        return
                    _resolved.append(chosen)
                    self._resolve_ids(
                        [], _callback, _resolved, _remaining,
                    )

                self.push_screen(
                    DisambiguateScreen(raw, exc.candidates),
                    callback=_on_disambiguate,
                )
                return
            except ItemNotFoundError as exc:
                self.notify(str(exc), severity="error")
                on_resolved(None)
                return

        on_resolved(resolved_so_far)

    def _on_set_parent(self, item_id: str, parent_id: str | None) -> None:
        """Handle set-parent modal result with prefix resolution."""
        if parent_id is None:
            return
        if parent_id:  # non-empty = set parent (empty = clear)
            def _finish_set_parent(resolved: list[str] | None) -> None:
                if resolved is None:
                    return
                self._do_set_parent(item_id, resolved[0])

            self._resolve_ids([parent_id], _finish_set_parent)
            return
        self._do_set_parent(item_id, parent_id)

    def _do_set_parent(self, item_id: str, parent_id: str) -> None:
        """Apply the resolved parent ID update."""
        try:
            self._tracker_set.update(
                item_id,
                set_fields={"parent": parent_id if parent_id else ""},
            )
            self.notify(f"Parent set for {item_id}")
            self._reload_after_write(item_id)
        except (
            ItemNotFoundError, LockedFieldError, AmbiguousPrefixError,
            PermissionError,
        ) as e:
            self.notify(str(e), severity="error")

    def action_edit_before(self) -> None:
        """Open the before-links modal for the selected item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        self.push_screen(
            BeforeScreen(item.id, list(item.before)),
            callback=partial(self._on_edit_before, item.id),
        )

    def _on_edit_before(
        self, item_id: str, result: dict[str, list[str]] | None,
    ) -> None:
        """Handle before-links modal result with prefix resolution."""
        if result is None:
            return
        add_ids = result.get("add", [])
        remove_ids = result.get("remove", [])

        def _finish_before(
            resolved_add: list[str] | None,
            resolved_remove: list[str] | None,
        ) -> None:
            try:
                a_fields = {"before": resolved_add} if resolved_add else None
                r_fields = (
                    {"before": resolved_remove} if resolved_remove else None
                )
                self._tracker_set.update(
                    item_id,
                    add_fields=a_fields,
                    remove_fields=r_fields,
                )
                self.notify(f"Before links updated for {item_id}")
                self._reload_after_write(item_id)
            except (
                ItemNotFoundError, LockedFieldError, AmbiguousPrefixError,
                PermissionError,
            ) as e:
                self.notify(str(e), severity="error")

        def _resolve_removes(resolved_add: list[str] | None) -> None:
            if add_ids and resolved_add is None:
                return
            final_add = resolved_add if add_ids else None
            if remove_ids:
                self._resolve_ids(
                    remove_ids,
                    lambda resolved_rm: (
                        _finish_before(final_add, resolved_rm)
                        if resolved_rm is not None
                        else None
                    ),
                )
            else:
                _finish_before(final_add, None)

        if add_ids:
            self._resolve_ids(add_ids, _resolve_removes)
        else:
            _resolve_removes(None)

    def action_toggle_lock(self) -> None:
        """Open the lock/unlock modal for the selected item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        self.push_screen(
            LockScreen(item.id, item.locked_fields),
            callback=partial(self._on_toggle_lock, item.id),
        )

    def _on_toggle_lock(
        self, item_id: str, result: dict[str, list[str]] | None,
    ) -> None:
        """Handle lock/unlock modal result."""
        if result is None:
            return
        try:
            if result.get("lock"):
                self._tracker_set.lock(item_id, result["lock"])
            if result.get("unlock"):
                self._tracker_set.unlock(item_id, result["unlock"])
            self.notify(f"Lock state updated for {item_id}")
            self._reload_after_write(item_id)
        except (HumanAuthorityError, ItemNotFoundError, PermissionError) as e:
            self.notify(str(e), severity="error")

    def action_toggle_freeze(self) -> None:
        """Toggle freeze/unfreeze on the selected item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        try:
            if item.frozen:
                self._tracker_set.unfreeze(item.id)
                self.notify(f"Unfrozen: {item.id}")
            else:
                self._tracker_set.freeze(item.id)
                self.notify(f"Frozen: {item.id}")
            self._reload_after_write(item.id)
        except (
            HumanAuthorityError, FrozenItemError, ValueError,
            PermissionError,
        ) as e:
            self.notify(str(e), severity="error")

    def action_repair_drift(self) -> None:
        """Repair drift on the selected frozen item."""
        item = self._get_selected_item()
        if not item:
            self.notify("No item selected", severity="warning")
            return
        if not item.frozen:
            self.notify("Item is not frozen", severity="warning")
            return
        drift = self._tracker_set.drift_check(item.id)
        if not drift:
            self.notify("No drift detected", severity="information")
            return

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                self._tracker_set.repair_drift(item.id)
                self.notify(f"Drift repaired: {item.id}")
                self._reload_after_write(item.id)
            except (HumanAuthorityError, ValueError, PermissionError) as e:
                self.notify(str(e), severity="error")

        self.push_screen(
            ConfirmScreen(f"Revert '{item.title}' to frozen state?"),
            _on_confirm,
        )
