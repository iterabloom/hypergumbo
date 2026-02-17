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

Write keybindings (d, D, m, n, e, p, b, l) push ModalScreen subclasses that
gather input, then call TrackerSet write methods on dismiss. Errors are shown
via ``self.notify(str(e), severity="error")``. After each write, _load_items()
refreshes the tables and _restore_selection() keeps the cursor stable.

See ADR-0013 §TUI for the responsive design specification.
"""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Rule,
    Select,
    Static,
    Tree,
)

from hypergumbo_tracker.models import CompiledItem, FieldSchema, Tier
from hypergumbo_tracker.store import (
    DiscussionRateLimitError,
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
        lines.append("[20+ msgs]")

    if len(entries) > limit:
        lines.append(f"(showing last {limit} of {len(entries)} entries)")
        entries = entries[-limit:]

    for entry in entries:
        ts = _format_timestamp(entry.at)
        lines.append(f"{ts} [{entry.by}]: {entry.message}")

    return lines


_TIER_INDICATOR = {
    Tier.CANONICAL: "C",
    Tier.WORKSPACE: "W",
    Tier.STEALTH: "S",
}


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


def _format_detail_lines(
    item: CompiledItem,
    tier: str = "standard",
    fields_schema: dict[str, FieldSchema] | None = None,
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
    lines.append(f"Title: {item.title}")
    lines.append(f"ID: {item.id}")

    lock_s = " [locked]" if "status" in item.locked_fields else ""
    lines.append(f"Status{lock_s}: {item.status}")

    lock_p = " [locked]" if "priority" in item.locked_fields else ""
    lines.append(f"Priority{lock_p}: P{item.priority}")

    tier_str = item.tier.value if item.tier else "unknown"
    lines.append(f"Tier: {tier_str}")

    if tier == "wide":
        if item.created_at:
            lines.append(f"Created: {_format_timestamp(item.created_at)}")
        if item.updated_at:
            lines.append(f"Updated: {_format_timestamp(item.updated_at)}")
        if item.cross_tier_conflict:
            lines.append("Cross-tier conflict: YES")

    if item.tags:
        lines.append(f"Tags: {', '.join(item.tags)}")
    if item.parent:
        lines.append(f"Parent: {item.parent}")

    lock_desc = " [locked]" if "description" in item.locked_fields else ""
    if item.description:
        desc = _collapse_double_spacing(item.description)
        lines.append(f"\nDescription{lock_desc}:\n{desc}")

    if item.fields:
        if fields_schema:
            lines.append("\nFields:")
            # Known fields in schema declaration order
            for fname, fschema in fields_schema.items():
                if fname in item.fields:
                    label = f" ({fschema.description})" if fschema.description else ""
                    lock = " [locked]" if fname in item.locked_fields else ""
                    lines.append(f"  {fname}{label}{lock}: {item.fields[fname]}")
            # Unknown fields (not in schema)
            unknown = {k: v for k, v in item.fields.items() if k not in fields_schema}
            if unknown:
                lines.append("\n  Other:")
                for k, v in unknown.items():
                    lock = " [locked]" if k in item.locked_fields else ""
                    lines.append(f"    {k}{lock}: {v}")
        else:
            lines.append("\nFields:")
            for k, v in item.fields.items():
                lock = " [locked]" if k in item.locked_fields else ""
                lines.append(f"  {k}{lock}: {v}")

    # In wide mode, discussion is shown in the activity panel
    if tier != "wide" and item.discussion:
        count = len(item.discussion)
        badge = " [20+ msgs]" if count >= 20 else ""
        lock_d = " [locked]" if "discussion" in item.locked_fields else ""
        lines.append(f"\nDiscussion{lock_d} ({count} entries){badge}:")
        for entry in item.discussion[-5:]:
            lines.append(f"  [{entry.at}] {entry.by}: {entry.message}")

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
    Returns a dict suitable for ``TrackerSet.add(**result)`` or None if
    cancelled.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("NewItemScreen")

    def __init__(self, kinds: list[str], statuses: list[str]) -> None:
        super().__init__()
        self._kinds = kinds
        self._statuses = statuses

    def compose(self) -> ComposeResult:
        kind_options: list[tuple[str, str]] = [(k, k) for k in self._kinds]
        status_options: list[tuple[str, str]] = [(s, s) for s in self._statuses]
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

    Pre-populated with the item's current values. Returns a dict with
    ``set_fields``, ``add_fields``, and ``remove_fields`` keys suitable
    for ``TrackerSet.update()``, or None if cancelled or nothing changed.
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = _modal_css("EditItemScreen")

    def __init__(self, item: CompiledItem, statuses: list[str]) -> None:
        super().__init__()
        self._item = item
        self._statuses = statuses

    def compose(self) -> ComposeResult:
        status_options: list[tuple[str, str]] = [
            (s, s) for s in self._statuses
        ]
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
        width: 45%;
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

    #activity-view {
        display: none;
        height: 40%;
        overflow-y: auto;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("escape", "back", "Back"),
        ("t", "toggle_tree", "Tree"),
        ("f", "toggle_filter", "Filter"),
        ("d", "discuss", "Discuss"),
        ("D", "discuss_clear", "Clear Disc."),
        ("m", "tier_move", "Move Tier"),
        ("n", "new_item", "New"),
        ("e", "edit_item", "Edit"),
        ("p", "set_parent", "Parent"),
        ("b", "edit_before", "Before"),
        ("l", "toggle_lock", "Lock"),
        ("i", "toggle_full_ids", "Full IDs"),
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
                yield VerticalScroll(
                    Static("", id="activity-content"), id="activity-view"
                )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize layout on mount."""
        self._refresh_tier()
        self._load_items()

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

        if self._layout_tier == "too-small":
            w, h = self.size
            msg.update(f"Terminal too small (need 40x16, got {w}x{h})")
            msg.display = True
            table.display = False
            detail.display = False
            two_pane.display = False
            filter_input.display = False
            return

        msg.display = False

        if self._layout_tier in ("standard", "wide"):
            # Standard/wide two-pane layout
            table.display = False
            detail.display = False
            two_pane.display = True
            filter_input.display = self._filter_active and self._layout_width >= 80

            # Activity panel: visible only in wide mode
            activity_view = self.query_one("#activity-view")
            activity_divider = self.query_one("#activity-divider")
            if self._layout_tier == "wide":
                activity_view.display = True
                activity_divider.display = True
            else:
                activity_view.display = False
                activity_divider.display = False

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
        if self._layout_tier in ("standard", "wide"):
            self._load_std_table()
            self._load_tree()
        else:
            self._populate_compact_table()

    def _filtered_items(self) -> list[CompiledItem]:
        """Return items matching the current filter text.

        Matches against title, status, tags, and kind (case-insensitive).
        Empty filter returns all items.
        """
        if not self._filter_text:
            return list(self._items)
        needle = self._filter_text.lower()
        result: list[CompiledItem] = []
        for item in self._items:
            if needle in item.title.lower():
                result.append(item)
            elif needle in item.status.lower():
                result.append(item)
            elif needle in item.kind.lower():
                result.append(item)
            elif any(needle in tag.lower() for tag in item.tags):
                result.append(item)
        return result

    def _populate_table(self, table: DataTable, width: int, tier: str) -> None:
        """Populate a DataTable with items, adapting columns to width and tier.

        Shared by both compact (#item-table) and standard/wide (#std-table).
        Standard always shows the status column; compact only at width >= 55.
        Wide mode adds conflict, created, and updated columns after title,
        and widens the ID column.
        """
        items = self._filtered_items()
        table.clear(columns=True)

        is_standard = table.id == "std-table"
        is_wide = tier == "wide"
        show_status = is_standard or width >= 55

        table.add_column("#", key="row_num")
        table.add_column("T", key="tier")
        table.add_column("P", key="priority")

        id_width = min(max(10, width // 4), 35)
        if is_standard:
            id_cap = 45 if is_wide else 35
            id_width = min(max(15 if not is_wide else 20, width // 3), id_cap)
        table.add_column("ID", key="id")

        if show_status:
            table.add_column("Status", key="status")

        table.add_column("Title", key="title")

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

            row: list[str] = [
                str(idx + 1),
                tier_char,
                str(item.priority),
                truncated_id,
            ]
            if show_status:
                row.append(item.status)
            row.append(item.title)

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
                label = f"[{tier_char}] {child.title}"
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
        lines = _format_detail_lines(item, fields_schema=fields_schema)
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
        )
        content = self.query_one("#std-detail-content", Static)
        content.update("\n".join(lines))
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

    def action_toggle_filter(self) -> None:
        """Toggle filter input visibility."""
        if self._filter_active:
            self._dismiss_filter()
        else:
            if self._layout_width < 80:
                self.notify(
                    "Filter requires terminal width \u2265 80",
                    severity="warning",
                )
                return
            self._filter_active = True
            self._apply_layout()
            self.query_one("#filter-input", Input).focus()

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
    # Write actions
    # ------------------------------------------------------------------

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
        except (HumanAuthorityError, ItemNotFoundError) as e:
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
        ) as e:
            self.notify(str(e), severity="error")

    def action_new_item(self) -> None:
        """Open the new-item modal."""
        config = self._tracker_set.config
        kinds = list(config.kinds.keys())
        statuses = list(config.statuses)
        self.push_screen(
            NewItemScreen(kinds, statuses),
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
        statuses = list(self._tracker_set.config.statuses)
        self.push_screen(
            EditItemScreen(item, statuses),
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
        except (ItemNotFoundError, LockedFieldError) as e:
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

    def _on_set_parent(self, item_id: str, parent_id: str | None) -> None:
        """Handle set-parent modal result."""
        if parent_id is None:
            return
        try:
            self._tracker_set.update(
                item_id,
                set_fields={"parent": parent_id if parent_id else ""},
            )
            self.notify(f"Parent set for {item_id}")
            self._reload_after_write(item_id)
        except (ItemNotFoundError, LockedFieldError) as e:
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
        """Handle before-links modal result."""
        if result is None:
            return
        try:
            add_fields = (
                {"before": result["add"]} if result.get("add") else None
            )
            remove_fields = (
                {"before": result["remove"]}
                if result.get("remove")
                else None
            )
            self._tracker_set.update(
                item_id,
                add_fields=add_fields,
                remove_fields=remove_fields,
            )
            self.notify(f"Before links updated for {item_id}")
            self._reload_after_write(item_id)
        except (ItemNotFoundError, LockedFieldError) as e:
            self.notify(str(e), severity="error")

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
        except (HumanAuthorityError, ItemNotFoundError) as e:
            self.notify(str(e), severity="error")
