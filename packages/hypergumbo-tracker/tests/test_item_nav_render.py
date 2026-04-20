# SPDX-License-Identifier: MPL-2.0
"""Tests for :mod:`hypergumbo_tracker.item_nav_render`.

Covers the pure content-assembly helper for the item-nav modal
(WI-sulij slice C2): header/detail/activity markup packaging with
hotspot wrapping applied uniformly to the Description and Activity
panes.
"""

from __future__ import annotations

import pytest

from hypergumbo_tracker.id_matching import build_item_id_pattern
from hypergumbo_tracker.item_nav_render import (
    NavModalContent,
    build_nav_modal_content,
)
from hypergumbo_tracker.models import KindConfig, TrackerConfig
from hypergumbo_tracker.nav_history import NavigationHistory


def _cfg() -> TrackerConfig:
    return TrackerConfig(
        kinds={"work_item": KindConfig(prefix="WI")},
        statuses=["todo_soft"],
        blocking_statuses=["todo_soft"],
        resolved_statuses=["done"],
    )


@pytest.fixture
def pattern():
    return build_item_id_pattern(_cfg())


@pytest.fixture
def history():
    h = NavigationHistory()
    h.push("WI-lusab-baril")
    return h


class TestBuildNavModalContent:
    def test_returns_nav_modal_content_dataclass(self, pattern, history):
        out = build_nav_modal_content(
            history=history,
            detail_text="",
            activity_text="",
            pattern=pattern,
            resolver=lambda _id: True,
        )
        assert isinstance(out, NavModalContent)
        assert out.header == "[1/1] WI-lusab-baril"
        assert out.detail == ""
        assert out.activity == ""

    def test_header_uses_format_nav_indicator_on_empty_history(self, pattern):
        h = NavigationHistory()
        out = build_nav_modal_content(
            history=h,
            detail_text="",
            activity_text="",
            pattern=pattern,
            resolver=lambda _id: True,
        )
        assert out.header == "(empty)"

    def test_detail_text_gets_hotspots(self, pattern, history):
        out = build_nav_modal_content(
            history=history,
            detail_text="Related: WI-hunof-damud",
            activity_text="",
            pattern=pattern,
            resolver=lambda _id: True,
        )
        assert "[@click=jump_to_item('WI-hunof-damud')]" in out.detail

    def test_activity_text_gets_hotspots(self, pattern, history):
        out = build_nav_modal_content(
            history=history,
            detail_text="",
            activity_text="see WI-hunof-damud for context",
            pattern=pattern,
            resolver=lambda _id: True,
        )
        assert "[@click=jump_to_item('WI-hunof-damud')]" in out.activity

    def test_unresolvable_ids_stay_plain(self, pattern, history):
        out = build_nav_modal_content(
            history=history,
            detail_text="WI-hunof-damud",
            activity_text="",
            pattern=pattern,
            resolver=lambda _id: False,
        )
        assert "[@click" not in out.detail
        assert "WI-hunof-damud" in out.detail

    def test_detail_and_activity_use_same_resolver(self, pattern, history):
        """Both panes consult the resolver for each ID independently."""
        calls: list[str] = []

        def resolver(item_id: str) -> bool:
            calls.append(item_id)
            return True

        build_nav_modal_content(
            history=history,
            detail_text="WI-hunof-damud",
            activity_text="WI-kopar-salit",
            pattern=pattern,
            resolver=resolver,
        )
        assert calls == ["WI-hunof-damud", "WI-kopar-salit"]

    def test_action_and_style_overrides_flow_through(self, pattern, history):
        out = build_nav_modal_content(
            history=history,
            detail_text="WI-hunof-damud",
            activity_text="WI-hunof-damud",
            pattern=pattern,
            resolver=lambda _id: True,
            action="open_item",
            style="bold",
        )
        assert "[@click=open_item('WI-hunof-damud')]" in out.detail
        assert "[@click=open_item('WI-hunof-damud')]" in out.activity
        assert "[bold]WI-hunof-damud[/bold]" in out.detail

    def test_header_reflects_cursor_position(self, pattern):
        h = NavigationHistory()
        h.push("WI-lusab-baril")
        h.push("WI-hunof-damud")
        h.back()
        out = build_nav_modal_content(
            history=h,
            detail_text="",
            activity_text="",
            pattern=pattern,
            resolver=lambda _id: True,
        )
        # Cursor is at position 0 of a 2-entry history after back().
        assert out.header == "[1/2] WI-lusab-baril"
