# SPDX-License-Identifier: MPL-2.0
"""Tests for :mod:`hypergumbo_tracker.nav_history`."""

from __future__ import annotations

import pytest

from hypergumbo_tracker.nav_history import (
    NavigationHistory,
    format_nav_indicator,
)


class TestEmpty:
    def test_current_is_none_initially(self):
        h = NavigationHistory()
        assert h.current() is None

    def test_cannot_go_back_or_forward(self):
        h = NavigationHistory()
        assert not h.can_go_back
        assert not h.can_go_forward

    def test_depth_and_position(self):
        h = NavigationHistory()
        assert h.depth() == 0
        assert h.position() == -1

    def test_back_and_forward_are_noops_on_empty(self):
        h = NavigationHistory()
        assert h.back() is None
        assert h.forward() is None
        assert h.depth() == 0

    def test_empty_id_raises(self):
        h = NavigationHistory()
        with pytest.raises(ValueError):
            h.push("")


class TestPushSemantics:
    def test_first_push_becomes_current(self):
        h = NavigationHistory()
        assert h.push("WI-a") == "WI-a"
        assert h.current() == "WI-a"
        assert h.depth() == 1
        assert h.position() == 0

    def test_second_push_advances_cursor(self):
        h = NavigationHistory()
        h.push("WI-a")
        h.push("WI-b")
        assert h.current() == "WI-b"
        assert h.position() == 1
        assert h.depth() == 2

    def test_push_same_id_is_noop(self):
        h = NavigationHistory()
        h.push("WI-a")
        h.push("WI-a")
        assert h.depth() == 1
        assert h.position() == 0

    def test_push_different_id_after_back_truncates_forward(self):
        h = NavigationHistory()
        for id_ in ("WI-a", "WI-b", "WI-c"):
            h.push(id_)
        h.back()
        h.back()
        # Cursor on WI-a; push WI-d should drop WI-b/WI-c.
        h.push("WI-d")
        assert h.current() == "WI-d"
        assert h.depth() == 2
        assert not h.can_go_forward

    def test_push_returns_the_new_current(self):
        h = NavigationHistory()
        assert h.push("WI-x") == "WI-x"
        h.push("WI-y")
        # Double-click at the tip: returns the tip, no history change.
        assert h.push("WI-y") == "WI-y"
        assert h.depth() == 2


class TestBackForward:
    def test_back_moves_cursor_one_step(self):
        h = NavigationHistory()
        h.push("WI-a")
        h.push("WI-b")
        assert h.back() == "WI-a"
        assert h.position() == 0

    def test_back_at_start_is_noop(self):
        h = NavigationHistory()
        h.push("WI-a")
        assert h.back() == "WI-a"
        assert h.position() == 0

    def test_forward_moves_cursor_one_step(self):
        h = NavigationHistory()
        h.push("WI-a")
        h.push("WI-b")
        h.back()
        assert h.forward() == "WI-b"
        assert h.position() == 1

    def test_forward_at_tip_is_noop(self):
        h = NavigationHistory()
        h.push("WI-a")
        h.push("WI-b")
        # Cursor is at tip.
        assert h.forward() == "WI-b"
        assert h.position() == 1

    def test_can_go_back_boundary(self):
        h = NavigationHistory()
        h.push("WI-a")
        assert not h.can_go_back
        h.push("WI-b")
        assert h.can_go_back
        h.back()
        assert not h.can_go_back

    def test_can_go_forward_boundary(self):
        h = NavigationHistory()
        h.push("WI-a")
        h.push("WI-b")
        assert not h.can_go_forward
        h.back()
        assert h.can_go_forward
        h.forward()
        assert not h.can_go_forward


class TestBrowserLikeTraversal:
    def test_full_back_forward_round_trip(self):
        """A→B→C→back→back→forward lands on B."""
        h = NavigationHistory()
        for id_ in ("WI-a", "WI-b", "WI-c"):
            h.push(id_)
        assert h.current() == "WI-c"
        assert h.back() == "WI-b"
        assert h.back() == "WI-a"
        assert h.forward() == "WI-b"

    def test_push_after_back_drops_forward_chain(self):
        h = NavigationHistory()
        for id_ in ("WI-a", "WI-b", "WI-c", "WI-d"):
            h.push(id_)
        h.back()
        h.back()
        # Cursor on WI-b; push WI-new must drop WI-c + WI-d.
        h.push("WI-new")
        assert h.depth() == 3
        assert not h.can_go_forward

    def test_depth_independent_of_cursor(self):
        h = NavigationHistory()
        for id_ in ("WI-a", "WI-b", "WI-c"):
            h.push(id_)
        h.back()
        assert h.depth() == 3
        h.back()
        assert h.depth() == 3


class TestFormatNavIndicator:
    def test_empty_returns_empty_label(self):
        h = NavigationHistory()
        assert format_nav_indicator(h) == "(empty)"

    def test_empty_custom_label(self):
        h = NavigationHistory()
        assert format_nav_indicator(h, empty_label="—") == "—"

    def test_single_item(self):
        h = NavigationHistory()
        h.push("WI-a")
        assert format_nav_indicator(h) == "[1/1] WI-a"

    def test_mid_stack(self):
        h = NavigationHistory()
        for id_ in ("WI-a", "WI-b", "WI-c", "WI-d"):
            h.push(id_)
        h.back()
        h.back()
        # Cursor at WI-b, which is index 1 → display "[2/4]".
        assert format_nav_indicator(h) == "[2/4] WI-b"

    def test_tip_of_stack(self):
        h = NavigationHistory()
        for id_ in ("WI-a", "WI-b", "WI-c"):
            h.push(id_)
        assert format_nav_indicator(h) == "[3/3] WI-c"
