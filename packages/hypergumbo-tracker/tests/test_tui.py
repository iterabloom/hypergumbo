# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.tui.

Covers the TUI scaffold: layout tier computation, ID truncation,
compact DataTable rendering, detail view navigation, too-small message,
and quit binding. Uses Textual's pilot API for async app tests.

Test strategy:
- Unit tests (sync): _compute_tier boundary matrix, _truncate_id buckets
- Pilot tests (async): mount the app at specific terminal sizes, verify
  widget visibility, row rendering, and key bindings. Uses a wait helper
  to handle coverage-tracing slowdowns in Textual's event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hypergumbo_tracker.models import (
    CompiledItem,
    KindConfig,
    Tier,
    TrackerConfig,
)
from hypergumbo_tracker.trackerset import TrackerSet
from hypergumbo_tracker.tui import _compute_tier, _truncate_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> TrackerConfig:
    return TrackerConfig(
        kinds={
            "invariant": KindConfig(prefix="INV", description="Test invariant"),
            "work_item": KindConfig(prefix="WI", description="Work item"),
        },
        statuses=["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
        blocking_statuses=["todo_hard", "todo_soft"],
        resolved_statuses=["done", "deferred", "wont_do"],
        agent_usernames=["*_agent"],
        lamport_branches=["dev", "main"],
    )


def _make_tracker_set(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with sample items for testing."""
    root = tmp_path / ".agent"
    for d in [
        root / "tracker" / ".ops",
        root / "tracker-workspace" / ".ops",
        root / "tracker-workspace" / "stealth",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    config = _make_config()
    config_path = root / "tracker" / "config.yaml"
    import yaml

    config_path.write_text(yaml.dump({
        "kinds": {
            "invariant": {"prefix": "INV", "description": "Test invariant"},
            "work_item": {"prefix": "WI", "description": "Work item"},
        },
        "statuses": ["todo_hard", "todo_soft", "in_progress", "done", "deferred", "wont_do"],
        "stop_hook": {
            "blocking_statuses": ["todo_hard", "todo_soft"],
            "resolved_statuses": ["done", "deferred", "wont_do"],
        },
        "actor_resolution": {"agent_usernames": ["*_agent"]},
        "lamport_branches": ["dev", "main"],
    }))

    ts = TrackerSet(root, config=config)

    ts.add(kind="invariant", title="Symbol IDs must be stable",
           status="todo_hard", priority=1, tags=["quality"],
           description="Symbol IDs change between runs.")
    ts.add(kind="work_item", title="Add caching layer",
           status="in_progress", priority=2)
    ts.add(kind="invariant", title="Routes must have methods",
           status="done", priority=0,
           fields={"statement": "Routes need methods", "root_cause": "Missing validation"})

    return ts


async def _wait_for_table(pilot: Any, app: Any, max_rounds: int = 30) -> None:
    """Wait for the DataTable to be populated.

    Coverage tracing slows Textual's event loop, so on_mount may not have
    completed by the time run_test yields the pilot. This helper retries
    pilot.pause() until the table has rows or max_rounds is reached.
    """
    table = app.query_one("#item-table")
    for _ in range(max_rounds):
        await pilot.pause()
        if table.row_count > 0:
            return
    # If we get here, the table still has no rows — let the assertion fail naturally


# ---------------------------------------------------------------------------
# Unit tests: _compute_tier
# ---------------------------------------------------------------------------


class TestComputeTier:
    """Test layout tier classification against the ADR-0013 boundary matrix."""

    def test_too_small_both_below(self) -> None:
        assert _compute_tier(30, 10) == "too-small"

    def test_too_small_width_below(self) -> None:
        assert _compute_tier(39, 16) == "too-small"

    def test_too_small_height_below(self) -> None:
        assert _compute_tier(40, 15) == "too-small"

    def test_compact_minimum(self) -> None:
        assert _compute_tier(40, 16) == "compact"

    def test_compact_mid(self) -> None:
        assert _compute_tier(50, 18) == "compact"

    def test_compact_width_below_standard(self) -> None:
        assert _compute_tier(59, 20) == "compact"

    def test_compact_height_below_standard(self) -> None:
        assert _compute_tier(60, 19) == "compact"

    def test_standard_minimum(self) -> None:
        assert _compute_tier(60, 20) == "standard"

    def test_standard_typical(self) -> None:
        assert _compute_tier(80, 24) == "standard"

    def test_standard_large_but_not_wide(self) -> None:
        assert _compute_tier(120, 34) == "standard"

    def test_standard_height_too_low_for_wide(self) -> None:
        assert _compute_tier(121, 38) == "standard"

    def test_wide_minimum(self) -> None:
        assert _compute_tier(121, 39) == "wide"

    def test_wide_large(self) -> None:
        assert _compute_tier(160, 45) == "wide"


# ---------------------------------------------------------------------------
# Unit tests: _truncate_id
# ---------------------------------------------------------------------------


class TestTruncateId:
    """Test adaptive ID truncation across column-width buckets."""

    def test_narrow_le10_shows_prefix_and_one_pair(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 10)
        assert result.startswith("INV-")
        assert len(result) <= 10

    def test_medium_11_20_shows_two_pairs(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 15)
        assert result.startswith("INV-")
        assert len(result) <= 15

    def test_wider_21_32_shows_more(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 25)
        assert result.startswith("INV-")
        assert len(result) <= 25

    def test_full_width_gt32(self) -> None:
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 40)
        assert result == full_id

    def test_short_id_always_fits(self) -> None:
        """An ID shorter than max_width should be returned as-is."""
        full_id = "WI-babab"
        result = _truncate_id(full_id, 20)
        assert result == full_id

    def test_truncation_adds_ellipsis(self) -> None:
        """Truncated IDs should end with '...' to indicate truncation."""
        full_id = "INV-babab-dabab-fabab-habab"
        result = _truncate_id(full_id, 10)
        if len(full_id) > 10:
            assert "…" in result

    def test_no_dashes_hard_truncate(self) -> None:
        """IDs without dashes get hard-truncated with ellipsis."""
        result = _truncate_id("LONGIDENTIFIER", 8)
        assert len(result) <= 8
        assert result.endswith("…")

    def test_narrow_prefix_too_long(self) -> None:
        """When prefix+pair doesn't fit in ≤10, hard-truncate."""
        full_id = "LONGERPREFIX-babab-dabab"
        result = _truncate_id(full_id, 10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_medium_width_all_pairs_too_long(self) -> None:
        """When even prefix-pair1 exceeds max_width in the >10 range."""
        # max_width=12 but prefix is long, so even "VERYLONG-ab…" exceeds 12
        full_id = "VERYLONGPREFIX-abcde-fghij"
        result = _truncate_id(full_id, 12)
        assert len(result) <= 12
        assert result.endswith("…")


# ---------------------------------------------------------------------------
# Pilot tests: compact layout
# ---------------------------------------------------------------------------


class TestCompactLayout:
    """Test the TUI at compact layout sizes using Textual's pilot API."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_list_renders_at_minimum_size(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            assert table.display is True
            assert table.row_count == 3

    async def test_detail_on_enter(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            await pilot.press("enter")
            await pilot.pause()
            detail = app.query_one("#detail-view")
            assert detail.display is True
            assert table.display is False

    async def test_detail_escape_returns(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            await pilot.press("enter")
            await pilot.pause()
            detail = app.query_one("#detail-view")
            assert detail.display is True
            await pilot.press("escape")
            await pilot.pause()
            assert table.display is True
            assert detail.display is False

    async def test_list_at_wide_size_with_status(self, tracker_set: TrackerSet) -> None:
        """At width >= 55, the status column should appear."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(60, 20)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            assert table.display is True
            assert table.row_count == 3
            # Status column should be present at width 60
            column_keys = [col.key.value for col in table.columns.values()]
            assert "status" in column_keys

    async def test_list_at_phone_size(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            assert table.display is True
            assert table.row_count == 3

    async def test_quit(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await pilot.press("q")

    async def test_detail_shows_item_content(self, tracker_set: TrackerSet) -> None:
        """Detail view should show title, status, priority, and tier for the selected item."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(60, 20)) as pilot:
            await _wait_for_table(pilot, app)
            # Select whatever item is first (order is store-dependent)
            await pilot.press("enter")
            await pilot.pause()
            content = app.query_one("#detail-content")
            text = str(content.renderable)
            # All items have these fields
            assert "Title:" in text
            assert "Status:" in text
            assert "Priority:" in text
            assert "Tier:" in text

    async def test_select_no_items_does_nothing(self, tmp_path: Path) -> None:
        """Selecting when table is empty should not crash."""
        from hypergumbo_tracker.tui import TrackerApp

        # Create a TrackerSet with no items
        root = tmp_path / ".agent"
        for d in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
            root / "tracker-workspace" / "stealth",
        ]:
            d.mkdir(parents=True, exist_ok=True)
        import yaml
        (root / "tracker" / "config.yaml").write_text(yaml.dump({
            "kinds": {"invariant": {"prefix": "INV", "description": "Test"}},
            "statuses": ["todo_hard", "done"],
            "stop_hook": {"blocking_statuses": ["todo_hard"], "resolved_statuses": ["done"]},
            "actor_resolution": {"agent_usernames": ["*_agent"]},
            "lamport_branches": ["dev"],
        }))
        config = _make_config()
        ts = TrackerSet(root, config=config)

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await pilot.pause()
            # Pressing enter on an empty table should not crash
            await pilot.press("enter")
            await pilot.pause()
            # Should still be in list mode
            detail = app.query_one("#detail-view")
            assert detail.display is False

    async def test_escape_in_list_mode_does_nothing(self, tracker_set: TrackerSet) -> None:
        """Escape in list mode (not in detail) should not change state."""
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            # We're in list mode; escape should be a no-op
            await pilot.press("escape")
            await pilot.pause()
            table = app.query_one("#item-table")
            assert table.display is True


# ---------------------------------------------------------------------------
# Pilot tests: too-small
# ---------------------------------------------------------------------------


class TestTooSmall:
    """Test that too-small terminals show the warning message."""

    @pytest.fixture()
    def tracker_set(self, tmp_path: Path) -> TrackerSet:
        return _make_tracker_set(tmp_path)

    async def test_too_small_message(self, tracker_set: TrackerSet) -> None:
        from hypergumbo_tracker.tui import TrackerApp

        app = TrackerApp(tracker_set=tracker_set)
        async with app.run_test(size=(30, 10)) as pilot:
            # Need multiple pauses for coverage-traced runs
            for _ in range(5):
                await pilot.pause()
            msg = app.query_one("#too-small-msg")
            assert msg.display is True
            assert "too small" in msg.renderable.lower()
            table = app.query_one("#item-table")
            assert table.display is False


# ---------------------------------------------------------------------------
# Unit tests: _show_detail (direct call, avoids pilot timing issues)
# ---------------------------------------------------------------------------


class TestShowDetailDirect:
    """Test _show_detail by calling it directly on a mounted app."""

    async def test_show_detail_with_all_fields(self, tmp_path: Path) -> None:
        """Verify _show_detail renders tags, parent, description, fields, discussion."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        # Add an item with all fields populated
        item_id = ts.add(
            kind="invariant",
            title="Full detail item",
            status="in_progress",
            priority=1,
            tags=["quality", "cross_language"],
            parent=ts.list_items()[0].id,
            description="A detailed description of the issue.",
            fields={"statement": "Things must work", "root_cause": "They don't"},
        )
        ts.discuss(item_id, "First discussion entry")
        ts.discuss(item_id, "Second discussion entry")

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(60, 20)) as pilot:
            await _wait_for_table(pilot, app)
            # Find the item we just created
            item = next(i for i in app._items if i.id == item_id)
            app._show_detail(item)
            content = app.query_one("#detail-content")
            text = str(content.renderable)
            assert "Full detail item" in text
            assert "quality" in text
            assert "A detailed description" in text
            assert "statement" in text
            assert "root_cause" in text
            assert "Discussion" in text
            assert "First discussion" in text
            # Parent should be shown
            assert ts.list_items()[0].id[:8] in text  # partial ID match

    async def test_show_detail_minimal_item(self, tmp_path: Path) -> None:
        """Item with no tags, parent, description, fields, or discussion."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        item = CompiledItem(
            id="TEST-abcde",
            kind="work_item",
            title="Minimal item",
            status="todo_hard",
            priority=2,
            tier=Tier.WORKSPACE,
        )

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            app._show_detail(item)
            content = app.query_one("#detail-content")
            text = str(content.renderable)
            assert "Minimal item" in text
            assert "workspace" in text

    async def test_show_detail_no_tier(self, tmp_path: Path) -> None:
        """Item with tier=None should display 'unknown'."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        item = CompiledItem(
            id="TEST-xyz",
            kind="invariant",
            title="No tier item",
            status="done",
        )

        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            app._show_detail(item)
            content = app.query_one("#detail-content")
            text = str(content.renderable)
            assert "unknown" in text


# ---------------------------------------------------------------------------
# Unit tests: action_back and on_data_table_row_selected edge cases
# ---------------------------------------------------------------------------


class TestActionBackDirect:
    """Test action_back directly."""

    async def test_action_back_not_in_detail(self, tmp_path: Path) -> None:
        """action_back when not in detail mode should be a no-op."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await pilot.pause()
            assert not app._in_detail
            app.action_back()
            assert not app._in_detail

    async def test_action_back_in_detail(self, tmp_path: Path) -> None:
        """action_back when in detail mode should return to list."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            # Enter detail mode
            app._in_detail = True
            app._apply_layout()
            assert app._in_detail
            # Now back
            app.action_back()
            assert not app._in_detail


class TestRowSelectedEdgeCases:
    """Test on_data_table_row_selected edge cases."""

    async def test_select_while_in_detail_is_noop(self, tmp_path: Path) -> None:
        """Row selected while already in detail mode should be ignored."""
        from textual.widgets import DataTable
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(40, 16)) as pilot:
            await _wait_for_table(pilot, app)
            app._in_detail = True
            table = app.query_one("#item-table", DataTable)
            # Construct a mock RowSelected event and call handler directly
            row_key = next(iter(table.rows.keys()))
            event = DataTable.RowSelected(table, table.cursor_coordinate, row_key)
            app.on_data_table_row_selected(event)
            # Should still be in detail mode, not having opened another detail
            assert app._in_detail

    async def test_select_in_too_small_is_noop(self, tmp_path: Path) -> None:
        """Row selected in too-small mode should be ignored."""
        from textual.widgets import DataTable
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(30, 10)) as pilot:
            for _ in range(5):
                await pilot.pause()
            assert app._layout_tier == "too-small"
            # Construct a mock event to test the handler directly
            table = app.query_one("#item-table", DataTable)
            event = DataTable.RowSelected(table, (0, 0), "fake-key")
            app.on_data_table_row_selected(event)
            assert not app._in_detail


# ---------------------------------------------------------------------------
# Unit tests: _load_items with status column
# ---------------------------------------------------------------------------


class TestLoadItemsStatusColumn:
    """Test _load_items behavior at different widths."""

    async def test_no_status_column_narrow(self, tmp_path: Path) -> None:
        """At width < 55, no status column."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(50, 18)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            column_keys = [col.key.value for col in table.columns.values()]
            assert "status" not in column_keys

    async def test_status_column_wide(self, tmp_path: Path) -> None:
        """At width >= 55, status column should appear."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)
        async with app.run_test(size=(60, 20)) as pilot:
            await _wait_for_table(pilot, app)
            table = app.query_one("#item-table")
            column_keys = [col.key.value for col in table.columns.values()]
            assert "status" in column_keys


# ---------------------------------------------------------------------------
# CLI integration: _cmd_tui
# ---------------------------------------------------------------------------


class TestCmdTui:
    """Test the CLI tui subcommand."""

    def test_tui_import_error(self, capsys: pytest.CaptureFixture) -> None:
        """When textual is not importable, show a helpful error."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "hypergumbo_tracker.tui":
                raise ImportError("No module named 'textual'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from hypergumbo_tracker.cli import main
            with pytest.raises(SystemExit) as exc:
                main(["tui"])
            assert exc.value.code == 1
            err = capsys.readouterr().err
            assert "textual" in err.lower()

    def test_tui_runs_app(self, tmp_path: Path) -> None:
        """When textual is available, _cmd_tui creates and runs the app."""
        ts = _make_tracker_set(tmp_path)
        tracker_root = tmp_path / ".agent"

        with patch("hypergumbo_tracker.tui.TrackerApp.run") as mock_run:
            from hypergumbo_tracker.cli import main
            with pytest.raises(SystemExit) as exc:
                main(["--tracker-root", str(tracker_root), "tui"])
            assert exc.value.code == 0
            mock_run.assert_called_once()
