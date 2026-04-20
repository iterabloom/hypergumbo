# SPDX-License-Identifier: MPL-2.0
"""Browser-style navigation history for the tracker TUI's item-nav modal.

The tracker TUI's clickable-item-ID feature (WI-sulij) opens a modal that
displays a target item and lets the user jump to further items via
detected IDs in its Description or Activity panes. A Back / Forward /
Jump control row needs browser-like semantics:

- ``current()`` — the item currently displayed (None when the history is
  empty).
- ``push(item_id)`` — navigate to a new item. Truncates any forward
  history (browser-style), refuses no-op navigations (pushing the same
  ID twice in a row is a double-click, not a new history entry).
- ``back()`` / ``forward()`` — move the cursor; both are no-ops when at
  the boundary. Both return the new ``current()`` so the caller can
  re-render in a single call.
- ``can_go_back`` / ``can_go_forward`` — boolean properties for the
  button-disabled state.

The class is a pure data structure — no rendering, no Textual imports —
so it can be unit tested exhaustively and later mounted inside a
``ModalScreen`` subclass without coupling the history logic to the
Textual event loop.

Deliberate non-features:

- No persistence. Closing the modal discards the stack; that matches the
  design sketch in WI-sulij (key constraint 5) and avoids cross-modal
  leakage if the user opens the modal twice from two different contexts.
- No maximum depth. Realistic cross-reference chains top out at a
  handful of jumps; bounding the stack would add complexity for no
  observed benefit.
- No hash/identity on ``current()``. Consumers that need to diff against
  a prior value should compare ID strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NavigationHistory:
    """Browser-style back/forward stack of item IDs.

    Internally: a list of IDs plus a cursor. The cursor points at the
    currently-displayed item; ``-1`` means the history is empty.

    ``push`` truncates any entries after the cursor (so navigating to C
    from A→B→cursor:A drops B before appending C), then appends the new
    ID and advances the cursor. A push of the same ID the cursor
    currently points at is a no-op — this models a double-click on the
    same hotspot.
    """

    _ids: list[str] = field(default_factory=list)
    _cursor: int = -1

    def current(self) -> str | None:
        """Return the currently-displayed item ID, or None if empty."""
        if self._cursor < 0:
            return None
        return self._ids[self._cursor]

    @property
    def can_go_back(self) -> bool:
        """True when there is at least one prior entry behind the cursor."""
        return self._cursor > 0

    @property
    def can_go_forward(self) -> bool:
        """True when there is at least one entry ahead of the cursor."""
        return 0 <= self._cursor < len(self._ids) - 1

    def push(self, item_id: str) -> str:
        """Navigate to *item_id*, truncating any forward history.

        Returns the new ``current()``.
        Raises ``ValueError`` on empty ``item_id`` so callers cannot
        silently produce an un-renderable history entry.
        """
        if not item_id:
            raise ValueError("item_id must be non-empty")
        if self._cursor >= 0 and self._ids[self._cursor] == item_id:
            # Double-click / self-navigation: no new entry.
            return item_id
        # Truncate forward history, then append.
        del self._ids[self._cursor + 1 :]
        self._ids.append(item_id)
        self._cursor = len(self._ids) - 1
        return item_id

    def back(self) -> str | None:
        """Move the cursor one step back. No-op at the boundary."""
        if self.can_go_back:
            self._cursor -= 1
        return self.current()

    def forward(self) -> str | None:
        """Move the cursor one step forward. No-op at the boundary."""
        if self.can_go_forward:
            self._cursor += 1
        return self.current()

    def depth(self) -> int:
        """Number of entries in the history (for display / telemetry)."""
        return len(self._ids)

    def position(self) -> int:
        """Cursor position, 0-indexed. ``-1`` when the history is empty."""
        return self._cursor
