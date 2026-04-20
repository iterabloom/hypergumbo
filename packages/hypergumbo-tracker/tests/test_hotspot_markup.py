# SPDX-License-Identifier: MPL-2.0
"""Tests for :mod:`hypergumbo_tracker.hotspot_markup`.

Covers the pure text-to-Rich-markup transform that wraps detected item-ID
substrings with Textual ``[@click=...]`` actions for the item-nav modal
(WI-sulij slice C).
"""

from __future__ import annotations

import pytest

from hypergumbo_tracker.hotspot_markup import render_hotspots
from hypergumbo_tracker.id_matching import build_item_id_pattern
from hypergumbo_tracker.models import KindConfig, TrackerConfig


def _cfg(prefixes: list[str]) -> TrackerConfig:
    kinds = {p.lower(): KindConfig(prefix=p) for p in prefixes}
    return TrackerConfig(
        kinds=kinds,
        statuses=["todo_soft"],
        blocking_statuses=["todo_soft"],
        resolved_statuses=["done"],
    )


@pytest.fixture
def pattern():
    return build_item_id_pattern(_cfg(["WI", "INV"]))


class TestRenderHotspots:
    def test_empty_text_is_empty(self, pattern):
        assert render_hotspots("", pattern, resolver=lambda _id: True) == ""

    def test_no_ids_unchanged(self, pattern):
        text = "plain text with no item references"
        assert (
            render_hotspots(text, pattern, resolver=lambda _id: True) == text
        )

    def test_resolvable_id_wrapped_with_click_action(self, pattern):
        out = render_hotspots(
            "see WI-lusab-baril here",
            pattern,
            resolver=lambda _id: True,
        )
        assert (
            out
            == "see [@click=jump_to_item('WI-lusab-baril')]"
            "[underline]WI-lusab-baril[/underline][/] here"
        )

    def test_unresolvable_id_left_as_plain_text(self, pattern):
        out = render_hotspots(
            "see WI-lusab-baril here",
            pattern,
            resolver=lambda _id: False,
        )
        assert out == "see WI-lusab-baril here"

    def test_resolver_called_with_matched_id(self, pattern):
        seen: list[str] = []

        def resolver(item_id: str) -> bool:
            seen.append(item_id)
            return True

        render_hotspots(
            "WI-lusab-baril and INV-hunof-damud",
            pattern,
            resolver=resolver,
        )
        assert seen == ["WI-lusab-baril", "INV-hunof-damud"]

    def test_multiple_ids_each_wrapped_independently(self, pattern):
        out = render_hotspots(
            "WI-lusab-baril then INV-hunof-damud",
            pattern,
            resolver=lambda _id: True,
        )
        assert out.count("[@click=jump_to_item(") == 2
        assert "WI-lusab-baril" in out
        assert "INV-hunof-damud" in out

    def test_mixed_resolvable_and_unresolvable(self, pattern):
        resolved = {"WI-lusab-baril"}
        out = render_hotspots(
            "WI-lusab-baril and INV-hunof-damud",
            pattern,
            resolver=lambda i: i in resolved,
        )
        assert "[@click=jump_to_item('WI-lusab-baril')]" in out
        # The unresolvable one appears plain (no click action preceding it).
        assert "[@click=jump_to_item('INV-hunof-damud')]" not in out
        assert "INV-hunof-damud" in out

    def test_custom_action_name_used(self, pattern):
        out = render_hotspots(
            "WI-lusab-baril",
            pattern,
            resolver=lambda _id: True,
            action="open_item",
        )
        assert out == (
            "[@click=open_item('WI-lusab-baril')]"
            "[underline]WI-lusab-baril[/underline][/]"
        )

    def test_custom_style_used(self, pattern):
        out = render_hotspots(
            "WI-lusab-baril",
            pattern,
            resolver=lambda _id: True,
            style="bold cyan",
        )
        assert out == (
            "[@click=jump_to_item('WI-lusab-baril')]"
            "[bold cyan]WI-lusab-baril[/bold cyan][/]"
        )

    def test_empty_style_omits_style_wrapper(self, pattern):
        out = render_hotspots(
            "WI-lusab-baril",
            pattern,
            resolver=lambda _id: True,
            style="",
        )
        assert out == "[@click=jump_to_item('WI-lusab-baril')]WI-lusab-baril[/]"

    def test_skip_ranges_suppress_hotspot(self, pattern):
        # The single match is fully inside the skip range, so it is dropped.
        text = "see WI-lusab-baril here"
        start = text.index("WI-lusab-baril")
        end = start + len("WI-lusab-baril")
        out = render_hotspots(
            text,
            pattern,
            resolver=lambda _id: True,
            skip_ranges=[(start, end)],
        )
        assert out == text

    def test_preserves_surrounding_punctuation(self, pattern):
        out = render_hotspots(
            "(WI-lusab-baril): details",
            pattern,
            resolver=lambda _id: True,
        )
        assert out.startswith("(")
        assert out.endswith("): details")
        assert "[@click=jump_to_item('WI-lusab-baril')]" in out

    def test_adjacent_ids_both_wrapped(self, pattern):
        # Two IDs separated only by a space — both should wrap independently
        # and the gap between them must be preserved exactly.
        out = render_hotspots(
            "WI-lusab-baril WI-hunof-damud",
            pattern,
            resolver=lambda _id: True,
        )
        assert out == (
            "[@click=jump_to_item('WI-lusab-baril')]"
            "[underline]WI-lusab-baril[/underline][/]"
            " "
            "[@click=jump_to_item('WI-hunof-damud')]"
            "[underline]WI-hunof-damud[/underline][/]"
        )
