# SPDX-License-Identifier: MPL-2.0
"""Tests for screenshot save + auto-create tracker item (WI-rujoz).

Screenshots are saved to .agent/tracker-workspace/screenshots/ and a new
work_item is auto-created with needs_human_review status.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_tracker.models import Tier
from hypergumbo_tracker.trackerset import TrackerSet


def _make_tracker(tmp_path: Path) -> TrackerSet:
    """Create a minimal TrackerSet for testing."""
    tracker_root = tmp_path / ".agent"
    tracker_dir = tracker_root / "tracker"
    tracker_dir.mkdir(parents=True)
    config_yaml = (
        "kinds:\n"
        "  work_item:\n"
        "    prefix: WI\n"
        "    allowed_statuses: [todo_soft, todo_hard, in_progress, needs_human_review, done, wont_do]\n"
        "statuses:\n"
        "  - todo_soft\n"
        "  - todo_hard\n"
        "  - in_progress\n"
        "  - needs_human_review\n"
        "  - done\n"
        "  - wont_do\n"
        "stop_hook:\n"
        "  blocking_statuses: [todo_soft, todo_hard]\n"
        "  resolved_statuses: [done, wont_do]\n"
        "agent_usernames: [test_agent]\n"
        "lamport_branches: [dev]\n"
    )
    (tracker_dir / "config.yaml").write_text(config_yaml)
    return TrackerSet(tracker_root)


class TestSaveScreenshot:
    """Tests for save_screenshot utility."""

    def test_saves_svg_to_screenshots_dir(self, tmp_path: Path) -> None:
        """SVG content is saved to tracker-workspace/screenshots/."""
        from hypergumbo_tracker.screenshot_save import save_screenshot

        ts = _make_tracker(tmp_path)
        svg = "<svg><rect x='0' y='0' width='100' height='100'/></svg>"

        path = save_screenshot(ts, svg)
        assert path.exists()
        assert path.parent.name == "screenshots"
        assert path.suffix == ".svg"
        assert path.read_text() == svg

    def test_creates_screenshots_dir(self, tmp_path: Path) -> None:
        """Creates the screenshots directory if it doesn't exist."""
        from hypergumbo_tracker.screenshot_save import save_screenshot

        ts = _make_tracker(tmp_path)
        screenshots_dir = tmp_path / ".agent" / "tracker-workspace" / "screenshots"
        assert not screenshots_dir.exists()

        save_screenshot(ts, "<svg/>")
        assert screenshots_dir.exists()

    def test_unique_filenames(self, tmp_path: Path) -> None:
        """Each screenshot gets a unique filename."""
        from hypergumbo_tracker.screenshot_save import save_screenshot

        ts = _make_tracker(tmp_path)
        p1 = save_screenshot(ts, "<svg>1</svg>")
        p2 = save_screenshot(ts, "<svg>2</svg>")
        assert p1 != p2
        assert p1.read_text() == "<svg>1</svg>"
        assert p2.read_text() == "<svg>2</svg>"


class TestSaveScreenshotWithItem:
    """Tests for save_screenshot_with_item (save + auto-create tracker item)."""

    def test_creates_tracker_item(self, tmp_path: Path) -> None:
        """Auto-creates a work_item with needs_human_review status."""
        from hypergumbo_tracker.screenshot_save import save_screenshot_with_item

        ts = _make_tracker(tmp_path)
        svg = "<svg><text>annotation</text></svg>"

        path, item_id = save_screenshot_with_item(ts, svg)

        assert path.exists()
        item = ts.get(item_id)
        assert item.status == "needs_human_review"
        assert "Screenshot" in item.title
        assert item.kind == "work_item"

    def test_item_description_contains_path(self, tmp_path: Path) -> None:
        """The auto-created item's description references the SVG path."""
        from hypergumbo_tracker.screenshot_save import save_screenshot_with_item

        ts = _make_tracker(tmp_path)
        _, item_id = save_screenshot_with_item(ts, "<svg/>")

        item = ts.get(item_id)
        assert ".svg" in item.description

    def test_item_in_workspace_tier(self, tmp_path: Path) -> None:
        """The auto-created item is in the workspace tier."""
        from hypergumbo_tracker.screenshot_save import save_screenshot_with_item

        ts = _make_tracker(tmp_path)
        _, item_id = save_screenshot_with_item(ts, "<svg/>")

        item = ts.get(item_id)
        assert item.tier == Tier.WORKSPACE
