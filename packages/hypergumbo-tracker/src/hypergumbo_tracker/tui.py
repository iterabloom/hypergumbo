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
- wide (> 120x38): Falls through to standard (future PR)

Two separate DataTable instances exist for compact (#item-table) and standard
(#std-table) to avoid reparenting complexity. Both use _populate_table() for
shared population logic. _format_detail_lines() is shared between compact
stacked detail and standard right-panel detail.

The tier computation and ID truncation are pure functions for easy unit testing.
TrackerApp reads from a TrackerSet instance (read-only in this PR).

See ADR-0013 §TUI for the responsive design specification.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.widgets import DataTable, Footer, Header, Input, Rule, Static, Tree

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


def _format_detail_lines(item: CompiledItem) -> list[str]:
    """Format a CompiledItem into lines for detail display.

    Used by both compact (stacked) and standard (side-panel) detail views.
    Returns a list of lines suitable for joining with newlines.
    """
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

    return lines


# ---------------------------------------------------------------------------
# TrackerApp
# ---------------------------------------------------------------------------


class TrackerApp(App):
    """Textual TUI for the hypergumbo tracker.

    Two layout tiers are fully implemented:

    - **compact** (40x16 - 59x19): Single DataTable (#item-table) with
      stacked detail view (Enter/Esc).
    - **standard** (60x20 - 120x38): Two-pane layout -- left panel holds
      a DataTable (#std-table) or Tree (#item-tree), right panel shows
      detail for the highlighted item. Filter input (f) narrows items.
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

    #std-detail-view {
        width: 1fr;
        overflow-y: auto;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("escape", "back", "Back"),
        ("t", "toggle_tree", "Tree"),
        ("f", "toggle_filter", "Filter"),
    ]

    def __init__(self, tracker_set: TrackerSet, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._tracker_set = tracker_set
        self._layout_tier = "compact"
        self._items: list[CompiledItem] = []
        self._in_detail = False
        self._selected_item_id: str | None = None
        self._tree_mode: bool = False
        self._filter_active: bool = False
        self._filter_text: str = ""

    def compose(self) -> ComposeResult:
        """Build the widget tree.

        Yields both compact and standard widgets. Visibility is controlled
        by _apply_layout() based on the current tier.
        """
        yield Header()
        yield Static("Terminal too small", id="too-small-msg")
        yield Input(placeholder="Filter...", id="filter-input")
        # Compact-only widgets
        yield DataTable(id="item-table", cursor_type="row")
        yield VerticalScroll(Static("", id="detail-content"), id="detail-view")
        # Standard two-pane widgets
        with Horizontal(id="two-pane"):
            with Vertical(id="left-panel"):
                yield DataTable(id="std-table", cursor_type="row")
                yield Tree("Items", id="item-tree")
            yield Rule(orientation="vertical", id="divider")
            yield VerticalScroll(
                Static("", id="std-detail-content"), id="std-detail-view"
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
            # Standard two-pane layout
            table.display = False
            detail.display = False
            two_pane.display = True
            filter_input.display = self._filter_active

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
            filter_input.display = self._filter_active
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

    def _populate_table(self, table: DataTable, width: int) -> None:
        """Populate a DataTable with items, adapting columns to width.

        Shared by both compact (#item-table) and standard (#std-table).
        Standard always shows the status column; compact only at width >= 55.
        """
        items = self._filtered_items()
        table.clear(columns=True)

        is_standard = table.id == "std-table"
        show_status = is_standard or width >= 55

        table.add_column("#", key="row_num")
        table.add_column("T", key="tier")
        table.add_column("P", key="priority")

        id_width = min(max(10, width // 4), 35)
        if is_standard:
            id_width = min(max(15, width // 3), 35)
        table.add_column("ID", key="id")

        if show_status:
            table.add_column("Status", key="status")

        table.add_column("Title", key="title")

        for idx, item in enumerate(items):
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

        if items:
            table.move_cursor(row=0)

    def _populate_compact_table(self) -> None:
        """Populate the compact DataTable."""
        table = self.query_one("#item-table", DataTable)
        w, _ = self.size
        self._populate_table(table, w)

    def _load_std_table(self) -> None:
        """Populate the standard DataTable."""
        table = self.query_one("#std-table", DataTable)
        w, _ = self.size
        self._populate_table(table, w)

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
        lines = _format_detail_lines(item)
        content = self.query_one("#detail-content", Static)
        content.update("\n".join(lines))

        self._in_detail = True
        self._apply_layout()

    def _show_std_detail(self, item_id: str) -> None:
        """Update the standard right-panel detail for the given item ID."""
        item = next((i for i in self._items if i.id == item_id), None)
        if not item:
            return
        self._selected_item_id = item_id
        lines = _format_detail_lines(item)
        content = self.query_one("#std-detail-content", Static)
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
        """Update filter and reload table when filter input changes."""
        if event.input.id != "filter-input":
            return
        self._filter_text = event.value
        self._reload_active_table()

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
            self._filter_active = True
            self._apply_layout()
            self.query_one("#filter-input", Input).focus()

    def _dismiss_filter(self) -> None:
        """Hide filter input, clear filter text, and reload."""
        self._filter_active = False
        self._filter_text = ""
        filter_input = self.query_one("#filter-input", Input)
        filter_input.value = ""
        self._apply_layout()
        self._reload_active_table()
