# SPDX-License-Identifier: MPL-2.0
"""Tests for TUI annotation mode (ADR-0020 Part 1).

Covers the AnnotationScreen modal, _AnnotationCanvas widget, and the
screenshot annotation flow in TrackerApp.action_capture_screenshot.

Test strategy:
- Unit tests for _AnnotationCanvas: rect drawing, label placement,
  draft lifecycle, undo, get_annotations() round-trip
- Unit tests for AnnotationScreen: mode switching, dismiss results
- Integration test for SVG injection in _on_annotation_result
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from textual.geometry import Size

from hypergumbo_tracker.annotations import (
    ArrowAnnotation,
    LabelAnnotation,
    RectAnnotation,
    sanitize_label_text,
)
from hypergumbo_tracker.tui import (
    AnnotationScreen,
    _AnnotationCanvas,
)


def _mouse_event(x: int, y: int) -> MagicMock:
    """Create a mock mouse event with both widget-relative and screen coords."""
    event = MagicMock()
    event.x = x
    event.y = y
    event.screen_x = x
    event.screen_y = y
    return event


# ---------------------------------------------------------------------------
# _AnnotationCanvas unit tests
# ---------------------------------------------------------------------------


class TestAnnotationCanvas:
    """Unit tests for the _AnnotationCanvas widget."""

    def _make_canvas(self, width: int = 40, height: int = 10) -> _AnnotationCanvas:
        """Create a canvas with a mocked size."""
        canvas = _AnnotationCanvas()
        # Override the size property to return our test dimensions
        type(canvas).size = property(lambda self: Size(width, height))
        # Stub refresh() since we're not mounted in a real app
        canvas.refresh = lambda *a, **kw: None  # type: ignore[assignment]
        return canvas

    def _grid_lines(self, canvas: _AnnotationCanvas) -> list[str]:
        """Convert annotation data to lines of text for assertions."""
        width = canvas.size.width
        height = canvas.size.height
        lines = []
        for y in range(height):
            anns = canvas._build_annotations_for_line(y, width)
            line = "".join(anns.get(x, " ") for x in range(width))
            lines.append(line)
        return lines

    def test_empty_canvas(self) -> None:
        """Fresh canvas has no annotations."""
        canvas = self._make_canvas(10, 3)
        anns = canvas._build_annotations_for_line(0, 10)
        assert anns == {}

    def test_set_draft_rect(self) -> None:
        """Draft rect renders with dashed characters."""
        canvas = self._make_canvas(20, 10)
        canvas.set_draft_rect(2, 2, 8, 5)
        assert canvas._draft_rect == (2, 2, 8, 5)
        lines = self._grid_lines(canvas)
        # Top edge at y=2 should have ░ characters from x=2 to x=8
        assert "░" in lines[2]

    def test_set_draft_rect_normalizes_coords(self) -> None:
        """Draft rect normalizes coordinates (handles drag in any direction)."""
        canvas = self._make_canvas(20, 10)
        canvas.set_draft_rect(8, 5, 2, 2)
        assert canvas._draft_rect == (2, 2, 8, 5)

    def test_commit_rect(self) -> None:
        """Committing promotes draft to permanent rect."""
        canvas = self._make_canvas(20, 10)
        canvas.set_draft_rect(2, 2, 8, 5)
        canvas.commit_rect()
        assert canvas._draft_rect is None
        assert len(canvas._rects) == 1
        assert canvas._rects[0] == (2, 2, 8, 5, "#ff3333")

    def test_commit_rect_custom_color(self) -> None:
        """Committing with a custom color."""
        canvas = self._make_canvas(20, 10)
        canvas.set_draft_rect(1, 1, 5, 5)
        canvas.commit_rect(color="#00ff00")
        assert canvas._rects[0][4] == "#00ff00"

    def test_commit_without_draft_is_noop(self) -> None:
        """Committing when no draft exists does nothing."""
        canvas = self._make_canvas(20, 10)
        canvas.commit_rect()
        assert len(canvas._rects) == 0

    def test_discard_draft(self) -> None:
        """Discarding clears the draft rect."""
        canvas = self._make_canvas(20, 10)
        canvas.set_draft_rect(2, 2, 8, 5)
        canvas.discard_draft()
        assert canvas._draft_rect is None

    def test_add_label(self) -> None:
        """Adding a label stores it."""
        canvas = self._make_canvas(40, 10)
        canvas.add_label(5, 3, "Hello")
        assert len(canvas._labels) == 1
        assert canvas._labels[0] == (5, 3, "Hello", "#ff3333")

    def test_add_label_renders(self) -> None:
        """Label text appears in the rendered grid."""
        canvas = self._make_canvas(40, 10)
        canvas.add_label(5, 3, "Hi")
        lines = self._grid_lines(canvas)
        assert lines[3][5] == "H"
        assert lines[3][6] == "i"

    def test_get_annotations_empty(self) -> None:
        """Empty canvas returns empty annotations list."""
        canvas = self._make_canvas()
        assert canvas.get_annotations() == []

    def test_get_annotations_with_rects_and_labels(self) -> None:
        """get_annotations returns proper dataclass instances."""
        canvas = self._make_canvas()
        canvas.set_draft_rect(1, 2, 5, 8)
        canvas.commit_rect()
        canvas.add_label(10, 3, "Test")

        anns = canvas.get_annotations()
        assert len(anns) == 2
        assert isinstance(anns[0], RectAnnotation)
        assert anns[0].cell_x1 == 1
        assert anns[0].cell_y1 == 2
        assert anns[0].cell_x2 == 5
        assert anns[0].cell_y2 == 8
        assert isinstance(anns[1], LabelAnnotation)
        assert anns[1].cell_x == 10
        assert anns[1].cell_y == 3
        assert anns[1].text == "Test"

    def test_committed_rect_renders_solid(self) -> None:
        """Committed rects render with solid block characters."""
        canvas = self._make_canvas(20, 10)
        canvas.set_draft_rect(2, 2, 8, 5)
        canvas.commit_rect()
        lines = self._grid_lines(canvas)
        assert "█" in lines[2]

    def test_multiple_rects(self) -> None:
        """Multiple rects all render."""
        canvas = self._make_canvas(40, 20)
        canvas.set_draft_rect(1, 1, 5, 5)
        canvas.commit_rect()
        canvas.set_draft_rect(10, 10, 15, 15)
        canvas.commit_rect()
        assert len(canvas._rects) == 2

    def test_label_clipped_at_edge(self) -> None:
        """Label text that extends past canvas width is clipped."""
        canvas = self._make_canvas(10, 3)
        canvas.add_label(8, 1, "Hello")
        lines = self._grid_lines(canvas)
        # Only "He" fits at columns 8-9
        assert lines[1][8] == "H"
        assert lines[1][9] == "e"

    def test_add_arrow(self) -> None:
        """Adding an arrow stores it."""
        canvas = self._make_canvas(40, 10)
        canvas.add_arrow(5, 3, 20, 3)
        assert len(canvas._arrows) == 1
        assert canvas._arrows[0] == (5, 3, 20, 3, "#ff3333")

    def test_arrow_renders_horizontal(self) -> None:
        """Horizontal arrow renders with ─ and ▶ characters."""
        canvas = self._make_canvas(30, 10)
        canvas.add_arrow(5, 3, 15, 3)
        lines = self._grid_lines(canvas)
        # Body should have ─ characters
        assert lines[3][10] == "─"
        # Head should have ▶
        assert lines[3][15] == "▶"

    def test_arrow_renders_vertical(self) -> None:
        """Vertical arrow renders with │ and ▼ characters."""
        canvas = self._make_canvas(20, 15)
        canvas.add_arrow(5, 2, 5, 10)
        lines = self._grid_lines(canvas)
        assert lines[5][5] == "│"
        assert lines[10][5] == "▼"

    def test_arrow_renders_upward(self) -> None:
        """Upward arrow ends with ▲."""
        canvas = self._make_canvas(20, 15)
        canvas.add_arrow(5, 10, 5, 2)
        lines = self._grid_lines(canvas)
        assert lines[2][5] == "▲"

    def test_arrow_renders_leftward(self) -> None:
        """Leftward arrow ends with ◀."""
        canvas = self._make_canvas(30, 10)
        canvas.add_arrow(20, 3, 5, 3)
        lines = self._grid_lines(canvas)
        assert lines[3][5] == "◀"

    def test_get_annotations_with_arrows(self) -> None:
        """get_annotations includes ArrowAnnotation instances."""
        canvas = self._make_canvas()
        canvas.add_arrow(1, 2, 10, 5)
        anns = canvas.get_annotations()
        assert len(anns) == 1
        assert isinstance(anns[0], ArrowAnnotation)
        assert anns[0].from_x == 1
        assert anns[0].from_y == 2
        assert anns[0].to_x == 10
        assert anns[0].to_y == 5


# ---------------------------------------------------------------------------
# render_line tests
# ---------------------------------------------------------------------------


class TestCanvasRenderLine:
    """Tests for _AnnotationCanvas.render_line() with frozen strips."""

    def _make_strips(self, lines: list[str]) -> list:
        """Create Strip objects from plain text lines."""
        from textual.strip import Strip as TStrip
        from rich.segment import Segment
        return [TStrip([Segment(line)]) for line in lines]

    def _make_canvas_with_strips(
        self, width: int = 40, height: int = 10,
        lines: list[str] | None = None,
    ) -> _AnnotationCanvas:
        strips = self._make_strips(lines) if lines else []
        canvas = _AnnotationCanvas(frozen_strips=strips)
        type(canvas).size = property(lambda self: Size(width, height))
        canvas.refresh = lambda *a, **kw: None  # type: ignore[assignment]
        return canvas

    def test_render_line_shows_frozen_background(self) -> None:
        """Non-annotation cells show frozen screen content."""
        canvas = self._make_canvas_with_strips(
            10, 3, lines=["ABCDEFGHIJ", "0123456789", "XXXXXXXXXX"],
        )
        strip = canvas.render_line(1)
        assert strip.text == "0123456789"

    def test_render_line_annotation_overrides_background(self) -> None:
        """Annotation chars override frozen content at their positions."""
        canvas = self._make_canvas_with_strips(10, 3, lines=["ABCDEFGHIJ"])
        canvas._rects = [(2, 0, 5, 0, "#ff3333")]
        strip = canvas.render_line(0)
        text = strip.text
        assert text[0] == "A"
        assert text[1] == "B"
        assert text[2] == "█"
        assert text[6] == "G"

    def test_render_line_out_of_range(self) -> None:
        """Out-of-range y returns blank strip."""
        canvas = self._make_canvas_with_strips(10, 3)
        strip = canvas.render_line(5)
        assert strip.text == " " * 10

    def test_render_line_no_strips_uses_spaces(self) -> None:
        """Without frozen strips, returns spaces."""
        canvas = self._make_canvas_with_strips(10, 3)
        strip = canvas.render_line(0)
        assert strip.text == " " * 10

    def test_render_line_no_annotation_returns_base_strip(self) -> None:
        """Line with no annotations returns the frozen strip unchanged."""
        canvas = self._make_canvas_with_strips(10, 3, lines=["ABCDEFGHIJ"])
        strip = canvas.render_line(0)
        assert strip.text == "ABCDEFGHIJ"

    def test_render_line_partial_segment_overlap(self) -> None:
        """Segments without annotation overlap are passed through unchanged."""
        from textual.strip import Strip as TStrip
        from rich.segment import Segment
        from rich.style import Style

        # Multi-segment strip: "AAAA" + "BBBB" (2 segments, 8 chars)
        style_a = Style(color="green")
        style_b = Style(color="blue")
        base = TStrip([Segment("AAAA", style_a), Segment("BBBB", style_b)])
        canvas = _AnnotationCanvas(frozen_strips=[base])
        type(canvas).size = property(lambda self: Size(8, 1))
        canvas.refresh = lambda *a, **kw: None  # type: ignore[assignment]
        # Annotation only on first segment (x=1)
        canvas._labels = [(1, 0, "X", "#ff3333")]
        strip = canvas.render_line(0)
        text = strip.text
        # First segment split: A, X, A, A (annotation at pos 1)
        assert text[0] == "A"
        assert text[1] == "X"
        assert text[2] == "A"
        # Second segment passed through unchanged (no overlap)
        assert text[4] == "B"
        assert text[7] == "B"


# ---------------------------------------------------------------------------
# AnnotationScreen unit tests
# ---------------------------------------------------------------------------


class TestAnnotationScreen:
    """Unit tests for the AnnotationScreen modal."""

    def test_initial_mode_is_rect(self) -> None:
        """AnnotationScreen starts in rect drawing mode."""
        screen = AnnotationScreen("<svg></svg>")
        assert screen._mode == "rect"

    def test_stores_svg_content(self) -> None:
        """Screen stores the SVG for later injection."""
        svg = "<svg>test content</svg>"
        screen = AnnotationScreen(svg)
        assert screen._svg_content == svg

    def test_discard_returns_none(self) -> None:
        """action_discard dismisses with None."""
        screen = AnnotationScreen("<svg></svg>")
        screen.dismiss = MagicMock()
        screen.action_discard()
        screen.dismiss.assert_called_once_with(None)

    def test_confirm_empty_returns_none(self) -> None:
        """Confirming with no annotations dismisses with None."""
        screen = AnnotationScreen("<svg></svg>")
        screen.dismiss = MagicMock()
        # Mock the canvas query
        mock_canvas = MagicMock()
        mock_canvas.get_annotations.return_value = []
        screen.query_one = MagicMock(return_value=mock_canvas)
        screen.action_confirm()
        screen.dismiss.assert_called_once_with(None)

    def test_confirm_with_annotations_returns_list(self) -> None:
        """Confirming with annotations returns the annotation list."""
        screen = AnnotationScreen("<svg></svg>")
        screen.dismiss = MagicMock()
        ann = RectAnnotation(1, 2, 5, 8)
        mock_canvas = MagicMock()
        mock_canvas.get_annotations.return_value = [ann]
        mock_canvas._labels = []
        mock_canvas._rects = [(1, 2, 5, 8, "#ff3333")]
        screen.query_one = MagicMock(return_value=mock_canvas)
        screen.action_confirm()
        screen.dismiss.assert_called_once_with([ann])

    def test_label_mode_switch(self) -> None:
        """action_label_mode switches to label mode."""
        screen = AnnotationScreen("<svg></svg>")
        mock_status = MagicMock()
        screen.query_one = MagicMock(return_value=mock_status)
        screen.action_label_mode()
        assert screen._mode == "label"

    def test_undo_removes_last_label(self) -> None:
        """action_undo removes the most recent label."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = [(5, 3, "Hi", "#ff3333")]
        mock_canvas._arrows = []
        mock_canvas._rects = []
        type(screen).canvas = property(lambda self: mock_canvas)
        screen.action_undo()
        assert mock_canvas._labels == []
        mock_canvas._render_canvas.assert_called_once()
        del type(screen).canvas

    def test_undo_removes_last_rect_when_no_labels_or_arrows(self) -> None:
        """action_undo removes the most recent rect when no labels or arrows."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = []
        mock_canvas._arrows = []
        mock_canvas._rects = [(1, 2, 5, 8, "#ff3333")]
        type(screen).canvas = property(lambda self: mock_canvas)
        screen.action_undo()
        assert mock_canvas._rects == []
        del type(screen).canvas

    def test_undo_noop_when_empty(self) -> None:
        """action_undo does nothing when no annotations exist."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = []
        mock_canvas._arrows = []
        mock_canvas._rects = []
        type(screen).canvas = property(lambda self: mock_canvas)
        screen.action_undo()
        mock_canvas._render_canvas.assert_not_called()
        del type(screen).canvas

    def test_mouse_down_starts_rect_drag(self) -> None:
        """MouseDown in rect mode starts dragging."""
        screen = AnnotationScreen("<svg></svg>")
        event = _mouse_event(10, 5)
        screen.on_mouse_down(event)
        assert screen._dragging is True
        assert screen._drag_start == (10, 5)

    def test_mouse_down_in_label_mode_sets_position(self) -> None:
        """MouseDown in label mode sets position and shows numbered marker."""
        screen = AnnotationScreen("<svg></svg>")
        screen._mode = "label"
        mock_canvas = MagicMock()
        mock_input = MagicMock()
        mock_status = MagicMock()

        def _query_one(selector: str, cls: type | None = None) -> object:
            if selector == "#label-input":
                return mock_input
            return mock_status

        type(screen).canvas = property(lambda self: mock_canvas)
        screen.query_one = _query_one
        event = _mouse_event(15, 8)
        screen.on_mouse_down(event)
        assert screen._label_pending is True
        assert screen._label_pos == (15, 8)
        assert screen._label_marker == 1
        # Should show numbered marker on canvas
        mock_canvas.add_label.assert_called_once_with(15, 8, "[1]")
        # Should show Input widget
        assert mock_input.display is True
        del type(screen).canvas

    def test_mouse_move_updates_draft_rect(self) -> None:
        """MouseMove while dragging updates the draft rect."""
        screen = AnnotationScreen("<svg></svg>")
        screen._dragging = True
        screen._drag_start = (5, 3)
        mock_canvas = MagicMock()
        type(screen).canvas = property(lambda self: mock_canvas)
        event = _mouse_event(20, 10)
        screen.on_mouse_move(event)
        mock_canvas.set_draft_rect.assert_called_once_with(5, 3, 20, 10)

    def test_mouse_move_no_drag_is_noop(self) -> None:
        """MouseMove without active drag does nothing."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        type(screen).canvas = property(lambda self: mock_canvas)
        event = _mouse_event(20, 10)
        screen.on_mouse_move(event)
        mock_canvas.set_draft_rect.assert_not_called()

    def test_mouse_up_commits_rect(self) -> None:
        """MouseUp with sufficient area commits the rect."""
        screen = AnnotationScreen("<svg></svg>")
        screen._dragging = True
        screen._drag_start = (5, 3)
        mock_canvas = MagicMock()
        type(screen).canvas = property(lambda self: mock_canvas)
        event = _mouse_event(20, 10)
        screen.on_mouse_up(event)
        mock_canvas.commit_rect.assert_called_once()
        assert screen._dragging is False

    def test_mouse_up_tiny_rect_discards(self) -> None:
        """MouseUp with tiny area (< 2px) discards the draft."""
        screen = AnnotationScreen("<svg></svg>")
        screen._dragging = True
        screen._drag_start = (5, 3)
        mock_canvas = MagicMock()
        type(screen).canvas = property(lambda self: mock_canvas)
        event = _mouse_event(6, 3)  # Only 1 cell away
        screen.on_mouse_up(event)
        mock_canvas.discard_draft.assert_called_once()
        mock_canvas.commit_rect.assert_not_called()

    def test_confirm_with_pending_label(self) -> None:
        """Confirm with a pending label reads from Input and commits."""
        screen = AnnotationScreen("<svg></svg>")
        screen._label_pending = True
        screen._label_pos = (10, 5)
        screen._label_marker = 1
        screen._mode = "label"
        mock_canvas = MagicMock()
        mock_canvas._labels = [(10, 5, "[1]", "#ff3333")]
        mock_input = MagicMock()
        mock_input.value = "Bug here"
        mock_status = MagicMock()

        def _query_one(selector: str, cls: type | None = None) -> object:
            if selector == "#label-input":
                return mock_input
            return mock_status

        type(screen).canvas = property(lambda self: mock_canvas)
        screen.query_one = _query_one
        screen.dismiss = MagicMock()
        screen.action_confirm()
        # The marker label should be updated with the text
        assert mock_canvas._labels[-1] == (
            10, 5, "[1] Bug here", "#ff3333",
        )
        assert screen._label_pending is False
        assert screen._mode == "rect"
        # Should NOT dismiss — just commits the label
        screen.dismiss.assert_not_called()
        # Input should be hidden
        assert mock_input.display is False
        del type(screen).canvas

    def test_on_input_submitted_commits_label(self) -> None:
        """Input.Submitted when label pending triggers action_confirm."""
        screen = AnnotationScreen("<svg></svg>")
        screen._label_pending = True
        screen._label_pos = (10, 5)
        screen._label_marker = 1
        screen._mode = "label"
        mock_canvas = MagicMock()
        mock_canvas._labels = [(10, 5, "[1]", "#ff3333")]
        mock_input = MagicMock()
        mock_input.value = "Fix this"
        mock_input.id = "label-input"
        mock_status = MagicMock()

        def _query_one(selector: str, cls: type | None = None) -> object:
            if selector == "#label-input":
                return mock_input
            return mock_status

        type(screen).canvas = property(lambda self: mock_canvas)
        screen.query_one = _query_one
        screen.dismiss = MagicMock()

        # Simulate Input.Submitted event
        event = MagicMock()
        event.input = mock_input
        screen.on_input_submitted(event)

        assert screen._label_pending is False
        assert mock_canvas._labels[-1] == (
            10, 5, "[1] Fix this", "#ff3333",
        )
        del type(screen).canvas

    def test_on_input_submitted_ignores_non_label(self) -> None:
        """Input.Submitted from non-label input is ignored."""
        screen = AnnotationScreen("<svg></svg>")
        screen._label_pending = True
        event = MagicMock()
        event.input = MagicMock()
        event.input.id = "other-input"
        # Should not crash or change state
        screen.on_input_submitted(event)
        assert screen._label_pending is True

    def test_label_counter_increments(self) -> None:
        """Each label click increments the numbered marker counter."""
        screen = AnnotationScreen("<svg></svg>")
        screen._mode = "label"
        mock_canvas = MagicMock()
        mock_input = MagicMock()
        mock_status = MagicMock()

        def _query_one(selector: str, cls: type | None = None) -> object:
            if selector == "#label-input":
                return mock_input
            return mock_status

        type(screen).canvas = property(lambda self: mock_canvas)
        screen.query_one = _query_one

        screen.on_mouse_down(_mouse_event(5, 3))
        assert screen._label_marker == 1
        # Reset pending for second click
        screen._label_pending = False
        screen.on_mouse_down(_mouse_event(10, 7))
        assert screen._label_marker == 2
        assert mock_canvas.add_label.call_count == 2
        del type(screen).canvas

    def test_arrow_mode_switch(self) -> None:
        """action_arrow_mode switches to arrow mode."""
        screen = AnnotationScreen("<svg></svg>")
        mock_status = MagicMock()
        screen.query_one = MagicMock(return_value=mock_status)
        screen.action_arrow_mode()
        assert screen._mode == "arrow"

    def test_rect_mode_switch(self) -> None:
        """action_rect_mode switches back to rect mode."""
        screen = AnnotationScreen("<svg></svg>")
        screen._mode = "arrow"
        mock_status = MagicMock()
        screen.query_one = MagicMock(return_value=mock_status)
        screen.action_rect_mode()
        assert screen._mode == "rect"

    def test_mouse_up_arrow_mode_adds_arrow(self) -> None:
        """MouseUp in arrow mode adds an arrow to the canvas."""
        screen = AnnotationScreen("<svg></svg>")
        screen._mode = "arrow"
        screen._dragging = True
        screen._drag_start = (5, 3)
        mock_canvas = MagicMock()
        type(screen).canvas = property(lambda self: mock_canvas)
        event = _mouse_event(20, 10)
        screen.on_mouse_up(event)
        mock_canvas.add_arrow.assert_called_once_with(5, 3, 20, 10)
        del type(screen).canvas

    def test_mouse_up_arrow_mode_small_drag_noop(self) -> None:
        """MouseUp in arrow mode with tiny drag doesn't add arrow."""
        screen = AnnotationScreen("<svg></svg>")
        screen._mode = "arrow"
        screen._dragging = True
        screen._drag_start = (5, 3)
        mock_canvas = MagicMock()
        type(screen).canvas = property(lambda self: mock_canvas)
        event = _mouse_event(6, 3)
        screen.on_mouse_up(event)
        mock_canvas.add_arrow.assert_not_called()
        del type(screen).canvas

    def test_mouse_move_arrow_mode_no_draft(self) -> None:
        """MouseMove in arrow mode doesn't update draft rect."""
        screen = AnnotationScreen("<svg></svg>")
        screen._mode = "arrow"
        screen._dragging = True
        screen._drag_start = (5, 3)
        mock_canvas = MagicMock()
        type(screen).canvas = property(lambda self: mock_canvas)
        event = _mouse_event(20, 10)
        screen.on_mouse_move(event)
        mock_canvas.set_draft_rect.assert_not_called()
        del type(screen).canvas

    def test_nudge_label(self) -> None:
        """Nudge moves the last label."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = [(10, 5, "Hi", "#ff3333")]
        mock_canvas._arrows = []
        mock_canvas._rects = []
        type(screen).canvas = property(lambda self: mock_canvas)
        screen._nudge(1, -1)
        assert mock_canvas._labels[-1] == (11, 4, "Hi", "#ff3333")
        mock_canvas._render_canvas.assert_called_once()
        del type(screen).canvas

    def test_nudge_arrow(self) -> None:
        """Nudge moves the last arrow when no labels exist."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = []
        mock_canvas._arrows = [(5, 3, 20, 10, "#ff3333")]
        mock_canvas._rects = []
        type(screen).canvas = property(lambda self: mock_canvas)
        screen._nudge(2, 0)
        assert mock_canvas._arrows[-1] == (7, 3, 22, 10, "#ff3333")
        del type(screen).canvas

    def test_nudge_rect(self) -> None:
        """Nudge moves the last rect when no labels or arrows exist."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = []
        mock_canvas._arrows = []
        mock_canvas._rects = [(1, 2, 5, 8, "#ff3333")]
        type(screen).canvas = property(lambda self: mock_canvas)
        screen._nudge(0, 3)
        assert mock_canvas._rects[-1] == (1, 5, 5, 11, "#ff3333")
        del type(screen).canvas

    def test_nudge_empty_noop(self) -> None:
        """Nudge with no annotations does nothing."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = []
        mock_canvas._arrows = []
        mock_canvas._rects = []
        type(screen).canvas = property(lambda self: mock_canvas)
        screen._nudge(1, 0)
        mock_canvas._render_canvas.assert_not_called()
        del type(screen).canvas

    def test_action_nudge_directions(self) -> None:
        """All four nudge actions call _nudge with correct deltas."""
        screen = AnnotationScreen("<svg></svg>")
        screen._nudge = MagicMock()
        screen.action_nudge_up()
        screen._nudge.assert_called_with(0, -1)
        screen.action_nudge_down()
        screen._nudge.assert_called_with(0, 1)
        screen.action_nudge_left()
        screen._nudge.assert_called_with(-1, 0)
        screen.action_nudge_right()
        screen._nudge.assert_called_with(1, 0)

    def test_undo_removes_arrow_before_rect(self) -> None:
        """Undo removes arrows before rects (labels first, arrows second)."""
        screen = AnnotationScreen("<svg></svg>")
        mock_canvas = MagicMock()
        mock_canvas._labels = []
        mock_canvas._arrows = [(5, 3, 20, 10, "#ff3333")]
        mock_canvas._rects = [(1, 2, 5, 8, "#ff3333")]
        type(screen).canvas = property(lambda self: mock_canvas)
        screen.action_undo()
        assert mock_canvas._arrows == []
        # Rects should be untouched
        assert len(mock_canvas._rects) == 1
        del type(screen).canvas


# ---------------------------------------------------------------------------
# SVG injection tests
# ---------------------------------------------------------------------------


class TestSVGInjection:
    """Tests for _on_annotation_result SVG annotation injection."""

    def test_inject_rect_into_svg(self, tmp_path: Path) -> None:
        """RectAnnotation is injected as SVG <rect> before </svg>."""
        from hypergumbo_tracker.tui import TrackerApp

        svg = "<svg><text>hello</text></svg>"
        path = tmp_path / "test.svg"
        path.write_text(svg)

        app = TrackerApp.__new__(TrackerApp)
        app._pending_screenshot_path = path
        app._pending_svg = svg
        app.notify = MagicMock()

        annotations = [RectAnnotation(2, 3, 10, 8, "#ff3333")]
        app._on_annotation_result(annotations)

        result = path.read_text()
        assert '<g class="annotations">' in result
        assert "<rect " in result
        assert 'stroke="#ff3333"' in result
        assert "</svg>" in result

    def test_inject_label_into_svg(self, tmp_path: Path) -> None:
        """LabelAnnotation is injected as SVG <text> before </svg>."""
        from hypergumbo_tracker.tui import TrackerApp

        svg = "<svg><text>hello</text></svg>"
        path = tmp_path / "test.svg"
        path.write_text(svg)

        app = TrackerApp.__new__(TrackerApp)
        app._pending_screenshot_path = path
        app._pending_svg = svg
        app.notify = MagicMock()

        annotations = [LabelAnnotation(5, 3, "Bug here", "#ff3333")]
        app._on_annotation_result(annotations)

        result = path.read_text()
        assert "<text " in result
        assert "Bug here" in result

    def test_inject_label_sanitizes_xml(self, tmp_path: Path) -> None:
        """Label text with XML chars is sanitized."""
        from hypergumbo_tracker.tui import TrackerApp

        svg = "<svg></svg>"
        path = tmp_path / "test.svg"
        path.write_text(svg)

        app = TrackerApp.__new__(TrackerApp)
        app._pending_screenshot_path = path
        app._pending_svg = svg
        app.notify = MagicMock()

        annotations = [LabelAnnotation(0, 0, "<script>alert('xss')</script>")]
        app._on_annotation_result(annotations)

        result = path.read_text()
        assert "&lt;script&gt;" in result
        assert "<script>" not in result

    def test_none_result_is_noop(self, tmp_path: Path) -> None:
        """None result from annotation screen does nothing."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp.__new__(TrackerApp)
        # Should not raise — just returns
        app._on_annotation_result(None)

    def test_empty_list_is_noop(self, tmp_path: Path) -> None:
        """Empty annotation list does nothing."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp.__new__(TrackerApp)
        app._on_annotation_result([])

    def test_multiple_annotations(self, tmp_path: Path) -> None:
        """Multiple annotations of different types are all injected."""
        from hypergumbo_tracker.tui import TrackerApp

        svg = "<svg></svg>"
        path = tmp_path / "test.svg"
        path.write_text(svg)

        app = TrackerApp.__new__(TrackerApp)
        app._pending_screenshot_path = path
        app._pending_svg = svg
        app.notify = MagicMock()

        annotations = [
            RectAnnotation(1, 1, 10, 5),
            ArrowAnnotation(3, 3, 15, 8),
            LabelAnnotation(3, 7, "Note"),
            RectAnnotation(15, 2, 20, 8, "#00ff00"),
        ]
        app._on_annotation_result(annotations)

        result = path.read_text()
        assert result.count("<rect ") == 2
        assert result.count("<line ") == 1
        assert result.count("<text ") == 1
        assert "Note" in result
        assert "#00ff00" in result
        assert "marker-end" in result
        assert "<defs>" in result
        assert "arrowhead" in result

    def test_inject_arrow_into_svg(self, tmp_path: Path) -> None:
        """ArrowAnnotation is injected as SVG <line> with arrowhead marker."""
        from hypergumbo_tracker.tui import TrackerApp

        svg = "<svg></svg>"
        path = tmp_path / "test.svg"
        path.write_text(svg)

        app = TrackerApp.__new__(TrackerApp)
        app._pending_screenshot_path = path
        app._pending_svg = svg
        app.notify = MagicMock()

        annotations = [ArrowAnnotation(5, 3, 20, 10, "#ff3333")]
        app._on_annotation_result(annotations)

        result = path.read_text()
        assert "<line " in result
        assert 'stroke="#ff3333"' in result
        assert "marker-end" in result
        assert "<defs>" in result
        assert "arrowhead" in result

    def test_extracts_cell_geometry_from_realistic_svg(self, tmp_path: Path) -> None:
        """Cell dimensions are extracted from Textual's SVG structure."""
        from hypergumbo_tracker.tui import TrackerApp

        # Realistic SVG with Textual's background rects and text elements.
        # Includes the large terminal background rect (height=1707) that
        # must be skipped when extracting line-level cell geometry.
        svg = (
            '<svg class="rich-terminal" viewBox="0 0 3056 1758.0">'
            '<rect x="0" y="0" width="3037.8" height="1707.0"/>'
            '<rect x="0" y="1.5" width="3037.8" height="24.4"/>'
            '<rect x="0" y="25.9" width="3037.8" height="24.4"/>'
            '<rect x="0" y="50.3" width="3037.8" height="24.4"/>'
            '<text x="12.2" y="20" textLength="12.2">X</text>'
            '</svg>'
        )
        path = tmp_path / "test.svg"
        path.write_text(svg)

        app = TrackerApp.__new__(TrackerApp)
        app._pending_screenshot_path = path
        app._pending_svg = svg
        app.notify = MagicMock()

        # Place a rect at cell (10, 5) — should use extracted geometry
        annotations = [RectAnnotation(10, 5, 20, 10)]
        app._on_annotation_result(annotations)

        result = path.read_text()
        assert "<rect " in result
        # With cell_w=12.2, x = 10*12.2 = 122.0
        assert 'x="122.0"' in result
        # With cell_h=24.4, y_offset=1.5, y = 5*24.4 + 1.5 = 123.5
        assert 'y="123.5"' in result

    def test_no_arrowhead_defs_without_arrows(self, tmp_path: Path) -> None:
        """SVG defs with arrowhead marker are only added when arrows exist."""
        from hypergumbo_tracker.tui import TrackerApp

        svg = "<svg></svg>"
        path = tmp_path / "test.svg"
        path.write_text(svg)

        app = TrackerApp.__new__(TrackerApp)
        app._pending_screenshot_path = path
        app._pending_svg = svg
        app.notify = MagicMock()

        annotations = [RectAnnotation(1, 1, 10, 5)]
        app._on_annotation_result(annotations)

        result = path.read_text()
        assert "<defs>" not in result
