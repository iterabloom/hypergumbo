# SPDX-License-Identifier: MPL-2.0
"""Snapshot (visual regression) tests for hypergumbo_tracker.tui.

Uses pytest-textual-snapshot to capture SVG screenshots of the TrackerApp
at various terminal sizes and interaction states. Each test creates a
deterministic TrackerSet via the shared _make_tracker_set() helper (with
frozen timestamps), then passes the App instance to snap_compare with the
appropriate terminal_size and run_before callable.

Scenario list (from ADR-0013 §PR 6d):
1. Compact list view (40x16)
2. Compact with status column (55x18)
3. Compact detail view (50x18) — Enter pressed on first row
4. Standard two-pane (80x24)
5. Standard tree view (80x24) — 't' pressed
6. Wide layout (160x45)
7. Filter panel open (80x24) — 'f' pressed
8. Too-small message (30x10)
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hypergumbo_tracker.models import TrackerConfig
from hypergumbo_tracker.trackerset import TrackerSet

# Frozen timestamp for deterministic snapshots (wide layout shows created/updated)
_FROZEN_DT = datetime.datetime(2026, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Helpers (shared vocabulary via tests/helpers.py)
# ---------------------------------------------------------------------------


def _make_config() -> TrackerConfig:
    from helpers import make_test_config

    return make_test_config()


class _FrozenDatetime(datetime.datetime):
    """datetime.datetime subclass that always returns a fixed 'now'."""

    @classmethod
    def now(cls, tz: Any = None) -> _FrozenDatetime:  # type: ignore[override]
        return cls(
            _FROZEN_DT.year, _FROZEN_DT.month, _FROZEN_DT.day,
            _FROZEN_DT.hour, _FROZEN_DT.minute, _FROZEN_DT.second,
            tzinfo=tz or _FROZEN_DT.tzinfo,
        )


def _make_tracker_set(tmp_path: Path) -> TrackerSet:
    """Create a TrackerSet with sample items and frozen timestamps."""
    from helpers import make_test_config_dict

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

    config_path.write_text(yaml.dump(make_test_config_dict()))

    ts = TrackerSet(root, config=config)

    # Freeze time so timestamps in wide layout are deterministic
    import hypergumbo_tracker.store as store_mod

    with patch.object(store_mod.datetime, "datetime", _FrozenDatetime):
        ts.add(kind="invariant", title="Symbol IDs must be stable",
               status="todo_hard", priority=1, tags=["quality"],
               description="Symbol IDs change between runs.")
        ts.add(kind="work_item", title="Add caching layer",
               status="in_progress", priority=2)
        ts.add(kind="invariant", title="Routes must have methods",
               status="done", priority=0,
               fields={"statement": "Routes need methods",
                        "root_cause": "Missing validation"})

    return ts


async def _wait_for_table(pilot: Any, widget_id: str = "#item-table",
                          max_rounds: int = 30) -> None:
    """Wait for a DataTable to populate (handles coverage-tracing slowdowns)."""
    table = pilot.app.query_one(widget_id)
    for _ in range(max_rounds):
        await pilot.pause()
        if table.row_count > 0:
            return


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


class TestTuiSnapshots:
    """Visual regression tests for TrackerApp at each layout tier."""

    def test_compact_list(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 1: Compact list view at minimum size (40x16)."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        async def wait(pilot: Any) -> None:
            await _wait_for_table(pilot, "#item-table")

        assert snap_compare(app, terminal_size=(40, 16), run_before=wait)

    def test_compact_with_status(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 2: Compact with status column at 55x18."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        async def wait(pilot: Any) -> None:
            await _wait_for_table(pilot, "#item-table")

        assert snap_compare(app, terminal_size=(55, 18), run_before=wait)

    def test_compact_detail(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 3: Compact detail view at 50x18 — Enter pressed."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        async def press_enter(pilot: Any) -> None:
            await _wait_for_table(pilot, "#item-table")
            await pilot.press("enter")
            await pilot.pause()

        assert snap_compare(app, terminal_size=(50, 18), run_before=press_enter)

    def test_standard_two_pane(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 4: Standard two-pane layout at 80x24."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        async def wait(pilot: Any) -> None:
            await _wait_for_table(pilot, "#std-table")

        assert snap_compare(app, terminal_size=(80, 24), run_before=wait)

    def test_standard_tree_view(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 5: Standard tree view at 80x24 — 't' pressed."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        async def toggle_tree(pilot: Any) -> None:
            await _wait_for_table(pilot, "#std-table")
            await pilot.press("t")
            await pilot.pause()

        assert snap_compare(app, terminal_size=(80, 24), run_before=toggle_tree)

    def test_wide_layout(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 6: Wide layout at 160x45."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        async def wait(pilot: Any) -> None:
            await _wait_for_table(pilot, "#std-table")

        assert snap_compare(app, terminal_size=(160, 45), run_before=wait)

    def test_filter_panel(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 7: Filter panel open at 80x24 — 'f' pressed."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        async def open_filter(pilot: Any) -> None:
            await _wait_for_table(pilot, "#std-table")
            await pilot.press("f")
            await pilot.pause()

        assert snap_compare(app, terminal_size=(80, 24), run_before=open_filter)

    def test_too_small(self, snap_compare: Any, tmp_path: Path) -> None:
        """Scenario 8: Too-small message at 30x10."""
        from hypergumbo_tracker.tui import TrackerApp

        ts = _make_tracker_set(tmp_path)
        app = TrackerApp(tracker_set=ts)

        assert snap_compare(app, terminal_size=(30, 10))
