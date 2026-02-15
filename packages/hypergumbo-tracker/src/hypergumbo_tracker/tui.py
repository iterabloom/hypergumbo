# SPDX-License-Identifier: MPL-2.0
"""Textual TUI for the hypergumbo tracker.

Provides a responsive terminal interface for browsing and managing tracker
items. The layout adapts to terminal size using a tier system:

- too-small (< 40x16): Shows only a "terminal too small" message
- compact (40x16 - 59x19): Full-width DataTable list, stacked detail view
- standard (60x20 - 120x38): Same layout, wider columns (future PR)
- wide (> 120x38): Side-by-side list + detail (future PR)

Compact layout (this PR):
- List mode: DataTable with row#, tier indicator, priority, truncated ID, title
- Detail mode: Replaces table with scrollable item detail (Enter/Esc toggle)
- Keybindings: q=quit, Enter=open, Escape=back

The tier computation and ID truncation are pure functions for easy unit testing.
TrackerApp reads from a TrackerSet instance (read-only in this PR).

See ADR-0013 §TUI for the responsive design specification.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.events import Resize
from textual.widgets import DataTable, Footer, Header, Static

from hypergumbo_tracker.models import CompiledItem, Tier
from hypergumbo_tracker.trackerset import TrackerSet


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


_TIER_INDICATOR = {
    Tier.CANONICAL: "C",
    Tier.WORKSPACE: "W",
    Tier.STEALTH: "S",
}


# ---------------------------------------------------------------------------
# TrackerApp
# ---------------------------------------------------------------------------


class TrackerApp(App):
    """Textual TUI for the hypergumbo tracker."""

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
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("escape", "back", "Back"),
    ]

    def __init__(self, tracker_set: TrackerSet, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._tracker_set = tracker_set
        self._layout_tier = "compact"
        self._items: list[CompiledItem] = []
        self._in_detail = False

    def compose(self) -> ComposeResult:
        """Build the widget tree."""
        yield Header()
        yield Static("Terminal too small", id="too-small-msg")
        yield DataTable(id="item-table", cursor_type="row")
        yield VerticalScroll(Static("", id="detail-content"), id="detail-view")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize layout on mount."""
        self._refresh_tier()
        self._load_items()

    def on_resize(self, event: Resize) -> None:
        """Re-evaluate layout tier when terminal is resized."""
        self._refresh_tier()
        if not self._items:
            self._load_items()

    def _refresh_tier(self) -> None:
        """Compute and apply the layout tier based on current terminal size."""
        w, h = self.size
        new_tier = _compute_tier(w, h)
        if new_tier != self._layout_tier:
            self._layout_tier = new_tier
        self._apply_layout()

    def _apply_layout(self) -> None:
        """Show/hide widgets based on the current layout tier."""
        table = self.query_one("#item-table", DataTable)
        detail = self.query_one("#detail-view")
        msg = self.query_one("#too-small-msg", Static)

        if self._layout_tier == "too-small":
            w, h = self.size
            msg.update(f"Terminal too small (need 40x16, got {w}x{h})")
            msg.display = True
            table.display = False
            detail.display = False
            return

        msg.display = False

        if self._in_detail:
            table.display = False
            detail.display = True
        else:
            table.display = True
            detail.display = False

    def _load_items(self) -> None:
        """Load items from TrackerSet into the DataTable."""
        self._items = self._tracker_set.list_items()

        table = self.query_one("#item-table", DataTable)
        table.clear(columns=True)

        w, _ = self.size
        show_status = w >= 55

        table.add_column("#", key="row_num")
        table.add_column("T", key="tier")
        table.add_column("P", key="priority")

        # Adaptive ID column width
        id_width = min(max(10, w // 4), 35)
        table.add_column("ID", key="id")

        if show_status:
            table.add_column("Status", key="status")

        table.add_column("Title", key="title")

        for idx, item in enumerate(self._items):
            tier_char = _TIER_INDICATOR.get(item.tier, "?") if item.tier else "?"
            truncated_id = _truncate_id(item.id, id_width)

            row: list[str] = [
                str(idx + 1),
                tier_char,
                str(item.priority),
                truncated_id,
            ]
            if show_status:
                row.append(item.status)
            row.append(item.title)

            table.add_row(*row, key=item.id)

        if self._items:
            table.move_cursor(row=0)

    def _show_detail(self, item: CompiledItem) -> None:
        """Populate the detail view with item information."""
        lines: list[str] = []
        lines.append(f"Title: {item.title}")
        lines.append(f"ID: {item.id}")
        lines.append(f"Status: {item.status}")
        lines.append(f"Priority: P{item.priority}")

        tier_str = item.tier.value if item.tier else "unknown"
        lines.append(f"Tier: {tier_str}")

        if item.tags:
            lines.append(f"Tags: {', '.join(item.tags)}")
        if item.parent:
            lines.append(f"Parent: {item.parent}")
        if item.description:
            lines.append(f"\nDescription:\n{item.description}")
        if item.fields:
            lines.append("\nFields:")
            for k, v in item.fields.items():
                lines.append(f"  {k}: {v}")
        if item.discussion:
            lines.append(f"\nDiscussion ({len(item.discussion)} entries):")
            for entry in item.discussion[-5:]:
                lines.append(f"  [{entry.at}] {entry.by}: {entry.message}")

        content = self.query_one("#detail-content", Static)
        content.update("\n".join(lines))

        self._in_detail = True
        self._apply_layout()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open the detail view when a row is selected (Enter key)."""
        if self._in_detail or self._layout_tier == "too-small":
            return

        item_id = str(event.row_key.value)
        item = next((i for i in self._items if i.id == item_id), None)
        if item:
            self._show_detail(item)

    def action_back(self) -> None:
        """Return from detail view to list view."""
        if not self._in_detail:
            return
        self._in_detail = False
        self._apply_layout()
