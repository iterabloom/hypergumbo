# SPDX-License-Identifier: MPL-2.0
"""Tests for the WI-sulij TUI hotspot + item-nav modal integration.

Slices A-C3 built the pure-Python foundation (id_matching,
nav_history, hotspot_markup, item_nav_render, ItemNavModal). This slice
wires those pieces into :class:`TrackerApp`: the Description and
Activity panes rewrite item-ID substrings into ``[@click=jump_to_item(...)]``
spans, and clicking (or invoking the action directly) opens the modal
with the target item's content.

The tests exercise:

- ``TrackerApp._item_exists`` — True/False resolver honouring
  ``ItemNotFoundError`` and ``AmbiguousPrefixError``.
- ``TrackerApp._apply_nav_hotspots`` — wraps resolvable IDs, leaves
  unresolvable IDs as plain text, no-ops on empty text.
- ``TrackerApp._nav_modal_content_for`` — returns a
  ``(detail_text, activity_text)`` pair matching the live panes.
- ``TrackerApp.action_jump_to_item`` — pushes ``ItemNavModal``.
- End-to-end Pilot test: a live item whose description contains a
  cross-reference shows a click span in the Static content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hypergumbo_tracker.trackerset import TrackerSet
from hypergumbo_tracker.tui import ItemNavModal, TrackerApp

from helpers import make_test_config, make_test_config_dict


def _make_tracker_with_cross_refs(tmp_path: Path) -> TrackerSet:
    """Build a TrackerSet seeded with items that reference each other.

    Items A and B both mention each other's IDs in their descriptions
    so the hotspot renderer has a real target to rewrite.
    """
    root = tmp_path / ".agent"
    for d in [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    config_path = root / "tracker" / "config.yaml"
    config_path.write_text(yaml.dump(make_test_config_dict()))

    ts = TrackerSet(root, config=make_test_config())
    id_a = ts.add(
        kind="work_item",
        title="Item A",
        status="todo_soft",
        priority=2,
        description="Placeholder",
    )
    ts.add(
        kind="work_item",
        title="Item B",
        status="todo_soft",
        priority=2,
        description=f"Cross-ref to {id_a} appears here.",
    )
    return ts


# ---------------------------------------------------------------------------
# Helper method unit tests (construct App without running it)
# ---------------------------------------------------------------------------


class TestItemExists:
    def test_returns_true_for_known_id(self, tmp_path: Path) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        known = ts.list_items()[0].id
        assert app._item_exists(known) is True

    def test_returns_false_for_unknown_id(self, tmp_path: Path) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        assert app._item_exists("WI-nonex-istenz") is False

    def test_returns_false_on_ambiguous_prefix(self, tmp_path: Path) -> None:
        """An ambiguous prefix must not render as a dead hotspot either."""
        from hypergumbo_tracker.store import AmbiguousPrefixError

        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)

        def _raise(_id: str) -> Any:
            raise AmbiguousPrefixError("ambiguous", [])

        app._tracker_set.get = _raise  # type: ignore[assignment]
        assert app._item_exists("WI-prefix") is False


class TestApplyNavHotspots:
    def test_wraps_resolvable_id(self, tmp_path: Path) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        target = ts.list_items()[0].id
        text = f"see {target} for details"
        result = app._apply_nav_hotspots(text)
        assert f"[@click=jump_to_item('{target}')]" in result

    def test_leaves_unresolvable_id_as_plain_text(
        self, tmp_path: Path,
    ) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        # Properly-shaped but non-existent ID: renders as plain text.
        text = "see WI-nunun-nunun for details"
        result = app._apply_nav_hotspots(text)
        assert "@click=jump_to_item" not in result
        assert "WI-nunun-nunun" in result

    def test_empty_text_no_op(self, tmp_path: Path) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        assert app._apply_nav_hotspots("") == ""


class TestNavModalContentFor:
    def test_returns_detail_and_activity_for_known_item(
        self, tmp_path: Path,
    ) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        target = ts.list_items()[0]
        detail, activity = app._nav_modal_content_for(target.id)
        # Detail pane includes the title verbatim (via _format_detail_lines)
        assert target.title in detail
        # Activity is a string (may be empty when there are no discussions).
        assert isinstance(activity, str)


# ---------------------------------------------------------------------------
# End-to-end Pilot tests
# ---------------------------------------------------------------------------


async def _wait_for_std_detail(pilot: Any, app: Any, max_rounds: int = 50) -> Any:
    from textual.css.query import NoMatches
    from textual.widgets import Static

    for _ in range(max_rounds):
        await pilot.pause()
        try:
            widget = app.query_one("#std-detail-content", Static)
        except NoMatches:
            continue
        if str(widget._Static__content).strip():
            return widget
    raise AssertionError("std-detail-content never populated")  # pragma: no cover


class TestPilotIntegration:
    async def test_detail_pane_emits_click_span_for_cross_ref(
        self, tmp_path: Path,
    ) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 40)) as pilot:
            await _wait_for_std_detail(pilot, app)
            # Find Item B (has the cross-ref in its description) and
            # force _show_std_detail so its markup reaches the pane.
            target = next(i for i in ts.list_items() if i.title == "Item B")
            app._show_std_detail(target.id)
            await pilot.pause()
            from textual.widgets import Static
            detail = app.query_one("#std-detail-content", Static)
            raw = str(detail._Static__content)
            assert "[@click=jump_to_item(" in raw

    async def test_action_jump_pushes_modal(self, tmp_path: Path) -> None:
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 40)) as pilot:
            await _wait_for_std_detail(pilot, app)
            target = ts.list_items()[0].id
            app.action_jump_to_item(target)
            await pilot.pause()
            assert isinstance(app.screen, ItemNavModal)

    async def test_action_jump_ignores_unknown_id(self, tmp_path: Path) -> None:
        """Defensive: never crash on a stale ID from a deleted item."""
        ts = _make_tracker_with_cross_refs(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(130, 40)) as pilot:
            await _wait_for_std_detail(pilot, app)
            before = type(app.screen)
            app.action_jump_to_item("WI-nunun-nunun")
            await pilot.pause()
            # Screen must be unchanged (no modal pushed).
            assert type(app.screen) is before


pytestmark = pytest.mark.filterwarnings(
    "ignore::DeprecationWarning"
)
